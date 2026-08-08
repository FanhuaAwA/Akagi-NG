"""FlyA Test API decision protocol and MJAI adapters."""

from __future__ import annotations

import json
import math
import time
from dataclasses import fields, replace
from http import HTTPStatus
from typing import Any
from uuid import uuid4

import requests

from akagi_ng.mjai_bot.flya_service import (
    FLYA_TEST_TIMEOUT_SECONDS,
    _log_flya_http_exception,
    _log_flya_http_request,
    _log_flya_http_response,
    _normalize_base_url,
    _resolve_ca_file,
    _response_error_code,
    _safe_response_error_code,
)
from akagi_ng.mjai_bot.logger import logger
from akagi_ng.mjai_bot.online_inference import (
    OnlineInferenceCancelled,
    OnlineInferenceError,
    OnlineInferenceExecutor,
)
from akagi_ng.mjai_bot.ot3 import is_local_decision
from akagi_ng.mjai_bot.ot3_proxy import configure_ot3_session
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import (
    EndGameEvent,
    MJAIEvent,
    MJAIEventBase,
    MJAIResponse,
    StartGameEvent,
    StartKyokuEvent,
)
from akagi_ng.settings import local_settings

FLYA_DECISION_PATH = "/decision"
FLYA_DECISION_TIMEOUT_SECONDS = (3.0, 10.0)
FLYA_END_TO_END_DEADLINE_SECONDS = 8.0
FLYA_BREAKER_BASE_SECONDS = 5.0
FLYA_BREAKER_MAX_SECONDS = 120.0
FLYA_DISPLAY_ACTION_LIMIT = 3
THREE_PLAYER_SEATS = 3
FOUR_PLAYER_SEATS = 4
HTTP_SERVER_ERROR_LIMIT = 600

_EVENT_FIELDS = {
    "start_game": ("names", "seed"),
    "start_kyoku": ("bakaze", "dora_marker", "honba", "kyoku", "kyotaku", "oya", "scores", "tehais"),
    "tsumo": ("actor", "pai"),
    "dahai": ("actor", "pai", "tsumogiri"),
    "chi": ("actor", "consumed", "pai", "target"),
    "pon": ("actor", "consumed", "pai", "target"),
    "daiminkan": ("actor", "consumed", "pai", "target"),
    "ankan": ("actor", "consumed"),
    "kakan": ("actor", "consumed", "pai"),
    "reach": ("actor",),
    "reach_accepted": ("actor", "deltas", "scores"),
    "dora": ("dora_marker",),
    "kita": ("actor", "pai"),
    "hora": ("actor", "deltas", "fan", "fu", "pai", "scores", "target"),
    "ryukyoku": ("deltas", "reason", "scores", "tehais", "tenpais"),
    "end_kyoku": (),
    "end_game": (),
}
_SEAT_ARRAY_FIELDS = frozenset({"deltas", "names", "scores", "tehais", "tenpais"})
_ACTION_FIELDS = {
    "dahai": ("pai", "tsumogiri"),
    "dealer_opening_dahai": ("pai",),
    "riichi_dahai": ("pai", "tsumogiri"),
    "dealer_opening_riichi_dahai": ("pai",),
    "chi": ("pai", "consumed"),
    "pon": ("pai", "consumed"),
    "daiminkan": ("pai", "consumed"),
    "ankan": ("pai", "consumed"),
    "kakan": ("pai", "consumed"),
    "kita": (),
    "tsumo": ("pai",),
    "ron": ("pai", "target"),
    "kyushukyuhai": (),
    "pass_all": (),
}


class FlyADecisionError(RuntimeError):
    """Safe decision failure that never includes request secrets."""


class FlyADecisionSuppressed(FlyADecisionError):
    """The server authoritatively determined that no client action should be shown."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_event(event: MJAIEvent, seat_count: int) -> dict[str, Any]:
    """Convert one Akagi dataclass event to a v1 observed canonical event."""
    if seat_count not in {THREE_PLAYER_SEATS, FOUR_PLAYER_SEATS}:
        raise ValueError("seat_count must be 3 or 4")

    raw = {field.name: getattr(event, field.name) for field in fields(event)}
    event_type = "kita" if raw.get("type") == "nukidora" else raw.get("type")
    if not isinstance(event_type, str) or event_type not in _EVENT_FIELDS:
        raise ValueError("unsupported MJAI event type")
    if event_type == "start_game":
        raw.update({"names": [""] * seat_count, "seed": None})

    result: dict[str, Any] = {"type": event_type}
    for name in _EVENT_FIELDS[event_type]:
        value = raw.get(name)
        if value is None and not (event_type == "start_game" and name == "seed"):
            continue
        if name in _SEAT_ARRAY_FIELDS and isinstance(value, list | tuple):
            value = list(value[:seat_count])
        result[name] = value
    return result


def compute_state_digest(events: list[dict[str, Any]]) -> str:
    """Match FlyTable's FNV-1a64 over serialized ``CanonicalEvent`` values."""
    canonical = []
    for event in events:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise ValueError("canonical event requires a string type")
        canonical.append(
            {"type": event_type} | {key: _sorted_json(event[key]) for key in sorted(event) if key != "type"}
        )
    return _fnv1a64(canonical)


def build_state_envelope(
    events: list[dict[str, Any]],
    player_id: int,
    is_3p: bool,
) -> dict[str, Any]:
    """Build the complete observed event stream; FlyAPI derives every legal action."""
    seats = THREE_PLAYER_SEATS if is_3p else FOUR_PLAYER_SEATS
    if isinstance(player_id, bool) or not isinstance(player_id, int) or not 0 <= player_id < seats:
        raise ValueError("player_id is out of range")

    canonical_events = [dict(event) for event in events]
    return {
        "schema": "flya-mahjong-events-v1",
        "rule_line": "riichi3p" if is_3p else "riichi4p",
        "viewer_seat": player_id,
        "source": {"kind": "observed"},
        "from_seq": 0,
        "to_seq": len(canonical_events),
        "events": canonical_events,
        "state_digest": compute_state_digest(canonical_events),
    }


def canonical_action_to_mjai(  # noqa: C901, PLR0911
    action: dict[str, Any],
    player_id: int,
    last_kawa_tile: str | None = None,
) -> dict[str, Any]:
    """Translate one selected FlyTable action into Akagi's MJAI response shape."""
    action_type = action.get("type")
    if action_type == "dahai":
        return {
            "type": "dahai",
            "actor": player_id,
            "pai": action["pai"],
            "tsumogiri": action["tsumogiri"],
        }
    if action_type == "dealer_opening_dahai":
        return {
            "type": "dahai",
            "actor": player_id,
            "pai": action["pai"],
            "tsumogiri": False,
        }
    if action_type in {"riichi_dahai", "dealer_opening_riichi_dahai"}:
        return {"type": "reach", "actor": player_id, "pai": action["pai"]}
    if action_type in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
        result = {
            "type": action_type,
            "actor": player_id,
            "consumed": list(action["consumed"]),
        }
        if action_type != "ankan" and "pai" in action:
            result["pai"] = action["pai"]
        return result
    if action_type == "kita":
        return {"type": "nukidora", "actor": player_id, "pai": "N"}
    if action_type == "tsumo":
        result = {"type": "hora", "actor": player_id, "target": player_id}
        if action.get("pai") is not None:
            result["pai"] = action["pai"]
        return result
    if action_type == "ron":
        pai = action.get("pai") or last_kawa_tile
        if not pai:
            raise ValueError("ron action requires a discard tile")
        return {"type": "hora", "actor": player_id, "target": action["target"], "pai": pai}
    if action_type == "kyushukyuhai":
        return {"type": "ryukyoku"}
    if action_type == "pass_all":
        return {"type": "none"}
    raise ValueError("unsupported canonical action type")


class FlyADecisionClient:
    """Authenticated, retry-safe client for ``POST /beta/v1/decision``."""

    def __init__(
        self,
        base_url: str,
        key: str,
        proxy: str = "",
        ca_file: str | None = None,
    ):
        self.base_url = _normalize_base_url(base_url)
        if not isinstance(key, str) or not key.strip():
            raise ValueError("FlyA test API key is required")
        self._key = key.strip()
        self._verify = _resolve_ca_file(ca_file)
        self.session = requests.Session()
        configure_ot3_session(self.session, proxy)
        self.circuit_open = False
        self._failures = 0
        self._retry_at = 0.0

    def close(self) -> None:
        self.session.close()

    def react(
        self, payload: dict[str, Any], status: BotStatusContext
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Submit one frozen request and return its selected action, probabilities and model."""
        self._check_circuit(status)
        try:
            request = {"request_id": str(uuid4())}
            request.update({key: value for key, value in payload.items() if key != "request_id"})
            body = json.dumps(
                request,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
            frozen_request = json.loads(body)
            response = self._post_with_retry(body, frozen_request)
            if response.status_code == HTTPStatus.FORBIDDEN and _response_error_code(response) == "test_api_key_frozen":
                self._activate_key()
                response = self._post_with_retry(body, frozen_request)
            if response.status_code != HTTPStatus.OK:
                code = _safe_response_error_code(response)
                if code in {
                    "decision_not_required",
                    "state_replay_incomplete",
                    "state_replay_invalid",
                }:
                    raise FlyADecisionSuppressed(code)
                detail = f", {code}" if code else ""
                raise FlyADecisionError(f"FlyA decision failed (HTTP {response.status_code}{detail})")
            try:
                value = response.json()
            except ValueError:
                raise FlyADecisionError("FlyA decision returned invalid JSON") from None
            action, actions, model_id = _validated_actions(value, frozen_request)
        except FlyADecisionSuppressed:
            raise
        except FlyADecisionError:
            self._record_failure(status)
            raise
        except (TypeError, ValueError, requests.RequestException):
            self._record_failure(status)
            raise FlyADecisionError("FlyA decision request failed") from None

        if self._failures:
            self._reset_breaker(status)
        return action, actions, model_id

    def _post_with_retry(self, body: bytes, request: dict[str, Any]) -> requests.Response:
        url = f"{self.base_url}{FLYA_DECISION_PATH}"
        state = request.get("state") if isinstance(request.get("state"), dict) else {}
        for attempt in range(2):
            attempt_number = attempt + 1
            started_at = _log_flya_http_request(
                "decision",
                "POST",
                url,
                attempt_number,
                request_id=request.get("request_id"),
                session_id=request.get("session_id"),
                model_id=request.get("model_id"),
                from_seq=state.get("from_seq"),
                to_seq=state.get("to_seq"),
                state_digest=state.get("state_digest"),
            )
            try:
                response = self.session.request(
                    "POST",
                    url,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                    data=body,
                    timeout=FLYA_DECISION_TIMEOUT_SECONDS,
                    verify=self._verify,
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                _log_flya_http_exception("decision", "POST", url, attempt_number, started_at, error, self._key)
                if attempt == 0:
                    continue
                raise
            _log_flya_http_response("decision", "POST", url, attempt_number, started_at, response)
            if not HTTPStatus.INTERNAL_SERVER_ERROR <= response.status_code < HTTP_SERVER_ERROR_LIMIT or attempt == 1:
                return response
        raise AssertionError("retry loop must return or raise")

    def _activate_key(self) -> None:
        url = f"{self.base_url}/quota"
        started_at = _log_flya_http_request("key-activation", "GET", url, 1)
        try:
            response = self.session.request(
                "GET",
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._key}",
                },
                timeout=FLYA_TEST_TIMEOUT_SECONDS,
                verify=self._verify,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            _log_flya_http_exception("key-activation", "GET", url, 1, started_at, error, self._key)
            raise FlyADecisionError("FlyA key activation could not reach the service") from None
        _log_flya_http_response("key-activation", "GET", url, 1, started_at, response)
        if response.status_code != HTTPStatus.OK:
            code = _safe_response_error_code(response)
            detail = f", {code}" if code else ""
            raise FlyADecisionError(f"FlyA key activation failed (HTTP {response.status_code}{detail})")
        try:
            value = response.json()
        except ValueError:
            raise FlyADecisionError("FlyA key activation returned invalid JSON") from None
        if not isinstance(value, dict) or value.get("status") not in {"active", "grace"}:
            raise FlyADecisionError("FlyA key activation returned an invalid response")

    def _check_circuit(self, status: BotStatusContext) -> None:
        if not self.circuit_open:
            return
        if time.monotonic() >= self._retry_at:
            self.circuit_open = False
            return
        status.set_flag(NotificationCode.RECONNECTING)
        status.set_metadata(NotificationCode.RECONNECTING, True)
        raise FlyADecisionError("FlyA decision circuit breaker is open")

    def _record_failure(self, status: BotStatusContext) -> None:
        self._failures += 1
        backoff = min(FLYA_BREAKER_BASE_SECONDS * 2 ** (self._failures - 1), FLYA_BREAKER_MAX_SECONDS)
        self._retry_at = time.monotonic() + backoff
        self.circuit_open = True
        logger.warning(f"FlyA decision circuit breaker opened for {backoff:.0f}s.")
        status.set_flag(NotificationCode.RECONNECTING)
        status.set_metadata(NotificationCode.RECONNECTING, True)

    def _reset_breaker(self, status: BotStatusContext) -> None:
        self._failures = 0
        self._retry_at = 0.0
        self.circuit_open = False
        status.set_flag(NotificationCode.SERVICE_RESTORED)
        status.set_metadata(NotificationCode.RECONNECTING, False)


class FlyADecider:
    """Send observed events to FlyAPI and apply its server-authoritative legal decision."""

    def __init__(
        self,
        status: BotStatusContext,
        online_executor: OnlineInferenceExecutor | None = None,
    ):
        self.status = status
        self.events: list[dict[str, Any]] = []
        self.mjai_events: list[MJAIEvent] = []
        self.player_id: int | None = None
        self.is_3p = False
        self.history_complete = False
        self.session_id: str | None = None
        self.client: FlyADecisionClient | None = None
        self._client_signature: tuple[str, str, str] | None = None
        self._owns_online_executor = online_executor is None
        self.online_executor = online_executor or OnlineInferenceExecutor()

    def observe_system_event(self, code: str) -> None:
        """Invalidate an observed replay as soon as its game transport loses data."""
        if code != NotificationCode.GAME_DISCONNECTED or self.player_id is None:
            return
        self._disable_remote_for_game("game_disconnected")

    def process(  # noqa: C901, PLR0911, PLR0912
        self, event: MJAIEvent, response: MJAIResponse | None, tracker: object
    ) -> MJAIResponse | None:
        self._record_event(event)
        if response is None:
            return response
        if isinstance(event, MJAIEventBase) and event.sync:
            return response
        if not self._enabled():
            self._disable_client()
            if response.get("meta", {}).get("engine_type") == "flya":
                return self._replay_local_decision() or response
            return response
        if not is_local_decision(response, is_3p=self.is_3p):
            return response

        meta = response.get("meta")
        if not meta:
            return response
        if not self.history_complete or self.player_id is None:
            return self._fallback(response)

        try:
            deadline = time.monotonic() + FLYA_END_TO_END_DEADLINE_SECONDS
            model_id = local_settings.ot.flya_model_for(self.is_3p).strip()
            state = build_state_envelope(self.events, self.player_id, self.is_3p)
            client = self._get_client()
            payload = {
                "state": state,
                "deadline_ms": 8000,
            }
            if self.session_id:
                payload["session_id"] = self.session_id
            if model_id:
                payload["model_id"] = model_id
            call_status = BotStatusContext()
            try:
                # The executor may abandon an uncooperative HTTP call. Track the
                # caller-visible wait so a discarded late result cannot report success.
                with self.status.track_inference("FlyA", model_id) as inference:
                    selected, actions, model_id = self.online_executor.run(
                        lambda: client.react(payload, call_status),
                        deadline=deadline,
                    )
                    inference.set_model(model_id)
            finally:
                self.status.update_flags(call_status.flags)
                self.status.update_metadata(call_status.metadata)
            remote = canonical_action_to_mjai(
                selected,
                self.player_id,
                getattr(tracker, "last_kawa_tile", None),
            )
        except FlyADecisionSuppressed as error:
            # process() reaches FlyA only when libriichi's legal mask says this
            # is a real local decision. Returning none here can time out a
            # mandatory discard, so immediately use Mortal and rotate the
            # server session before the next decision.
            logger.warning(
                f"FlyA suppression at required decision code={error.code} "
                f"events={len(self.events)} session_id={self.session_id or 'none'}; "
                "using local Mortal and applying safe recovery."
            )
            if error.code in {"state_replay_invalid", "state_replay_incomplete"}:
                self._disable_remote_for_game(error.code)
            else:
                self._rebuild_remote_state(error.code)
            return self._fallback(response)
        except OnlineInferenceCancelled:
            logger.info("FlyA inference cancelled; skipping local replay during lifecycle transition")
            return response
        except OnlineInferenceError:
            self.status.set_flag(NotificationCode.RECONNECTING)
            self.status.set_metadata(NotificationCode.RECONNECTING, True)
            logger.exception("FlyA inference deadline/capacity failure; using the local Mortal decision")
            return self._fallback(response)
        except (FlyADecisionError, TypeError, ValueError):
            logger.exception("FlyA inference failed; using the local Mortal decision")
            return self._fallback(response)

        remote_meta = dict(meta)
        remote_meta.pop("q_values", None)
        remote_meta.update(
            {
                "decision_source": "flya",
                "engine_type": "flya",
                "fallback_used": False,
                "online_service_reconnecting": client.circuit_open,
                "flya_action": selected,
                "flya_actions": actions,
                "flya_model": model_id,
            }
        )
        remote["meta"] = remote_meta
        return remote

    def _record_event(self, event: MJAIEvent) -> None:
        if isinstance(event, StartGameEvent):
            self.online_executor.next_generation()
            self.events = []
            self.mjai_events = []
            self.player_id = event.id
            self.is_3p = event.is_3p
            self.history_complete = True
            self.session_id = str(uuid4())
            if self.client:
                self.client.close()
            self.client = None
            self._client_signature = None
        if not isinstance(event, MJAIEventBase):
            return

        # A reconnect can only provide a suffix of the current hand.  Never send
        # that suffix to FlyA as though it were complete.  A later start_kyoku is
        # a trustworthy boundary because it contains scores, dealer, dora and all
        # of the local player's tiles, so it can safely form a new observed replay.
        if not self.history_complete:
            if isinstance(event, StartKyokuEvent) and self.player_id is not None:
                self._rebase_at_start_kyoku(event)
                return
            self.mjai_events.append(event)
            if isinstance(event, EndGameEvent):
                self.online_executor.next_generation()
                self.player_id = None
                self.session_id = None
            return

        self.mjai_events.append(event)
        try:
            self.events.append(canonical_event(event, THREE_PLAYER_SEATS if self.is_3p else FOUR_PLAYER_SEATS))
        except (TypeError, ValueError) as error:
            logger.warning(f"FlyA could not canonicalize event type={event.type}: {error}")
            self._disable_remote_for_game("canonical_event_invalid")
        if isinstance(event, EndGameEvent):
            self.online_executor.next_generation()
            self.history_complete = False
            self.player_id = None
            self.session_id = None
            if self.client:
                self.client.close()
            self.client = None
            self._client_signature = None

    def _rebase_at_start_kyoku(self, event: StartKyokuEvent) -> None:
        """Resume FlyA from the first complete hand boundary after a replay gap."""
        if self.player_id is None:
            return
        previous_event_count = len(self.events)
        previous_session = self.session_id
        start_game = StartGameEvent(id=self.player_id, is_3p=self.is_3p, sync=True)
        seat_count = THREE_PLAYER_SEATS if self.is_3p else FOUR_PLAYER_SEATS

        self.online_executor.next_generation()
        if self.client:
            self.client.close()
        self.client = None
        self._client_signature = None
        self.mjai_events = [start_game, event]
        try:
            self.events = [canonical_event(start_game, seat_count), canonical_event(event, seat_count)]
        except (TypeError, ValueError) as error:
            self.events = []
            self.history_complete = False
            self.session_id = None
            logger.warning(f"FlyA failed to rebuild at start_kyoku: {error}")
            return

        self.history_complete = True
        self.session_id = str(uuid4())
        self.status.set_metadata(NotificationCode.RECONNECTING, False)
        self.status.set_metadata(NotificationCode.FALLBACK_USED, False)
        logger.info(
            "FlyA replay resumed at fresh start_kyoku "
            f"previous_events={previous_event_count} previous_session={previous_session or 'none'} "
            f"new_session={self.session_id}."
        )

    def _enabled(self) -> bool:
        ot = local_settings.ot
        return bool(ot.online and ot.provider == "flya_test_api" and ot.flya_server.strip() and ot.flya_api_key.strip())

    def _get_client(self) -> FlyADecisionClient:
        ot = local_settings.ot
        signature = (ot.flya_server.strip().rstrip("/"), ot.flya_api_key.strip(), ot.effective_proxy())
        if self.client is None or signature != self._client_signature:
            self.online_executor.next_generation()
            if self.client:
                self.client.close()
            self.client = FlyADecisionClient(*signature)
            self._client_signature = signature
        return self.client

    def _disable_client(self) -> None:
        if self.client or self._client_signature:
            self.online_executor.next_generation()
        if self.client:
            self.client.close()
        self.client = None
        self._client_signature = None

    def _rebuild_remote_state(self, reason: str) -> None:
        """Keep observed history but force a fresh full replay on a new server session."""
        previous_session = self.session_id
        self.session_id = str(uuid4()) if self.player_id is not None else None
        if self.client:
            self.client.close()
        self.client = None
        self._client_signature = None
        logger.info(
            f"FlyA remote state rebuilt reason={reason} events={len(self.events)} "
            f"previous_session={previous_session or 'none'} new_session={self.session_id or 'none'}."
        )

    def _disable_remote_for_game(self, reason: str) -> None:
        """Stop replaying an invalid prefix until the next complete hand boundary."""
        diagnostic_fields = {
            "type",
            "actor",
            "target",
            "pai",
            "consumed",
            "tsumogiri",
            "dora_marker",
            "scores",
            "deltas",
        }
        tail_events = [
            {key: value for key, value in event.items() if key in diagnostic_fields} for event in self.events[-12:]
        ]
        logger.warning(
            f"FlyA disabled for current game reason={reason} events={len(self.events)} "
            f"tail_events={tail_events}; local Mortal remains active."
        )
        self.history_complete = False
        self.session_id = None
        self.status.set_metadata(NotificationCode.RECONNECTING, False)
        self.status.set_metadata(NotificationCode.FALLBACK_USED, True)
        self.online_executor.next_generation()
        if self.client:
            self.client.close()
        self.client = None
        self._client_signature = None

    def close(self) -> None:
        self._disable_client()
        if self._owns_online_executor:
            self.online_executor.close()

    def _fallback(self, response: MJAIResponse) -> MJAIResponse:
        replayed = self._replay_local_decision()
        if replayed is None:
            logger.error(
                f"FlyA local fallback replay produced no response; preserving response_type={response.get('type')}."
            )
        response = replayed or response
        meta = response.get("meta")
        if meta is not None:
            meta.update(
                {
                    "decision_source": "flya_fallback",
                    "fallback_used": True,
                    "online_service_reconnecting": bool(
                        (self.client and self.client.circuit_open)
                        or self.status.metadata.get(NotificationCode.RECONNECTING)
                    ),
                }
            )
        return response

    def _replay_local_decision(self) -> MJAIResponse | None:
        """Replay the current game into Mortal only when local fallback is needed."""
        if not self.mjai_events:
            return None

        from akagi_ng.mjai_bot.bot import MortalBot

        bot = MortalBot(BotStatusContext(), is_3p=self.is_3p)
        response: MJAIResponse | None = None
        last_index = len(self.mjai_events) - 1
        try:
            for index, event in enumerate(self.mjai_events):
                response = bot.react(replace(event, sync=index < last_index))
        except Exception:
            logger.exception("Failed to replay events for local Mortal fallback")
            return None
        return response


def _validated_actions(  # noqa: C901
    value: object, request: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if (
        not isinstance(value, dict)
        or value.get("protocol") != "flya-test-api-v1"
        or not isinstance(value.get("attempt"), dict)
    ):
        raise FlyADecisionError("FlyA decision returned an invalid response")
    attempt = value["attempt"]
    if attempt.get("status") != "success":
        raise FlyADecisionError("FlyA decision was not successful")
    model_id = value.get("model_id")
    requested_model = request.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip() or (requested_model and model_id != requested_model):
        raise FlyADecisionError("FlyA decision returned an invalid response")
    actions = attempt.get("actions")
    if not isinstance(actions, list) or not actions:
        raise FlyADecisionError("FlyA decision returned invalid actions")

    result: list[tuple[int, dict[str, Any]]] = []
    actions_by_id: dict[int, dict[str, Any]] = {}
    for item in actions:
        if not isinstance(item, dict):
            raise FlyADecisionError("FlyA decision returned invalid actions")
        action_id = item.get("action_id")
        action = item.get("action")
        probability = item.get("probability")
        if (
            isinstance(action_id, bool)
            or not isinstance(action_id, int)
            or action_id < 0
            or action_id in actions_by_id
            or not isinstance(action, dict)
            or not isinstance(action.get("type"), str)
            or isinstance(probability, bool)
            or not isinstance(probability, int | float)
            or not math.isfinite(probability)
            or not 0 < probability <= 1
        ):
            raise FlyADecisionError("FlyA decision returned invalid actions")
        fields = _ACTION_FIELDS.get(action["type"])
        optional: set[str] = set()
        if action["type"] in {"tsumo", "ron"}:
            optional.add("pai")
        required = set(fields or ()) - optional
        allowed = {"type", *(fields or ())}
        if fields is None or not required.issubset(action) or not set(action).issubset(allowed):
            raise FlyADecisionError("FlyA decision returned invalid actions")
        canonical = dict(action)
        actions_by_id[action_id] = canonical
        result.append((action_id, {"action": canonical, "prob": float(probability)}))

    if not math.isclose(sum(item["prob"] for _action_id, item in result), 1.0, abs_tol=1e-6):
        raise FlyADecisionError("FlyA decision returned invalid actions")
    selected_id = attempt.get("selected_action_id")
    server_selected = (
        actions_by_id.get(selected_id) if isinstance(selected_id, int) and not isinstance(selected_id, bool) else None
    )
    if server_selected is None or attempt.get("action") != server_selected:
        raise FlyADecisionError("FlyA decision returned invalid actions")
    selected_item = next(item for action_id, item in result if action_id == selected_id)
    remaining = sorted(
        (item for action_id, item in result if action_id != selected_id),
        key=lambda item: item["prob"],
        reverse=True,
    )
    visible = [selected_item, *remaining[: FLYA_DISPLAY_ACTION_LIMIT - 1]]
    return server_selected, visible, model_id


def _sorted_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: _sorted_json(value[key]) for key in sorted(value)}
    if isinstance(value, list | tuple):
        return [_sorted_json(item) for item in value]
    return value


def _fnv1a64(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    digest = 0xCBF29CE484222325
    for byte in data:
        digest ^= byte
        digest = digest * 0x100000001B3 & 0xFFFFFFFFFFFFFFFF
    return f"fnv1a64:{digest:016x}"
