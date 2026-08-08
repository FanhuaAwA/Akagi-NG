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

# Temporarily disabled until the lobby/result-screen detector can prove the
# current Mahjong Soul UI state without relying on coordinates alone.
AUTO_JOIN_AVAILABLE = False

# Coordinates use the same 16:9 client-area space as in-game autoplay. They
# were calibrated against the Mahjong Soul desktop client and therefore remain
# stable when the game window is resized or letterboxed.
RANKED_LOBBY = (11.20, 2.80)
ROOM_SCROLL_ANCHOR = (12.20, 5.40)
INITIAL_LOBBY_DELAY = 7.0
RESULT_CONFIRM_ATTEMPTS = 3
RESULT_BUTTON_FIRST_TIMEOUT = 60.0
RESULT_BUTTON_NEXT_TIMEOUT = 8.0
RESULT_BUTTON_POLL_INTERVAL = 0.35
RESULT_BUTTON_STABILITY_TOLERANCE = 5
RESULT_BUTTON_STABILITY_HITS = 2
ROOM_COORDS: dict[str, tuple[float, float]] = {
    "copper": (11.55, 3.37),
    "silver": (11.55, 4.77),
    "gold": (11.55, 6.19),
    "jade": (11.55, 7.59),
    # Throne is selected after the room list has been scrolled to its bottom.
    "throne": (11.55, 6.65),
}
MODE_COORDS: dict[str, tuple[float, float]] = {
    "four_east": (11.55, 3.37),
    "four_south": (11.55, 4.77),
    "three_east": (11.55, 6.19),
    "three_south": (11.55, 7.59),
}


class AutoJoinManager:
    """Drive the post-game Mahjong Soul menus with guarded coordinate input."""

    def __init__(self, runtime_provider: RuntimeProvider, executor: WindowsInputExecutor):
        self._runtime_provider = runtime_provider
        self._executor = executor
        self._task: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def schedule(self, *, initial_lobby: bool = False) -> bool:
        config = local_settings.autoplay.auto_join
        if not AUTO_JOIN_AVAILABLE:
            if config.enabled:
                logger.warning("Auto join is temporarily disabled; ignoring the scheduled run.")
            return False
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

    def stop(self) -> None:
        with self._lock:
            if self._task and self._task.is_alive():
                self._stop_event.set()
                if self._task is not threading.current_thread():
                    self._task.join(timeout=0.2)
            self._task = None

    def _run(self, runtime: object, stop_event: threading.Event, initial_lobby: bool) -> None:
        config = local_settings.autoplay.auto_join
        if initial_lobby:
            if not self._sleep(INITIAL_LOBBY_DELAY, stop_event):
                return
        elif not self._leave_result_screens(runtime, float(config.result_delay), stop_event):
            return
        if not self._open_ranked_lobby(runtime, stop_event):
            return
        self._select_target(runtime, stop_event)

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
            target = self._executor.find_enabled_gold_button(geometry)
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
            if self._executor.find_enabled_gold_button(geometry) is None:
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
        if config.room == "throne" and not self._reveal_throne(stop_event):
            return False

        if not self._click(runtime, room_coord, f"room-{config.room}", stop_event):
            return False
        if not self._sleep(1.8, stop_event):
            return False
        if not self._click(runtime, mode_coord, f"mode-{config.mode}", stop_event):
            return False
        logger.info(f"Auto join submitted room={config.room} mode={config.mode}.")
        return True

    def _reveal_throne(self, stop_event: threading.Event) -> bool:
        if not self._executor.scroll_vertical(-12):
            logger.warning("Auto join aborted: failed to reveal Throne room.")
            return False
        return self._sleep(0.7, stop_event)

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
        self._executor.left_click()
        return True

    def _sleep(self, delay: float, stop_event: threading.Event) -> bool:
        deadline = time.monotonic() + max(delay, 0.0)
        while time.monotonic() < deadline:
            if stop_event.wait(timeout=0.05) or not local_settings.autoplay.auto_join.enabled:
                return False
        return not self._cancel_requested(stop_event)

    @staticmethod
    def _cancel_requested(stop_event: threading.Event) -> bool:
        return not AUTO_JOIN_AVAILABLE or stop_event.is_set() or not local_settings.autoplay.auto_join.enabled
