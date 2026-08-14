"""
测试模块：akagi_backend/tests/unit/test_dataserver_lifecycle.py

描述：针对数据服务器 (DataServer) 生命周期管理和 SSE 消息分发的单元测试。
主要测试点：
- DataServer 的启动 (Run)、清理和停止 (Stop) 流程。
- 通过 SSEManager 广播事件、推荐和通知的转发逻辑。
- 异步事件循环 (Event Loop) 的正确管理与关闭。
"""

import asyncio
import inspect
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from aiohttp import web

from akagi_ng.dataserver.dataserver import DataServer, DataServerStartupError
from akagi_ng.settings import local_settings


@pytest.fixture
def ds():
    return DataServer(host="127.0.0.1", external_port=8000)


def _reserve_consecutive_ports(count: int) -> tuple[int, list[socket.socket]]:
    """Reserve a deterministic consecutive range for startup fallback tests."""
    for base_port in range(20000, 60000 - count):
        listeners: list[socket.socket] = []
        try:
            for port in range(base_port, base_port + count):
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.bind(("127.0.0.1", port))
                listener.listen()
                listeners.append(listener)
        except OSError:
            for listener in listeners:
                listener.close()
            continue
        return base_port, listeners
    raise RuntimeError(f"Could not reserve {count} consecutive test ports")


async def test_dataserver_lifecycle_basic(ds) -> None:
    # 直接设置运行状态，无需调用 start
    ds.running = True
    ds.loop = MagicMock()
    ds.loop.is_running.return_value = True

    # 验证停止逻辑
    ds.stop()

    # Verify loop stop was scheduled
    assert ds.loop.stop.called or ds.loop.call_soon_threadsafe.called


def test_dataserver_proxy_methods(ds):
    """测试 DataServer 转发给 SSEManager 的方法"""
    ds.sse_manager = MagicMock()

    # broadcast_event
    ds.broadcast_event("test_type", {"data": 1})
    ds.sse_manager.broadcast_event.assert_called()

    # send_recommendations
    ds.send_recommendations({"recommendations": ["discard"], "action": "discard"})
    assert ds.sse_manager.broadcast_event.called

    # send_notifications
    ds.send_notifications(["1001"])
    ds.sse_manager.broadcast_event.assert_called()


def test_dataserver_run_logic(ds):
    """测试 run 方法的启动、运行和清理逻辑"""
    with patch("asyncio.new_event_loop") as mock_new_loop:
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mock_new_loop.return_value = mock_loop
        ds.sse_manager = MagicMock()

        def discard_coroutine(awaitable):
            if inspect.iscoroutine(awaitable):
                awaitable.close()

        mock_loop.run_until_complete.side_effect = discard_coroutine

        # Mock run_forever to stop immediately
        mock_loop.run_forever.return_value = None

        # 1. 启动异常收集
        with patch("aiohttp.web.Application", side_effect=RuntimeError("startup fail")):
            ds.run()
            # 应该调用了 loop.close()
            mock_loop.close.assert_called()

        mock_loop.reset_mock()

        normal_server = DataServer(host="127.0.0.1", external_port=8000)
        normal_server.sse_manager = MagicMock()

        # 2. 正常运行流程拦截
        with (
            patch("aiohttp.web.Application"),
            patch("akagi_ng.dataserver.dataserver.SSEManager"),
            patch("aiohttp.web.AppRunner"),
            patch("aiohttp.web.TCPSite"),
            patch.object(local_settings.server, "port", normal_server.external_port),
        ):
            # 让 run_until_complete 立即结束以模拟 stop_event
            normal_server.run()

            # 验证清理工作
            assert mock_loop.run_until_complete.call_count >= 3
            mock_loop.close.assert_called()
            assert normal_server.startup_ready_event.is_set()
            assert not normal_server.startup_failed_event.is_set()


def test_dataserver_stop_signal(ds):
    """验证 stop 方法会正确停止事件循环"""
    ds.loop = MagicMock(spec=asyncio.AbstractEventLoop)
    # Ensure is_running returns True to trigger stop logic
    ds.loop.is_running.return_value = True
    ds.running = True

    ds.stop()

    assert ds.running is False
    ds.loop.call_soon_threadsafe.assert_called()


@pytest.mark.asyncio
async def test_dataserver_stop_closes_active_sse_without_full_timeout():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = DataServer(host="127.0.0.1", external_port=port)
    with patch.object(local_settings.server, "port", port):
        server.start()
        assert server.wait_until_ready(timeout=2.0) is True
    for _ in range(100):
        if server.running:
            break
        await asyncio.sleep(0.01)
    assert server.running

    session = aiohttp.ClientSession()
    response = None
    try:
        response = await session.get(f"http://127.0.0.1:{port}/sse?clientId=shutdown-test")
        assert response.status == 200
        assert await response.content.readline() == b": connected\n"

        started = time.monotonic()
        await asyncio.to_thread(server.stop)
        elapsed = time.monotonic() - started

        assert elapsed < 1.0
        assert not server.is_alive()
    finally:
        if response is not None:
            response.close()
        await session.close()
        if server.is_alive():
            server.stop()


def test_dataserver_stop_during_loop_creation_cancels_startup(monkeypatch):
    original_new_event_loop = asyncio.new_event_loop
    loop_creation_entered = threading.Event()
    release_loop_creation = threading.Event()

    def delayed_new_event_loop():
        loop_creation_entered.set()
        assert release_loop_creation.wait(timeout=2.0)
        return original_new_event_loop()

    monkeypatch.setattr("akagi_ng.dataserver.dataserver.asyncio.new_event_loop", delayed_new_event_loop)
    server = DataServer(host="127.0.0.1", external_port=28000)
    server.start()
    assert loop_creation_entered.wait(timeout=1.0)

    stopper = threading.Thread(target=server.stop)
    stopper.start()
    time.sleep(0.05)
    release_loop_creation.set()
    stopper.join(timeout=2.0)
    server.join(timeout=2.0)

    assert not stopper.is_alive()
    assert not server.is_alive()
    assert server.running is False


def test_dataserver_reports_failure_when_all_fallback_ports_are_occupied():
    base_port, listeners = _reserve_consecutive_ports(10)
    server = DataServer(host="127.0.0.1", external_port=base_port)

    try:
        server.start()
        with pytest.raises(DataServerStartupError, match="Could not bind DataServer") as exc_info:
            server.wait_until_ready(timeout=2.0)
        server.join(timeout=2.0)

        assert isinstance(exc_info.value.__cause__, OSError)
        assert server.startup_error is exc_info.value
        assert server.startup_failed_event.is_set()
        assert not server.startup_ready_event.is_set()
        assert not server.is_alive()
    finally:
        for listener in listeners:
            listener.close()
        if server.is_alive():
            server.stop()


def test_dataserver_skips_occupied_ports_and_reports_actual_bound_port():
    base_port, listeners = _reserve_consecutive_ports(3)
    listeners[-1].close()
    occupied_listeners = listeners[:-1]
    server = DataServer(host="127.0.0.1", external_port=base_port)

    try:
        with (
            patch.object(local_settings.server, "port", base_port),
            patch.object(type(local_settings), "save") as save_settings,
        ):
            server.start()
            assert server.wait_until_ready(timeout=2.0) is True

            assert server.external_port == base_port + 2
            assert server.running is True
            assert server.startup_ready_event.is_set()
            assert not server.startup_failed_event.is_set()
            assert server.startup_error is None
            assert local_settings.server.port == base_port + 2
            save_settings.assert_called_once_with()
    finally:
        server.stop()
        for listener in occupied_listeners:
            listener.close()


def test_dataserver_preserves_permission_error_details_and_exception_chain():
    permission_error = OSError(13, "Permission denied", None, 10013)
    server = DataServer(host="127.0.0.1", external_port=28000)

    async def reject_bind(_site):
        raise permission_error

    with (
        patch.object(web.TCPSite, "start", reject_bind),
        patch("akagi_ng.dataserver.dataserver.logger") as mock_logger,
    ):
        server.start()
        with pytest.raises(DataServerStartupError) as exc_info:
            server.wait_until_ready(timeout=2.0)
        server.join(timeout=2.0)

    warnings = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert len(warnings) == 10
    assert all("host='127.0.0.1'" in message for message in warnings)
    assert "port=28000" in warnings[0]
    assert "errno=13" in warnings[0]
    assert "winerror=10013" in warnings[0]
    assert "PermissionError" in warnings[0]
    assert exc_info.value.__cause__ is permission_error
    assert server.startup_error is exc_info.value
    assert server.startup_failed_event.is_set()
    assert not server.is_alive()


def test_dataserver_stops_at_the_maximum_tcp_port():
    permission_error = OSError(13, "Permission denied", None, 10013)
    server = DataServer(host="127.0.0.1", external_port=65534)

    async def reject_bind(_site):
        raise permission_error

    with (
        patch.object(web.TCPSite, "start", reject_bind),
        patch("akagi_ng.dataserver.dataserver.logger") as mock_logger,
    ):
        server.start()
        with pytest.raises(DataServerStartupError, match=r"65534-65535"):
            server.wait_until_ready(timeout=2.0)
        server.join(timeout=2.0)

    warnings = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert len(warnings) == 2
    assert "port=65534" in warnings[0]
    assert "port=65535" in warnings[1]


def test_dataserver_reports_failures_before_event_loop_initialization():
    server = DataServer(host="127.0.0.1", external_port=28000)
    server.sse_manager.start = MagicMock(side_effect=RuntimeError("SSE initialization failed"))

    server.start()
    with pytest.raises(RuntimeError, match="SSE initialization failed"):
        server.wait_until_ready(timeout=2.0)
    server.join(timeout=2.0)

    assert server.startup_failed_event.is_set()
    assert not server.startup_ready_event.is_set()
    assert not server.is_alive()


def test_dataserver_releases_bound_port_before_reporting_settings_save_failure():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = DataServer(host="127.0.0.1", external_port=port)
    different_configured_port = port - 1
    with (
        patch.object(local_settings.server, "port", different_configured_port),
        patch.object(type(local_settings), "save", side_effect=OSError(5, "settings write failed")),
    ):
        server.start()
        with pytest.raises(OSError, match="settings write failed"):
            server.wait_until_ready(timeout=2.0)
        server.join(timeout=2.0)

    assert not server.is_alive()
    assert server.startup_failed_event.is_set()
    with socket.socket() as rebound:
        rebound.bind(("127.0.0.1", port))
