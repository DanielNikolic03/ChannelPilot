"""ADB handles lifecycle/foreground metadata; the TV remote sends navigation keys."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .config import AdbConfig
from .errors import AdbError, AdbPairingRequired, AdbUnavailable, CalibrationRequired
from .navigation import Navigator
from .remote import Remote

_LOG = logging.getLogger(__name__)
_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_COMPONENT = re.compile(r"([A-Za-z][A-Za-z0-9_.]*)/([A-Za-z0-9_.$]+)")
_AUTH_FAILURE = re.compile(r"unauthorized|failed to authenticate|authentication failed", re.I)


def validate_package(package: str) -> None:
    if not isinstance(package, str) or not _PACKAGE.fullmatch(package):
        raise ValueError("Expected an Android package ID such as com.mtssi.supernovabih")


def validate_component(package: str, component: str) -> None:
    match = _COMPONENT.fullmatch(component)
    if not match or match[1] != package:
        raise ValueError("Activity must be a component belonging to the configured package")


def parse_foreground_app(output: str) -> str | None:
    """Read current/resumed metadata, never historical task entries or UI elements."""
    for label in (
        "topResumedActivity",
        "mResumedActivity",
        "ResumedActivity",
        "mCurrentFocus",
        "mFocusedApp",
    ):
        for line in output.splitlines():
            if not re.match(r"\s*" + label + r"\s*[:=]", line):
                continue
            match = re.search(
                r"\b([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)/[\w.$]+", line
            )
            if match:
                return match[1]
    return None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def text(self) -> str:
        return self.stdout + "\n" + self.stderr


@dataclass(frozen=True)
class Service:
    name: str
    host: str
    port: int

    @property
    def endpoint(self) -> str:
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"


def parse_mdns(output: str) -> list[Service]:
    """Ignore pairing ports, legacy services, headers, and malformed records."""
    services = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[1].rstrip(".") != "_adb-tls-connect._tcp":
            continue
        host, separator, port = fields[2].rpartition(":")
        host = host.strip("[]")
        try:
            if not separator or not 1 <= int(port) <= 65535:
                continue
            ipaddress.ip_address(host)
        except ValueError:
            continue
        services.append(Service(fields[0], host, int(port)))
    return services


class AppControl(Protocol):
    connected: bool
    endpoint: str | None

    async def ensure_connected(self, *, recover: bool = True) -> None: ...
    async def launch(self, package: str, activity: str | None = None) -> None: ...
    async def force_stop(self, package: str, *, recover: bool = True) -> None: ...
    async def foreground_app(self) -> str | None: ...
    def invalidate(self) -> None: ...


class AdbController:
    def __init__(self, config: AdbConfig, host: str, remote: Remote) -> None:
        self.config = config
        self.host = host
        self.remote = remote
        self.connected = False
        self.endpoint: str | None = None
        self._lock = asyncio.Lock()
        self._last_recovery = float("-inf")

    def invalidate(self) -> None:
        self.connected = False

    async def _run(self, *arguments: str, input_text: str | None = None) -> CommandResult:
        """No local shell; package/component arguments are also validated for adb shell."""
        try:
            process = await asyncio.create_subprocess_exec(
                self.config.executable,
                "-P",
                str(self.config.server_port),
                *arguments,
                stdin=asyncio.subprocess.PIPE
                if input_text is not None
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AdbError(
                "Cannot execute adb. Install Android SDK Platform Tools and check adb.executable"
            ) from exc
        try:
            async with asyncio.timeout(self.config.command_timeout):
                stdout, stderr = await process.communicate(
                    input_text.encode() if input_text is not None else None
                )
        except (TimeoutError, asyncio.CancelledError):
            # Terminate only our CLI child, never the shared ADB server or other transports.
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(2):
                    await asyncio.shield(process.communicate())
            except TimeoutError:
                _LOG.warning("ADB CLI cleanup timed out")
            self.connected = False
            raise
        return CommandResult(
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    def _check_auth(self, result: CommandResult) -> None:
        if _AUTH_FAILURE.search(result.text):
            self.connected = False
            raise AdbPairingRequired(
                "ADB authorization rejected. Run --adb-pair with the displayed pairing port "
                "as the same OS user as ChannelPilot; remote pairing is separate."
            )

    async def _probe(self, endpoint: str) -> bool:
        result = await self._run("-s", endpoint, "get-state")
        self._check_auth(result)
        return result.returncode == 0 and result.stdout.strip() == "device"

    async def _connect_endpoint(self, endpoint: str) -> bool:
        _LOG.info("Connecting ADB to %s", endpoint)
        result = await self._run("connect", endpoint)
        self._check_auth(result)
        if result.returncode or not re.search(r"(?:already )?connected to ", result.text):
            return False
        if not await self._probe(endpoint):
            return False
        self.endpoint, self.connected = endpoint, True
        _LOG.info("ADB connected: %s", endpoint)
        return True

    async def _addresses(self) -> set[str]:
        try:
            return {str(ipaddress.ip_address(self.host.strip("[]")))}
        except ValueError:
            pass
        try:
            info = await asyncio.get_running_loop().getaddrinfo(
                self.host, None, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise AdbUnavailable("Cannot resolve configured Chromecast host") from exc
        return {str(ipaddress.ip_address(record[4][0])) for record in info}

    async def _discover(self) -> bool:
        addresses = await self._addresses()
        _LOG.info("Discovering Chromecast ADB connection port via adb mdns services")
        result = await self._run("mdns", "services")
        if result.returncode:
            _LOG.warning("ADB mDNS discovery unavailable; check Platform Tools and LAN mDNS")
            return False
        services = sorted(
            parse_mdns(result.stdout), key=lambda service: service.endpoint == self.endpoint
        )
        for service in services:
            # Never choose the first unrelated TV/phone or accidentally connect to a pairing port.
            if str(ipaddress.ip_address(service.host)) in addresses:
                try:
                    if await self._connect_endpoint(service.endpoint):
                        return True
                except TimeoutError:
                    _LOG.info("Discovered ADB endpoint timed out; checking other records")
        return False

    async def _discover_until(self) -> bool:
        while True:
            if await self._discover():
                return True
            await asyncio.sleep(self.config.poll_interval)

    async def _ensure_connected(self, *, recover: bool) -> None:
        if self.connected and self.endpoint:
            try:
                if await self._probe(self.endpoint):
                    return
            except TimeoutError:
                pass
        self.connected = False
        candidates = [self.endpoint] if self.endpoint else []
        if self.config.port is not None:
            endpoint = Service("configured", self.host.strip("[]"), self.config.port).endpoint
            if endpoint not in candidates:
                candidates.append(endpoint)
        for endpoint in candidates:
            try:
                if await self._connect_endpoint(endpoint):
                    return
            except TimeoutError:
                _LOG.info("Saved ADB endpoint timed out; rediscovering the port")
        try:
            async with asyncio.timeout(self.config.discovery_timeout):
                if await self._discover_until():
                    return
        except TimeoutError:
            self.connected = False
        if not recover:
            raise AdbUnavailable("ADB unavailable; menu recovery disabled for this operation")
        if not self.config.recovery_calibrated or not self.config.recovery_steps:
            raise CalibrationRequired(
                "ADB unavailable. Enter the Wireless debugging menu sequence in "
                "adb.recovery_steps and set adb.recovery_calibrated=true after testing, "
                "or enable Wireless debugging manually."
            )
        if time.monotonic() - self._last_recovery < self.config.recovery_cooldown:
            raise AdbUnavailable(
                "ADB still unavailable; menu recovery cooldown prevents retoggling"
            )
        self._last_recovery = time.monotonic()
        _LOG.warning("ADB unavailable; enabling Wireless debugging using the TV remote")
        try:
            async with asyncio.timeout(self.config.recovery_timeout):
                await self.remote.wait_ready()
                await self.remote.wake()
                nav = Navigator(self.remote, self.config.recovery_timeout)
                await nav.press("HOME")
                await nav.run(self.config.recovery_steps)
                # The menu ending is only presumed. A successful ADB connection verifies recovery.
                if await self._discover_until():
                    _LOG.info("Wireless debugging recovery succeeded")
                    return
        finally:
            # Cool down from completion, including a failed/timed-out menu pass.
            self._last_recovery = time.monotonic()
        raise AdbUnavailable("ADB unavailable after Wireless debugging recovery")

    async def ensure_connected(self, *, recover: bool = True) -> None:
        async with self._lock:
            try:
                await self._ensure_connected(recover=recover)
            except TimeoutError as exc:
                self.connected = False
                raise AdbUnavailable("ADB discovery/recovery timed out") from exc

    async def _shell(self, *arguments: str) -> CommandResult:
        assert self.endpoint is not None
        try:
            result = await self._run("-s", self.endpoint, "shell", *arguments)
        except TimeoutError as exc:
            self.connected = False
            raise AdbUnavailable("ADB app command timed out") from exc
        self._check_auth(result)
        if result.returncode or re.search(r"error:|exception", result.text, re.I):
            self.connected = False
            raise AdbError("ADB app command failed; check app installation/authorization and retry")
        return result

    async def launch(self, package: str, activity: str | None = None) -> None:
        validate_package(package)
        if activity is not None:
            validate_component(package, activity)
        async with self._lock:
            try:
                await self._ensure_connected(recover=True)
            except TimeoutError as exc:
                raise AdbUnavailable("ADB discovery/recovery timed out") from exc
            if activity is None:
                for category in ("LEANBACK_LAUNCHER", "LAUNCHER"):
                    result = await self._shell(
                        "cmd",
                        "package",
                        "resolve-activity",
                        "--brief",
                        "--user",
                        "current",
                        "-a",
                        "android.intent.action.MAIN",
                        "-c",
                        "android.intent.category." + category,
                        "-p",
                        package,
                    )
                    for line in result.stdout.splitlines():
                        if _COMPONENT.fullmatch(line.strip()):
                            validate_component(package, line.strip())
                            activity = line.strip()
                            break
                    if activity:
                        break
            if activity is None:
                raise AdbError(
                    f"No launcher activity found for {package}; configure its activity component"
                )
            _LOG.info("Starting app through ADB: %s", package)
            # -S force-stops the target before launch; -W reports activity launch completion.
            result = await self._shell(
                "am", "start", "-S", "-W", "--user", "current", "-n", activity
            )
            if not re.search(r"^Status:\s*ok\s*$", result.stdout, re.M | re.I):
                raise AdbError("ADB did not report successful activity launch")

    async def force_stop(self, package: str, *, recover: bool = True) -> None:
        validate_package(package)
        async with self._lock:
            try:
                await self._ensure_connected(recover=recover)
            except TimeoutError as exc:
                raise AdbUnavailable("ADB discovery/recovery timed out") from exc
            _LOG.info("Force-stopping app through ADB: %s", package)
            await self._shell("am", "force-stop", "--user", "current", package)

    async def foreground_app(self) -> str | None:
        """Resolve contradictory remote reports without navigating or opening Settings."""
        async with self._lock:
            try:
                await self._ensure_connected(recover=False)
                for arguments in (("activity", "activities"), ("window", "windows")):
                    assert self.endpoint is not None
                    result = await self._run("-s", self.endpoint, "shell", "dumpsys", *arguments)
                    self._check_auth(result)
                    if result.returncode:
                        raise AdbError("ADB foreground-app query failed")
                    if package := parse_foreground_app(result.stdout):
                        _LOG.debug("ADB foreground application: %s", package)
                        return package
                return None
            except TimeoutError as exc:
                raise AdbUnavailable("ADB foreground-app check timed out") from exc

    async def pair(self, port: int, read_code: Callable[[], Awaitable[str]]) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("ADB pairing port must be from 1 to 65535")
        endpoint = Service("pairing", self.host.strip("[]"), port).endpoint
        async with self._lock:
            async with asyncio.timeout(120):
                code = (await read_code()).strip()
            if not re.fullmatch(r"\d{6}", code):
                raise AdbPairingRequired("ADB pairing code must be six digits")
            # Supply the secret through stdin, not process arguments or logs.
            result = await self._run("pair", endpoint, input_text=code + "\n")
            if result.returncode or "Successfully paired" not in result.stdout:
                raise AdbPairingRequired(
                    "ADB pairing failed; open Pair using pairing code and retry"
                )
            _LOG.info("ADB pairing completed; host credentials are retained by adb")
