"""Application contract and shared calibrated launch behavior."""

import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from channelpilot.core.channels import Channel
from channelpilot.core.config import duration
from channelpilot.core.errors import CalibrationRequired
from channelpilot.core.navigation import Navigator, validate_steps
from channelpilot.core.remote import Remote
from channelpilot.core.state import Confidence

_LOG = logging.getLogger(__name__)


class TVApp(ABC):
    def __init__(self, remote: Remote, config: dict[str, Any], timeout: float = 120) -> None:
        self.remote = remote
        self.config = config
        self.package: str = config["package"]
        if not isinstance(self.package, str) or not self.package:
            raise ValueError("App package must be a nonempty string")
        if type(config.get("calibrated", False)) is not bool:
            raise ValueError("calibrated must be boolean")
        self.timeout = timeout
        self.nav = Navigator(remote, timeout)
        self.confidence = Confidence.UNKNOWN
        self.presumed_position: int | None = None
        for key in ("launch_steps", "reset_steps", "select_prefix", "select_suffix"):
            validate_steps(config.get(key, []))
        duration(config.get("home_delay", 1), "home_delay", zero=True)
        duration(config.get("startup_delay", 8), "startup_delay", zero=True)
        duration(config.get("channel_delay", 1), "channel_delay", zero=True)
        baseline = config.get("baseline", 1)
        if type(baseline) is not int or not 1 <= baseline <= 1000:
            raise ValueError("App baseline must be an integer from 1 to 1000")
        link = config.get("launch_link")
        if link is not None and (not isinstance(link, str) or not urlparse(link).scheme):
            raise ValueError("launch_link must be a URL/deep link, not a package name")

    def invalidate(self) -> None:
        if self.nav.expected_app is not None:
            self.nav.invalidated = True
        self.confidence = Confidence.UNKNOWN
        self.presumed_position = None

    def require_calibration(self) -> None:
        if not self.config.get("calibrated", False):
            raise CalibrationRequired(f"Calibrate {self.package} and set calibrated=true first")
        if not self.config.get("launch_link") and not self.config.get("launch_steps"):
            raise CalibrationRequired("Configure a tested launch_link or HOME-based launch_steps")
        if not self.config.get("reset_steps"):
            raise CalibrationRequired("Configure reset_steps from both cold and resumed app states")

    async def open(self) -> None:
        self.require_calibration()
        self.invalidate()
        self.nav = Navigator(self.remote, self.timeout)
        _LOG.info("Opening application: %s", self.package)
        # Always relaunch from HOME. HOME does not force-stop/reset an Android application.
        await self.nav.press("HOME")
        await self.nav.wait(self.config.get("home_delay", 1))
        if link := self.config.get("launch_link"):
            await self.remote.launch(link)
        else:
            await self.nav.run(self.config["launch_steps"])
        await self.nav.wait(self.config.get("startup_delay", 8))
        self.nav.expected_app = self.package
        self.nav.check()  # A nonempty unexpected reported package is a detectable failure.

    async def reset_navigation(self) -> None:
        self.require_calibration()
        await self.nav.run(self.config["reset_steps"])
        self.presumed_position = self.config.get("baseline", 1)
        self.confidence = Confidence.ASSUMED

    @abstractmethod
    async def navigate_to_channel(self, channel: Channel) -> None:
        """Complete a sequence; never claim that playback has been verified."""

    async def close(self) -> None:
        self.invalidate()
        await self.remote.press("HOME")  # The remote protocol cannot force-stop an app.
