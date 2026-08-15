import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from akagi_ng.autoplay.autojoin import (
    MODE_COORDS,
    RESULT_CONFIRM_ATTEMPTS,
    ROOM_COORDS,
    AutoJoinManager,
)
from akagi_ng.autoplay.executor import LOBBY_ANCHORS, WindowGeometry, WindowsInputExecutor
from akagi_ng.schema.constants import Platform
from akagi_ng.settings import local_settings


def _runtime(platform: Platform = Platform.MAJSOUL) -> SimpleNamespace:
    return SimpleNamespace(platform=platform, window_keyword="mahjong soul")


def test_auto_join_can_schedule_for_majsoul_when_enabled():
    runtime_provider = MagicMock(return_value=_runtime())
    executor = MagicMock(available=True)
    manager = AutoJoinManager(runtime_provider, executor)

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch("akagi_ng.autoplay.autojoin.threading.Thread") as thread_factory,
    ):
        task = thread_factory.return_value
        assert manager.schedule() is True

    runtime_provider.assert_called_once_with()
    task.start.assert_called_once_with()


def test_throne_room_scrolls_from_a_known_top_position_before_mode_selection():
    executor = MagicMock()
    executor.scroll_vertical.return_value = True
    manager = AutoJoinManager(lambda: _runtime(), executor)
    manager._sleep = MagicMock(return_value=True)
    manager._move = MagicMock(return_value=True)
    manager._click = MagicMock(return_value=True)

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch.object(local_settings.autoplay.auto_join, "room", "throne"),
        patch.object(local_settings.autoplay.auto_join, "mode", "three_south"),
    ):
        assert manager._select_target(_runtime(), threading.Event()) is True

    assert executor.scroll_vertical.call_args_list == [call(12), call(-12)]
    assert manager._click.call_args_list[-2].args[1] == ROOM_COORDS["throne"]
    assert manager._click.call_args_list[-1].args[1] == MODE_COORDS["three_south"]


def test_jade_room_reveals_lower_room_rows_before_selection():
    executor = MagicMock()
    executor.scroll_vertical.return_value = True
    manager = AutoJoinManager(lambda: _runtime(), executor)
    manager._sleep = MagicMock(return_value=True)
    manager._move = MagicMock(return_value=True)
    manager._click = MagicMock(return_value=True)

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch.object(local_settings.autoplay.auto_join, "room", "jade"),
        patch.object(local_settings.autoplay.auto_join, "mode", "four_east"),
    ):
        assert manager._select_target(_runtime(), threading.Event()) is True

    assert executor.scroll_vertical.call_args_list == [call(12), call(-12)]
    assert manager._click.call_args_list[-2].args[1] == ROOM_COORDS["jade"]
    assert manager._click.call_args_list[-1].args[1] == MODE_COORDS["four_east"]


def test_auto_join_rejects_other_platforms_without_starting_a_thread():
    executor = MagicMock()
    executor.available = True
    manager = AutoJoinManager(lambda: _runtime(Platform.TENHOU), executor)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager.schedule() is False

    assert manager._task is None
    executor.ensure_target_window.assert_not_called()


def test_disabling_auto_join_cancels_wait_immediately():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())

    with patch.object(local_settings.autoplay.auto_join, "enabled", False):
        assert manager._sleep(1.0, threading.Event()) is False


def test_visual_guard_finds_enabled_gold_button_center():
    executor = WindowsInputExecutor()
    geometry = WindowGeometry(left=100, top=200, width=800, height=450)
    pixels = bytearray(geometry.width * geometry.height * 4)
    for y in range(350, 405):
        for x in range(600, 750):
            offset = (y * geometry.width + x) * 4
            pixels[offset : offset + 4] = bytes((80, 190, 240, 0))
    executor._capture_target_bgra = MagicMock(return_value=(bytes(pixels), geometry.width, 0, 0))

    target = executor.find_enabled_gold_button(geometry)

    assert target is not None
    assert abs(target[0] - 774) <= 3
    assert abs(target[1] - 577) <= 3


def test_visual_guard_rejects_locked_grey_button():
    executor = WindowsInputExecutor()
    geometry = WindowGeometry(left=100, top=200, width=800, height=450)
    pixels = bytearray(geometry.width * geometry.height * 4)
    for y in range(350, 405):
        for x in range(600, 750):
            offset = (y * geometry.width + x) * 4
            pixels[offset : offset + 4] = bytes((110, 110, 110, 0))
    executor._capture_target_bgra = MagicMock(return_value=(bytes(pixels), geometry.width, 0, 0))

    assert executor.find_enabled_gold_button(geometry) is None


def test_visual_guard_uses_calibrated_target_and_ignores_other_gold_components():
    executor = WindowsInputExecutor()
    geometry = WindowGeometry(left=100, top=200, width=800, height=450)
    pixels = bytearray(geometry.width * geometry.height * 4)
    # A larger unrelated gold decoration above the actual confirmation button.
    for y in range(280, 340):
        for x in range(560, 760):
            offset = (y * geometry.width + x) * 4
            pixels[offset : offset + 4] = bytes((80, 190, 240, 0))
    # Enabled confirmation button surrounding the measured click center.
    for y in range(380, 435):
        for x in range(650, 790):
            offset = (y * geometry.width + x) * 4
            pixels[offset : offset + 4] = bytes((80, 190, 240, 0))
    executor._capture_target_bgra = MagicMock(return_value=(bytes(pixels), geometry.width, 0, 0))

    target = executor.find_enabled_gold_button(geometry, expected_coord=(14.57, 8.18))

    assert target is not None
    assert abs(target[0] - 820) <= 5
    assert abs(target[1] - 607) <= 5


def _lobby_capture(*, include_jade: bool = True) -> tuple[bytes, int, int, int]:
    geometry = WindowGeometry(left=100, top=200, width=1600, height=900)
    pixels = bytearray(geometry.width * geometry.height * 4)
    scale = 100.0
    for index, (x, y, width, height, red, green, blue) in enumerate(LOBBY_ANCHORS):
        if index == 1 and not include_jade:
            continue
        left = int((x - width / 2.0) * scale)
        top = int((y - height / 2.0) * scale)
        right = int((x + width / 2.0) * scale) + 1
        bottom = int((y + height / 2.0) * scale) + 1
        for pixel_y in range(top, bottom):
            for pixel_x in range(left, right):
                offset = (pixel_y * geometry.width + pixel_x) * 4
                pixels[offset : offset + 4] = bytes((blue, green, red, 0))
    return bytes(pixels), geometry.width, 0, 0


def test_lobby_probe_requires_both_independent_anchors():
    executor = WindowsInputExecutor()
    geometry = WindowGeometry(left=100, top=200, width=1600, height=900)
    executor._capture_target_bgra = MagicMock(return_value=_lobby_capture())

    assert executor.probe_lobby_ready(geometry) is True

    executor._capture_target_bgra = MagicMock(return_value=_lobby_capture(include_jade=False))
    assert executor.probe_lobby_ready(geometry) is False


def test_initial_lobby_flow_skips_result_screen_confirmations():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._leave_result_screens = MagicMock(return_value=True)
    manager._wait_for_lobby_ready = MagicMock(return_value=True)
    manager._open_ranked_lobby = MagicMock(return_value=True)
    manager._select_target = MagicMock(return_value=True)
    manager._wait_for_matching_confirmation = MagicMock(return_value=True)
    runtime = _runtime()
    stop_event = threading.Event()

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        manager._run(runtime, stop_event, initial_lobby=True)

    manager._leave_result_screens.assert_not_called()
    manager._wait_for_lobby_ready.assert_called_once()
    manager._open_ranked_lobby.assert_called_once_with(runtime, stop_event)
    manager._select_target.assert_called_once_with(runtime, stop_event)


def test_lobby_readiness_requires_stable_geometry_and_visual_anchors():
    executor = MagicMock(available=True)
    executor.ensure_target_window.return_value = True
    executor.get_target_geometry.return_value = WindowGeometry(left=100, top=200, width=1600, height=900)
    executor.probe_lobby_ready.return_value = True
    manager = AutoJoinManager(lambda: _runtime(), executor)
    manager._sleep = MagicMock(return_value=True)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._wait_for_lobby_ready(_runtime(), 1.0, threading.Event()) is True

    assert executor.ensure_target_window.call_count == 2
    assert executor.probe_lobby_ready.call_count == 2


def test_lobby_readiness_fails_closed_when_visual_anchors_do_not_match():
    executor = MagicMock(available=True)
    executor.ensure_target_window.return_value = True
    executor.get_target_geometry.return_value = WindowGeometry(left=100, top=200, width=1600, height=900)
    executor.probe_lobby_ready.return_value = False
    manager = AutoJoinManager(lambda: _runtime(), executor)
    sleep_calls = 0

    def bounded_sleep(_delay, _stop_event):
        nonlocal sleep_calls
        sleep_calls += 1
        return sleep_calls < 4

    manager._sleep = bounded_sleep

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._wait_for_lobby_ready(_runtime(), 1.0, threading.Event()) is False

    executor.probe_lobby_ready.assert_called()


def test_lobby_readiness_coordinate_fallback_passes_when_anchors_drift():
    executor = MagicMock(available=True)
    executor.ensure_target_window.return_value = True
    executor.get_target_geometry.return_value = WindowGeometry(left=100, top=200, width=1600, height=900)
    executor.probe_lobby_ready.return_value = False
    manager = AutoJoinManager(lambda: _runtime(), executor)
    sleep_calls = 0

    def bounded_sleep(_delay, _stop_event):
        nonlocal sleep_calls
        sleep_calls += 1
        return True

    manager._sleep = bounded_sleep

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._wait_for_lobby_ready(_runtime(), 5.0, threading.Event()) is True

    # The anchor grace window must be crossed before the coordinate fallback.
    assert executor.probe_lobby_ready.call_count == 8


def test_matchmaking_notification_releases_protocol_confirmation_wait():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager.notify_matching_started()

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._wait_for_matching_confirmation(0.1, threading.Event()) is True


def test_matchmaking_notification_cancels_all_remaining_coordinate_input():
    executor = MagicMock(available=True)
    manager = AutoJoinManager(lambda: _runtime(), executor)
    manager.notify_matching_started()

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._click(_runtime(), (11.6, 4.9), "mode-four-south", threading.Event()) is False

    executor.move_to.assert_not_called()
    executor.left_click.assert_not_called()


def test_result_wait_advances_every_bottom_right_confirmation_layer():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._sleep = MagicMock(return_value=True)
    manager._wait_for_enabled_result_button = MagicMock(side_effect=[(1740, 1000), (1740, 1000), (1740, 1000)])
    manager._wait_for_result_button_clear = MagicMock(return_value=True)
    manager._click_screen = MagicMock(return_value=True)
    runtime = _runtime()
    stop_event = threading.Event()

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._leave_result_screens(runtime, 10.0, stop_event) is True

    assert manager._click_screen.call_count == RESULT_CONFIRM_ATTEMPTS
    assert [item.args[2] for item in manager._click_screen.call_args_list] == [
        f"result-confirm-{attempt}" for attempt in range(1, RESULT_CONFIRM_ATTEMPTS + 1)
    ]


def test_result_flow_never_clicks_when_button_is_still_locked():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._sleep = MagicMock(return_value=True)
    manager._wait_for_enabled_result_button = MagicMock(return_value=None)
    manager._wait_for_result_button_clear = MagicMock(return_value=True)
    manager._click_screen = MagicMock(return_value=True)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._leave_result_screens(_runtime(), 10.0, threading.Event()) is False

    manager._click_screen.assert_not_called()


def test_result_flow_can_finish_after_last_visible_confirmation():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._sleep = MagicMock(return_value=True)
    manager._wait_for_enabled_result_button = MagicMock(side_effect=[(1740, 1000), None])
    manager._wait_for_result_button_clear = MagicMock(return_value=True)
    manager._click_screen = MagicMock(return_value=True)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._leave_result_screens(_runtime(), 10.0, threading.Event()) is True

    manager._click_screen.assert_called_once()


def test_result_flow_aborts_if_confirmation_clicks_never_leave_result_screen():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._sleep = MagicMock(return_value=True)
    manager._wait_for_enabled_result_button = MagicMock(return_value=(1740, 1000))
    manager._wait_for_result_button_clear = MagicMock(return_value=False)
    manager._click_screen = MagicMock(return_value=True)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager._leave_result_screens(_runtime(), 10.0, threading.Event()) is False

    assert manager._click_screen.call_count == RESULT_CONFIRM_ATTEMPTS
