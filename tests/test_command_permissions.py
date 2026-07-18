from __future__ import annotations

import unittest
from unittest import mock
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from euthernet_cli import run_allowed_command


def config(*, allow_remote: bool = True, allow_write: bool = False) -> dict:
    return {
        "server": {"command_transport": "local"},
        "security": {"allow_write_actions": allow_write},
        "commands": {
            "allow_remote": allow_remote,
            "allowed": [
                {"name": "health", "description": "Read health", "command": "true"},
                {
                    "name": "restart",
                    "description": "Restart service",
                    "command": "true",
                    "write": True,
                },
            ],
        },
    }


class CommandPermissionTests(unittest.TestCase):
    @mock.patch("euthernet_cli.run_configured")
    def test_read_command_remains_available_when_writes_are_disabled(self, run: mock.Mock) -> None:
        run.return_value = {"ok": True, "returncode": 0, "stdout": "ok", "stderr": ""}

        result = run_allowed_command(config(), "health")

        self.assertTrue(result["ok"])
        self.assertFalse(result["write"])
        run.assert_called_once()

    @mock.patch("euthernet_cli.run_configured")
    def test_write_command_is_blocked_when_writes_are_disabled(self, run: mock.Mock) -> None:
        result = run_allowed_command(config(), "restart")

        self.assertEqual(result, {"ok": False, "error": "write actions are disabled in config"})
        run.assert_not_called()

    @mock.patch("euthernet_cli.run_configured")
    def test_write_command_runs_only_when_both_gates_are_enabled(self, run: mock.Mock) -> None:
        run.return_value = {"ok": True, "returncode": 0, "stdout": "ok", "stderr": ""}

        result = run_allowed_command(config(allow_write=True), "restart")

        self.assertTrue(result["ok"])
        self.assertTrue(result["write"])
        run.assert_called_once()

    @mock.patch("euthernet_cli.run_configured")
    def test_all_commands_are_blocked_when_remote_commands_are_disabled(self, run: mock.Mock) -> None:
        result = run_allowed_command(config(allow_remote=False, allow_write=True), "health")

        self.assertEqual(result, {"ok": False, "error": "remote commands are disabled in config"})
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
