"""
测试模块：akagi_backend/tests/unit/test_dataserver_api.py

描述：针对数据服务器 HTTP API 和 CORS 中间件的单元测试。
主要测试点：
- CORS 中间件对允许/禁止来源 (Origin) 的过滤逻辑。
- 获取、修改和重置设置 (Settings) 的 API 接口。
- 消息注入 (Ingest) 和系统关闭 (Shutdown) 接口的功能与错误处理。
- 修改配置时触发的资源缓存清理逻辑。
"""

import queue
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from akagi_ng.dataserver.api import _is_allowed_origin, cors_middleware, setup_routes
from akagi_ng.schema.types import SystemShutdownEvent, WebSocketClosedMessage


@pytest.fixture
async def cli():
    app = web.Application(middlewares=[cors_middleware])
    setup_routes(app)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


def test_is_allowed_origin():
    assert _is_allowed_origin(None) is True
    assert _is_allowed_origin("http://localhost:3000") is True
    assert _is_allowed_origin("http://127.0.0.1:8080") is True
    assert _is_allowed_origin("http://malicious.com") is False


async def test_cors_middleware_allowed(cli):
    resp = await cli.get("/api/settings", headers={"Origin": "http://localhost:3000"})
    assert resp.status == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


async def test_cors_middleware_forbidden(cli):
    resp = await cli.get("/api/settings", headers={"Origin": "http://evil.com"})
    assert resp.status == 403


async def test_get_settings(cli):
    with patch("akagi_ng.dataserver.api.get_settings_dict", return_value={"test": "val"}):
        resp = await cli.get("/api/settings")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["data"] == {"test": "val"}


async def test_ot3_management_routes_use_local_proxy_without_exposing_credentials(cli):
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok", "models": [], "queue_depth": {}}
    mock_client.key_status.return_value = {
        "plan": "pro",
        "expires_at": "2026-09-01",
        "usage_today": 1,
        "rpd": 6000,
        "rpm": 10.0,
        "topk": 3,
    }
    mock_client.models.return_value = [{"id": "4p-ot3", "game": "4p", "desc": "OT3"}]

    with patch("akagi_ng.dataserver.api.OT3ServiceClient", return_value=mock_client) as client_type:
        health = await cli.post("/api/ot3/health", json={"server": "https://server.example"})
        status = await cli.post(
            "/api/ot3/key-status",
            json={
                "server": "https://server.example",
                "api_key": "secret",
                "proxy": "socks5h://127.0.0.1:7890",
            },
        )
        models = await cli.post(
            "/api/ot3/models",
            json={"server": "https://server.example", "api_key": "secret"},
        )

    assert health.status == status.status == models.status == 200
    assert (await health.json())["data"]["status"] == "ok"
    assert (await status.json())["data"]["plan"] == "pro"
    assert (await models.json())["data"][0]["id"] == "4p-ot3"
    assert client_type.call_args_list[1].args == (
        "https://server.example",
        "secret",
        "socks5h://127.0.0.1:7890",
    )


async def test_ot3_redeem_and_purchase_routes_validate_input(cli):
    missing_code = await cli.post(
        "/api/ot3/redeem",
        json={"server": "https://server.example", "code": 123},
    )
    invalid_product = await cli.post(
        "/api/ot3/purchase/order",
        json={"server": "https://server.example", "product": 123, "redeem": True},
    )

    assert missing_code.status == 400
    assert invalid_product.status == 400


async def test_save_settings_invalid_json(cli):
    resp = await cli.post("/api/settings", data="not json")
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False


async def test_save_settings_validation_failed(cli):
    with patch("akagi_ng.dataserver.api.verify_settings", return_value=False):
        resp = await cli.post("/api/settings", json={"platform": "invalid"})
        assert resp.status == 400
        data = await resp.json()
        assert data["ok"] is False


async def test_save_settings_success(cli):
    with (
        patch("akagi_ng.dataserver.api.verify_settings", return_value=True),
        patch("akagi_ng.dataserver.api.get_settings_dict", return_value={}),
        patch("akagi_ng.dataserver.api.local_settings") as mock_settings,
    ):
        resp = await cli.post("/api/settings", json={"log_level": "DEBUG"})
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert mock_settings.update.called
        assert mock_settings.save.called


async def test_ot3_settings_apply_without_restart(cli):
    old = {"ot": {"online": False, "server": "https://old.example", "api_key": ""}}
    new = {"ot": {"online": True, "server": "https://new.example", "api_key": "secret"}}
    with (
        patch("akagi_ng.dataserver.api.verify_settings", return_value=True),
        patch("akagi_ng.dataserver.api.get_settings_dict", side_effect=[old, new]),
        patch("akagi_ng.dataserver.api.local_settings"),
    ):
        resp = await cli.post("/api/settings", json=new)

    assert resp.status == 200
    data = await resp.json()
    assert data["restartRequired"] is False


async def test_proxy_settings_are_reconciled_at_runtime(cli):
    old = {
        "mitm": {"enabled": True, "host": "127.0.0.1", "port": 6789, "upstream": ""},
        "mihomo": {"enabled": True},
    }
    new = {
        "mitm": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 6789,
            "upstream": "http://127.0.0.1:7897",
        },
        "mihomo": {"enabled": True},
    }
    mock_app = MagicMock()
    mock_settings = MagicMock()
    mock_settings.mitm.enabled = True

    with (
        patch("akagi_ng.dataserver.api.verify_settings", return_value=True),
        patch("akagi_ng.dataserver.api.get_settings_dict", side_effect=[old, new]),
        patch("akagi_ng.dataserver.api.local_settings", mock_settings),
        patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app),
    ):
        resp = await cli.post("/api/settings", json=new)

    assert resp.status == 200
    data = await resp.json()
    assert data["proxyChanged"] is True
    assert data["proxyError"] == ""
    assert data["restartRequired"] is False
    mock_app.mitm_client.stop.assert_called_once()
    mock_app.mitm_client.start.assert_called_once()


async def test_proxy_runtime_failure_is_reported_without_losing_saved_settings(cli):
    old = {
        "mitm": {"enabled": False, "host": "127.0.0.1", "port": 6789, "upstream": ""},
        "mihomo": {"enabled": False},
    }
    new = {
        "mitm": {"enabled": True, "host": "127.0.0.1", "port": 6789, "upstream": ""},
        "mihomo": {"enabled": False},
    }
    mock_app = MagicMock()
    mock_app.mitm_client.stop.side_effect = RuntimeError("transport details")
    mock_settings = MagicMock()
    mock_settings.mitm.enabled = True

    with (
        patch("akagi_ng.dataserver.api.verify_settings", return_value=True),
        patch("akagi_ng.dataserver.api.get_settings_dict", side_effect=[old, new]),
        patch("akagi_ng.dataserver.api.local_settings", mock_settings),
        patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app),
    ):
        resp = await cli.post("/api/settings", json=new)

    assert resp.status == 200
    data = await resp.json()
    assert data["proxyChanged"] is True
    assert "transport details" not in data["proxyError"]
    mock_settings.save.assert_called_once()


async def test_reset_settings(cli):
    with (
        patch("akagi_ng.dataserver.api.get_default_settings_dict", return_value={"default": True}),
        patch("akagi_ng.dataserver.api.local_settings"),
    ):
        resp = await cli.post("/api/settings/reset")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["data"] == {"default": True}


async def test_ingest_mjai_success(cli):
    mock_app = MagicMock()
    mock_app.electron_client = MagicMock()

    with patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app):
        resp = await cli.post("/api/ingest", json={"type": "websocket_closed"})
        assert resp.status == 200
        mock_app.electron_client.push_message.assert_called_once_with(WebSocketClosedMessage())


async def test_ingest_mjai_no_client(cli):
    mock_app = MagicMock()
    mock_app.electron_client = None

    with patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app):
        resp = await cli.post("/api/ingest", json={"type": "websocket_closed"})
        assert resp.status == 503


async def test_shutdown_no_message_queue(cli):
    mock_app = MagicMock()
    mock_app.shared_queue = None

    with patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app):
        resp = await cli.post("/api/shutdown")
        assert resp.status == 503
        data = await resp.json()
        assert data["ok"] is False
        assert "Message queue not available" in data["error"]


async def test_shutdown_with_message_queue(cli):
    mock_app = MagicMock()
    mock_app.shared_queue = queue.Queue()

    with patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app):
        resp = await cli.post("/api/shutdown")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["message"] == "Shutdown initiated"

    shutdown_msg = mock_app.shared_queue.get_nowait()
    assert isinstance(shutdown_msg, SystemShutdownEvent)


async def test_save_settings_triggers_cache_clear(cli):
    """验证保存设置时会触发缓存清理"""
    with (
        patch("akagi_ng.dataserver.api.verify_settings", return_value=True),
        patch("akagi_ng.dataserver.api.local_settings"),
        patch("akagi_ng.dataserver.api.clear_resource_cache") as mock_clear,
        patch("akagi_ng.dataserver.api.get_settings_dict", return_value={}),
    ):
        resp = await cli.post("/api/settings", json={"model_config": {"device": "cpu"}})
        assert resp.status == 200
        mock_clear.assert_called_once()


async def test_reset_settings_triggers_cache_clear(cli):
    """验证重置设置时会触发缓存清理"""
    with (
        patch("akagi_ng.dataserver.api.local_settings"),
        patch("akagi_ng.dataserver.api.clear_resource_cache") as mock_clear,
        patch("akagi_ng.dataserver.api.get_default_settings_dict", return_value={}),
    ):
        resp = await cli.post("/api/settings/reset")
        assert resp.status == 200
        mock_clear.assert_called_once()


async def test_save_settings_internal_error(cli):
    with (
        patch("akagi_ng.dataserver.api.verify_settings", return_value=True),
        patch("akagi_ng.dataserver.api.get_settings_dict", return_value={}),
        patch("akagi_ng.dataserver.api.local_settings") as mock_settings,
    ):
        mock_settings.update.side_effect = RuntimeError("boom")
        resp = await cli.post("/api/settings", json={"log_level": "DEBUG"})
        assert resp.status == 500
        data = await resp.json()
        assert data["ok"] is False


async def test_reset_settings_internal_error(cli):
    with (
        patch("akagi_ng.dataserver.api.get_default_settings_dict", return_value={"default": True}),
        patch("akagi_ng.dataserver.api.local_settings") as mock_settings,
    ):
        mock_settings.update.side_effect = RuntimeError("boom")
        resp = await cli.post("/api/settings/reset")
        assert resp.status == 500
        data = await resp.json()
        assert data["ok"] is False


async def test_shutdown_queue_full(cli):
    full_queue = queue.Queue(maxsize=1)
    full_queue.put(object())

    mock_app = MagicMock()
    mock_app.shared_queue = full_queue

    with patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app):
        resp = await cli.post("/api/shutdown")
        assert resp.status == 503
        data = await resp.json()
        assert data["ok"] is False
        assert data["error"] == "Message queue is full"


async def test_update_protocol_success(cli):
    """测试协议更新成功，包括文件保存和热重载逻辑"""
    from pathlib import Path

    from akagi_ng.bridge.majsoul.bridge import MajsoulBridge

    mock_app = MagicMock()
    mock_electron = MagicMock()
    mock_electron.bridge = MajsoulBridge()
    mock_app.electron_client = mock_electron

    mock_mitm = MagicMock()
    mock_mitm.addon.bridges = {"f1": MajsoulBridge()}
    mock_app.mitm_client = mock_mitm

    mock_path = MagicMock(spec=Path)

    with (
        patch("akagi_ng.dataserver.api.get_assets_dir", return_value=mock_path),
        patch("akagi_ng.dataserver.api.ensure_dir"),
        patch("akagi_ng.dataserver.api.get_app_context", return_value=mock_app),
    ):
        liqi_file_mock = mock_path.__truediv__.return_value

        resp = await cli.post("/api/protocol/update", json={"data": '{"test": 1}'})
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert liqi_file_mock.write_text.called


async def test_update_protocol_missing_data(cli):
    """测试协议更新：缺少 data 字段"""
    resp = await cli.post("/api/protocol/update", json={})
    assert resp.status == 400
    data = await resp.json()
    assert "Missing 'data' field" in data["error"]


async def test_update_protocol_invalid_json(cli):
    """测试协议更新：data 中包含无效 JSON"""
    resp = await cli.post("/api/protocol/update", json={"data": "invalid json"})
    assert resp.status == 400
    data = await resp.json()
    assert "Invalid JSON in liqi data" in data["error"]


async def test_update_protocol_os_error(cli):
    """测试协议更新：文件系统错误"""
    from pathlib import Path

    mock_path = MagicMock(spec=Path)
    liqi_file_mock = mock_path.__truediv__.return_value
    liqi_file_mock.write_text.side_effect = OSError("disk full")

    with (
        patch("akagi_ng.dataserver.api.get_assets_dir", return_value=mock_path),
        patch("akagi_ng.dataserver.api.ensure_dir"),
        patch("akagi_ng.dataserver.api.get_app_context", side_effect=RuntimeError("Should not be called")),
    ):
        resp = await cli.post("/api/protocol/update", json={"data": "{}"})
        assert resp.status == 500
        data = await resp.json()
        assert "disk full" in data["error"]
