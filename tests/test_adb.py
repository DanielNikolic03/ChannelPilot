"""ADB discovery/lifecycle/recovery without talking to the user's ADB server."""

import asyncio
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from channelpilot.core.adb import AdbController, CommandResult, parse_mdns
from channelpilot.core.config import AdbConfig
from channelpilot.core.errors import (
    AdbError,
    AdbPairingRequired,
    AdbUnavailable,
    CalibrationRequired,
    RemoteUnavailable,
)

HOST = "192.168.0.115"
PACKAGE = "com.mtssi.supernovabih"
RECORD = f"adb-2A20105GN4CCJH-2CkQNv\t_adb-tls-connect._tcp\t{HOST}:41191\n"


class ScriptedAdb(AdbController):
    def __init__(self, remote, **settings):
        config = AdbConfig(
            command_timeout=0.1,
            discovery_timeout=0.01,
            recovery_timeout=0.08,
            poll_interval=0.001,
            recovery_cooldown=60,
        )
        super().__init__(replace(config, **settings), HOST, remote)
        self.calls = []
        self.records = "List of discovered mdns services\n" + RECORD
        self.needs_menu = False
        self.auth_failure = False
        self.launch_output = "Status: ok\nActivity: " + PACKAGE + "/.MainActivity\n"
        self.resolve_output = PACKAGE + "/.MainActivity\n"
        self.old_endpoint = None
        self.time_out_old = False
        self.hide_after_menu = False

    async def _run(self, *args, input_text=None):
        self.calls.append(args)
        if args == ("mdns", "services"):
            if self.needs_menu and "DPAD_CENTER" not in self.remote.commands:
                return CommandResult(0, "List of discovered mdns services\n")
            return CommandResult(0, "" if self.hide_after_menu else self.records)
        if args[0] == "connect":
            if args[1] == self.old_endpoint:
                if self.time_out_old:
                    raise TimeoutError
                return CommandResult(1, "failed to connect: Connection refused")
            if self.auth_failure:
                return CommandResult(1, "failed to authenticate to " + args[1])
            return CommandResult(0, "connected to " + args[1])
        if args[-1] == "get-state":
            return CommandResult(0, "device\n")
        if "resolve-activity" in args:
            return CommandResult(0, self.resolve_output)
        if "start" in args:
            return CommandResult(0, self.launch_output)
        if "force-stop" in args:
            return CommandResult(0)
        if args[0] == "pair":
            assert input_text == "123456\n"
            return CommandResult(0, "Successfully paired to " + args[1])
        raise AssertionError(args)


def test_parse_actual_mdns_sample_and_exclude_pairing():
    output = (
        "List of discovered mdns services\n"
        + RECORD
        + f"pair _adb-tls-pairing._tcp {HOST}:40000\n"
        + f"legacy _adb._tcp {HOST}:5555\n"
        + f"invalid _adb-tls-connect._tcp {HOST}:99999\n"
        + "bad _adb-tls-connect._tcp not-an-ip:1234\n"
        + "bad record\n"
    )
    services = parse_mdns(output)
    assert len(services) == 1
    assert services[0].name == "adb-2A20105GN4CCJH-2CkQNv"
    assert services[0].endpoint == HOST + ":41191"


async def test_only_connects_to_configured_chromecast(remote):
    adb = ScriptedAdb(remote)
    adb.records = (
        "phone _adb-tls-connect._tcp 192.168.0.120:45678\n"
        + f"pair _adb-tls-pairing._tcp {HOST}:40000\n"
        + RECORD
    )
    await adb.ensure_connected()
    assert adb.connected
    assert adb.endpoint == HOST + ":41191"
    assert [call for call in adb.calls if call[0] == "connect"] == [("connect", HOST + ":41191")]
    assert remote.commands == []


@pytest.mark.parametrize("time_out", [False, True])
async def test_changed_port_is_discovered_before_touching_menu(remote, time_out):
    adb = ScriptedAdb(remote)
    adb.endpoint = adb.old_endpoint = HOST + ":30000"
    adb.time_out_old = time_out
    await adb.ensure_connected()
    assert adb.endpoint == HOST + ":41191"
    assert remote.commands == []


async def test_connection_failure_enables_menu_then_rediscovers(remote):
    adb = ScriptedAdb(
        remote,
        recovery_calibrated=True,
        recovery_steps=[
            {"press": "SETTINGS"},
            {"press": "DPAD_CENTER"},
        ],
    )
    adb.needs_menu = True
    await adb.ensure_connected()
    assert remote.commands == ["HOME", "SETTINGS", "DPAD_CENTER"]
    assert adb.connected
    assert adb.calls[-1] == ("-s", HOST + ":41191", "get-state")


async def test_recovery_sequence_cannot_repeat_on_retry(remote):
    adb = ScriptedAdb(
        remote,
        recovery_calibrated=True,
        recovery_steps=[
            {"press": "SETTINGS"},
            {"press": "DPAD_CENTER"},
        ],
    )
    adb.hide_after_menu = True
    with pytest.raises(AdbUnavailable, match="timed out"):
        await adb.ensure_connected()
    with pytest.raises(AdbUnavailable, match="cooldown"):
        await adb.ensure_connected()
    assert remote.commands == ["HOME", "SETTINGS", "DPAD_CENTER"]
    assert not adb.connected


async def test_no_invented_menu_steps_when_uncalibrated(remote):
    adb = ScriptedAdb(remote)
    adb.records = ""
    with pytest.raises(CalibrationRequired, match="adb.recovery_steps"):
        await adb.ensure_connected()
    assert remote.commands == []


async def test_authorization_failure_does_not_try_menu_recovery(remote):
    adb = ScriptedAdb(remote, recovery_calibrated=True, recovery_steps=[{"press": "SETTINGS"}])
    adb.auth_failure = True
    with pytest.raises(AdbPairingRequired, match="--adb-pair"):
        await adb.ensure_connected()
    assert remote.commands == []
    assert not adb.connected


async def test_offline_remote_prevents_recovery_keys(remote):
    adb = ScriptedAdb(remote, recovery_calibrated=True, recovery_steps=[{"press": "SETTINGS"}])
    adb.records = ""
    remote.set_connection(False)
    with pytest.raises(RemoteUnavailable, match="offline"):
        await adb.ensure_connected()
    assert remote.commands == []


async def test_launch_resolves_tv_activity_and_starts_fresh(remote):
    adb = ScriptedAdb(remote)
    await adb.launch(PACKAGE)
    assert any("android.intent.category.LEANBACK_LAUNCHER" in call for call in adb.calls)
    assert adb.calls[-1] == (
        "-s",
        HOST + ":41191",
        "shell",
        "am",
        "start",
        "-S",
        "-W",
        "--user",
        "current",
        "-n",
        PACKAGE + "/.MainActivity",
    )
    assert remote.commands == []  # No remote-protocol link launch, HOME, or ADB input commands.
    await adb.force_stop(PACKAGE)
    assert adb.calls[-1] == (
        "-s",
        HOST + ":41191",
        "shell",
        "am",
        "force-stop",
        "--user",
        "current",
        PACKAGE,
    )


async def test_standard_launcher_fallback(remote):
    adb = ScriptedAdb(remote)
    original = adb._run

    async def run(*args, **kwargs):
        if "android.intent.category.LEANBACK_LAUNCHER" in args:
            return CommandResult(0, "No activity found")
        return await original(*args, **kwargs)

    adb._run = run
    await adb.launch(PACKAGE)
    assert any("android.intent.category.LAUNCHER" in call for call in adb.calls)


async def test_explicit_activity_and_launch_errors(remote):
    adb = ScriptedAdb(remote)
    await adb.launch(PACKAGE, PACKAGE + "/.activities.MainActivity")
    assert not any("resolve-activity" in call for call in adb.calls)
    adb.launch_output = "Status: timeout"
    with pytest.raises(AdbError, match="successful activity"):
        await adb.launch(PACKAGE, PACKAGE + "/.activities.MainActivity")
    adb.launch_output = "Error: Activity class does not exist"
    with pytest.raises(AdbError, match="command failed"):
        await adb.launch(PACKAGE, PACKAGE + "/.activities.MainActivity")


async def test_missing_launcher_is_reported(remote):
    adb = ScriptedAdb(remote)
    adb.resolve_output = "No activity found"
    with pytest.raises(AdbError, match="No launcher activity"):
        await adb.launch(PACKAGE)


@pytest.mark.parametrize("package", ["com.test;reboot", "com.test\nreboot", "--help", "test://app"])
async def test_shell_arguments_are_not_injectable(remote, package):
    adb = ScriptedAdb(remote)
    with pytest.raises(ValueError):
        await adb.launch(package)
    assert adb.calls == []


async def test_pairing_keeps_code_out_of_arguments_and_logs(remote, caplog):
    adb = ScriptedAdb(remote)
    await adb.pair(40000, AsyncMock(return_value="123456"))
    assert adb.calls == [("pair", HOST + ":40000")]
    assert "123456" not in caplog.text


async def test_shutdown_does_not_enable_wireless_debugging(remote):
    adb = ScriptedAdb(remote)
    adb.records = ""
    with pytest.raises(AdbUnavailable, match="recovery disabled"):
        await adb.force_stop(PACKAGE, recover=False)
    assert remote.commands == []


@pytest.mark.parametrize("cancel", [False, True])
async def test_subprocess_timeout_or_cancellation_reaps_child(remote, monkeypatch, cancel):
    original = asyncio.create_subprocess_exec
    children = []
    started = asyncio.Event()

    async def subprocess(*args, **kwargs):
        child = await original(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", subprocess)
    adb = AdbController(AdbConfig(command_timeout=0.02), HOST, remote)
    task = asyncio.create_task(adb._run("mdns", "services"))
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert children[0].returncode is not None


async def test_missing_adb_is_configuration_error(remote, tmp_path):
    adb = AdbController(AdbConfig(executable=str(tmp_path / "missing-adb")), HOST, remote)
    with pytest.raises(AdbError, match="Install Android SDK Platform Tools"):
        await adb.ensure_connected()
    assert remote.commands == []


@pytest.mark.parametrize(
    "settings",
    [
        {"port": 0},
        {"server_port": True},
        {"poll_interval": 0},
        {"recovery_calibrated": "true"},
        {"recovery_calibrated": True},
        {"recovery_steps": [{"press": "INVALID"}]},
    ],
)
def test_adb_config_rejects_invalid_values(settings):
    with pytest.raises(ValueError):
        AdbConfig.load(settings)


async def test_new_port_is_tried_before_stale_mdns_record(remote):
    adb = ScriptedAdb(remote)
    adb.endpoint = adb.old_endpoint = HOST + ":30000"
    adb.records = f"stale _adb-tls-connect._tcp {HOST}:30000\n" + RECORD
    await adb.ensure_connected()
    connects = [call for call in adb.calls if call[0] == "connect"]
    assert connects == [("connect", HOST + ":30000"), ("connect", HOST + ":41191")]


async def test_slow_failed_recovery_cools_down_from_end(remote, monkeypatch):
    adb = ScriptedAdb(remote, recovery_calibrated=True, recovery_steps=[{"press": "SETTINGS"}])
    adb.records = ""
    stamp = [100.0]
    monkeypatch.setattr("channelpilot.core.adb.time", SimpleNamespace(monotonic=lambda: stamp[0]))
    # The scripted discovery raises immediately after the menu; simulate a long operation.
    original = adb._discover_until

    async def discover():
        if remote.commands:
            stamp[0] = 200.0
            raise TimeoutError
        return await original()

    adb._discover_until = discover
    with pytest.raises(AdbUnavailable):
        await adb.ensure_connected()
    assert adb._last_recovery == 200.0
    with pytest.raises(AdbUnavailable, match="cooldown"):
        await adb.ensure_connected()
    assert remote.commands == ["HOME", "SETTINGS"]


@pytest.mark.parametrize(
    "label",
    [
        "topResumedActivity=",
        "mResumedActivity:",
        "ResumedActivity:",
        "mCurrentFocus=",
        "mFocusedApp=",
    ],
)
def test_parse_foreground_metadata_variants(label):
    from channelpilot.core.adb import parse_foreground_app

    output = (
        "mLastPausedActivity: ActivityRecord{old u0 old.app/.Main t1}\n"
        + "  "
        + label
        + "ActivityRecord{abc u0 "
        + PACKAGE
        + "/.MainActivity t2}\n"
    )
    assert parse_foreground_app(output) == PACKAGE
    assert (
        parse_foreground_app("mLastPausedActivity: ActivityRecord{old u0 old.app/.Main t1}") is None
    )
    assert parse_foreground_app("mCurrentFocus=null\n") is None


async def test_foreground_query_uses_activity_then_window_metadata(remote):
    adb = ScriptedAdb(remote)
    original = adb._run
    queries = []

    async def run(*args, **kwargs):
        if "dumpsys" in args:
            queries.append(args[-2:])
            if args[-2:] == ("activity", "activities"):
                return CommandResult(0, "mResumedActivity=null")
            return CommandResult(0, f"mCurrentFocus=Window{{abc u0 {PACKAGE}/.MainActivity}}")
        return await original(*args, **kwargs)

    adb._run = run
    assert await adb.foreground_app() == PACKAGE
    assert queries == [("activity", "activities"), ("window", "windows")]
    assert remote.commands == []


async def test_foreground_query_never_starts_settings_recovery(remote):
    adb = ScriptedAdb(remote, recovery_calibrated=True, recovery_steps=[{"press": "SETTINGS"}])
    adb.records = ""
    with pytest.raises(AdbUnavailable, match="recovery disabled"):
        await adb.foreground_app()
    assert remote.commands == []
