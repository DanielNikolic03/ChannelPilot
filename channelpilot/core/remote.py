"""Thin transport wrapper using androidtvremote2's public API only."""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
from cryptography import x509

from .config import RemoteConfig
from .errors import PairingRequired, PowerStateUnconfirmed, RemoteUnavailable

_LOG = logging.getLogger(__name__)


class Remote(Protocol):
    connected: bool
    epoch: int

    @property
    def current_app(self) -> str | None: ...
    @property
    def is_on(self) -> bool | None: ...

    async def wait_ready(self) -> None: ...
    async def press(self, key: str) -> None: ...
    async def wake(self) -> None: ...
    async def standby(self) -> None: ...
    def add_availability_callback(self, callback: Callable[[bool], None]) -> None: ...
    def add_app_callback(self, callback: Callable[[str], None]) -> None: ...
    def add_power_callback(self, callback: Callable[[bool], None]) -> None: ...


class RemoteController:
    def __init__(self, config: RemoteConfig, client: AndroidTVRemote | None = None) -> None:
        self.config = config
        self.certfile = config.credentials_dir / "client-cert.pem"
        self.keyfile = config.credentials_dir / "client-key.pem"
        self.client = client or AndroidTVRemote(
            "ChannelPilot",
            str(self.certfile),
            str(self.keyfile),
            config.host,
        )
        self.connected = False
        self.epoch = 0
        self._auth_error = False
        self._changed = asyncio.Event()
        self._power_changed = asyncio.Event()
        self._power_uncertain = False
        self._send_lock = asyncio.Lock()
        self._supervisor: asyncio.Task[None] | None = None
        self._availability_callbacks: list[Callable[[bool], None]] = []
        self._app_callbacks: list[Callable[[str], None]] = []
        self._power_callbacks: list[Callable[[bool], None]] = []
        self.client.add_is_available_updated_callback(self._availability)
        self.client.add_current_app_updated_callback(self._app_updated)
        self.client.add_is_on_updated_callback(self._power_updated)

    @property
    def current_app(self) -> str | None:
        return self.client.current_app if self.connected else None

    @property
    def is_on(self) -> bool | None:
        return self.client.is_on if self.connected and not self._power_uncertain else None

    def add_availability_callback(self, callback: Callable[[bool], None]) -> None:
        self._availability_callbacks.append(callback)

    def add_app_callback(self, callback: Callable[[str], None]) -> None:
        self._app_callbacks.append(callback)

    def add_power_callback(self, callback: Callable[[bool], None]) -> None:
        self._power_callbacks.append(callback)

    def _power_updated(self, is_on: bool) -> None:
        self._power_uncertain = False
        self._power_changed.set()
        for callback in self._power_callbacks:
            callback(is_on)

    def _app_updated(self, app: str) -> None:
        for callback in self._app_callbacks:
            callback(app)

    def _availability(self, available: bool) -> None:
        if available == self.connected:
            return
        self.connected = available
        if not available:
            self.epoch += 1
            self._power_uncertain = True
        self._power_changed.set()
        _LOG.info("Connected to Chromecast" if available else "Disconnected; reconnecting")
        self._changed.set()
        for callback in self._availability_callbacks:
            callback(available)

    def _invalid_auth(self) -> None:
        self._auth_error = True
        self._availability(False)
        self._changed.set()
        _LOG.error("Credentials rejected. Stop the service and run: python -m channelpilot --pair")

    def validate_credentials(self) -> None:
        try:
            for path in (self.certfile, self.keyfile):
                if path.is_symlink():
                    raise ValueError("Credential symlinks are not supported")
                path.chmod(0o600)
            cert = x509.load_pem_x509_certificate(self.certfile.read_bytes())
            now = datetime.now(UTC)
            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                raise ValueError("Certificate expired or not yet valid")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_cert_chain(self.certfile, self.keyfile)
        except (OSError, ValueError, ssl.SSLError) as exc:
            raise PairingRequired(
                "Credentials missing, invalid, or expired. Run with --pair; "
                "move invalid PEM files aside before generating new credentials."
            ) from exc

    async def pair(self, read_code: Callable[[], Awaitable[str]]) -> None:
        """Explicit interactive pairing; never initiated by the background service."""
        directory = self.config.credentials_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise PairingRequired("Credential directory must not be a symlink")
        directory.chmod(0o700)
        for path in (self.certfile, self.keyfile):
            if path.is_symlink():
                raise PairingRequired("Credential files must not be symlinks")
            if path.exists():
                path.chmod(0o600)
        # Parent directory is private before the public library API creates any files.
        # A finally block tightens file modes even if generation is interrupted.
        try:
            await self.client.async_generate_cert_if_missing()
        finally:
            for path in (self.certfile, self.keyfile):
                if path.exists():
                    path.chmod(0o600)
        self.validate_credentials()
        try:
            async with asyncio.timeout(self.config.connect_timeout):
                await self.client.async_start_pairing()
            async with asyncio.timeout(120):
                code = (await read_code()).strip()
            async with asyncio.timeout(self.config.connect_timeout):
                await self.client.async_finish_pairing(code)
            _LOG.info("Pairing completed; credentials stored privately")
        except InvalidAuth as exc:
            raise PairingRequired("Pairing code rejected; run --pair again") from exc
        except (CannotConnect, ConnectionClosed) as exc:
            raise RemoteUnavailable(
                "Pairing connection failed; check the host and run --pair again"
            ) from exc
        finally:
            self.client.disconnect()

    def start(self) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._connect(), name="remote-connection")

    async def _connect(self) -> None:
        """Retry initial connection; library owns all reconnections after success."""
        try:
            self.validate_credentials()
        except PairingRequired as exc:
            _LOG.error("%s", exc)
            self._invalid_auth()
            return
        while True:
            try:
                _LOG.info("Connecting to Chromecast")
                async with asyncio.timeout(self.config.connect_timeout):
                    await self.client.async_connect()
                self._availability(True)
                self.client.keep_reconnecting(self._invalid_auth)
                return
            except InvalidAuth:
                self.client.disconnect()
                self._invalid_auth()
                return
            except (CannotConnect, ConnectionClosed, OSError, TimeoutError) as exc:
                self.client.disconnect()
                _LOG.warning("Connection unavailable (%s); retrying", type(exc).__name__)
                await asyncio.sleep(self.config.reconnect_delay)

    async def wait_ready(self) -> None:
        async with asyncio.timeout(self.config.connect_timeout):
            while not self.connected:
                if self._auth_error:
                    raise PairingRequired("Manual pairing required; run with --pair")
                self._changed.clear()
                await self._changed.wait()

    async def _send(self, command: Callable[[], None], description: str) -> None:
        async with self._send_lock:
            if not self.connected:
                raise RemoteUnavailable("No command sent: Chromecast disconnected")
            epoch = self.epoch
            _LOG.debug("Remote command: %s", description)
            try:
                command()
            except (ConnectionClosed, CannotConnect, OSError) as exc:
                self._availability(False)
                raise RemoteUnavailable("Remote command transmission failed") from exc
            await asyncio.sleep(self.config.command_delay)
            if not self.connected or epoch != self.epoch:
                raise RemoteUnavailable("Connection lost after command was buffered")

    async def press(self, key: str) -> None:
        await self._send(lambda: self.client.send_key_command(key), key)

    async def _set_power(self, on: bool) -> bool:
        await self.wait_ready()
        current = self.is_on
        if current is on:
            _LOG.debug("Chromecast already reports %s", "on" if on else "standby")
            return False
        if current is None:
            # Without a reported state a toggle could do the opposite of the request.
            await self.press("WAKEUP" if on else "SLEEP")
            _LOG.warning(
                "Power state unknown; sent explicit %s without a POWER toggle",
                "WAKEUP" if on else "SLEEP",
            )
            return True
        epoch = self.epoch
        self._power_changed.clear()
        self._power_uncertain = True
        await self.press("POWER")
        try:
            async with asyncio.timeout(self.config.power_timeout):
                while self.is_on is not on:
                    if not self.connected or self.epoch != epoch:
                        raise PowerStateUnconfirmed(
                            "Connection changed after POWER; not toggling again automatically"
                        )
                    self._power_changed.clear()
                    await self._power_changed.wait()
        except TimeoutError as exc:
            raise PowerStateUnconfirmed(
                "No power-state confirmation after POWER; not toggling again automatically. "
                "Check Chromecast/TV HDMI-CEC settings and inspect status."
            ) from exc
        _LOG.info("Chromecast reports %s", "on" if on else "standby")
        return True

    async def wake(self) -> None:
        if await self._set_power(True):
            await asyncio.sleep(self.config.wake_delay)

    async def standby(self) -> None:
        await self._set_power(False)

    async def volume(self, up: bool) -> None:
        await self.press("VOLUME_UP" if up else "VOLUME_DOWN")

    async def close(self) -> None:
        if self._supervisor:
            self._supervisor.cancel()
            with suppress(asyncio.CancelledError):
                await self._supervisor
        self.client.disconnect()
        self._availability(False)
