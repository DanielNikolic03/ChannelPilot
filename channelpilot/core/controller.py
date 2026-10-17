"""Single navigation owner with a one-slot latest-request-wins mailbox."""

import asyncio
import logging
from dataclasses import dataclass

from channelpilot.apps.base import TVApp

from .channels import Channels
from .errors import CalibrationRequired, ChannelPilotError, PairingRequired
from .remote import Remote
from .state import Confidence, ControllerState, Status

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Request:
    action: str
    value: str | None = None


class Controller:
    def __init__(
        self,
        remote: Remote,
        channels: Channels,
        apps: dict[str, TVApp],
        *,
        operation_timeout: float = 120,
        retry_limit: int = 1,
        standby_on_failure: bool = False,
        default_channel: str | None = None,
    ) -> None:
        if retry_limit not in (0, 1):
            raise ValueError("At most one retry is supported")
        for channel in channels.by_id.values():
            if channel.app not in apps:
                raise ValueError(f"Channel {channel.id} uses unregistered app {channel.app}")
        if default_channel is not None and default_channel not in channels.by_id:
            raise ValueError("default_channel is not registered")
        self.remote = remote
        self.channels = channels
        self.apps = apps
        self.operation_timeout = operation_timeout
        self.retry_limit = retry_limit
        self.standby_on_failure = standby_on_failure
        self.default_channel = default_channel
        self.state = ControllerState(connected=remote.connected)
        self.queue: asyncio.Queue[Request] = asyncio.Queue(maxsize=1)
        self._active: Request | None = None
        remote.add_availability_callback(self._availability)
        remote.add_app_callback(self._app_changed)

    def invalidate_navigation(self) -> None:
        for app in self.apps.values():
            app.invalidate()
        self.state.presumed_channel = None
        self.state.navigation_confidence = Confidence.UNKNOWN
        self.state.sequence_completed = False
        self.state.current_app = self.remote.current_app
        self.state.app_confidence = (
            Confidence.CONFIRMED if self.remote.current_app else Confidence.UNKNOWN
        )

    def _availability(self, available: bool) -> None:
        self.state.connected = available
        self.invalidate_navigation()
        if not available and self._active is None:
            self.state.status = Status.CONNECTING
        elif available and self.state.status == Status.CONNECTING and self._active is None:
            self.state.status = Status.READY

    def _app_changed(self, package: str) -> None:
        if package != self.state.current_app:
            self.invalidate_navigation()
        self.state.current_app = package
        self.state.app_confidence = Confidence.CONFIRMED

    def submit(self, action: str, value: str | None = None) -> None:
        if action not in {"channel", "wake", "standby", "key", "launch"}:
            raise ValueError(f"Unknown action: {action}")
        if action == "channel":
            if value not in self.channels.by_id:
                raise ValueError(f"Unknown channel: {value}")
            self.state.requested_channel = value
            _LOG.info("Channel requested: %s", self.channels.by_id[value].name)
        request = Request(action, value)
        # Coalesce duplicate active requests only when no newer action is pending.
        if request == self._active and self.queue.empty():
            return
        if self.queue.full():
            self.queue.get_nowait()
            self.queue.task_done()
        self.queue.put_nowait(request)

    def handle_input(self, action: str) -> None:
        if action in self.channels.by_key:
            self.submit("channel", self.channels.by_key[action].id)
        elif action in {"next", "previous"}:
            channel = self.channels.adjacent(
                self.state.requested_channel or self.state.presumed_channel,
                1 if action == "next" else -1,
            )
            self.submit("channel", channel.id)
        elif action in {"wake", "standby"}:
            if action == "wake" and self.default_channel:
                self.submit("channel", self.default_channel)
            else:
                self.submit(action)

    async def run(self) -> None:
        try:
            while True:
                request = await self.queue.get()
                self._active = request
                try:
                    await self._execute(request)
                except asyncio.CancelledError:
                    self.invalidate_navigation()
                    raise
                except Exception as exc:
                    # A programming or device-specific error must not kill input handling.
                    self._failed(exc)
                    _LOG.exception("Unexpected operation failure; waiting for new input")
                finally:
                    self._active = None
                    self.queue.task_done()
        finally:
            self.invalidate_navigation()
            self.state.status = Status.IDLE

    def _failed(self, exc: Exception) -> None:
        self.invalidate_navigation()
        self.state.status = Status.ERROR
        self.state.last_error = f"{type(exc).__name__}: {exc}"
        _LOG.error("Navigation failed: %s", self.state.last_error)

    async def _execute(self, request: Request) -> None:
        self.state.last_error = None
        for attempt in range(self.retry_limit + 1):
            try:
                async with asyncio.timeout(self.operation_timeout):
                    if attempt:
                        self.state.status = Status.RECOVERING
                        _LOG.info("Recovery attempt started")
                        self.invalidate_navigation()
                        await self.remote.wait_ready()
                        if request.action == "channel":
                            await self.remote.wake()
                            await self.remote.press("HOME")
                    await self._operate(request)
                self.state.last_error = None
                return
            except (ChannelPilotError, TimeoutError, OSError) as exc:
                self._failed(exc)
                # Configuration/auth require a human; do not navigate blindly to fix them.
                if isinstance(exc, (CalibrationRequired, PairingRequired)):
                    return
                if attempt == self.retry_limit:
                    _LOG.error("Recovery failed; waiting for a new request")
                    if self.standby_on_failure:
                        try:
                            async with asyncio.timeout(self.operation_timeout):
                                await self.remote.standby()
                        except (ChannelPilotError, TimeoutError, OSError):
                            _LOG.warning("Optional standby unavailable")
                    return

    async def _operate(self, request: Request) -> None:
        if request.action == "channel":
            channel = self.channels.by_id[request.value]  # type: ignore[index]
            app = self.apps[channel.app]
            self.state.last_attempted_channel = channel.id
            app.require_calibration()  # Validate before sending any keypresses.
        self.invalidate_navigation()
        self.state.status = Status.CONNECTING
        await self.remote.wait_ready()
        if request.action == "standby":
            _LOG.info("Entering standby")
            await self.remote.standby()
            self.state.status = Status.STANDBY
            return
        if request.action in {"key", "launch"}:
            if request.value is None:
                raise ValueError("Manual command requires a value")
            if request.action == "key":
                await self.remote.press(request.value)
            else:
                await self.remote.launch(request.value)
            self.state.status = Status.READY
            return
        await self.remote.wake()
        if request.action == "wake":
            self.state.status = Status.READY
            return
        self.state.status = Status.LAUNCHING_APP
        await app.open()
        self.state.status = Status.NAVIGATING
        _LOG.info("Navigation started: %s", channel.name)
        await app.navigate_to_channel(channel)
        self.state.presumed_channel = channel.id
        self.state.current_app = self.remote.current_app or app.package
        self.state.app_confidence = (
            Confidence.CONFIRMED if self.remote.current_app else Confidence.ASSUMED
        )
        self.state.navigation_confidence = Confidence.ASSUMED
        self.state.sequence_completed = True
        self.state.status = Status.READY
        _LOG.info("Navigation sequence completed: %s (playback presumed)", channel.name)
