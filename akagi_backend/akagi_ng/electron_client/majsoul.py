import base64
import queue

from akagi_ng.bridge.majsoul.bridge import MajsoulBridge
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


class MajsoulElectronClient(BaseElectronClient):
    def __init__(self, shared_queue: queue.Queue[AkagiEvent]):
        super().__init__(shared_queue=shared_queue)
        try:
            self.bridge = MajsoulBridge()
        except Exception:
            logger.exception("Failed to initialize MajsoulBridge in MajsoulElectronClient")
            self.bridge = None

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
        # 跟踪雀魂相关 WebSocket（含不同域名变体）
        if any(keyword in url for keyword in ["maj-soul", "mahjongsoul", "majsoul"]):
            with self._lock:
                if message.request_id in self._tracked_connection_ids:
                    return
                self._tracked_connection_ids.add(message.request_id)
                self._active_connections = len(self._tracked_connection_ids)
                if self._active_connections == 1:
                    self._enqueue_event(SystemEvent(code=NotificationCode.CLIENT_CONNECTED))
                    logger.info(f"[Electron] Majsoul client connected (first connection): {url}")
        else:
            logger.debug(f"[Electron] Ignoring non-Majsoul WebSocket: {url}")

    def _handle_websocket_closed(self, message: WebSocketClosedMessage):
        with self._lock:
            if message.request_id not in self._tracked_connection_ids:
                logger.debug(f"[Electron] Ignoring untracked Majsoul websocket close: {message.request_id}")
                return

            self._tracked_connection_ids.remove(message.request_id)
            self._active_connections = len(self._tracked_connection_ids)
            if self._active_connections == 0:
                # 根据游戏状态决定是否发送 GAME_DISCONNECTED
                game_ended = getattr(self.bridge, "game_ended", False) if self.bridge else False

                if not game_ended:
                    self._enqueue_event(SystemEvent(code=NotificationCode.GAME_DISCONNECTED))
                    logger.info(
                        f"[Electron] All Majsoul connections closed, sending {NotificationCode.GAME_DISCONNECTED}"
                    )
                else:
                    logger.info(
                        "[Electron] All Majsoul connections closed after game end, suppressing GAME_DISCONNECTED."
                    )

    def _handle_websocket_frame(self, message: WebSocketFrameMessage):
        if not self.bridge:
            return

        try:
            b64_data = message.data
            if not b64_data:
                return

            direction = "<-" if message.direction == "outbound" else "->"
            logger.trace(f"[Electron] {direction} Message: {b64_data}")

            # 雀魂消息固定为二进制帧（opcode=2），在 CDP 中以 base64 传输
            try:
                raw_bytes = base64.b64decode(b64_data)
            except ValueError as e:
                logger.error(f"Failed to decode base64 websocket data: {e}")
                return

            mjai_messages = self.bridge.parse(raw_bytes)

            if not mjai_messages:
                return

            for msg in mjai_messages:
                self._enqueue_event(msg)

                # 结束对局时触发返回大厅通知
                match msg:
                    case EndGameEvent():
                        logger.info("[Electron] Detected end_game message, sending RETURN_LOBBY")
                        self._enqueue_event(SystemEvent(code=NotificationCode.RETURN_LOBBY))

        except Exception as e:
            logger.exception(f"Error decoding Majsoul websocket frame: {e}")
