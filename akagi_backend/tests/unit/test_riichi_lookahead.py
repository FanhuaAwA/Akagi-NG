"""
测试模块：akagi_backend/tests/unit/test_riichi_lookahead.py

描述：针对立直前瞻 (Riichi Lookahead) 逻辑的单元测试。
主要测试点：
- 根据推理结果（立直动作是否在前三）触发模拟的逻辑。
- 模拟执行流程 (_run_riichi_lookahead) 的正确性，包括历史事件的回放。
- LookaheadBot 对 C++ 核心 Bot 实例的创建与注入。
- 模拟过程中的错误捕获与状态标志上报。
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

from akagi_ng.mjai_bot.bot import MortalBot
from akagi_ng.mjai_bot.lookahead import LookaheadBot
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.mjai_bot.utils import meta_to_recommend
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import ReachEvent, StartGameEvent, StartKyokuEvent, TsumoEvent

# 自动应用 mock_lib_loader_module fixture（定义在 unit/conftest.py 中）
pytestmark = pytest.mark.usefixtures("mock_lib_loader_module")


class TestRiichiLookahead(unittest.TestCase):
    def setUp(self):
        self.logger = MagicMock()
        self.model_loader = MagicMock()
        self.status = BotStatusContext()
        self.bot = MortalBot(status=self.status, is_3p=False)
        self.bot.logger = self.logger
        self.bot.model_loader = self.model_loader
        self.bot.player_id = 0

    @patch("akagi_ng.mjai_bot.bot.meta_to_recommend")
    def test_handle_riichi_lookahead_trigger(self, mock_meta_to_recommend):
        # Case: Reach is in Top 3 -> Should run simulation
        mock_meta_to_recommend.return_value = [("reach", 0.8), ("discard", 0.15), ("chi", 0.05)]

        self.bot._run_riichi_lookahead = MagicMock(return_value={"simulated_q": [1.0, 2.0]})

        meta = {"q_values": [0.1], "mask_bits": 1}
        self.bot._handle_riichi_lookahead(meta)

        self.bot._run_riichi_lookahead.assert_called_once()
        self.assertEqual(meta["riichi_lookahead"], {"simulated_q": [1.0, 2.0]})
        self.logger.info.assert_any_call(
            "Riichi Lookahead: Reach is in Top 3 (['reach', 'discard', 'chi']). Starting simulation."
        )

    @patch("akagi_ng.mjai_bot.bot.meta_to_recommend")
    def test_handle_riichi_lookahead_no_trigger(self, mock_meta_to_recommend):
        # Case: Reach is NOT in Top 3 -> Should NOT run simulation
        mock_meta_to_recommend.return_value = [("discard", 0.8), ("chi", 0.15), ("pon", 0.05)]

        self.bot._run_riichi_lookahead = MagicMock()

        meta = {"q_values": [0.1], "mask_bits": 1}
        self.bot._handle_riichi_lookahead(meta)

        self.bot._run_riichi_lookahead.assert_not_called()
        self.assertNotIn("riichi_lookahead", meta)

    @patch("akagi_ng.mjai_bot.bot.meta_to_recommend")
    def test_handle_riichi_lookahead_error(self, mock_meta_to_recommend):
        # Case: Simulation returns error -> Should add to notification_flags
        mock_meta_to_recommend.return_value = [("reach", 0.9)]

        self.bot._run_riichi_lookahead = MagicMock(return_value=None)

        meta = {"q_values": [0.1], "mask_bits": 1}
        self.bot._handle_riichi_lookahead(meta)

        self.assertIn(NotificationCode.RIICHI_SIM_FAILED, self.bot.status.flags)
        self.assertNotIn("riichi_lookahead", meta)

    def test_run_riichi_lookahead_simulation_success(self):
        """Test _run_riichi_lookahead executes full simulation flow successfully."""
        # 1. Setup Mock Bot and Engine returned by model_loader
        mock_sim_bot = MagicMock()
        mock_sim_engine = MagicMock()

        # Simulate simulation result
        expected_meta = {"q_values": [1.0], "mask_bits": 1}
        # sim_bot.react returns JSON string
        mock_sim_bot.react.side_effect = [
            None,  # React to game_start (is_3p=True)
            None,  # React to history event 1 (start_kyoku)
            None,  # React to history event 2 (discard)
            json.dumps({"meta": expected_meta}),  # React to REACH event
        ]

        self.bot.model_loader = MagicMock(return_value=(mock_sim_bot, mock_sim_engine))

        # Setup Bot state
        self.bot.player_id = 0
        mock_engine = MagicMock()
        mock_engine.status = BotStatusContext()
        self.bot.engine = mock_engine  # Required for LookaheadBot instantiation
        self.bot.is_3p = True
        self.bot.game_start_event = StartGameEvent(id=0, is_3p=True)
        unknown_tehai = ["?"] * 13
        self.bot.history = [
            StartKyokuEvent(
                bakaze="E",
                kyoku=1,
                honba=0,
                kyotaku=0,
                oya=0,
                scores=[25000] * 4,
                dora_marker="1p",
                tehais=[["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "E", "E", "E", "S"]]
                + [unknown_tehai] * 3,
            ),
            TsumoEvent(actor=0, pai="S"),
        ]

        # 2. Run with patched LookaheadBot
        with patch("akagi_ng.mjai_bot.bot.LookaheadBot") as MockLookaheadBot:
            mock_lookahead_instance = MockLookaheadBot.return_value
            mock_lookahead_instance.simulate_reach.return_value = expected_meta

            result = self.bot._run_riichi_lookahead()

            # Check LookaheadBot initialized with correct args
            # Note: Now uses sim_engine = self.engine.fork(status=sim_status)
            MockLookaheadBot.assert_called_once()
            args, kwargs = MockLookaheadBot.call_args
            self.assertEqual(args[1], self.bot.player_id)
            self.assertEqual(kwargs.get("is_3p"), self.bot.is_3p)

            # Check simulate_reach called with correct args including game_start_event
            mock_lookahead_instance.simulate_reach.assert_called_once()
            args, kwargs = mock_lookahead_instance.simulate_reach.call_args
            self.assertEqual(args[0], self.bot.history)  # history_events
            self.assertEqual(args[1], ReachEvent(actor=self.bot.player_id))  # candidate_event
            self.assertEqual(kwargs.get("game_start_event"), self.bot.game_start_event)  # game_start_event

        # Verify result
        self.assertEqual(result, expected_meta)

    def test_run_riichi_lookahead_simulation_failure(self):
        """Test _run_riichi_lookahead handles simulation errors gracefully."""
        # Setup Mock that returns garbage JSON
        mock_sim_bot = MagicMock()
        mock_sim_engine = MagicMock()
        mock_sim_bot.react.return_value = "invalid json"

        self.bot.model_loader = MagicMock(return_value=(mock_sim_bot, mock_sim_engine))

        # Run
        result = self.bot._run_riichi_lookahead()

        # Verify error handling
        self.assertIsNone(result)

    def test_lookahead_with_direct_engine(self):
        """测试 Lookahead 直接使用传入 engine 创建 sim_bot。"""
        mock_loader = sys.modules["akagi_ng.core.lib_loader"]
        mock_loader.libriichi.mjai.Bot = MagicMock(return_value=MagicMock())

        # 创建 mock engine
        mock_engine = MagicMock()
        mock_engine.status = BotStatusContext()
        mock_engine.last_inference_result = {
            "actions": [5],
            "q_out": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]],
            "masks": [[True, False, True, True, False, True]],
            "is_greedy": [True],
        }

        # 创建 LookaheadBot
        lookahead_bot = LookaheadBot(mock_engine, player_id=0, is_3p=False)

        # 配置 sim_bot (libs.mjai.Bot) 的行为
        # 注意：LookaheadBot 现在会创建一个新的 sim_bot
        mock_sim_bot = mock_loader.libriichi.mjai.Bot.return_value
        mock_sim_bot.react.side_effect = [
            None,  # game_start
            None,  # history
            json.dumps({"type": "dahai", "meta": {"q_values": [0.1], "mask_bits": 45}}),  # reach event
        ]

        # 执行模拟
        result = lookahead_bot.simulate_reach(
            history_events=[
                StartKyokuEvent(
                    bakaze="E",
                    kyoku=1,
                    honba=0,
                    kyotaku=0,
                    oya=0,
                    scores=[25000] * 4,
                    dora_marker="1p",
                    tehais=[["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "E", "E", "E", "S"]]
                    + [["?"] * 13] * 3,
                )
            ],
            candidate_event=ReachEvent(actor=0),
            game_start_event=StartGameEvent(id=0, is_3p=False),
        )

        # 验证 sim_bot 使用传入的真实 engine 构建
        mock_loader.libriichi.mjai.Bot.assert_called_once_with(mock_engine, 0)

        # 回放阶段应禁用推理（can_act=False），候选事件保持默认推理
        self.assertEqual(mock_sim_bot.react.call_count, 3)
        self.assertEqual(mock_sim_bot.react.call_args_list[0].kwargs, {"can_act": False})
        self.assertEqual(mock_sim_bot.react.call_args_list[1].kwargs, {"can_act": False})
        self.assertEqual(mock_sim_bot.react.call_args_list[2].kwargs, {})

        # 验证结果
        self.assertIsNotNone(result)
        self.assertEqual(result["mask_bits"], 45)

    def test_quick_eval_forced_discard_is_a_successful_single_candidate(self):
        """A valid meta-less quick_eval discard must not become a false failure."""
        mock_loader = sys.modules["akagi_ng.core.lib_loader"]
        mock_engine = MagicMock()
        mock_sim_bot = MagicMock()
        mock_loader.libriichi3p.mjai.Bot = MagicMock(return_value=mock_sim_bot)
        mock_sim_bot.react.side_effect = [
            None,
            None,
            json.dumps({"type": "dahai", "actor": 0, "pai": "4p", "tsumogiri": False}),
        ]
        lookahead_bot = LookaheadBot(mock_engine, player_id=0, is_3p=True)

        result = lookahead_bot.simulate_reach(
            history_events=[
                StartKyokuEvent(
                    bakaze="E",
                    kyoku=1,
                    honba=0,
                    kyotaku=0,
                    oya=0,
                    scores=[35000] * 4,
                    dora_marker="1p",
                    tehais=[["?"] * 13] * 4,
                )
            ],
            candidate_event=ReachEvent(actor=0),
            game_start_event=StartGameEvent(id=0, is_3p=True),
        )

        self.assertIsNotNone(result)
        self.assertEqual(meta_to_recommend(result, is_3p=True, temperature=0.3), [("4p", 1.0)])

    def test_meta_less_non_discard_response_remains_a_failure(self):
        mock_loader = sys.modules["akagi_ng.core.lib_loader"]
        mock_engine = MagicMock()
        mock_sim_bot = MagicMock()
        mock_loader.libriichi.mjai.Bot = MagicMock(return_value=mock_sim_bot)
        mock_sim_bot.react.side_effect = [
            None,
            json.dumps({"type": "none"}),
        ]
        lookahead_bot = LookaheadBot(mock_engine, player_id=0, is_3p=False)

        result = lookahead_bot.simulate_reach(
            history_events=[],
            candidate_event=ReachEvent(actor=0),
            game_start_event=StartGameEvent(id=0, is_3p=False),
        )

        self.assertIsNone(result)
