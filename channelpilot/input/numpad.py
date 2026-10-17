"""Focus-independent input from one explicit Linux device; no keyboard scanning."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

_LOG = logging.getLogger(__name__)

# Linux input-event-codes.h; keypad codes do NOT form a consecutive 0-9 range.
KEY_ACTIONS: dict[int, str] = {
    82: "0",
    79: "1",
    80: "2",
    81: "3",
    75: "4",
    76: "5",
    77: "6",
    71: "7",
    72: "8",
    73: "9",
    78: "next",
    74: "previous",
    96: "wake",
    28: "wake",
    14: "standby",
}


class KeyFilter:
    def __init__(self, debounce: float = 0.15, overrides: dict[str, str] | None = None) -> None:
        self.debounce = debounce
        self.actions = dict(KEY_ACTIONS)
        for code, action in (overrides or {}).items():
            if action not in {*map(str, range(10)), "next", "previous", "wake", "standby"}:
                raise ValueError(f"Unsupported key action: {action}")
            self.actions[int(code)] = action
        self.held: set[int] = set()
        self.last: dict[int, float] = {}

    def feed(self, event_type: int, code: int, value: int, now: float | None = None) -> str | None:
        if event_type != 1:  # EV_KEY
            return None
        if value == 0:
            self.held.discard(code)
            return None
        if value != 1 or code in self.held:  # Ignore EV_KEY repeat (2) and duplicate downs.
            return None
        self.held.add(code)
        stamp = time.monotonic() if now is None else now
        if stamp - self.last.get(code, float("-inf")) < self.debounce:
            return None
        self.last[code] = stamp
        return self.actions.get(code)

    def reset(self) -> None:
        self.held.clear()
        self.last.clear()


class NumpadListener:
    def __init__(
        self,
        path: str,
        callback: Callable[[str], None],
        *,
        grab: bool = False,
        debounce: float = 0.15,
        reconnect_delay: float = 2,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.callback = callback
        self.grab = grab
        self.reconnect_delay = reconnect_delay
        self.filter = KeyFilter(debounce, overrides)

    async def run(self) -> None:
        try:
            from evdev import InputDevice
        except ImportError as exc:
            raise RuntimeError("Numpad input requires Linux evdev; use --test on macOS") from exc
        while True:
            device = None
            try:
                device = InputDevice(self.path)
                if self.grab:
                    device.grab()
                self.filter.reset()
                _LOG.info("Listening to numpad: %s (%s)", self.path, device.name)
                async for event in device.async_read_loop():
                    if event.type == 0 and event.code == 3:  # SYN_DROPPED: lost input events.
                        # Reopen rather than trusting stale held-key state.
                        raise OSError("Input event buffer overrun")
                    if action := self.filter.feed(event.type, event.code, event.value):
                        self.callback(action)
            except OSError as exc:
                _LOG.warning("Numpad unavailable: %s; retrying configured device", exc)
            finally:
                if device is not None:
                    device.close()  # Releases any grab even during cancellation.
                self.filter.reset()
            await asyncio.sleep(self.reconnect_delay)
