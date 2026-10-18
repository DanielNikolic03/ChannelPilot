"""SVT Play: independent baseline and horizontal channel selection."""

from channelpilot.core.channels import Channel
from channelpilot.core.errors import NavigationError
from channelpilot.core.state import Confidence

from .base import TVApp


class SVTApp(TVApp):
    async def navigate_to_channel(self, channel: Channel) -> None:
        if not self.can_reuse_navigation():
            await self.reset_navigation()
        if self.presumed_position is None:
            raise NavigationError("SVT baseline unknown")
        delta = channel.navigation_id - self.presumed_position
        if delta:
            await self.nav.run(self.config.get("select_prefix", []))
            await self.nav.press(
                "DPAD_RIGHT" if delta > 0 else "DPAD_LEFT",
                abs(delta),
                self.config.get("channel_delay", 1),
            )
            await self.nav.run(self.config.get("select_suffix", []))
        self.nav.check()
        self.presumed_position = channel.navigation_id
        self.confidence = Confidence.ASSUMED
