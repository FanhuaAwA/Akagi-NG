from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import mitmproxy.http

    from akagi_ng.bridge.base import BaseBridge


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    id: str
    name: str
    version: str
    description: str
    author: str
    homepage: str
    requires_mitm: bool
    capabilities: tuple[str, ...]
    risk_notice: str = ""


class AkagiPlugin:
    """Akagi-NG 插件的最小生命周期与流量钩子契约。"""

    metadata: PluginMetadata

    def __init__(self) -> None:
        self.enabled = False
        self.last_error = ""

    def on_enable(self) -> None:
        """插件启用后的可选初始化钩子。"""

    def on_disable(self) -> None:
        """插件禁用后的可选清理钩子。"""

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow, bridge: BaseBridge) -> None:
        """在平台 Bridge 解析前观察或改写最新 WebSocket 帧。"""

    def request(self, flow: mitmproxy.http.HTTPFlow) -> None:
        """在平台 Bridge 处理前观察或改写 HTTP 请求。"""

    def to_public_dict(self, *, mitm_running: bool) -> dict[str, Any]:
        if self.last_error:
            runtime_status = "error"
        elif not self.enabled:
            runtime_status = "disabled"
        elif self.metadata.requires_mitm and not mitm_running:
            runtime_status = "waiting_for_mitm"
        else:
            runtime_status = "active"

        return {
            **asdict(self.metadata),
            "capabilities": list(self.metadata.capabilities),
            "enabled": self.enabled,
            "runtime_status": runtime_status,
            "error": self.last_error,
        }
