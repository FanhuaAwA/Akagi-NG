import threading
import time
from unittest.mock import MagicMock

import pytest
import requests

from akagi_ng.mjai_bot.bot import MortalBot
from akagi_ng.mjai_bot.online_inference import OnlineInferenceCancelled, OnlineInferenceExecutor
from akagi_ng.mjai_bot.ot3 import (
    OT3Client,
    OT3ProtocolError,
    apply_player_actor,
    build_ot3_events,
    validate_ot3_response,
)
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.schema.types import StartGameEvent, StartKyokuEvent, TsumoEvent


def _start_kyoku(*, is_3p: bool = False) -> StartKyokuEvent:
    players = 3 if is_3p else 4
    return StartKyokuEvent(
        bakaze="E",
        dora_marker="1p",
        kyoku=1,
        honba=0,
        kyotaku=0,
        oya=0,
        scores=[35000] * players,
        tehais=[["1m"] * 13, *([["9p"] * 13] * (players - 1))],
    )


def test_ot3_client_uses_v3_bearer_contract() -> None:
    client = OT3Client("http://server.example/", "secret-key")
    response = MagicMock()
    response.json.return_value = {
        "reaction": {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": False},
        "candidates": [{"action": "dahai:1m", "prob": 0.9}],
        "model": "4p-ot3",
    }
    client.session.post = MagicMock(return_value=response)

    result = client.react(
        player_id=0,
        events=[{"type": "start_game", "names": ["", "", "", ""]}],
        status=BotStatusContext(),
    )

    assert client.session.headers["Authorization"] == "Bearer secret-key"
    client.session.post.assert_called_once()
    url = client.session.post.call_args.args[0]
    body = client.session.post.call_args.kwargs["json"]
    assert url == "http://server.example/v3/react"
    assert body["player_id"] == 0
    assert "model" not in body
    assert result["model"] == "4p-ot3"


def test_ot3_executor_telemetry_reports_returned_model() -> None:
    events = []
    status = BotStatusContext()
    status.set_inference_listener(events.append)
    executor = MagicMock()
    executor.run.side_effect = lambda operation, **_kwargs: operation()
    bot = MortalBot(status, online_executor=executor)
    bot.player_id = 0
    client = MagicMock()
    client.react.return_value = {"model": "returned-model"}

    result = bot._run_ot3(client, model="requested-model", events=[], deadline=time.monotonic() + 1.0)

    assert result == {"model": "returned-model"}
    assert [event["phase"] for event in events] == ["requesting", "success"]
    assert events[-1]["model"] == "returned-model"


def test_ot3_cancellation_telemetry_ignores_late_worker_success() -> None:
    events = []
    status = BotStatusContext()
    status.set_inference_listener(events.append)
    executor = OnlineInferenceExecutor()
    bot = MortalBot(status, online_executor=executor)
    bot.player_id = 0
    release = threading.Event()
    started = threading.Event()
    worker_finished = threading.Event()
    errors: list[BaseException] = []
    client = MagicMock()

    def late_success(**_kwargs):
        started.set()
        release.wait(timeout=2.0)
        worker_finished.set()
        return {"model": "late-model"}

    client.react.side_effect = late_success

    def caller() -> None:
        try:
            bot._run_ot3(client, model="requested-model", events=[], deadline=time.monotonic() + 2.0)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=caller)
    thread.start()
    try:
        assert started.wait(timeout=1.0)
        executor.next_generation()
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], OnlineInferenceCancelled)
        assert [event["phase"] for event in events] == ["requesting", "error"]

        release.set()
        assert worker_finished.wait(timeout=1.0)
        assert [event["phase"] for event in events] == ["requesting", "error"]
    finally:
        release.set()
        thread.join(timeout=1.0)
        executor.close()


def test_ot3_client_applies_socks_proxy_and_ignores_environment() -> None:
    client = OT3Client("https://server.example", "key", "socks5h://127.0.0.1:7890")

    assert client.session.trust_env is False
    assert client.session.proxies == {
        "http": "socks5h://127.0.0.1:7890",
        "https": "socks5h://127.0.0.1:7890",
    }


def test_ot3_client_error_does_not_expose_key() -> None:
    client = OT3Client("http://server.example", "never-log-this-key")
    client.session.post = MagicMock(side_effect=requests.ConnectionError("transport exploded"))
    with pytest.raises(RuntimeError) as exc_info:
        client.react(player_id=0, events=[], status=BotStatusContext())
    assert "never-log-this-key" not in str(exc_info.value)
    assert client.circuit_open is True


def test_ot3_client_sends_optional_model_and_skips_calls_during_backoff() -> None:
    client = OT3Client("http://server.example", "key")
    client.session.post = MagicMock(side_effect=requests.ConnectionError("offline"))

    with pytest.raises(RuntimeError):
        client.react(
            player_id=1,
            events=[],
            status=BotStatusContext(),
            model="ot3-3p",
        )
    with pytest.raises(RuntimeError, match="circuit breaker"):
        client.react(
            player_id=1,
            events=[],
            status=BotStatusContext(),
            model="ot3-3p",
        )

    client.session.post.assert_called_once()
    assert client.session.post.call_args.kwargs["json"]["model"] == "ot3-3p"


def test_build_ot3_events_censors_hidden_information() -> None:
    start_game = StartGameEvent(id=0, is_3p=False)
    events = build_ot3_events(
        start_game,
        [_start_kyoku(), TsumoEvent(actor=2, pai="9p")],
        player_id=0,
        is_3p=False,
    )
    assert events[0] == {"type": "start_game", "names": ["", "", "", ""]}
    assert events[1]["tehais"][0] == ["1m"] * 13
    assert events[1]["tehais"][1] == ["?"] * 13
    assert events[2]["pai"] == "?"
    assert "sync" not in events[2]


def test_build_ot3_events_pads_three_player_shape() -> None:
    events = build_ot3_events(
        StartGameEvent(id=1, is_3p=True),
        [_start_kyoku(is_3p=True)],
        player_id=1,
        is_3p=True,
    )
    assert len(events[1]["scores"]) == 4
    assert events[1]["scores"][3] == 0
    assert len(events[1]["tehais"]) == 4
    assert events[1]["tehais"][3] == ["?"] * 13


def test_validate_ot3_response_rejects_malformed_candidates() -> None:
    with pytest.raises(OT3ProtocolError):
        validate_ot3_response({"reaction": None, "candidates": [{"prob": 1.0}]})
    with pytest.raises(OT3ProtocolError):
        validate_ot3_response(
            {
                "reaction": {"type": "dahai"},
                "candidates": [{"action": "dahai:1m", "prob": float("nan")}],
            }
        )


def test_apply_player_actor_is_defensive() -> None:
    reaction = apply_player_actor({"type": "pon", "actor": 3, "pai": "E"}, 1)
    assert reaction["actor"] == 1
