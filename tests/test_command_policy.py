import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import euthernet_cli  # noqa: E402


def config_for(mode: str | None, *, allow_write_actions: bool = False) -> dict:
    command = {
        "name": "test-command",
        "description": "Policy test command.",
        "command": "/bin/true",
    }
    if mode is not None:
        command["mode"] = mode
    return {
        "server": {"command_transport": "local"},
        "security": {"allow_write_actions": allow_write_actions},
        "commands": {"allow_remote": True, "allowed": [command]},
    }


class CommandPolicyTests(unittest.TestCase):
    def test_read_command_runs_when_write_actions_are_disabled(self) -> None:
        with mock.patch.object(
            euthernet_cli,
            "run_configured",
            return_value={"ok": True, "returncode": 0, "stdout": "ok", "stderr": ""},
        ) as run:
            result = euthernet_cli.run_allowed_command(config_for("read"), "test-command")

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "read")
        run.assert_called_once()

    def test_write_command_is_denied_when_write_actions_are_disabled(self) -> None:
        with mock.patch.object(euthernet_cli, "run_configured") as run:
            result = euthernet_cli.run_allowed_command(config_for("write"), "test-command")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "write actions are disabled in config")
        run.assert_not_called()

    def test_missing_mode_fails_closed(self) -> None:
        with mock.patch.object(euthernet_cli, "run_configured") as run:
            result = euthernet_cli.run_allowed_command(config_for(None), "test-command")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "command mode is missing or invalid")
        run.assert_not_called()

    def test_write_command_runs_only_when_explicitly_enabled(self) -> None:
        with mock.patch.object(
            euthernet_cli,
            "run_configured",
            return_value={"ok": True, "returncode": 0, "stdout": "ok", "stderr": ""},
        ) as run:
            result = euthernet_cli.run_allowed_command(
                config_for("write", allow_write_actions=True), "test-command"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "write")
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
