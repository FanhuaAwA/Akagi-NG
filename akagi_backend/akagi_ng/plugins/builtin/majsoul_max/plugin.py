from __future__ import annotations

from typing import TYPE_CHECKING

from mitmproxy import ctx

from akagi_ng.bridge.majsoul import MajsoulBridge
from akagi_ng.core.paths import ensure_dir, get_assets_dir, get_settings_dir
from akagi_ng.plugins.base import AkagiPlugin, PluginMetadata
from akagi_ng.plugins.builtin.majsoul_max.vendor import liqi_pb2
from akagi_ng.plugins.builtin.majsoul_max.vendor.mod import MajsoulMaxMod
from akagi_ng.plugins.logger import logger

if TYPE_CHECKING:
    import mitmproxy.http

    from akagi_ng.bridge.base import BaseBridge

MAJSOUL_MAX_VERSION = "v2026.07.07"


class MajsoulMaxPlugin(AkagiPlugin):
    metadata = PluginMetadata(
        id="majsoul-max",
        name="雀魂 MAX",
        version=MAJSOUL_MAX_VERSION,
        description="本地解锁雀魂角色、皮肤、装扮、称号与加载插图。",
        author="Avenshy（NG 适配）",
        homepage="https://github.com/Avenshy/MajsoulMax",
        requires_mitm=True,
        capabilities=("websocket.modify", "websocket.inject", "local.cosmetic_unlock"),
        risk_notice="仅在本机显示，可能违反游戏服务条款并存在账号处罚风险。启用即表示你理解并自行承担风险。",
    )

    def __init__(self) -> None:
        super().__init__()
        settings_dir = ensure_dir(get_settings_dir() / "plugins" / self.metadata.id)
        self._settings_path = settings_dir / "settings.yaml"
        self._data_path = get_assets_dir() / "plugins" / self.metadata.id / "max_data.yaml"
        self._mod: MajsoulMaxMod | None = None

    def on_enable(self) -> None:
        if self._mod is None:
            self._mod = MajsoulMaxMod(
                MAJSOUL_MAX_VERSION,
                settings_path=self._settings_path,
                data_path=self._data_path,
            )

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow, bridge: BaseBridge) -> None:
        if (
            self._mod is None
            or not isinstance(bridge, MajsoulBridge)
            or flow.request.path == "/ob"
            or flow.websocket is None
        ):
            return

        message = flow.websocket.messages[-1]
        if message.injected:
            return

        liqi_pb2.set_lqi_proto(bridge.liqi_proto)
        try:
            modify, drop, content, inject, inject_content = self._mod.main(message, bridge.liqi_proto)
        finally:
            liqi_pb2.clear_lqi_proto()

        if drop:
            message.drop()
        if inject:
            ctx.master.commands.call("inject.websocket", flow, True, inject_content, False)
        if modify:
            message.content = content
            logger.debug("MajsoulMax modified a WebSocket message")
