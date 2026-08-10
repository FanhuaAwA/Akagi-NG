from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from akagi_ng.core.paths import ensure_dir, get_plugins_dir, get_settings_dir
from akagi_ng.plugins.base import AkagiPlugin
from akagi_ng.plugins.logger import logger
from akagi_ng.plugins.packages import (
    DEFAULT_CATALOG_URL,
    PLUGIN_ID_PATTERN,
    CatalogEntry,
    DuplicatePluginError,
    PluginManifest,
    PluginPackageError,
    download_plugin,
    extract_archive_to_staging,
    fetch_catalog,
    load_installed_plugin,
)

if TYPE_CHECKING:
    import mitmproxy.http

    from akagi_ng.bridge.base import BaseBridge

STATE_VERSION = 2


class PluginConflictError(PluginPackageError):
    pass


class PluginManager:
    """Discover, install, enable, and isolate independently packaged plugins."""

    def __init__(
        self,
        state_path: Path | None = None,
        plugins: Iterable[AkagiPlugin] | None = None,
        *,
        plugins_root: Path | None = None,
        catalog_url: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state_path = state_path or ensure_dir(get_settings_dir()) / "plugins.json"
        default_root = get_plugins_dir() if state_path is None else self._state_path.parent / "installed-plugins"
        self._plugins_root = ensure_dir(plugins_root or default_root)
        self._catalog_url = catalog_url or os.environ.get("AKAGI_PLUGIN_CATALOG_URL", DEFAULT_CATALOG_URL)
        self._plugins: dict[str, AkagiPlugin] = {}
        self._manifests: dict[str, PluginManifest] = {}
        self._external_modules: dict[str, ModuleType] = {}

        if plugins is not None:
            for plugin in plugins:
                self.register(plugin)
        else:
            self._discover_installed_plugins()
        self._load_state()

    @property
    def plugins_root(self) -> Path:
        return self._plugins_root

    def _validate_plugin(self, plugin: AkagiPlugin) -> None:
        plugin_id = plugin.metadata.id
        if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise ValueError(f"Invalid plugin id: {plugin_id}")
        for conflict in plugin.metadata.conflicts:
            if not PLUGIN_ID_PATTERN.fullmatch(conflict) or conflict == plugin_id:
                raise ValueError(f"Invalid conflict id for plugin {plugin_id}: {conflict}")

    def register(
        self,
        plugin: AkagiPlugin,
        *,
        manifest: PluginManifest | None = None,
        module: ModuleType | None = None,
    ) -> None:
        self._validate_plugin(plugin)
        plugin_id = plugin.metadata.id
        with self._lock:
            if plugin_id in self._plugins:
                raise DuplicatePluginError(f"Duplicate plugin id: {plugin_id}")
            self._plugins[plugin_id] = plugin
            if manifest is not None:
                self._manifests[plugin_id] = manifest
            if module is not None:
                self._external_modules[plugin_id] = module

    def _discover_installed_plugins(self) -> None:
        for plugin_dir in sorted(self._plugins_root.iterdir(), key=lambda item: item.name):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
                continue
            try:
                plugin, module = load_installed_plugin(plugin_dir)
                from akagi_ng.plugins.packages import load_manifest

                self.register(plugin, manifest=load_manifest(plugin_dir / "akagi-plugin.json"), module=module)
            except Exception:
                logger.exception(f"Failed to load installed plugin from {plugin_dir}")

    def list_plugins(self, *, mitm_running: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            result: list[dict[str, Any]] = []
            for plugin in self._plugins.values():
                item = plugin.to_public_dict(mitm_running=mitm_running)
                item["source"] = "external" if plugin.metadata.id in self._manifests else "runtime"
                result.append(item)
            return result

    def get(self, plugin_id: str) -> AkagiPlugin | None:
        with self._lock:
            return self._plugins.get(plugin_id)

    def _enabled_conflicts(self, plugin: AkagiPlugin) -> list[str]:
        conflicts: list[str] = []
        for other in self._plugins.values():
            if not other.enabled or other.metadata.id == plugin.metadata.id:
                continue
            if other.metadata.id in plugin.metadata.conflicts or plugin.metadata.id in other.metadata.conflicts:
                conflicts.append(other.metadata.id)
        return conflicts

    def set_enabled(self, plugin_id: str, enabled: bool) -> AkagiPlugin:
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                raise KeyError(plugin_id)
            if plugin.enabled == enabled:
                return plugin
            if enabled:
                conflicts = self._enabled_conflicts(plugin)
                if conflicts:
                    raise PluginConflictError(
                        f"Plugin {plugin_id} conflicts with enabled plugin(s): {', '.join(conflicts)}"
                    )

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

    def request(self, flow: mitmproxy.http.HTTPFlow) -> None:
        self._run_hook("request", flow)

    def response(self, flow: mitmproxy.http.HTTPFlow) -> None:
        self._run_hook("response", flow)

    def websocket_start(self, flow: mitmproxy.http.HTTPFlow, bridge: BaseBridge) -> None:
        self._run_hook("websocket_start", flow, bridge)

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow, bridge: BaseBridge) -> None:
        self._run_hook("websocket_message", flow, bridge)

    def websocket_end(self, flow: mitmproxy.http.HTTPFlow, bridge: BaseBridge) -> None:
        self._run_hook("websocket_end", flow, bridge)

    def _run_hook(self, hook_name: str, *args: Any) -> None:
        for plugin in self._enabled_plugins():
            try:
                getattr(plugin, hook_name)(*args)
                plugin.last_error = ""
            except Exception as exc:
                plugin.last_error = str(exc)
                logger.exception(f"Plugin {hook_name} hook failed: {plugin.metadata.id}")

    def _enabled_plugins(self) -> tuple[AkagiPlugin, ...]:
        with self._lock:
            return tuple(plugin for plugin in self._plugins.values() if plugin.enabled)

    def catalog(self) -> list[dict[str, Any]]:
        entries = fetch_catalog(self._catalog_url)
        with self._lock:
            result: list[dict[str, Any]] = []
            for entry in entries:
                item = entry.to_public_dict()
                installed = self._plugins.get(entry.id)
                item["installed"] = installed is not None
                item["installed_version"] = installed.metadata.version if installed else ""
                item["update_available"] = bool(installed and installed.metadata.version != entry.version)
                result.append(item)
            return result

    def install_remote(self, plugin_id: str, *, replace: bool = False) -> AkagiPlugin:
        entries = fetch_catalog(self._catalog_url)
        matches = [entry for entry in entries if entry.id == plugin_id]
        if not matches:
            raise KeyError(plugin_id)
        if len(matches) > 1:
            raise DuplicatePluginError(f"Duplicate plugin id in catalog: {plugin_id}")
        entry: CatalogEntry = matches[0]
        fd, temporary_name = tempfile.mkstemp(
            dir=self._plugins_root,
            prefix=f".download-{plugin_id}-",
            suffix=".zip",
        )
        os.close(fd)
        archive_path = Path(temporary_name)
        try:
            download_plugin(entry, archive_path)
            return self.install_archive(archive_path, replace=replace)
        finally:
            archive_path.unlink(missing_ok=True)

    def install_archive(self, archive_path: Path, *, replace: bool = False) -> AkagiPlugin:
        manifest, staging = extract_archive_to_staging(archive_path, self._plugins_root)
        target = self._plugins_root / manifest.id
        backup = self._plugins_root / f".backup-{manifest.id}-{uuid.uuid4().hex}"
        try:
            with self._lock:
                existing = self._plugins.get(manifest.id)
                if (existing is not None or target.exists()) and not replace:
                    raise DuplicatePluginError(
                        f"Plugin {manifest.id} is already installed; duplicate installation was blocked"
                    )
                if existing is not None and existing.enabled:
                    raise PluginPackageError(f"Disable plugin {manifest.id} before updating it")
                if target.exists():
                    os.replace(target, backup)
                os.replace(staging, target)
                try:
                    plugin, module = load_installed_plugin(target, manifest)
                    self._validate_plugin(plugin)
                except Exception:
                    shutil.rmtree(target, ignore_errors=True)
                    if backup.exists():
                        os.replace(backup, target)
                    raise

                self._plugins[manifest.id] = plugin
                self._manifests[manifest.id] = manifest
                self._external_modules[manifest.id] = module
                self._save_state()
            shutil.rmtree(backup, ignore_errors=True)
            return plugin
        finally:
            shutil.rmtree(staging, ignore_errors=True)

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
                if plugin is None:
                    continue
                conflicts = self._enabled_conflicts(plugin)
                if conflicts:
                    plugin.last_error = f"Disabled because it conflicts with: {', '.join(conflicts)}"
                    logger.error(f"Refused conflicting persisted plugin state: {plugin_id} vs {conflicts}")
                    continue
                plugin.on_enable()
                plugin.enabled = True
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
