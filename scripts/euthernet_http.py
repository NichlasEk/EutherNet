#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from euthernet_cli import (
    answer_question,
    backup_manifest,
    drift_changes,
    eutherverse_map,
    full_report,
    latest_snapshot,
    operational_summary,
    parse_repos,
    restore_drill,
    restore_bundle,
    restore_plan,
    run_allowed_command,
)
from euthernet_inventory import collect, load_config, write_outputs


class EutherNetHTTP(BaseHTTPRequestHandler):
    config: dict[str, Any]

    server_version = "EutherNetHTTP/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def write_json(self, status: HTTPStatus, payload: dict[str, Any] | list[Any]) -> None:
        raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def write_error(self, status: HTTPStatus, message: str) -> None:
        self.write_json(status, {"ok": False, "error": message})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/euthernet/status":
            self.handle_status()
            return
        if path == "/api/euthernet/repos":
            self.handle_repos()
            return
        if path == "/api/euthernet/inventory":
            self.handle_inventory()
            return
        if path == "/api/euthernet/commands":
            self.handle_commands()
            return
        if path == "/api/euthernet/report":
            self.handle_report()
            return
        if path == "/api/euthernet/summary":
            self.handle_summary()
            return
        if path == "/api/euthernet/changes":
            self.handle_changes()
            return
        if path == "/api/euthernet/restore-plan":
            self.handle_restore_plan()
            return
        if path == "/api/euthernet/restore-bundle":
            self.handle_restore_bundle()
            return
        if path == "/api/euthernet/backup-manifest":
            self.handle_backup_manifest()
            return
        if path == "/api/euthernet/restore-drill":
            self.handle_restore_drill()
            return
        if path == "/api/euthernet/map":
            self.handle_map()
            return
        self.write_error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/euthernet/ask":
            self.handle_ask()
            return
        if path == "/api/euthernet/refresh":
            self.handle_refresh()
            return
        if path == "/api/euthernet/run":
            self.handle_run()
            return
        if path == "/api/euthernet/eutherium/award":
            self.handle_eutherium_award()
            return
        self.write_error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def current_snapshot(self) -> dict[str, Any] | None:
        state_root = pathlib.Path(self.config["server"].get("state_root", "state"))
        return latest_snapshot(state_root)

    def handle_status(self) -> None:
        snapshot = self.current_snapshot()
        if snapshot is None:
            self.write_error(HTTPStatus.NOT_FOUND, "no snapshot exists; run refresh first")
            return

        repos = parse_repos(snapshot)
        collectors: dict[str, Any] = {}
        for name, group in snapshot.get("collectors", {}).items():
            if name == "git_repositories":
                collectors[name] = {
                    "ok": bool(group.get("scan", {}).get("ok")),
                    "repository_count": len(repos),
                }
            elif "preflight" in group:
                collectors[name] = {"ok": False, "skipped": True}
            else:
                total = len(group)
                ok = sum(1 for value in group.values() if value.get("ok"))
                collectors[name] = {"ok_commands": ok, "total_commands": total}

        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "server": snapshot["server"],
                "collected_at": snapshot["collected_at"],
                "ssh_preflight": bool(snapshot.get("ssh_preflight", {}).get("ok")),
                "collectors": collectors,
            },
        )

    def handle_repos(self) -> None:
        snapshot = self.current_snapshot()
        if snapshot is None:
            self.write_error(HTTPStatus.NOT_FOUND, "no snapshot exists; run refresh first")
            return
        self.write_json(HTTPStatus.OK, {"ok": True, "repos": parse_repos(snapshot)})

    def handle_inventory(self) -> None:
        snapshot = self.current_snapshot()
        if snapshot is None:
            self.write_error(HTTPStatus.NOT_FOUND, "no snapshot exists; run refresh first")
            return
        self.write_json(HTTPStatus.OK, {"ok": True, "snapshot": snapshot})

    def handle_commands(self) -> None:
        commands = [
            {"name": item["name"], "description": item.get("description", "")}
            for item in self.config.get("commands", {}).get("allowed", [])
        ]
        self.write_json(HTTPStatus.OK, {"ok": True, "commands": commands})

    def handle_report(self) -> None:
        report = full_report(self.config)
        if not report.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(report.get("error", "report unavailable")))
            return
        self.write_json(HTTPStatus.OK, report)

    def handle_summary(self) -> None:
        summary = operational_summary(self.config)
        if not summary.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(summary.get("error", "summary unavailable")))
            return
        self.write_json(HTTPStatus.OK, summary)

    def handle_changes(self) -> None:
        changes = drift_changes(self.config)
        if not changes.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(changes.get("error", "changes unavailable")))
            return
        self.write_json(HTTPStatus.OK, changes)

    def handle_restore_plan(self) -> None:
        plan = restore_plan(self.config)
        if not plan.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(plan.get("error", "restore plan unavailable")))
            return
        self.write_json(HTTPStatus.OK, plan)

    def handle_restore_bundle(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        profile = query.get("profile", ["full"])[0]
        bundle = restore_bundle(self.config, profile=profile)
        if not bundle.get("ok"):
            self.write_error(HTTPStatus.BAD_REQUEST, str(bundle.get("error", "restore bundle unavailable")))
            return
        self.write_json(HTTPStatus.OK, bundle)

    def handle_backup_manifest(self) -> None:
        manifest = backup_manifest(self.config)
        if not manifest.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(manifest.get("error", "backup manifest unavailable")))
            return
        self.write_json(HTTPStatus.OK, manifest)

    def handle_restore_drill(self) -> None:
        drill = restore_drill(self.config)
        if not drill.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(drill.get("error", "restore drill unavailable")))
            return
        self.write_json(HTTPStatus.OK, drill)

    def handle_map(self) -> None:
        server_map = eutherverse_map(self.config)
        if not server_map.get("ok"):
            self.write_error(HTTPStatus.NOT_FOUND, str(server_map.get("error", "server map unavailable")))
            return
        self.write_json(HTTPStatus.OK, server_map)

    def handle_ask(self) -> None:
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        question = str(payload.get("question", "")).strip()
        if not question:
            self.write_error(HTTPStatus.BAD_REQUEST, "question is required")
            return
        self.write_json(HTTPStatus.OK, {"ok": True, **answer_question(self.config, question)})

    def handle_refresh(self) -> None:
        snapshot = collect(self.config)
        write_outputs(self.config, snapshot)
        self.write_json(
            HTTPStatus.OK,
            {
                "ok": bool(snapshot.get("ssh_preflight", {}).get("ok")),
                "collected_at": snapshot["collected_at"],
                "ssh_preflight": bool(snapshot.get("ssh_preflight", {}).get("ok")),
            },
        )


    def handle_eutherium_award(self) -> None:
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        result = award_eutherium(self.config, payload)
        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
        self.write_json(status, result)

    def handle_run(self) -> None:
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        name = str(payload.get("name", "")).strip()
        if not name:
            self.write_error(HTTPStatus.BAD_REQUEST, "name is required")
            return
        result = run_allowed_command(self.config, name)
        self.write_json(HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST, result)


def award_eutherium(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    user = clean_user(str(payload.get("user", payload.get("userId", ""))))
    if not user:
        return {"ok": False, "error": "user is required"}
    try:
        amount = int(payload.get("amount", 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "amount must be an integer"}
    if amount <= 0 or amount > 1_000_000:
        return {"ok": False, "error": "amount must be between 1 and 1000000"}
    reason = str(payload.get("reason", "")).strip()
    if not reason or len(reason) > 160:
        return {"ok": False, "error": "reason must be 1-160 characters"}
    source = clean_source(str(payload.get("source", "euthernet"))) or "euthernet"
    created_by = clean_source(str(payload.get("createdBy", source))) or source
    idempotency_key = clean_idempotency(str(payload.get("idempotencyKey", "")))
    host_dir = eutherium_host_dir(config)
    users = load_eutheroxide_users(host_dir / "users.toml")
    if user not in users:
        return {"ok": False, "error": f"unknown or banned user: {user}"}
    ledger_path = host_dir / "eutherium" / "ledger.json"
    ledger = load_json_list(ledger_path)
    entry_id = eutherium_entry_id(source, user, idempotency_key)
    for entry in ledger:
        if str(entry.get("id", "")) == entry_id:
            return {
                "ok": True,
                "awarded": False,
                "duplicate": True,
                "entry": entry,
                "balance": eutherium_balance(ledger, user),
            }
    entry = {
        "id": entry_id,
        "userId": user,
        "amount": amount,
        "reason": reason,
        "source": source,
        "createdByUserId": created_by,
        "createdUnixMs": int(time.time() * 1000),
    }
    ledger.append(entry)
    save_json_list(ledger_path, ledger)
    return {
        "ok": True,
        "awarded": True,
        "duplicate": False,
        "entry": entry,
        "balance": eutherium_balance(ledger, user),
    }


def eutherium_host_dir(config: dict[str, Any]) -> pathlib.Path:
    configured = config.get("eutherium", {}).get("host_dir")
    return pathlib.Path(configured or "/home/nichlas/EutherOxide/.euther-host")


def load_eutheroxide_users(path: pathlib.Path) -> set[str]:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    users: set[str] = set()
    current_name = ""
    current_banned = False
    in_user = False
    for raw in contents.splitlines() + ["[[user]]"]:
        line = raw.strip()
        if line == "[[user]]":
            if in_user and current_name and not current_banned:
                users.add(current_name)
            in_user = True
            current_name = ""
            current_banned = False
            continue
        if not in_user or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key == "name":
            current_name = value.strip().strip('"')
        elif key == "banned":
            current_banned = value.lower() == "true"
    return users


def load_json_list(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError(f"expected JSON list in {path}")
    return [item for item in value if isinstance(item, dict)]


def save_json_list(path: pathlib.Path, value: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def eutherium_balance(ledger: list[dict[str, Any]], user: str) -> int:
    return sum(int(entry.get("amount", 0)) for entry in ledger if entry.get("userId") == user)


def eutherium_entry_id(source: str, user: str, idempotency_key: str) -> str:
    if idempotency_key:
        return f"euthernet-{source}-{idempotency_key}"
    return f"euthernet-{source}-{int(time.time() * 1000)}-{clean_idempotency(user)}"


def clean_user(value: str) -> str:
    return "".join(ch for ch in value.strip()[:80] if ch.isalnum() or ch in "_.-")


def clean_source(value: str) -> str:
    return "".join(ch for ch in value.strip().lower()[:48] if ch.isalnum() or ch in "_.-")


def clean_idempotency(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]", "-", value.strip())[:120]
    return clean.strip("-")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the local EutherNet HTTP API.")
    parser.add_argument("--config", default="euthernet.toml")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)

    config = load_config(pathlib.Path(args.config))
    http_config = config.get("http", {})
    host = args.host or http_config.get("host", "127.0.0.1")
    port = args.port or int(http_config.get("port", 8791))

    EutherNetHTTP.config = config
    server = ThreadingHTTPServer((host, port), EutherNetHTTP)
    print(f"euthernet http listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
