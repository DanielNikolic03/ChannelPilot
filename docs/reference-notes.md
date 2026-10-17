# Reference study and design decisions

The study preceded implementation. Original repository snapshot: `2d9fb2f94944504bbb1f3db67141acd22bb3858e`. Library dependency tested: `androidtvremote2` 0.3.2. Links below point to primary sources; current upstream behavior may evolve after this version.

## WayPi-TV

Read the original `src/main.cpp`, `channels.h`, `App.h`, `Apps/MOVE.cpp/.h`, `Apps/SVT.cpp/.h`, and `waydroid.cpp`.

- `main.cpp` supplies the exact digit mappings retained here. Its `std::map` orders +/− by numeric keys, including 0 first, with wrapping. It reads every event device and grabs them; ChannelPilot deliberately uses one configured path.
- `App.h` only abstracts channel selection. `Waydroid::setChannel` owns app selection and instantiates the appropriate class. ChannelPilot moves selection into a registry and exposes open/reset/navigate/close, with no app-specific key commands in the controller.
- MOVE launches `com.mtssi.supernovabih` twice, waits 8 seconds, then BACK, LEFT twice, DOWN, CENTER twice, BACK, with 2-second pauses. It presumes RTS 1. Relative changes open the list with LEFT, move vertically (1-second pauses), CENTER, BACK (2-second pauses). Internal positions are RTS 1=1, Pink=2, Prva=3, Happy=4, BN=5, BN Muzika=6. Its initial channel is assigned in the constructor; this is not observation.
- SVT launches `se.svt.android.svtplay` twice, waits 3 seconds, then LEFT (1s), CENTER (5s), LEFT (1s), DOWN three times (1s), CENTER (5s), presuming SVT1. Relative changes use RIGHT/LEFT and CENTER. Its position mapping is SVT1=1, SVT2=2, Kunskapskanalen=4, SVT24=5, explicitly described as a placeholder.
- Original app stop force-stops through ADB. The remote protocol provides no equivalent force-stop guarantee. ChannelPilot's close returns HOME and invalidates state. HOME may leave the app alive and preserve its UI; resetting a resumed UI is a calibration requirement.
- The original global state is assigned after commands without validating their result. ChannelPilot records only presumed playback and resets from a calibrated baseline for every selection.

Sources: [MOVE](https://github.com/DanielNikolic03/WayPi-TV/blob/2d9fb2f94944504bbb1f3db67141acd22bb3858e/src/Apps/MOVE.cpp), [SVT](https://github.com/DanielNikolic03/WayPi-TV/blob/2d9fb2f94944504bbb1f3db67141acd22bb3858e/src/Apps/SVT.cpp), [input/controller](https://github.com/DanielNikolic03/WayPi-TV/blob/2d9fb2f94944504bbb1f3db67141acd22bb3858e/src/main.cpp).

## androidtvremote2

Read `src/androidtvremote2/androidtv_remote.py`, `src/demo.py`, README, and project dependency metadata, then exercised the installed 0.3.2 SDK in automated tests.

- Construct `AndroidTVRemote(client_name, certfile, keyfile, host)` inside the asyncio loop.
- Certificate generation uses `await async_generate_cert_if_missing()` inside an already-private directory. Pairing uses `await async_start_pairing()` and `await async_finish_pairing(code)`, not guessed APIs.
- `await async_connect()` establishes the connection. `keep_reconnecting(invalid_auth_callback)` starts the documented reconnection task; `disconnect()` cancels it and closes transports. ChannelPilot retries initial failures separately and never starts a competing reconnect loop after success.
- Register `add_is_available_updated_callback` and `add_current_app_updated_callback`. Read `is_on` and `current_app`; these do not expose selected UI elements or playback.
- `send_key_command("DPAD_RIGHT")` and `send_launch_app_command(link)` are synchronous buffering calls. Awaiting our wrapper adds timing/connection checks, not an acknowledgement.
- The API may prepend `market://launch?id=` to bare package IDs. The demo warns this fails for many apps after Play Store changes. ChannelPilot requires a URL scheme for configured direct launches and provides HOME-based launcher steps as an alternative.
- InvalidAuth requires manual pairing. Credentials are not logged, automatically rotated, or silently replaced during normal startup.
- Explicit Android WAKEUP/SLEEP keys avoid power toggles; VOLUME_UP/DOWN and VOLUME_MUTE are available manual controls.

Sources: [library](https://github.com/tronikos/androidtvremote2), [API source](https://github.com/tronikos/androidtvremote2/blob/main/src/androidtvremote2/androidtv_remote.py), [demo](https://github.com/tronikos/androidtvremote2/blob/main/src/demo.py).

## Deep-link investigation and unresolved hardware work

The reference code launches packages through Waydroid and therefore supplies no MOVE/SVT deep links. The library's README/demo documents URL launching generally, with examples for other apps. Searches of these primary repositories and SVT's Android/TV support documentation did not establish a reliable MOVE or SVT Android TV launch URL. SVT's [TV instructions](https://www.svt.se/kontakt/sa-anvander-du-svt-play-pa-din-tv) establish Android TV support but do not establish a working remote-protocol deep link for these sequences.

Therefore `launch_link` defaults to null and `launch_steps` to empty. This is an explicit calibration point, not a claim that no deep links exist. A maintainer can test candidate links using the terminal, inspect the reported package, and confirm visually. Otherwise calibrate a launcher path from HOME. Do not copy unrelated apps' link patterns and assume they work.

Both reference reset sequences and SVT offsets require Chromecast validation. They must work when cold, resumed from playback, resumed from menus, and after switching apps. If a single reset cannot cover these states, revise the app class/configuration before enabling it for unattended use. A reference profile is not a verified reset just because all remote commands were accepted.
