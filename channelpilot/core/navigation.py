"""Bounded, readable sequences with device-state checks, never UI verification."""

import asyncio
import logging
from typing import Any

from .config import duration
from .errors import NavigationError, RemoteUnavailable
from .remote import Remote

_LOG = logging.getLogger(__name__)


def validate_steps(steps: list[dict[str, Any]]) -> None:
    if not isinstance(steps, list) or len(steps) > 200:
        raise ValueError("Navigation sequences must be lists with at most 200 steps")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Each navigation step must be an object")
        actions = set(step) & {"press", "wait", "checkpoint"}
        if len(actions) != 1:
            raise ValueError("Each step needs exactly one of press, wait, checkpoint")
        allowed = {"press", "times", "delay", "wait", "checkpoint", "if_app", "if_power"}
        if set(step) - allowed:
            raise ValueError(f"Unknown step fields: {set(step) - allowed}")
        if "press" in step:
            from androidtvremote2.remotemessage_pb2 import RemoteKeyCode

            key = step["press"]
            if not isinstance(key, str):
                raise ValueError("press must be an Android key name")
            RemoteKeyCode.Value(key if key.startswith("KEYCODE_") else f"KEYCODE_{key}")
            times = step.get("times", 1)
            if type(times) is not int or not 1 <= times <= 1000:
                raise ValueError("times must be an integer from 1 to 1000")
        for name in ("delay", "wait"):
            if name in step:
                duration(step[name], name, zero=True)
        if "checkpoint" in step and not isinstance(step["checkpoint"], str):
            raise ValueError("checkpoint must be a label")
        if "if_app" in step and not isinstance(step["if_app"], str):
            raise ValueError("if_app must be a package name")
        if "if_power" in step and type(step["if_power"]) is not bool:
            raise ValueError("if_power must be boolean")


class Navigator:
    def __init__(self, remote: Remote, timeout: float = 120) -> None:
        self.remote = remote
        self.timeout = timeout
        self.epoch = remote.epoch
        self.invalidated = False
        self.expected_app: str | None = None
        self.checkpoint: str | None = None

    def check(self) -> None:
        if self.invalidated:
            raise NavigationError("Navigation state explicitly invalidated")
        if self.expected_app and self.remote.is_on is False:
            raise NavigationError("Device entered standby during navigation")
        if not self.remote.connected or self.remote.epoch != self.epoch:
            raise RemoteUnavailable("Navigation interrupted by a connection change")
        active = self.remote.current_app
        if self.expected_app and active and active != self.expected_app:
            raise NavigationError(f"Unexpected active application: {active}")

    async def press(self, key: str, times: int = 1, delay: float = 0) -> None:
        for _ in range(times):
            self.check()
            await self.remote.press(key)
            await self.wait(delay)

    async def wait(self, seconds: float) -> None:
        # Check throughout long startup waits so a detectable failure stops promptly.
        remaining = seconds
        self.check()
        while remaining > 0:
            interval = min(remaining, 0.2)
            await asyncio.sleep(interval)
            remaining -= interval
            self.check()

    async def run(self, steps: list[dict[str, Any]]) -> None:
        validate_steps(steps)
        async with asyncio.timeout(self.timeout):
            for step in steps:
                self.check()
                if "if_app" in step and self.remote.current_app != step["if_app"]:
                    continue
                if "if_power" in step and self.remote.is_on is not step["if_power"]:
                    continue
                if "press" in step:
                    await self.press(step["press"], step.get("times", 1), step.get("delay", 0))
                elif "wait" in step:
                    await self.wait(step["wait"])
                else:
                    self.checkpoint = step["checkpoint"]
                    _LOG.debug("Navigation checkpoint (sequence marker): %s", self.checkpoint)
