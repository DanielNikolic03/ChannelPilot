import asyncio
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cli_config(tmp_path):
    raw = json.loads(Path("config/config.example.json").read_text())
    raw["remote"]["credentials_dir"] = str(tmp_path / "missing-private")
    raw["channels_file"] = str(Path("config/channels.json").resolve())
    config = tmp_path / "config.json"
    config.write_text(json.dumps(raw))
    return config


async def test_cli_config_and_terminal_shutdown(cli_config):
    config = cli_config
    for arguments, stdin in ((["--check-config"], None), (["--test"], b"status\nquit\n")):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "channelpilot",
            "--config",
            str(config),
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), 3)
        assert process.returncode == 0, stderr.decode()
        assert b"Traceback" not in stderr
        assert stdout
    assert not (config.parent / "missing-private").exists()
