import asyncio
import contextlib
import queue
import threading

from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster

from akagi_ng.mitm_client.bridge_addon import BridgeAddon
from akagi_ng.mitm_client.logger import logger
from akagi_ng.plugins import PluginManager
from akagi_ng.schema.constants import ServerConstants
from akagi_ng.schema.types import AkagiEvent
from akagi_ng.settings import local_settings


class MitmClient:
    STARTUP_TIMEOUT_SECONDS = 5.0

    def __init__(self, shared_queue: queue.Queue[AkagiEvent], plugin_manager: PluginManager | None = None):
        self.running = False
        self.starting = False
        self.last_error: str | None = None
        self._thread: threading.Thread | None = None
        self._master: DumpMaster | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._state_lock = threading.RLock()
        self._startup_complete = threading.Event()
        self._stop_requested = threading.Event()
        self._master_shutdown_requested = threading.Event()
        self.addon: BridgeAddon | None = None
        self.shared_queue = shared_queue
        self.plugin_manager = plugin_manager

    def _mark_ready(self) -> None:
        with self._state_lock:
            if self._stop_requested.is_set():
                return
            self.running = True
            self.starting = False
            self.last_error = None
            self._startup_complete.set()
        logger.info("MITM proxy listeners are ready.")

    def _mark_failed(self, exc: BaseException) -> None:
        with self._state_lock:
            self.running = False
            self.starting = False
            if not self._stop_requested.is_set():
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._startup_complete.set()

    def _request_master_shutdown(self, master: DumpMaster) -> None:
        with self._state_lock:
            if self._master_shutdown_requested.is_set():
                return
            self._master_shutdown_requested.set()
        try:
            master.shutdown()
        except Exception as exc:
            with self._state_lock:
                self.last_error = self.last_error or f"{type(exc).__name__}: {exc}"
            logger.exception("Failed to request MITM proxy shutdown")

    async def _start_proxy(self, host: str, port: int, upstream: str = ""):
        """启动代理服务的异步任务。"""
        try:
            opts = options.Options(listen_host=host, listen_port=port)
            if upstream:
                if upstream.startswith(("http://", "https://")):
                    opts.mode = [f"upstream:{upstream}"]
                else:
                    logger.warning(f"Invalid upstream protocol in '{upstream}', only http/https allowed. Ignoring.")

            master = DumpMaster(
                opts,
                with_termlog=False,
                with_dumper=False,
            )
            addon = BridgeAddon(
                shared_queue=self.shared_queue,
                plugin_manager=self.plugin_manager,
                on_running=self._mark_ready,
            )
            master.addons.add(addon)
            with self._state_lock:
                self._master = master
                self.addon = addon

            logger.info(f"Starting MITM proxy server at {host}:{port}")
            if self._stop_requested.is_set():
                return

            # Scheduling run as a task gives stop() a safe point after
            # Master.run() has cleared should_exit but before we await it.
            run_task = asyncio.create_task(master.run())
            await asyncio.sleep(0)
            if self._stop_requested.is_set():
                self._request_master_shutdown(master)
            await run_task
        except Exception as exc:
            self._mark_failed(exc)
            logger.exception("MITM proxy error")
        finally:
            logger.info("MITM proxy server stopped.")

    def _run_in_thread(self, host: str, port: int, upstream: str = ""):
        """线程入口：运行独立 asyncio 事件循环。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_proxy(host, port, upstream))
        except Exception as exc:
            self._mark_failed(exc)
            logger.exception("MITM proxy thread error")
        finally:
            with self._state_lock:
                if self.running and not self._stop_requested.is_set():
                    self.last_error = "MITM proxy stopped unexpectedly"
                self.running = False
                self.starting = False
                if not self._startup_complete.is_set():
                    if not self._stop_requested.is_set():
                        self.last_error = self.last_error or "MITM proxy stopped before becoming ready"
                    self._startup_complete.set()
            with contextlib.suppress(Exception):
                pending = asyncio.all_tasks(self._loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()
            with self._state_lock:
                self._loop = None
                self._master = None
                self.addon = None

    def start(self, timeout: float | None = None) -> bool:
        startup_timeout = self.STARTUP_TIMEOUT_SECONDS if timeout is None else timeout
        initiated_start = False
        startup_complete: threading.Event | None = None

        with self._state_lock:
            if self.running:
                return True

            if self.starting:
                startup_complete = self._startup_complete
            elif self._thread and self._thread.is_alive():
                self.last_error = self.last_error or "Previous MITM proxy thread is still running"
            elif not local_settings.mitm.enabled and not (self.plugin_manager and self.plugin_manager.requires_mitm()):
                logger.info("MITM is disabled in settings.")
            else:
                conf = local_settings.mitm
                self.running = False
                self.starting = True
                self.last_error = None
                self._startup_complete.clear()
                self._stop_requested.clear()
                self._master_shutdown_requested.clear()
                self._thread = threading.Thread(
                    target=self._run_in_thread,
                    args=(conf.host, conf.port, conf.upstream),
                    daemon=True,
                )
                startup_complete = self._startup_complete
                initiated_start = True
                try:
                    self._thread.start()
                except Exception as exc:
                    self._mark_failed(exc)
                    logger.exception("Failed to start MITM proxy thread")

        if startup_complete is None:
            return False

        if not startup_complete.wait(startup_timeout):
            if not initiated_start:
                return False
            with self._state_lock:
                if not startup_complete.is_set():
                    self.running = False
                    self.starting = False
                    self.last_error = f"MITM proxy did not become ready within {startup_timeout:.1f} seconds"
                    startup_complete.set()
            self.stop()
            return False

        with self._state_lock:
            return self.running

    def stop(self) -> bool:
        with self._state_lock:
            thread = self._thread
            thread_alive = bool(thread and thread.is_alive())
            if not self.running and not self.starting and not thread_alive:
                self._thread = None
                self._master = None
                self.addon = None
                return True

            self._stop_requested.set()
            self.starting = False
            master = self._master

        if master:
            self._request_master_shutdown(master)

        if thread and thread is not threading.current_thread():
            thread.join(timeout=ServerConstants.SHUTDOWN_JOIN_TIMEOUT_SECONDS)

        thread_alive = bool(thread and thread.is_alive())
        with self._state_lock:
            self.running = False
            self.starting = False
            self._startup_complete.set()
            if thread_alive:
                self.last_error = self.last_error or "MITM proxy thread did not stop before timeout"
            else:
                self._thread = None
                self._master = None
                self._loop = None
                self.addon = None

        if thread_alive:
            logger.error(f"MITM client stop timed out: {self.last_error}")
            return False

        logger.info("MITM client stopped.")
        return True
