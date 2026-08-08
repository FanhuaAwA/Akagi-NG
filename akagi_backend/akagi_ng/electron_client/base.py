import queue
import threading

from akagi_ng.electron_client.logger import logger
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.protocols import GameBridge
from akagi_ng.schema.types import (
    AkagiEvent,
    DebuggerDetachedMessage,
    ElectronMessage,
    SystemEvent,
)


class BaseElectronClient:
    bridge: GameBridge | None = None

    def __init__(self, shared_queue: queue.Queue[AkagiEvent]):
        self.message_queue: queue.Queue[AkagiEvent] = shared_queue
        self.running = False
        self._active_connections = 0
        self._tracked_connection_ids: set[str] = set()
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            self.running = True
            self._active_connections = 0
            self._tracked_connection_ids.clear()
            if self.bridge:
                self.bridge.reset()
            logger.info(f"{self.__class__.__name__} started.")

    def stop(self):
        with self._lock:
            self.running = False
            self._active_connections = 0
            self._tracked_connection_ids.clear()
            logger.info(f"{self.__class__.__name__} stopped.")

    def _enqueue_event(self, event: AkagiEvent):
        try:
            self.message_queue.put(event, block=False)
        except queue.Full:
            logger.warning(f"[{self.__class__.__name__}] Message queue full, dropping event: {event}")

    def push_message(self, message: ElectronMessage):
        """处理来自 Electron ingest API 的消息。"""
        if not self.running:
            return

        # 统一处理调试器断开事件
        match message:
            case DebuggerDetachedMessage():
                self._handle_debugger_detached(message)
                return

        # 包括 websocket 生命周期在内，其余消息交给子类处理
        self.handle_message(message)

    def _handle_debugger_detached(self, _message: DebuggerDetachedMessage):
        """处理调试器断开：重置连接计数并按需发送断线通知。"""
        with self._lock:
            if self._active_connections > 0:
                logger.info(
                    f"[{self.__class__.__name__}] Debugger detached, forcing disconnect."
                    f"(Active: {self._active_connections})"
                )
                self._active_connections = 0
                self._tracked_connection_ids.clear()

                # 若桥接器已判定结束，则通常已发送 RETURN_LOBBY，这里抑制 GAME_DISCONNECTED。
                game_ended = False
                if self.bridge:
                    game_ended = getattr(self.bridge, "game_ended", False)

                if not game_ended:
                    self._enqueue_event(SystemEvent(code=NotificationCode.GAME_DISCONNECTED))
                else:
                    logger.info(
                        f"[{self.__class__.__name__}] Debugger detached after game end, suppressing GAME_DISCONNECTED."
                    )
            else:
                logger.debug(f"[{self.__class__.__name__}] Debugger detached, no active connections.")

    def handle_message(self, message: ElectronMessage):
        """处理平台特定消息（抽象方法）。"""
        raise NotImplementedError("Subclasses must implement handle_message()")
