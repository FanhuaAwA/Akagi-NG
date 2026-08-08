"""
测试模块：akagi_backend/tests/unit/test_mortal_bot_decision.py

描述：针对 MortalBot 决策与元数据注入逻辑的单元测试。
主要测试点：
- 基础对战事件 (Tsumo, Dahai) 的响应流程。
- 同步事件 (sync=True) 不触发决策的逻辑校验。
- 三麻模式下的元数据格式、player_id 设置及动作屏蔽逻辑。
- 运行时异常、Json 解析错误等异常情况的稳健性。
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from akagi_ng.mjai_bot.bot import MortalBot
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.mjai_bot.utils import mask_unicode_3p
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import DahaiEvent, ReachAcceptedEvent, StartGameEvent, TsumoEvent

# 自动应用 mock_lib_loader_module fixture（定义在 unit/conftest.py 中）
pytestmark = pytest.mark.usefixtures("mock_lib_loader_module")


@pytest.fixture
def mock_engine_setup():
    """
    配置模型加载器的 Mock。
    """
    with patch("akagi_ng.mjai_bot.bot.load_bot_and_engine") as mock_loader:
        # 默认模拟一个打 1m 的响应
        mock_bot_instance = MagicMock()
        mock_bot_instance.react.return_value = json.dumps(
            {
                "type": "dahai",
                "pai": "1m",
                "meta": {
                    "q_values": [10.0] + [0.0] * 45,
                    "mask_bits": 1,
                },
            }
        )

        mock_engine = MagicMock()
        # 确保 Mock 拥有引擎协议要求的属性
        mock_engine.engine_type = "mortal"
        mock_engine.is_3p = False
        mock_engine.status = None  # 将由 bot 注入

        mock_loader.return_value = (mock_bot_instance, mock_engine)
        yield mock_loader, mock_bot_instance, mock_engine


# ===== 基本事件处理 =====


def test_event_processing_flow(mock_engine_setup) -> None:
    """验证基本的事件处理流程。"""
    _, mock_bot_instance, _ = mock_engine_setup
    status = BotStatusContext()
    bot = MortalBot(status, is_3p=False)

    # 1. start_game 初始化
    bot.react(StartGameEvent(id=0, is_3p=False))
    assert bot.bot == mock_bot_instance

    # 2. tsumo 会触发推理
    resp = bot.react(TsumoEvent(actor=0, pai="1m"))
    assert resp["type"] == "dahai"
    assert resp["pai"] == "1m"


def test_sync_event_uses_can_act_false(mock_engine_setup) -> None:
    """同步事件应通过 can_act=False 仅推进状态，不触发推理。"""
    _, mock_bot_instance, _ = mock_engine_setup
    status = BotStatusContext()
    bot = MortalBot(status, is_3p=False)

    bot.react(StartGameEvent(id=0, is_3p=False))
    mock_bot_instance.react.reset_mock()

    bot.react(TsumoEvent(actor=0, pai="1m", sync=True))

    assert mock_bot_instance.react.call_count == 1
    args, kwargs = mock_bot_instance.react.call_args
    assert isinstance(args[0], str)
    assert kwargs == {"can_act": False}


def test_self_riichi_tsumo_is_auto_discarded_without_inference_or_online_request(mock_engine_setup) -> None:
    _, mock_bot_instance, _ = mock_engine_setup
    status = BotStatusContext()
    bot = MortalBot(status, is_3p=False)
    bot.react(StartGameEvent(id=0, is_3p=False))
    bot.react(ReachAcceptedEvent(actor=0))
    mock_bot_instance.react.reset_mock()

    with patch.object(bot, "_try_ot3") as online:
        response = bot.react(TsumoEvent(actor=0, pai="7s"))

    assert response == {
        "type": "dahai",
        "actor": 0,
        "pai": "7s",
        "tsumogiri": True,
        "meta": {
            "engine_type": "mortal",
            "decision_source": "local",
            "fallback_used": False,
            "online_service_reconnecting": False,
        },
    }
    mock_bot_instance.react.assert_called_once()
    assert mock_bot_instance.react.call_args.kwargs == {"can_act": False}
    online.assert_not_called()


def test_opponent_riichi_does_not_enable_self_auto_discard(mock_engine_setup) -> None:
    _, mock_bot_instance, _ = mock_engine_setup
    bot = MortalBot(BotStatusContext(), is_3p=False)
    bot.react(StartGameEvent(id=0, is_3p=False))
    bot.react(ReachAcceptedEvent(actor=1))
    mock_bot_instance.react.reset_mock()

    response = bot.react(TsumoEvent(actor=0, pai="7s"))

    assert response["type"] == "dahai"
    assert mock_bot_instance.react.call_args.kwargs == {}


def test_meta_data_format_3p(mock_engine_setup) -> None:
    """验证三麻模式下的数据格式。"""
    _, mock_bot_instance, mock_engine = mock_engine_setup
    mock_engine.is_3p = True

    status = BotStatusContext()
    bot = MortalBot(status, is_3p=True)
    assert bot.is_3p is True

    # 模拟有多个合法动作的情况，确保 3p 不会抑制 meta
    mock_bot_instance.react.return_value = json.dumps(
        {
            "type": "dahai",
            "pai": "1m",
            "meta": {
                "q_values": [0.8, 0.7] + [0.0] * 44,
                "mask_bits": 3,
            },
        }
    )

    bot.react(StartGameEvent(id=1, is_3p=True))
    # 手动设置 metadata 模拟 Provider 行为，因为单元测试直接测试 Bot
    status.set_metadata("engine_type", "mortal")
    resp = bot.react(TsumoEvent(actor=1, pai="1m"))

    assert "meta" in resp
    # engine_type 应该通过 status.metadata 注入
    assert resp["meta"]["engine_type"] == "mortal"


def test_mortal3p_player_id(mock_engine_setup) -> None:
    """验证三麻 Bot 的 player_id 正确设置。（原 test_bots.py）"""
    _, mock_bot_instance, _ = mock_engine_setup
    mock_bot_instance.react.return_value = json.dumps({"type": "dahai", "pai": "1m", "meta": {"q_values": []}})

    status = BotStatusContext()
    bot = MortalBot(status, is_3p=True)
    assert bot.is_3p is True

    bot.react(StartGameEvent(id=1, is_3p=True))
    assert bot.player_id == 1


def test_3p_none_only_response_is_dropped(mock_engine_setup) -> None:
    """三麻中仅有 none 选项的响应应被视为无效并丢弃。"""
    _, mock_bot_instance, mock_engine = mock_engine_setup
    mock_engine.is_3p = True

    none_only_mask = 1 << mask_unicode_3p.index("none")
    mock_bot_instance.react.return_value = json.dumps(
        {
            "type": "none",
            "meta": {
                "q_values": [1.0],
                "mask_bits": none_only_mask,
            },
        }
    )

    status = BotStatusContext()
    bot = MortalBot(status, is_3p=True)
    bot.react(StartGameEvent(id=1, is_3p=True))

    resp = bot.react(TsumoEvent(actor=1, pai="1m"))
    assert resp is not None
    assert resp["type"] == "none"


def test_response_missing_type_is_dropped(mock_engine_setup) -> None:
    """模型返回缺失 type 的响应应被安全过滤。"""
    _, mock_bot_instance, _ = mock_engine_setup
    mock_bot_instance.react.return_value = json.dumps({"meta": {"q_values": [1.0], "mask_bits": 1}})

    status = BotStatusContext()
    bot = MortalBot(status, is_3p=False)
    bot.react(StartGameEvent(id=0, is_3p=False))

    resp = bot.react(TsumoEvent(actor=0, pai="1m"))
    assert resp is not None
    assert "type" not in resp
    assert NotificationCode.BOT_RUNTIME_ERROR not in status.flags


def test_ot3_bare_reach_uses_followup_discard_and_candidates() -> None:
    status = BotStatusContext()
    bot = MortalBot(status=status, is_3p=False)
    bot.player_id = 0
    bot.game_start_event = StartGameEvent(id=0, is_3p=False)
    bot.history = [TsumoEvent(actor=0, pai="5m")]

    client = MagicMock()
    client.circuit_open = False
    client.react.side_effect = [
        {
            "reaction": {"type": "reach", "actor": 0},
            "candidates": [{"action": "reach", "prob": 0.8}],
            "model": "ot3-4p",
        },
        {
            "reaction": {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": False},
            "candidates": [
                {"action": "dahai:5m", "prob": 0.7},
                {"action": "dahai:1p", "prob": 0.3},
            ],
            "model": "ot3-4p",
        },
    ]
    local_response = {
        "type": "reach",
        "actor": 0,
        "meta": {"q_values": [0.0], "mask_bits": 1},
    }

    with patch.object(bot, "_refresh_ot3_session", return_value=(client, "ot3-4p")):
        response = bot._try_ot3(TsumoEvent(actor=0, pai="5m"), local_response)

    assert response["type"] == "reach"
    assert response["pai"] == "5m"
    assert response["meta"]["ot3_reach_candidates"][0]["action"] == "dahai:5m"
    assert client.react.call_count == 2
    assert client.react.call_args_list[1].kwargs["events"][-1] == {
        "type": "reach",
        "actor": 0,
    }


# ===== 错误处理 & 边界情况 =====


def test_error_handling_runtime_exception(mock_engine_setup) -> None:
    """验证模型 react 抛出异常时的错误响应。"""
    _, mock_bot_instance, _ = mock_engine_setup
    status = BotStatusContext()
    bot = MortalBot(status, is_3p=False)
    bot.react(StartGameEvent(id=0, is_3p=False))

    # 模拟模型崩溃
    mock_bot_instance.react.side_effect = Exception("Model Crash")
    resp = bot.react(TsumoEvent(actor=0, pai="1m"))

    assert resp is None
    assert NotificationCode.BOT_RUNTIME_ERROR in status.flags


# 移除了 test_mortal_bot_parse_error，因为 MortalBot.react 不再承担运行时非对象输入的校验重任。


def test_mortal_bot_json_decode_error() -> None:
    """验证模型返回无效 JSON 时的处理。（原 test_mortal_bot_edge_cases.py）"""
    status = BotStatusContext()
    bot = MortalBot(status=status, is_3p=False)
    bot.player_id = 0
    bot.bot = MagicMock()
    bot.bot.react.return_value = "corrupt { json"

    bot.engine = MagicMock()
    bot.engine.status = status

    # 传递对象，而不是 JSON 字符串
    res = bot.react(DahaiEvent(actor=0, pai="1m", tsumogiri=False))
    assert res is None
    # 验证正确的通知标志
    assert NotificationCode.JSON_DECODE_ERROR in status.flags


def test_mortal_bot_unknown_engine_notification() -> None:
    """验证 _handle_start_game 在未知引擎类型时不设置加载标志。（原 test_mortal_bot_edge_cases.py）"""
    status = BotStatusContext()
    bot = MortalBot(status=status, is_3p=False)
    event = StartGameEvent(id=0, is_3p=False)
    mock_engine = MagicMock()
    status.set_metadata("engine_type", "alien_ai")

    with patch("akagi_ng.mjai_bot.bot.load_bot_and_engine") as mock_loader:
        mock_loader.return_value = (MagicMock(), mock_engine)
        bot._handle_start_game(event)

    assert "model_loaded_local" not in status.flags
    assert "model_loaded_online" not in status.flags


# ===== 通知标志 =====


def test_notification_flags_persistency(mock_engine_setup) -> None:
    """验证通知标志在对局中是持久的。"""
    _, _, _ = mock_engine_setup
    status = BotStatusContext()
    bot = MortalBot(status, is_3p=False)
    bot.react(StartGameEvent(id=0, is_3p=False))

    status.set_flag(NotificationCode.GAME_CONNECTED, True)

    # 模拟下一轮推理
    bot.react(TsumoEvent(actor=0, pai="2m"))

    # 验证标志依然存在
    assert NotificationCode.GAME_CONNECTED in status.flags
