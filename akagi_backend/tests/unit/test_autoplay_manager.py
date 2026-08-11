import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from akagi_ng.autoplay.executor import WindowGeometry, WindowsInputExecutor
from akagi_ng.autoplay.manager import AutoPlayManager, AutoPlayRuntime
from akagi_ng.autoplay.planner import PlannedClick
from akagi_ng.schema.constants import Platform
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.settings import local_settings


def _runtime(step_provider=lambda: 41) -> AutoPlayRuntime:
    return AutoPlayRuntime(
        platform=Platform.MAJSOUL,
        window_keyword="mahjong soul",
        get_operation_list=lambda: [{"type": 7, "combination": []}],
        get_operation_step=step_provider,
    )


def _executor() -> MagicMock:
    executor = MagicMock()
    executor.ensure_target_window.return_value = True
    executor.focus_target_window.return_value = True
    executor.get_target_geometry.return_value = WindowGeometry(0, 0, 1600, 900)
    executor.normalized_to_screen.return_value = (800, 700)
    executor.move_to.return_value = True
    executor.click_with_retry.return_value = True
    return executor


def test_reach_intermediate_button_does_not_wait_for_operation_clear_before_discard():
    executor = _executor()
    manager = AutoPlayManager(lambda: _runtime(), executor=executor)
    manager._sleep = MagicMock(return_value=True)
    plan = [
        PlannedClick(
            coord=(8.0, 7.0),
            delay=0,
            label="reach",
            expected_types=(7,),
            requires_operation_step=True,
            verify_operation_clear=False,
        ),
        PlannedClick(
            coord=(5.0, 8.0),
            delay=0,
            label="reach-discard",
            expected_types=(1,),
            requires_operation_step=True,
        ),
    ]

    with patch.object(local_settings.autoplay, "enabled", True):
        manager._run_plan(plan, _runtime(), 41, threading.Event())

    assert executor.click_with_retry.call_count == 2
    first_kwargs = executor.click_with_retry.call_args_list[0].kwargs
    second_kwargs = executor.click_with_retry.call_args_list[1].kwargs
    assert first_kwargs["get_operation_step"] is None
    assert second_kwargs["get_operation_step"] is not None
    assert second_kwargs["expected_step"] == 41


def test_operation_step_change_during_cursor_move_aborts_stale_click():
    executor = _executor()
    steps = iter([41, 42])
    runtime = _runtime(lambda: next(steps))
    manager = AutoPlayManager(lambda: runtime, executor=executor)
    manager._sleep = MagicMock(return_value=True)
    plan = [PlannedClick(coord=(5.0, 8.0), delay=0, label="discard", expected_types=(1,))]

    with patch.object(local_settings.autoplay, "enabled", True):
        manager._run_plan(plan, runtime, 41, threading.Event())

    executor.click_with_retry.assert_not_called()


def test_disabling_autoplay_cancels_pending_sleep_immediately():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())

    with patch.object(local_settings.autoplay, "enabled", False):
        assert manager._sleep(1.0, threading.Event()) is False


def test_click_retry_verifies_operation_step_transition():
    executor = WindowsInputExecutor()
    executor.left_click = MagicMock()
    executor._user32 = MagicMock()

    with patch("akagi_ng.autoplay.executor.time.sleep"):
        result = executor.click_with_retry(
            (100, 100),
            (1,),
            None,
            get_operation_step=lambda: 12,
            expected_step=11,
            cancel_requested=lambda: False,
        )

    assert result is True
    executor.left_click.assert_called_once_with()


def test_click_retry_does_not_claim_success_while_step_is_unchanged():
    executor = WindowsInputExecutor()
    executor.left_click = MagicMock()
    executor._user32 = MagicMock()

    with patch("akagi_ng.autoplay.executor.time.sleep"):
        result = executor.click_with_retry(
            (100, 100),
            (1,),
            None,
            get_operation_step=lambda: 11,
            expected_step=11,
            cancel_requested=lambda: False,
        )

    assert result is False
    assert executor.left_click.call_count == 2


def test_window_selection_never_falls_back_to_an_unrelated_single_window():
    executor = WindowsInputExecutor()
    executor._get_windows = MagicMock(return_value=[SimpleNamespace(hwnd=99, name="Unrelated document")])

    selected = executor._auto_select_window(Platform.MAJSOUL)

    assert selected is None
    assert executor._target_hwnd is None


def test_client_connection_never_schedules_auto_join_before_lobby_is_ready():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)
    manager._auto_join.stop = MagicMock()

    manager.observe_system_event(NotificationCode.CLIENT_CONNECTED)

    manager._auto_join.schedule.assert_not_called()
    manager._auto_join.stop.assert_called_once_with()


def test_lobby_ready_schedules_initial_auto_join_once():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)

    manager.observe_system_event(NotificationCode.LOBBY_READY)
    manager.observe_system_event(NotificationCode.LOBBY_READY)

    manager._auto_join.schedule.assert_called_once_with(initial_lobby=True)


def test_matching_started_confirms_pending_auto_join():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.notify_matching_started = MagicMock()
    manager._lobby_ready_seen = True

    manager.observe_system_event(NotificationCode.MATCHING_STARTED)

    manager._auto_join.notify_matching_started.assert_called_once_with()
    assert manager._lobby_ready_seen is False
    assert manager._matching_active is True


def test_lobby_ready_does_not_restart_auto_join_while_matching():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)

    manager.observe_system_event(NotificationCode.MATCHING_STARTED)
    manager.observe_system_event(NotificationCode.LOBBY_READY)

    manager._auto_join.schedule.assert_not_called()


def test_lobby_ready_is_ignored_during_game_and_round_result():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)

    manager.observe_event(SimpleNamespace(type="start_kyoku"))
    manager.observe_event(SimpleNamespace(type="end_kyoku"))
    manager.observe_system_event(NotificationCode.LOBBY_READY)

    manager._auto_join.schedule.assert_not_called()


def test_completed_game_limit_stops_at_exact_boundary_and_deduplicates_end_game():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)
    manager._auto_join.stop = MagicMock()

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch.object(local_settings.autoplay.auto_join, "game_limit", 2),
    ):
        manager.observe_event(SimpleNamespace(type="start_game"))
        manager.observe_event(SimpleNamespace(type="end_game"))
        manager.observe_event(SimpleNamespace(type="end_game"))
        manager.observe_event(SimpleNamespace(type="start_game"))
        manager._auto_join.stop.reset_mock()
        manager.observe_event(SimpleNamespace(type="end_game"))

    manager._auto_join.schedule.assert_called_once_with()
    manager._auto_join.stop.assert_called_once_with()
    assert manager._completed_games == 2


def test_zero_completed_game_limit_remains_unlimited():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch.object(local_settings.autoplay.auto_join, "game_limit", 0),
    ):
        for _ in range(3):
            manager.observe_event(SimpleNamespace(type="start_game"))
            manager.observe_event(SimpleNamespace(type="end_game"))

    assert manager._auto_join.schedule.call_count == 3
    assert manager._completed_games == 3


def test_initial_lobby_does_not_consume_completed_game_limit():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        manager.observe_system_event(NotificationCode.LOBBY_READY)

    manager._auto_join.schedule.assert_called_once_with(initial_lobby=True)
    assert manager._completed_games == 0


def test_lobby_ready_does_not_restart_auto_join_after_limit_is_reached():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)
    manager._completed_games = 1

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch.object(local_settings.autoplay.auto_join, "game_limit", 1),
    ):
        manager.observe_system_event(NotificationCode.LOBBY_READY)

    manager._auto_join.schedule.assert_not_called()


def test_reenabling_auto_join_resets_limit_and_resumes_known_lobby():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)
    manager._auto_join.stop = MagicMock()
    manager._completed_games = 3
    manager._lobby_ready_seen = True

    with patch.object(local_settings.autoplay.auto_join, "enabled", False):
        manager.on_auto_join_settings_changed(was_enabled=True)
    manager._auto_join.stop.assert_called_once_with()

    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        manager.on_auto_join_settings_changed(was_enabled=False)

    assert manager._completed_games == 0
    manager._auto_join.schedule.assert_called_once_with(initial_lobby=True)


def test_raising_completed_game_limit_resumes_known_lobby():
    manager = AutoPlayManager(lambda: _runtime(), executor=_executor())
    manager._auto_join.schedule = MagicMock(return_value=True)
    manager._completed_games = 2
    manager._lobby_ready_seen = True

    with (
        patch.object(local_settings.autoplay.auto_join, "enabled", True),
        patch.object(local_settings.autoplay.auto_join, "game_limit", 3),
    ):
        manager.on_auto_join_settings_changed(was_enabled=True, previous_game_limit=2)

    manager._auto_join.schedule.assert_called_once_with(initial_lobby=True)


def test_disabled_or_non_majsoul_games_do_not_consume_limit():
    disabled = AutoPlayManager(lambda: _runtime(), executor=_executor())
    disabled._auto_join.schedule = MagicMock(return_value=True)
    with patch.object(local_settings.autoplay.auto_join, "enabled", False):
        disabled.observe_event(SimpleNamespace(type="start_game"))
        disabled.observe_event(SimpleNamespace(type="end_game"))

    other_platform_runtime = _runtime()
    other_platform_runtime.platform = Platform.TENHOU
    other_platform = AutoPlayManager(lambda: other_platform_runtime, executor=_executor())
    other_platform._auto_join.schedule = MagicMock(return_value=True)
    with patch.object(local_settings.autoplay.auto_join, "enabled", True):
        other_platform.observe_event(SimpleNamespace(type="start_game"))
        other_platform.observe_event(SimpleNamespace(type="end_game"))

    assert disabled._completed_games == 0
    assert other_platform._completed_games == 0
    disabled._auto_join.schedule.assert_not_called()
    other_platform._auto_join.schedule.assert_not_called()
