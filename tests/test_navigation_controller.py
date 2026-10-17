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
    return Controller(remote, channels, build_apps(remote, app_configs, 1), **kwargs)


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
    apps = build_apps(remote, app_configs, 1)
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


async def test_launch_fallback_and_missing_strategy(remote, app_configs):
    app_configs["move"]["launch_link"] = None
    app_configs["move"]["launch_steps"] = [{"press": "DPAD_CENTER"}]
    app = build_apps(remote, app_configs, 1)["move"]
    await app.open()
    assert remote.commands == ["HOME", "DPAD_CENTER"]
    app.config["launch_steps"] = []
    with pytest.raises(CalibrationRequired):
        await app.open()


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
    assert remote.commands[:3] == ["HOME", "HOME", "HOME"]
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
    assert remote.commands == ["HOME", "HOME"]
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
    assert remote.commands.count("launch:test://app") == 2
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
    assert remote.commands == ["HOME", "HOME", "SLEEP"]
    assert controller.state.status == Status.ERROR
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_unexpected_exception_is_contained(remote, channels, app_configs):
    controller = make_controller(remote, channels, app_configs)
    original = controller.apps["move"].open

    async def broken():
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
