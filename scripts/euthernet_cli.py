#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import textwrap
import urllib.error
import urllib.request
from typing import Any

from euthernet_inventory import load_config, run_configured


def latest_snapshot(state_root: pathlib.Path) -> dict[str, Any] | None:
    snapshots = sorted(state_root.glob("snapshot-*.json"))
    if not snapshots:
        return None
    return json.loads(snapshots[-1].read_text(encoding="utf-8"))


def parse_repos(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    scan = snapshot.get("collectors", {}).get("git_repositories", {}).get("scan", {})
    repos: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in scan.get("stdout", "").splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key == "REPO":
            if current:
                repos.append(current)
            current = {"path": value, "remote": "", "branch": "", "head": "", "dirty_lines": "0"}
        elif current is not None:
            current[key.lower()] = value.strip()
    if current:
        repos.append(current)
    return repos


def command_status(config: dict[str, Any]) -> int:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        print("No state snapshot found. Run `make inventory` first.")
        return 1

    server = snapshot["server"]
    print(f"{server['name']} @ {server.get('lan_host')} ({server.get('public_host')})")
    print(f"collected_at: {snapshot['collected_at']}")
    print(f"ssh_preflight: {'ok' if snapshot.get('ssh_preflight', {}).get('ok') else 'failed'}")

    for name, group in snapshot.get("collectors", {}).items():
        if name == "git_repositories":
            scan = group.get("scan", {})
            print(f"{name}: {'ok' if scan.get('ok') else 'failed'} ({len(parse_repos(snapshot))} repos)")
        elif "preflight" in group:
            print(f"{name}: skipped")
        else:
            total = len(group)
            ok = sum(1 for value in group.values() if value.get("ok"))
            print(f"{name}: {ok}/{total} commands ok")
    return 0


def command_repos(config: dict[str, Any]) -> int:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        print("No state snapshot found. Run `make inventory` first.")
        return 1

    repos = parse_repos(snapshot)
    if not repos:
        print("No repositories found in latest snapshot.")
        return 1

    for repo in repos:
        dirty = int(repo.get("dirty_lines") or "0")
        marker = "dirty" if dirty else "clean"
        branch = repo.get("branch") or "(detached/unknown)"
        print(f"{repo['path']} [{branch} {repo.get('head', '')}] {marker}")
        if repo.get("remote"):
            print(f"  remote: {repo['remote']}")
    return 0


def local_answer(config: dict[str, Any], question: str) -> str:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return "Jag har ingen snapshot ännu. Kör `make inventory` först."

    question_lc = question.lower()
    repos = parse_repos(snapshot)
    server = snapshot["server"]

    if any(word in question_lc for word in ["repo", "git", "repository"]):
        dirty = [repo for repo in repos if int(repo.get("dirty_lines") or "0") > 0]
        lines = [f"Jag ser {len(repos)} git-repon på {server['name']}."]
        if dirty:
            lines.append(f"{len(dirty)} verkar ha lokala ändringar:")
            lines.extend(f"- {repo['path']} ({repo.get('dirty_lines')} statusrader)" for repo in dirty)
        else:
            lines.append("Inga dirty repos syns i senaste snapshoten.")
        return "\n".join(lines)

    if any(word in question_lc for word in ["status", "mår", "hälsa", "health"]):
        preflight = "ok" if snapshot.get("ssh_preflight", {}).get("ok") else "failed"
        failed = snapshot.get("collectors", {}).get("systemd", {}).get("failed_services", {})
        failed_text = failed.get("stdout", "")
        failed_summary = "inga failed services" if "0 loaded units listed" in failed_text else "se failed-services"
        return (
            f"Senaste snapshoten är från {snapshot['collected_at']}. "
            f"SSH är {preflight}, och systemd visar {failed_summary}."
        )

    if any(word in question_lc for word in ["kommando", "command", "köra", "run"]):
        names = [item["name"] for item in config.get("commands", {}).get("allowed", [])]
        return "Tillåtna kommandon är: " + ", ".join(names)

    return (
        "Jag kan svara på senaste inventoryn lokalt. Prova till exempel: "
        "`make ask Q=\"hur mår servern?\"`, `make ask Q=\"vilka repos finns?\"`, "
        "eller `make run CMD=health`."
    )


def ai_context(config: dict[str, Any]) -> str:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return "No snapshot exists."

    repos = parse_repos(snapshot)
    collectors = snapshot.get("collectors", {})
    lines = [
        f"server={snapshot['server']}",
        f"collected_at={snapshot['collected_at']}",
        f"ssh_preflight_ok={snapshot.get('ssh_preflight', {}).get('ok')}",
    ]
    for name, group in collectors.items():
        if name == "git_repositories":
            lines.append(f"collector.git_repositories.ok={group.get('scan', {}).get('ok')}")
            lines.append(f"collector.git_repositories.count={len(repos)}")
        elif "preflight" in group:
            lines.append(f"collector.{name}=skipped")
        else:
            total = len(group)
            ok = sum(1 for value in group.values() if value.get("ok"))
            lines.append(f"collector.{name}={ok}/{total}")

    dirty = [repo for repo in repos if int(repo.get("dirty_lines") or "0") > 0]
    lines.append("repos:")
    for repo in repos:
        lines.append(
            f"- path={repo['path']} branch={repo.get('branch')} head={repo.get('head')} "
            f"dirty_lines={repo.get('dirty_lines')} remote={repo.get('remote')}"
        )
    if dirty:
        lines.append("dirty_repos=" + ", ".join(repo["path"] for repo in dirty))
    return "\n".join(lines)


def ai_answer(config: dict[str, Any], question: str, local_context: str) -> str | None:
    ai = config.get("ai", {})
    if not ai.get("enabled"):
        return None

    endpoint = ai.get("endpoint", "").rstrip("/")
    model = ai.get("model")
    if not endpoint or not model:
        return None

    payload = {
        "model": model,
        "prompt": (
            "Du är en lokal serverassistent för EutherNet. "
            "Svara kort, praktiskt och basera dig bara på kontexten. "
            "Säg tydligt om något inte finns i inventoryn. "
            "Föreslå bara kommandon som finns i allowlisten om användaren vill köra något.\n\n"
            f"Snabbsvar från deterministisk inventorylogik:\n{local_context}\n\n"
            f"Inventorykontext:\n{ai_context(config)}\n\nFråga: {question}\n"
        ),
        "stream": False,
    }
    request = urllib.request.Request(
        f"{endpoint}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("response")
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def answer_question(config: dict[str, Any], question: str) -> dict[str, Any]:
    local = local_answer(config, question)
    ai = ai_answer(config, question, local)
    return {
        "answer": ai or local,
        "source": "ai" if ai else "inventory",
        "fallback": local if ai else "",
    }


def command_ask(config: dict[str, Any], question: str) -> int:
    print(answer_question(config, question)["answer"])
    return 0


def run_allowed_command(config: dict[str, Any], command_name: str) -> dict[str, Any]:
    commands = {item["name"]: item for item in config.get("commands", {}).get("allowed", [])}
    if command_name not in commands:
        return {
            "ok": False,
            "error": "unknown command",
            "allowed": [
                {"name": name, "description": item.get("description", "")}
                for name, item in commands.items()
            ],
        }

    if not config.get("commands", {}).get("allow_remote", False):
        return {"ok": False, "error": "remote commands are disabled in config"}

    server = config["server"]
    result = run_configured(config, commands[command_name]["command"], timeout=45)
    return {
        "ok": bool(result.get("ok")),
        "name": command_name,
        "description": commands[command_name].get("description", ""),
        "returncode": result.get("returncode"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


def command_run(config: dict[str, Any], command_name: str) -> int:
    result = run_allowed_command(config, command_name)
    if result.get("error") == "unknown command":
        print("Unknown command. Allowed commands:")
        for item in result.get("allowed", []):
            print(f"- {item['name']}: {item.get('description', '')}")
        return 1
    if result.get("error"):
        print(result["error"])
        return 1
    if result.get("stdout"):
        print(result["stdout"])
    if result.get("stderr"):
        print(result["stderr"], file=sys.stderr)
    return 0 if result.get("ok") else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Ask and operate on the latest EutherNet inventory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              scripts/euthernet_cli.py status
              scripts/euthernet_cli.py repos
              scripts/euthernet_cli.py ask "hur mår servern?"
              scripts/euthernet_cli.py run health
            """
        ),
    )
    parser.add_argument("--config", default="euthernet.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("repos")

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("question")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("name")

    args = parser.parse_args(argv)
    config = load_config(pathlib.Path(args.config))

    if args.command == "status":
        return command_status(config)
    if args.command == "repos":
        return command_repos(config)
    if args.command == "ask":
        return command_ask(config, args.question)
    if args.command == "run":
        return command_run(config, args.name)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
