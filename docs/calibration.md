# Real Chromecast testing and calibration

A maintainer watches the physical TV during this procedure. ChannelPilot itself never captures or analyzes the screen. Do not enable unattended channel selection until every relevant starting state works.

1. **Prepare.** Reserve the Chromecast IP. Install and sign into MOVE and SVT Play; dismiss first-run/privacy/subscription prompts. Pair ChannelPilot. Start `python -m channelpilot --config <path> --test --debug`. Leave both apps `calibrated=false` while testing individual commands.
2. **Verify the transport.** Test `key HOME`, each DPAD direction, CENTER, BACK, `key VOLUME_UP`, `key VOLUME_DOWN`, `key VOLUME_MUTE`, `standby`, and `wake`. `default_channel` can be set to null for wake-only testing. Watch actual device/TV behavior. Inspect `status` after foreground-app updates. Check that power is not toggled when already in the desired state, and measure a reliable wake delay.
3. **Establish app launching.** Test a documented candidate with `launch <url>` only if one is available; confirm the right app both physically and from reported package when provided. Do not treat a web link or Play Store page as successful app launching. If no reliable link exists, pin the app in a fixed Google TV position and determine `launch_steps` from HOME. ChannelPilot inserts HOME and `home_delay` before these steps. Repeat with the app cold, already open, and after opening the other app. Do not assume a launcher position is fixed across updates or profiles.
4. **Calibrate MOVE baseline.** The supplied reset profile is from Waydroid: BACK, LEFT twice, DOWN, CENTER twice, BACK. Verify whether it actually reaches live RTS 1 on the Chromecast. Measure delays. Revise the reset steps for the actual TV UI. Test from initial launch, RTS 1 playback, every other mapped channel, the channel list, app menus, after HOME, after SVT, and after wake. HOME alone does not reset the app. If states cannot be reconciled with these keys, revise `MoveApp` or its configuration; keep it disabled until a repeatable baseline exists.
5. **Calibrate MOVE offsets.** From the baseline, verify the list prefix, DOWN/UP axis, all six positions, selection and dismissal suffix. The source order is RTS 1, Pink, Prva, Happy, BN, BN Muzika. This may differ for your account/channel lineup. Update navigation IDs and steps accordingly.
6. **Calibrate SVT independently.** Its supplied reset profile is LEFT, CENTER, LEFT, DOWN three times, CENTER. Verify and revise for the current Android TV UI and all cold/resumed/menu/playback states. Confirm horizontal selection actually works after baseline reset. In particular validate positions 1, 2, 4, 5: the source labels these placeholders. Do not simply assume the gaps are correct.
7. **Enable one app at a time.** Set `calibrated=true` only for the app just validated, with a nonempty tested launch strategy and reset sequence. Restart, run `--check-config`, then `--test`. Select every key repeatedly and switch apps in both directions. Confirm the physical channel each time; the status only says presumed. Record app versions, account layout, launcher arrangement, TV/Chromecast model and firmware, delays, and results in a local calibration record.
8. **Exercise timing and concurrency.** While a long operation is running, press several distinct digits quickly. Verify the active operation finishes, followed only by the latest pending request. Try +/− wrapping and a long key hold. Confirm pending standby replaces pending channel selection and waits for the active operation. Keep the full attempt deadline longer than the slowest valid launch + reset + channel selection, with a realistic margin.
9. **Test network recovery.** Disconnect Chromecast networking during reset and selection, then restore it. Confirm logs show loss/reconnection and the interrupted attempt does not continue from the middle. Within the retry deadline it should restart through HOME/launch/reset; if both attempts expire, a new key should work after reconnect. Restart the Chromecast and repeat. Verify power state and app state are not inferred from the last request.
10. **Test numpad recovery.** Configure exactly one stable by-id path. Unplug/replug it during navigation and while idle. Verify it reconnects and held-key state resets, and that other keyboards do not control ChannelPilot. With `grab=true`, verify only the configured device is grabbed and it is released after service stop.
11. **Test credentials and boot.** Reboot the Pi and confirm no new pairing prompt. Test invalid credentials using a separate private test directory/config, never overwrite the working pair. Confirm an explicit pairing instruction and no private data in logs. Install the systemd service and boot without a desktop/monitor/interactive login. Test SIGTERM shutdown and service restart; confirm no interrupted sequence resumes from a stale position.
12. **Handoff.** Run an extended viewing session with the intended numpad and all ten channels. Keep an ordinary TV remote available to the maintainer for unexpected account/UI dialogs. Record unresolved failures and keep affected apps disabled. Recalibrate after relevant app, Google TV, account/lineup, or launcher updates.

Suggested local acceptance record (do not put secrets in it):

| Item | Result / observed value |
| --- | --- |
| Device models, OS/firmware, app versions | |
| Account/region and channel ordering | |
| Launch strategy and reported package for each app | |
| Cold/resumed reset behavior from all tested states | |
| All ten keys and repeated cross-app switches | |
| Worst complete-operation duration and timeout margin | |
| Rapid input, +/−, holds, standby and wake | |
| Network loss, Chromecast reboot, numpad replug | |
| Pi boot, persistent credentials, shutdown | |
| Known limitations, date, maintainer | |
