import queue
from collections.abc import Callable
from dataclasses import dataclass

from akagi_ng.schema.protocols import (
    ControllerProtocol,
    ElectronClientProtocol,
    MitmClientProtocol,
    StateTrackerProtocol,
)
from akagi_ng.schema.types import AkagiEvent
from akagi_ng.settings import Settings


@dataclass(slots=True)
class AppContext:
    """应用上下文，聚合核心组件。"""

    settings: Settings
    shared_queue: queue.Queue[AkagiEvent]
    controller: ControllerProtocol | None
    state_tracker: StateTrackerProtocol | None
    mitm_client: MitmClientProtocol | None
    electron_client: ElectronClientProtocol | None = None
    request_shutdown: Callable[[], None] | None = None


# 全局应用上下文（跨线程共享）
_app_context: AppContext | None = None


def get_app_context() -> AppContext:
    """获取当前应用上下文。"""
    global _app_context
    if _app_context is None:
        raise RuntimeError("Application context not initialized. Call set_app_context() first.")
    return _app_context


def set_app_context(context: AppContext):
    """设置应用上下文。"""
    global _app_context
    _app_context = context
