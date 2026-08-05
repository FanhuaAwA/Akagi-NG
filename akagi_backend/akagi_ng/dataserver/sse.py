import asyncio
import contextlib
import inspect
import json
from collections import deque

from aiohttp import web

from akagi_ng.dataserver.logger import logger
from akagi_ng.schema.constants import ServerConstants
from akagi_ng.schema.types import (
    FullRecommendationData,
    InferenceStatus,
    Notification,
    SSEClientData,
)


def _format_sse_message(data: dict, event: str | None = None) -> bytes:
    msg = f"data: {json.dumps(data, ensure_ascii=False)}\n"
    if event:
        msg = f"event: {event}\n{msg}"
    return f"{msg}\n".encode()


class SSEManager:
    """管理 SSE 连接、广播与保活。"""

    def __init__(self):
        self.clients: dict[str, SSEClientData] = {}
        self.latest_recommendations: FullRecommendationData | None = None
        self.latest_inference_status: InferenceStatus | None = None
        self.notification_history: deque[dict[str, list[Notification]]] = deque(
            maxlen=ServerConstants.SSE_MAX_NOTIFICATION_HISTORY
        )
        self.keep_alive_task = None
        self.loop = None  # 事件循环引用，由 DataServer 设置
        self.running = False
        self.lock = asyncio.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def start(self):
        self.running = True
        if self.loop:
            self.keep_alive_task = self.loop.create_task(self.keep_alive())

    def stop(self):
        self.running = False
        if self.keep_alive_task:
            self.keep_alive_task.cancel()

    @staticmethod
    def _wake_queue(queue: asyncio.Queue[bytes | None]) -> None:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
                queue.task_done()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(None)

    async def close(self):
        """Stop background work and wake every blocked SSE handler."""
        self.stop()

        keep_alive_task = self.keep_alive_task
        self.keep_alive_task = None
        if keep_alive_task and keep_alive_task is not asyncio.current_task() and inspect.isawaitable(keep_alive_task):
            with contextlib.suppress(asyncio.CancelledError):
                await keep_alive_task

        async with self.lock:
            queues = [client.queue for client in self.clients.values()]

        for queue in queues:
            self._wake_queue(queue)

    async def _remove_client(self, client_id: str, expected_response: web.StreamResponse | None = None):
        async with self.lock:
            client_data = self.clients.get(client_id)
            # 如果存储的响应与打算关闭的不匹配，则跳过移除以避免踢掉重用相同 client_id 的新连接
            if (
                expected_response is not None
                and client_data is not None
                and client_data.response is not expected_response
            ):
                return

            client_data = self.clients.pop(client_id, None)

        if not client_data:
            return

        self._wake_queue(client_data.queue)
        response = client_data.response
        try:
            if response:
                await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            logger.debug(f"Client {client_id} already closed or connection reset.")
        except Exception as exc:
            logger.warning(f"Error while closing connection for {client_id}: {exc}")

        logger.info(f"SSE client {client_id} disconnected.")

    @staticmethod
    async def _stream_queue(response: web.StreamResponse, queue: asyncio.Queue[bytes | None]):
        while True:
            payload = await queue.get()
            try:
                if payload is None:
                    return
                await response.write(payload)
            finally:
                queue.task_done()

    async def sse_handler(self, request: web.Request) -> web.StreamResponse:
        client_id = request.query.get("clientId")
        if not client_id:
            logger.warning("Client connected without clientId, rejecting.")
            return web.HTTPBadRequest(text="clientId is required")

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        }

        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)

        # 为每个客户端创建消息队列，使用常量定义的上限
        queue = asyncio.Queue(maxsize=ServerConstants.MESSAGE_QUEUE_MAXSIZE)

        # 避免在持锁期间 await，防止与 _remove_client 形成死锁。
        old_response: web.StreamResponse | None = None
        async with self.lock:
            existing = self.clients.get(client_id)
            if existing:
                old_response = existing.response

        if old_response is not None:
            logger.warning(f"Client {client_id} already connected. Closing old connection.")
            await self._remove_client(client_id, expected_response=old_response)

        async with self.lock:
            self.clients[client_id] = SSEClientData(response=response, queue=queue)

        logger.info(f"SSE client {client_id} connected from {request.remote}")

        try:
            await response.write(b": connected\n\n")

            # 发送缓存的最新推荐
            if self.latest_recommendations:
                payload = _format_sse_message(self.latest_recommendations, event="recommendations")
                await response.write(payload)

            if self.latest_inference_status:
                payload = _format_sse_message(self.latest_inference_status, event="inference_status")
                await response.write(payload)

            # 发送历史通知，确保客户端能看到启动过程中的所有状态
            for notification in self.notification_history:
                payload = _format_sse_message(notification, event="notification")
                await response.write(payload)

            # 事件驱动的消息循环：等待并发送队列中的新消息
            await self._stream_queue(response, queue)

        except (asyncio.CancelledError, ConnectionResetError):
            logger.debug(f"SSE handler for {client_id} closed/cancelled.")
        except Exception as e:
            logger.error(f"Error in SSE handler for {client_id}: {e}")
        finally:
            await self._remove_client(client_id, expected_response=response)

        return response

    async def _broadcast_async(self, payload: bytes):
        """
        异步广播，不再直接写入响应，而是推送到客户端各自的队列中。
        """
        async with self.lock:
            if not self.clients:
                return
            targets = list(self.clients.values())

        for client_data in targets:
            queue = client_data.queue
            if queue:
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.warning("SSE client queue full, dropping message.")

    def broadcast_event(
        self,
        event: str,
        data: FullRecommendationData | InferenceStatus | dict[str, list[Notification]],
    ):
        """广播指定事件，并按事件类型更新缓存。"""
        match event:
            case "recommendations":
                self.latest_recommendations = data
            case "notification":
                self.notification_history.append(data)
            case "inference_status":
                self.latest_inference_status = data

        if self.loop and self.running:
            payload = _format_sse_message(data, event)
            asyncio.run_coroutine_threadsafe(self._broadcast_async(payload), self.loop)

    async def keep_alive(self):
        """
        定期保活，推送到客户端队列中。
        """
        while True:
            await asyncio.sleep(ServerConstants.SSE_KEEPALIVE_INTERVAL_SECONDS)

            async with self.lock:
                if not self.clients:
                    continue
                targets = list(self.clients.values())

            keepalive_payload = b": keep-alive\n\n"
            for client_data in targets:
                queue = client_data.queue
                if queue:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(keepalive_payload)

    async def add_client(self, client_id: str, data: SSEClientData):
        """
        手动添加客户端（用于特定内部逻辑或测试）
        """
        async with self.lock:
            self.clients[client_id] = data
            logger.info(f"Client {client_id} added manually to SSE.")
