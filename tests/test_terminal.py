from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from channelpilot.input import terminal


async def test_app_names_packages_and_unknown_links(monkeypatch, caplog):
    controller = MagicMock()
    controller.apps = {"move": SimpleNamespace(package="com.mtssi.supernovabih")}
    monkeypatch.setattr(
        terminal,
        "read_line",
        AsyncMock(
            side_effect=[
                "launch com.mtssi.supernovabih",
                "open move",
                "close move",
                "launch test://app",
                "adb-connect",
                "quit",
            ]
        ),
    )
    await terminal.run_terminal(controller)
    assert [call.args for call in controller.submit.call_args_list] == [
        ("open", "move"),
        ("open", "move"),
        ("close", "move"),
        ("adb-connect",),
    ]
    assert "Unknown app" in caplog.text


async def test_wake_and_standby_use_power_input_actions(monkeypatch):
    controller = MagicMock()
    monkeypatch.setattr(terminal, "read_line", AsyncMock(side_effect=["wake", "standby", "quit"]))
    await terminal.run_terminal(controller)
    assert [call.args for call in controller.handle_input.call_args_list] == [
        ("wake",),
        ("standby",),
    ]
    controller.submit.assert_not_called()
