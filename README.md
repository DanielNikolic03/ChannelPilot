# ChannelPilot

ChannelPilot lets a viewer select a TV channel with one key on a USB numpad. A Raspberry Pi reads the numpad and controls MOVE and SVT Play on a Chromecast with Google TV.

The application is Python 3.11+ and asyncio. **Android TV Remote (`androidtvremote2`) sends navigation/power/volume keys. ADB starts and force-stops apps.** It runs in the background without a desktop, terminal focus, screenshots, OCR, or image recognition. The Pi does not run Android applications or Waydroid.

The code and fake-device tests run without a television. Real navigation and the Wireless debugging recovery menu must be calibrated on your Chromecast. Sequence completion means no detectable error occurred; playback is presumed, never visually verified.

## Install

Requirements: Raspberry Pi 5 / Raspberry Pi OS, USB numpad, Chromecast with Google TV on the same LAN, MOVE and SVT Play installed and signed in. Reserve the Chromecast IP. Android TV Remote Service must be enabled, and initial ADB pairing requires Developer options → Wireless debugging. Devices without modern wireless debugging need a manually configured reachable ADB port; automatic mDNS recovery targets the modern wireless connection service.

On Raspberry Pi OS:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-dev build-essential linux-libc-dev git adb

git clone https://github.com/DanielNikolic03/ChannelPilot.git
cd ChannelPilot
python3 -m venv .venv
.venv/bin/python -m pip install .
cp config/config.example.json config/config.json
```

Use recent [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) if your distro's `adb` lacks `pair` or `mdns services`. Check with `adb version` and `adb mdns check`. On macOS, install Python 3.11+ and Platform Tools, then use the same virtual environment and pip commands. Linux-only evdev is omitted on macOS; terminal control works there.

Set `remote.host` in `config/config.json` to the Chromecast IP. ADB uses that same host and will never select an unrelated discovered phone/TV. Set `adb.executable` to the absolute executable path if `adb` is not in PATH.

```sh
.venv/bin/python -m channelpilot --check-config
```

Validation does not connect, generate credentials, or execute recovery menus. It reports uncalibrated apps and configuration migration needs.

## Pair both protocols once

Remote pairing and ADB pairing are independent. Run both as the account that will run ChannelPilot; pairing your Mac does not authorize a different Raspberry Pi/account.

First pair the Android TV Remote protocol:

```sh
.venv/bin/python -m channelpilot --pair
```

Enter the code shown on the TV. Remote certificates default to `~/.local/state/channelpilot/`, outside Git, with a private directory and mode-0600 files. Normal restarts reuse them. For missing/rejected/expired credentials, stop the service, run `--pair` with the same account/config, and restart. Move invalid/expired PEM files to a private backup before generating replacements. Keep the system clock correct.

Next, enable **Wireless debugging → Pair device with pairing code** on the Chromecast. Use the **pairing port** displayed there:

```sh
.venv/bin/python -m channelpilot --adb-pair 40000
```

`40000` is an example; use your actual pairing port. Enter the six-digit code when prompted. The code goes to adb through stdin, not command arguments or logs. ADB retains its own host keys, normally in `~/.android/`, outside this repository. The app does not reset those keys, kill the shared ADB server, or revoke authorizations.

The pairing port differs from the normal connection port. After initial authorization, ChannelPilot discovers the connection port through `adb mdns services`. Example:

```text
List of discovered mdns services
adb-2A20105GN4CCJH-2CkQNv  _adb-tls-connect._tcp  192.168.0.115:41191
```

ChannelPilot selects `_adb-tls-connect._tcp` for the configured host and verifies `get-state` reports `device`. It ignores pairing ports, legacy advertisements, unrelated hosts, and malformed records. `adb.port` optionally supplies an initial connection port; null uses discovery. Cached/configured ports are retried, then discovery finds a changed port before any settings navigation is attempted.

## Automatic Wireless debugging recovery

If ADB cannot connect, ChannelPilot can navigate the Chromecast menus using the already-paired TV remote, enable Wireless debugging, then rediscover its new port with `adb mdns services` and reconnect.

Enter **your own device-specific menu steps** in `config/config.json`:

```json
"adb": {
  "executable": "adb",
  "port": null,
  "server_port": 5037,
  "command_timeout": 10,
  "discovery_timeout": 5,
  "recovery_timeout": 60,
  "poll_interval": 1,
  "recovery_cooldown": 60,
  "shutdown_timeout": 8,
  "recovery_calibrated": false,
  "recovery_steps": []
}
```

**`adb.recovery_steps` is the list to fill in manually.** It uses the same step format as app navigation. ChannelPilot sends HOME before the list, so begin with a wait if HOME needs time to load, followed by the actual menu navigation you tested. Do not add guessed steps or assume generic Google TV menu positions match your Chromecast.

Set `recovery_calibrated=true` after you test the sequence. It must leave debugging **enabled**, including handling any known network permission prompt. Blindly toggling a switch can disable an already-enabled setting: test what happens when debugging is on as well as off. Navigation has no screen observation; if you cannot make the menu sequence repeatable, leave it disabled and enable debugging manually.

Recovery uses a bounded deadline and one menu pass, followed by a cooldown so a failed whole-operation retry does not immediately toggle the setting again. The app treats successful ADB connection as recovery confirmation. If the remote is disconnected, it cannot send recovery keys. Pairing/authentication rejection requires manual `--adb-pair`; menu recovery cannot recreate authorization or read a fresh pairing code. Reboot behavior depends on device firmware; test an actual Chromecast restart/power outage rather than assuming it always disables debugging or always retains authorization.

mDNS must be supported on the LAN. Discovery failure can mean debugging is disabled, networking/multicast is unavailable, or the port has not appeared yet; the app cannot infer the switch state directly from a failed connection. See [calibration](docs/calibration.md).

## App lifecycle and navigation

When opening a different app, initializing an unknown state, or recovering from a failure:

```text
Wake if needed
→ force-stop previous app when switching apps
→ ensure ADB connection (recover debugging if configured and necessary)
→ resolve the target's launcher activity
→ ADB am start -S -W (fresh target process)
→ wait startup_delay
→ run startup_steps
→ run reset_steps
→ select_prefix → move by channel offset → select_suffix
```

ADB first resolves an Android TV `LEANBACK_LAUNCHER` activity, then falls back to `LAUNCHER`. Optional `activity` overrides resolution with a full component such as `com.mtssi.supernovabih/.activities.MainActivity`. ADB uses explicit device selection and validates package/activity arguments. It checks process exit codes, command errors, and launch status; it never sends ADB navigation/input commands.

For subsequent selections within the running app, ChannelPilot keeps its last presumed channel position and sends only `select_prefix`, the movement delta from that position, and `select_suffix`. It does not run ADB launch/stop, startup delays/steps, or reset steps again. Selecting the presumed current channel sends no keys. For example, SVT1 → SVT2 moves RIGHT once and confirms; SVT2 → Kunskapskanalen uses its configured position difference, rather than navigating again from SVT1.

Reuse requires a completed prior channel sequence, the same controlled app, an uninterrupted remote connection, valid assumed position, and no conflicting reported foreground app or standby state. Missing foreground-app information remains unknown: continuity is an assumption, not playback verification. Manual commands, explicit invalidation, reported app changes, power-off, disconnect/reconnect, and navigation failures invalidate the position. The next channel request performs a fresh launch/startup/reset. ADB connectivity is not needed for normal relative switches; it is checked when lifecycle/recovery needs it.

Switching apps force-stops the previously tracked/configured foreground app first. Manual close and clean program shutdown force-stop the app opened by ChannelPilot. Standby attempts a bounded force-stop without Settings recovery, but an ADB close failure cannot prevent the remote power-off request. Shutdown cleanup is bounded and never opens Settings to recover ADB; an unavailable transport is logged and cleanup stops. The shared ADB server is left running to preserve other clients.

Force-stop does not clear data, sign out accounts, or guarantee the app forgets its last channel. Test its actual cold-start behavior. All in-app steps and playback remain presumed. Remote foreground-app reporting can detect an unexpected package, but cannot verify selected channels.

App settings:

| Field | Meaning |
| --- | --- |
| `package` | Android app package ID used by ADB |
| `activity` | Optional full activity component; null uses launcher resolution |
| `calibrated` | Allows automatic channel procedures after human testing |
| `startup_delay` | Pause after ADB launch completion, before navigating |
| `startup_steps` | Optional in-app preparation after each fresh launch |
| `reset_steps` | Establish the baseline on initialization; use `[]` if startup already reaches it |
| `baseline` | Navigation position presumed after reset |
| `select_prefix` | Open the channel selector |
| `channel_delay` | Pause after each movement between positions |
| `select_suffix` | Confirm selection/dismiss selector |

`reset_steps` run after a fresh launch to initialize the baseline, including after invalidation or recovery. They are skipped for ordinary channel changes within the same app. Reset must still account for any state persisted on disk. Keep `reset_steps: []` when the fresh launch/startup steps already establish the baseline; this explicitly declares that no additional reset keys are needed. After reset, if the target equals `baseline`, selection prefix/suffix are skipped, so reset must leave that baseline channel playing. During relative switching, moving back to the baseline from another position uses normal movement and selection; it does not reset the app. MOVE uses vertical offsets and SVT horizontal offsets in their own Python classes. Configure positions in `channels.json`; layouts requiring a different model need an app-class adjustment.

**Migration from the earlier remote-launch version:** `launch_link`, `launch_steps`, and `home_delay` are no longer used. Existing nonempty `launch_steps`/`launch_link` trigger a migration message before automatic channel navigation. Your old settings are preserved in local `config.json`: remove Google TV launcher steps, move any needed app-internal steps into `startup_steps`/`reset_steps`, clear the old launch fields, and recalibrate for a cold start. Manual `open`/`close` works even while an app is uncalibrated or still needs migration.

## Controls and mappings

```sh
# Mac/SSH terminal tests
.venv/bin/python -m channelpilot --test --debug

# Raspberry Pi physical numpad
.venv/bin/python -m channelpilot
```

Terminal commands:

```text
adb-connect
open move
open svt
launch com.mtssi.supernovabih
close move
key HOME
key DPAD_RIGHT
key DPAD_CENTER
key VOLUME_UP
key VOLUME_DOWN
key VOLUME_MUTE
status
invalidate
quit
```

`open` and its alias `launch` accept registered app names/package IDs and use ADB. They only launch/wait/check the app, without configured startup/reset/channel navigation, so you can calibrate manually. `close` force-stops that registered app. Deep-link URL launching through the remote library has been removed. Individual `key` commands still use the remote library. `status` includes reported Chromecast `is_on`, separate remote/ADB connectivity, ADB endpoint, requested/attempted/presumed channels, sequence completion, confidence and errors. It is last-known status, not a live ADB poll. Manual commands invalidate presumed navigation. A READY status after opening alone is not a verified channel.

In either mode, digits select channels; +/− select the next/previous numeric key with wrapping. Enter and `wake` only request power on; they never select a channel or open RTS 1. The retired `default_channel` setting is ignored. Backspace/`standby` attempts to close the opened app and requests power off even if ADB cleanup fails.

Power uses a state-checked `POWER` key, as in the [Home Assistant client for androidtvremote2](https://github.com/home-assistant/core/blob/dev/homeassistant/components/androidtv_remote/media_player.py): send it only when the reported state differs from the request, and do nothing when the reported state already matches. `remote.power_timeout` bounds waiting for confirmation. A missing confirmation invalidates the power estimate and reports an error without automatically retoggling. If the power state is unknown, use explicit WAKEUP/SLEEP commands with a warning instead of a blind toggle; those commands may be unsupported by the device. `status.is_on` is the Chromecast's last-known reported state (or null), not proof of TV-panel power. TV panel/volume behavior depends on HDMI-CEC/audio settings; see [Google's CEC guidance](https://support.google.com/googlehome/answer/7498991).

| Key | Channel | App | Navigation position |
| --- | --- | --- | --- |
| 1 | SVT1 | svt | 1 |
| 2 | SVT2 | svt | 2 |
| 3 | Kunskapskanalen | svt | 4 |
| 4 | SVT24 | svt | 5 |
| 5 | BN Muzika | move | 6 |
| 6 | BN | move | 5 |
| 7 | Happy | move | 4 |
| 8 | Prva | move | 3 |
| 9 | Pink | move | 2 |
| 0 | RTS 1 | move | 1 |

Positions are reference values from WayPi-TV, with SVT positions originally labeled placeholders. Verify them on your account/app version. IDs and keys are unique. To remap a key, edit `channels.json`; for example replace key 5's entry with the correct calibrated record:

```json
{"id":"another-channel","name":"Another Channel","app":"move","navigation_id":7,"key":"5"}
```

Position 7 is illustrative. +/− order follows numeric keys, not JSON file order, and rapid presses advance from the newest requested channel.

## Sequence format and reliability

```json
[
  {"press":"DPAD_LEFT","times":2,"delay":1},
  {"wait":0.5},
  {"press":"DPAD_CENTER","if_app":"com.example.tv"},
  {"press":"WAKEUP","if_power":false},
  {"checkpoint":"sequence marker; not screen verification"}
]
```

Delays follow each press, in addition to `remote.command_delay`. Conditions use only available reported app/power facts; unknown values skip conditional steps. Repetitions and sequence lengths are bounded, and complete attempts time out. Checkpoints only label progress in logs. The same utilities run app navigation and ADB settings recovery; no visual verification is introduced.

One worker serializes channels, app lifecycle, recovery-menu navigation, power, and manual commands. There is one pending slot: the newest request replaces older pending requests. An active operation finishes/fails/times out before the next starts; no rapid-key cancellation interleaves apps. Pending standby can be replaced by a later channel key and vice versa.

The states are IDLE, CONNECTING, READY, LAUNCHING_APP, NAVIGATING, RECOVERING, ERROR, STANDBY. Detectable failures invalidate presumed navigation, stop the current sequence, and allow one whole-operation retry. Channel retries return HOME and repeat fresh app launch/startup/reset/selection. A second failure waits in ERROR for a new request. Auth/calibration issues stop without blind retry. `retry_limit` allows only 0 or 1. Optional `standby_on_failure` is bounded. Unexpected exceptions are logged and contained by the worker.

`operation_timeout` bounds each full attempt and must include possible ADB connection/recovery plus launch/startup/reset/selection. Each ADB CLI child also has `adb.command_timeout`; timed-out/cancelled children are killed/reaped. No shared server is killed. Remote reconnects use the library's documented mechanism. Disconnects invalidate both presumed navigation and cached ADB health. Old navigation never resumes after a disconnect epoch change; a complete retry or a new request starts again. Unobservable silent key loss, wrong channel selection, login prompts and app UI changes remain limitations.

## Numpad and other configuration

Find the numpad on the Pi with `ls -l /dev/input/by-id/` and use its complete stable `...-event-kbd` path in `input.device`. Unplug it to see which entry disappears. The listener opens only that device, optionally grabs only it, filters repeat/held/bouncing keys, and reopens the same path after a disconnect. Mac `--test` ignores `input.device`.

Other settings in [config.example.json](config/config.example.json): `remote.connect_timeout`, `remote.reconnect_delay`, `remote.command_delay`, `remote.wake_delay`, `remote.power_timeout`, `channels_file`, `input.grab`, `input.debounce`, `input.reconnect_delay`, optional `input.key_overrides`, `log_level`, and complete-attempt/retry settings above. Paths are relative to the configuration file; `~` means the service account's home. Restart after changing config; it is not hot reloaded. DEBUG logs remote keys without upstream pairing payloads or ADB pairing codes.

## Boot service without root

Deploy the code and a virtual environment at its final path:

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

Reuse existing accounts/groups instead of recreating them. In service config set `remote.credentials_dir=/var/lib/channelpilot`, `channels_file=channels.json`, and `adb.executable` to the installed executable (normally `/usr/bin/adb`). Keep ADB host keys in `/var/lib/channelpilot/.android/`, outside Git. Service HOME is explicitly `/var/lib/channelpilot`; the state directory is writable despite filesystem hardening.

Identify the numpad USB vendor/product IDs with `udevadm info --attribute-walk --name=/dev/input/eventN`. Install the [udev template](scripts/99-channelpilot-numpad.rules.example) as `/etc/udev/rules.d/99-channelpilot-numpad.rules`, replacing VVVV/PPPP. Constrain serial/physical path too if identical devices exist. It grants the dedicated group access to the numpad, not all keyboards. Set `input.device` to its by-id event path.

```sh
sudo udevadm control --reload-rules
# Replug the numpad.
sudo -u channelpilot env HOME=/var/lib/channelpilot /opt/channelpilot/.venv/bin/python -m channelpilot --config /etc/channelpilot/config.json --pair
# Open Wireless debugging > Pair using pairing code; replace 40000:
sudo -u channelpilot env HOME=/var/lib/channelpilot /opt/channelpilot/.venv/bin/python -m channelpilot --config /etc/channelpilot/config.json --adb-pair 40000
sudo install -m 0644 /opt/channelpilot/scripts/channelpilot.service /etc/systemd/system/channelpilot.service
sudo systemctl daemon-reload
sudo systemctl enable --now channelpilot
journalctl -u channelpilot -f
```

ADB's local server is shared at port 5037 by default. Pairing and runtime must use the same server/account. If another account owns that server, choose an unused `adb.server_port` (e.g. 5038) for both pairing/runtime rather than killing others' sessions. Keep the same service state directory across reboots.

The service runs as `channelpilot`, receives the numpad's group, uses umask 0077, starts without a desktop/login, logs to the journal, restarts on crashes, and responds to SIGTERM with bounded app cleanup. No root runtime or monitor is required. For personal foreground numpad tests, explicitly add your user to `channelpilot-input` and log in again.

## Add an application and develop

Create a `TVApp` subclass in `channelpilot/apps/` and implement its own navigation using `self.nav`; the base class handles ADB launch/stop and startup preparation. Register the type in `APP_TYPES` in `apps/registry.py`, add its settings/package/calibrated steps under `apps`, and register channels pointing to that config name. A different config name can select the implementation with `"type":"example"`. The central controller contains no MOVE/SVT navigation commands.

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check channelpilot tests
.venv/bin/ruff format --check channelpilot tests
.venv/bin/mypy channelpilot
```

Tests use injected fake ADB/remote clients and scripted CLI output, never the real television/ADB server. They cover mapping, input, app switching/force-stop, port changes, service filtering, recovery menus/cooldown, authorization failure, process cleanup, timeouts and retries. Human-observed hardware acceptance is in [calibration.md](docs/calibration.md); future hardware pytest tests must be marked `hardware`. CI checks Python 3.11/3.13 on Linux and 3.13 on macOS.

## Troubleshooting and sources

Inspect logs with `journalctl -u channelpilot -n 100` or `-f`; use foreground `--test --debug` for individual keys. Stop/start with `sudo systemctl stop channelpilot` / `start channelpilot`. Update while stopped, reinstall the package in its venv, rerun checks and relevant hardware calibration.

- **ADB unavailable:** check host, debugging setting, `adb mdns services`, LAN multicast, executable path and configured recovery steps.
- **ADB authorization rejected:** stop service and run `--adb-pair` as the same account/server; enabling the switch cannot restore revoked authorization.
- **Old launch settings migration:** move in-app preparation to `startup_steps`/`reset_steps`, remove launcher navigation, clear old fields, recalibrate.
- **Wrong channel without an error:** check cold-start baseline/positions/timing physically; key transmission doesn't verify playback.
- **Unexpected app:** verify package/activity, startup delay and account/consent dialogs.
- **Numpad unavailable:** check by-id path and targeted group permissions; no need to run everything as root.
- **Repeated recovery fails:** cooldown prevents repeated switch toggles; fix the menu sequence/network or enable debugging manually, then request again.
- **Timeout:** allow enough time for connection/recovery plus full app navigation; operations never replay indefinitely.

Original navigation findings: [reference-notes.md](docs/reference-notes.md), [WayPi-TV](https://github.com/DanielNikolic03/WayPi-TV/tree/2d9fb2f94944504bbb1f3db67141acd22bb3858e). Remote API: [androidtvremote2](https://github.com/tronikos/androidtvremote2). ADB lifecycle/pairing: [Android ADB documentation](https://developer.android.com/tools/adb). mDNS formats and distinct pairing/connection services: [AOSP ADB Wi-Fi architecture](https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/docs/dev/adb_wifi.md).
