from akagi_ng.schema.types import (
    AkagiEvent,
    AnkanEvent,
    ChiEvent,
    DahaiEvent,
    DaiminkanEvent,
    DoraEvent,
    EndGameEvent,
    EndKyokuEvent,
    KakanEvent,
    NukidoraEvent,
    PonEvent,
    ReachAcceptedEvent,
    ReachEvent,
    StartGameEvent,
    StartKyokuEvent,
    SystemEvent,
    SystemEventCode,
    TsumoEvent,
)


class BaseBridge:
    """
    Bridge 基类。

    提供所有平台通用的状态属性和 MJAI 消息构建器方法。
    各平台继承此类并实现 `parse()` 方法。
    """

    def __init__(self):
        self.seat = 0

    def reset(self):
        """
        重置 Bridge 状态。
        """
        pass

    def parse(self, content: bytes) -> list[AkagiEvent]:
        """
        解析平台消息并返回 MJAI 指令列表。

        Args:
            content: 平台原始消息内容

        Returns:
            list[AkagiEvent]: MJAI 指令
        """
        raise NotImplementedError

    # ===== MJAI 消息构建器 =====

    def _resolve_sync(self, sync: bool | None = None) -> bool:
        """解析事件同步标志。

        优先级:
        1. 显式 sync 参数
        2. Bridge 运行时状态 self.syncing（如果存在）
        3. 默认 False
        """
        if sync is not None:
            return sync
        return bool(getattr(self, "syncing", False))

    def make_start_game(self, seat: int, is_3p: bool, *, sync: bool | None = None) -> StartGameEvent:
        """构建 start_game（游戏开始）消息"""
        return StartGameEvent(id=seat, is_3p=is_3p, sync=self._resolve_sync(sync))

    def make_start_kyoku(  # noqa
        self,
        bakaze: str,
        kyoku: int,
        honba: int,
        kyotaku: int,
        oya: int,
        dora_marker: str,
        scores: list[int],
        tehais: list[list[str]],
        *,
        sync: bool | None = None,
    ) -> StartKyokuEvent:
        """构建 start_kyoku（本局开始）消息"""
        return StartKyokuEvent(
            bakaze=bakaze,
            dora_marker=dora_marker,
            kyoku=kyoku,
            honba=honba,
            kyotaku=kyotaku,
            oya=oya,
            scores=scores,
            tehais=tehais,
            sync=self._resolve_sync(sync),
        )

    def make_tsumo(self, actor: int, pai: str, *, sync: bool | None = None) -> TsumoEvent:
        """构建 tsumo（摸牌）消息"""
        return TsumoEvent(actor=actor, pai=pai, sync=self._resolve_sync(sync))

    def make_dahai(self, actor: int, pai: str, tsumogiri: bool, *, sync: bool | None = None) -> DahaiEvent:
        """构建 dahai（弃牌）消息"""
        return DahaiEvent(actor=actor, pai=pai, tsumogiri=tsumogiri, sync=self._resolve_sync(sync))

    def make_chi(self, actor: int, target: int, pai: str, consumed: list[str], *, sync: bool | None = None) -> ChiEvent:
        """构建 chi（吃）消息"""
        return ChiEvent(actor=actor, target=target, pai=pai, consumed=consumed, sync=self._resolve_sync(sync))

    def make_pon(self, actor: int, target: int, pai: str, consumed: list[str], *, sync: bool | None = None) -> PonEvent:
        """构建 pon（碰）消息"""
        return PonEvent(actor=actor, target=target, pai=pai, consumed=consumed, sync=self._resolve_sync(sync))

    def make_daiminkan(
        self, actor: int, target: int, pai: str, consumed: list[str], *, sync: bool | None = None
    ) -> DaiminkanEvent:
        """构建 daiminkan（大明杠）消息"""
        return DaiminkanEvent(actor=actor, target=target, pai=pai, consumed=consumed, sync=self._resolve_sync(sync))

    def make_ankan(self, actor: int, consumed: list[str], *, sync: bool | None = None) -> AnkanEvent:
        """构建 ankan（暗杠）消息"""
        return AnkanEvent(actor=actor, consumed=consumed, sync=self._resolve_sync(sync))

    def make_kakan(self, actor: int, pai: str, consumed: list[str], *, sync: bool | None = None) -> KakanEvent:
        """构建 kakan（加杠）消息"""
        return KakanEvent(actor=actor, pai=pai, consumed=consumed, sync=self._resolve_sync(sync))

    def make_reach(self, actor: int, *, sync: bool | None = None) -> ReachEvent:
        """构建 reach（立直宣言）消息"""
        return ReachEvent(actor=actor, sync=self._resolve_sync(sync))

    def make_reach_accepted(
        self, actor: int, deltas: list[int] | None = None, scores: list[int] | None = None, *, sync: bool | None = None
    ) -> ReachAcceptedEvent:
        """构建 reach_accepted（立直确认）消息"""
        return ReachAcceptedEvent(actor=actor, deltas=deltas, scores=scores, sync=self._resolve_sync(sync))

    def make_dora(self, dora_marker: str, *, sync: bool | None = None) -> DoraEvent:
        """构建 dora（新宝牌）消息"""
        return DoraEvent(dora_marker=dora_marker, sync=self._resolve_sync(sync))

    def make_nukidora(self, actor: int, *, sync: bool | None = None) -> NukidoraEvent:
        """构建 nukidora（拔北）消息"""
        return NukidoraEvent(actor=actor, pai="N", sync=self._resolve_sync(sync))

    def make_end_kyoku(self, *, sync: bool | None = None) -> EndKyokuEvent:
        """构建 end_kyoku（本局结束）消息"""
        return EndKyokuEvent(sync=self._resolve_sync(sync))

    def make_end_game(self, *, sync: bool | None = None) -> EndGameEvent:
        """构建 end_game（游戏结束）消息"""
        return EndGameEvent(sync=self._resolve_sync(sync))

    def make_system_event(self, code: SystemEventCode) -> SystemEvent:
        """构建 system_event（系统消息）"""
        return SystemEvent(code=code)
