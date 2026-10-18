"""CLI entry point and structured task lifetime."""

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from channelpilot.apps.registry import build_apps
from channelpilot.core.adb import AdbController
from channelpilot.core.channels import Channels
from channelpilot.core.config import Config
from channelpilot.core.controller import Controller
from channelpilot.core.errors import ChannelPilotError
from channelpilot.core.remote import RemoteController
from channelpilot.core.state import Status
from channelpilot.input.numpad import NumpadListener
from channelpilot.input.terminal import read_line, run_terminal
from channelpilot.utils.logging import configure_logging


async def run(args: argparse.Namespace) -> None:
    config = Config.load(args.config)
    configure_logging("DEBUG" if args.debug else config.log_level)
    if args.check_config:
        # Validate without network activity or credentials.
        channels = Channels.load(config.channels_file)
        remote = RemoteController(config.remote)
        adb = AdbController(config.adb, config.remote.host, remote)
        apps = build_apps(remote, config.apps, config.operation_timeout, adb=adb)
        Controller(remote, channels, apps, app_close_timeout=config.adb.shutdown_timeout)
        NumpadListener(config.device, lambda _: None, overrides=config.input_key_overrides)
        print(f"Configuration valid: {len(channels.by_id)} channels, {len(apps)} applications")
        for name, app in apps.items():
            try:
                app.require_calibration()
                print(f"  {name}: marked calibrated (requires hardware validation)")
            except ChannelPilotError as exc:
                print(f"  {name}: {exc}")
        return
    remote = RemoteController(config.remote)
    tasks: list[asyncio.Task[None]] = []
    controller: Controller | None = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def wait_for_stop() -> None:
        await stop.wait()

    try:
        if args.adb_pair is not None:
            adb = AdbController(config.adb, config.remote.host, remote)
            tasks.append(
                asyncio.create_task(
                    adb.pair(
                        args.adb_pair, lambda: read_line("Enter the six-digit ADB pairing code: ")
                    ),
                    name="adb-pairing",
                )
            )
            tasks.append(asyncio.create_task(wait_for_stop(), name="shutdown-signal"))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            return
        if args.pair:
            tasks.append(
                asyncio.create_task(
                    remote.pair(lambda: read_line("Enter the pairing code displayed on the TV: ")),
                    name="pairing",
                )
            )
            tasks.append(asyncio.create_task(wait_for_stop(), name="shutdown-signal"))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            return
        channels = Channels.load(config.channels_file)
        adb = AdbController(config.adb, config.remote.host, remote)
        apps = build_apps(remote, config.apps, config.operation_timeout, adb=adb)
        controller = Controller(
            remote,
            channels,
            apps,
            operation_timeout=config.operation_timeout,
            retry_limit=config.retry_limit,
            standby_on_failure=config.standby_on_failure,
            app_close_timeout=config.adb.shutdown_timeout,
        )
        controller.state.status = Status.CONNECTING
        remote.start()
        tasks.append(asyncio.create_task(controller.run(), name="navigation-worker"))
        if args.test:
            tasks.append(asyncio.create_task(run_terminal(controller), name="terminal-input"))
        else:
            listener = NumpadListener(
                config.device,
                controller.handle_input,
                grab=config.grab,
                debounce=config.debounce,
                reconnect_delay=config.device_retry,
                overrides=config.input_key_overrides,
            )
            tasks.append(asyncio.create_task(listener.run(), name="numpad-input"))
        tasks.append(asyncio.create_task(wait_for_stop(), name="shutdown-signal"))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # Surface a failed worker/listener to systemd, rather than run deaf.
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if controller is not None:
            await controller.close(config.adb.shutdown_timeout)
        await remote.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Accessible Chromecast control")
    parser.add_argument("--config", type=Path, default=Path("config/config.json"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--adb-pair", type=int, metavar="PORT", help="Pair ADB using the displayed pairing port"
    )
    mode.add_argument("--pair", action="store_true", help="Initial/manual Chromecast pairing")
    mode.add_argument("--test", action="store_true", help="Terminal controls without a numpad")
    mode.add_argument(
        "--check-config", action="store_true", help="Validate config without connecting"
    )
    parser.add_argument("--debug", action="store_true", help="Log individual remote commands")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ChannelPilotError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from None
    except EOFError:
        logging.getLogger(__name__).error("Input closed before pairing completed; run --pair again")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
