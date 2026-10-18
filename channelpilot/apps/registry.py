"""App registration; the central controller has no application navigation logic."""

from typing import Any

from channelpilot.core.adb import AppControl
from channelpilot.core.remote import Remote

from .base import TVApp
from .move import MoveApp
from .svt import SVTApp

APP_TYPES: dict[str, type[TVApp]] = {"move": MoveApp, "svt": SVTApp}


def build_apps(
    remote: Remote, configs: dict[str, dict[str, Any]], timeout: float, *, adb: AppControl
) -> dict[str, TVApp]:
    result = {}
    for name, settings in configs.items():
        kind = settings.get("type", name)
        if kind not in APP_TYPES:
            raise ValueError(f"Unregistered application type: {kind}")
        result[name] = APP_TYPES[kind](remote, settings, timeout, adb=adb)
    return result
