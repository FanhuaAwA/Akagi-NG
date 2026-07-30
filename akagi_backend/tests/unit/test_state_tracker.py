"""
测试模块：akagi_backend/tests/unit/test_state_tracker.py

描述：针对游戏状态追踪器 (StateTracker) 的单元测试。
主要测试点：
- 对 C++ PlayerState 实例的初始化与事件更新逻辑。
- 拔北 (Nukidora) 事件向切牌 (Dahai) 逻辑的内部分发转换。
- 手牌 MJAI 格式化 (tehai_mjai_with_aka) 对赤宝牌的正确映射。
- 手牌消耗逻辑 (_extract_consumed) 中对赤宝牌的优先处理。
- 各种副露（吃、碰、杠）和和牌动作的详情提取逻辑。
- 推荐信息 (_process_standard_recommendations) 的转换与过滤。
- 立直前瞻 (Riichi Lookahead) 信息的附加逻辑。
- 状态更新异常时的错误捕获与标志设置。
"""

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.mjai_bot.tracker import StateTracker
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import DahaiEvent, NukidoraEvent, StartGameEvent


@pytest.fixture
def status_ctx():
    return BotStatusContext()


@pytest.fixture
def bot(status_ctx):
    return StateTracker(status=status_ctx)


@pytest.fixture
def tracker(status_ctx):
    """专门用于测试推荐适配逻辑的 tracker 变体"""
    tracker = StateTracker(status=status_ctx)
    tracker.is_3p = False

    ps = MagicMock()
    ps.last_cans.can_chi_low = False
    ps.last_cans.can_chi_mid = False
    ps.last_cans.can_chi_high = False
    ps.last_cans.can_pon = False
    ps.last_cans.can_daiminkan = False
    ps.ankan_candidates.return_value = []
    ps.kakan_candidates.return_value = []
    ps.tehai = [0] * 34
    ps.akas_in_hand = [False] * 3

    tracker.player_state = ps
    return tracker


def test_initialization(bot):
    assert bot.is_3p is False
    assert bot.meta == {}
    assert bot.player_id == 0
    assert bot.player_state is None
    assert bot.last_self_tsumo is None
    assert bot.tehai_mjai_with_aka == []


def test_react_start_game(bot):
    with patch("akagi_ng.core.lib_loader.libriichi.state.PlayerState") as MockPlayerState:
        event = StartGameEvent(id=1, is_3p=False)
        bot.react(event)

        assert bot.player_id == 1
        assert bot.is_3p is False
        assert bot.player_state is not None
        MockPlayerState.assert_called_with(1)
        # 验证 update 也被调用
        MockPlayerState.return_value.update.assert_called_once()


def test_react_nukidora_conversion(bot):
    bot.player_id = 0
    bot.player_state = MagicMock()
    bot.player_state.last_self_tsumo.return_value = "N"

    event = NukidoraEvent(actor=0)
    # nukidora 内部会判断 == "N"
    bot.react(event)

    # 验证转换为了 dahai 调用 update
    called_args = bot.player_state.update.call_args[0][0]
    payload = json.loads(called_args)
    assert payload["type"] == "dahai"
    assert payload["pai"] == "N"


def test_error_handling(bot):
    bot.player_state = MagicMock()
    bot.player_state.update.side_effect = RuntimeError("test error")

    res = bot.react(DahaiEvent(actor=1, pai="1m", tsumogiri=False))
    assert res is None
    assert NotificationCode.STATE_TRACKER_ERROR in bot.status.flags


def test_properties_pass_through(bot):
    bot.player_state = MagicMock()
    bot.player_state.last_self_tsumo.return_value = "1m"
    bot.player_state.last_kawa_tile.return_value = "2m"
    bot.player_state.self_riichi_accepted = True
    bot.player_state.last_cans.can_tsumo_agari = True

    assert bot.last_self_tsumo == "1m"
    assert bot.last_kawa_tile == "2m"
    assert bot.self_riichi_accepted is True
    assert bot.can_tsumo_agari is True


def test_tehai_mjai_with_aka(bot):
    bot.player_state = MagicMock()

    # 模拟手牌 (1m x1, 5m x2, 5p x1, 中 x1)
    # index: 1m=0, 5m=4, 5p=13, 中(C)=33
    tehai = [0] * 34
    tehai[0] = 1
    tehai[4] = 2
    tehai[13] = 1
    tehai[33] = 1

    bot.player_state.tehai = tehai

    # 持有 5mr, 不持有 5pr
    bot.player_state.akas_in_hand = [True, False, False]

    result = bot.tehai_mjai_with_aka

    assert len(result) == 5
    assert "1m" in result
    assert "5mr" in result
    assert "5m" in result
    assert "5p" in result
    assert "C" in result

    # 确保排序正确（依照字面量先后顺序）
    assert result == ["1m", "5mr", "5m", "5p", "C"]


def test_extract_consumed_no_aka(tracker):
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["1m", "2m", "3m", "4m", "5m"]
        res = tracker._extract_consumed(["4m", "5m"])
        assert res == ["4m", "5m"]


def test_extract_consumed_with_aka(tracker):
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["1m", "4m", "5m", "5mr"]
        res = tracker._extract_consumed(["4m", "5m"])
        assert res == ["4m", "5mr"]


def test_extract_consumed_aka_target_multiple(tracker):
    # 测试暗杠时优先消耗 aka
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["5m", "5m", "5m", "5mr"]
        res = tracker._extract_consumed(["5m", "5m", "5m", "5m"])
        # 结果里必定包含 5mr
        assert res.count("5mr") == 1
        assert res.count("5m") == 3


def test_extract_consumed_fallback(tracker):
    # 如果手牌里没有，退回 base
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["1m"]
        res = tracker._extract_consumed(["2m", "3m"])
        assert res == ["2m", "3m"]


def test_handle_chi_fuuro_success(tracker):
    tracker.player_state.last_cans.can_chi_low = True
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["4m", "5m"]
        res = tracker._handle_chi_fuuro("3m", "chi_low")
        assert len(res) == 1
        assert res[0] == {"tile": "3m", "consumed": ["4m", "5m"]}


def test_handle_chi_fuuro_types(tracker):
    tracker.player_state.last_cans.can_chi_low = True
    tracker.player_state.last_cans.can_chi_mid = True
    tracker.player_state.last_cans.can_chi_high = True

    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["1m", "2m", "4m", "5m"]

        res_low = tracker._handle_chi_fuuro("3m", "chi_low")
        assert res_low[0]["consumed"] == ["4m", "5m"]

        res_mid = tracker._handle_chi_fuuro("3m", "chi_mid")
        assert res_mid[0]["consumed"] == ["2m", "4m"]

        res_high = tracker._handle_chi_fuuro("3m", "chi_high")
        assert res_high[0]["consumed"] == ["1m", "2m"]


def test_handle_chi_fuuro_with_aka_priority(tracker):
    tracker.player_state.last_cans.can_chi_low = True
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["4m", "5m", "5mr"]
        res = tracker._handle_chi_fuuro("3m", "chi_low")
        assert res[0]["consumed"] == ["4m", "5mr"]


def test_handle_chi_fuuro_edge_cases(tracker):
    # Can chi but parsing fails -> empty consumed
    tracker.player_state.last_cans.can_chi_low = True
    res = tracker._handle_chi_fuuro("E", "chi_low")
    assert res == [{"tile": "E", "consumed": []}]

    # Not allowed by libriichi
    tracker.player_state.last_cans.can_chi_low = False
    res = tracker._handle_chi_fuuro("3m", "chi_low")
    assert res == [{"tile": "3m", "consumed": []}]


def test_handle_pon_fuuro_success(tracker):
    tracker.player_state.last_cans.can_pon = True
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["1m", "1m", "2p"]
        res = tracker._handle_pon_fuuro("1m")
        assert res == [{"tile": "1m", "consumed": ["1m", "1m"]}]


def test_handle_pon_fuuro_fallback(tracker):
    tracker.player_state.last_cans.can_pon = False
    res = tracker._handle_pon_fuuro("1m")
    assert res == [{"tile": "1m", "consumed": []}]


def test_handle_pon_fuuro_with_aka_priority(tracker):
    tracker.player_state.last_cans.can_pon = True
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["5m", "5mr"]
        res = tracker._handle_pon_fuuro("5m")
        assert "5mr" in res[0]["consumed"]


def test_handle_kan_fuuro_daiminkan(tracker):
    tracker.player_state.last_cans.can_daiminkan = True
    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["1m", "1m", "1m"]
        res = tracker._handle_kan_fuuro("1m")
        assert res == [{"tile": "1m", "consumed": ["1m", "1m", "1m"]}]


def test_handle_kan_fuuro_ankan_kakan(tracker):
    tracker.player_state.last_cans.can_daiminkan = False
    tracker.player_state.ankan_candidates.return_value = ["2m"]
    tracker.player_state.kakan_candidates.return_value = ["3m"]

    with patch.object(tracker.__class__, "tehai_mjai_with_aka", new_callable=PropertyMock) as mock_tehai:
        mock_tehai.return_value = ["2m", "2m", "2m", "2m", "3m"]
        res = tracker._handle_kan_fuuro("10z")  # dummy kawa
        assert len(res) == 2
        assert res[0] == {"tile": "2m", "consumed": ["2m", "2m", "2m", "2m"]}
        assert res[1] == {"tile": "3m", "consumed": ["3m"]}


def test_handle_kan_fuuro_empty_consumed(tracker):
    tracker.player_state.last_cans.can_daiminkan = False
    res = tracker._handle_kan_fuuro(None)
    assert res == []


def test_get_fuuro_details_dispatch(tracker):
    tracker.player_state.last_kawa_tile.return_value = "3m"
    with patch.object(tracker, "_handle_chi_fuuro") as m:
        tracker._get_fuuro_details("chi_low")
        m.assert_called_with("3m", chi_type="chi_low")

    with patch.object(tracker, "_handle_pon_fuuro") as m:
        tracker._get_fuuro_details("pon")
        m.assert_called_with("3m")

    with patch.object(tracker, "_handle_kan_fuuro") as m:
        tracker._get_fuuro_details("kan_select")
        m.assert_called_with("3m")

    assert tracker._get_fuuro_details("unknown") == []


def test_handle_hora_action(tracker):
    # Tsumo
    with patch.object(tracker.__class__, "can_tsumo_agari", new_callable=PropertyMock) as mock_can:
        mock_can.return_value = True
        tracker.player_state.last_self_tsumo.return_value = "5z"
        item = {}
        tracker._handle_hora_action(item)
        assert item == {"action": "tsumo", "tile": "5z"}

    # Ron
    with patch.object(tracker.__class__, "can_tsumo_agari", new_callable=PropertyMock) as mock_can:
        mock_can.return_value = False
        tracker.player_state.last_kawa_tile.return_value = "9p"
        item = {}
        tracker._handle_hora_action(item)
        assert item == {"action": "ron", "tile": "9p"}


def test_build_recommendations_no_meta(tracker):
    assert tracker.build_recommendations({}) is None


def test_build_recommendations_with_valid_meta(tracker):
    mjai_response = {
        "type": "dahai",
        "meta": {
            "q_values": [1.0, 2.0],
            "mask_bits": 3,
            "engine_type": "test",
        },
    }

    with patch("akagi_ng.mjai_bot.tracker.meta_to_recommend") as mock_m2r:
        mock_m2r.return_value = [("1m", 0.9)]
        tracker.player_state.self_riichi_accepted = False
        # 模拟 last_kawa_tile 等属性
        with patch.object(tracker.__class__, "last_kawa_tile", new_callable=PropertyMock) as mock_kawa:
            mock_kawa.return_value = "1m"
            result = tracker.build_recommendations(mjai_response)
            assert result is not None
            assert len(result["recommendations"]) > 0
            assert result["recommendations"][0]["action"] == "1m"
            assert result["engine_type"] == "test"


def test_ot3_recommendations_use_exact_reaction_and_skip_duplicate_top_candidate(tracker):
    tracker.player_state.self_riichi_accepted = False
    response = {
        "meta": {
            "engine_type": "akagiot",
            "ot3_reaction": {"type": "dahai", "actor": 0, "pai": "5mr", "tsumogiri": False},
            "ot3_candidates": [
                {"action": "dahai:5m", "prob": 0.8},
                {"action": "dahai:1p", "prob": 0.15},
                {"action": "reach", "prob": 0.05},
            ],
        }
    }

    result = tracker.build_recommendations(response)

    assert result is not None
    assert result["recommendations"] == [
        {"action": "5mr", "confidence": 0.8},
        {"action": "1p", "confidence": 0.15},
        {"action": "reach", "confidence": 0.05},
    ]


def test_ot3_recommendation_preserves_exact_meld_tiles(tracker):
    tracker.player_state.self_riichi_accepted = False
    response = {
        "meta": {
            "engine_type": "akagiot",
            "ot3_reaction": {
                "type": "chi",
                "actor": 0,
                "target": 3,
                "pai": "3m",
                "consumed": ["1m", "2m"],
            },
            "ot3_candidates": [{"action": "chi_low", "prob": 0.9}],
        }
    }

    result = tracker.build_recommendations(response)

    assert result is not None
    assert result["recommendations"] == [
        {
            "action": "chi",
            "confidence": 0.9,
            "tile": "3m",
            "consumed": ["1m", "2m"],
        }
    ]


def test_ot3_reach_displays_followup_discard_candidates(tracker):
    tracker.player_state.self_riichi_accepted = False
    response = {
        "meta": {
            "engine_type": "akagiot",
            "ot3_reaction": {"type": "reach", "actor": 0, "pai": "5m"},
            "ot3_candidates": [{"action": "reach", "prob": 0.8}],
            "ot3_reach_candidates": [
                {"action": "dahai:5m", "prob": 0.7},
                {"action": "dahai:1p", "prob": 0.3},
            ],
        }
    }

    result = tracker.build_recommendations(response)

    assert result is not None
    assert result["recommendations"][0] == {
        "action": "reach",
        "confidence": 0.8,
        "sim_candidates": [
            {"tile": "5m", "confidence": 0.7},
            {"tile": "1p", "confidence": 0.3},
        ],
    }


def test_build_recommendations_riichi_filter(tracker):
    mjai_response = {
        "meta": {
            "q_values": [1.0],
            "mask_bits": 1,
        }
    }
    with patch("akagi_ng.mjai_bot.tracker.meta_to_recommend") as mock_m2r:
        mock_m2r.return_value = [("1m", 0.9), ("kan_select", 0.8), ("tsumo", 0.7)]

        # 已立直状态
        with patch.object(tracker.__class__, "self_riichi_accepted", new_callable=PropertyMock) as mock_riichi:
            mock_riichi.return_value = True

            # 需要模拟 _get_fuuro_details 对于 kan_select 的返回
            with patch.object(tracker, "_get_fuuro_details", return_value=[{"tile": "2m", "consumed": ["2m"]}]):
                result = tracker.build_recommendations(mjai_response)
                actions = [r["action"] for r in result["recommendations"]]
                # 1m 被过滤掉，保留 kan (来自 kan_select), tsumo
                assert "1m" not in actions
                assert "kan" in actions
                assert "tsumo" in actions


def test_attach_riichi_lookahead_all_branches(tracker):
    meta = {"riichi_lookahead": {"dummy": "meta"}}
    tracker.meta = meta

    # Multi-path test
    recs = [{"action": "reach"}]
    with patch("akagi_ng.mjai_bot.tracker.meta_to_recommend") as mock_m2r:
        # 1. Valid rec
        mock_m2r.return_value = [("1m", 0.9), ("2m", 0.8)]
        tracker._attach_riichi_lookahead(recs)
        assert len(recs[0]["sim_candidates"]) == 2

        # 2. Limit test
        mock_m2r.return_value = [(str(i), 0.5) for i in range(20)]
        tracker._attach_riichi_lookahead(recs)
        assert len(recs[0]["sim_candidates"]) == 5


def test_build_recommendations_comprehensive(tracker):
    # Exception path - trigger error in _process_standard_recommendations
    mjai_res = {
        "meta": {
            "q_values": [1.0],
            "mask_bits": 1,
            "engine_type": "test",
            "fallback_used": False,
            "circuit_open": False,
        }
    }
    with patch("akagi_ng.mjai_bot.tracker.meta_to_recommend", side_effect=RuntimeError("crash")):
        assert tracker.build_recommendations(mjai_res) is None
