#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
from typing import Any

from euthernet_cli import consume_eutherid_action_proof
from euthernet_inventory import load_config


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consume an EutherID proof through EutherNet without running a command."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--internal-token-file", default="/etc/eutherid/internal-token")
    parser.add_argument("--dev-token-file", default="/etc/eutherid/dev-signer-token")
    args = parser.parse_args()

    config = load_config(pathlib.Path(args.config))
    commands = {
        item["name"]: item for item in config.get("commands", {}).get("allowed", [])
    }
    command = commands["restart-caddy"]
    expected = {
        "actor": "euthernet-shadow-smoke",
        "session_hash": "c" * 64,
        "origin": "https://apothictech.se",
        "action": command["required_action"],
        "target": command["target"],
        "command_id": command["name"],
    }
    base_url = str(config["security"]["eutherid_url"]).rstrip("/")
    internal_token = pathlib.Path(args.internal_token_file).read_text().strip()
    dev_token = pathlib.Path(args.dev_token_file).read_text().strip()

    challenge = request_json(
        f"{base_url}/v1/challenges",
        {**expected, "ttl_seconds": 120},
        {"X-EutherID-Internal-Token": internal_token},
    )
    challenge_id = challenge["id"]
    signed = request_json(
        f"{base_url}/v1/dev-sign/{challenge_id}",
        {"decision": "approve"},
        {"X-EutherID-Dev-Token": dev_token},
    )
    request_json(
        f"{base_url}/v1/challenges/{challenge_id}/approval",
        {"approval_proof": signed["approval_proof"]},
    )
    issued = request_json(
        f"{base_url}/v1/challenges/{challenge_id}/action-proof",
        {"expected": expected},
        {"X-EutherID-Internal-Token": internal_token},
    )
    authorization = {"action_proof": issued["action_proof"], "expected": expected}
    first = consume_eutherid_action_proof(config, command, authorization)
    second = consume_eutherid_action_proof(config, command, authorization)
    if not first.get("ok") or second.get("ok"):
        raise RuntimeError("EutherID shadow verification or replay rejection failed")
    print(
        "EutherNet EutherID shadow smoke passed: verified=true replay_rejected=true command_run=false"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
