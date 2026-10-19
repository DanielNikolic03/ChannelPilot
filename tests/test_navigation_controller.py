import asyncio

import pytest

from channelpilot.apps.registry import build_apps
from channelpilot.core.controller import Controller
from channelpilot.core.errors import CalibrationRequired, NavigationError, RemoteUnavailable
from channelpilot.core.navigation import Navigator, validate_steps
from channelpilot.core.state import Confidence, Status


async def drain(controller):
    task = asyncio.create_task(controller.run())
    try:
        await asyncio.wait_for(controller.queue.join(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def make_controller(remote, channels, app_configs, **kwargs):
    return Controller(
        remote, channels, build_apps(remote, app_configs, 1, adb=remote.adb), **kwargs
    )


async def test_sequences_checkpoints_conditions(remote):
    nav = Navigator(remote)
    await nav.run(
        [
            {"press": "DPAD_DOWN", "times": 3},
            {"wait": 0},
            {"press": "BACK", "if_app": "absent"},
            {"press": "DPAD_CENTER", "if_power": True},
            {"checkpoint": "menu presumed"},
        ]
    )
    assert remote.commands == ["DPAD_DOWN"] * 3 + ["DPAD_CENTER"]
    assert nav.checkpoint == "menu presumed"


@pytest.mark.parametrize(
    "steps",
    [
        [{"press": "NOT_A_KEY"}],
        [{"wait": -1}],
        [{"press": "BACK", "times": 1001}],
        [{"press": "BACK", "wait": 1}],
        [{"wait": float("inf")}],
        [{"typo": "BACK"}],
        [{"wait": 1, "if_power": "false"}],
    ],
)
def test_invalid_sequences(steps):
    with pytest.raises(ValueError):
        validate_steps(steps)


async def test_disconnect_reconnect_aborts_old_sequence(remote):
    nav = Navigator(remote)
    remote.set_connection(False)
    remote.set_connection(True)
    with pytest.raises(RemoteUnavailable):
        await nav.press("BACK")
    assert remote.commands == []


async def test_unexpected_app(remote):
    nav = Navigator(remote)
    nav.expected_app = "expected"
    remote.current_app = "other"
    with pytest.raises(NavigationError):
        await nav.press("BACK")


async def test_navigation_timeout(remote):
    nav = Navigator(remote, timeout=0.005)
    with pytest.raises(TimeoutError):
        await nav.run([{"wait": 1}])


async def test_move_and_svt_have_independent_sequences(remote, channels, app_configs):
    apps = build_apps(remote, app_configs, 1, adb=remote.adb)
    await apps["move"].open()
    await apps["move"].navigate_to_channel(channels.by_id["bn-muzika"])
    assert remote.commands.count("DPAD_DOWN") == 6  # Baseline down + five positions.
    assert "DPAD_RIGHT" not in remote.commands
    remote.commands.clear()
    await apps["svt"].open()
    await apps["svt"].navigate_to_channel(channels.by_id["kunskap"])
    assert remote.commands.count("DPAD_RIGHT") == 3
    assert apps["svt"].confidence == Confidence.ASSUMED


async def test_uncalibrated_sends_nothing(remote, channels, app_configs):
    app_configs["move"]["calibrated"] = False
    controller = make_controller(remote, channels, app_configs)
    controller.submit("channel", "rts1")
    await drain(controller)
    assert remote.commands == []
    assert "Calibrate" in controller.state.last_error


async def test_legacy_launcher_steps_require_migration(remote, app_configs):
    app_configs["move"]["launch_steps"] = [{"press": "DPAD_CENTER"}]
    app = build_apps(remote, app_configs, 1, adb=remote.adb)["move"]
    with pytest.raises(CalibrationRequired, match="Old launch_steps"):
        await app.open()
    assert remote.commands == []
    assert remote.adb.commands == []
    # Manual opening remains available for calibration without running old sequences.
    await app.open(prepare=False)
    assert remote.adb.commands == [("start", app.package)]
    assert remote.commands == []


async def test_retry_restarts_whole_sequence(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    remote.failures = 1
    controller.submit("channel", "rts1")
    task = asyncio.create_task(controller.run())
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.status == Status.READY
    assert controller.state.presumed_channel == "rts1"
    assert controller.state.requested_channel == "rts1"
    assert controller.state.last_attempted_channel == "rts1"
    assert controller.state.navigation_confidence == Confidence.ASSUMED
    assert controller.state.sequence_completed
    assert remote.commands[:2] == ["BACK", "HOME"]
    assert len(remote.adb.commands) == 2
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_retry_exhaustion_and_worker_survives(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    remote.failures = 2
    task = asyncio.create_task(controller.run())
    controller.submit("channel", "rts1")
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.status == Status.ERROR
    assert controller.state.presumed_channel is None
    assert len(remote.commands) == 2
    controller.submit("channel", "svt1")
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.status == Status.READY
    assert controller.state.presumed_channel == "svt1"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_timeout_bounded_and_no_mid_sequence_retry(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs, operation_timeout=0.005)
    remote.block = asyncio.Event()
    task = asyncio.create_task(controller.run())
    controller.submit("channel", "rts1")
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.status == Status.ERROR
    assert remote.commands == ["BACK", "HOME"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_latest_wins_and_navigation_never_interleaves(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    remote.block = asyncio.Event()
    task = asyncio.create_task(controller.run())
    controller.submit("channel", "svt1")
    await remote.started.wait()
    for channel in ("bn", "happy", "prva", "rts1"):
        controller.submit("channel", channel)
    assert controller.queue.qsize() == 1
    assert controller.state.requested_channel == "rts1"
    remote.block.set()
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.last_attempted_channel == "rts1"
    assert remote.adb.commands == [
        ("start", app_configs["svt"]["package"]),
        ("stop", app_configs["svt"]["package"]),
        ("start", app_configs["move"]["package"]),
    ]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_standby_replaces_pending_channel(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    controller.submit("channel", "rts1")
    controller.submit("standby")
    task = asyncio.create_task(controller.run())
    await asyncio.wait_for(controller.queue.join(), 1)
    assert remote.commands == ["SLEEP"]
    assert controller.state.status == Status.STANDBY
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_invalidation_on_reported_app_change_and_connection(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    controller.submit("channel", "rts1")
    await asyncio.wait_for(controller.queue.join(), 1)
    remote.set_app("external.application")
    assert controller.state.presumed_channel is None
    assert controller.state.current_app == "external.application"
    assert controller.state.app_confidence == Confidence.CONFIRMED
    remote.set_connection(False)
    assert not controller.state.connected
    assert controller.state.status == Status.CONNECTING
    remote.set_connection(True)
    assert controller.state.status == Status.READY
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_channel_up_down_uses_latest_request(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    controller.handle_input("9")
    controller.handle_input("next")
    assert controller.state.requested_channel == "rts1"
    controller.handle_input("previous")
    assert controller.state.requested_channel == "pink"
    assert controller.queue.qsize() == 1


async def test_standby_failure_does_not_navigate_or_wake(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    remote.failures = 1
    controller.submit("standby")
    task = asyncio.create_task(controller.run())
    await asyncio.wait_for(controller.queue.join(), 1)
    assert remote.commands == ["SLEEP", "SLEEP"]
    assert controller.state.status == Status.STANDBY
    assert controller.state.last_error is None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_failure_standby_is_optional_and_bounded(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs, standby_on_failure=True)
    remote.failures = 2
    controller.submit("channel", "rts1")
    task = asyncio.create_task(controller.run())
    await asyncio.wait_for(controller.queue.join(), 1)
    assert remote.commands == ["BACK", "HOME", "SLEEP"]
    assert controller.state.status == Status.ERROR
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_unexpected_exception_is_contained(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    original = controller.apps["move"].open

    async def broken(**kwargs):
        raise RuntimeError("unexpected application error")

    controller.apps["move"].open = broken
    task = asyncio.create_task(controller.run())
    controller.submit("channel", "rts1")
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.status == Status.ERROR
    assert "RuntimeError" in controller.state.last_error
    controller.apps["move"].open = original
    controller.submit("channel", "rts1")
    await asyncio.wait_for(controller.queue.join(), 1)
    assert controller.state.status == Status.READY
    assert controller.state.last_error is None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_transient_app_change_invalidates_in_progress_navigation(
    remote, channels, app_configs
):
    controller = make_controller(remote, channels, app_configs)
    app = controller.apps["move"]
    await app.open()
    remote.set_app("external.application")
    remote.set_app(app.package)
    with pytest.raises(NavigationError, match="invalidated"):
        await app.navigate_to_channel(channels.by_id["rts1"])
    assert app.presumed_position is None


async def test_open_close_and_standby_use_adb_without_channel_calibration(
    remote, channels, app_configs
):
    app_configs["move"]["calibrated"] = False
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("open", "move")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert controller.state.status == Status.READY
        assert remote.commands == []
        assert controller.state.presumed_channel is None
        controller.submit("standby")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.adb.commands == [
            ("start", app_configs["move"]["package"]),
            ("stop", app_configs["move"]["package"]),
        ]
        assert remote.commands == ["SLEEP"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_force_stops_tracked_app(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    controller.submit("open", "move")
    await drain(controller)
    await controller.close()
    assert remote.adb.commands[-1] == ("stop", app_configs["move"]["package"])


async def test_startup_steps_run_before_reset(remote, channels, app_configs):
    app_configs["move"]["startup_steps"] = [{"press": "MENU"}]
    controller = make_controller(remote, channels, app_configs)
    controller.submit("channel", "rts1")
    await drain(controller)
    assert remote.commands[:2] == ["MENU", "BACK"]


async def test_adb_launch_failure_retries_complete_channel_procedure(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    remote.adb.failures = 1
    controller.submit("channel", "rts1")
    task = asyncio.create_task(controller.run())
    try:
        await asyncio.wait_for(controller.queue.join(), 1)
        assert controller.state.status == Status.READY
        assert controller.state.presumed_channel == "rts1"
        assert remote.commands[0] == "HOME"
        assert remote.adb.commands == [("start", app_configs["move"]["package"])]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_invalid_home_key_has_actionable_message():
    with pytest.raises(ValueError, match="DPAD_HOME.*Use HOME instead"):
        validate_steps([{"press": "DPAD_HOME"}])


@pytest.mark.parametrize(
    "initial,target,expected",
    [
        ("svt1", "svt2", ["DPAD_RIGHT", "DPAD_CENTER"]),
        ("svt2", "kunskap", ["DPAD_RIGHT", "DPAD_RIGHT", "DPAD_CENTER"]),
        ("kunskap", "svt2", ["DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]),
        ("pink", "rts1", ["DPAD_LEFT", "DPAD_UP", "DPAD_CENTER", "BACK"]),
        ("bn", "happy", ["DPAD_LEFT", "DPAD_UP", "DPAD_CENTER", "BACK"]),
    ],
)
async def test_same_app_only_moves_from_previous_presumed_position(
    remote, channels, app_configs, initial, target, expected
):
    for settings in app_configs.values():
        settings["startup_steps"] = [{"press": "MENU"}]
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", initial)
        await asyncio.wait_for(controller.queue.join(), 1)
        remote.commands.clear()
        controller.submit("channel", target)
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == expected
        assert remote.adb.commands == [
            ("start", app_configs[channels.by_id[target].app]["package"])
        ]
        assert controller.state.presumed_channel == target
        assert controller.state.navigation_confidence == Confidence.ASSUMED
        assert controller.state.sequence_completed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reselecting_presumed_channel_sends_no_keys(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "svt2")
        await asyncio.wait_for(controller.queue.join(), 1)
        remote.commands.clear()
        controller.submit("channel", "svt2")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == []
        assert len(remote.adb.commands) == 1
        assert controller.state.sequence_completed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "invalidation",
    ["disconnect", "app-change", "power-cycle", "manual-key", "explicit", "epoch", "known-off"],
)
async def test_invalid_state_requires_fresh_start_and_baseline(
    remote, channels, app_configs, invalidation
):
    app_configs["svt"]["startup_steps"] = [{"press": "MENU"}]
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "svt2")
        await asyncio.wait_for(controller.queue.join(), 1)
        if invalidation == "disconnect":
            remote.set_connection(False)
            remote.set_connection(True)
        elif invalidation == "app-change":
            remote.set_app("other.app")
            remote.set_app(app_configs["svt"]["package"])
        elif invalidation == "power-cycle":
            remote.set_power(False)
            remote.set_power(True)
        elif invalidation == "manual-key":
            controller.submit("key", "BACK")
            await asyncio.wait_for(controller.queue.join(), 1)
        elif invalidation == "explicit":
            controller.invalidate_navigation()
        elif invalidation == "epoch":
            remote.epoch += 1
        else:
            remote.is_on = False
        remote.commands.clear()
        controller.submit("channel", "kunskap")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands.count("DPAD_RIGHT") == 3  # Baseline 1 -> position 4, not 2 -> 4.
        assert "MENU" in remote.commands
        assert len(remote.adb.commands) == 2
        assert controller.state.presumed_channel == "kunskap"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_relative_switch_retries_from_full_launch(remote, channels, app_configs):
    app_configs["svt"]["startup_steps"] = [{"press": "MENU"}]
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "svt1")
        await asyncio.wait_for(controller.queue.join(), 1)
        remote.commands.clear()
        remote.failures = 1
        controller.submit("channel", "svt2")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands[:3] == ["DPAD_RIGHT", "HOME", "MENU"]
        assert len(remote.adb.commands) == 2
        assert controller.state.presumed_channel == "svt2"
        assert controller.state.last_error is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_pending_same_app_request_uses_completed_active_position(
    remote, channels, app_configs
):
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "svt1")
        await asyncio.wait_for(controller.queue.join(), 1)
        remote.commands.clear()
        remote.started.clear()
        remote.block = asyncio.Event()
        controller.submit("channel", "svt2")
        await asyncio.wait_for(remote.started.wait(), 1)
        controller.submit("channel", "svt24")
        controller.submit("channel", "kunskap")
        remote.block.set()
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == [
            "DPAD_RIGHT",
            "DPAD_CENTER",
            "DPAD_RIGHT",
            "DPAD_RIGHT",
            "DPAD_CENTER",
        ]
        assert len(remote.adb.commands) == 1
        assert controller.state.presumed_channel == "kunskap"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_same_app_does_not_require_adb_reconnection(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "svt1")
        await asyncio.wait_for(controller.queue.join(), 1)
        remote.adb.invalidate()
        remote.adb.failures = 1
        controller.submit("channel", "svt2")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert controller.state.presumed_channel == "svt2"
        assert len(remote.adb.commands) == 1
        assert remote.adb.failures == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_startup_can_establish_baseline_without_extra_reset_steps(
    remote, channels, app_configs
):
    app_configs["svt"]["startup_steps"] = [{"press": "MENU"}]
    app_configs["svt"]["reset_steps"] = []
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "svt1")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == ["MENU"]
        assert controller.state.presumed_channel == "svt1"
        controller.submit("channel", "svt2")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == ["MENU", "DPAD_RIGHT", "DPAD_CENTER"]
        assert len(remote.adb.commands) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_missing_baseline_configuration_requires_explicit_decision(remote, app_configs):
    del app_configs["svt"]["reset_steps"]
    app = build_apps(remote, app_configs, 1, adb=remote.adb)["svt"]
    with pytest.raises(CalibrationRequired, match=r"use \[\]"):
        app.require_calibration()


async def test_wake_only_turns_on_device_without_selecting_channel(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    remote.set_power(False)
    controller.handle_input("wake")
    assert controller.queue.qsize() == 1
    task = asyncio.create_task(controller.run())
    try:
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == ["WAKEUP"]
        assert remote.adb.commands == []
        assert controller.state.requested_channel is None
        assert controller.state.status == Status.READY
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failure", ["offline", "timeout"])
async def test_standby_still_sleeps_when_adb_app_close_fails(
    remote, channels, app_configs, failure, caplog
):
    controller = make_controller(remote, channels, app_configs, app_close_timeout=0.005)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("open", "svt")
        await asyncio.wait_for(controller.queue.join(), 1)
        remote.commands.clear()
        recovered = []

        async def stop(package, *, recover=True):
            recovered.append(recover)
            if failure == "timeout":
                await asyncio.Event().wait()
            else:
                from channelpilot.core.errors import AdbUnavailable

                raise AdbUnavailable("offline")

        remote.adb.force_stop = stop
        controller.submit("standby")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == ["SLEEP"]
        assert recovered == [False]  # No Settings recovery menu while trying to sleep.
        assert controller.state.status == Status.STANDBY
        assert "sending standby anyway" in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unconfirmed_toggle_does_not_trigger_automatic_power_retry(
    remote, channels, app_configs
):
    from channelpilot.core.errors import PowerStateUnconfirmed

    controller = make_controller(remote, channels, app_configs)
    attempts = []

    async def standby():
        attempts.append("POWER")
        raise PowerStateUnconfirmed("not confirmed")

    remote.standby = standby
    controller.submit("standby")
    task = asyncio.create_task(controller.run())
    try:
        await asyncio.wait_for(controller.queue.join(), 1)
        assert attempts == ["POWER"]
        assert controller.state.status == Status.ERROR
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_move_navigation_works_with_stale_remote_launcher_report(
    remote, channels, app_configs, caplog
):
    launcher = "com.google.android.apps.tv.launcherx"
    remote.current_app = launcher
    remote.adb.foreground = app_configs["move"]["package"]
    app_configs["move"]["startup_steps"] = [{"press": "MENU"}]
    app_configs["move"]["reset_steps"] = []
    controller = make_controller(remote, channels, app_configs)
    task = asyncio.create_task(controller.run())
    try:
        controller.submit("channel", "rts1")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == ["MENU"]
        assert controller.state.presumed_channel == "rts1"
        assert controller.state.current_app == app_configs["move"]["package"]
        assert controller.state.remote_reported_app == launcher
        assert "ADB confirms" in caplog.text
        # Repeated identical stale reports must not invalidate an ADB-confirmed session.
        remote.set_app(launcher)
        assert controller.state.presumed_channel == "rts1"
        remote.commands.clear()
        # Expire the short cache: the next switch must recheck ADB, not restart MOVE.
        controller.apps["move"].nav._verified_at = float("-inf")
        controller.submit("channel", "pink")
        await asyncio.wait_for(controller.queue.join(), 1)
        assert remote.commands == ["DPAD_LEFT", "DPAD_DOWN", "DPAD_CENTER", "BACK"]
        assert len(remote.adb.commands) == 1
        assert remote.adb.foreground_checks == 2
        assert controller.state.presumed_channel == "pink"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("actual", ["other.app", None])
async def test_foreground_disagreement_is_not_blindly_ignored(remote, actual):
    remote.current_app = "com.google.android.apps.tv.launcherx"
    remote.adb.foreground = actual
    nav = Navigator(remote, foreground_probe=remote.adb.foreground_app)
    nav.expected_app = "com.mtssi.supernovabih"
    with pytest.raises(NavigationError):
        await nav.press("DPAD_CENTER")
    assert remote.commands == []


async def test_adb_foreground_override_expires_and_detects_real_home_transition(remote):
    launcher = "com.google.android.apps.tv.launcherx"
    remote.current_app = launcher
    remote.adb.foreground = "com.mtssi.supernovabih"
    nav = Navigator(remote, foreground_probe=remote.adb.foreground_app)
    nav.expected_app = remote.adb.foreground
    await nav.press("DPAD_LEFT")
    remote.adb.foreground = launcher
    nav._verified_at = float("-inf")
    with pytest.raises(NavigationError, match="Unexpected active application .ADB."):
        await nav.press("DPAD_CENTER")
    assert remote.commands == ["DPAD_LEFT"]


async def test_disconnect_during_foreground_query_aborts_before_keypress(remote):
    remote.current_app = "other.app"

    async def probe():
        remote.set_connection(False)
        remote.set_connection(True)
        return "com.mtssi.supernovabih"

    nav = Navigator(remote, foreground_probe=probe)
    nav.expected_app = "com.mtssi.supernovabih"
    with pytest.raises(RemoteUnavailable):
        await nav.press("DPAD_CENTER")
    assert remote.commands == []


async def test_if_app_uses_adb_verified_foreground(remote):
    remote.current_app = "stale.app"
    remote.adb.foreground = "com.mtssi.supernovabih"
    nav = Navigator(remote, foreground_probe=remote.adb.foreground_app)
    nav.expected_app = remote.adb.foreground
    await nav.run([{"press": "DPAD_CENTER", "if_app": "com.mtssi.supernovabih"}])
    assert remote.commands == ["DPAD_CENTER"]
    assert remote.adb.foreground_checks == 1
