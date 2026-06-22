#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from euthernet_cli import answer_question, latest_snapshot, parse_repos, run_allowed_command
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
