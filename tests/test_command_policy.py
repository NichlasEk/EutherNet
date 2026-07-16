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
        "required_action": "service.restart",
        "target": "test.service",
    }
    if mode is not None:
        command["mode"] = mode
    return {
        "server": {"command_transport": "local"},
        "security": {"allow_write_actions": allow_write_actions},
        "commands": {"allow_remote": True, "allowed": [command]},
    }


def authorization() -> dict:
    return {
        "action_proof": "signed-action-proof",
        "expected": {
            "actor": "nichlas",
            "session_hash": "a" * 64,
            "origin": "https://apothictech.se",
            "action": "service.restart",
            "target": "test.service",
            "command_id": "test-command",
        },
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
        with (
            mock.patch.object(
                euthernet_cli,
                "consume_eutherid_action_proof",
                return_value={"ok": True, "authorization": {}},
            ) as consume,
            mock.patch.object(
                euthernet_cli,
                "run_configured",
                return_value={"ok": True, "returncode": 0, "stdout": "ok", "stderr": ""},
            ) as run,
        ):
            result = euthernet_cli.run_allowed_command(
                config_for("write", allow_write_actions=True),
                "test-command",
                authorization(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "write")
        consume.assert_called_once()
        run.assert_called_once()

    def test_enabled_write_without_eutherid_authorization_fails_closed(self) -> None:
        with mock.patch.object(euthernet_cli, "run_configured") as run:
            result = euthernet_cli.run_allowed_command(
                config_for("write", allow_write_actions=True), "test-command"
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "EutherID authorization is required")
        run.assert_not_called()

    def test_binding_mismatch_is_rejected_before_contacting_eutherid(self) -> None:
        proof = authorization()
        proof["expected"]["target"] = "other.service"
        config = config_for("write", allow_write_actions=True)
        with mock.patch.object(euthernet_cli.urllib.request, "urlopen") as urlopen:
            result = euthernet_cli.consume_eutherid_action_proof(
                config,
                config["commands"]["allowed"][0],
                proof,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "EutherID authorization binding mismatch")
        urlopen.assert_not_called()

    def test_eutherid_consumer_receives_exact_bound_proof(self) -> None:
        config = config_for("write", allow_write_actions=True)
        config["security"].update(
            {
                "eutherid_url": "http://127.0.0.1:8792",
                "eutherid_internal_token_file": "/run/credentials/euthernet/token",
            }
        )
        consumed = {
            "actor": "nichlas",
            "action": "service.restart",
            "target": "test.service",
            "command_id": "test-command",
            "challenge_id": "challenge",
            "device_id": "phone",
            "jti": "one-time-id",
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = euthernet_cli.json.dumps(
            consumed
        ).encode("utf-8")
        with (
            mock.patch.object(
                euthernet_cli.pathlib.Path, "read_text", return_value="internal-token\n"
            ),
            mock.patch.object(
                euthernet_cli.urllib.request, "urlopen", return_value=response
            ) as urlopen,
        ):
            result = euthernet_cli.consume_eutherid_action_proof(
                config,
                config["commands"]["allowed"][0],
                authorization(),
            )

        self.assertTrue(result["ok"])
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "http://127.0.0.1:8792/v1/action-proofs/consume"
        )
        self.assertEqual(request.get_header("X-eutherid-internal-token"), "internal-token")
        body = euthernet_cli.json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, authorization())


if __name__ == "__main__":
    unittest.main()
