import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from akagi_ng.bridge.majsoul import MajsoulBridge
from akagi_ng.dataserver.api import cors_middleware, setup_routes
from akagi_ng.plugins.base import AkagiPlugin, PluginMetadata
from akagi_ng.plugins.builtin.majsoul_max.vendor import basic_pb2, liqi_pb2
from akagi_ng.plugins.builtin.majsoul_max.vendor.mod import MajsoulMaxMod
from akagi_ng.plugins.manager import PluginManager


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


class Frame:
    def __init__(self, content: bytes, *, from_client: bool):
        self.content = content
        self.from_client = from_client
        self.injected = False


def _envelope(method: str, data: bytes) -> bytes:
    encoded = basic_pb2.BaseMessage(method_name=method, data=data).SerializeToString()
    # Liqi sends the empty bytes field explicitly; proto3's serializer omits it.
    return encoded if data else encoded + b"\x12\x00"


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


def test_majsoul_max_unlocks_character_response(tmp_path: Path):
    bridge = MajsoulBridge()
    protocol = bridge.liqi_proto
    method = ".lq.Lobby.fetchCharacterInfo"
    method_info = protocol.jsonProto["nested"]["lq"]["nested"]["Lobby"]["methods"]["fetchCharacterInfo"]
    request_class = protocol.get_message_class(method_info["requestType"])
    response_class = protocol.get_message_class(method_info["responseType"])
    assert request_class is not None
    assert response_class is not None

    modifier = MajsoulMaxMod(
        "test",
        settings_path=tmp_path / "settings.yaml",
        data_path=Path(__file__).parents[3] / "assets" / "plugins" / "majsoul-max" / "max_data.yaml",
    )
    message_id = 31
    request = Frame(
        b"\x02" + message_id.to_bytes(2, "little") + _envelope(method, request_class().SerializeToString()),
        from_client=True,
    )

    liqi_pb2.set_lqi_proto(protocol)
    try:
        modified, *_ = modifier.main(request, protocol)
    finally:
        liqi_pb2.clear_lqi_proto()
    assert modified is False
    assert protocol.parse(request.content)["method"] == method

    response = Frame(
        b"\x03" + message_id.to_bytes(2, "little") + _envelope("", response_class().SerializeToString()),
        from_client=False,
    )
    liqi_pb2.set_lqi_proto(protocol)
    try:
        modified, dropped, output, *_ = modifier.main(response, protocol)
    finally:
        liqi_pb2.clear_lqi_proto()

    assert modified is True
    assert dropped is False
    parsed = protocol.parse(output)
    assert parsed["method"] == method
    assert len(parsed["data"]["characters"]) >= 100
    assert len(parsed["data"]["skins"]) >= 400
    assert parsed["data"]["mainCharacterId"] == 200001

    envelope = basic_pb2.BaseMessage.FromString(output[3:])
    unlocked = response_class.FromString(envelope.data)
    assert len(unlocked.characters) >= 100
    assert len(unlocked.skins) >= 400
    assert unlocked.main_character_id == 200001
