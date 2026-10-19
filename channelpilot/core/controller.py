"""Single navigation owner with a one-slot latest-request-wins mailbox."""

import asyncio
import logging
from dataclasses import dataclass

from channelpilot.apps.base import TVApp

from .channels import Channels
from .errors import CalibrationRequired, ChannelPilotError, PairingRequired, PowerStateUnconfirmed
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
        app_close_timeout: float = 8,
    ) -> None:
        if retry_limit not in (0, 1):
            raise ValueError("At most one retry is supported")
        for channel in channels.by_id.values():
            if channel.app not in apps:
                raise ValueError(f"Channel {channel.id} uses unregistered app {channel.app}")
        self.remote = remote
        self.channels = channels
        self.apps = apps
        self.operation_timeout = operation_timeout
        self.retry_limit = retry_limit
        self.standby_on_failure = standby_on_failure
        self.app_close_timeout = app_close_timeout
        self.state = ControllerState(connected=remote.connected, is_on=remote.is_on)
        self.queue: asyncio.Queue[Request] = asyncio.Queue(maxsize=1)
        self._active: Request | None = None
        self._opened_app: TVApp | None = None
        self._reported_app = remote.current_app
        remote.add_availability_callback(self._availability)
        remote.add_app_callback(self._app_changed)
        remote.add_power_callback(self._power_changed)

    def invalidate_navigation(self) -> None:
        for app in self.apps.values():
            app.invalidate()
        self.state.presumed_channel = None
        self.state.navigation_confidence = Confidence.UNKNOWN
        self.state.sequence_completed = False
        self.state.current_app = self.remote.current_app
        self.state.remote_reported_app = self.remote.current_app
        self.state.app_confidence = (
            Confidence.CONFIRMED if self.remote.current_app else Confidence.UNKNOWN
        )

    def _availability(self, available: bool) -> None:
        self.state.connected = available
        self.state.is_on = self.remote.is_on
        if not available:
            for app in self.apps.values():
                app.adb.invalidate()
        self.invalidate_navigation()
        if not available and self._active is None:
            self.state.status = Status.CONNECTING
        elif available and self.state.status == Status.CONNECTING and self._active is None:
            self.state.status = Status.READY

    def _app_changed(self, package: str) -> None:
        changed = package != self._reported_app
        if changed:
            self.invalidate_navigation()
        self._reported_app = package
        self.state.remote_reported_app = package
        # An unchanged stale report carries no new foreground information.
        if changed or self.state.current_app is None:
            self.state.current_app = package
            self.state.app_confidence = Confidence.CONFIRMED

    def _power_changed(self, is_on: bool) -> None:
        self.state.is_on = is_on
        if not is_on:
            self.invalidate_navigation()
            if self._active is None:
                self.state.status = Status.STANDBY

    def submit(self, action: str, value: str | None = None) -> None:
        if action not in {"channel", "wake", "standby", "key", "open", "close", "adb-connect"}:
            raise ValueError(f"Unknown action: {action}")
        if action in {"open", "close"} and value not in self.apps:
            raise ValueError(f"Unknown app: {value}")
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
                    self.refresh_adb_state()
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
                if isinstance(exc, (CalibrationRequired, PairingRequired, PowerStateUnconfirmed)):
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
        if request.action != "channel":
            self.invalidate_navigation()
        self.state.status = Status.CONNECTING
        await self.remote.wait_ready()
        if request.action == "standby":
            if self._opened_app:
                try:
                    async with asyncio.timeout(self.app_close_timeout):
                        await self._opened_app.close(recover=False)
                    self._opened_app = None
                except (ChannelPilotError, TimeoutError, OSError) as exc:
                    _LOG.warning("App close failed; sending standby anyway: %s", exc)
            _LOG.info("Entering standby")
            await self.remote.standby()
            self.state.status = Status.STANDBY
            return
        if request.action == "adb-connect":
            await next(iter(self.apps.values())).adb.ensure_connected()
            self.state.status = Status.READY
            return
        if request.action == "close":
            selected = self.apps[request.value]  # type: ignore[index]
            await selected.close()
            if self._opened_app is selected:
                self._opened_app = None
            self.state.status = Status.READY
            return
        if request.action == "key":
            if request.value is None:
                raise ValueError("Manual command requires a value")
            await self.remote.press(request.value)
            self.state.status = Status.READY
            return
        reuse = (
            request.action == "channel"
            and self._opened_app is app
            and self.state.sequence_completed
            and await app.can_reuse_navigation()
        )
        await self.remote.wake()
        if request.action == "wake":
            _LOG.info("Wake request completed; no channel selected")
            self.state.status = Status.READY
            return
        if request.action == "open":
            app = self.apps[request.value]  # type: ignore[index]
        # Recheck after wake: connection/app/power callbacks may have invalidated continuity.
        if reuse and await app.can_reuse_navigation():
            _LOG.info(
                "Switching channel in running app: %s (presumed position %s)",
                app.package,
                app.presumed_position,
            )
        else:
            self.invalidate_navigation()
            self.state.status = Status.LAUNCHING_APP
            previous = self._opened_app or next(
                (
                    candidate
                    for candidate in self.apps.values()
                    if candidate.package == self.remote.current_app
                ),
                None,
            )
            if previous is not None and previous is not app:
                await previous.close()
                self._opened_app = None
            # Retain identity even if launch/startup fails, so retry/shutdown can close it.
            self._opened_app = app
            await app.open(prepare=request.action != "open")
        if request.action == "open":
            self._record_app_state(app)
            self.state.status = Status.READY
            return
        self.state.status = Status.NAVIGATING
        self.state.sequence_completed = False
        _LOG.info("Navigation started: %s", channel.name)
        await app.navigate_to_channel(channel)
        self.state.presumed_channel = channel.id
        self._record_app_state(app)
        self.state.navigation_confidence = Confidence.ASSUMED
        self.state.sequence_completed = True
        self.state.status = Status.READY
        _LOG.info("Navigation sequence completed: %s (playback presumed)", channel.name)

    def _record_app_state(self, app: TVApp) -> None:
        self.state.current_app = app.nav.current_app or app.package
        self.state.remote_reported_app = self.remote.current_app
        self.state.app_confidence = (
            Confidence.CONFIRMED if app.nav.current_app else Confidence.ASSUMED
        )

    def refresh_adb_state(self) -> None:
        self.state.is_on = self.remote.is_on
        if self.apps:
            adb = next(iter(self.apps.values())).adb
            self.state.adb_connected = adb.connected
            self.state.adb_endpoint = adb.endpoint

    async def close(self, shutdown_timeout: float = 8) -> None:
        """Call after the worker stops; do not enter device menus during shutdown."""
        try:
            if self._opened_app:
                async with asyncio.timeout(shutdown_timeout):
                    await self._opened_app.close(recover=False)
                self._opened_app = None
        except (ChannelPilotError, TimeoutError, OSError) as exc:
            _LOG.warning("Could not force-stop the app during shutdown: %s", exc)
        finally:
            self.invalidate_navigation()
            self.refresh_adb_state()
