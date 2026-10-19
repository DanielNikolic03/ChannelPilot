"""Transport fake: tests simulate only facts that the protocol actually exposes."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest

from channelpilot.core.channels import Channels
from channelpilot.core.config import Config
from channelpilot.core.errors import AdbUnavailable, RemoteUnavailable


class FakeAdb:
    def __init__(self) -> None:
        self.connected = True
        self.endpoint = "192.0.2.1:41191"
        self.commands: list[tuple[str, str]] = []
        self.failures = 0
        self.foreground: str | None = None
        self.foreground_checks = 0

    def invalidate(self) -> None:
        self.connected = False

    async def ensure_connected(self, *, recover: bool = True) -> None:
        if self.failures:
            self.failures -= 1
            self.connected = False
            raise AdbUnavailable("offline")
        self.connected = True

    async def launch(self, package: str, activity: str | None = None) -> None:
        await self.ensure_connected()
        self.commands.append(("start", package))

    async def force_stop(self, package: str, *, recover: bool = True) -> None:
        await self.ensure_connected(recover=recover)
        self.commands.append(("stop", package))

    async def foreground_app(self) -> str | None:
        self.foreground_checks += 1
        return self.foreground


class FakeRemote:
    def __init__(self) -> None:
        self.connected = True
        self.adb = FakeAdb()
        self.epoch = 0
        self.current_app: str | None = None
        self.is_on: bool | None = True
        self.commands: list[str] = []
        self.availability: list[Callable[[bool], None]] = []
        self.apps: list[Callable[[str], None]] = []
        self.power: list[Callable[[bool], None]] = []
        self.failures = 0
        self.block: asyncio.Event | None = None
        self.started = asyncio.Event()

    def add_availability_callback(self, callback: Callable[[bool], None]) -> None:
        self.availability.append(callback)

    def add_app_callback(self, callback: Callable[[str], None]) -> None:
        self.apps.append(callback)

    def add_power_callback(self, callback: Callable[[bool], None]) -> None:
        self.power.append(callback)

    def set_power(self, is_on: bool) -> None:
        self.is_on = is_on
        for callback in self.power:
            callback(is_on)

    def set_connection(self, available: bool) -> None:
        self.connected = available
        if not available:
            self.epoch += 1
        for callback in self.availability:
            callback(available)

    def set_app(self, package: str) -> None:
        self.current_app = package
        for callback in self.apps:
            callback(package)

    async def wait_ready(self) -> None:
        if not self.connected:
            raise RemoteUnavailable("offline")

    async def press(self, key: str) -> None:
        await self.wait_ready()
        self.commands.append(key)
        self.started.set()
        if self.block:
            await self.block.wait()
        await asyncio.sleep(0)
        if self.failures:
            self.failures -= 1
            raise RemoteUnavailable("transmission failed")

    async def wake(self) -> None:
        await self.wait_ready()
        if self.is_on is not True:
            await self.press("WAKEUP")
            self.is_on = True

    async def standby(self) -> None:
        await self.wait_ready()
        if self.is_on is not False:
            await self.press("SLEEP")
            self.is_on = False


@pytest.fixture
def remote() -> FakeRemote:
    return FakeRemote()


@pytest.fixture
def channels() -> Channels:
    return Channels.load(Path("config/channels.json"))


@pytest.fixture
def app_configs() -> dict:
    settings = deepcopy(Config.load(Path("config/config.example.json")).apps)
    for app in settings.values():
        app.update(
            calibrated=True,
            startup_delay=0,
            channel_delay=0,
        )
        for name in ("reset_steps", "select_prefix", "select_suffix"):
            for step in app[name]:
                if "delay" in step:
                    step["delay"] = 0
    return settings
