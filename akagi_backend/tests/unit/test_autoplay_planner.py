from types import SimpleNamespace
from unittest.mock import patch

import pytest

from akagi_ng.autoplay.planner import LOCATION, ActionPlanner, delay_controller
from akagi_ng.schema.constants import MahjongConstants
from akagi_ng.settings import local_settings


def _mock_cans(**overrides):
    defaults = {
        "can_discard": True,
        "can_riichi": False,
        "can_chi": False,
        "can_chi_low": False,
        "can_chi_mid": False,
        "can_chi_high": False,
        "can_pon": False,
        "can_kan": False,
        "can_ankan": False,
        "can_kakan": False,
        "can_daiminkan": False,
        "can_tsumo_agari": False,
        "can_ron_agari": False,
        "can_ryukyoku": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_player_state(*, cans=None, kakan_candidates=None):
    return SimpleNamespace(
        last_cans=cans or _mock_cans(),
        kakan_candidates=lambda: list(kakan_candidates or []),
    )


def test_plan_tsumogiri_discard_targets_drawn_tile():
    planner = ActionPlanner()

    plan = planner.plan(
        {"type": "dahai", "pai": "5m", "tsumogiri": True},
        ["1m", "2m", "3m", "4m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p", "5p"],
        "5m",
    )

    assert len(plan) == 1
    assert plan[0].label == "discard"
    assert plan[0].expected_types == (1,)


def test_plan_reach_click_appends_follow_up_discard():
    planner = ActionPlanner()
    planner.update_operation_list([{"type": 7, "combination": []}])

    plan = planner.plan(
        {"type": "reach", "pai": "1m"},
        ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"],
        None,
    )

    assert [click.label for click in plan] == ["reach", "reach-discard"]
    assert plan[0].verify_operation_clear is False
    assert plan[1].verify_operation_clear is True
    assert plan[1].requires_operation_step is True
    assert planner.reached is False
    assert planner.pending_reach_discard is False


def test_advanced_reach_discard_uses_its_independent_range():
    planner = ActionPlanner()
    planner.update_operation_list([{"type": 7, "combination": []}])

    with (
        patch.object(local_settings.autoplay, "delay_mode", "advanced"),
        patch.object(local_settings.autoplay.advanced_timing.reach, "min", 2.0),
        patch.object(local_settings.autoplay.advanced_timing.reach, "max", 2.0),
        patch.object(local_settings.autoplay.advanced_timing.reach_discard, "min", 0.9),
        patch.object(local_settings.autoplay.advanced_timing.reach_discard, "max", 0.9),
    ):
        plan = planner.plan(
            {"type": "reach", "pai": "1m"},
            ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"],
            None,
        )

    assert plan[0].delay == pytest.approx(2.0)
    assert plan[1].delay == pytest.approx(0.9)


def test_advanced_hora_distinguishes_ron_and_tsumo_ranges():
    planner = ActionPlanner()

    with (
        patch.object(local_settings.autoplay, "delay_mode", "advanced"),
        patch.object(local_settings.autoplay.advanced_timing.ron, "min", 2.1),
        patch.object(local_settings.autoplay.advanced_timing.ron, "max", 2.1),
        patch.object(local_settings.autoplay.advanced_timing.tsumo, "min", 3.2),
        patch.object(local_settings.autoplay.advanced_timing.tsumo, "max", 3.2),
    ):
        ron_delay = planner.action_delay({"type": "hora", "actor": 0, "target": 1}, 0.0, dealer_opening=False)
        tsumo_delay = planner.action_delay({"type": "hora", "actor": 0, "target": 0}, 0.0, dealer_opening=False)
        missing_context_delay = planner.action_delay({"type": "hora"}, 0.0, dealer_opening=False)

    assert ron_delay == pytest.approx(2.1)
    assert tsumo_delay == pytest.approx(3.2)
    assert missing_context_delay == pytest.approx(2.1)


def test_plan_nukidora_button_supported():
    planner = ActionPlanner()
    planner.update_operation_list([{"type": 11, "combination": []}])

    plan = planner.plan(
        {"type": "nukidora"},
        ["N", "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p"],
        None,
    )

    assert len(plan) == 1
    assert plan[0].label == "nukidora"
    assert plan[0].expected_types == (11,)


def test_plan_chi_candidate_uses_fallback_operation_reconstruction():
    planner = ActionPlanner()
    player_state = _mock_player_state(
        cans=_mock_cans(can_chi=True, can_chi_low=True, can_chi_mid=True, can_chi_high=True)
    )

    plan = planner.plan(
        {"type": "chi", "consumed": ["1m", "2m"]},
        ["1m", "2m", "2m", "4m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p"],
        None,
        player_state=player_state,
        last_kawa_tile="3m",
    )

    assert len(plan) == 2
    assert plan[0].label == "chi"
    assert plan[1].label == "chi-candidate"


def test_plan_opening_discard_after_nukidora_targets_tsumo_slot_without_overflow():
    planner = ActionPlanner()

    tehai = ["2p", "3p", "4p", "5p", "8p", "9p", "2s", "2s", "3s", "3s", "3s", "P", "P", "P"]
    plan = planner.plan(
        {"type": "dahai", "pai": "C", "tsumogiri": False},
        tehai,
        "C",
    )

    assert len(plan) == 1
    assert plan[0].label == "discard"
    assert plan[0].coord == planner.get_pai_coord(MahjongConstants.TEHAI_SIZE, tehai)


def test_get_pai_coord_clamps_overflow_indexes_to_tsumo_slot():
    planner = ActionPlanner()
    tehai = ["1m"] * 14

    assert planner.get_pai_coord(14, tehai) == planner.get_pai_coord(MahjongConstants.TEHAI_SIZE, tehai)


def test_get_pai_coord_uses_shortened_tsumo_slot_after_open_meld():
    planner = ActionPlanner()
    tehai = ["2p", "3p", "4p", "5pr", "6p", "6p", "4s", "4s", "6s", "8s", "E"]

    assert planner.get_pai_coord(MahjongConstants.TEHAI_SIZE, tehai) == pytest.approx((11.175, 8.3625))


def test_plan_discard_finds_last_hand_tile_when_tracker_tehai_already_has_14_tiles():
    planner = ActionPlanner()
    planner.is_new_round = False

    tehai = ["1p", "2p", "3p", "7p", "7p", "8p", "9p", "3s", "7s", "9s", "9s", "S", "S", "P"]
    plan = planner.plan(
        {"type": "dahai", "pai": "P", "tsumogiri": False},
        tehai,
        "1p",
    )

    assert len(plan) == 1
    assert plan[0].label == "discard"
    assert plan[0].coord == planner.get_pai_coord(12, tehai)


def test_plan_none_requires_a_real_claim_window():
    planner = ActionPlanner()

    plan = planner.plan(
        {"type": "none"},
        ["1m"] * MahjongConstants.TEHAI_SIZE,
        None,
    )

    assert plan == []


def test_plan_none_uses_skip_only_during_a_live_claim_window():
    planner = ActionPlanner()
    planner.update_operation_list([{"type": 3, "combination": ["5m|5m"]}])

    plan = planner.plan(
        {"type": "none"},
        ["1m"] * MahjongConstants.TEHAI_SIZE,
        None,
        require_live_operations=True,
    )

    assert len(plan) == 1
    assert plan[0].label == "skip"


def test_majsoul_button_plan_requires_captured_live_operations():
    planner = ActionPlanner()
    player_state = _mock_player_state(cans=_mock_cans(can_riichi=True))

    plan = planner.plan(
        {"type": "reach", "pai": "1m"},
        ["1m"] * MahjongConstants.TEHAI_SIZE,
        None,
        player_state=player_state,
        require_live_operations=True,
    )

    assert plan == []


def test_dealer_opening_uses_continuous_fourteen_tile_layout_and_animation_floor():
    planner = ActionPlanner()
    planner.observe_event(SimpleNamespace(type="start_game", id=1))
    planner.observe_event(SimpleNamespace(type="start_kyoku", oya=1))
    tehai = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"]

    with (
        patch.object(local_settings.autoplay, "delay_mode", "human"),
        patch.object(delay_controller, "sleep_seconds", return_value=0.12),
    ):
        plan = planner.plan(
            {"type": "dahai", "pai": "9s", "tsumogiri": True},
            tehai,
            "9s",
            elapsed_seconds=0.5,
        )

    assert len(plan) == 1
    assert plan[0].coord == LOCATION["tiles"][13]
    assert plan[0].delay == pytest.approx(2.5)


def test_non_dealer_first_action_does_not_wait_for_dealer_opening_animation():
    planner = ActionPlanner()
    planner.observe_event(SimpleNamespace(type="start_game", id=1))
    planner.observe_event(SimpleNamespace(type="start_kyoku", oya=0))

    with (
        patch.object(local_settings.autoplay, "delay_mode", "human"),
        patch.object(delay_controller, "sleep_seconds", return_value=0.12),
    ):
        plan = planner.plan(
            {"type": "dahai", "pai": "9s", "tsumogiri": True},
            ["1m"] * MahjongConstants.TEHAI_SIZE,
            "9s",
        )

    assert len(plan) == 1
    assert plan[0].delay == pytest.approx(1.0)


def test_non_tsumogiri_prefers_matching_tile_in_hand_over_detached_draw():
    planner = ActionPlanner()
    planner.is_new_round = False
    tehai = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"]

    plan = planner.plan(
        {"type": "dahai", "pai": "5m", "tsumogiri": False},
        tehai,
        "5m",
    )

    assert len(plan) == 1
    assert plan[0].coord == planner.get_pai_coord(4, tehai)


def test_red_five_discard_prefers_exact_red_tile_over_normal_five():
    planner = ActionPlanner()
    planner.is_new_round = False
    tehai = ["1m", "2m", "3m", "4m", "5m", "5mr", "6m", "7m", "8m", "9m", "1p", "2p", "3p"]

    plan = planner.plan(
        {"type": "dahai", "pai": "5mr", "tsumogiri": False},
        tehai,
        "9p",
    )

    assert len(plan) == 1
    assert plan[0].coord == planner.get_pai_coord(4, tehai)
    assert plan[0].coord != planner.get_pai_coord(5, tehai)


def test_full_hand_does_not_duplicate_drawn_tile_already_in_tracker_hand():
    planner = ActionPlanner()
    tehai = [
        "1m",
        "2m",
        "3m",
        "4m",
        "5m",
        "6m",
        "7m",
        "8m",
        "9m",
        "1p",
        "2p",
        "3p",
        "4p",
        "5p",
    ]

    full_hand = planner._full_hand(tehai, "5p")

    assert len(full_hand) == MahjongConstants.TSUMO_TEHAI_SIZE
    assert full_hand.count("5p") == 1


def test_advanced_delay_subtracts_inference_time_to_avoid_late_clicks():
    planner = ActionPlanner()

    with (
        patch.object(local_settings.autoplay, "delay_mode", "advanced"),
        patch.object(local_settings.autoplay.advanced_timing.discard, "min", 3.0),
        patch.object(local_settings.autoplay.advanced_timing.discard, "max", 3.0),
    ):
        delay = planner.discard_delay(
            {"type": "dahai", "pai": "1m"},
            1.25,
            first_action=False,
            dealer_opening=False,
        )

    assert delay == pytest.approx(1.75)


def test_action_button_floor_is_total_time_not_extra_sleep():
    planner = ActionPlanner()

    with (
        patch.object(local_settings.autoplay, "delay_mode", "human"),
        patch.object(delay_controller, "sleep_seconds", return_value=0.12),
    ):
        delay = planner.action_delay(
            {"type": "pon"},
            0.5,
            dealer_opening=False,
        )

    assert delay == pytest.approx(1.1)


def test_daiminkan_multiple_candidates_gets_second_selection_click():
    planner = ActionPlanner()
    planner.update_operation_list([{"type": 5, "combination": ["5m|5m|5mr", "5m|5mr|5mr"]}])

    plan = planner.plan(
        {"type": "daiminkan", "consumed": ["5m", "5m", "5mr"]},
        ["5m", "5m", "5mr"] + ["1p"] * 10,
        None,
        require_live_operations=True,
    )

    assert [click.label for click in plan] == ["daiminkan", "daiminkan-candidate"]
    assert plan[0].verify_operation_clear is False
