"""Detectable failures; no exception claims to verify playback."""


class ChannelPilotError(Exception):
    """A detectable operation failure suitable for bounded recovery."""


class RemoteUnavailable(ChannelPilotError):
    """Connection lost or transport unavailable."""


class PairingRequired(ChannelPilotError):
    """Credentials need manual pairing; automatic retry cannot fix this."""


class NavigationError(ChannelPilotError):
    """Unexpected app, invalidated session, or checkpoint failure."""


class CalibrationRequired(ChannelPilotError):
    """Navigation configuration has not been calibrated on this device."""
