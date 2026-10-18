"""Exercise the wrapper against the installed SDK contract, without a TV."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth

from channelpilot.core.config import RemoteConfig
from channelpilot.core.errors import PairingRequired, RemoteUnavailable
from channelpilot.core.remote import RemoteController


@pytest.fixture
def sdk():
    client = MagicMock(spec=AndroidTVRemote)
    client.current_app = "se.svt.android.svtplay"
    client.is_on = True
    return client


def wrapper(tmp_path, sdk, **kwargs):
    return RemoteController(
        RemoteConfig(
            "192.0.2.1",
            tmp_path / "private",
            command_delay=0,
            wake_delay=0,
            connect_timeout=0.03,
            reconnect_delay=0.001,
            **kwargs,
        ),
        sdk,
    )


async def test_sdk_key_volume_and_explicit_power(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    remote._availability(True)
    await remote.press("DPAD_RIGHT")
    sdk.send_key_command.assert_called_with("DPAD_RIGHT")
    await remote.volume(True)
    sdk.send_key_command.assert_called_with("VOLUME_UP")
    await remote.wake()  # Already on; no power key.
    sdk.send_key_command.assert_called_with("VOLUME_UP")
    sdk.is_on = None
    await remote.wake()
    sdk.send_key_command.assert_called_with("WAKEUP")
    await remote.standby()
    sdk.send_key_command.assert_called_with("SLEEP")
    sdk.is_on = False
    sdk.send_key_command.reset_mock()
    await remote.standby()
    sdk.send_key_command.assert_not_called()
    await remote.close()


async def test_offline_never_sends_and_buffer_failure_invalidates(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    with pytest.raises(RemoteUnavailable):
        await remote.press("HOME")
    sdk.send_key_command.assert_not_called()
    remote._availability(True)
    sdk.send_key_command.side_effect = ConnectionClosed()
    with pytest.raises(RemoteUnavailable):
        await remote.press("HOME")
    assert not remote.connected
    assert remote.epoch == 1
    await remote.close()


async def test_initial_connection_retries_then_library_owns_reconnection(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    remote.validate_credentials = lambda: None
    sdk.async_connect = AsyncMock(side_effect=[CannotConnect(), None])
    observed = []
    remote.add_availability_callback(observed.append)
    remote.start()
    await remote.wait_ready()
    assert sdk.async_connect.await_count == 2
    sdk.keep_reconnecting.assert_called_once()
    # Drive the actual callback registered with the SDK, as a network drop would.
    availability = sdk.add_is_available_updated_callback.call_args.args[0]
    availability(False)
    assert not remote.connected
    waiter = asyncio.create_task(remote.wait_ready())
    await asyncio.sleep(0)
    availability(True)
    await waiter
    assert remote.connected
    assert remote.epoch == 1
    assert observed == [True, False, True]
    await remote.close()
    sdk.disconnect.assert_called()


async def test_connection_timeout_retries_and_shutdown_is_clean(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    remote.validate_credentials = lambda: None
    started = asyncio.Event()

    async def hung_connect():
        started.set()
        await asyncio.Event().wait()

    sdk.async_connect = AsyncMock(side_effect=hung_connect)
    remote.start()
    await started.wait()
    with pytest.raises(TimeoutError):
        await remote.wait_ready()
    await remote.close()
    assert remote._supervisor.done()


async def test_invalid_auth_initial_and_after_reconnect(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    remote.validate_credentials = lambda: None
    sdk.async_connect = AsyncMock(side_effect=InvalidAuth())
    remote.start()
    with pytest.raises(PairingRequired):
        await remote.wait_ready()
    assert sdk.async_connect.await_count == 1
    await remote.close()
    remote = wrapper(tmp_path, sdk)
    remote.validate_credentials = lambda: None
    sdk.async_connect = AsyncMock()
    remote.start()
    await remote.wait_ready()
    auth_callback = sdk.keep_reconnecting.call_args.args[0]
    auth_callback()
    with pytest.raises(PairingRequired):
        await remote.wait_ready()
    await remote.close()


async def test_pair_real_certificate_generation_permissions_and_reuse(tmp_path):
    # The real SDK generates certificates; only network pairing is mocked.
    config = RemoteConfig("192.0.2.1", tmp_path / "private")
    remote = RemoteController(config)
    remote.client.async_start_pairing = AsyncMock()
    remote.client.async_finish_pairing = AsyncMock()
    read_code = AsyncMock(return_value="ABC123")
    await remote.pair(read_code)
    remote.client.async_finish_pairing.assert_awaited_once_with("ABC123")
    cert = remote.certfile.read_bytes()
    key = remote.keyfile.read_bytes()
    assert config.credentials_dir.stat().st_mode & 0o777 == 0o700
    assert remote.keyfile.stat().st_mode & 0o777 == 0o600
    assert remote.certfile.stat().st_mode & 0o777 == 0o600
    remote.validate_credentials()
    await remote.pair(read_code)
    assert remote.certfile.read_bytes() == cert
    assert remote.keyfile.read_bytes() == key
    await remote.close()


@pytest.mark.parametrize("material", [None, b"not a certificate"])
def test_missing_invalid_credentials(tmp_path, sdk, material):
    remote = wrapper(tmp_path, sdk)
    if material:
        remote.config.credentials_dir.mkdir()
        remote.certfile.write_bytes(material)
        remote.keyfile.write_bytes(material)
    with pytest.raises(PairingRequired):
        remote.validate_credentials()


async def test_reject_credential_symlinks(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    remote.config.credentials_dir.mkdir()
    target = tmp_path / "target"
    target.write_text("must stay unchanged")
    remote.keyfile.symlink_to(target)
    with pytest.raises(PairingRequired):
        await remote.pair(AsyncMock(return_value="ABC123"))
    assert target.read_text() == "must stay unchanged"


async def test_expired_certificate_is_detected(tmp_path):
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    remote = RemoteController(RemoteConfig("192.0.2.1", tmp_path / "private"))
    remote.config.credentials_dir.mkdir(mode=0o700)
    await remote.client.async_generate_cert_if_missing()
    cert = x509.load_pem_x509_certificate(remote.certfile.read_bytes())
    key = serialization.load_pem_private_key(remote.keyfile.read_bytes(), password=None)
    now = datetime.now(UTC)
    expired = (
        x509.CertificateBuilder()
        .subject_name(cert.subject)
        .issuer_name(cert.issuer)
        .public_key(cert.public_key())
        .serial_number(cert.serial_number)
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    remote.certfile.write_bytes(expired.public_bytes(serialization.Encoding.PEM))
    with pytest.raises(PairingRequired, match="expired"):
        remote.validate_credentials()
    await remote.close()


async def test_pair_network_failure_is_actionable(tmp_path):
    remote = RemoteController(RemoteConfig("192.0.2.1", tmp_path / "private"))
    remote.client.async_start_pairing = AsyncMock(side_effect=CannotConnect())
    with pytest.raises(RemoteUnavailable, match="Pairing connection failed"):
        await remote.pair(AsyncMock(return_value="ABC123"))
    await remote.close()


async def test_remote_has_no_application_launch_interface(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    assert not hasattr(remote, "launch")
    sdk.send_launch_app_command.assert_not_called()
    await remote.close()


async def test_reported_power_is_forwarded_to_controller_callbacks(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    changes = []
    remote.add_power_callback(changes.append)
    callback = sdk.add_is_on_updated_callback.call_args.args[0]
    callback(False)
    callback(True)
    assert changes == [False, True]
    await remote.close()


@pytest.mark.parametrize("initial,target", [(True, False), (False, True)])
async def test_power_toggle_is_guarded_and_waits_for_report(tmp_path, sdk, initial, target):
    remote = wrapper(tmp_path, sdk)
    sdk.is_on = initial
    remote._availability(True)
    callback = sdk.add_is_on_updated_callback.call_args.args[0]

    def send(key):
        assert key == "POWER"
        sdk.is_on = target
        callback(target)

    sdk.send_key_command.side_effect = send
    if target:
        await remote.wake()
        await remote.wake()
    else:
        await remote.standby()
        await remote.standby()
    sdk.send_key_command.assert_called_once_with("POWER")
    assert remote.is_on is target
    await remote.close()


async def test_unconfirmed_power_toggle_is_not_repeated(tmp_path, sdk):
    from channelpilot.core.errors import PowerStateUnconfirmed

    remote = wrapper(tmp_path, sdk, power_timeout=0.005)
    remote._availability(True)
    sdk.is_on = True
    with pytest.raises(PowerStateUnconfirmed, match="No power-state confirmation"):
        await remote.standby()
    assert remote.is_on is None  # Old state is insufficient to safely toggle again.
    await remote.standby()
    assert [call.args[0] for call in sdk.send_key_command.call_args_list] == ["POWER", "SLEEP"]
    await remote.close()


async def test_unknown_power_never_uses_toggle(tmp_path, sdk):
    remote = wrapper(tmp_path, sdk)
    remote._availability(True)
    sdk.is_on = None
    await remote.standby()
    await remote.wake()
    assert [call.args[0] for call in sdk.send_key_command.call_args_list] == ["SLEEP", "WAKEUP"]
    await remote.close()
