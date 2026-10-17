"""Journal-friendly logs, with upstream pairing payload logging suppressed."""

import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # Upstream debug logs can include pairing/protocol messages. Our DEBUG logs only keys.
    logging.getLogger("androidtvremote2").setLevel(logging.WARNING)
