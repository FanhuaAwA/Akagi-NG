from unittest.mock import patch

import pytest

from akagi_ng.mjai_bot.status import BotStatusContext


def test_inference_tracker_emits_requesting_and_success_with_exact_latency():
    events = []
    status = BotStatusContext()
    status.set_inference_listener(events.append)

    with (
        patch("akagi_ng.mjai_bot.status.time.time", return_value=1_700_000_000.25),
        patch("akagi_ng.mjai_bot.status.time.monotonic", side_effect=[10.0, 10.1234]),
        status.track_inference("FutureProvider", "requested-model") as attempt,
    ):
        attempt.set_model("returned-model")

    assert [event["phase"] for event in events] == ["requesting", "success"]
    assert events[0]["request_id"] == events[1]["request_id"]
    assert events[0]["started_at_ms"] == 1_700_000_000_250
    assert events[0]["elapsed_ms"] == 0
    assert events[1]["elapsed_ms"] == 123
    assert events[1]["provider"] == "FutureProvider"
    assert events[1]["model"] == "returned-model"


def test_inference_tracker_emits_error_without_exposing_exception_details():
    events = []
    status = BotStatusContext()
    status.set_inference_listener(events.append)

    with (
        pytest.raises(RuntimeError, match="sensitive backend detail"),
        status.track_inference("OT3"),
    ):
        raise RuntimeError("sensitive backend detail")

    assert [event["phase"] for event in events] == ["requesting", "error"]
    assert "sensitive backend detail" not in str(events)


def test_inference_listener_failure_never_changes_request_result():
    status = BotStatusContext()
    status.set_inference_listener(lambda _event: (_ for _ in ()).throw(OSError("transport down")))

    with status.track_inference("AnyProvider"):
        pass
