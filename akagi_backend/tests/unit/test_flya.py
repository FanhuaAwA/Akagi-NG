import json
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import requests

from akagi_ng.mjai_bot.flya import (
    FLYA_BREAKER_MAX_SECONDS,
    FlyADecider,
    FlyADecisionClient,
    FlyADecisionError,
    FlyADecisionSuppressed,
    build_state_envelope,
    canonical_action_to_mjai,
    canonical_event,
    compute_state_digest,
)
from akagi_ng.mjai_bot.online_inference import OnlineInferenceCancelled
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.types import (
    EndGameEvent,
    HoraEvent,
    NukidoraEvent,
    ReachAcceptedEvent,
    StartGameEvent,
    StartKyokuEvent,
    TsumoEvent,
)
from akagi_ng.settings import local_settings


def _response(payload: object, *, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def _decision_payload() -> dict[str, object]:
    return {
        "model_id": "flya-heyman-2.1",
        "state": build_state_envelope([], player_id=0, is_3p=False),
        "deadline_ms": 8000,
    }


def _success(action: dict[str, object] | None = None) -> dict[str, object]:
    selected = action or {"type": "dahai", "pai": "P", "tsumogiri": False}
    return {
        "protocol": "flya-test-api-v1",
        "model_id": "flya-heyman-2.1",
        "model_selection": "explicit",
        "attempt": {
            "status": "success",
            "selected_action_id": 0,
            "action": selected,
            "actions": [{"action_id": 0, "action": selected, "probability": 1.0}],
        },
    }


def _enable_flya(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_settings.ot, "online", True)
    monkeypatch.setattr(local_settings.ot, "provider", "flya_test_api")
    monkeypatch.setattr(local_settings.ot, "flya_server", "https://server.example")
    monkeypatch.setattr(local_settings.ot, "flya_api_key", "secret")
    monkeypatch.setattr(local_settings.ot, "flya_model_4p", "flya-heyman-2.1")


def _record_four_player_start(decider: FlyADecider) -> None:
    decider.process(StartGameEvent(id=0, is_3p=False), None, object())
    decider.process(
        StartKyokuEvent(
            bakaze="E",
            dora_marker="3p",
            kyoku=1,
            honba=0,
            kyotaku=0,
            oya=0,
            scores=[25000] * 4,
            tehais=[["1m"] * 13, ["?"] * 13, ["?"] * 13, ["?"] * 13],
        ),
        None,
        object(),
    )


def test_digest_goldens_match_flytable() -> None:
    events = [
        {"type": "start_game", "names": ["a", "b", "c", "d"], "seed": None},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "dora_marker": "3p",
            "honba": 0,
            "kyoku": 1,
            "kyotaku": 0,
            "oya": 0,
            "scores": [25000] * 4,
            "tehais": [
                ["P", "5p", "W", "N", "7m", "2m", "2p", "1m", "C", "8p", "9m", "6s", "N"],
                ["?"] * 13,
                ["?"] * 13,
                ["?"] * 13,
            ],
        },
        {"type": "tsumo", "actor": 0, "pai": "2s"},
    ]
    assert compute_state_digest([]) == "fnv1a64:09612b07b5ecb5a5"
    assert compute_state_digest(events) == "fnv1a64:719e5ca198e7a43f"


def test_canonical_event_shapes_four_player_events_and_filters_fields() -> None:
    assert canonical_event(StartGameEvent(id=2, is_3p=False, sync=True), 4) == {
        "type": "start_game",
        "names": ["", "", "", ""],
        "seed": None,
    }
    assert canonical_event(ReachAcceptedEvent(actor=1, scores=[24000] * 4), 4) == {
        "type": "reach_accepted",
        "actor": 1,
        "scores": [24000] * 4,
    }
    assert canonical_event(
        HoraEvent(
            actor=0,
            target=1,
            pai="5m",
            scores=[33000, 17000, 25000, 25000],
            deltas=[8000, -8000, 0, 0],
            kyoku=1,
            hand=["1m"] * 14,
            yaku=["riichi"],
            fu=30,
            fan=4,
        ),
        4,
    ) == {
        "type": "hora",
        "actor": 0,
        "deltas": [8000, -8000, 0, 0],
        "fan": 4,
        "fu": 30,
        "pai": "5m",
        "scores": [33000, 17000, 25000, 25000],
        "target": 1,
    }


def test_canonical_event_trims_three_player_arrays_and_normalizes_kita() -> None:
    start = StartKyokuEvent(
        bakaze="E",
        dora_marker="1p",
        kyoku=1,
        honba=0,
        kyotaku=0,
        oya=0,
        scores=[35000, 35000, 35000, 0],
        tehais=[[str(seat)] * 13 for seat in range(4)],
    )
    result = canonical_event(start, 3)

    assert result["scores"] == [35000, 35000, 35000]
    assert len(result["tehais"]) == 3
    assert canonical_event(NukidoraEvent(actor=1), 3) == {"type": "kita", "actor": 1, "pai": "N"}
    assert canonical_event(NukidoraEvent(actor=1), 4) == {"type": "kita", "actor": 1, "pai": "N"}


def test_build_state_envelope_contains_only_observed_events() -> None:
    events = [canonical_event(StartGameEvent(id=1, is_3p=True), 3)]
    envelope = build_state_envelope(events, player_id=1, is_3p=True)

    assert envelope == {
        "schema": "flya-mahjong-events-v1",
        "rule_line": "riichi3p",
        "viewer_seat": 1,
        "source": {"kind": "observed"},
        "from_seq": 0,
        "to_seq": 1,
        "events": events,
        "state_digest": compute_state_digest(events),
    }


@pytest.mark.parametrize(
    ("action", "last_kawa", "expected"),
    [
        (
            {"type": "dahai", "pai": "1m", "tsumogiri": True},
            None,
            {"type": "dahai", "actor": 2, "pai": "1m", "tsumogiri": True},
        ),
        (
            {"type": "riichi_dahai", "pai": "2p", "tsumogiri": False},
            None,
            {"type": "reach", "actor": 2, "pai": "2p"},
        ),
        (
            {"type": "chi", "pai": "3m", "consumed": ["1m", "2m"]},
            None,
            {"type": "chi", "actor": 2, "pai": "3m", "consumed": ["1m", "2m"]},
        ),
        (
            {"type": "pon", "pai": "P", "consumed": ["P", "P"]},
            None,
            {"type": "pon", "actor": 2, "pai": "P", "consumed": ["P", "P"]},
        ),
        (
            {"type": "daiminkan", "pai": "9s", "consumed": ["9s"] * 3},
            None,
            {"type": "daiminkan", "actor": 2, "pai": "9s", "consumed": ["9s"] * 3},
        ),
        (
            {"type": "ankan", "pai": "E", "consumed": ["E"] * 4},
            None,
            {"type": "ankan", "actor": 2, "consumed": ["E"] * 4},
        ),
        (
            {"type": "kakan", "pai": "C", "consumed": ["C"] * 3},
            None,
            {"type": "kakan", "actor": 2, "pai": "C", "consumed": ["C"] * 3},
        ),
        ({"type": "kita"}, None, {"type": "nukidora", "actor": 2, "pai": "N"}),
        (
            {"type": "tsumo", "pai": "5pr"},
            None,
            {"type": "hora", "actor": 2, "target": 2, "pai": "5pr"},
        ),
        (
            {"type": "ron", "target": 1},
            "7s",
            {"type": "hora", "actor": 2, "target": 1, "pai": "7s"},
        ),
        ({"type": "kyushukyuhai"}, None, {"type": "ryukyoku"}),
        ({"type": "pass_all"}, None, {"type": "none"}),
    ],
)
def test_canonical_actions_write_back_to_mjai(action, last_kawa, expected) -> None:
    assert canonical_action_to_mjai(action, 2, last_kawa) == expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            {"type": "dealer_opening_dahai", "pai": "1m"},
            {"type": "dahai", "actor": 2, "pai": "1m", "tsumogiri": False},
        ),
        (
            {"type": "dealer_opening_riichi_dahai", "pai": "2p"},
            {"type": "reach", "actor": 2, "pai": "2p"},
        ),
        ({"type": "pass_all"}, {"type": "none"}),
    ],
)
def test_decision_client_accepts_new_public_action_shapes(action, expected) -> None:
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(_success(action)))

    selected, actions, _model_id = client.react(_decision_payload(), BotStatusContext())

    assert selected == action
    assert actions == [{"action": action, "prob": 1.0}]
    assert canonical_action_to_mjai(selected, 2) == expected


def test_decider_replaces_local_action_and_marks_flya_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flya(monkeypatch)
    decider = FlyADecider(BotStatusContext())
    _record_four_player_start(decider)
    client = MagicMock(circuit_open=False)
    selected = {"type": "dahai", "pai": "1m", "tsumogiri": False}
    actions = [
        {"action": selected, "prob": 0.75},
        {"action": {"type": "dahai", "pai": "2s", "tsumogiri": True}, "prob": 0.25},
    ]
    client.react.return_value = selected, actions, "flya-heyman-2.1"
    monkeypatch.setattr(decider, "_get_client", lambda: client)
    tracker = MagicMock(last_kawa_tile=None)
    local = {"type": "dahai", "actor": 0, "pai": "2s", "meta": {"mask_bits": 1, "engine_type": "mortal"}}

    result = decider.process(TsumoEvent(actor=0, pai="2s"), local, tracker)

    assert result["pai"] == "1m"
    assert result["meta"]["decision_source"] == "flya"
    assert result["meta"]["flya_model"] == "flya-heyman-2.1"
    assert result["meta"]["flya_actions"] == actions
    assert result["meta"]["fallback_used"] is False
    assert "q_values" not in result["meta"]
    request = client.react.call_args.args[0]
    assert request["session_id"] == decider.session_id
    assert "rule_profile" not in request["state"]
    assert "match_context" not in request


def test_decider_reuses_session_id_until_game_boundary() -> None:
    decider = FlyADecider(BotStatusContext())

    decider.process(StartGameEvent(id=0, is_3p=False), None, object())
    first_session = decider.session_id
    assert first_session is not None
    assert str(UUID(first_session)) == first_session

    decider.process(TsumoEvent(actor=0, pai="2s", sync=True), None, object())
    assert decider.session_id == first_session

    decider.process(EndGameEvent(), None, object())
    assert decider.session_id is None

    decider.process(StartGameEvent(id=0, is_3p=False, sync=True), None, object())
    assert decider.session_id is not None
    assert decider.session_id != first_session


def test_decider_omits_model_id_for_server_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flya(monkeypatch)
    monkeypatch.setattr(local_settings.ot, "flya_model_4p", "")
    decider = FlyADecider(BotStatusContext())
    _record_four_player_start(decider)
    selected = {"type": "dahai", "pai": "1m", "tsumogiri": False}
    client = MagicMock(circuit_open=False)
    client.react.return_value = selected, [{"action": selected, "prob": 1.0}], "flya-manout-1"
    monkeypatch.setattr(decider, "_get_client", lambda: client)
    tracker = MagicMock(last_kawa_tile=None)
    local = {"type": "dahai", "actor": 0, "pai": "2s", "meta": {"mask_bits": 1}}

    result = decider.process(TsumoEvent(actor=0, pai="2s"), local, tracker)

    assert "model_id" not in client.react.call_args.args[0]
    assert result["meta"]["flya_model"] == "flya-manout-1"


def test_decider_preserves_local_action_on_flya_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flya(monkeypatch)
    decider = FlyADecider(BotStatusContext())
    _record_four_player_start(decider)
    client = MagicMock(circuit_open=True)
    client.react.side_effect = FlyADecisionError("safe failure")
    decider.client = client
    monkeypatch.setattr(decider, "_get_client", lambda: client)
    tracker = MagicMock(last_kawa_tile=None)
    local = {"type": "dahai", "actor": 0, "pai": "2s", "meta": {"mask_bits": 1, "engine_type": "mortal"}}
    monkeypatch.setattr(decider, "_replay_local_decision", lambda: local)

    result = decider.process(TsumoEvent(actor=0, pai="2s"), local, tracker)

    assert result["pai"] == "2s"
    assert result["meta"]["decision_source"] == "flya_fallback"
    assert result["meta"]["fallback_used"] is True
    assert result["meta"]["online_service_reconnecting"] is True


def test_decider_skips_expensive_local_replay_when_shutdown_cancels_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flya(monkeypatch)
    executor = MagicMock()
    executor.run.side_effect = OnlineInferenceCancelled("shutdown")
    decider = FlyADecider(BotStatusContext(), executor)
    _record_four_player_start(decider)
    client = MagicMock(circuit_open=False)
    monkeypatch.setattr(decider, "_get_client", lambda: client)
    replay = MagicMock()
    monkeypatch.setattr(decider, "_replay_local_decision", replay)
    local = {"type": "dahai", "actor": 0, "pai": "2s", "meta": {"mask_bits": 1}}

    result = decider.process(TsumoEvent(actor=0, pai="2s"), local, MagicMock(last_kawa_tile=None))

    assert result is local
    replay.assert_not_called()


def test_decider_suppresses_local_action_when_server_rejects_the_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flya(monkeypatch)
    decider = FlyADecider(BotStatusContext())
    _record_four_player_start(decider)
    client = MagicMock(circuit_open=False)
    client.react.side_effect = FlyADecisionSuppressed("decision_not_required")
    monkeypatch.setattr(decider, "_get_client", lambda: client)
    local = {"type": "dahai", "actor": 0, "pai": "2s", "meta": {"mask_bits": 1}}

    result = decider.process(TsumoEvent(actor=0, pai="2s"), local, MagicMock(last_kawa_tile=None))

    assert result == {"type": "none"}


def test_decider_replays_synced_history_for_exact_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    decider = FlyADecider(BotStatusContext())
    decider.mjai_events = [StartGameEvent(id=0, is_3p=False), TsumoEvent(actor=0, pai="2s")]
    seen = []
    expected = {"type": "dahai", "actor": 0, "pai": "1m", "meta": {"engine_type": "mortal"}}

    class FakeMortalBot:
        def __init__(self, *_args, **_kwargs):
            pass

        def react(self, event):
            seen.append(event)
            return expected if len(seen) == 2 else None

    monkeypatch.setattr("akagi_ng.mjai_bot.bot.MortalBot", FakeMortalBot)

    assert decider._replay_local_decision() == expected
    assert seen[0].sync is True
    assert seen[1].sync is False


def test_decision_client_freezes_compact_body_and_reuses_it_on_retry() -> None:
    client = FlyADecisionClient("https://server.example", "secret", "http://127.0.0.1:7890")
    client.session.request = MagicMock(side_effect=[requests.ReadTimeout("secret URL and body"), _response(_success())])

    action, actions, model_id = client.react(_decision_payload(), BotStatusContext())

    assert action == {"type": "dahai", "pai": "P", "tsumogiri": False}
    assert actions == [{"action": action, "prob": 1.0}]
    assert model_id == "flya-heyman-2.1"
    first, second = client.session.request.call_args_list
    assert first.args == ("POST", "https://server.example/beta/v1/decision")
    assert first.kwargs["data"] is second.kwargs["data"]
    assert b": " not in first.kwargs["data"]
    assert b", " not in first.kwargs["data"]
    body = json.loads(first.kwargs["data"])
    assert body["request_id"]
    assert first.kwargs["allow_redirects"] is False
    assert first.kwargs["verify"] is True
    assert first.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert client.session.trust_env is False
    assert client.session.proxies["https"] == "http://127.0.0.1:7890"


def test_decision_client_accepts_fewer_than_three_probabilities() -> None:
    actions = [
        {"type": "dahai", "pai": "1m", "tsumogiri": False},
        {"type": "dahai", "pai": "2m", "tsumogiri": True},
    ]
    payload = {
        "state": build_state_envelope([], player_id=0, is_3p=False),
    }
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(
        return_value=_response(
            {
                "protocol": "flya-test-api-v1",
                "model_id": "flya-manout-1",
                "model_selection": "server_default",
                "attempt": {
                    "status": "success",
                    "selected_action_id": 0,
                    "action": actions[0],
                    "actions": [
                        {"action_id": 1, "action": actions[1], "probability": 0.7},
                        {"action_id": 0, "action": actions[0], "probability": 0.3},
                    ],
                },
            }
        )
    )

    selected, returned_actions, model_id = client.react(payload, BotStatusContext())

    assert selected == actions[0]
    assert returned_actions == [
        {"action": actions[0], "prob": 0.3},
        {"action": actions[1], "prob": 0.7},
    ]
    assert model_id == "flya-manout-1"


def test_decision_client_executes_and_displays_selected_action_with_at_most_three_actions() -> None:
    actions = [{"type": "dahai", "pai": pai, "tsumogiri": False} for pai in ("1m", "2m", "3m", "4m")]
    response = {
        "protocol": "flya-test-api-v1",
        "model_id": "flya-manout-1",
        "attempt": {
            "status": "success",
            "selected_action_id": 3,
            "action": actions[3],
            "actions": [
                {"action_id": 3, "action": actions[3], "probability": 0.1},
                {"action_id": 1, "action": actions[1], "probability": 0.3},
                {"action_id": 0, "action": actions[0], "probability": 0.4},
                {"action_id": 2, "action": actions[2], "probability": 0.2},
            ],
        },
    }
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(response))

    request = {
        "state": build_state_envelope([], player_id=0, is_3p=False),
    }
    selected, returned_actions, _model_id = client.react(request, BotStatusContext())

    assert selected == actions[3]
    assert returned_actions == [
        {"action": actions[3], "prob": 0.1},
        {"action": actions[0], "prob": 0.4},
        {"action": actions[1], "prob": 0.3},
    ]


def test_decision_client_rejects_an_explicit_model_mismatch() -> None:
    response = _success()
    response["model_id"] = "flya-manout-1"
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(response))

    with pytest.raises(FlyADecisionError, match="invalid response"):
        client.react(_decision_payload(), BotStatusContext())


def test_decision_client_treats_server_state_rejection_as_non_transient() -> None:
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response({"error": "decision_not_required"}, status=422))

    with pytest.raises(FlyADecisionSuppressed, match="decision_not_required"):
        client.react(_decision_payload(), BotStatusContext())

    assert client._failures == 0
    assert client.circuit_open is False


@pytest.mark.parametrize(
    "actions",
    [
        [],
        [{"action": {"type": "dahai", "pai": "P"}, "probability": float("nan")}],
        [{"action": {"type": "dahai", "pai": "P"}, "probability": 0.9}],
        [
            {"action": {"type": "dahai", "pai": "P"}, "probability": 0.6},
            {"action": {"type": "dahai", "pai": "P"}, "probability": 0.4},
        ],
    ],
)
def test_decision_client_rejects_invalid_probabilities(actions: list[dict[str, object]]) -> None:
    response = _success()
    response["attempt"]["actions"] = actions
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(response))

    with pytest.raises(FlyADecisionError, match="invalid actions"):
        client.react(_decision_payload(), BotStatusContext())


def test_decision_client_retries_one_5xx_with_same_request_id() -> None:
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(side_effect=[_response({}, status=503), _response(_success())])

    client.react(_decision_payload(), BotStatusContext())

    calls = client.session.request.call_args_list
    assert len(calls) == 2
    assert json.loads(calls[0].kwargs["data"])["request_id"] == json.loads(calls[1].kwargs["data"])["request_id"]


def test_decision_client_activates_a_frozen_key_and_retries_the_same_request() -> None:
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(
        side_effect=[
            _response({"error": "test_api_key_frozen"}, status=403),
            _response({"status": "active"}),
            _response(_success()),
        ]
    )

    client.react(_decision_payload(), BotStatusContext())

    first, activation, retried = client.session.request.call_args_list
    assert [first.args[0], activation.args[0], retried.args[0]] == ["POST", "GET", "POST"]
    assert activation.args[1] == "https://server.example/beta/v1/quota"
    assert first.kwargs["data"] is retried.kwargs["data"]


def test_decision_client_uses_explicit_or_environment_ca(monkeypatch, tmp_path) -> None:
    environment_ca = tmp_path / "environment.pem"
    environment_ca.write_text("ca")
    explicit_ca = tmp_path / "explicit.pem"
    explicit_ca.write_text("ca")
    monkeypatch.setenv("FLYA_TEST_CA_FILE", str(environment_ca))

    environment = FlyADecisionClient("https://server.example", "secret")
    environment.session.request = MagicMock(return_value=_response(_success()))
    environment.react(_decision_payload(), BotStatusContext())
    assert environment.session.request.call_args.kwargs["verify"] == str(environment_ca)

    explicit = FlyADecisionClient("https://server.example", "secret", ca_file=str(explicit_ca))
    explicit.session.request = MagicMock(return_value=_response(_success()))
    explicit.react(_decision_payload(), BotStatusContext())
    assert explicit.session.request.call_args.kwargs["verify"] == str(explicit_ca)


@pytest.mark.parametrize("attempt_status", ["failure", "timeout", "abstain"])
def test_decision_client_rejects_non_successful_200_attempts(attempt_status: str) -> None:
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(
        return_value=_response(
            {
                "protocol": "flya-test-api-v1",
                "model_id": "flya-heyman-2.1",
                "attempt": {"status": attempt_status},
            }
        )
    )

    with pytest.raises(FlyADecisionError, match="not successful"):
        client.react(_decision_payload(), BotStatusContext())


def test_decision_client_rejects_missing_protocol() -> None:
    response = _success()
    response.pop("protocol")
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(response))

    with pytest.raises(FlyADecisionError, match="invalid response"):
        client.react(_decision_payload(), BotStatusContext())


@pytest.mark.parametrize("http_status", [401, 402])
def test_configuration_failures_are_safe_and_not_retried(http_status: int) -> None:
    key = "never-expose-key"
    client = FlyADecisionClient("https://private-server.example", key)
    client.session.request = MagicMock(return_value=_response({"error": "never-expose-body"}, status=http_status))

    with pytest.raises(FlyADecisionError) as exc_info:
        client.react(_decision_payload(), BotStatusContext())

    message = str(exc_info.value)
    assert f"HTTP {http_status}" in message
    assert "private-server" not in message
    assert key not in message
    assert "never-expose-body" not in message
    client.session.request.assert_called_once()


def test_known_quota_error_code_is_safe_to_report() -> None:
    client = FlyADecisionClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response({"error": "test_api_weekly_quota_exhausted"}, status=402))

    with pytest.raises(FlyADecisionError, match="test_api_weekly_quota_exhausted"):
        client.react(_decision_payload(), BotStatusContext())


def test_circuit_breaker_backs_off_caps_and_resets_after_success(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr("akagi_ng.mjai_bot.flya.time.monotonic", lambda: now)
    client = FlyADecisionClient("https://server.example", "secret")
    status = BotStatusContext()
    client.session.request = MagicMock(return_value=_response({}, status=401))

    for expected in [5.0, 10.0, 20.0, 40.0, 80.0, FLYA_BREAKER_MAX_SECONDS]:
        client._retry_at = 0
        with pytest.raises(FlyADecisionError):
            client.react(_decision_payload(), status)
        assert client._retry_at == now + expected

    calls = client.session.request.call_count
    with pytest.raises(FlyADecisionError, match="circuit breaker"):
        client.react(_decision_payload(), status)
    assert client.session.request.call_count == calls
    assert NotificationCode.RECONNECTING in status.flags

    client._retry_at = 0
    client.session.request = MagicMock(return_value=_response(_success()))
    assert client.react(_decision_payload(), status)[0]["type"] == "dahai"
    assert client.circuit_open is False
    assert client._failures == 0
    assert status.metadata[NotificationCode.RECONNECTING] is False
    assert NotificationCode.SERVICE_RESTORED in status.flags


def test_network_failure_error_never_exposes_url_key_or_body() -> None:
    client = FlyADecisionClient("https://private-server.example", "never-expose-key")
    client.session.request = MagicMock(
        side_effect=requests.ConnectionError("private-server never-expose-key never-expose-body")
    )

    with pytest.raises(FlyADecisionError) as exc_info:
        client.react(_decision_payload(), BotStatusContext())

    assert "private-server" not in str(exc_info.value)
    assert "never-expose" not in str(exc_info.value)
    assert client.session.request.call_count == 2
