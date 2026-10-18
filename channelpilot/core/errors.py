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


class AdbError(ChannelPilotError):
    """An ADB app-lifecycle command failed detectably."""


class AdbUnavailable(AdbError):
    """ADB transport could not be reached within its deadline."""


class AdbPairingRequired(PairingRequired):
    """The ADB host must be paired separately from the remote protocol."""


class PowerStateUnconfirmed(ChannelPilotError):
    """A power toggle was sent but not confirmed; automatic retoggling is unsafe."""
