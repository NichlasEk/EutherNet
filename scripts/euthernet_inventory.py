#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|passphrase|token|secret|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(Authorization:\s*)\S+"),
]


@dataclass(frozen=True)
class RemoteCommand:
    name: str
    command: str


SYSTEM_COMMANDS = [
    RemoteCommand("hostname", "hostname"),
    RemoteCommand("date", "date -Is"),
    RemoteCommand("uname", "uname -a"),
    RemoteCommand("os_release", "cat /etc/os-release"),
    RemoteCommand("uptime", "uptime"),
    RemoteCommand("disk", "df -hT"),
    RemoteCommand("memory", "free -h"),
    RemoteCommand(
        "packages",
        "if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${binary:Package}\\t${Version}\\n'; "
        "elif command -v pacman >/dev/null 2>&1; then pacman -Q; "
        "else printf 'package inventory unavailable\\n'; fi",
    ),
]

SYSTEMD_COMMANDS = [
    RemoteCommand(
        "failed_services",
        "systemctl list-units --type=service --state=failed --no-pager --plain",
    ),
    RemoteCommand(
        "running_services",
        "systemctl list-units --type=service --state=running --no-pager --plain",
    ),
    RemoteCommand("timers", "systemctl list-timers --all --no-pager --plain"),
]

NETWORK_COMMANDS = [
    RemoteCommand("addresses", "hostname -I"),
    RemoteCommand("listening_tcp_udp", "ss -tuln"),
    RemoteCommand("listening_processes", "ss -tulpn"),
    RemoteCommand("ssh_connections", "ss -tnp | awk 'NR == 1 || /:22|ssh/'"),
]


def load_config(path: pathlib.Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def redact(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: match.group(1) + "[REDACTED]", redacted)
    return redacted


def run_ssh(host: str, command: str, timeout: int = 20) -> dict[str, Any]:
    ssh_command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        host,
        command,
    ]
    try:
        result = subprocess.run(
            ssh_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": redact(result.stdout.strip()),
            "stderr": redact(result.stderr.strip()),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": redact((exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""),
            "stderr": f"timeout after {timeout}s",
        }


def run_local(command: str, timeout: int = 20) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": redact(result.stdout.strip()),
            "stderr": redact(result.stderr.strip()),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": redact((exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""),
            "stderr": f"timeout after {timeout}s",
        }


def run_configured(config: dict[str, Any], command: str, timeout: int = 20) -> dict[str, Any]:
    server = config["server"]
    transport = server.get("command_transport", "ssh")
    if transport == "local":
        return run_local(command, timeout=timeout)
    host = server.get("ssh_host_alias") or server["lan_host"]
    return run_ssh(host, command, timeout=timeout)


def bundle_command(commands: list[RemoteCommand]) -> str:
    parts = ["set +e"]
    for item in commands:
        parts.append(f"printf '\\n__EUTHERNET_BEGIN__\\t{item.name}\\n'")
        parts.append(f"( {item.command} ) 2>&1")
        parts.append("rc=$?")
        parts.append(f"printf '\\n__EUTHERNET_END__\\t{item.name}\\t%s\\n' \"$rc\"")
    return "\n".join(parts)


def parse_bundle(result: dict[str, Any], commands: list[RemoteCommand]) -> dict[str, Any]:
    parsed = {
        item.name: {
            "ok": False,
            "returncode": result.get("returncode"),
            "stdout": "",
            "stderr": result.get("stderr", ""),
        }
        for item in commands
    }
    if not result.get("stdout"):
        return parsed

    current_name: str | None = None
    current_lines: list[str] = []
    for line in result["stdout"].splitlines():
        if line.startswith("__EUTHERNET_BEGIN__\t"):
            current_name = line.split("\t", 1)[1]
            current_lines = []
            continue
        if line.startswith("__EUTHERNET_END__\t"):
            _, name, rc_text = line.split("\t", 2)
            if current_name == name and name in parsed:
                rc = int(rc_text)
                parsed[name] = {
                    "ok": rc == 0,
                    "returncode": rc,
                    "stdout": redact("\n".join(current_lines).strip()),
                    "stderr": "",
                }
            current_name = None
            current_lines = []
            continue
        if current_name is not None:
            current_lines.append(line)
    return parsed


def command_group(config: dict[str, Any], commands: list[RemoteCommand]) -> dict[str, Any]:
    return parse_bundle(run_configured(config, bundle_command(commands), timeout=45), commands)


def git_inventory_command(roots: list[str]) -> str:
    quoted_roots = " ".join(shlex.quote(root) for root in roots)
    return f"""
set -eu
find {quoted_roots} -maxdepth 4 -type d -name .git -prune 2>/dev/null | while read -r gitdir; do
  repo="${{gitdir%/.git}}"
  printf 'REPO\\t%s\\n' "$repo"
  git -C "$repo" remote get-url origin 2>/dev/null | sed 's/^/REMOTE\\t/' || true
  git -C "$repo" branch --show-current 2>/dev/null | sed 's/^/BRANCH\\t/' || true
  git -C "$repo" rev-parse --short HEAD 2>/dev/null | sed 's/^/HEAD\\t/' || true
  git -C "$repo" status --short 2>/dev/null | wc -l | sed 's/^/DIRTY_LINES\\t/' || true
done
""".strip()


def collect(config: dict[str, Any]) -> dict[str, Any]:
    server = config["server"]
    enabled = {
        item["name"]: item for item in config.get("collectors", []) if item.get("enabled", True)
    }

    output: dict[str, Any] = {
        "collected_at": utc_now(),
        "server": {
            "name": server["name"],
            "lan_host": server.get("lan_host"),
            "public_host": server.get("public_host"),
            "ssh_host_alias": server.get("ssh_host_alias"),
            "command_transport": server.get("command_transport", "ssh"),
        },
        "collectors": {},
    }

    preflight = run_configured(config, "true", timeout=12)
    output["ssh_preflight"] = preflight
    if not preflight["ok"]:
        for name in enabled:
            output["collectors"][name] = {
                "preflight": {
                    "ok": False,
                    "returncode": preflight.get("returncode"),
                    "stdout": "",
                    "stderr": preflight.get("stderr", "ssh preflight failed"),
                }
            }
        return output

    if "system" in enabled:
        output["collectors"]["system"] = command_group(config, SYSTEM_COMMANDS)
    if "systemd" in enabled:
        output["collectors"]["systemd"] = command_group(config, SYSTEMD_COMMANDS)
    if "network" in enabled:
        output["collectors"]["network"] = command_group(config, NETWORK_COMMANDS)
    if "git_repositories" in enabled:
        roots = enabled["git_repositories"].get("roots", ["/home/nichlas"])
        output["collectors"]["git_repositories"] = {
            "scan": run_configured(config, git_inventory_command(roots), timeout=60)
        }

    return output


def ok_count(group: dict[str, Any]) -> tuple[int, int]:
    total = len(group)
    ok = sum(1 for value in group.values() if isinstance(value, dict) and value.get("ok"))
    return ok, total


def first_line(value: str) -> str:
    return value.splitlines()[0] if value else ""


def render_markdown(snapshot: dict[str, Any]) -> str:
    server = snapshot["server"]
    collectors = snapshot["collectors"]
    lines = [
        f"# {server['name']} Server Inventory",
        "",
        f"- Collected at: `{snapshot['collected_at']}`",
        f"- LAN host: `{server.get('lan_host', '')}`",
        f"- Public host: `{server.get('public_host', '')}`",
        f"- SSH host alias: `{server.get('ssh_host_alias', '')}`",
        f"- Command transport: `{server.get('command_transport', 'ssh')}`",
        "",
        "## Collector Status",
        "",
    ]

    preflight = snapshot.get("ssh_preflight", {})
    if preflight:
        status = "ok" if preflight.get("ok") else "failed"
        lines.append(f"- `ssh_preflight`: {status}")
        if not preflight.get("ok") and preflight.get("stderr"):
            lines.extend(["", "## SSH Preflight Error", "", "```text", preflight["stderr"], "```", ""])

    for name, group in collectors.items():
        if name == "git_repositories":
            scan = group.get("scan", {})
            preflight_status = group.get("preflight", {})
            status = "ok" if scan.get("ok") else "failed"
            if preflight_status:
                status = "skipped: ssh preflight failed"
            lines.append(f"- `{name}`: {status}")
        elif "preflight" in group:
            lines.append(f"- `{name}`: skipped: ssh preflight failed")
        else:
            ok, total = ok_count(group)
            lines.append(f"- `{name}`: {ok}/{total} commands ok")

    system = collectors.get("system", {})
    hostname = first_line(system.get("hostname", {}).get("stdout", ""))
    uname = first_line(system.get("uname", {}).get("stdout", ""))
    uptime = first_line(system.get("uptime", {}).get("stdout", ""))
    if hostname or uname or uptime:
        lines.extend(["", "## System", ""])
        if hostname:
            lines.append(f"- Hostname: `{hostname}`")
        if uname:
            lines.append(f"- Kernel: `{uname}`")
        if uptime:
            lines.append(f"- Uptime: `{uptime}`")

    network = collectors.get("network", {})
    addresses = first_line(network.get("addresses", {}).get("stdout", ""))
    if addresses:
        lines.extend(["", "## Network", "", f"- Addresses: `{addresses}`"])

    systemd = collectors.get("systemd", {})
    failed = systemd.get("failed_services", {}).get("stdout", "")
    if failed:
        lines.extend(["", "## Failed Services", "", "```text", failed[:4000], "```"])

    git_scan = collectors.get("git_repositories", {}).get("scan", {}).get("stdout", "")
    repos = [line.split("\t", 1)[1] for line in git_scan.splitlines() if line.startswith("REPO\t")]
    lines.extend(["", "## Git Repositories", ""])
    if repos:
        for repo in repos:
            lines.append(f"- `{repo}`")
    else:
        lines.append("- No repositories found or repository scan failed.")

    return "\n".join(lines).rstrip() + "\n"


def toml_string(value: Any) -> str:
    if value is None:
        return '""'
    return json.dumps(str(value))


def render_toml(snapshot: dict[str, Any]) -> str:
    server = snapshot["server"]
    lines = [
        f'collected_at = {toml_string(snapshot["collected_at"])}',
        "",
        "[server]",
        f'name = {toml_string(server.get("name"))}',
        f'lan_host = {toml_string(server.get("lan_host"))}',
        f'public_host = {toml_string(server.get("public_host"))}',
        f'ssh_host_alias = {toml_string(server.get("ssh_host_alias"))}',
        f'command_transport = {toml_string(server.get("command_transport", "ssh"))}',
        "",
    ]

    for name, group in snapshot["collectors"].items():
        lines.append(f"[collectors.{name}]")
        if "preflight" in group:
            lines.append("ok = false")
            lines.append('error = "ssh preflight failed"')
        elif name == "git_repositories":
            scan = group.get("scan", {})
            lines.append(f"ok = {str(bool(scan.get('ok'))).lower()}")
            lines.append(f"returncode = {scan.get('returncode') if scan.get('returncode') is not None else -1}")
            repo_count = scan.get("stdout", "").count("REPO\t")
            lines.append(f"repository_count = {repo_count}")
        else:
            ok, total = ok_count(group)
            lines.append(f"ok_commands = {ok}")
            lines.append(f"total_commands = {total}")
        lines.append("")

    return "\n".join(lines)


def write_outputs(config: dict[str, Any], snapshot: dict[str, Any]) -> None:
    inventory_root = pathlib.Path(config["server"].get("inventory_root", "inventory"))
    state_root = pathlib.Path(config["server"].get("state_root", "state"))
    inventory_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)

    stamp = snapshot["collected_at"].replace(":", "").replace("+", "Z")
    (state_root / f"snapshot-{stamp}.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (inventory_root / "server.md").write_text(render_markdown(snapshot), encoding="utf-8")
    (inventory_root / "server.toml").write_text(render_toml(snapshot), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Collect a read-only EutherNet server inventory.")
    parser.add_argument("--config", default="euthernet.toml", help="Path to EutherNet TOML config.")
    parser.add_argument("--no-write", action="store_true", help="Print JSON snapshot without writing files.")
    args = parser.parse_args(argv)

    config = load_config(pathlib.Path(args.config))
    snapshot = collect(config)
    if args.no_write:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        write_outputs(config, snapshot)
        print("wrote inventory/server.md, inventory/server.toml, and ignored state snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
