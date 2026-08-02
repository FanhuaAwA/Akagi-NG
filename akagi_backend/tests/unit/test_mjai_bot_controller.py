"""
测试模块：akagi_backend/tests/unit/test_mjai_bot_controller.py

描述：针对 MJAI Bot 协调器 (Controller) 的单元测试。
主要测试点：
- Bot 实例的生命周期管理，包括 4P/3P 模式切换。
- 对战事件的转发与重放 (Replay) 机制。
- Bot 运行异常、切换失败等场景的状态标志捕获。
- 事件序列不完整或 Bot 未加载时的安全降级处理。
"""

from unittest.mock import MagicMock, patch

import pytest

from akagi_ng.mjai_bot.controller import Controller
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import DahaiEvent, StartGameEvent, StartKyokuEvent, TsumoEvent
from akagi_ng.settings import local_settings


@pytest.fixture
def controller():
    return Controller(BotStatusContext())


# ===== 基础行为测试 =====


def test_controller_unmatched_event_sequence(controller):
    """start_game 后直接 dahai（未经 start_kyoku），应安全返回 none"""
    controller.bot = MagicMock()
    controller.bot.react.return_value = None

    controller.react(StartGameEvent(id=0, is_3p=False))
    res = controller.react(DahaiEvent(actor=0, pai="1m", tsumogiri=False))
    assert res is None


def test_controller_runtime_error(controller):
    """Bot.react 抛出异常时应设置 RUNTIME_ERROR 标志"""
    mock_bot = MagicMock()
    mock_bot.react.side_effect = Exception("test error")
    controller.bot = mock_bot

    res = controller.react(TsumoEvent(actor=0, pai="1m"))
    assert res is None
    assert NotificationCode.BOT_RUNTIME_ERROR in controller.status.flags


def test_controller_no_bot_loaded(controller):
    """未加载 Bot 时所有事件应返回 none"""
    controller.bot = None
    res = controller.react(DahaiEvent(actor=0, pai="1m", tsumogiri=False))
    assert res is None


# ===== Bot 激活 & is_3p 检测 =====


def test_controller_early_is_3p_detection(controller):
    """start_game 中的 is_3p 应立即触发 Bot 激活"""
    with patch.object(controller, "_ensure_bot_activated") as mock_activate:
        controller.react(StartGameEvent(id=0, is_3p=True))
        mock_activate.assert_called_once_with(True)


def test_controller_mandatory_is_3p(controller):
    """start_game 的 is_3p=False 也应触发激活"""
    with patch.object(controller, "_ensure_bot_activated") as mock_activate:
        controller.react(StartGameEvent(id=0, is_3p=False))
        mock_activate.assert_called_once_with(False)


def test_controller_bot_switch_failed(controller):
    """_choose_bot 失败时应设置 BOT_SWITCH_FAILED 标志"""
    with patch.object(controller, "_choose_bot", return_value=False):
        controller.react(StartGameEvent(id=0, is_3p=False))

        assert controller.bot is None
        assert NotificationCode.BOT_SWITCH_FAILED in controller.status.flags


# ===== 生命周期 & Bot 切换 =====


def test_controller_lifecycle_replay():
    """start_game 应缓存并重放给新加载的 Bot"""
    controller = Controller(BotStatusContext())

    mock_instance = MagicMock()
    mock_instance.react.return_value = None
    mock_instance.notification_flags = {}

    with patch("akagi_ng.mjai_bot.bot.MortalBot", return_value=mock_instance) as MockBotClass:
        # start_game(is_3p=True) 应触发加载 mortal3p 并重放
        start_game = StartGameEvent(id=1, is_3p=True)
        controller.react(start_game)

        assert controller.bot is mock_instance
        # _choose_bot 调用 cls(status=...) 创建实例
        MockBotClass.assert_called_once()
        # 重放时 bot.react 被调用一次（传入 start_game）
        mock_instance.react.assert_called_once_with(start_game)


def test_controller_enables_probe_only_for_configured_flya(monkeypatch):
    controller = Controller(BotStatusContext(), flya_probe=True)
    monkeypatch.setattr("akagi_ng.mjai_bot.controller.local_settings.ot.online", True)
    monkeypatch.setattr("akagi_ng.mjai_bot.controller.local_settings.ot.provider", "flya_test_api")
    monkeypatch.setattr("akagi_ng.mjai_bot.controller.local_settings.ot.flya_server", "https://flya.example")
    monkeypatch.setattr("akagi_ng.mjai_bot.controller.local_settings.ot.flya_api_key", "secret")
    mock_instance = MagicMock()
    mock_instance.react.return_value = None

    with patch("akagi_ng.mjai_bot.bot.MortalBot", return_value=mock_instance) as mock_bot:
        controller.react(StartGameEvent(id=0, is_3p=False))

    assert mock_bot.call_args.kwargs["flya_probe"] is True


@pytest.mark.parametrize(
    ("online", "provider"),
    [(False, "flya_test_api"), (True, "akagi_ot3")],
)
def test_controller_rebuilds_bot_when_leaving_flya(monkeypatch, online, provider):
    controller = Controller(BotStatusContext(), flya_probe=True)
    ot = local_settings.ot
    monkeypatch.setattr(ot, "online", True)
    monkeypatch.setattr(ot, "provider", "flya_test_api")
    monkeypatch.setattr(ot, "flya_server", "https://flya.example")
    monkeypatch.setattr(ot, "flya_api_key", "secret")

    def make_bot(**kwargs):
        bot = MagicMock()
        bot.is_3p = kwargs["is_3p"]
        bot.flya_probe = kwargs["flya_probe"]
        bot.react.return_value = None
        return bot

    with patch("akagi_ng.mjai_bot.bot.MortalBot", side_effect=make_bot) as mock_bot:
        controller.react(StartGameEvent(id=0, is_3p=False))
        monkeypatch.setattr(ot, "online", online)
        monkeypatch.setattr(ot, "provider", provider)
        controller.react(StartGameEvent(id=0, is_3p=False))

    assert [call.kwargs["flya_probe"] for call in mock_bot.call_args_list] == [True, False]


def test_controller_lifecycle_start_kyoku_after_replay():
    """start_game 重放后，start_kyoku 应正常转发给 Bot"""
    controller = Controller(BotStatusContext())

    mock_instance = MagicMock()
    mock_instance.react.return_value = None

    with patch("akagi_ng.mjai_bot.bot.MortalBot", return_value=mock_instance):
        start_game = StartGameEvent(id=1, is_3p=True)
        controller.react(start_game)

        start_kyoku = StartKyokuEvent(
            bakaze="E",
            kyoku=1,
            honba=0,
            oya=0,
            scores=[35000, 35000, 35000, 0],
            dora_marker="1p",
            kyotaku=0,
            tehais=[["?"] * 13] * 4,
        )
        res = controller.react(start_kyoku)
        assert res is None
        # react 应被调用 2 次：start_game 重放 + start_kyoku
        assert mock_instance.react.call_count == 2
        mock_instance.react.assert_any_call(start_game)
        mock_instance.react.assert_any_call(start_kyoku)


def test_controller_bot_switch_4p_to_3p():
    """验证从四麻切换到三麻时，新 Bot 能正确被激活"""
    controller = Controller(BotStatusContext())

    mock_bot = MagicMock()
    mock_bot.react.return_value = None

    with patch("akagi_ng.mjai_bot.bot.MortalBot", return_value=mock_bot) as MockBotClass:
        # 1. 激活四麻
        controller.react(StartGameEvent(id=0, is_3p=False))
        assert controller.bot is mock_bot
        MockBotClass.assert_called_once()
        MockBotClass.reset_mock()
        mock_bot.reset_mock()

        # 为了测试切换，改写 mock_bot 的 is_3p 让 controller 认为它还是 4P bot
        mock_bot.is_3p = False

        # 2. 切换到三麻
        new_start_game = StartGameEvent(id=0, is_3p=True)
        controller.react(new_start_game)
        assert controller.bot is mock_bot
        MockBotClass.assert_called_once()
        # 三麻 bot 应收到 start_game 重放
        mock_bot.react.assert_called_once_with(new_start_game)


def test_controller_bot_switch_reconnect_scenario():
    """模拟重连场景：先 start_game 再切换模式"""
    controller = Controller(BotStatusContext())

    mock_3p = MagicMock()
    mock_3p.react.return_value = None

    with patch("akagi_ng.mjai_bot.bot.MortalBot", return_value=mock_3p):
        # 重连场景必须有 start_game（Bridge 合成）→ start_kyoku
        start_game = StartGameEvent(id=0, is_3p=True)
        controller.react(start_game)
        assert controller.bot is mock_3p

        start_kyoku = StartKyokuEvent(
            bakaze="E",
            kyoku=1,
            honba=0,
            kyotaku=0,
            oya=0,
            dora_marker="1p",
            scores=[35000, 35000, 35000, 0],
            tehais=[["?"] * 13] * 4,
        )
        res = controller.react(start_kyoku)
        assert res is None
        assert mock_3p.react.call_count == 2
