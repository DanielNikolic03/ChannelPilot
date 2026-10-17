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
    print("launch <tested-deep-link> | invalidate | quit")
    while True:
        try:
            line = (await read_line("> ")).strip()
        except EOFError:
            return
        if line == "quit":
            return
        if line == "status":
            print(json.dumps(asdict(controller.state), indent=2))
        elif line == "invalidate":
            controller.invalidate_navigation()
        elif line.startswith("key "):
            controller.submit("key", line[4:].upper())
        elif line.startswith("launch "):
            controller.submit("launch", line[7:])
        elif line in {*map(str, range(10)), "+", "-", "wake", "standby"}:
            controller.handle_input({"+": "next", "-": "previous"}.get(line, line))
        elif line:
            _LOG.warning("Unknown command: %s", line)
