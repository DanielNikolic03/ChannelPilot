"""Application contract and shared calibrated launch behavior."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from channelpilot.core.adb import AppControl, validate_component, validate_package
from channelpilot.core.channels import Channel
from channelpilot.core.config import duration
from channelpilot.core.errors import CalibrationRequired
from channelpilot.core.navigation import Navigator, validate_steps
from channelpilot.core.remote import Remote
from channelpilot.core.state import Confidence

_LOG = logging.getLogger(__name__)


class TVApp(ABC):
    def __init__(
        self, remote: Remote, config: dict[str, Any], timeout: float = 120, *, adb: AppControl
    ) -> None:
        self.remote = remote
        self.adb = adb
        self.config = config
        self.package: str = config["package"]
        validate_package(self.package)
        if config.get("activity") is not None:
            validate_component(self.package, config["activity"])
        if type(config.get("calibrated", False)) is not bool:
            raise ValueError("calibrated must be boolean")
        self.timeout = timeout
        self.nav = Navigator(remote, timeout)
        self.confidence = Confidence.UNKNOWN
        self.presumed_position: int | None = None
        for key in ("startup_steps", "reset_steps", "select_prefix", "select_suffix"):
            validate_steps(config.get(key, []))
        duration(config.get("startup_delay", 8), "startup_delay", zero=True)
        duration(config.get("channel_delay", 1), "channel_delay", zero=True)
        baseline = config.get("baseline", 1)
        if type(baseline) is not int or not 1 <= baseline <= 1000:
            raise ValueError("App baseline must be an integer from 1 to 1000")

    def invalidate(self) -> None:
        if self.nav.expected_app is not None:
            self.nav.invalidated = True
        self.confidence = Confidence.UNKNOWN
        self.presumed_position = None

    def can_reuse_navigation(self) -> bool:
        """Continuity permits an assumed position; it does not verify playback."""
        return (
            self.confidence != Confidence.UNKNOWN
            and self.presumed_position is not None
            and self.remote.connected
            and self.remote.epoch == self.nav.epoch
            and not self.nav.invalidated
            and self.nav.expected_app == self.package
            and self.remote.is_on is not False
            and self.remote.current_app in (None, "", self.package)
        )

    def require_calibration(self) -> None:
        if not self.config.get("calibrated", False):
            raise CalibrationRequired(f"Calibrate {self.package} and set calibrated=true first")
        if self.config.get("launch_steps") or self.config.get("launch_link"):
            raise CalibrationRequired(
                "Old launch_steps/launch_link are no longer used: ADB opens the package. "
                "Move app-internal startup navigation to startup_steps/reset_steps, "
                "remove Google TV launcher steps, and clear the old launch settings."
            )
        if "reset_steps" not in self.config:
            raise CalibrationRequired(
                "Declare reset_steps to establish the baseline after a fresh app launch; "
                "use [] if startup_steps already reach that baseline"
            )

    async def open(self, *, prepare: bool = True) -> None:
        if prepare:
            self.require_calibration()
        self.invalidate()
        _LOG.info("Opening application through ADB: %s", self.package)
        await self.adb.launch(self.package, self.config.get("activity"))
        # ADB recovery may have navigated through Settings; anchor a fresh remote session now.
        self.nav = Navigator(self.remote, self.timeout)
        await self.nav.wait(self.config.get("startup_delay", 8))
        self.nav.expected_app = self.package
        self.nav.check()
        if prepare:
            await self.nav.run(self.config.get("startup_steps", []))

    async def reset_navigation(self) -> None:
        self.require_calibration()
        await self.nav.run(self.config["reset_steps"])
        self.presumed_position = self.config.get("baseline", 1)
        self.confidence = Confidence.ASSUMED

    @abstractmethod
    async def navigate_to_channel(self, channel: Channel) -> None:
        """Complete a sequence; never claim that playback has been verified."""

    async def close(self, *, recover: bool = True) -> None:
        self.invalidate()
        await self.adb.force_stop(self.package, recover=recover)
