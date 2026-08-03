import json
import time
from typing import Any

from akagi_ng.mjai_bot.engine.factory import load_bot_and_engine
from akagi_ng.mjai_bot.logger import logger
from akagi_ng.mjai_bot.lookahead import LookaheadBot
from akagi_ng.mjai_bot.online_inference import OnlineInferenceError, OnlineInferenceExecutor
from akagi_ng.mjai_bot.ot3 import (
    OT3_END_TO_END_DEADLINE_SECONDS,
    OT3Client,
    apply_player_actor,
    build_ot3_events,
    is_local_decision,
)
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.mjai_bot.utils import meta_to_recommend, serialize_mjai_event
from akagi_ng.schema.constants import MahjongConstants
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.protocols import EngineProtocol, MJAIBotProtocol
from akagi_ng.schema.types import (
    EndGameEvent,
    MJAIEvent,
    MJAIEventBase,
    MJAIMetadata,
    MJAIResponse,
    ReachEvent,
    StartGameEvent,
    StartKyokuEvent,
)
from akagi_ng.settings import local_settings


class MortalBot:
    """
    MJAI Bot 的封装类,负责处理事件并返回推荐动作。
    """

    def __init__(
        self,
        status: BotStatusContext,
        engine: EngineProtocol | None = None,
        is_3p: bool = False,
        *,
        flya_probe: bool = False,
        online_executor: OnlineInferenceExecutor | None = None,
    ):
        self.status = status
        self.engine = engine
        self.is_3p = is_3p
        self.flya_probe = flya_probe
        self.player_id: int | None = None
        self.history: list[MJAIEvent] = []
        self.bot: MJAIBotProtocol | None = None
        self.game_start_event: StartGameEvent | None = None
        self.ot3_client: OT3Client | None = None
        self._ot3_signature: tuple[str, str, str, str] | None = None
        self._owns_online_executor = online_executor is None
        self.online_executor = online_executor or OnlineInferenceExecutor()

        self.logger = logger

    def react(self, event: MJAIEvent) -> MJAIResponse | None:
        """MortalBot 对外核心接口，流水线处理事件"""
        try:
            # 1. 预处理：生命周期管理与历史记录
            self._pre_react(event)

            # 2. 决策：调用模型/引擎
            response: MJAIResponse | None = self._think(event)
            if not response:
                return None
            response = self._try_ot3(event, response)

            # 3. 增强：注入元数据与执行前瞻逻辑
            meta: MJAIMetadata | None = response.get("meta")
            if not meta:
                return None

            self._post_react(meta)

            # 4. 智能抑制：如果推荐内容仅包含唯一的“跳过”，则移除 meta 以隐藏推荐
            if self._should_suppress_meta(meta):
                response.pop("meta", None)

            return response

        except Exception as e:
            self.logger.exception(f"MortalBot runtime error in select_action: {e}")
            self.status.set_flag(NotificationCode.BOT_RUNTIME_ERROR)
            return None

    def _pre_react(self, event: MJAIEvent) -> None:
        """维护历史、处理生命周期事件。"""
        match event:
            case StartGameEvent():
                self._handle_start_game(event)
            case StartKyokuEvent():
                self.history = []
            case EndGameEvent():
                self._handle_end_game()

        # 维护历史
        self.history.append(event)

    def _think(self, event: MJAIEvent) -> MJAIResponse | None:
        """调用引擎/模型获取决策动作"""
        if not self.bot:
            return None

        is_sync = False
        match event:
            case ReachEvent(actor=actor) if actor == self.player_id:
                # 玩家自己立直时，接下来必须且只能切出立直宣告牌。
                # 引擎无需再做推理（立直前瞻已涵盖此信息），直接转为同步状态以节省算力并抑制 UI 闪烁。
                is_sync = True
                # 检查 MJAIEvent 中的 sync 字段
            case MJAIEventBase(sync=is_sync):
                pass

        try:
            # MJAI 协议底层 C++ Bot (mjai-python) 接受并返回 JSON 字符串
            event_json = serialize_mjai_event(event)
            # can_act=False 时同步快进，仅更新状态，不触发决策推理。
            res = self.bot.react(event_json, can_act=False) if is_sync else self.bot.react(event_json)
            if not res:
                return None
            try:
                return json.loads(res)
            except json.JSONDecodeError:
                self.logger.error(f"MortalBot: engine returned invalid JSON: {res}")
                self.status.set_flag(NotificationCode.JSON_DECODE_ERROR)
                return None
        except Exception:
            self.logger.exception("MortalBot engine error")
            self.status.set_flag(NotificationCode.BOT_RUNTIME_ERROR)
            return None

    def _should_suppress_meta(self, meta: MJAIMetadata) -> bool:
        """
        判断是否应该抑制元数据。
        如果唯一合法动作是“跳过”，则返回 True。
        这通常发生在三麻中由于规则禁止“吃”而导致模型返回唯一的“none”动作。
        """
        mask_bits = meta.get("mask_bits", 0)
        none_idx = 43 if self.is_3p else 45
        return mask_bits == (1 << none_idx)

    def _post_react(self, meta: MJAIMetadata):
        """元数据增强阶段"""
        # 1. 注入同步元数据
        meta.update(self.status.metadata)

        # 2. 立直前瞻逻辑
        self._handle_riichi_lookahead(meta)

    def _handle_start_game(self, e: StartGameEvent):
        """处理游戏开始事件，初始化模型和引擎"""
        self.player_id = e.id
        self.bot, self.engine = load_bot_and_engine(
            self.status,
            self.player_id,
            self.is_3p,
            flya_probe=self.flya_probe,
        )
        self.history = []
        self.game_start_event = e
        self.ot3_client = None
        self._ot3_signature = None
        if self._refresh_ot3_session():
            self.status.set_metadata(NotificationCode.ENGINE_TYPE, "akagiot")

        # 检测加载的模型类型并设置通知
        if self.engine:
            engine_meta = self.status.metadata
            engine_type = engine_meta.get(NotificationCode.ENGINE_TYPE, "unknown")

            flya_configured = bool(
                self.flya_probe
                and local_settings.ot.online
                and local_settings.ot.provider == "flya_test_api"
                and local_settings.ot.flya_server.strip()
                and local_settings.ot.flya_api_key.strip()
                and engine_type == "mortal"
            )
            match "flya" if flya_configured else "akagiot" if self.ot3_client else engine_type:
                case "flya":
                    self.status.set_flag(NotificationCode.MODEL_LOADED_ONLINE)
                case "akagiot":
                    self.status.set_flag(NotificationCode.MODEL_LOADED_ONLINE)
                case "mortal":
                    self.status.set_flag(NotificationCode.MODEL_LOADED_LOCAL)
                case _:
                    self.logger.warning(f"Unknown engine type: {engine_type}")

    def _handle_end_game(self):
        """处理游戏结束事件，清理状态"""
        self.player_id = None
        self.bot = None
        self.engine = None
        if self.ot3_client:
            self.ot3_client.close()
        self.ot3_client = None
        self._ot3_signature = None
        self.game_start_event = None

    def _ot3_enabled(self) -> bool:
        ot = local_settings.ot
        return (
            ot.online
            and ot.provider == "akagi_ot3"
            and getattr(ot, "protocol", "v3") == "v3"
            and bool(ot.server.strip())
            and bool(ot.api_key.strip())
        )

    def _refresh_ot3_session(self) -> tuple[OT3Client, str] | None:
        """Apply online config changes on the next decision without restarting."""
        if not self._ot3_enabled():
            if self.ot3_client or self._ot3_signature:
                self.online_executor.next_generation()
            if self.ot3_client:
                self.ot3_client.close()
            self.ot3_client = None
            self._ot3_signature = None
            return None

        ot = local_settings.ot
        model = ot.model_for(self.is_3p).strip()
        proxy = ot.effective_proxy()
        signature = (ot.server.strip().rstrip("/"), ot.api_key.strip(), model, proxy)
        if signature != self._ot3_signature:
            self.online_executor.next_generation()
            if self.ot3_client:
                self.ot3_client.close()
            self.ot3_client = OT3Client(ot.server, ot.api_key, proxy)
            self._ot3_signature = signature
        return (self.ot3_client, model) if self.ot3_client else None

    def _try_ot3(self, event: MJAIEvent, local_response: MJAIResponse) -> MJAIResponse:
        """Use OT3 at real decision points and preserve the local response as fallback."""
        session = self._refresh_ot3_session()
        if (
            not session
            or self.player_id is None
            or self.game_start_event is None
            or getattr(event, "sync", False)
            or not is_local_decision(local_response, is_3p=self.is_3p)
        ):
            return local_response

        try:
            client, model = session
            deadline = time.monotonic() + OT3_END_TO_END_DEADLINE_SECONDS
            events = build_ot3_events(
                self.game_start_event,
                self.history,
                player_id=self.player_id,
                is_3p=self.is_3p,
            )
            result = self._run_ot3(
                client,
                model=model,
                events=events,
                deadline=deadline,
            )
            reaction = result.get("reaction")
            if not isinstance(reaction, dict):
                raise RuntimeError("OT3 returned no reaction at a local decision point")

            remote_response = apply_player_actor(reaction, self.player_id)
            reach_candidates: list[dict[str, str | float]] = []
            partial_fallback = False
            if remote_response.get("type") == "reach" and not remote_response.get("pai"):
                reach_candidates, discard, partial_fallback = self._resolve_ot3_reach(
                    client,
                    model,
                    events,
                    deadline,
                )
                if not discard:
                    raise RuntimeError("OT3 reach reaction could not be resolved to a discard")
                remote_response["pai"] = discard

            local_meta = local_response.get("meta") or {}
            remote_response["meta"] = {
                key: local_meta[key]
                for key in ("q_values", "mask_bits", "shanten", "waits", "at_furiten")
                if key in local_meta
            }
            remote_response["meta"].update(
                {
                    "ot3_candidates": result["candidates"],
                    "ot3_reaction": {key: value for key, value in remote_response.items() if key != "meta"},
                    "ot3_model": result.get("model") or "OT3",
                    NotificationCode.ENGINE_TYPE: "akagiot",
                    NotificationCode.FALLBACK_USED: partial_fallback,
                    NotificationCode.RECONNECTING: client.circuit_open,
                }
            )
            if reach_candidates:
                remote_response["meta"]["ot3_reach_candidates"] = reach_candidates
            self.status.set_metadata(NotificationCode.ENGINE_TYPE, "akagiot")
            self.status.set_metadata(NotificationCode.FALLBACK_USED, partial_fallback)
            self.status.set_metadata(NotificationCode.RECONNECTING, client.circuit_open)
            return remote_response
        except Exception as exc:
            self.logger.exception("OT3 inference failed; using the local Mortal decision")
            if isinstance(exc, OnlineInferenceError):
                self.status.set_flag(NotificationCode.RECONNECTING)
                self.status.set_metadata(NotificationCode.RECONNECTING, True)
            self.status.set_metadata(NotificationCode.ENGINE_TYPE, "akagiot")
            self.status.set_metadata(NotificationCode.FALLBACK_USED, True)
            if meta := local_response.get("meta"):
                meta.update(self.status.metadata)
            return local_response

    def _resolve_ot3_reach(
        self,
        client: OT3Client,
        model: str,
        events: list[dict[str, object]],
        deadline: float,
    ) -> tuple[list[dict[str, str | float]], str | None, bool]:
        """Resolve OT3's bare reach with one follow-up call, then local fallback."""
        if self.player_id is None:
            return [], None, True

        followup_events = [*events, {"type": "reach", "actor": self.player_id}]
        try:
            followup = self._run_ot3(
                client,
                model=model,
                events=followup_events,
                deadline=deadline,
            )
            reaction = followup.get("reaction")
            if isinstance(reaction, dict) and reaction.get("type") == "dahai" and isinstance(reaction.get("pai"), str):
                return followup["candidates"], reaction["pai"], False
            self.logger.warning("OT3 reach follow-up returned no valid discard; using local lookahead")
        except Exception:
            self.logger.exception("OT3 reach follow-up failed; using local lookahead")

        local_meta = self._run_riichi_lookahead()
        if not local_meta:
            return [], None, True
        local_candidates = [
            {"action": f"dahai:{action}", "prob": float(confidence)}
            for action, confidence in meta_to_recommend(
                local_meta,
                is_3p=self.is_3p,
                temperature=local_settings.model_config.temperature,
            )
            if action in MahjongConstants.BASE_TILES
        ]
        discard = str(local_candidates[0]["action"]).removeprefix("dahai:") if local_candidates else None
        return local_candidates, discard, True

    def _run_ot3(
        self,
        client: OT3Client,
        *,
        model: str,
        events: list[dict[str, object]],
        deadline: float,
    ) -> dict[str, Any]:
        """Execute one OT3 request off-thread and merge only accepted status."""
        if self.player_id is None:
            raise RuntimeError("OT3 request requires an active player")
        player_id = self.player_id
        call_status = BotStatusContext()
        try:
            return self.online_executor.run(
                lambda: client.react(
                    player_id=player_id,
                    events=events,
                    status=call_status,
                    model=model,
                ),
                deadline=deadline,
            )
        finally:
            self.status.update_flags(call_status.flags)
            self.status.update_metadata(call_status.metadata)

    def close(self) -> None:
        if self.ot3_client:
            self.ot3_client.close()
            self.ot3_client = None
        if self._owns_online_executor:
            self.online_executor.close()

    def _handle_riichi_lookahead(self, meta: MJAIMetadata):
        """
        处理立直前瞻逻辑
        """
        if "q_values" not in meta or "mask_bits" not in meta:
            return

        recommendations = meta_to_recommend(meta, is_3p=self.is_3p, temperature=local_settings.model_config.temperature)
        top_3_actions = [rec[0] for rec in recommendations[:3]]
        flya_enabled = local_settings.ot.online and local_settings.ot.provider == "flya_test_api"
        eligible_actions = [action for action, _confidence in recommendations] if flya_enabled else top_3_actions

        if "reach" not in eligible_actions:
            return

        self.logger.info(f"Riichi Lookahead: Reach is in Top 3 ({top_3_actions}). Starting simulation.")
        lookahead_meta = self._run_riichi_lookahead()
        if lookahead_meta:
            meta["riichi_lookahead"] = lookahead_meta
        else:
            self.status.set_flag(NotificationCode.RIICHI_SIM_FAILED)

    def _run_riichi_lookahead(self) -> MJAIMetadata | None:
        """
        运行立直前瞻模拟。
        """
        try:
            if not self.engine or self.player_id is None:
                return None

            self.logger.debug("Riichi Lookahead: Starting simulation (using LookaheadBot).")
            sim_status = BotStatusContext()
            sim_engine = self.engine.fork(status=sim_status)
            lookahead_bot = LookaheadBot(sim_engine, self.player_id, is_3p=self.is_3p)

            reach_event = ReachEvent(actor=self.player_id)
            sim_meta: MJAIMetadata | None = lookahead_bot.simulate_reach(
                self.history,
                reach_event,
                game_start_event=self.game_start_event,
            )

            if not sim_meta:
                self.logger.warning("Riichi Lookahead: Simulation returned no metadata.")
                return None

            sim_recs = meta_to_recommend(
                sim_meta, is_3p=self.is_3p, temperature=local_settings.model_config.temperature
            )
            all_candidates = ", ".join([f"{action}({conf:.3f})" for action, conf in sim_recs])
            self.logger.info(f"Riichi Lookahead: Simulation success. Candidates: {all_candidates}")
            return sim_meta

        except Exception:
            self.logger.exception("Riichi Lookahead failed")
            return None
