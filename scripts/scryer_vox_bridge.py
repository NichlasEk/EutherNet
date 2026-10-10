"""Forced SSH command: report reads and acknowledgements only. No shell dispatch."""

import json
import sys
import urllib.request


def request(payload):
    command = payload.get("command")
    if command not in {"list", "report-ack", "report-snooze"}:
        raise ValueError("command denied")
    base = "http://127.0.0.1:8791/api/euthernet/scryer"
    if command != "list":
        identity = payload.get("id")
        if not isinstance(identity, str) or len(identity) > 80:
            raise ValueError("invalid report")
        req = urllib.request.Request(
            base + "/control",
            data=json.dumps({"command": command, "ghost_id": identity}).encode(),
            headers={"Content-Type": "application/json"},
        )
    else:
        req = base
    with urllib.request.urlopen(req, timeout=8) as response:
        value = json.loads(response.read(524289))
    state = value["state"]
    return {
        "available": True,
        "reports": value["reports"],
        "inventory_at": value["world"]["stamp"],
        "paused": state["paused"],
        "disabled": state["disabled"],
        "observer_error": bool(value["diagnostics"]["error"]),
        "fetched_at": value["updated_at"],
    }


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(2049)
        if len(raw) > 2048:
            raise ValueError("oversized request")
        print(json.dumps(request(json.loads(raw))))
    except Exception:
        print(
            json.dumps(
                {
                    "available": False,
                    "error": "Report access unavailable; action not confirmed.",
                }
            )
        )
        sys.exit(1)
