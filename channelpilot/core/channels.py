"""Validated, configurable channel mapping and numeric keypad ordering."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    app: str
    navigation_id: int
    key: str


class Channels:
    def __init__(self, channels: list[Channel]) -> None:
        if not channels:
            raise ValueError("At least one channel is required")
        self.by_id: dict[str, Channel] = {}
        self.by_key: dict[str, Channel] = {}
        for channel in channels:
            if not all(
                isinstance(v, str) and v.strip() for v in (channel.id, channel.name, channel.app)
            ):
                raise ValueError("Channel id, name and app must be nonempty strings")
            if channel.id in self.by_id or channel.key in self.by_key:
                raise ValueError("Channel IDs and keys must be unique")
            if channel.key not in tuple(str(n) for n in range(10)):
                raise ValueError("Channel key must be a string digit 0-9")
            if type(channel.navigation_id) is not int or not 1 <= channel.navigation_id <= 1000:
                raise ValueError("navigation_id must be an integer from 1 to 1000")
            self.by_id[channel.id] = channel
            self.by_key[channel.key] = channel
        self.ordered = sorted(channels, key=lambda c: int(c.key))

    @classmethod
    def load(cls, path: Path) -> "Channels":
        with path.open(encoding="utf-8") as stream:
            records = json.load(stream)
        return cls([Channel(**record) for record in records])

    def adjacent(self, current: str | None, direction: int) -> Channel:
        ids = [channel.id for channel in self.ordered]
        if current not in ids:
            return self.ordered[0 if direction > 0 else -1]
        return self.ordered[(ids.index(current) + direction) % len(ids)]
