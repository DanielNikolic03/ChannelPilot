# ChannelPilot

ChannelPilot lets an elderly viewer select a TV channel with one key on a USB numpad. A Raspberry Pi reads that numpad and controls MOVE and SVT Play on a separate Chromecast with Google TV using Python and `androidtvremote2`.

The application runs in the background without a desktop, terminal focus, ADB, Waydroid, or screen access. It preserves WayPi-TV's controls and channel order while replacing its architecture with asyncio, isolated app classes, persistent pairing, serialized navigation, and bounded recovery.

**Implementation status:** the Python application and automated tests run without TV hardware. The supplied MOVE/SVT sequences are reference profiles from WayPi-TV, **not verified Chromecast profiles**. They are disabled until you configure app launching and calibrate navigation on your device. ChannelPilot cannot observe which channel is playing. A completed sequence means no detectable error occurred, not verified playback.

## Hardware and dependencies

- Raspberry Pi 5 with Raspberry Pi OS and Python 3.11 or newer.
- A USB numpad and a Chromecast with Google TV on the same LAN as the Pi.
- MOVE (`com.mtssi.supernovabih`) and SVT Play (`se.svt.android.svtplay`) installed, signed in, and initially set up on the Chromecast.
- Android TV Remote Service enabled on the Chromecast. Reserve its IP in your router.
- `androidtvremote2==0.3.2`, `cryptography>=42` for credential validation, and Linux-only `evdev`. These install through `pyproject.toml`; development checks use pytest, pytest-asyncio, Ruff, and mypy.

macOS supports pairing, remote commands, configuration validation, and terminal tests. Physical numpad input requires Linux. Network access must permit the Chromecast's remote service ports (normally TCP 6466 and 6467).

## Quick setup

On Raspberry Pi OS, install Python and the headers needed if evdev must be built:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-dev build-essential linux-libc-dev git

git clone https://github.com/DanielNikolic03/ChannelPilot.git
cd ChannelPilot
python3 -m venv .venv
.venv/bin/python -m pip install .
cp config/config.example.json config/config.json
```

On macOS, use Python 3.11+ and the same virtual environment/install commands; evdev is omitted automatically. Change `remote.host` in `config/config.json` to the Chromecast's address, then validate:

```sh
.venv/bin/python -m channelpilot --check-config
```

Configuration validation does not connect or generate credentials. It reports uncalibrated applications without rejecting the otherwise valid file. Errors in keys, sequence steps, channel registration, and numeric settings fail validation.

## Pair once

Keep the TV visible during initial setup:

```sh
.venv/bin/python -m channelpilot --pair
```

Enter the pairing code shown on the Chromecast. Pairing has a 120-second input deadline and bounded network operations. Rerun the command if a code is rejected. Codes and private credentials are never intentionally logged, including in debug mode; upstream protocol debug logging is suppressed.

Credentials default to `~/.local/state/channelpilot/client-cert.pem` and `client-key.pem`, outside the repository. The directory is mode 0700 and files are mode 0600. Later runs reuse them, including after Pi restarts. Keep the system clock correct for certificate validation.

If authentication is rejected, expired, or corrupted, stop the service and run `--pair` as the **same account with the same configuration** used by the service. For invalid or expired PEM files, move both files to a private backup outside the repository, then pair to generate a fresh pair. Normal remote/network failures never automatically delete credentials or initiate interactive pairing.

## Configure and calibrate

Edit `config/config.json`; paths are relative to that file, and `~` expands to the running user's home. See the complete [example configuration](config/config.example.json) and [hardware calibration procedure](docs/calibration.md).

| Setting | Meaning |
| --- | --- |
| `remote.host` | Chromecast IP address or hostname |
| `remote.credentials_dir` | Persistent private directory outside Git |
| `remote.connect_timeout` | Deadline for initial connection attempts and readiness waits |
| `remote.reconnect_delay` | Delay between initial connection failures; library handles reconnects after success |
| `remote.command_delay` | Minimum processing pause after each buffered remote command |
| `remote.wake_delay` | Additional pause after WAKEUP |
| `channels_file` | JSON channel mapping path |
| `input.device` | One explicit event device, preferably `/dev/input/by-id/...-event-kbd` |
| `input.grab` | Exclusively grab that device only; default false |
| `input.debounce` | Per-key press debounce interval in seconds |
| `input.reconnect_delay` | Delay before reopening an unplugged numpad |
| `input.key_overrides` | Optional Linux key-code strings mapped to actions |
| `operation_timeout` | Deadline per complete attempt, including recovery on the retry |
| `retry_limit` | 0 or 1, never more than one retry |
| `standby_on_failure` | Attempt explicit SLEEP after final detectable failure |
| `default_channel` | Channel ID selected by Enter; null makes Enter wake only |
| `log_level` | INFO by default; DEBUG logs individual commands |

The example's `default_channel` is `rts1`. Each app has its own package, `calibrated` flag, `launch_link` or `launch_steps`, startup/home delays, baseline position, reset sequence, and selection sequences. Tune these independently. Restart ChannelPilot after configuration changes; it does not hot reload.

No reliable MOVE or SVT Android TV launch deep link was established from the reference sources. There is no fabricated default URL. `launch_link` accepts a tested URL/deep link, never a bare package name: package launching through the remote protocol is unreliable. Alternatively configure `launch_steps` from the Google TV HOME screen to the pinned app. The application sends HOME before those steps, waits `home_delay`, executes them, and waits `startup_delay`. HOME does **not** force-stop or reset a resumed app; `reset_steps` must accommodate that.

Channel selection refuses to send commands until the responsible app is marked calibrated and both its launch and reset strategies exist. Manual terminal commands remain available for setup.

## Controls and channels

| Numpad | Action |
| --- | --- |
| 0–9 | Select mapped channel, waking the device if necessary |
| + / − | Next / previous channel by numeric key, wrapping 9 → 0 and 0 → 9 |
| Enter | Start `default_channel`, or wake only when it is null |
| Backspace | Explicit standby |

| Key | Channel | App | Navigation position from original source |
| --- | --- | --- | --- |
| 1 | SVT1 | SVT Play | 1 |
| 2 | SVT2 | SVT Play | 2 |
| 3 | Kunskapskanalen | SVT Play | 4 |
| 4 | SVT24 | SVT Play | 5 |
| 5 | BN Muzika | MOVE | 6 |
| 6 | BN | MOVE | 5 |
| 7 | Happy | MOVE | 4 |
| 8 | Prva | MOVE | 3 |
| 9 | Pink | MOVE | 2 |
| 0 | RTS 1 | MOVE | 1 |

SVT's original source calls its positions placeholders; verify them, especially the skipped position 3. MOVE positions are relative list positions rather than displayed channel numbers. See [reference findings](docs/reference-notes.md).

Edit [channels.json](config/channels.json) to remap keys. IDs and keys must be unique, positions must be positive integers, and every app must be registered. To replace an existing key with another channel:

```json
{"id": "another-channel", "name": "Another Channel", "app": "move", "navigation_id": 7, "key": "5"}
```

Position 7 above is an illustration; use the actual calibrated position and remove the previous mapping for key 5. File order does not affect +/− ordering. Rapid +/− presses advance from the newest requested channel, including pending requests.

## Navigation and recovery behavior

A single asyncio worker owns all channel navigation, manual commands, wake, and standby. Only one operation can run at a time. There is one pending slot: new requests replace pending requests across all action types. An in-progress operation finishes or reaches a detectable error/timeout before the next starts. A standby request therefore replaces pending navigation but waits for the active operation to finish; a subsequent key can replace pending standby. Duplicate requests for the active channel are coalesced when nothing newer is pending.

The state machine exposes IDLE, CONNECTING, READY, LAUNCHING_APP, NAVIGATING, RECOVERING, ERROR, and STANDBY. Terminal `status` shows requested channel, last attempted channel, presumed channel, app/confidence, connection status, sequence completion, and last error. Device-reported foreground app is confirmed as a protocol fact; all channel/menu positions remain assumed. A READY state after wake alone does not imply playback. STANDBY records completed standby instructions, not verified TV-panel power.

Each channel request launches from HOME and runs the app's reset sequence before navigating from its configured baseline. No relative switch uses a stale previous channel. App changes, disconnects, standby/manual operations, and shutdown invalidate presumed state. `Controller.invalidate_navigation()` explicitly clears it. Disconnect epochs prevent a rapid disconnect/reconnect from allowing an old sequence to continue.

Detectable connection/transport failure, unexpected reported foreground app, or timeout aborts the attempt. Channel recovery invalidates state, waits for connectivity, wakes, returns HOME, and retries the **entire** operation once. Power/manual-command retries wait for connectivity and repeat their operation without launching or navigating an app. A failed recovery/second attempt enters ERROR and waits for a new request. Auth/calibration problems stop without a blind retry. Unexpected exceptions are logged and contained by the worker. Each attempt has `operation_timeout`; optional failure standby has its own deadline. No key is sent while the wrapper knows it is disconnected.

The connection task retries initial network failures. After connection, `androidtvremote2.keep_reconnecting()` owns reconnection and backoff. Reconnection restores readiness and invalidates navigation; it never resumes midway through an old command sequence. If an operation exhausts both deadlines while offline, the viewer must press a key again after connectivity returns. Reconnection attempts continue while the service runs; individual navigation waits stay bounded.

Remote key calls only buffer data; they do not acknowledge channel selection. Missing foreground-app information is treated as unknown, not proof of launch failure or success. App loading screens, account dialogs, silent input loss, changed UI, and wrong playback can remain undetectable without screen access. There is no screenshot, OCR, or image verification code. Calibration and periodic human observation are necessary.

WAKEUP and SLEEP are explicit Android commands, never POWER toggles. Known matching power states suppress redundant commands. Actual television power and volume depend on your Chromecast/TV's HDMI-CEC and audio setup; test these on the real hardware.

## Customize navigation

Sequences contain JSON objects:

```json
[
  {"press": "DPAD_LEFT", "times": 2, "delay": 1},
  {"wait": 0.5},
  {"press": "DPAD_CENTER", "if_app": "com.example.tv"},
  {"press": "WAKEUP", "if_power": false},
  {"checkpoint": "menu opening sequence finished"}
]
```

Conditions use only available protocol state; an unknown value skips a conditional step. Delays follow each press and are additional to `remote.command_delay`. Repetition and sequence lengths are bounded, and execution has timeouts. Checkpoints are named sequence markers for logs, not UI observations. Keys are validated against the installed library's Android enum.

MOVE independently applies its vertical list prefix, delta from the RTS 1 baseline, and confirmation/back suffix. SVT independently applies horizontal deltas from its SVT1 baseline. You can edit JSON timing, launch, reset, prefix, and suffix steps without changing controller code. If Chromecast's app layout differs fundamentally from these reference models, adjust that application's Python class.

## Test from a terminal (including macOS)

```sh
.venv/bin/python -m channelpilot --test --debug
```

Enter a digit, `+`, `-`, `wake`, `standby`, `status`, `invalidate`, or `quit`. For calibration use `key HOME`, `key DPAD_LEFT`, `key DPAD_CENTER`, `key BACK`, `key VOLUME_UP`, `key VOLUME_DOWN`, and `key VOLUME_MUTE`. `launch <deep-link>` tests a candidate URL manually. Pairing and basic remote commands work without calibrating channel navigation. Manual commands share the worker and invalidate presumed navigation state. This is a human test interface; the service never needs an interactive terminal.

## Run at boot without root

The following installation uses a dedicated service user and a group granted access only to the chosen numpad. First complete interactive calibration, then deploy the configuration. Use a fresh virtual environment at its final path; virtual environments should not be moved.

```sh
sudo groupadd --system channelpilot-input
sudo useradd --system --user-group --home-dir /var/lib/channelpilot --shell /usr/sbin/nologin channelpilot
sudo git clone https://github.com/DanielNikolic03/ChannelPilot.git /opt/channelpilot
sudo python3 -m venv /opt/channelpilot/.venv
sudo /opt/channelpilot/.venv/bin/python -m pip install /opt/channelpilot
sudo install -d -m 0755 /etc/channelpilot
sudo install -m 0644 config/config.json /etc/channelpilot/config.json
sudo install -m 0644 config/channels.json /etc/channelpilot/channels.json
sudo install -d -o channelpilot -g channelpilot -m 0700 /var/lib/channelpilot
```

In `/etc/channelpilot/config.json`, set `remote.credentials_dir` to `/var/lib/channelpilot` and `channels_file` to `channels.json`. If those service accounts/groups already exist, reuse them instead of rerunning their creation commands.

Find the numpad's stable event path and vendor/product identifiers:

```sh
ls -l /dev/input/by-id/
udevadm info --attribute-walk --name=/dev/input/eventN
```

Replace `eventN` with the numpad's event device. Do not grant access to all keyboards. Create `/etc/udev/rules.d/99-channelpilot-numpad.rules` using the following [template](scripts/99-channelpilot-numpad.rules.example), replacing `VVVV`/`PPPP` with that numpad's USB vendor/product IDs. If multiple identical devices exist, also constrain their serial or physical path.

```udev
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{idVendor}=="VVVV", ATTRS{idProduct}=="PPPP", GROUP="channelpilot-input", MODE="0660"
```

Set `input.device` to the matching `/dev/input/by-id/...-event-kbd` path. Reload rules, replug the numpad, and pair as the service user:

```sh
sudo udevadm control --reload-rules
# Unplug and reconnect the numpad now.
sudo -u channelpilot /opt/channelpilot/.venv/bin/python -m channelpilot --config /etc/channelpilot/config.json --pair
sudo install -m 0644 /opt/channelpilot/scripts/channelpilot.service /etc/systemd/system/channelpilot.service
sudo systemctl daemon-reload
sudo systemctl enable --now channelpilot
journalctl -u channelpilot -f
```

Use the service-user pairing command before service startup (or after stopping it). It stores credentials where the service can read them. The service receives `channelpilot-input` as a supplementary group, uses a private state directory and restrictive umask, restarts on crashes, and shuts down on SIGTERM. It has no desktop dependency or keyboard-focus requirement and does not run as root. `PrivateDevices` is intentionally absent because the numpad must remain accessible.

For foreground numpad testing as your own user, explicitly add that user to `channelpilot-input` and log in again. Do not run the complete application as root to work around device permissions.

## Add another TV application

1. Create `channelpilot/apps/example.py`, subclass `TVApp`, and implement `async navigate_to_channel(channel)`. Reuse `open()` and `reset_navigation()` or override them for the app's needs. Use `self.nav` for checks and bounded sequences, and mark positions assumed.
2. Register its type in `apps/registry.py`:

```python
from .example import ExampleApp

APP_TYPES["example"] = ExampleApp
```

3. Add `apps.example` configuration with its package, launch strategy, `reset_steps`, calibrated delays, and `calibrated` flag. If the config name differs from its registered type, specify `"type": "example"`.
4. Add channel records with `"app": "example"` and unused/remapped keys.
5. Test with the fake remote, then follow hardware calibration before enabling it for the viewer.

The controller only resolves a channel's configured app and calls its interface. It has no MOVE/SVT-specific command sequences.

## Development checks

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check channelpilot tests
.venv/bin/ruff format --check channelpilot tests
.venv/bin/mypy channelpilot
```

Tests use a fake transport and the installed SDK interface; they do not contact a television. They cover mapping, app selection, numpad codes, holds/debounce/replugging, latest-request handling, navigation, disconnect epochs, foreground-app checks, timeouts, recovery limits, credential generation/reuse/permissions, and reconnection callbacks. Real-device acceptance is a human-observed procedure in [calibration.md](docs/calibration.md), not an automated playback-verification test. Future hardware tests must use the `hardware` pytest marker.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `Calibrate ... first` | Configure a launch strategy, verify cold/resumed reset behavior, then mark that app calibrated |
| Wrong channel, no error | Inspect/tune baseline and offsets with a human watching; successful buffered keys do not verify playback |
| Unexpected active application | Check package/launch strategy and startup delay; handle consent/account prompts manually during setup |
| Network unavailable | Check reserved IP, same LAN, Remote Service, and router isolation; inspect connection logs |
| Manual pairing required | Stop the service; pair using its account/config and restart; move invalid PEM files aside if needed |
| Numpad unavailable | Check stable path, dedicated udev group, permissions and USB cable; listener retries only that path |
| Long key selects repeatedly | Check device events; repeat value 2 and duplicate held-key downs are ignored, debounce is configurable |
| Navigation too slow/times out | Tune delays using the real device and set an appropriate complete-attempt deadline |
| TV doesn't wake/sleep | Check Chromecast power state and television CEC; the remote protocol cannot guarantee panel behavior |
| Error after network returns | A new key starts a fresh operation; expired requests are not replayed indefinitely |

Logs are available through SSH with `journalctl -u channelpilot -n 100` and live with `-f`. Use `--debug` for individual commands during foreground tests. Stop/start with `sudo systemctl stop channelpilot` and `sudo systemctl start channelpilot`. Before updating, stop the service; update the checkout and reinstall its Python package, then rerun checks and hardware calibration for changed app interfaces.

## Sources

Navigation concepts and channel positions were studied from [WayPi-TV](https://github.com/DanielNikolic03/WayPi-TV/tree/2d9fb2f94944504bbb1f3db67141acd22bb3858e). Remote calls follow the public [androidtvremote2 API](https://github.com/tronikos/androidtvremote2/blob/main/src/androidtvremote2/androidtv_remote.py) and [demo](https://github.com/tronikos/androidtvremote2/blob/main/src/demo.py), including its warning about package-name launching. See [reference-notes.md](docs/reference-notes.md) for the exact findings and remaining hardware questions.
