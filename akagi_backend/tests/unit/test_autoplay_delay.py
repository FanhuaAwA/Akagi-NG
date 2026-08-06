from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from akagi_ng.autoplay.delay import (
    MAX_DELAY_SECONDS,
    MIN_CLICK_DELAY_SECONDS,
    DelayContext,
    DelayController,
    HumanDelayModel,
    tile_class,
)


def test_human_delay_is_bounded_and_reproducible() -> None:
    context = DelayContext(
        action="discard",
        tsumogiri=False,
        tile_class="middle",
        first_action=True,
        opponent_riichi=True,
        junme=12,
    )
    first = HumanDelayModel(random.Random(42)).target_seconds(context)
    second = HumanDelayModel(random.Random(42)).target_seconds(context)

    assert first == second
    assert MIN_CLICK_DELAY_SECONDS <= first <= MAX_DELAY_SECONDS


def test_advanced_delay_uses_action_range_and_subtracts_elapsed_time() -> None:
    controller = DelayController(random.Random(7))

    delay = controller.sleep_seconds(
        DelayContext(action="ron", elapsed_seconds=0.75),
        "advanced",
        2.5,
        2.5,
    )

    assert delay == pytest.approx(1.75)


def test_human_delay_is_clamped_to_configured_range() -> None:
    controller = DelayController(random.Random(3))

    with patch.object(HumanDelayModel, "target_seconds", return_value=8.0):
        delay = controller.sleep_seconds(DelayContext(action="pon"), "human", 1.0, 2.0)

    assert delay == pytest.approx(2.0)


def test_interval_normalizes_reversed_range_and_respects_floor() -> None:
    controller = DelayController(random.Random(1))

    assert controller.interval_seconds(0.1, 0.1, floor=0.3) == pytest.approx(0.3)
    assert 1.0 <= controller.interval_seconds(3.0, 1.0) <= 3.0


@pytest.mark.parametrize(
    ("tile", "expected"),
    [("E", "honor"), ("C", "honor"), ("1m", "terminal"), ("9s", "terminal"), ("5pr", "middle")],
)
def test_tile_classification(tile: str, expected: str) -> None:
    assert tile_class(tile) == expected
