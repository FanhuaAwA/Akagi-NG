import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from uuid import uuid4

from akagi_ng.schema.types import (
    EngineAdditionalMeta,
    EngineAdditionalMetaKey,
    EngineType,
    InferencePhase,
    InferenceStatus,
    NotificationFlagKey,
    NotificationFlags,
)

InferenceListener = Callable[[InferenceStatus], None]


class InferenceAttempt:
    """Report one online request without coupling inference clients to the UI transport."""

    __slots__ = (
        "_finished",
        "_listener",
        "_started_monotonic",
        "model",
        "provider",
        "request_id",
        "started_at_ms",
    )

    def __init__(self, listener: InferenceListener | None, provider: str, model: str = ""):
        self._listener = listener
        self.provider = provider.strip() or "online"
        self.model = model.strip()
        self.request_id = str(uuid4())
        self.started_at_ms = int(time.time() * 1000)
        self._started_monotonic = time.monotonic()
        self._finished = False

    def __enter__(self) -> "InferenceAttempt":
        self._emit("requesting", 0)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.finish("error" if exc_type else "success")
        return False

    def set_model(self, model: object) -> None:
        if isinstance(model, str) and model.strip():
            self.model = model.strip()

    def finish(self, phase: InferencePhase) -> None:
        if self._finished:
            return
        self._finished = True
        elapsed_ms = max(0, round((time.monotonic() - self._started_monotonic) * 1000))
        self._emit(phase, elapsed_ms)

    def _emit(self, phase: InferencePhase, elapsed_ms: int) -> None:
        if not self._listener:
            return
        event = InferenceStatus(
            request_id=self.request_id,
            phase=phase,
            provider=self.provider,
            started_at_ms=self.started_at_ms,
            elapsed_ms=elapsed_ms,
        )
        if self.model:
            event["model"] = self.model
        # Telemetry must never change inference, cancellation, or fallback behavior.
        with contextlib.suppress(Exception):
            self._listener(event)


@dataclass(slots=True)
class BotStatusContext:
    """
    Bot 状态上下文。
    用于在 Bot 的生命周期内，由各个组件（Bot, Engine, Provider）直接报告状态标志和元数据，
    避免层层传递和聚合。
    """

    _flags: NotificationFlags = field(default_factory=set)
    _metadata: EngineAdditionalMeta = field(default_factory=dict)
    _inference_listener: InferenceListener | None = field(default=None, repr=False)

    def set_inference_listener(self, listener: InferenceListener | None) -> None:
        """Attach or detach the transport for inference lifecycle events."""
        self._inference_listener = listener

    def track_inference(self, provider: str, model: str = "") -> InferenceAttempt:
        """Create a tracker for one user-visible online inference attempt."""
        return InferenceAttempt(self._inference_listener, provider, model)

    def set_flag(self, key: NotificationFlagKey, value: bool = True):
        """设置通知标志位"""
        if value:
            self._flags.add(key)
        else:
            self._flags.discard(key)

    def update_flags(self, flags: NotificationFlags):
        """批量更新标志位"""
        self._flags.update(flags)

    def set_metadata(self, key: EngineAdditionalMetaKey, value: EngineType | bool):
        """设置附加元数据"""
        self._metadata[key] = value

    def update_metadata(self, metadata: EngineAdditionalMeta):
        """批量更新元数据"""
        self._metadata.update(metadata)

    @property
    def flags(self) -> NotificationFlags:
        """获取所有标志位"""
        return self._flags.copy()

    @property
    def metadata(self) -> EngineAdditionalMeta:
        """获取所有元数据"""
        return self._metadata.copy()

    def clear_flags(self):
        """清除所有通知标志位"""
        self._flags.clear()

    def clear_metadata(self):
        """清除所有附加元数据"""
        self._metadata.clear()

    def clear(self):
        """重置所有状态"""
        self.clear_flags()
        self.clear_metadata()
