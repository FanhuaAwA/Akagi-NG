from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cmp_to_key
from itertools import combinations

from akagi_ng.autoplay.delay import DelayContext, delay_controller, tile_class
from akagi_ng.bridge.majsoul.tile_mapping import MS_TILE_2_MJAI_TILE, compare_pai
from akagi_ng.schema.constants import MahjongConstants
from akagi_ng.schema.protocols import ActionCandidatesProtocol, PlayerStateProtocol
from akagi_ng.settings import local_settings
from akagi_ng.settings.settings import DelayRangeConfig

LOCATION = {
    "tiles": [
        (2.23125, 8.3625),
        (3.021875, 8.3625),
        (3.8125, 8.3625),
        (4.603125, 8.3625),
        (5.39375, 8.3625),
        (6.184375, 8.3625),
        (6.975, 8.3625),
        (7.765625, 8.3625),
        (8.55625, 8.3625),
        (9.346875, 8.3625),
        (10.1375, 8.3625),
        (10.928125, 8.3625),
        (11.71875, 8.3625),
        (12.509375, 8.3625),
    ],
    "tsumo_space": 0.246875,
    "actions": [
        (10.875, 7.0),
        (8.6375, 7.0),
        (6.4, 7.0),
        (10.875, 5.9),
        (8.6375, 5.9),
        (6.4, 5.9),
        (10.875, 4.8),
        (8.6375, 4.8),
        (6.4, 4.8),
    ],
    "candidates": [
        (3.6625, 6.3),
        (4.49625, 6.3),
        (5.33, 6.3),
        (6.16375, 6.3),
        (6.9975, 6.3),
        (7.83125, 6.3),
        (8.665, 6.3),
        (9.49875, 6.3),
        (10.3325, 6.3),
        (11.16625, 6.3),
        (12.0, 6.3),
    ],
    "candidates_kan": [
        (4.325, 6.3),
        (5.4915, 6.3),
        (6.6583, 6.3),
        (7.825, 6.3),
        (8.9917, 6.3),
        (10.1583, 6.3),
        (11.325, 6.3),
    ],
}

ACTION_PRIORITY = {
    0: 0,
    1: 99,
    2: 4,
    3: 3,
    4: 3,
    5: 2,
    6: 3,
    7: 2,
    8: 1,
    9: 1,
    10: 5,
    11: 4,
}

ACTION2TYPE = {
    "none": 0,
    "chi": 2,
    "pon": 3,
    "ankan": 4,
    "daiminkan": 5,
    "kakan": 6,
    "reach": 7,
    "ryukyoku": 10,
    "nukidora": 11,
}

BUTTON_ACTIONS = set(ACTION2TYPE) | {"hora", "tsumo", "ron"}
ANKAN_OPERATION_TYPE = ACTION2TYPE["ankan"]
KAKAN_OPERATION_TYPE = ACTION2TYPE["kakan"]
KAN_TILE_COUNT = 4
CONCEALED_HAND_MODULUS = 3
DRAWN_HAND_REMAINDER = 2
MIN_TILE_UI_READY_SECONDS = 1.0
MIN_BUTTON_UI_READY_SECONDS = 1.6
MIN_INTER_CLICK_SECONDS = 0.3
OPENING_ANIMATION_FLOOR_SECONDS = 3.0
CLAIM_OPERATION_TYPES = {ACTION2TYPE["chi"], ACTION2TYPE["pon"], ACTION2TYPE["daiminkan"], 9}


@dataclass(slots=True)
class PlannedClick:
    coord: tuple[float, float]
    delay: float
    label: str
    expected_types: tuple[int, ...] = ()
    requires_operation_step: bool = False
    verify_operation_clear: bool = True


class ActionPlanner:
    def __init__(self) -> None:
        self.is_new_round = True
        self.reached = False
        self.pending_reach_discard = False
        self.latest_operation_list: list[dict] = []
        self.player_id = 0
        self.oya: int | None = None
        self.junme = 0
        self.opponent_riichi = False

    def observe_event(self, event: object) -> None:
        event_type = getattr(event, "type", None)
        if event_type == "start_game":
            self.player_id = int(getattr(event, "id", 0))
        elif event_type == "start_kyoku":
            self.oya = int(getattr(event, "oya", -1))
        if event_type in {"start_game", "start_kyoku", "end_kyoku", "end_game"}:
            self.is_new_round = True
            self.reached = False
            self.pending_reach_discard = False
            self.junme = 0
            self.opponent_riichi = False
        elif event_type == "dahai" and getattr(event, "actor", None) == self.player_id:
            self.junme += 1
            self.is_new_round = False
        elif event_type == "reach_accepted":
            actor = getattr(event, "actor", None)
            if actor == self.player_id:
                self.reached = True
            elif actor is not None:
                self.opponent_riichi = True

    def update_operation_list(self, operation_list: list[dict] | None) -> None:
        self.latest_operation_list = operation_list or []

    def discard_delay(
        self,
        action: dict,
        elapsed_seconds: float,
        *,
        first_action: bool,
        dealer_opening: bool,
        delay_action: str | None = None,
    ) -> float:
        action_name = delay_action or (
            "first_discard" if first_action else "tsumogiri" if action.get("tsumogiri") else "discard"
        )
        context = self._delay_context(
            action,
            elapsed_seconds,
            first_action=first_action,
            dealer_opening=dealer_opening,
            delay_action=action_name,
        )
        timing_range = self._timing_range(action_name, first_action=first_action, button=False)
        delay = delay_controller.sleep_seconds(
            context,
            local_settings.autoplay.delay_mode,
            timing_range.min,
            timing_range.max,
        )
        return self._apply_ui_floor(
            delay,
            elapsed_seconds,
            dealer_opening=dealer_opening,
            button=False,
        )

    def action_delay(self, action: dict, elapsed_seconds: float, *, dealer_opening: bool) -> float:
        action_name = self._button_delay_action(action)
        context = self._delay_context(
            action,
            elapsed_seconds,
            first_action=self.is_new_round,
            dealer_opening=dealer_opening,
            delay_action=action_name,
        )
        timing_range = self._timing_range(action_name, first_action=False, button=True)
        delay = delay_controller.sleep_seconds(
            context,
            local_settings.autoplay.delay_mode,
            timing_range.min,
            timing_range.max,
        )
        return self._apply_ui_floor(
            delay,
            elapsed_seconds,
            dealer_opening=dealer_opening,
            button=True,
        )

    def candidate_delay(self, action_name: str = "candidate") -> float:
        timing_range = self._timing_range(action_name, first_action=False, button=True)
        return delay_controller.interval_seconds(
            timing_range.min,
            timing_range.max,
            floor=MIN_INTER_CLICK_SECONDS,
        )

    def get_pai_coord(self, idx: int, tehais: list[str]) -> tuple[float, float]:
        visible_count = sum(1 for tehai in tehais if tehai != "?")
        last_tile_index = len(LOCATION["tiles"]) - 1
        if idx >= MahjongConstants.TEHAI_SIZE:
            base_index = min(visible_count, last_tile_index)
            base = LOCATION["tiles"][base_index]
            return (base[0] + LOCATION["tsumo_space"], base[1])
        return LOCATION["tiles"][min(idx, last_tile_index)]

    def plan(  # noqa: C901, PLR0911, PLR0912, PLR0913
        self,
        mjai_msg: dict | None,
        tehai: list[str],
        tsumohai: str | None,
        *,
        player_state: PlayerStateProtocol | None = None,
        last_kawa_tile: str | None = None,
        elapsed_seconds: float = 0.0,
        require_live_operations: bool = False,
    ) -> list[PlannedClick]:
        if mjai_msg is None:
            return []

        action_type = mjai_msg.get("type")
        dealer_opening = self.is_new_round and self.oya is not None and self.player_id == self.oya
        if action_type == "dahai" and (not self.reached or self.pending_reach_discard):
            first_action = self.is_new_round
            is_reach_discard = self.pending_reach_discard
            coord = self._find_discard_coord(
                mjai_msg["pai"],
                list(tehai),
                tsumohai,
                prefer_tsumo=bool(mjai_msg.get("tsumogiri")),
                dealer_opening=dealer_opening,
            )
            if coord is None:
                return []
            label = "reach-discard" if is_reach_discard else "discard"
            self.pending_reach_discard = False
            return [
                PlannedClick(
                    coord=coord,
                    delay=self.discard_delay(
                        mjai_msg,
                        elapsed_seconds,
                        first_action=first_action,
                        dealer_opening=dealer_opening,
                        delay_action="reach_discard" if is_reach_discard else None,
                    ),
                    label=label,
                    expected_types=(1,),
                    requires_operation_step=False,
                )
            ]

        if action_type not in BUTTON_ACTIONS:
            return []
        if action_type == "none" and not any(
            item.get("type") in CLAIM_OPERATION_TYPES for item in self.latest_operation_list
        ):
            return []
        if require_live_operations and not self.latest_operation_list:
            return []

        operation_list = self._sorted_operation_list(player_state, last_kawa_tile, action_type, tehai, tsumohai)
        action_index = self._find_action_index(operation_list, self._operation_types_for_action(action_type))
        if action_index is None:
            return []

        click_label = "skip" if action_type == "none" else action_type
        plan = [
            PlannedClick(
                coord=LOCATION["actions"][action_index],
                delay=self.action_delay(mjai_msg, elapsed_seconds, dealer_opening=dealer_opening),
                label=click_label,
                expected_types=self._expected_types_for_action(action_type),
                requires_operation_step=True,
            )
        ]

        if action_type == "reach":
            plan[0].verify_operation_clear = False
            pai = mjai_msg.get("pai")
            if pai is None and isinstance(mjai_msg.get("reach_dahai"), dict):
                pai = mjai_msg["reach_dahai"].get("pai")
            if pai is None:
                self.pending_reach_discard = True
                return plan

            discard_coord = self._find_discard_coord(
                pai,
                list(tehai),
                tsumohai,
                prefer_tsumo=bool(mjai_msg.get("tsumogiri")),
                dealer_opening=dealer_opening,
            )
            if discard_coord is None:
                self.pending_reach_discard = True
                return plan

            self.pending_reach_discard = False
            plan.append(
                PlannedClick(
                    coord=discard_coord,
                    delay=self.candidate_delay("reach_discard"),
                    label="reach-discard",
                    expected_types=(1,),
                    requires_operation_step=True,
                )
            )
            return plan

        if action_type in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
            candidate_click = self._plan_candidate_click(operation_list, mjai_msg)
            if candidate_click is not None:
                plan[0].verify_operation_clear = False
                plan.append(candidate_click)
        return plan

    def _delay_context(
        self,
        action: dict,
        elapsed_seconds: float,
        *,
        first_action: bool,
        dealer_opening: bool,
        delay_action: str | None = None,
    ) -> DelayContext:
        action_type = delay_action or str(action.get("type") or "none")
        legal_types = {item.get("type") for item in self.latest_operation_list}
        return DelayContext(
            action=action_type,
            tsumogiri=bool(action.get("tsumogiri")),
            tile_class=tile_class(action.get("pai")),
            first_action=first_action,
            dealer_opening=dealer_opening,
            can_riichi=ACTION2TYPE["reach"] in legal_types,
            is_kan=action_type in {"ankan", "kakan", "daiminkan"},
            in_riichi=self.reached,
            opponent_riichi=self.opponent_riichi,
            junme=self.junme,
            legal_count=len(legal_types),
            elapsed_seconds=elapsed_seconds,
        )

    def _button_delay_action(self, action: dict) -> str:
        action_type = str(action.get("type") or "none")
        if action_type == "none":
            return "skip"
        if action_type == "hora":
            actor = action.get("actor")
            target = action.get("target")
            return "tsumo" if actor is not None and actor == target else "ron"
        return action_type

    def _timing_range(self, action_name: str, *, first_action: bool, button: bool) -> DelayRangeConfig:
        autoplay = local_settings.autoplay
        if autoplay.delay_mode == "advanced":
            return getattr(autoplay.advanced_timing, action_name, autoplay.advanced_timing.candidate)
        if first_action:
            return autoplay.timing.first_discard
        if action_name == "reach_discard":
            return autoplay.timing.candidate
        if action_name in {"discard", "tsumogiri"} and not button:
            return autoplay.timing.discard
        return (
            autoplay.timing.button
            if button and action_name not in {"candidate", "reach_discard"}
            else autoplay.timing.candidate
        )

    def _apply_ui_floor(
        self,
        delay: float,
        elapsed_seconds: float,
        *,
        dealer_opening: bool,
        button: bool,
    ) -> float:
        target_total = MIN_BUTTON_UI_READY_SECONDS if button else MIN_TILE_UI_READY_SECONDS
        if dealer_opening:
            target_total = max(
                target_total,
                OPENING_ANIMATION_FLOOR_SECONDS,
            )
        remaining = max(0.12, target_total - max(elapsed_seconds, 0.0))
        return max(delay, remaining)

    def _find_discard_coord(  # noqa: PLR0911
        self,
        dahai: str,
        tehai: list[str],
        tsumohai: str | None,
        prefer_tsumo: bool = False,
        dealer_opening: bool = False,
    ) -> tuple[float, float] | None:
        normalized_tehai, detached_tile = self._normalize_visible_hand(tehai, tsumohai)

        if dealer_opening:
            opening_hand = list(normalized_tehai)
            if detached_tile is not None:
                opening_hand.append(detached_tile)
            opening_hand.sort(key=cmp_to_key(compare_pai))
            opening_index = self._find_exact_or_matching_index(opening_hand, dahai)
            if opening_index is not None and opening_index < len(LOCATION["tiles"]):
                return LOCATION["tiles"][opening_index]
            return None

        if prefer_tsumo and detached_tile and self._tile_matches(dahai, detached_tile):
            return self.get_pai_coord(MahjongConstants.TEHAI_SIZE, normalized_tehai)

        hand_index = self._find_exact_or_matching_index(normalized_tehai, dahai)
        if hand_index is not None:
            return self.get_pai_coord(hand_index, normalized_tehai)

        if detached_tile and self._tile_matches(dahai, detached_tile):
            return self.get_pai_coord(MahjongConstants.TEHAI_SIZE, normalized_tehai)

        if prefer_tsumo:
            return self.get_pai_coord(MahjongConstants.TEHAI_SIZE, normalized_tehai)
        return None

    def _sorted_operation_list(
        self,
        player_state: PlayerStateProtocol | None,
        last_kawa_tile: str | None,
        requested_type: str,
        tehai: list[str],
        tsumohai: str | None,
    ) -> list[dict]:
        operations = self.latest_operation_list or self._fallback_operation_list(
            player_state, last_kawa_tile, requested_type, tehai, tsumohai
        )
        operations = [operation.copy() for operation in operations]
        operations.append({"type": 0, "combination": []})

        can_ankan = any(operation["type"] == ANKAN_OPERATION_TYPE for operation in operations)
        can_kakan = any(operation["type"] == KAKAN_OPERATION_TYPE for operation in operations)
        if can_ankan and can_kakan:
            ankan_combinations = [
                combination
                for operation in operations
                if operation["type"] == ANKAN_OPERATION_TYPE
                for combination in operation.get("combination", [])
            ]
            merged_operations: list[dict] = []
            for operation in operations:
                if operation["type"] == ANKAN_OPERATION_TYPE:
                    continue
                if operation["type"] == KAKAN_OPERATION_TYPE:
                    updated = operation.copy()
                    updated["combination"] = list(updated.get("combination", [])) + ankan_combinations
                    merged_operations.append(updated)
                    continue
                merged_operations.append(operation)
            operations = merged_operations

        operations.sort(key=lambda item: ACTION_PRIORITY.get(item["type"], 99))
        return operations

    def _find_action_index(self, operations: list[dict], action_types: tuple[int, ...]) -> int | None:
        for idx, operation in enumerate(operations):
            if operation["type"] in action_types:
                return idx
        return None

    def _plan_candidate_click(self, operation_list: list[dict], mjai_msg: dict) -> PlannedClick | None:
        action_type = mjai_msg["type"]
        consumed = sorted(mjai_msg.get("consumed", []), key=cmp_to_key(compare_pai))
        target_type = ACTION2TYPE[action_type]

        for operation in operation_list:
            if operation["type"] != target_type and not (
                action_type in {"ankan", "kakan"} and operation["type"] == KAKAN_OPERATION_TYPE
            ):
                continue

            combinations_raw = list(operation.get("combination", []))
            if len(combinations_raw) <= 1:
                return None

            candidate_slots = (
                LOCATION["candidates_kan"] if action_type in {"ankan", "kakan"} else LOCATION["candidates"]
            )
            mid_point = 3 if action_type in {"ankan", "kakan"} else 5
            for idx, combination in enumerate(combinations_raw):
                normalized = sorted(self._normalize_combination(combination), key=cmp_to_key(compare_pai))
                if normalized != consumed:
                    continue
                candidate_index = int((-(len(combinations_raw) / 2) + idx + 0.5) * 2 + mid_point)
                if 0 <= candidate_index < len(candidate_slots):
                    return PlannedClick(
                        coord=candidate_slots[candidate_index],
                        delay=self.candidate_delay(),
                        label=f"{action_type}-candidate",
                        expected_types=self._operation_types_for_action(action_type),
                        requires_operation_step=True,
                    )
        return None

    def _fallback_operation_list(  # noqa: C901
        self,
        player_state: PlayerStateProtocol | None,
        last_kawa_tile: str | None,
        requested_type: str,
        tehai: list[str],
        tsumohai: str | None,
    ) -> list[dict]:
        if player_state is None:
            fallback_type = self._operation_types_for_action(requested_type)
            return [{"type": fallback_type[0], "combination": []}] if fallback_type else []

        operations: list[dict] = []
        hand = self._full_hand(tehai, tsumohai)
        cans = player_state.last_cans

        chi_combinations = self._build_chi_combinations(last_kawa_tile, hand, cans)
        if chi_combinations:
            operations.append({"type": 2, "combination": chi_combinations})

        pon_combinations = self._build_same_tile_combinations(last_kawa_tile, hand, 2)
        if cans.can_pon and pon_combinations:
            operations.append({"type": 3, "combination": pon_combinations})

        ankan_combinations = self._build_ankan_combinations(tehai, tsumohai)
        if cans.can_ankan and ankan_combinations:
            operations.append({"type": 4, "combination": ankan_combinations})

        daiminkan_combinations = self._build_same_tile_combinations(last_kawa_tile, hand, 3)
        if cans.can_daiminkan and daiminkan_combinations:
            operations.append({"type": 5, "combination": daiminkan_combinations})

        kakan_combinations = self._build_kakan_combinations(player_state, hand)
        if cans.can_kakan and kakan_combinations:
            operations.append({"type": 6, "combination": kakan_combinations})

        if cans.can_riichi:
            operations.append({"type": 7, "combination": []})
        if cans.can_tsumo_agari or cans.can_ron_agari:
            operations.append({"type": 9 if cans.can_ron_agari else 8, "combination": []})
        if cans.can_ryukyoku:
            operations.append({"type": 10, "combination": []})
        if requested_type == "nukidora":
            operations.append({"type": 11, "combination": []})
        return operations

    def _operation_types_for_action(self, action_type: str) -> tuple[int, ...]:
        if action_type in {"hora", "tsumo", "ron"}:
            return (8, 9)
        if action_type not in ACTION2TYPE:
            return ()
        return (ACTION2TYPE[action_type],)

    def _expected_types_for_action(self, action_type: str) -> tuple[int, ...]:
        if action_type == "none":
            return tuple(op["type"] for op in self.latest_operation_list if op.get("type", 0) != 0)
        return self._operation_types_for_action(action_type)

    def _build_ankan_combinations(self, tehai: list[str], tsumohai: str | None) -> list[str]:
        full_hand = self._full_hand(tehai, tsumohai)
        grouped: dict[str, list[str]] = {}
        for tile in full_hand:
            grouped.setdefault(tile.replace("r", ""), []).append(tile)

        combinations_out: list[str] = []
        for base_tile, tiles in grouped.items():
            if len(tiles) < KAN_TILE_COUNT:
                continue
            ordered = sorted(tiles, key=cmp_to_key(compare_pai))
            while len(ordered) < KAN_TILE_COUNT:
                ordered.append(base_tile)
            combinations_out.append("|".join(ordered[:KAN_TILE_COUNT]))
        return combinations_out

    def _build_kakan_combinations(self, player_state: PlayerStateProtocol, hand: list[str]) -> list[str]:
        combinations_out: list[str] = []
        for cand in player_state.kakan_candidates():
            combinations_out.extend(self._build_tile_selection_combinations([cand.replace("r", "")], hand))
        return list(dict.fromkeys(combinations_out))

    def _build_chi_combinations(
        self,
        last_kawa_tile: str | None,
        hand: list[str],
        cans: ActionCandidatesProtocol,
    ) -> list[str]:
        if not last_kawa_tile:
            return []
        base = last_kawa_tile.replace("r", "")
        try:
            num = int(base[0])
            suit = base[1]
        except (ValueError, IndexError):
            return []

        targets_by_type = {
            "chi_low": [f"{num + 1}{suit}", f"{num + 2}{suit}"],
            "chi_mid": [f"{num - 1}{suit}", f"{num + 1}{suit}"],
            "chi_high": [f"{num - 2}{suit}", f"{num - 1}{suit}"],
        }

        combinations_out: list[str] = []
        for chi_type, targets in targets_by_type.items():
            if not getattr(cans, f"can_{chi_type}", False):
                continue
            if any(target[0] not in "123456789" for target in targets):
                continue
            if not all("1" <= target[0] <= "9" and target[1] == suit for target in targets):
                continue
            combinations_out.extend(self._build_tile_selection_combinations(targets, hand))
        return list(dict.fromkeys(combinations_out))

    def _build_same_tile_combinations(self, tile: str | None, hand: list[str], count: int) -> list[str]:
        if not tile:
            return []
        return self._build_tile_selection_combinations([tile.replace("r", "")] * count, hand)

    def _build_tile_selection_combinations(self, targets: list[str], hand: list[str]) -> list[str]:
        if not targets:
            return []

        indexed_hand = list(enumerate(hand))
        combinations_out: set[str] = set()
        for picked in combinations(indexed_hand, len(targets)):
            tiles = [tile for _idx, tile in picked]
            if self._matches_target_multiset(tiles, targets):
                normalized = sorted(tiles, key=cmp_to_key(compare_pai))
                combinations_out.add("|".join(normalized))
        return sorted(combinations_out)

    def _matches_target_multiset(self, tiles: list[str], targets: list[str]) -> bool:
        remaining = list(targets)
        for tile in tiles:
            matched = False
            for idx, target in enumerate(remaining):
                if self._tile_matches(target, tile):
                    remaining.pop(idx)
                    matched = True
                    break
            if not matched:
                return False
        return not remaining

    def _normalize_combination(self, combination: str | Iterable[str]) -> list[str]:
        tiles = combination.split("|") if isinstance(combination, str) else list(combination)
        return [MS_TILE_2_MJAI_TILE.get(tile, tile) for tile in tiles]

    def _full_hand(self, tehai: list[str], tsumohai: str | None) -> list[str]:
        hand, detached_tile = self._normalize_visible_hand(tehai, tsumohai)
        if detached_tile is not None:
            hand.append(detached_tile)
        return hand

    def _normalize_visible_hand(self, tehai: list[str], tsumohai: str | None) -> tuple[list[str], str | None]:
        sorted_tehai = sorted((tile for tile in tehai if tile != "?"), key=cmp_to_key(compare_pai))
        detached_tile = tsumohai if tsumohai and tsumohai != "?" else None

        if detached_tile is not None and len(sorted_tehai) % CONCEALED_HAND_MODULUS == DRAWN_HAND_REMAINDER:
            detached_index = self._find_exact_or_matching_index(sorted_tehai, detached_tile)
            if detached_index is not None:
                sorted_tehai.pop(detached_index)
        elif len(sorted_tehai) > MahjongConstants.TEHAI_SIZE:
            detached_tile = sorted_tehai.pop()

        if len(sorted_tehai) > MahjongConstants.TEHAI_SIZE:
            sorted_tehai = sorted_tehai[: MahjongConstants.TEHAI_SIZE]

        return sorted_tehai, detached_tile

    def _find_exact_or_matching_index(self, tiles: list[str], target: str) -> int | None:
        for idx, tile in enumerate(tiles):
            if tile == target:
                return idx
        for idx, tile in enumerate(tiles):
            if self._tile_matches(target, tile):
                return idx
        return None

    def _tile_matches(self, target: str, current: str) -> bool:
        if target == current:
            return True
        if target.endswith("r"):
            return target[:2] == current[:2]
        if current.endswith("r"):
            return target[:2] == current[:2]
        return target == current
