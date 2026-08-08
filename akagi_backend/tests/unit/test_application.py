"""
测试模块：akagi_backend/tests/unit/test_application.py

描述：针对主应用 (AkagiApp) 生命周期和核心流程的单元测试。
主要测试点：
- 应用初始化 (Init) 和启动/停止 (Start/Stop) 信号处理。
- 消息处理主循环 (Main Loop) 的调度逻辑。
- 处理过程中的错误捕获以及输出发射 (Emit) 路径（包括同步期间的屏蔽）。
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from akagi_ng.application import AkagiApp
from akagi_ng.bridge import MajsoulBridge
from akagi_ng.core.context import AppContext
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import MJAIResponse, ProcessResult, SystemEvent


@pytest.fixture
def app():
    return AkagiApp()


def test_app_initialization(app) -> None:
    """测试应用初始化流程。"""
    with (
        patch("akagi_ng.application.configure_logging"),
        patch("akagi_ng.application.DataServer") as mock_ds_class,
        patch("akagi_ng.application.MitmClient"),
        patch("akagi_ng.mjai_bot.Controller"),
        patch("akagi_ng.mjai_bot.StateTracker"),
        patch("akagi_ng.electron_client.create_electron_client"),
        patch("akagi_ng.application.set_app_context") as mock_set_ctx,
    ):
        app.initialize()

        assert app.ds is not None
        mock_ds_class.assert_called_once()
        mock_set_ctx.assert_called_once()


def test_app_start_stop(app) -> None:
    """测试应用的启动和停止信号。"""
    app.ds = MagicMock()

    mock_ctx = MagicMock()
    # 手动构建嵌套 Mock 结构避免 AttributeError
    mock_ctx.settings.mitm.enabled = True
    mock_ctx.mitm_client = MagicMock()
    mock_ctx.electron_client = MagicMock()

    with (
        patch("akagi_ng.application.get_app_context", return_value=mock_ctx),
        patch("akagi_ng.application.signal.signal"),
    ):
        app.start()

        assert app.ds.start.called
        assert mock_ctx.mitm_client.start.called
        assert mock_ctx.electron_client.start.called

        app.stop()
        assert app.get_stop_event().is_set()


def test_app_main_loop_flow(app) -> None:
    """测试主循环的消息处理流程。"""
    app.ds = MagicMock()
    app._stop_event = threading.Event()

    mock_ctx = MagicMock(spec=AppContext)
    mock_ctx.state_tracker = MagicMock()
    mock_ctx.controller = MagicMock()

    msg = {"type": "tsumo", "actor": 0}

    # 模拟获取一条消息后停止
    def side_effect(*args, **kwargs):
        app.stop()
        return msg

    with (
        patch("akagi_ng.application.get_app_context", return_value=mock_ctx),
        patch.object(app.message_queue, "get", side_effect=side_effect),
        patch.object(app, "_emit_outputs") as mock_emit,
        patch.object(app, "cleanup"),
    ):
        app.run()

        # 验证是否采集了输出
        mock_emit.assert_called_once()


def test_app_cleanup(app) -> None:
    """测试清理逻辑。"""
    app.ds = MagicMock()
    mock_ctx = MagicMock(spec=AppContext)
    mock_ctx.mitm_client = MagicMock()
    mock_ctx.electron_client = MagicMock()

    with patch("akagi_ng.application.get_app_context", return_value=mock_ctx):
        app.cleanup()

        assert mock_ctx.mitm_client.stop.called
        assert mock_ctx.electron_client.stop.called
        assert app.ds.stop.called


def test_plugin_only_mitm_bridge_is_used_for_autoplay(app) -> None:
    mock_ctx = MagicMock(spec=AppContext)
    mock_ctx.settings.mitm.enabled = False
    mock_ctx.plugin_manager = MagicMock()
    mock_ctx.plugin_manager.requires_mitm.return_value = True
    plugin_bridge = MagicMock()
    mock_ctx.mitm_client = MagicMock()
    mock_ctx.mitm_client.addon.activated_flows = ["plugin-flow"]
    mock_ctx.mitm_client.addon.bridges = {"plugin-flow": plugin_bridge}
    mock_ctx.mitm_client.addon.last_activity = {"plugin-flow": 1.0}
    mock_ctx.electron_client = MagicMock()
    mock_ctx.electron_client.bridge = MagicMock()

    with patch("akagi_ng.application.get_app_context", return_value=mock_ctx):
        assert app._get_active_bridge() is plugin_bridge


def test_autoplay_prefers_authenticated_game_bridge_over_newer_lobby_bridge(app) -> None:
    mock_ctx = MagicMock(spec=AppContext)
    mock_ctx.settings.mitm.enabled = True
    mock_ctx.plugin_manager = MagicMock()
    mock_ctx.plugin_manager.requires_mitm.return_value = False

    game_bridge = MajsoulBridge()
    game_bridge.accountId = 24992979
    game_bridge.mode_id = 17
    game_bridge.latest_self_operation_list = [{"type": 2, "combination": ["4s|4s"]}]
    game_bridge.latest_operation_step = 61
    lobby_bridge = MajsoulBridge()

    mock_ctx.mitm_client = MagicMock()
    mock_ctx.mitm_client.addon.activated_flows = ["game-flow", "new-lobby-flow"]
    mock_ctx.mitm_client.addon.bridges = {
        "game-flow": game_bridge,
        "new-lobby-flow": lobby_bridge,
    }
    # The lobby is newer and has more recent traffic, but it has no active game.
    mock_ctx.mitm_client.addon.last_activity = {"game-flow": 100.0, "new-lobby-flow": 200.0}
    mock_ctx.electron_client = MagicMock()

    with patch("akagi_ng.application.get_app_context", return_value=mock_ctx):
        assert app._get_active_bridge() is game_bridge
        assert app._get_latest_operation_list() == [{"type": 2, "combination": ["4s|4s"]}]
        assert app._get_latest_operation_step() == 61


def test_autoplay_prefers_most_recent_authenticated_bridge_during_reconnect(app) -> None:
    mock_ctx = MagicMock(spec=AppContext)
    mock_ctx.settings.mitm.enabled = True
    mock_ctx.plugin_manager = MagicMock()
    mock_ctx.plugin_manager.requires_mitm.return_value = False

    old_game_bridge = MajsoulBridge()
    old_game_bridge.accountId = 1
    old_game_bridge.mode_id = 17
    new_game_bridge = MajsoulBridge()
    new_game_bridge.accountId = 1
    new_game_bridge.mode_id = 17

    mock_ctx.mitm_client = MagicMock()
    mock_ctx.mitm_client.addon.activated_flows = ["old-game", "new-game"]
    mock_ctx.mitm_client.addon.bridges = {"old-game": old_game_bridge, "new-game": new_game_bridge}
    mock_ctx.mitm_client.addon.last_activity = {"old-game": 100.0, "new-game": 101.0}
    mock_ctx.electron_client = MagicMock()

    with patch("akagi_ng.application.get_app_context", return_value=mock_ctx):
        assert app._get_active_bridge() is new_game_bridge


def test_process_event_error_handling(app) -> None:
    """测试消息处理中的异常捕获。"""
    mock_state_tracker = MagicMock()
    mock_ctrl = MagicMock()

    # 模拟 Controller 抛出异常
    mock_ctrl.react.side_effect = ValueError("Test Error")

    msg = {"type": "dahai", "sync": False}
    result = app._process_event(msg, mock_state_tracker, mock_ctrl)

    # 不应导致崩溃，且返回为空
    assert result.response is None
    assert result.notifications == []
    assert result.is_sync is False


def test_system_disconnect_invalidates_flya_replay(app) -> None:
    app.autoplay = MagicMock()
    app.flya_decider = MagicMock()
    controller = MagicMock()
    tracker = MagicMock()
    event = SystemEvent(code=NotificationCode.GAME_DISCONNECTED)

    result = app._process_event(event, tracker, controller)

    app.autoplay.observe_system_event.assert_called_once_with(NotificationCode.GAME_DISCONNECTED)
    app.flya_decider.observe_system_event.assert_called_once_with(NotificationCode.GAME_DISCONNECTED)
    controller.react.assert_not_called()
    tracker.react.assert_not_called()
    assert result.notifications == [{"code": NotificationCode.GAME_DISCONNECTED}]


def test_emit_outputs_standard(app) -> None:
    """测试标准输出发射路径。"""
    app.ds = MagicMock()
    result = ProcessResult(
        response=MJAIResponse(action="dahai", meta={}),
        notifications=["TEST"],
        is_sync=False,
    )
    mock_state_tracker = MagicMock()

    with patch.object(mock_state_tracker, "build_recommendations", return_value={"rec": True}):
        app._emit_outputs(result, mock_state_tracker)

        # 应该发送通知和推荐
        assert app.ds.send_notifications.called
        assert app.ds.send_recommendations.called


def test_emit_outputs_sync_masking(app) -> None:
    """测试同步期间屏蔽推荐。"""
    app.ds = MagicMock()
    result = ProcessResult(
        response=MJAIResponse(action="sync", meta={}),
        notifications=["SYNCING"],
        is_sync=True,
    )
    mock_state_tracker = MagicMock()

    with patch.object(mock_state_tracker, "build_recommendations", return_value={"rec": True}):
        app._emit_outputs(result, mock_state_tracker)

        # 应该发送通知，但不发送推荐
        assert app.ds.send_notifications.called
        assert not app.ds.send_recommendations.called


# 为测试添加辅助方法
def get_stop_event(self):
    return self._stop_event


AkagiApp.get_stop_event = get_stop_event
