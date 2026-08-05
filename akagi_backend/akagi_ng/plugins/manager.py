from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from akagi_ng.core.paths import ensure_dir, get_settings_dir
from akagi_ng.plugins.base import AkagiPlugin
from akagi_ng.plugins.builtin.majsoul_max import MajsoulMaxPlugin
from akagi_ng.plugins.logger import logger

if TYPE_CHECKING:
    import mitmproxy.http

    from akagi_ng.bridge.base import BaseBridge

PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
STATE_VERSION = 1


class PluginManager:
    """注册插件、持久化启用状态，并隔离插件异常。"""

    def __init__(self, state_path: Path | None = None, plugins: Iterable[AkagiPlugin] | None = None) -> None:
        self._lock = threading.RLock()
        self._state_path = state_path or ensure_dir(get_settings_dir()) / "plugins.json"
        self._plugins: dict[str, AkagiPlugin] = {}
        for plugin in plugins if plugins is not None else (MajsoulMaxPlugin(),):
            self.register(plugin)
        self._load_state()

    def register(self, plugin: AkagiPlugin) -> None:
        plugin_id = plugin.metadata.id
        if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise ValueError(f"Invalid plugin id: {plugin_id}")
        with self._lock:
            if plugin_id in self._plugins:
                raise ValueError(f"Duplicate plugin id: {plugin_id}")
            self._plugins[plugin_id] = plugin

    def list_plugins(self, *, mitm_running: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            return [plugin.to_public_dict(mitm_running=mitm_running) for plugin in self._plugins.values()]

    def get(self, plugin_id: str) -> AkagiPlugin | None:
        with self._lock:
            return self._plugins.get(plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> AkagiPlugin:
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                raise KeyError(plugin_id)
            if plugin.enabled == enabled:
                return plugin

            previous = plugin.enabled
            try:
                if enabled:
                    plugin.on_enable()
                else:
                    plugin.on_disable()
                plugin.enabled = enabled
                plugin.last_error = ""
                self._save_state()
            except Exception as exc:
                plugin.enabled = previous
                plugin.last_error = str(exc)
                logger.exception(f"Plugin lifecycle failed: {plugin_id}")
                raise
            return plugin

    def requires_mitm(self) -> bool:
        with self._lock:
            return any(plugin.enabled and plugin.metadata.requires_mitm for plugin in self._plugins.values())

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow, bridge: BaseBridge) -> None:
        for plugin in self._enabled_plugins():
            try:
                plugin.websocket_message(flow, bridge)
                plugin.last_error = ""
            except Exception as exc:
                plugin.last_error = str(exc)
                logger.exception(f"Plugin WebSocket hook failed: {plugin.metadata.id}")

    def request(self, flow: mitmproxy.http.HTTPFlow) -> None:
        for plugin in self._enabled_plugins():
            try:
                plugin.request(flow)
                plugin.last_error = ""
            except Exception as exc:
                plugin.last_error = str(exc)
                logger.exception(f"Plugin request hook failed: {plugin.metadata.id}")

    def _enabled_plugins(self) -> tuple[AkagiPlugin, ...]:
        with self._lock:
            return tuple(plugin for plugin in self._plugins.values() if plugin.enabled)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            self._save_state()
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            enabled_ids = payload.get("enabled", []) if isinstance(payload, dict) else []
            if not isinstance(enabled_ids, list) or not all(isinstance(item, str) for item in enabled_ids):
                raise ValueError("enabled must be a list of plugin ids")
            for plugin_id in enabled_ids:
                plugin = self._plugins.get(plugin_id)
                if plugin is not None:
                    plugin.on_enable()
                    plugin.enabled = True
            # Rewrite older state files with derived runtime requirements so the
            # Electron TUN lifecycle can make the same effective-MITM decision.
            self._save_state()
        except Exception:
            logger.exception(f"Failed to load plugin state from {self._state_path}; all plugins remain disabled")

    def _save_state(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "enabled": [plugin.metadata.id for plugin in self._plugins.values() if plugin.enabled],
            "mitm_required": self.requires_mitm(),
        }
        ensure_dir(self._state_path.parent)
        temp_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self._state_path)
