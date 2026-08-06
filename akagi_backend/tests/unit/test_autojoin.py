import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from akagi_ng.autoplay.autojoin import (
    INITIAL_LOBBY_DELAY,
    MODE_COORDS,
    RESULT_CONFIRM_ATTEMPTS,
    ROOM_COORDS,
    AutoJoinManager,
)
from akagi_ng.autoplay.executor import WindowGeometry, WindowsInputExecutor
from akagi_ng.schema.constants import Platform
from akagi_ng.settings import local_settings


def _runtime(platform: Platform = Platform.MAJSOUL) -> SimpleNamespace:
    return SimpleNamespace(platform=platform, window_keyword="mahjong soul")


def test_auto_join_is_hard_disabled_even_when_stale_settings_enable_it():
    runtime_provider = MagicMock(return_value=_runtime())
    executor = MagicMock(available=True)
    manager = AutoJoinManager(runtime_provider, executor)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        assert manager.schedule() is False

    assert manager._task is None
    runtime_provider.assert_not_called()
    executor.ensure_target_window.assert_not_called()


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


def test_non_throne_room_only_resets_scroll_to_top():
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

    executor.scroll_vertical.assert_called_once_with(12)
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


def test_initial_lobby_flow_skips_result_screen_confirmations():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._sleep = MagicMock(return_value=True)
    manager._leave_result_screens = MagicMock(return_value=True)
    manager._open_ranked_lobby = MagicMock(return_value=True)
    manager._select_target = MagicMock(return_value=True)
    runtime = _runtime()
    stop_event = threading.Event()

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        manager._run(runtime, stop_event, initial_lobby=True)

    manager._sleep.assert_called_once_with(INITIAL_LOBBY_DELAY, stop_event)
    manager._leave_result_screens.assert_not_called()
    manager._open_ranked_lobby.assert_called_once_with(runtime, stop_event)
    manager._select_target.assert_called_once_with(runtime, stop_event)


def test_result_wait_advances_every_bottom_right_confirmation_layer():
    manager = AutoJoinManager(lambda: _runtime(), MagicMock())
    manager._sleep = MagicMock(return_value=True)
    manager._wait_for_enabled_result_button = MagicMock(
        side_effect=[(1740, 1000), (1740, 1000), (1740, 1000)]
    )
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
