"""
测试模块：akagi_backend/tests/unit/test_electron_client.py

描述：针对 Electron 注入客户端 (Electron Client) 的单元测试，负责与 Electron 渲染进程的 WebSocket 通信。
主要测试点：
- 客户端工厂 (create_electron_client) 的实例创建。
- 雀魂 (Majsoul) 和天凤 (Tenhou) 客户端的连接生命周期管理。
- 调试器事件 (Debugger Detached) 的处理。
- WebSocket 帧 (Frame) 的 push 与分发逻辑，包括队列满时的丢弃策略。
"""

import base64
import contextlib
import queue
from unittest.mock import patch

import pytest

from akagi_ng.electron_client import (
    MajsoulElectronClient,
    TenhouElectronClient,
    create_electron_client,
)
from akagi_ng.schema.types import (
    DebuggerDetachedMessage,
    EndGameEvent,
    TsumoEvent,
    WebSocketClosedMessage,
    WebSocketCreatedMessage,
    WebSocketFrameMessage,
)

# ==========================================================
# Factory Tests
# ==========================================================


def test_create_electron_client():
    q = queue.Queue()
    client = create_electron_client("majsoul", shared_queue=q)
    assert isinstance(client, MajsoulElectronClient)

    client = create_electron_client("tenhou", shared_queue=q)
    assert isinstance(client, TenhouElectronClient)

    client = create_electron_client("auto", shared_queue=q)
    assert isinstance(client, MajsoulElectronClient)


# ==========================================================
# Majsoul Client Tests
# ==========================================================


@pytest.fixture
def ms_client():
    q = queue.Queue()
    with patch("akagi_ng.electron_client.majsoul.MajsoulBridge") as mock_bridge_cls:
        mock_bridge = mock_bridge_cls.return_value
        mock_bridge.game_ended = False
        client = MajsoulElectronClient(shared_queue=q)
    client.start()
    return client


def test_majsoul_lifecycle(ms_client):
    # Created
    ms_client.push_message(WebSocketCreatedMessage(request_id="majsoul-game", url="wss://majsoul.com/game"))
    assert ms_client._active_connections == 1
    assert ms_client.message_queue.get(timeout=2.0).code == "client_connected"

    # Closed
    ms_client.push_message(WebSocketClosedMessage(request_id="majsoul-game"))
    assert ms_client._active_connections == 0
    assert ms_client.message_queue.get(timeout=2.0).code == "game_disconnected"


def test_majsoul_debugger_events(ms_client):
    ms_client._active_connections = 1
    ms_client.push_message(DebuggerDetachedMessage())
    assert ms_client._active_connections == 0
    assert ms_client.message_queue.get(timeout=2.0).code == "game_disconnected"


def test_majsoul_frames(ms_client):
    ms_client.bridge.parse.return_value = [TsumoEvent(actor=0, pai="1m"), EndGameEvent()]
    ms_client.push_message(
        WebSocketFrameMessage(
            request_id="majsoul-game",
            direction="inbound",
            data=base64.b64encode(b"raw").decode(),
        )
    )

    assert ms_client.message_queue.get(timeout=2.0).type == "tsumo"
    assert ms_client.message_queue.get(timeout=2.0).type == "end_game"
    assert ms_client.message_queue.get(timeout=2.0).code == "return_lobby"


# ==========================================================
# Tenhou Client Tests
# ==========================================================


@pytest.fixture
def th_client():
    q = queue.Queue()
    with patch("akagi_ng.electron_client.tenhou.TenhouBridge") as mock_bridge_cls:
        mock_bridge = mock_bridge_cls.return_value
        mock_bridge.game_ended = False
        client = TenhouElectronClient(shared_queue=q)
    client.start()
    return client


def test_tenhou_lifecycle(th_client):
    th_client.push_message(WebSocketCreatedMessage(request_id="tenhou-game", url="https://tenhou.net/3/"))
    assert th_client._active_connections == 1
    assert th_client.message_queue.get(timeout=2.0).code == "client_connected"

    th_client.push_message(WebSocketClosedMessage(request_id="tenhou-game"))
    assert th_client._active_connections == 0
    assert th_client.message_queue.get(timeout=2.0).code == "game_disconnected"


def test_tenhou_non_target_url_ignored(th_client):
    th_client.push_message(WebSocketCreatedMessage(request_id="other", url="wss://google.com/socket"))
    assert th_client._active_connections == 0
    assert th_client.message_queue.empty()


def test_tenhou_frames(th_client):
    th_client.push_message(WebSocketCreatedMessage(request_id="tenhou-game", url="wss://tenhou.net/game"))
    assert th_client.message_queue.get(timeout=2.0).code == "client_connected"

    # Text frame
    th_client.bridge.parse.return_value = [TsumoEvent(actor=0, pai="1m")]
    th_client.push_message(WebSocketFrameMessage(request_id="tenhou-game", direction="inbound", data="HELO"))

    msg = th_client.message_queue.get(timeout=2.0)
    assert msg.type == "tsumo"

    # Binary frame
    th_client.push_message(
        WebSocketFrameMessage(
            request_id="tenhou-game",
            direction="inbound",
            opcode=2,
            data=base64.b64encode(b"binary").decode(),
        )
    )
    th_client.bridge.parse.assert_called_with(b"binary")

    # Exception handle
    th_client.bridge.parse.side_effect = Exception("crash")
    th_client.push_message(WebSocketFrameMessage(request_id="tenhou-game", direction="inbound", data="FAIL"))

    # Process remaining binary message if any
    with contextlib.suppress(queue.Empty):
        th_client.message_queue.get(timeout=0.1)

    assert th_client.message_queue.empty()


def test_tenhou_outbound_frame_ignored(th_client):
    th_client.push_message(WebSocketFrameMessage(request_id="tenhou-game", direction="outbound", data="ignore"))
    th_client.bridge.parse.assert_not_called()
    assert th_client.message_queue.empty()


def test_majsoul_queue_full_drops_event():
    q = queue.Queue(maxsize=1)
    q.put_nowait({"sentinel": True})

    with patch("akagi_ng.electron_client.majsoul.MajsoulBridge"):
        client = MajsoulElectronClient(shared_queue=q)
    client.start()

    client.push_message(WebSocketCreatedMessage(request_id="majsoul-game", url="wss://majsoul.com/game"))

    assert client._active_connections == 1
    assert q.qsize() == 1


def test_tenhou_queue_full_drops_event():
    q = queue.Queue(maxsize=1)
    q.put_nowait({"sentinel": True})

    with patch("akagi_ng.electron_client.tenhou.TenhouBridge") as mock_bridge_cls:
        mock_bridge = mock_bridge_cls.return_value
        mock_bridge.parse.return_value = [TsumoEvent(actor=0, pai="1m")]
        client = TenhouElectronClient(shared_queue=q)
    client.start()

    client.push_message(WebSocketFrameMessage(request_id="tenhou-game", direction="inbound", data="HELO"))

    assert q.qsize() == 1


def test_tenhou_ignores_unrelated_close_and_duplicate_create(th_client):
    th_client.bridge.reset.reset_mock()
    th_client.push_message(WebSocketCreatedMessage(request_id="tenhou-game", url="wss://tenhou.net/game"))
    assert th_client.message_queue.get(timeout=2.0).code == "client_connected"
    th_client.bridge.reset.assert_called_once_with()

    th_client.push_message(WebSocketCreatedMessage(request_id="tenhou-game", url="wss://tenhou.net/game"))
    th_client.push_message(WebSocketCreatedMessage(request_id="other", url="wss://example.com/telemetry"))
    th_client.push_message(WebSocketClosedMessage(request_id="other"))

    assert th_client._active_connections == 1
    assert th_client._tracked_connection_ids == {"tenhou-game"}
    assert th_client.message_queue.empty()
    th_client.bridge.reset.assert_called_once_with()


def test_tenhou_adopts_validated_frame_from_unclassified_endpoint(th_client):
    th_client.bridge.parse.return_value = [TsumoEvent(actor=0, pai="1m")]

    th_client.push_message(WebSocketFrameMessage(request_id="relay-game", direction="inbound", data="HELO"))

    assert th_client.message_queue.get(timeout=2.0).type == "tsumo"
    assert th_client.message_queue.get(timeout=2.0).code == "client_connected"
    assert th_client._tracked_connection_ids == {"relay-game"}

    th_client.push_message(WebSocketClosedMessage(request_id="relay-game"))
    assert th_client.message_queue.get(timeout=2.0).code == "game_disconnected"
