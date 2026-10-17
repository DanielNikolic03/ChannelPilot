"""JSON configuration, validation, and paths relative to the configuration file."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def duration(value: Any, name: str, *, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")
    return float(value)


@dataclass(frozen=True)
class RemoteConfig:
    host: str
    credentials_dir: Path
    connect_timeout: float = 15.0
    reconnect_delay: float = 3.0
    command_delay: float = 0.35
    wake_delay: float = 3.0


@dataclass(frozen=True)
class Config:
    remote: RemoteConfig
    channels_file: Path
    apps: dict[str, dict[str, Any]]
    device: str = "/dev/input/by-id/REPLACE_WITH_NUMPAD-event-kbd"
    grab: bool = False
    debounce: float = 0.15
    device_retry: float = 2.0
    operation_timeout: float = 120.0
    retry_limit: int = 1
    standby_on_failure: bool = False
    log_level: str = "INFO"
    default_channel: str | None = None
    input_key_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Config":
        path = path.expanduser().resolve()
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)

        def relative(value: str) -> Path:
            result = Path(value).expanduser()
            return result if result.is_absolute() else path.parent / result

        rc = data["remote"]
        if not isinstance(rc["host"], str) or not rc["host"].strip():
            raise ValueError("remote.host is required")
        remote = RemoteConfig(
            host=rc["host"],
            credentials_dir=relative(rc.get("credentials_dir", "~/.local/state/channelpilot")),
            **{
                name: duration(
                    rc.get(name, default), name, zero=name in {"command_delay", "wake_delay"}
                )
                for name, default in (
                    ("connect_timeout", 15),
                    ("reconnect_delay", 3),
                    ("command_delay", 0.35),
                    ("wake_delay", 3),
                )
            },
        )
        # Credentials must never live in the checked-out project.
        credential_path = remote.credentials_dir.resolve()
        if any(
            (parent / ".git").exists() for parent in (credential_path, *credential_path.parents)
        ):
            raise ValueError("Store credentials outside the Git repository")
        retry = data.get("retry_limit", 1)
        if type(retry) is not int or retry not in (0, 1):
            raise ValueError("retry_limit must be 0 or 1 (at most one whole-operation retry)")
        inp = data.get("input", {})
        for value in (inp.get("grab", False), data.get("standby_on_failure", False)):
            if type(value) is not bool:
                raise ValueError("grab and standby_on_failure must be booleans")
        level = data.get("log_level", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid log_level")
        if not isinstance(inp.get("device", ""), str):
            raise ValueError("input.device must be a path")
        return cls(
            remote=remote,
            channels_file=relative(data.get("channels_file", "channels.json")),
            apps=data["apps"],
            device=inp.get("device", cls.device),
            grab=inp.get("grab", False),
            debounce=duration(inp.get("debounce", 0.15), "debounce", zero=True),
            device_retry=duration(inp.get("reconnect_delay", 2), "input.reconnect_delay"),
            operation_timeout=duration(data.get("operation_timeout", 120), "operation_timeout"),
            retry_limit=retry,
            standby_on_failure=data.get("standby_on_failure", False),
            log_level=level,
            default_channel=data.get("default_channel"),
            input_key_overrides=inp.get("key_overrides", {}),
        )
