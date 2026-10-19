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
    power_timeout: float = 5.0


@dataclass(frozen=True)
class AdbConfig:
    executable: str = "adb"
    mdns_backend: str = "adb"
    avahi_executable: str = "avahi-browse"
    port: int | None = None
    server_port: int = 5037
    command_timeout: float = 10.0
    discovery_timeout: float = 5.0
    recovery_timeout: float = 60.0
    poll_interval: float = 1.0
    recovery_cooldown: float = 60.0
    recovery_calibrated: bool = False
    recovery_steps: list[dict[str, Any]] = field(default_factory=list)
    shutdown_timeout: float = 8.0

    @classmethod
    def load(cls, data: dict[str, Any]) -> "AdbConfig":
        from .navigation import validate_steps

        executable = data.get("executable", "adb")
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("adb.executable must be an executable path/name")
        mdns_backend = data.get("mdns_backend", "adb")
        if mdns_backend not in {"adb", "avahi"}:
            raise ValueError("adb.mdns_backend must be 'adb' or 'avahi'")
        avahi_executable = data.get("avahi_executable", "avahi-browse")
        if not isinstance(avahi_executable, str) or not avahi_executable.strip():
            raise ValueError("adb.avahi_executable must be an executable path/name")
        port = data.get("port")
        server = data.get("server_port", 5037)
        for value in (port, server):
            if value is not None and (type(value) is not int or not 1 <= value <= 65535):
                raise ValueError("ADB ports must be integers from 1 to 65535")
        if server is None:
            raise ValueError("adb.server_port is required")
        calibrated = data.get("recovery_calibrated", False)
        if type(calibrated) is not bool:
            raise ValueError("adb.recovery_calibrated must be boolean")
        steps = data.get("recovery_steps", [])
        validate_steps(steps)
        if calibrated and not any("press" in step for step in steps):
            raise ValueError("Calibrated ADB recovery needs actual remote-key steps")
        return cls(
            executable=executable,
            mdns_backend=mdns_backend,
            avahi_executable=avahi_executable,
            port=port,
            server_port=server,
            recovery_calibrated=calibrated,
            recovery_steps=steps,
            **{
                name: duration(data.get(name, default), "adb." + name)
                for name, default in (
                    ("command_timeout", 10),
                    ("discovery_timeout", 5),
                    ("recovery_timeout", 60),
                    ("poll_interval", 1),
                    ("recovery_cooldown", 60),
                    ("shutdown_timeout", 8),
                )
            },
        )


@dataclass(frozen=True)
class Config:
    remote: RemoteConfig
    channels_file: Path
    apps: dict[str, dict[str, Any]]
    adb: AdbConfig = field(default_factory=AdbConfig)
    device: str = "/dev/input/by-id/REPLACE_WITH_NUMPAD-event-kbd"
    grab: bool = False
    debounce: float = 0.15
    device_retry: float = 2.0
    operation_timeout: float = 120.0
    retry_limit: int = 1
    standby_on_failure: bool = False
    log_level: str = "INFO"
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
                    ("power_timeout", 5),
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
            adb=AdbConfig.load(data.get("adb", {})),
            device=inp.get("device", cls.device),
            grab=inp.get("grab", False),
            debounce=duration(inp.get("debounce", 0.15), "debounce", zero=True),
            device_retry=duration(inp.get("reconnect_delay", 2), "input.reconnect_delay"),
            operation_timeout=duration(data.get("operation_timeout", 120), "operation_timeout"),
            retry_limit=retry,
            standby_on_failure=data.get("standby_on_failure", False),
            log_level=level,
            input_key_overrides=inp.get("key_overrides", {}),
        )
