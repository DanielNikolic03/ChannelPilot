import json
from pathlib import Path

import pytest

from channelpilot.core.channels import Channel, Channels
from channelpilot.core.config import Config
from channelpilot.input.numpad import KEY_ACTIONS, KeyFilter, NumpadListener


def test_original_mapping_and_order(channels):
    assert {key: channel.name for key, channel in channels.by_key.items()} == {
        "0": "RTS 1",
        "1": "SVT1",
        "2": "SVT2",
        "3": "Kunskapskanalen",
        "4": "SVT24",
        "5": "BN Muzika",
        "6": "BN",
        "7": "Happy",
        "8": "Prva",
        "9": "Pink",
    }
    assert channels.adjacent("pink", 1).id == "rts1"
    assert channels.adjacent("rts1", -1).id == "pink"
    assert channels.adjacent(None, 1).id == "rts1"
    assert channels.adjacent(None, -1).id == "pink"
    assert channels.by_id["kunskap"].navigation_id == 4


@pytest.mark.parametrize(
    "records",
    [
        [],
        [Channel("x", "X", "svt", 0, "1")],
        [Channel("x", "X", "svt", 1, "1"), Channel("x", "X", "svt", 2, "2")],
        [Channel("x", "X", "svt", 1, "1"), Channel("y", "Y", "svt", 2, "1")],
    ],
)
def test_bad_channels(records):
    with pytest.raises(ValueError):
        Channels(records)


@pytest.mark.parametrize("code,action", list(KEY_ACTIONS.items()))
def test_linux_codes(code, action):
    assert KeyFilter().feed(1, code, 1, 0) == action


def test_repeat_hold_release_and_bounce():
    keys = KeyFilter(0.15)
    assert keys.feed(1, 79, 1, 0) == "1"
    assert keys.feed(1, 79, 2, 1) is None
    assert keys.feed(1, 79, 1, 2) is None
    assert keys.feed(1, 79, 0, 2) is None
    assert keys.feed(1, 79, 1, 0.1) is None
    keys.feed(1, 79, 0, 0.11)
    assert keys.feed(1, 79, 1, 0.2) == "1"
    assert keys.feed(1, 80, 1, 0.21) == "2"
    assert keys.feed(0, 81, 1) is None
    assert keys.feed(1, 999, 1) is None
    keys.reset()
    assert keys.feed(1, 79, 1, 0.21) == "1"


def test_key_overrides():
    assert KeyFilter(overrides={"2": "1"}).feed(1, 2, 1) == "1"
    with pytest.raises(ValueError):
        KeyFilter(overrides={"2": "shutdown"})


def test_config_relative_paths_and_no_repo_credentials(tmp_path):
    raw = json.loads(Path("config/config.example.json").read_text())
    raw["remote"]["credentials_dir"] = str(tmp_path / "private")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    conf = Config.load(path)
    assert conf.channels_file == tmp_path / "channels.json"
    raw["remote"]["credentials_dir"] = str(Path.cwd() / "credentials")
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="outside"):
        Config.load(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("retry_limit", 2),
        ("operation_timeout", 0),
        ("operation_timeout", float("nan")),
        ("standby_on_failure", "false"),
    ],
)
def test_config_rejects_unbounded_values(tmp_path, field, value):
    raw = json.loads(Path("config/config.example.json").read_text())
    raw[field] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        Config.load(path)


async def test_numpad_reopens_only_selected_device(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace

    opened = []
    closed = []
    actions = []
    stop = asyncio.Event()

    class Device:
        name = "numpad"

        def __init__(self, path):
            opened.append(path)
            self.count = len(opened)

        async def async_read_loop(self):
            yield SimpleNamespace(type=1, code=79, value=1)
            if self.count == 1:
                raise OSError("unplugged")
            stop.set()
            await asyncio.Event().wait()

        def close(self):
            closed.append(self.count)

    monkeypatch.setitem(sys.modules, "evdev", SimpleNamespace(InputDevice=Device))
    listener = NumpadListener("/dev/input/by-id/selected", actions.append, reconnect_delay=0.001)
    task = asyncio.create_task(listener.run())
    await asyncio.wait_for(stop.wait(), 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert opened == ["/dev/input/by-id/selected"] * 2
    assert closed == [1, 2]
    assert actions == ["1", "1"]
