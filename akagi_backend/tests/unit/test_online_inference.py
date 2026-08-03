import statistics
import threading
import time

import pytest

from akagi_ng.application import AkagiApp
from akagi_ng.mjai_bot.online_inference import (
    OnlineInferenceBusy,
    OnlineInferenceCancelled,
    OnlineInferenceDeadlineExceeded,
    OnlineInferenceExecutor,
)


def _start_blocked_call(
    executor: OnlineInferenceExecutor,
    release: threading.Event,
    *,
    deadline_seconds: float = 10.0,
) -> tuple[threading.Thread, threading.Event, list[BaseException]]:
    started = threading.Event()
    errors: list[BaseException] = []

    def operation() -> None:
        started.set()
        release.wait(timeout=deadline_seconds)

    def caller() -> None:
        try:
            executor.run(operation, deadline=time.monotonic() + deadline_seconds)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=caller)
    thread.start()
    assert started.wait(timeout=1.0)
    return thread, started, errors


def test_online_inference_runs_off_the_caller_thread() -> None:
    executor = OnlineInferenceExecutor()
    try:
        caller_name = threading.current_thread().name
        worker_name = executor.run(lambda: threading.current_thread().name, deadline=time.monotonic() + 1.0)

        assert worker_name.startswith("akagi-online-inference-")
        assert worker_name != caller_name
    finally:
        executor.close()


def test_online_inference_deadline_abandons_uncooperative_operation() -> None:
    executor = OnlineInferenceExecutor()
    release = threading.Event()
    started = time.monotonic()
    try:
        with pytest.raises(OnlineInferenceDeadlineExceeded):
            executor.run(lambda: release.wait(timeout=5.0), deadline=time.monotonic() + 0.05)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        executor.close()


def test_online_inference_generation_discards_running_result() -> None:
    executor = OnlineInferenceExecutor()
    release = threading.Event()
    caller, _started, errors = _start_blocked_call(executor, release)
    try:
        executor.next_generation()
        caller.join(timeout=0.5)

        assert not caller.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], OnlineInferenceCancelled)
    finally:
        release.set()
        executor.close()


def test_online_inference_capacity_is_bounded() -> None:
    executor = OnlineInferenceExecutor(max_workers=1, max_pending=0)
    release = threading.Event()
    caller, _started, _errors = _start_blocked_call(executor, release)
    try:
        with pytest.raises(OnlineInferenceBusy):
            executor.run(lambda: None, deadline=time.monotonic() + 1.0)
    finally:
        executor.next_generation()
        caller.join(timeout=0.5)
        release.set()
        executor.close()


def test_network_blackhole_shutdown_p95_stays_below_two_seconds() -> None:
    latencies: list[float] = []

    for _index in range(30):
        app = AkagiApp()
        release = threading.Event()
        caller, _started, errors = _start_blocked_call(app.online_executor, release)

        started = time.monotonic()
        app.stop()
        caller.join(timeout=2.0)
        latencies.append(time.monotonic() - started)

        assert not caller.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], OnlineInferenceCancelled)
        release.set()
        app.online_executor.close()

    p95 = statistics.quantiles(latencies, n=100, method="inclusive")[94]
    assert p95 < 2.0
