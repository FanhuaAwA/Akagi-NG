from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from akagi_ng.bridge.tenhou.utils.converter import tenhou_to_mjai


class MeldType(StrEnum):
    CHI = "chi"
    PON = "pon"
    KAKAN = "kakan"
    DAIMINKAN = "daiminkan"
    ANKAN = "ankan"


@dataclass(frozen=True, slots=True)
class Meld:
    target: int
    meld_type: MeldType
    tiles: list[int]
    unused: int | None = None
    r: int | None = None

    @property
    def pai(self) -> str:
        return tenhou_to_mjai([self.tiles[0]])[0]

    @property
    def consumed(self) -> list[str]:
        if self.meld_type == MeldType.ANKAN:
            return tenhou_to_mjai(self.tiles)
        return tenhou_to_mjai(self.tiles[1:])

    @property
    def exposed(self) -> list[int]:
        if self.meld_type == MeldType.ANKAN:
            return self.tiles
        if self.meld_type == MeldType.KAKAN:
            return self.tiles[0:1]
        return self.tiles[1:]

    @classmethod
    def parse_meld(cls, m: int) -> Self:
        if m & (1 << 2):
            # チー
            return cls.parse_chi(m)
        if m & (1 << 3):
            # ポン
            return cls.parse_pon(m)
        if m & (1 << 4):
            # 加槓
            return cls.parse_kakan(m)
        # 大明槓, 暗槓
        return cls.parse_daiminkan_ankan(m)

    @classmethod
    def parse_chi(cls, m: int) -> Self:
        t = m >> 10
        r = t % 3
        t //= 3
        t = t // 7 * 9 + t % 7
        t *= 4
        h = [
            t + 4 * 0 + ((m >> 3) & 0x3),
            t + 4 * 1 + ((m >> 5) & 0x3),
            t + 4 * 2 + ((m >> 7) & 0x3),
        ]
        h[0], h[r] = h[r], h[0]
        return cls(m & 3, MeldType.CHI, h, r=r)

    @classmethod
    def parse_pon(cls, m: int) -> Self:
        unused_index = (m >> 5) & 0x3
        t = m >> 9
        called_index = t % 3
        t = t // 3 * 4
        h = [t, t + 1, t + 2, t + 3]
        # Tenhou first swaps the unused fourth copy to the end, then indexes
        # the called tile inside the remaining three-tile combination.
        h[3], h[unused_index] = h[unused_index], h[3]
        combination = h[:3]
        called = combination[called_index]
        consumed = [tile for i, tile in enumerate(combination) if i != called_index]
        return cls(m & 3, MeldType.PON, [called, *consumed], unused=h[3])

    @classmethod
    def parse_kakan(cls, m: int) -> Self:
        added = (m >> 5) & 0x3
        t = m >> 9
        r = t % 3
        t = t // 3 * 4
        h = [t, t + 1, t + 2, t + 3]
        added = h.pop(added)
        h[0], h[r] = h[r], h[0]
        h = [added, *h]
        return cls(m & 3, MeldType.KAKAN, h)

    @classmethod
    def parse_daiminkan_ankan(cls, m: int) -> Self:
        target = m & 3
        hai0 = m >> 8
        t = hai0 // 4 * 4
        r = hai0 % 4
        h = [t, t + 1, t + 2, t + 3]
        h[0], h[r] = h[r], h[0]

        if target == 0:
            return cls(target, MeldType.ANKAN, h)
        return cls(target, MeldType.DAIMINKAN, h)


def parse_sc_tag(message: dict[str, str]) -> list[int]:
    sc = [int(s) for s in message["sc"].split(",")]
    before = sc[0::2]
    delta = sc[1::2]
    return [(x + y) * 100 for x, y in zip(before, delta, strict=False)]


def parse_owari_tag(message: dict[str, str]) -> list[int]:
    sc = [int(s) for s in message["owari"].split(",")[0::2]]
    return [x * 100 for x in sc]
