import asyncio
import contextlib
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError

from aiohttp import web

from akagi_ng.dataserver.api import cors_middleware, setup_routes
from akagi_ng.dataserver.logger import logger
from akagi_ng.dataserver.sse import SSEManager
from akagi_ng.schema.types import FullRecommendationData, InferenceStatus, Notification
from akagi_ng.settings import local_settings

MAX_TCP_PORT = 65535
MAX_BIND_ATTEMPTS = 10


class DataServerStartupError(RuntimeError):
    """Raised when the HTTP data server cannot complete its startup sequence."""


class DataServer(threading.Thread):
    def __init__(self, host: str | None = None, external_port: int | None = None):
        super().__init__()
        self.host = host if host is not None else local_settings.server.host
        self.daemon = True
        self.external_port = external_port if external_port is not None else local_settings.server.port
        self.sse_manager = SSEManager()
        self.loop = None
        self.runner = None
        self.running = False
        self._stop_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._startup_complete = threading.Event()
        self.startup_ready_event = threading.Event()
        self.startup_failed_event = threading.Event()
        self.startup_error: Exception | None = None

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Wait for a successful bind, or re-raise startup failure on the caller thread."""
        if not self._startup_complete.wait(timeout):
            timeout_description = "the configured timeout" if timeout is None else f"{timeout:.1f} seconds"
            raise TimeoutError(f"DataServer did not finish startup within {timeout_description}")

        if self.startup_error is not None:
            raise self.startup_error
        if not self.startup_ready_event.is_set():
            raise DataServerStartupError("DataServer stopped before becoming ready")
        return True

    def _mark_startup_ready(self) -> None:
        self.startup_ready_event.set()
        self._startup_complete.set()

    def _mark_startup_failed(self, error: Exception) -> None:
        self.startup_error = error
        self.startup_failed_event.set()
        self._startup_complete.set()

    def broadcast_event(self, event: str, data: dict):
        """代理到 SSEManager"""
        self.sse_manager.broadcast_event(event, data)

    def send_recommendations(self, recommendations_data: FullRecommendationData):
        """广播推荐数据"""
        # 过滤空推荐以避免干扰
        if not recommendations_data.get("recommendations"):
            return
        logger.debug(f"-> {recommendations_data}")
        self.broadcast_event("recommendations", recommendations_data)

    def send_notifications(self, notifications: list[Notification]):
        """
        使用 'notification' 事件广播通知列表。
        """
        if not notifications:
            return
        data = {"list": notifications}
        logger.debug(f"-> {data}")
        self.broadcast_event("notification", data)

    def send_inference_status(self, status: InferenceStatus):
        """Broadcast one provider-neutral online inference lifecycle event."""
        logger.debug(
            "-> inference_status "
            f"provider={status['provider']} phase={status['phase']} elapsed_ms={status['elapsed_ms']}"
        )
        self.broadcast_event("inference_status", status)

    def stop(self):
        self._stop_requested.set()
        with self._stop_lock:
            self.running = False
            loop = self.loop

            if not self.is_alive():
                self.sse_manager.stop()
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
                return

            if loop and not loop.is_closed() and loop.is_running():
                if threading.current_thread() is self:
                    task = loop.create_task(self._shutdown_async())
                    task.add_done_callback(lambda _task: loop.stop())
                else:
                    shutdown_future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
                    try:
                        shutdown_future.result(timeout=0.75)
                    except FutureTimeoutError:
                        logger.warning("DataServer graceful shutdown timed out; stopping event loop.")
                        shutdown_future.cancel()
                    except Exception as exc:
                        logger.warning(f"DataServer graceful shutdown failed: {exc}")
                    finally:
                        loop.call_soon_threadsafe(loop.stop)
                logger.info("DataServer stop signal sent.")
            elif loop and not loop.is_closed():
                self.sse_manager.stop()
                loop.call_soon_threadsafe(loop.stop)

            if threading.current_thread() is not self:
                self.join(timeout=1.0)
                if self.is_alive():
                    logger.warning("DataServer thread did not stop within the shutdown deadline.")

    async def _shutdown_async(self):
        """Close active streams and aiohttp resources on their owning event loop."""
        self.running = False
        await self.sse_manager.close()

        runner = self.runner
        self.runner = None
        if runner:
            try:
                await asyncio.wait_for(runner.cleanup(), timeout=0.5)
            except TimeoutError:
                logger.warning("DataServer aiohttp cleanup exceeded the shutdown deadline.")

    def _bind_first_available_port(self) -> None:
        requested_port = self.external_port
        if not 1 <= requested_port <= MAX_TCP_PORT:
            raise DataServerStartupError(f"Invalid DataServer port: {requested_port}")

        last_port = min(requested_port + MAX_BIND_ATTEMPTS - 1, MAX_TCP_PORT)
        last_bind_error: OSError | None = None
        for current_port in range(requested_port, last_port + 1):
            try:
                site = web.TCPSite(self.runner, self.host, current_port)
                self.loop.run_until_complete(site.start())
                self.external_port = current_port
                return
            except OSError as exc:
                last_bind_error = exc
                logger.warning(
                    "DataServer bind failed: "
                    f"host={self.host!r} port={current_port} "
                    f"errno={getattr(exc, 'errno', None)!r} "
                    f"winerror={getattr(exc, 'winerror', None)!r} "
                    f"error={exc!r}"
                )

        error = DataServerStartupError(f"Could not bind DataServer on {self.host}:{requested_port}-{last_port}")
        raise error from last_bind_error

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # 初始化 SSE 循环
        self.sse_manager.set_loop(self.loop)
        self.sse_manager.start()

        try:
            if self._stop_requested.is_set():
                raise DataServerStartupError("DataServer startup cancelled before initialization")

            app = web.Application(middlewares=[cors_middleware])

            # --- API / SSE 路由 ---
            app.router.add_get("/sse", self.sse_manager.sse_handler)
            setup_routes(app)

            self.runner = web.AppRunner(app, shutdown_timeout=0.25)
            self.loop.run_until_complete(self.runner.setup())

            self._bind_first_available_port()

            if self.external_port != local_settings.server.port:
                local_settings.server.port = self.external_port
                local_settings.save()
                logger.info(f"DataServer port conflicted. Switched to {self.external_port}.")

            if self._stop_requested.is_set():
                raise DataServerStartupError("DataServer startup cancelled before entering the event loop")

            logger.info(f"DataServer listening on {self.host}:{self.external_port}")
            self.running = True
            self._mark_startup_ready()

            # 保活任务由 SSEManager 管理，但事件循环仍需 run_forever
            self.loop.run_forever()
        except Exception as e:
            self.running = False
            if not self._startup_complete.is_set():
                self.startup_error = e
                logger.exception(f"DataServer startup failed: {e}")
            else:
                logger.exception(f"DataServer runtime error: {e}")
        finally:
            with contextlib.suppress(Exception):
                self.loop.run_until_complete(self._shutdown_async())

            # 取消所有剩余任务
            with contextlib.suppress(Exception):
                pending = asyncio.all_tasks(self.loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

            with contextlib.suppress(Exception):
                self.loop.close()

            logger.info("DataServer event loop stopped.")

    def run(self) -> None:
        """Run the server thread and always publish a terminal startup result."""
        try:
            self._run()
        except Exception as exc:
            self.running = False
            self.startup_error = exc
            logger.exception(f"DataServer startup failed before event-loop initialization: {exc}")

            loop = self.loop
            if loop is not None and not loop.is_closed():
                self.sse_manager.stop()
                with contextlib.suppress(Exception):
                    loop.close()
                logger.info("DataServer event loop stopped.")
        finally:
            if not self._startup_complete.is_set():
                error = self.startup_error or DataServerStartupError(
                    "DataServer stopped before reporting startup status"
                )
                self._mark_startup_failed(error)
