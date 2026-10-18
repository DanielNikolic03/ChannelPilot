# Chromecast calibration with ADB app lifecycle

A maintainer watches the physical TV. ChannelPilot never captures or interprets screen content. Calibrate the app's navigation and the separate Wireless debugging recovery menu independently.

## 1. Prepare both connections

Reserve the Chromecast IP, install/sign into MOVE and SVT Play, dismiss first-run prompts, and enable Developer options. Pair the remote protocol with `python -m channelpilot --pair`. Enable Wireless debugging, open **Pair using pairing code**, then run `python -m channelpilot --adb-pair <displayed-pairing-port>` and enter the code.

Use the same OS account/config/ADB server for pairing and normal operation. On Raspberry Pi service deployments this is the `channelpilot` user with HOME `/var/lib/channelpilot`. Pairing on your Mac does not authorize the Pi. Verify Platform Tools supports `adb mdns services`; the normal connection record looks like:

```text
adb-2A20105GN4CCJH-2CkQNv  _adb-tls-connect._tcp  192.168.0.115:41191
```

The connection port can change and differs from the pairing port. Do not hardcode the example port. The controller matches the configured Chromecast address, excludes pairing services and unrelated devices, and verifies connection readiness.

## 2. Test manual app lifecycle and remote keys

```sh
python -m channelpilot --test --debug
```

Try `adb-connect`, `open move`, `close move`, `open svt`, and `close svt`. `launch com.mtssi.supernovabih` is an alias for opening MOVE by package. These manual commands do not require app calibration and do not run configured startup/reset/channel sequences.

Confirm apps actually open/close. ADB resolves TV launcher activities automatically. If needed, configure a tested full `activity` component belonging to the app. Verify force-stop/reopen behavior: account/session data persists, and an app may restore its previous channel even after its process has been stopped.

Test `key HOME`, DPAD directions, CENTER, BACK, `key VOLUME_UP`, `key VOLUME_DOWN`, `key VOLUME_MUTE`, standby/wake. Wake/Enter only requests power on and never selects a default channel. Test state-checked POWER in both directions, repeated same-state requests, and missing power confirmation (no automatic retoggle). Observe physical TV/CEC behavior; `status.is_on` reports the Chromecast, not the TV panel. If standby app cleanup fails, the power request must still be sent without opening Settings for ADB recovery.

## 3. Migrate old launcher sequences

Google TV HOME navigation and deep links no longer start apps. If you have an old `launch_steps` sequence, preserve a private copy while editing, separate any app-internal commands from Google TV launcher commands, move the in-app preparation to `startup_steps` or `reset_steps`, then clear/remove `launch_steps` and `launch_link`. `home_delay` is retired. Old nonempty launch settings intentionally block automatic channel navigation until migrated.

The supplied original MOVE/SVT reset profiles remain reference material, not validated Chromecast sequences. The new app launch is a cold process start using `am start -S -W`, followed by `startup_delay`, optional `startup_steps`, then `reset_steps` to establish its baseline. Subsequent same-app selections reuse the last presumed position without relaunching or resetting.

## 4. Calibrate each app from a fresh launch

1. Start with `calibrated=false`. Use manual `open <app>` to reproduce a fresh process start, then individual remote `key` commands while watching the TV.
2. Measure a reliable `startup_delay`. `am start -W` reports an activity launch, not that the streaming app has fully loaded its UI.
3. Configure optional `startup_steps` to perform consistent in-app preparation. No Google TV launcher navigation belongs here.
4. Configure `reset_steps` to reach one known baseline **playing** state. If startup already reaches it, set `reset_steps: []`; no extra reset commands are required. For the reference MOVE profile this is RTS 1, baseline position 1; SVT uses SVT1, position 1. Do not assume a fixed number of inverse movement keys resets a list from every position. Test what the app restores after force-stop from different channels and menus.
5. Configure `select_prefix` to open the selector, validate every channel's `navigation_id` in `channels.json`, set movement timing, then configure `select_suffix` to select/dismiss. MOVE's class moves vertically and SVT horizontally. If that model does not fit the app, adjust its class rather than inventing offsets.
6. If requested position equals baseline, prefix/suffix are skipped. Therefore the reset sequence must already have started baseline playback, not just highlighted it.
7. Set `calibrated=true` after manually testing the sequence components. Restart and type channel digits: the first selection launches/prepares/resets the app, then later same-app selections move relatively from the last presumed position. Test every channel, forward/backward changes, returning to baseline, reselecting the current channel, and cross-app switches. Confirm no ADB restart, startup steps, or reset steps occur during normal same-app changes. Restart after every config edit.
8. Verify invalidation: a manual key, explicit `invalidate`, standby/power-off, a reported app change, or a remote disconnect must cause the next channel request to launch and initialize from the baseline again. A detectable failure during relative movement must retry through a full fresh launch/startup/reset, never continue from a partially executed movement. Keep remembered positions labeled assumed; an external remote changing a channel inside the same app may be unobservable, so explicitly invalidate after such intervention.

Step format:

```json
[
  {"press":"DPAD_LEFT","times":2,"delay":1},
  {"wait":0.5},
  {"checkpoint":"baseline presumed; not verified"}
]
```

These lines only illustrate the format; they are not a known baseline for either app. Delays follow each press, in addition to the wrapper's `remote.command_delay`. A checkpoint is just a log label.

## 5. Fill in Wireless debugging recovery manually

The configuration has an intentionally empty **`adb.recovery_steps`** list. Enter your own remote-key sequence there; no generic menu path is assumed.

1. Keep `adb.recovery_calibrated=false` initially. From `key HOME`, find and test the exact path to Wireless debugging using individual remote keys. ChannelPilot automatically sends HOME before `recovery_steps`, so put any needed HOME-settle wait at the beginning of the list.
2. Write down the key sequence and pauses, including any network permission prompt. It must end with Wireless debugging **enabled**. Verify behavior both when the setting starts off and when it starts on, because an ADB/mDNS failure does not prove it is off. Do not use an untested blind toggle.
3. Set `adb.recovery_calibrated=true`, restart, then reproduce disabled debugging and run `adb-connect`. Observe one recovery-menu pass, rediscovery of the new `_adb-tls-connect._tcp` port, and successful ADB readiness. Confirmation is successful ADB connection, not the menu's presumed appearance.
4. Repeat after an actual Chromecast reboot/power outage on your firmware. Test retained host authorization separately from the debugging switch. If authorization is revoked, the program must report manual `--adb-pair`; it cannot read the on-screen pairing code or recreate trust through menu keys.
5. Test failures: remote unavailable, Wi-Fi unavailable, mDNS blocked, stale connection port, slow settings UI. Commands/deadlines stay bounded and the cooldown prevents immediate repeated toggling. A new request may be needed after connectivity returns.
6. Leave recovery uncalibrated if your menu sequence cannot reliably end enabled. Debugging can still be enabled manually; automatic port discovery/ADB lifecycle works without menu recovery whenever ADB is reachable.

Choose `operation_timeout` longer than possible ADB reconnection/recovery plus cold app startup and complete channel navigation. `adb.command_timeout` bounds each child process; discovery/recovery have separate deadlines. Shutdown uses `adb.shutdown_timeout` and never attempts settings-menu recovery.

## 6. Exercise concurrency, numpad, and service startup

While navigation is running, press several distinct digits rapidly. Only the active procedure followed by the latest pending request should execute. Test +/− wrapping, long holds, bounce filtering, pending standby and a later channel request. Lifecycle, remote navigation and recovery menus must not overlap.

Select exactly one stable by-id numpad path. Unplug/replug it during activity and idle, confirm reopening and held-key reset, confirm other keyboards do not control the program, and check optional grabs release on stop.

Reboot the Pi without a desktop/login and verify both remote and ADB authorizations persist. Verify `journalctl -u channelpilot`, clean SIGTERM shutdown, and bounded ADB force-stop cleanup. Disconnect ADB before shutdown and confirm no settings menu opens and no shared ADB server is killed. Test invalid credentials with a separate private test configuration, preserving working keys.

Run an extended human-observed viewing session before handoff. Record unresolved failures and keep affected automatic features uncalibrated. Recalibrate after relevant app/firmware/account/lineup changes. Normal streaming dialogs or silent lost keys can remain undetectable without screen access.

## Acceptance record

| Item | Observed result |
| --- | --- |
| Chromecast/TV models, firmware, app versions | |
| Host/IP, Platform Tools version, remote and ADB service account | |
| Initial pairing, authorization retained after reboot | |
| Cold app launch, persisted last-channel behavior | |
| Startup/reset baseline and every channel offset | |
| All ten keys and repeated cross-app switches | |
| Wireless debugging off/on recovery, network consent prompts | |
| Port change, unrelated mDNS devices, multicast failure | |
| Complete-attempt duration and timeout margin | |
| Rapid input, +/−, holds, standby and wake | |
| Pi boot, numpad replug, shutdown cleanup | |
| Known limitations, date, maintainer | |
