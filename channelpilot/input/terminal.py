"""Async line-based controls for SSH and macOS without GUI dependencies."""

import asyncio
import json
import logging
import sys
from dataclasses import asdict

from channelpilot.core.controller import Controller

_LOG = logging.getLogger(__name__)


async def read_line(prompt: str = "") -> str:
    """Read stdin without a blocked executor thread preventing clean shutdown."""
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[str] = loop.create_future()

    def readable() -> None:
        if not ready.done():
            ready.set_result(sys.stdin.readline())

    loop.add_reader(sys.stdin.fileno(), readable)
    try:
        line = await ready
    finally:
        loop.remove_reader(sys.stdin.fileno())
    if line == "":
        raise EOFError
    return line.rstrip("\r\n")


async def run_terminal(controller: Controller) -> None:
    print("0-9: channel | +/-: next/previous | wake | standby | status")
    print("key DPAD_UP (or HOME, BACK, VOLUME_UP, VOLUME_DOWN, VOLUME_MUTE)")
    print(
        "open <app-name-or-package> | close <app-name-or-package> | adb-connect | invalidate | quit"
    )
    while True:
        try:
            line = (await read_line("> ")).strip()
        except EOFError:
            return
        if line == "quit":
            return
        if line == "status":
            controller.refresh_adb_state()
            print(json.dumps(asdict(controller.state), indent=2))
        elif line == "invalidate":
            controller.invalidate_navigation()
        elif line.startswith("key "):
            controller.submit("key", line[4:].upper())
        elif line == "adb-connect":
            controller.submit("adb-connect")
        elif line.startswith(("open ", "launch ", "close ")):
            action, value = line.split(maxsplit=1)
            name = next(
                (name for name, app in controller.apps.items() if value in {name, app.package}),
                None,
            )
            if name is None:
                _LOG.error("Unknown app; use a configured app name or package ID")
            else:
                controller.submit("close" if action == "close" else "open", name)
        elif line in {*map(str, range(10)), "+", "-", "wake", "standby"}:
            controller.handle_input({"+": "next", "-": "previous"}.get(line, line))
        elif line:
            _LOG.warning("Unknown command: %s", line)
