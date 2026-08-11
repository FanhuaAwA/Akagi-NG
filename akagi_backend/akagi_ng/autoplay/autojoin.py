from __future__ import annotations

import threading
import time
from collections.abc import Callable

from akagi_ng.autoplay.executor import WindowsInputExecutor
from akagi_ng.core.logging import logger as base_logger
from akagi_ng.schema.constants import Platform
from akagi_ng.settings import local_settings

logger = base_logger.bind(module="autojoin")

RuntimeProvider = Callable[[], object]

# Coordinates use the same 16:9 client-area space as in-game autoplay. They
# are resolved against the letterboxed game area by WindowsInputExecutor.
RANKED_LOBBY = (11.4, 2.6)
ROOM_SCROLL_ANCHOR = (11.6, 5.0)
# Calibrated center of the enabled gold result confirmation button.
RESULT_CONFIRM_COORD = (14.57, 8.18)
LOBBY_READY_TIMEOUT = 35.0
LOBBY_READY_POLL_INTERVAL = 0.4
LOBBY_READY_STABILITY_HITS = 2
LOBBY_RENDER_DELAY = 4.0
MATCH_CONFIRM_TIMEOUT = 15.0
RESULT_CONFIRM_ATTEMPTS = 3
RESULT_BUTTON_FIRST_TIMEOUT = 60.0
RESULT_BUTTON_NEXT_TIMEOUT = 8.0
RESULT_BUTTON_POLL_INTERVAL = 0.35
RESULT_BUTTON_STABILITY_TOLERANCE = 5
RESULT_BUTTON_STABILITY_HITS = 2
ROOM_COORDS: dict[str, tuple[float, float]] = {
    "copper": (11.6, 3.4),
    "silver": (11.6, 4.9),
    "gold": (11.6, 6.3),
    "jade": (11.6, 5.3),
    "throne": (11.6, 6.7),
}
MODE_COORDS: dict[str, tuple[float, float]] = {
    "four_east": (11.6, 3.4),
    "four_south": (11.6, 4.9),
    "three_east": (11.6, 6.3),
    "three_south": (11.6, 7.3),
}


class AutoJoinManager:
    """Drive the post-game Mahjong Soul menus with guarded coordinate input."""

    def __init__(self, runtime_provider: RuntimeProvider, executor: WindowsInputExecutor):
        self._runtime_provider = runtime_provider
        self._executor = executor
        self._task: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._matching_event = threading.Event()
        self._lock = threading.Lock()

    def schedule(self, *, initial_lobby: bool = False) -> bool:
        config = local_settings.autoplay.auto_join
        if not config.enabled:
            return False

        runtime = self._runtime_provider()
        if getattr(runtime, "platform", None) != Platform.MAJSOUL:
            logger.warning("Auto join is only available for Mahjong Soul.")
            return False
        if not self._executor.available:
            logger.warning("Auto join requested but Windows input executor is unavailable.")
            return False

        self.stop()
        self._stop_event = threading.Event()
        self._matching_event.clear()
        self._task = threading.Thread(
            target=self._run,
            args=(runtime, self._stop_event, initial_lobby),
            name="AutoJoinTask",
            daemon=True,
        )
        self._task.start()
        if initial_lobby:
            logger.info("Auto join scheduled after the initial lobby finishes loading.")
        else:
            logger.info("Auto join scheduled after the game result animation.")
        return True

    def notify_matching_started(self) -> None:
        """Release protocol confirmation wait as soon as matchmaking is observed."""
        self._matching_event.set()

    def stop(self) -> None:
        with self._lock:
            if self._task and self._task.is_alive():
                self._stop_event.set()
                if self._task is not threading.current_thread():
                    self._task.join(timeout=0.2)
            self._task = None

    def _run(self, runtime: object, stop_event: threading.Event, initial_lobby: bool) -> None:
        config = local_settings.autoplay.auto_join
        if not initial_lobby and not self._leave_result_screens(runtime, float(config.result_delay), stop_event):
            return
        if not self._wait_for_lobby_ready(runtime, LOBBY_READY_TIMEOUT, stop_event):
            logger.warning("Auto join aborted: the Mahjong Soul target window did not become stable.")
            return
        if not self._open_ranked_lobby(runtime, stop_event):
            return
        if not self._select_target(runtime, stop_event):
            return
        if not self._wait_for_matching_confirmation(MATCH_CONFIRM_TIMEOUT, stop_event):
            logger.warning("Auto join failed: no matchmaking request was observed after the click chain.")
            return
        logger.info("Auto join confirmed by the Mahjong Soul matchmaking protocol.")

    def _wait_for_lobby_ready(self, runtime: object, timeout: float, stop_event: threading.Event) -> bool:
        platform = getattr(runtime, "platform", Platform.MAJSOUL)
        window_keyword = getattr(runtime, "window_keyword", "")
        # The login/result protocol event is authoritative, but it can arrive a
        # few seconds before the lobby has finished rendering. Require both
        # stable client geometry and two independent lobby chrome anchors before
        # allowing any fixed-coordinate input. Unknown UI revisions fail closed.
        if not self._sleep(LOBBY_RENDER_DELAY, stop_event):
            return False
        deadline = time.monotonic() + max(0.0, timeout)
        stable_hits = 0
        previous_geometry: tuple[int, int, int, int] | None = None
        while time.monotonic() < deadline:
            if self._cancel_requested(stop_event):
                return False
            if not self._executor.ensure_target_window(platform, window_keyword):
                stable_hits = 0
                previous_geometry = None
            else:
                geometry = self._executor.get_target_geometry()
                if geometry is not None and self._executor.probe_lobby_ready(geometry):
                    geometry_signature = (
                        geometry.left,
                        geometry.top,
                        geometry.width,
                        geometry.height,
                    )
                    stable_hits = stable_hits + 1 if geometry_signature == previous_geometry else 1
                    previous_geometry = geometry_signature
                    if stable_hits >= LOBBY_READY_STABILITY_HITS:
                        logger.info("Auto join lobby geometry and visual anchors are stable.")
                        return True
                else:
                    stable_hits = 0
                    previous_geometry = None
            if not self._sleep(LOBBY_READY_POLL_INTERVAL, stop_event):
                return False
        return False

    def _wait_for_matching_confirmation(self, timeout: float, stop_event: threading.Event) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._matching_event.is_set():
                return True
            if self._cancel_requested(stop_event):
                return False
            self._matching_event.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        return self._matching_event.is_set()

    def _leave_result_screens(  # noqa: PLR0911
        self, runtime: object, delay: float, stop_event: threading.Event
    ) -> bool:
        # NotifyGameEndResult arrives before the visual result animation and
        # before the confirmation button is unlocked. Never click a guessed
        # coordinate: wait for a stable, rendered gold button and use its real
        # center. A locked grey button is intentionally not detected.
        if not self._sleep(delay, stop_event):
            return False
        for attempt in range(1, RESULT_CONFIRM_ATTEMPTS + 1):
            timeout = RESULT_BUTTON_FIRST_TIMEOUT if attempt == 1 else RESULT_BUTTON_NEXT_TIMEOUT
            target = self._wait_for_enabled_result_button(runtime, timeout, stop_event)
            if target is None:
                if attempt > 1:
                    logger.info("No further enabled result confirmation button; result flow completed.")
                    return self._sleep(4.5, stop_event)
                logger.warning("Auto join aborted: result confirmation never became visually enabled.")
                return False
            if not self._click_screen(runtime, target, f"result-confirm-{attempt}", stop_event):
                return False
            if attempt < RESULT_CONFIRM_ATTEMPTS and not self._sleep(1.2, stop_event):
                return False
        if not self._wait_for_result_button_clear(runtime, RESULT_BUTTON_NEXT_TIMEOUT, stop_event):
            logger.warning("Auto join aborted: result screen did not advance after confirmation clicks.")
            return False
        return self._sleep(4.5, stop_event)

    def _wait_for_enabled_result_button(
        self, runtime: object, timeout: float, stop_event: threading.Event
    ) -> tuple[int, int] | None:
        platform = getattr(runtime, "platform", Platform.MAJSOUL)
        window_keyword = getattr(runtime, "window_keyword", "")
        deadline = time.monotonic() + max(0.0, timeout)
        previous: tuple[int, int] | None = None
        stable_hits = 0
        while time.monotonic() < deadline:
            if self._cancel_requested(stop_event):
                return None
            if not self._executor.ensure_target_window(platform, window_keyword):
                return None
            geometry = self._executor.get_target_geometry()
            if geometry is None:
                return None
            target = self._executor.find_enabled_gold_button(geometry, expected_coord=RESULT_CONFIRM_COORD)
            if target is not None and previous is not None:
                if (
                    abs(target[0] - previous[0]) <= RESULT_BUTTON_STABILITY_TOLERANCE
                    and abs(target[1] - previous[1]) <= RESULT_BUTTON_STABILITY_TOLERANCE
                ):
                    stable_hits += 1
                else:
                    stable_hits = 1
                if stable_hits >= RESULT_BUTTON_STABILITY_HITS:
                    return target
            else:
                stable_hits = 1 if target is not None else 0
            previous = target
            if not self._sleep(RESULT_BUTTON_POLL_INTERVAL, stop_event):
                return None
        return None

    def _click_screen(
        self,
        runtime: object,
        target: tuple[int, int],
        label: str,
        stop_event: threading.Event,
    ) -> bool:
        if self._cancel_requested(stop_event):
            return False
        platform = getattr(runtime, "platform", Platform.MAJSOUL)
        window_keyword = getattr(runtime, "window_keyword", "")
        if not self._executor.ensure_target_window(platform, window_keyword):
            return False
        if not self._executor.move_to(target, cancel_requested=lambda: self._cancel_requested(stop_event)):
            return False
        if self._cancel_requested(stop_event) or not self._executor.focus_target_window():
            return False
        if self._cancel_requested(stop_event):
            return False
        logger.info(f"Auto join input label={label} detected_screen={target}.")
        self._executor.left_click()
        return True

    def _wait_for_result_button_clear(self, runtime: object, timeout: float, stop_event: threading.Event) -> bool:
        platform = getattr(runtime, "platform", Platform.MAJSOUL)
        window_keyword = getattr(runtime, "window_keyword", "")
        deadline = time.monotonic() + max(0.0, timeout)
        clear_hits = 0
        while time.monotonic() < deadline:
            if self._cancel_requested(stop_event):
                return False
            if not self._executor.ensure_target_window(platform, window_keyword):
                return False
            geometry = self._executor.get_target_geometry()
            if geometry is None:
                return False
            if self._executor.find_enabled_gold_button(geometry, expected_coord=RESULT_CONFIRM_COORD) is None:
                clear_hits += 1
                if clear_hits >= RESULT_BUTTON_STABILITY_HITS:
                    return True
            else:
                clear_hits = 0
            if not self._sleep(RESULT_BUTTON_POLL_INTERVAL, stop_event):
                return False
        return False

    def _open_ranked_lobby(self, runtime: object, stop_event: threading.Event) -> bool:
        return self._click(runtime, RANKED_LOBBY, "ranked-lobby", stop_event) and self._sleep(2.2, stop_event)

    def _select_target(self, runtime: object, stop_event: threading.Event) -> bool:  # noqa: PLR0911
        # Read the choices at execution time so settings changed while the
        # result screen is open take effect immediately.
        config = local_settings.autoplay.auto_join
        room_coord = ROOM_COORDS.get(config.room)
        mode_coord = MODE_COORDS.get(config.mode)
        if room_coord is None or mode_coord is None:
            logger.error(f"Invalid auto join target room={config.room!r} mode={config.mode!r}.")
            return False

        if not self._move(runtime, ROOM_SCROLL_ANCHOR, "room-scroll-anchor", stop_event):
            return False
        if not self._executor.scroll_vertical(12):
            logger.warning("Auto join aborted: failed to reset the ranked-room list.")
            return False
        if not self._sleep(0.7, stop_event):
            return False
        if config.room in {"jade", "throne"} and not self._reveal_lower_rooms(stop_event):
            return False

        if not self._click(runtime, room_coord, f"room-{config.room}", stop_event):
            return False
        if not self._sleep(1.4, stop_event):
            return False
        if not self._click(runtime, mode_coord, f"mode-{config.mode}", stop_event):
            return False
        logger.info(f"Auto join submitted room={config.room} mode={config.mode}.")
        return True

    def _reveal_lower_rooms(self, stop_event: threading.Event) -> bool:
        if not self._executor.scroll_vertical(-12):
            logger.warning("Auto join aborted: failed to reveal the lower ranked rooms.")
            return False
        return self._sleep(0.6, stop_event)

    def _move(  # noqa: PLR0911
        self,
        runtime: object,
        coord: tuple[float, float],
        label: str,
        stop_event: threading.Event,
    ) -> bool:
        if self._cancel_requested(stop_event):
            return False
        platform = getattr(runtime, "platform", Platform.MAJSOUL)
        window_keyword = getattr(runtime, "window_keyword", "")
        if not self._executor.ensure_target_window(platform, window_keyword):
            logger.warning(f"Auto join aborted: target window unavailable before {label}.")
            return False
        geometry = self._executor.get_target_geometry()
        if geometry is None:
            logger.warning(f"Auto join aborted: invalid target geometry before {label}.")
            return False
        target = self._executor.normalized_to_screen(geometry, coord)
        if not self._executor.move_to(target, cancel_requested=lambda: self._cancel_requested(stop_event)):
            logger.info(f"Auto join cancelled while moving to {label}.")
            return False
        if self._cancel_requested(stop_event):
            return False
        if not self._executor.focus_target_window():
            logger.warning(f"Auto join aborted: target window could not be focused before {label}.")
            return False
        logger.info(f"Auto join input label={label} coord={coord} screen={target}.")
        return True

    def _click(
        self,
        runtime: object,
        coord: tuple[float, float],
        label: str,
        stop_event: threading.Event,
    ) -> bool:
        if not self._move(runtime, coord, label, stop_event):
            return False
        if self._cancel_requested(stop_event):
            return False
        self._executor.left_click()
        return True

    def _sleep(self, delay: float, stop_event: threading.Event) -> bool:
        deadline = time.monotonic() + max(delay, 0.0)
        while time.monotonic() < deadline:
            if (
                stop_event.wait(timeout=0.05)
                or self._matching_event.is_set()
                or not local_settings.autoplay.auto_join.enabled
            ):
                return False
        return not self._cancel_requested(stop_event)

    def _cancel_requested(self, stop_event: threading.Event) -> bool:
        return stop_event.is_set() or self._matching_event.is_set() or not local_settings.autoplay.auto_join.enabled
