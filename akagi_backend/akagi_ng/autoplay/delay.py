from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

DelayMode = Literal["human", "advanced"]
MAX_DELAY_SECONDS = 15.0
MIN_CLICK_DELAY_SECONDS = 0.12
CLOSE_MARGIN = 0.02
LONG_THINK_PROBABILITY = 0.02
HIGH_CONFIDENCE = 0.97
LOW_CONFIDENCE = 0.60
RIICHI_LONG_TAIL_PROBABILITY = 0.06
RANGE_ENDPOINT_COUNT = 2


@dataclass(frozen=True, slots=True)
class DelayContext:
    action: str
    tsumogiri: bool = False
    post_call: bool = False
    tile_class: str | None = None
    first_action: bool = False
    dealer_opening: bool = False
    can_riichi: bool = False
    is_kan: bool = False
    in_riichi: bool = False
    opponent_riichi: bool = False
    junme: int = 0
    legal_count: int = 0
    top_prob: float | None = None
    second_prob: float | None = None
    margin: float | None = None
    elapsed_seconds: float = 0.0


ROUTINE = {
    "tedashi": {"honor": 0.12, "terminal": 0.00, "middle": 0.00},
    "tsumogiri": {"honor": 0.15, "terminal": 0.10, "middle": 0.04},
}
THINK = {
    "tedashi": {
        "honor": (0.77, 0.50),
        "terminal": (0.83, 0.50),
        "middle": (1.01, 0.55),
        "default": (0.87, 0.55),
    },
    "tsumogiri": {
        "honor": (0.54, 0.45),
        "terminal": (0.57, 0.45),
        "middle": (0.63, 0.48),
        "default": (0.57, 0.47),
    },
}
OTHER = {
    "reach": (1.10, 0.55),
    "claim": (0.26, 0.57),
    "post_call_dahai": (0.52, 0.42),
    "hora": (0.15, 0.50),
    "in_riichi": (0.35, 0.30),
}


class HumanDelayModel:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    def target_seconds(self, context: DelayContext) -> float:
        if context.in_riichi:
            return self._lognormal(*OTHER["in_riichi"])

        if context.action in {"dahai", "discard", "tsumogiri", "first_discard", "reach_discard"}:
            think = self._lognormal(*OTHER["post_call_dahai"]) if context.post_call else self._discard(context)
        elif context.action == "reach":
            think = self._lognormal(*OTHER["reach"])
        elif context.action in {"hora", "tsumo", "ron"}:
            think = self._lognormal(*OTHER["hora"])
        elif context.action in {"ankan", "kakan"}:
            think = self._lognormal(*THINK["tedashi"]["middle"])
        else:
            think = self._lognormal(*OTHER["claim"])

        if context.first_action:
            think += 0.5 + 0.8 * self._rng.random()
        if context.can_riichi and context.action in {"dahai", "discard", "tsumogiri"}:
            think += 0.4 + 0.8 * self._rng.random()
        if context.margin is not None and context.margin < CLOSE_MARGIN:
            think += self._lognormal(0.6, 0.5)
        if self._rng.random() < LONG_THINK_PROBABILITY:
            think += 2.0 + self._lognormal(0.8, 0.6)
        return _bounded_target(think)

    def _discard(self, context: DelayContext) -> float:
        giri = "tsumogiri" if context.tsumogiri else "tedashi"
        tile_kind = context.tile_class or "middle"
        routine = ROUTINE[giri].get(tile_kind, 0.04)
        if context.top_prob is not None:
            if context.top_prob > HIGH_CONFIDENCE:
                routine *= 1.8
            if context.top_prob < LOW_CONFIDENCE:
                routine *= 0.3
        if context.opponent_riichi:
            routine *= 0.4
        if self._rng.random() < min(routine, 0.6):
            return self._lognormal(0.0, 0.18)

        params = THINK[giri].get(tile_kind, THINK[giri]["default"])
        think = self._lognormal(*params)
        if giri == "tedashi" and context.junme > 0:
            think *= 1.0 + 0.012 * min(context.junme, 15)
        if context.opponent_riichi:
            think *= 1.16 if giri == "tedashi" else 1.25
            if self._rng.random() < RIICHI_LONG_TAIL_PROBABILITY:
                think += self._lognormal(0.9, 0.5)
        return think

    def _lognormal(self, mu: float, sigma: float) -> float:
        return self._rng.lognormvariate(mu, sigma)


class DelayController:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.human = HumanDelayModel(rng)
        self._rng = rng or random.Random()

    def sleep_seconds(
        self,
        context: DelayContext,
        mode: DelayMode,
        minimum: float,
        maximum: float,
    ) -> float:
        low, high = _normalized_range(minimum, maximum)
        if mode == "advanced":
            target = self._rng.uniform(low, high)
        else:
            target = min(max(self.human.target_seconds(context), low), high)
        return max(MIN_CLICK_DELAY_SECONDS, target - max(context.elapsed_seconds, 0.0))

    def interval_seconds(self, minimum: float, maximum: float, *, floor: float = 0.0) -> float:
        low, high = _normalized_range(minimum, maximum)
        return max(floor, self._rng.uniform(low, high))


def tile_class(tile: str | None) -> str | None:
    if not tile:
        return None
    if tile in {"E", "S", "W", "N", "P", "F", "C"}:
        return "honor"
    number = tile[0]
    if number in {"1", "9"}:
        return "terminal"
    if number.isdigit():
        return "middle"
    return None


def _normalized_range(minimum: float, maximum: float) -> tuple[float, float]:
    values = [value for value in (minimum, maximum) if math.isfinite(value)]
    if len(values) != RANGE_ENDPOINT_COUNT:
        return MIN_CLICK_DELAY_SECONDS, MIN_CLICK_DELAY_SECONDS
    low = max(0.0, min(values))
    high = min(MAX_DELAY_SECONDS, max(values))
    return low, max(low, high)


def _bounded_target(value: float) -> float:
    if not math.isfinite(value):
        return MIN_CLICK_DELAY_SECONDS
    return max(MIN_CLICK_DELAY_SECONDS, min(value, MAX_DELAY_SECONDS))


delay_controller = DelayController()
