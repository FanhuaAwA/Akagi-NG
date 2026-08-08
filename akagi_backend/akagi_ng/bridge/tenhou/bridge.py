import json
import re
from dataclasses import replace

from akagi_ng.bridge.base import BaseBridge
from akagi_ng.bridge.logger import logger
from akagi_ng.bridge.tenhou.consts import TenhouConstants
from akagi_ng.bridge.tenhou.utils.converter import (
    tenhou_to_mjai,
    tenhou_to_mjai_one,
    to_34_array,
)
from akagi_ng.bridge.tenhou.utils.decoder import Meld, MeldType, parse_sc_tag
from akagi_ng.bridge.tenhou.utils.judrdy import isrh
from akagi_ng.bridge.tenhou.utils.state import State
from akagi_ng.schema.constants import MahjongConstants
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import AkagiEvent, HoraEvent, MJAIEvent, RyukyokuEvent


class TenhouBridge(BaseBridge):
    def __init__(self):
        super().__init__()
        self.state = State()
        self.game_ended = False
        self._pending_hora_end = False
        self._pending_draw_actors: set[int] = set()
        self._pending_draw_tiles: dict[int, int] = {}
        self.handlers = {
            "HELO": self._convert_helo,
            "REJOIN": self._convert_rejoin,
            "UN": self._convert_un,
            "TAIKYOKU": self._convert_start_game,
            "INIT": self._convert_start_kyoku,
            "DORA": self._convert_dora,
            "REACH": self._dispatch_reach,
            "AGARI": self._convert_hora,
            "RYUUKYOKU": self._convert_ryukyoku,
            "N": self._dispatch_n,
        }

    def reset(self):
        self.state = State()
        self.game_ended = False
        self._pending_hora_end = False
        self._pending_draw_actors.clear()
        self._pending_draw_tiles.clear()

    def parse(self, content: bytes) -> list[AkagiEvent]:
        """解析内容并返回 MJAI 指令。

        Args:
            content (bytes): 待解析的内容。

        Returns:
            list[AkagiEvent]: MJAI 指令
        """
        try:
            message = self._decode_message(content)
            if not message:
                return []

            logger.trace(f"<- {message}")
            parsed = self._dispatch_message(message)

            if parsed:
                logger.trace(f"-> {parsed}")
            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding Tenhou JSON: {e}")
            return [self.make_system_event(NotificationCode.JSON_DECODE_ERROR)]
        except Exception as e:
            logger.error(f"Error parsing Tenhou message: {e}")
            return [self.make_system_event(NotificationCode.PARSE_ERROR)]

    def _decode_message(self, content: bytes) -> dict | None:
        if content == b"<Z/>":
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON: {content}")
            raise

    def _dispatch_message(self, message: dict) -> list[MJAIEvent]:
        tag = message.get("tag")
        if "owari" in message:
            events = self.handlers[tag](message) if tag in {"AGARI", "RYUUKYOKU"} else []
            if self._pending_hora_end:
                events.append(self.make_end_kyoku())
                self._pending_hora_end = False
            events.extend(self._convert_end_game())
            return events
        if not tag:
            return []

        if tag == "INIT" and self._pending_hora_end:
            self._pending_hora_end = False
            return [self.make_end_kyoku(), *self._convert_start_kyoku(message)]

        return self._dispatch_tag_message(tag, message)

    def _dispatch_tag_message(self, tag: str, message: dict) -> list[MJAIEvent]:
        if handler := self.handlers.get(tag):
            return handler(message)

        match tag:
            case _ if re.match(r"^[TUVW]\d*$", tag):
                return self._convert_tsumo(message)
            case _ if re.match(r"^[DEFGdefg]\d*$", tag):
                return self._convert_dahai(message)
            case _:
                return []

    def _dispatch_reach(self, message: dict) -> list[MJAIEvent]:
        step = message.get("step")
        if step == "1":
            return self._convert_reach(message)
        if step == "2":
            return self._convert_reach_accepted(message)
        return []

    def _dispatch_n(self, message: dict) -> list[MJAIEvent]:
        if "m" in message:
            return self._convert_meld(message)
        return []

    def _convert_helo(self, _message: dict) -> list[MJAIEvent]:
        if self.state.game_active:
            logger.info("[Tenhou] Session re-initialized via HELO while game active. Sending end_game.")
            msgs = [self.make_end_game()]
            self.reset()
            return msgs
        return []

    def _convert_rejoin(self, _message: dict) -> list[MJAIEvent]:
        return []

    def _convert_un(self, message: dict) -> list[MJAIEvent]:
        # 稳健的三麻检测：统计 n0~n3 中非空昵称数量。
        # 三麻模式下四个位置会有一个为空。
        names = [message.get(f"n{i}", "") for i in range(4)]
        player_count = len([n for n in names if n])
        self.state.is_3p = player_count == MahjongConstants.SEATS_3P
        logger.debug(f"[Tenhou] UN tag: player_count={player_count}, is_3p={self.state.is_3p}")
        return []

    def _convert_start_game(self, message: dict) -> list[MJAIEvent]:
        self.state.game_active = True
        self.game_ended = False
        # TAIKYOKU 的 oya 是庄家绝对座位号。
        # 兼容旧逻辑：通过 (4 - oya) % 4 将庄家映射到 actor 0。
        self.state.seat = (MahjongConstants.SEATS_4P - int(message["oya"])) % MahjongConstants.SEATS_4P
        msg = (
            f"[Tenhou] Game started. {'3P' if self.state.is_3p else '4P'} mode. "
            f"Assigned internal seat {self.state.seat}"
        )
        logger.info(msg)
        return [self.make_start_game(self.state.seat, is_3p=self.state.is_3p)]

    def _convert_start_kyoku(self, message: dict) -> list[MJAIEvent]:
        self.state.hand = [int(s) for s in message["hai"].split(",")]
        self.state.in_riichi = False
        self.state.live_wall = 70
        self.state.melds.clear()
        self.state.wait.clear()
        self.state.last_kawa_tile = "?"
        self.state.is_tsumo = False
        self.state.is_new_round = True
        self._pending_draw_actors.clear()
        self._pending_draw_tiles.clear()

        bakaze_names = ["E", "S", "W", "N"]
        oya = self.rel_to_abs(int(message["oya"]))
        seed = [int(s) for s in message["seed"].split(",")]
        bakaze = bakaze_names[seed[0] // 4]
        kyoku = seed[0] % 4 + 1
        honba = seed[1]
        kyotaku = seed[2]
        dora_marker = tenhou_to_mjai_one(seed[5])

        # 处理分数：始终补全到 4 人以兼容 MJAI 引擎
        raw_scores = [int(s) * 100 for s in message["ten"].split(",")]
        scores = [0] * MahjongConstants.SEATS_4P
        for i in range(min(len(raw_scores), MahjongConstants.SEATS_4P)):
            scores[self.rel_to_abs(i)] = raw_scores[i]

        tehais = [["?" for _ in range(MahjongConstants.TEHAI_SIZE)] for _ in range(MahjongConstants.SEATS_4P)]
        tehais[self.state.seat] = tenhou_to_mjai(self.state.hand)

        return [
            self.make_start_kyoku(
                bakaze=bakaze,
                kyoku=kyoku,
                honba=honba,
                kyotaku=kyotaku,
                oya=oya,
                dora_marker=dora_marker,
                scores=scores,
                tehais=tehais,
            )
        ]

    def _convert_tsumo(self, message: dict) -> list[MJAIEvent]:
        self.state.live_wall -= 1

        tag = message["tag"]
        rel_seat = ord(tag[0]) - ord("T")

        # 三麻模式下 'W' 不是有效玩家位
        if self.state.is_3p and rel_seat >= MahjongConstants.SEATS_3P:
            return []

        actor = self.rel_to_abs(rel_seat)
        mjai_messages: list[MJAIEvent] = [self.make_tsumo(actor, "?")]

        index_str = tag[1:]
        raw_index = int(index_str) if index_str else int(message["p"]) if "p" in message else None
        self._pending_draw_actors.add(actor)
        if raw_index is not None:
            self._pending_draw_tiles[actor] = raw_index
        else:
            self._pending_draw_tiles.pop(actor, None)

        if actor == self.state.seat:
            if raw_index is None:
                logger.warning(f"[Tenhou] Self tsumo tag '{tag}' missing index")
                return mjai_messages

            mjai_messages[0] = replace(mjai_messages[0], pai=tenhou_to_mjai_one(raw_index))
            self.state.hand.append(raw_index)
            self.state.is_tsumo = True
            return mjai_messages
        return mjai_messages

    def _convert_dahai(self, message: dict) -> list[MJAIEvent]:
        tag = message["tag"]
        rel_seat = ord(str.upper(tag[0])) - ord("D")
        actor = self.rel_to_abs(rel_seat)

        if "p" in message:
            index = int(message["p"])
        elif len(tag) > 1:
            index = int(tag[1:])
        else:
            # 兜底：索引缺失时按摸切处理（JSON 协议正常不会发生）
            index = self.state.hand[-1] if actor == self.state.seat and self.state.hand else -1

        pai = tenhou_to_mjai_one(index) if index != -1 else "?"

        had_pending_draw = actor in self._pending_draw_actors
        if actor == self.state.seat:
            tsumogiri = had_pending_draw and self._pending_draw_tiles.get(actor) == index
        else:
            # Uppercase is Tenhou's live hint for an opponent tsumogiri, but it
            # is only legal when that actor actually drew immediately before.
            # A discard after chi/pon can never be tsumogiri.
            tsumogiri = had_pending_draw and str.isupper(tag[0])
        self.state.last_kawa_tile = pai

        mjai_messages: list[MJAIEvent] = [self.make_dahai(actor, pai, tsumogiri)]

        self.state.is_tsumo = False
        self._clear_pending_draw(actor)
        if actor == self.state.seat and index != -1:
            if index in self.state.hand:
                self.state.hand.remove(index)
            else:
                logger.warning(f"[Tenhou] Tile {index} ({pai}) not in hand during Dahai. State sync issue.")

        return mjai_messages

    def _convert_meld(self, message: dict) -> list[MJAIEvent]:
        actor = self.rel_to_abs(int(message["who"]))
        self._clear_pending_draw(actor)
        m = int(message["m"])
        if (m & TenhouConstants.BIT_MASK_M) == TenhouConstants.BIT_NUKIDORA:
            return self._handle_nukidora(actor)

        meld = Meld.parse_meld(m)
        num_players = MahjongConstants.SEATS_4P
        mjai_messages: list[MJAIEvent] = []

        match meld.meld_type:
            case MeldType.CHI:
                target = (actor - 1) % num_players
                mjai_messages.append(self.make_chi(actor, target, meld.pai, meld.consumed))
            case MeldType.PON:
                target = (actor + meld.target) % num_players
                mjai_messages.append(self.make_pon(actor, target, meld.pai, meld.consumed))
            case MeldType.DAIMINKAN:
                target = (actor + meld.target) % num_players
                mjai_messages.append(self.make_daiminkan(actor, target, meld.pai, meld.consumed))
            case MeldType.KAKAN:
                mjai_messages.append(self.make_kakan(actor, meld.pai, meld.consumed))
            case MeldType.ANKAN:
                mjai_messages.append(self.make_ankan(actor, meld.consumed))
            case _:
                logger.warning(f"Unknown meld type: {meld.meld_type}")
                return []

        if actor == self.state.seat:
            self._update_hand_for_meld(meld)
            self.state.melds.append(meld)

        return mjai_messages

    def _clear_pending_draw(self, actor: int) -> None:
        self._pending_draw_actors.discard(actor)
        self._pending_draw_tiles.pop(actor, None)

    def _handle_nukidora(self, actor: int) -> list[MJAIEvent]:
        mjai_messages: list[MJAIEvent] = [self.make_nukidora(actor)]
        if actor == self.state.seat:
            found = False
            for i in self.state.hand:
                if i // TenhouConstants.TILES_PER_TYPE == TenhouConstants.PEI_INDEX:
                    self.state.hand.remove(i)
                    found = True
                    break
            if not found:
                logger.warning("[Tenhou] Nukidora (Pei) not found in hand during conversion")
        return mjai_messages

    def _update_hand_for_meld(self, meld: Meld):
        for i in meld.exposed:
            if i in self.state.hand:
                self.state.hand.remove(i)
            else:
                logger.warning(f"[Tenhou] Tile {i} in meld {meld.meld_type} not found in hand")

    def _convert_reach(self, message: dict) -> list[MJAIEvent]:
        actor = self.rel_to_abs(int(message["who"]))
        return [self.make_reach(actor)]

    def _convert_reach_accepted(self, message: dict) -> list[MJAIEvent]:
        actor = self.rel_to_abs(int(message["who"]))
        if actor == self.state.seat:
            self.state.in_riichi = True
            waiting_indices = isrh(to_34_array(self.state.hand))
            self.state.wait = [MahjongConstants.BASE_TILES[i] for i in sorted(waiting_indices)]

        deltas = [0] * 4
        deltas[actor] = -1000
        scores = [0] * 4
        raw_scores = [int(s) * 100 for s in message["ten"].split(",")]

        for i in range(min(len(raw_scores), 4)):
            scores[self.rel_to_abs(i)] = raw_scores[i]

        return [self.make_reach_accepted(actor, deltas, scores)]

    def _convert_dora(self, message: dict) -> list[MJAIEvent]:
        hai = int(message["hai"])
        dora_marker = tenhou_to_mjai_one(hai)
        return [self.make_dora(dora_marker)]

    def _convert_hora(self, message: dict) -> list[MJAIEvent]:
        self.state.is_new_round = False
        actor = self.rel_to_abs(int(message["who"]))
        target = self.rel_to_abs(int(message["fromWho"]))
        pai = tenhou_to_mjai_one(int(message["machi"]))

        raw_sc = [int(value) for value in message["sc"].split(",")]
        raw_scores = [(score + delta) * 100 for score, delta in zip(raw_sc[0::2], raw_sc[1::2], strict=False)]
        raw_deltas = [delta * 100 for delta in raw_sc[1::2]]
        scores = [0] * 4
        deltas = [0] * 4
        for i in range(min(len(raw_scores), 4)):
            scores[self.rel_to_abs(i)] = raw_scores[i]
            deltas[self.rel_to_abs(i)] = raw_deltas[i]

        self._pending_hora_end = True
        return [HoraEvent(actor=actor, target=target, pai=pai, scores=scores, deltas=deltas)]

    def _convert_ryukyoku(self, message: dict) -> list[MJAIEvent]:
        raw_scores = parse_sc_tag(message)
        scores = [0] * 4
        for i in range(min(len(raw_scores), 4)):
            scores[self.rel_to_abs(i)] = raw_scores[i]
        ryukyoku_event = RyukyokuEvent(scores=scores)
        return [ryukyoku_event, self.make_end_kyoku()]

    def _convert_end_game(self) -> list[MJAIEvent]:
        self.state.game_active = False
        self.game_ended = True
        return [self.make_end_game()]

    def rel_to_abs(self, rel: int) -> int:
        return (rel + self.state.seat) % MahjongConstants.SEATS_4P
