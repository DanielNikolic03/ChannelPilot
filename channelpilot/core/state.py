"""Reported device facts and explicitly presumed navigation state."""

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    READY = "READY"
    LAUNCHING_APP = "LAUNCHING_APP"
    NAVIGATING = "NAVIGATING"
    RECOVERING = "RECOVERING"
    ERROR = "ERROR"
    STANDBY = "STANDBY"


class Confidence(StrEnum):
    UNKNOWN = "unknown"
    ASSUMED = "assumed"
    CONFIRMED = "confirmed"  # Only device-reported facts, never a channel selection.


@dataclass
class ControllerState:
    requested_channel: str | None = None
    last_attempted_channel: str | None = None
    presumed_channel: str | None = None
    current_app: str | None = None
    remote_reported_app: str | None = None
    app_confidence: Confidence = Confidence.UNKNOWN
    navigation_confidence: Confidence = Confidence.UNKNOWN
    status: Status = Status.IDLE
    connected: bool = False
    is_on: bool | None = None
    adb_connected: bool = False
    adb_endpoint: str | None = None
    last_error: str | None = None
    sequence_completed: bool = False
