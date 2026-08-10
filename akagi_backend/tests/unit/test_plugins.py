import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from akagi_ng.dataserver.api import cors_middleware, setup_routes
from akagi_ng.plugins.base import AkagiPlugin, PluginMetadata
from akagi_ng.plugins.manager import PluginConflictError, PluginManager
from akagi_ng.plugins.packages import DuplicatePluginError, PluginPackageError


class DummyPlugin(AkagiPlugin):
    metadata = PluginMetadata(
        id="dummy",
        name="Dummy",
        version="1.0.0",
        description="Test plugin",
        author="Tests",
        homepage="https://example.com",
        requires_mitm=True,
        capabilities=("test",),
    )


class ConflictingPlugin(AkagiPlugin):
    metadata = PluginMetadata(
        id="conflicting",
        name="Conflicting",
        version="1.0.0",
        description="Conflicts with dummy",
        author="Tests",
        homepage="https://example.com",
        requires_mitm=True,
        capabilities=("test",),
        conflicts=("dummy",),
    )


def _write_plugin_archive(
    path: Path,
    *,
    plugin_id: str = "external-test",
    version: str = "v1.0.0",
) -> Path:
    manifest = {
        "schema_version": 1,
        "id": plugin_id,
        "version": version,
        "entrypoint": "plugin.py:ExternalPlugin",
        "conflicts": [],
    }
    source = f"""\
from akagi_ng.plugins.base import AkagiPlugin, PluginMetadata

class ExternalPlugin(AkagiPlugin):
    metadata = PluginMetadata(
        id={plugin_id!r},
        name="External Test",
        version={version!r},
        description="package test",
        author="Tests",
        homepage="https://example.com",
        requires_mitm=False,
        capabilities=("test",),
    )
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("akagi-plugin.json", json.dumps(manifest))
        archive.writestr("plugin.py", source)
    return path


def test_plugin_manager_persists_enabled_state(tmp_path: Path):
    state_path = tmp_path / "plugins.json"
    manager = PluginManager(state_path=state_path, plugins=(DummyPlugin(),))

    manager.set_enabled("dummy", True)

    assert manager.requires_mitm() is True
    assert manager.list_plugins(mitm_running=False)[0]["runtime_status"] == "waiting_for_mitm"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["enabled"] == ["dummy"]
    assert persisted["mitm_required"] is True

    restored = PluginManager(state_path=state_path, plugins=(DummyPlugin(),))
    assert restored.get("dummy").enabled is True
    assert restored.list_plugins(mitm_running=True)[0]["runtime_status"] == "active"


@pytest.mark.parametrize(
    "case",
    [
        (False, False, False, 0, 0),  # effective 0 -> 0
        (False, False, True, 1, 0),  # effective 0 -> 1
        (False, True, False, 0, 1),  # effective 1 -> 0
        (True, False, True, 0, 0),  # effective 1 -> 1
    ],
)
async def test_plugin_api_reconciles_only_effective_mitm_transitions(
    tmp_path: Path,
    case: tuple[bool, bool, bool, int, int],
):
    settings_enabled, initial_enabled, target_enabled, expected_starts, expected_stops = case
    manager = PluginManager(state_path=tmp_path / "plugins.json", plugins=(DummyPlugin(),))
    if initial_enabled:
        manager.set_enabled("dummy", True)

    mitm_client = MagicMock()
    mitm_client.running = settings_enabled or initial_enabled
    mitm_client.start.return_value = True
    mitm_client.stop.return_value = True
    context = SimpleNamespace(plugin_manager=manager, mitm_client=mitm_client)
    app = web.Application(middlewares=[cors_middleware])
    setup_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        with (
            patch("akagi_ng.dataserver.api.get_app_context", return_value=context),
            patch("akagi_ng.dataserver.api.local_settings") as mock_settings,
        ):
            mock_settings.mitm.enabled = settings_enabled
            response = await client.get("/api/plugins")
            assert response.status == 200
            assert (await response.json())["data"][0]["enabled"] is initial_enabled

            response = await client.post("/api/plugins/dummy", json={"enabled": target_enabled})
            payload = await response.json()
            assert response.status == 200
            assert payload["data"]["enabled"] is target_enabled
            assert payload["proxyChanged"] is (settings_enabled is False and initial_enabled != target_enabled)
            assert mitm_client.start.call_count == expected_starts
            assert mitm_client.stop.call_count == expected_stops
    finally:
        await client.close()


def test_conflicting_plugins_cannot_be_enabled_together(tmp_path: Path):
    manager = PluginManager(
        state_path=tmp_path / "plugins.json",
        plugins=(DummyPlugin(), ConflictingPlugin()),
    )
    manager.set_enabled("dummy", True)

    with pytest.raises(PluginConflictError, match="dummy"):
        manager.set_enabled("conflicting", True)

    assert manager.get("dummy").enabled is True
    assert manager.get("conflicting").enabled is False


def test_external_plugin_install_blocks_duplicate_and_zip_traversal(tmp_path: Path):
    manager = PluginManager(
        state_path=tmp_path / "config" / "plugins.json",
        plugins_root=tmp_path / "plugins",
    )
    archive = _write_plugin_archive(tmp_path / "external.zip")
    plugin = manager.install_archive(archive)
    assert plugin.metadata.id == "external-test"
    assert (tmp_path / "plugins" / "external-test" / "plugin.py").is_file()

    with pytest.raises(DuplicatePluginError):
        manager.install_archive(archive)

    update = _write_plugin_archive(tmp_path / "update.zip", version="v1.0.1")
    updated = manager.install_archive(update, replace=True)
    assert updated.metadata.version == "v1.0.1"
    assert [path.name for path in (tmp_path / "plugins").iterdir()] == ["external-test"]

    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as package:
        package.writestr("../escape.py", "bad")
        package.writestr("akagi-plugin.json", "{}")
    with pytest.raises(PluginPackageError, match="Unsafe archive path"):
        manager.install_archive(unsafe)


async def test_local_plugin_import_api(tmp_path: Path):
    manager = PluginManager(
        state_path=tmp_path / "config" / "plugins.json",
        plugins_root=tmp_path / "plugins",
    )
    archive = _write_plugin_archive(tmp_path / "external.zip")
    context = SimpleNamespace(plugin_manager=manager, mitm_client=None)
    app = web.Application(middlewares=[cors_middleware])
    setup_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        with patch("akagi_ng.dataserver.api.get_app_context", return_value=context):
            form = FormData()
            form.add_field(
                "file",
                archive.read_bytes(),
                filename="external.akagi-plugin.zip",
                content_type="application/zip",
            )
            response = await client.post("/api/plugins/install/local", data=form)
            payload = await response.json()
            assert response.status == 200
            assert payload["data"]["id"] == "external-test"

            form = FormData()
            form.add_field("file", archive.read_bytes(), filename="duplicate.zip")
            response = await client.post("/api/plugins/install/local", data=form)
            assert response.status == 409
    finally:
        await client.close()
