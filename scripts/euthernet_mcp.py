#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from euthernet_cli import command_ask, latest_snapshot, local_answer, parse_repos
from euthernet_inventory import collect, load_config, run_configured, write_outputs


def response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def text_content(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def snapshot_context(config: dict[str, Any]) -> tuple[dict[str, Any] | None, pathlib.Path]:
    state_root = pathlib.Path(config["server"].get("state_root", "state"))
    return latest_snapshot(state_root), state_root


def resource_text(config: dict[str, Any], uri: str) -> str:
    snapshot, _ = snapshot_context(config)
    inventory_root = pathlib.Path(config["server"].get("inventory_root", "inventory"))
    if uri == "euthernet://server/current":
        if snapshot is None:
            return "No snapshot exists. Run refresh_inventory first."
        return json.dumps(snapshot, indent=2, sort_keys=True)
    if uri == "euthernet://server/summary":
        path = inventory_root / "server.md"
        return path.read_text(encoding="utf-8") if path.exists() else "No server.md inventory exists."
    if uri == "euthernet://server/repos":
        if snapshot is None:
            return "No snapshot exists."
        return json.dumps(parse_repos(snapshot), indent=2, sort_keys=True)
    raise KeyError(uri)


def list_tools(config: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = [item["name"] for item in config.get("commands", {}).get("allowed", [])]
    return [
        {
            "name": "refresh_inventory",
            "description": "Collect a new read-only server inventory over SSH.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "status",
            "description": "Summarize the latest inventory status.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "ask",
            "description": "Ask a question about the latest EutherNet inventory.",
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_command",
            "description": "Run a named read-only remote command from the EutherNet allowlist.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": allowed}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    ]


def call_tool(config: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "refresh_inventory":
        snapshot = collect(config)
        write_outputs(config, snapshot)
        return {"content": text_content("Inventory refreshed.")}

    if name == "status":
        snapshot, _ = snapshot_context(config)
        if snapshot is None:
            return {"content": text_content("No snapshot exists.")}
        repos = parse_repos(snapshot)
        text = (
            f"{snapshot['server']['name']} collected_at={snapshot['collected_at']} "
            f"ssh_preflight={snapshot.get('ssh_preflight', {}).get('ok')} repos={len(repos)}"
        )
        return {"content": text_content(text)}

    if name == "ask":
        question = arguments.get("question", "")
        return {"content": text_content(local_answer(config, question))}

    if name == "run_command":
        command_name = arguments.get("name", "")
        commands = {item["name"]: item for item in config.get("commands", {}).get("allowed", [])}
        if command_name not in commands:
            return {"isError": True, "content": text_content("Unknown command.")}
        result = run_configured(config, commands[command_name]["command"], timeout=45)
        text = result.get("stdout") or result.get("stderr") or ""
        return {"isError": not result.get("ok"), "content": text_content(text)}

    return {"isError": True, "content": text_content(f"Unknown tool: {name}")}


def handle(config: dict[str, Any], message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"resources": {}, "tools": {}},
                "serverInfo": {"name": "euthernet", "version": "0.1.0"},
            },
        )
    if method == "resources/list":
        return response(
            request_id,
            {
                "resources": [
                    {
                        "uri": "euthernet://server/current",
                        "name": "Current server snapshot",
                        "mimeType": "application/json",
                    },
                    {
                        "uri": "euthernet://server/summary",
                        "name": "Server inventory summary",
                        "mimeType": "text/markdown",
                    },
                    {
                        "uri": "euthernet://server/repos",
                        "name": "Server git repositories",
                        "mimeType": "application/json",
                    },
                ]
            },
        )
    if method == "resources/read":
        uri = params.get("uri", "")
        try:
            text = resource_text(config, uri)
        except KeyError:
            return error_response(request_id, -32602, f"Unknown resource: {uri}")
        return response(request_id, {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]})
    if method == "tools/list":
        return response(request_id, {"tools": list_tools(config)})
    if method == "tools/call":
        return response(
            request_id,
            call_tool(config, params.get("name", ""), params.get("arguments", {})),
        )
    return error_response(request_id, -32601, f"Unknown method: {method}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the EutherNet stdio MCP server.")
    parser.add_argument("--config", default="euthernet.toml")
    args = parser.parse_args(argv)
    config = load_config(pathlib.Path(args.config))

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            reply = handle(config, message)
        except Exception as exc:
            reply = error_response(None, -32603, str(exc))
        if reply is not None:
            print(json.dumps(reply), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
