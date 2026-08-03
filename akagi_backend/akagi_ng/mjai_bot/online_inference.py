"""Bounded, interruptible execution for blocking online inference calls."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, TypeVar, cast

T = TypeVar("T")


class OnlineInferenceError(RuntimeError):
    """Base class for safe online inference scheduling failures."""


class OnlineInferenceBusy(OnlineInferenceError):
    """The bounded worker and pending queue are both occupied."""


class OnlineInferenceCancelled(OnlineInferenceError):
    """The application stopped or the inference generation was superseded."""


class OnlineInferenceDeadlineExceeded(OnlineInferenceError):
    """The end-to-end decision deadline elapsed."""


@dataclass(slots=True)
class _InferenceJob:
    generation: int
    deadline: float
    operation: Callable[[], Any]
    future: Future[Any]


_STOP = object()


class OnlineInferenceExecutor:
    """Run blocking HTTP inference on a small daemon worker pool.

    Capacity covers both running and queued jobs. Callers wait interruptibly, so
    an uncooperative socket can be abandoned without holding the application
    shutdown path. Generation changes make late results from an old game or
    configuration unusable.
    """

    def __init__(
        self,
        *,
        max_workers: int = 1,
        max_pending: int = 1,
        cancel_event: threading.Event | None = None,
        poll_interval: float = 0.025,
        thread_name_prefix: str = "akagi-online-inference",
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or max_pending < 0:
            raise ValueError("max_pending must be a non-negative integer")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")

        self._max_workers = max_workers
        self._cancel_event = cancel_event or threading.Event()
        self._poll_interval = poll_interval
        self._thread_name_prefix = thread_name_prefix
        self._jobs: queue.Queue[_InferenceJob | object] = queue.Queue(maxsize=max_workers + max_pending)
        self._capacity = threading.BoundedSemaphore(max_workers + max_pending)
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False
        self._started = False
        self._threads: list[threading.Thread] = []
        self._futures: dict[Future[Any], int] = {}

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def next_generation(self) -> int:
        """Cancel queued work and invalidate any running result."""
        with self._lock:
            if self._closed:
                return self._generation
            self._generation += 1
            generation = self._generation
            stale = [future for future, job_generation in self._futures.items() if job_generation != generation]
        for future in stale:
            future.cancel()
        return generation

    def run(
        self,
        operation: Callable[[], T],
        *,
        deadline: float,
        generation: int | None = None,
    ) -> T:
        """Run one operation before an absolute ``time.monotonic`` deadline."""
        if not callable(operation):
            raise TypeError("operation must be callable")
        if deadline <= time.monotonic():
            raise OnlineInferenceDeadlineExceeded("online inference deadline exceeded")

        future, expected_generation = self._submit(operation, deadline, generation)
        return self._await_result(future, deadline, expected_generation)

    def _submit(
        self,
        operation: Callable[[], T],
        deadline: float,
        generation: int | None,
    ) -> tuple[Future[T], int]:
        """Reserve bounded capacity and enqueue one generation-scoped job."""

        with self._lock:
            if self._closed or self._cancel_event.is_set():
                raise OnlineInferenceCancelled("online inference is shutting down")
            expected_generation = self._generation if generation is None else generation
            if expected_generation != self._generation:
                raise OnlineInferenceCancelled("online inference generation was superseded")
            self._start_workers_locked()

        if not self._capacity.acquire(blocking=False):
            raise OnlineInferenceBusy("online inference worker capacity is exhausted")

        future: Future[T] = Future()
        future.add_done_callback(self._release_capacity)
        with self._lock:
            cancelled_before_submission = (
                self._closed or self._cancel_event.is_set() or expected_generation != self._generation
            )
            if not cancelled_before_submission:
                self._futures[cast(Future[Any], future)] = expected_generation
        if cancelled_before_submission:
            future.cancel()
            raise OnlineInferenceCancelled("online inference was cancelled before submission")

        try:
            self._jobs.put_nowait(
                _InferenceJob(
                    generation=expected_generation,
                    deadline=deadline,
                    operation=operation,
                    future=cast(Future[Any], future),
                )
            )
        except queue.Full:
            future.cancel()
            raise OnlineInferenceBusy("online inference queue is full") from None

        return future, expected_generation

    def _await_result(self, future: Future[T], deadline: float, expected_generation: int) -> T:
        """Wait in short intervals so shutdown and generation changes win."""
        while True:
            if self._cancel_event.is_set():
                future.cancel()
                raise OnlineInferenceCancelled("online inference cancelled by application shutdown")
            if expected_generation != self.generation:
                future.cancel()
                raise OnlineInferenceCancelled("online inference generation was superseded")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise OnlineInferenceDeadlineExceeded("online inference deadline exceeded")
            try:
                return future.result(timeout=min(self._poll_interval, remaining))
            except TimeoutError:
                if future.done():
                    return future.result()

    def close(self, *, join_timeout: float = 0.1) -> None:
        """Cancel pending work without waiting for uncooperative network I/O."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            futures = list(self._futures)
            threads = list(self._threads)
        for future in futures:
            future.cancel()

        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            try:
                if isinstance(job, _InferenceJob):
                    job.future.cancel()
            finally:
                self._jobs.task_done()

        for _thread in threads:
            self._jobs.put_nowait(_STOP)

        deadline = time.monotonic() + max(0.0, join_timeout)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _start_workers_locked(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self._max_workers):
            thread = threading.Thread(
                target=self._worker,
                name=f"{self._thread_name_prefix}-{index}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _worker(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                if job is _STOP:
                    return
                if not isinstance(job, _InferenceJob) or not job.future.set_running_or_notify_cancel():
                    continue
                if self._cancel_event.is_set() or job.generation != self.generation:
                    job.future.set_exception(OnlineInferenceCancelled("online inference was cancelled"))
                    continue
                if time.monotonic() >= job.deadline:
                    job.future.set_exception(OnlineInferenceDeadlineExceeded("online inference deadline exceeded"))
                    continue

                try:
                    result = job.operation()
                except BaseException as exc:
                    job.future.set_exception(exc)
                    continue

                if self._cancel_event.is_set() or job.generation != self.generation:
                    job.future.set_exception(OnlineInferenceCancelled("stale online inference result discarded"))
                elif time.monotonic() >= job.deadline:
                    job.future.set_exception(OnlineInferenceDeadlineExceeded("late online inference result discarded"))
                else:
                    job.future.set_result(result)
            finally:
                self._jobs.task_done()

    def _release_capacity(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.pop(future, None)
        self._capacity.release()
