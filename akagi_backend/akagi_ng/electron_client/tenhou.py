import base64
import queue

from akagi_ng.bridge.tenhou.bridge import TenhouBridge
from akagi_ng.electron_client.base import BaseElectronClient
from akagi_ng.electron_client.logger import logger
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import (
    AkagiEvent,
    ElectronMessage,
    EndGameEvent,
    SystemEvent,
    WebSocketClosedMessage,
    WebSocketCreatedMessage,
    WebSocketFrameMessage,
)


class TenhouElectronClient(BaseElectronClient):
    def __init__(self, shared_queue: queue.Queue[AkagiEvent]):
        super().__init__(shared_queue=shared_queue)
        try:
            self.bridge = TenhouBridge()
        except Exception:
            logger.exception("Failed to initialize TenhouBridge in TenhouElectronClient")
            self.bridge = None

    WS_TEXT = 1
    WS_BINARY = 2

    def handle_message(self, message: ElectronMessage):
        match message:
            case WebSocketCreatedMessage():
                self._handle_websocket_created(message)
            case WebSocketClosedMessage():
                self._handle_websocket_closed(message)
            case WebSocketFrameMessage():
                self._handle_websocket_frame(message)

    def _handle_websocket_created(self, message: WebSocketCreatedMessage):
        url = message.url
        # 仅跟踪天凤相关 WebSocket
        if "tenhou.net" in url or "nodocchi" in url:
            with self._lock:
                if message.request_id in self._tracked_connection_ids:
                    return
                first_connection = not self._tracked_connection_ids
                self._tracked_connection_ids.add(message.request_id)
                self._active_connections = len(self._tracked_connection_ids)
                if first_connection:
                    if self.bridge:
                        self.bridge.reset()
                    self._enqueue_event(SystemEvent(code=NotificationCode.CLIENT_CONNECTED))
                    logger.info(f"[Electron] Tenhou client connected (first connection): {url}")

    def _handle_websocket_closed(self, message: WebSocketClosedMessage):
        with self._lock:
            if message.request_id not in self._tracked_connection_ids:
                logger.debug(f"[Electron] Ignoring untracked Tenhou websocket close: {message.request_id}")
                return

            self._tracked_connection_ids.remove(message.request_id)
            self._active_connections = len(self._tracked_connection_ids)
            if self._active_connections == 0:
                # 根据游戏状态决定是否发送 GAME_DISCONNECTED
                game_ended = getattr(self.bridge, "game_ended", False) if self.bridge else False

                if not game_ended:
                    self._enqueue_event(SystemEvent(code=NotificationCode.GAME_DISCONNECTED))
                    logger.info(
                        f"[Electron] All Tenhou connections closed, sending {NotificationCode.GAME_DISCONNECTED}"
                    )
                else:
                    logger.info(
                        "[Electron] All Tenhou connections closed after game end, suppressing GAME_DISCONNECTED."
                    )

    def _handle_websocket_frame(self, message: WebSocketFrameMessage):
        if not self.bridge:
            return

        try:
            # 仅处理服务端下行消息，避免与回显确认重复计数。
            # CDP 中 outbound=客户端->服务端，inbound=服务端->客户端。
            if message.direction == "outbound":
                return

            data = message.data
            if not data:
                return

            logger.trace(f"[Electron] -> Message: {data}")

            # 天凤 Web 客户端：
            # - 文本帧（opcode=1）：原始字符串（如 HELO）
            # - 二进制帧（opcode=2）：base64 编码字节串
            opcode = message.opcode or self.WS_TEXT

            if opcode == self.WS_BINARY:
                raw_bytes = base64.b64decode(data)
            else:
                raw_bytes = data.encode("utf-8") if isinstance(data, str) else bytes(data)

            mjai_messages = self.bridge.parse(raw_bytes)

            if not mjai_messages:
                return

            for msg in mjai_messages:
                self._enqueue_event(msg)

                # 结束对局时触发返回大厅通知
                match msg:
                    case EndGameEvent():
                        logger.info("[Electron] Detected end_game message in Tenhou, sending RETURN_LOBBY")
                        self._enqueue_event(SystemEvent(code=NotificationCode.RETURN_LOBBY))

            self._adopt_validated_connection(message.request_id)

        except Exception as e:
            logger.exception(f"Error decoding Tenhou websocket frame: {e}")

    def _adopt_validated_connection(self, request_id: str) -> None:
        # Some Tenhou endpoints use an IP or relay hostname that cannot be
        # classified from webSocketCreated. Adopt the request only after a
        # frame has successfully produced a Tenhou event.
        with self._lock:
            if request_id in self._tracked_connection_ids:
                return
            first_connection = not self._tracked_connection_ids
            self._tracked_connection_ids.add(request_id)
            self._active_connections = len(self._tracked_connection_ids)
            if first_connection:
                self._enqueue_event(SystemEvent(code=NotificationCode.CLIENT_CONNECTED))
                logger.info(f"[Electron] Adopted Tenhou websocket from a validated game frame: {request_id}")
