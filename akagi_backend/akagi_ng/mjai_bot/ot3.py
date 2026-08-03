"""Akagi OT3 (/v3/react) online inference client and MJAI adapter."""

from __future__ import annotations

import time
from dataclasses import fields
from math import isfinite
from typing import Any

import requests

from akagi_ng.mjai_bot.logger import logger
from akagi_ng.mjai_bot.ot3_proxy import configure_ot3_session
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import (
    MJAIEvent,
    StartGameEvent,
    StartKyokuEvent,
    TsumoEvent,
)

OT3_CONNECT_TIMEOUT_SECONDS = 1.0
OT3_READ_TIMEOUT_SECONDS = 2.0
OT3_END_TO_END_DEADLINE_SECONDS = 6.0
OT3_BREAKER_BASE_SECONDS = 5.0
OT3_BREAKER_MAX_SECONDS = 120.0
MJAI_SEAT_COUNT = 4


class OT3ProtocolError(RuntimeError):
    """The OT3 server returned a response that does not match the v3 contract."""


class OT3Client:
    """Reusable authenticated client for the stateless OT3 v3 API."""

    def __init__(self, url: str, api_key: str, proxy: str = ""):
        self.url = url.strip().rstrip("/")
        self._api_key = api_key.strip()
        self.session = requests.Session()
        configure_ot3_session(self.session, proxy)
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self.timeout = (OT3_CONNECT_TIMEOUT_SECONDS, OT3_READ_TIMEOUT_SECONDS)

        self.circuit_open = False
        self._failures = 0
        self._retry_at = 0.0

    def close(self) -> None:
        self.session.close()

    def react(
        self,
        *,
        player_id: int,
        events: list[dict[str, Any]],
        status: BotStatusContext,
        model: str = "",
    ) -> dict[str, Any]:
        """Call ``POST /v3/react`` and return a validated response."""
        self._check_circuit(status)

        payload: dict[str, Any] = {
            "player_id": player_id,
            "events": events,
        }
        if model.strip():
            payload["model"] = model.strip()

        try:
            response = self.session.post(
                f"{self.url}/v3/react",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
            validated = validate_ot3_response(result)
        except (requests.RequestException, ValueError, OT3ProtocolError) as exc:
            self._record_failure(status)
            # Never include the URL, response body, request headers, or key here.
            raise RuntimeError(f"OT3 request failed: {type(exc).__name__}") from exc

        if self._failures:
            self._reset_breaker(status)
        return validated

    def _check_circuit(self, status: BotStatusContext) -> None:
        if not self.circuit_open:
            return
        if time.monotonic() >= self._retry_at:
            logger.info("OT3 circuit breaker HALF-OPEN; probing the service.")
            self.circuit_open = False
            return
        status.set_flag(NotificationCode.RECONNECTING)
        status.set_metadata(NotificationCode.RECONNECTING, True)
        raise RuntimeError("OT3 circuit breaker is OPEN")

    def _record_failure(self, status: BotStatusContext) -> None:
        self._failures += 1
        backoff = min(
            OT3_BREAKER_BASE_SECONDS * (2 ** (self._failures - 1)),
            OT3_BREAKER_MAX_SECONDS,
        )
        self._retry_at = time.monotonic() + backoff
        self.circuit_open = True
        logger.warning(f"OT3 circuit breaker OPENED for {backoff:.0f}s.")
        status.set_flag(NotificationCode.RECONNECTING)
        status.set_metadata(NotificationCode.RECONNECTING, True)

    def _reset_breaker(self, status: BotStatusContext) -> None:
        logger.info("OT3 circuit breaker CLOSED; service connectivity restored.")
        self._failures = 0
        self.circuit_open = False
        self._retry_at = 0.0
        status.set_flag(NotificationCode.SERVICE_RESTORED)
        status.set_metadata(NotificationCode.RECONNECTING, False)


def validate_ot3_response(value: object) -> dict[str, Any]:
    """Validate the subset of the Akagi-3 v3 response consumed by Akagi-NG."""
    if not isinstance(value, dict):
        raise OT3ProtocolError("response root must be an object")

    reaction = value.get("reaction")
    if reaction is not None and not isinstance(reaction, dict):
        raise OT3ProtocolError("reaction must be an object or null")
    if isinstance(reaction, dict) and not isinstance(reaction.get("type"), str):
        raise OT3ProtocolError("reaction.type must be a string")

    candidates = value.get("candidates", [])
    if not isinstance(candidates, list):
        raise OT3ProtocolError("candidates must be an array")
    normalized_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("action"), str):
            raise OT3ProtocolError("candidate.action must be a string")
        probability = candidate.get("prob", 0.0)
        if isinstance(probability, bool) or not isinstance(probability, int | float):
            raise OT3ProtocolError("candidate.prob must be numeric")
        if not isfinite(probability):
            raise OT3ProtocolError("candidate.prob must be finite")
        normalized_candidates.append(
            {
                "action": candidate["action"],
                "prob": min(1.0, max(0.0, float(probability))),
            }
        )

    model = value.get("model")
    if model is not None and not isinstance(model, str):
        raise OT3ProtocolError("model must be a string or null")

    return {
        "reaction": reaction,
        "candidates": normalized_candidates,
        "model": model,
    }


def build_ot3_events(
    game_start_event: StartGameEvent,
    history: list[MJAIEvent],
    *,
    player_id: int,
    is_3p: bool,
) -> list[dict[str, Any]]:
    """Shape Akagi-NG events exactly as Akagi-3 sends them to ``/v3/react``."""
    stream: list[MJAIEvent] = [game_start_event, *history]
    return [_to_ot3_event(event, player_id=player_id, is_3p=is_3p) for event in stream]


def _to_ot3_event(event: MJAIEvent, *, player_id: int, is_3p: bool) -> dict[str, Any]:
    if isinstance(event, StartGameEvent):
        return {"type": "start_game", "names": ["", "", "", ""]}

    if isinstance(event, StartKyokuEvent):
        scores = list(event.scores)
        tehais = [list(hand) if seat == player_id else _hidden_hand() for seat, hand in enumerate(event.tehais)]
        if is_3p:
            while len(scores) < MJAI_SEAT_COUNT:
                scores.append(0)
            while len(tehais) < MJAI_SEAT_COUNT:
                tehais.append(_hidden_hand())
        return {
            "type": "start_kyoku",
            "bakaze": event.bakaze,
            "dora_marker": event.dora_marker,
            "kyoku": event.kyoku,
            "honba": event.honba,
            "kyotaku": event.kyotaku,
            "oya": event.oya,
            "scores": scores,
            "tehais": tehais,
        }

    if isinstance(event, TsumoEvent):
        return {
            "type": "tsumo",
            "actor": event.actor,
            "pai": event.pai if event.actor == player_id else "?",
        }

    payload = {field.name: getattr(event, field.name) for field in fields(event.__class__) if field.name != "sync"}
    return {key: value for key, value in payload.items() if value is not None}


def apply_player_actor(reaction: dict[str, Any], player_id: int) -> dict[str, Any]:
    """Defensively force an actionable server reaction to the local player's seat."""
    result = dict(reaction)
    if result.get("type") in {
        "tsumo",
        "dahai",
        "reach",
        "pon",
        "chi",
        "daiminkan",
        "ankan",
        "kakan",
        "hora",
        "nukidora",
    }:
        result["actor"] = player_id
    return result


def is_local_decision(response: dict[str, Any], *, is_3p: bool) -> bool:
    """Use the local Mortal result as a cheap legal-action gate for OT3."""
    meta = response.get("meta")
    if not isinstance(meta, dict):
        return False
    mask_bits = meta.get("mask_bits")
    if not isinstance(mask_bits, int) or mask_bits == 0:
        return False
    # A lone ``none`` action is a synchronization event, not a decision.
    none_index = 43 if is_3p else 45
    return mask_bits != 1 << none_index


def _hidden_hand() -> list[str]:
    return ["?"] * 13
