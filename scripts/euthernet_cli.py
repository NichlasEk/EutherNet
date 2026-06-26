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


def recent_snapshots(state_root: pathlib.Path, count: int = 2) -> list[dict[str, Any]]:
    snapshots = sorted(state_root.glob("snapshot-*.json"))[-count:]
    return [json.loads(path.read_text(encoding="utf-8")) for path in snapshots]


def recent_healthy_snapshots(state_root: pathlib.Path, count: int = 2) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in reversed(sorted(state_root.glob("snapshot-*.json"))):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not snapshot.get("ssh_preflight", {}).get("ok"):
            continue
        if not snapshot.get("collectors", {}).get("git_repositories", {}).get("scan", {}).get("ok"):
            continue
        snapshots.append(snapshot)
        if len(snapshots) == count:
            break
    return list(reversed(snapshots))




def systemd_stdout(snapshot: dict[str, Any], command: str) -> str:
    collectors = snapshot.get("collectors", {})
    return "\n".join(
        part
        for part in [
            collectors.get("systemd", {}).get(command, {}).get("stdout", ""),
            collectors.get("systemd_user", {}).get(command, {}).get("stdout", ""),
        ]
        if part
    )


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


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    repos = parse_repos(snapshot)
    dirty_repos = [repo for repo in repos if int(repo.get("dirty_lines") or "0") > 0]
    failed_text = systemd_stdout(snapshot, "failed_services")
    failed_services = failed_service_lines(failed_text)
    disk_text = (
        snapshot.get("collectors", {})
        .get("system", {})
        .get("disk", {})
        .get("stdout", "")
    )
    disk_warnings = disk_usage_warnings(disk_text)
    package_text = (
        snapshot.get("collectors", {})
        .get("system", {})
        .get("packages", {})
        .get("stdout", "")
    )
    listening_text = (
        snapshot.get("collectors", {})
        .get("network", {})
        .get("listening_tcp_udp", {})
        .get("stdout", "")
    )
    return {
        "collected_at": snapshot.get("collected_at", ""),
        "server": snapshot.get("server", {}),
        "ssh_preflight": bool(snapshot.get("ssh_preflight", {}).get("ok")),
        "repository_count": len(repos),
        "dirty_repository_count": len(dirty_repos),
        "dirty_repositories": dirty_repos,
        "failed_service_count": len(failed_services),
        "failed_services": failed_services,
        "disk_warning_count": len(disk_warnings),
        "disk_warnings": disk_warnings,
        "package_count": len(parse_packages(package_text)),
        "listening_port_count": len(parse_listening_ports(listening_text)),
    }


def failed_service_lines(value: str) -> list[str]:
    lines: list[str] = []
    for line in value.splitlines():
        line = line.strip()
        if not line or line.startswith("UNIT ") or line.startswith("Legend:") or " loaded units listed" in line:
            continue
        if " loaded failed " in line:
            lines.append(line)
    return lines


def disk_usage_warnings(value: str, threshold: int = 85) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for line in value.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        use = parts[5]
        if not use.endswith("%"):
            continue
        try:
            used_percent = int(use[:-1])
        except ValueError:
            continue
        if used_percent >= threshold:
            warnings.append(
                {
                    "filesystem": parts[0],
                    "type": parts[1],
                    "size": parts[2],
                    "used": parts[3],
                    "available": parts[4],
                    "used_percent": used_percent,
                    "mount": " ".join(parts[6:]),
                }
            )
    return warnings


def parse_listening_ports(value: str) -> set[str]:
    ports: set[str] = set()
    for line in value.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[1] != "LISTEN":
            continue
        local = parts[4]
        if ":" in local:
                ports.add(local.rsplit(":", 1)[-1])
    return ports


def parse_listening_services(value: str) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for line in value.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[1] != "LISTEN":
            continue
        protocol = parts[0]
        local = parts[4]
        port = local.rsplit(":", 1)[-1] if ":" in local else local
        process = ""
        if len(parts) >= 7:
            process = " ".join(parts[6:])
        services.append(
            {
                "protocol": protocol,
                "local": local,
                "port": port,
                "process": process,
                "status": "listening",
            }
        )
    return services


def parse_running_service_units(value: str) -> set[str]:
    units: set[str] = set()
    for line in value.splitlines():
        line = line.strip()
        if not line or line.startswith("UNIT "):
            continue
        parts = line.split()
        if parts and parts[0].endswith(".service"):
            units.add(parts[0])
    return units


def parse_ssh_connections(value: str) -> list[dict[str, str]]:
    connections: list[dict[str, str]] = []
    for line in value.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] in {"State", "Netid"}:
            continue
        state = parts[0]
        if state not in {"ESTAB", "SYN-SENT", "SYN-RECV", "TIME-WAIT", "CLOSE-WAIT"}:
            continue
        local = parts[3]
        peer = parts[4]
        process = " ".join(parts[5:]) if len(parts) > 5 else ""
        local_port = local.rsplit(":", 1)[-1] if ":" in local else ""
        peer_port = peer.rsplit(":", 1)[-1] if ":" in peer else ""
        direction = "outbound"
        if local_port == "22":
            direction = "inbound"
        elif peer_port == "22":
            direction = "outbound"
        connections.append(
            {
                "state": state,
                "local": local,
                "peer": peer,
                "local_port": local_port,
                "peer_port": peer_port,
                "process": process,
                "direction": direction,
            }
        )
    return connections


def parse_packages(value: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for line in value.splitlines():
        line = line.strip()
        if not line or line == "package inventory unavailable":
            continue
        if "\t" in line:
            name, version = line.split("\t", 1)
        else:
            parts = line.split(maxsplit=1)
            name = parts[0]
            version = parts[1] if len(parts) > 1 else ""
        packages.append({"name": name, "version": version})
    return packages


def repo_key(repo: dict[str, str]) -> str:
    return repo.get("path", "")


def port_sort_key(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


SERVICE_RESTORE_CATALOG: list[dict[str, Any]] = [
    {
        "name": "EutherNet",
        "repo_path": "/home/nichlas/EutherNet",
        "profiles": ["full", "backup"],
        "systemd": ["euthernet.service", "euthernet-refresh.timer"],
        "ports": ["8791"],
        "packages": ["ca-certificates", "curl", "git", "python3", "systemd"],
        "persistent_paths": ["/home/nichlas/EutherNet/state"],
        "steps": [
            "cd /home/nichlas/EutherNet",
            "mkdir -p /home/nichlas/.config/systemd/user",
            "cp deploy/euthernet.service /home/nichlas/.config/systemd/user/euthernet.service",
            "cp deploy/euthernet-refresh.service /home/nichlas/.config/systemd/user/euthernet-refresh.service",
            "cp deploy/euthernet-refresh.timer /home/nichlas/.config/systemd/user/euthernet-refresh.timer",
            "systemctl --user daemon-reload",
            "systemctl --user enable --now euthernet.service",
            "systemctl --user enable --now euthernet-refresh.timer",
            "curl -fsS -X POST http://127.0.0.1:8791/api/euthernet/refresh",
        ],
        "verify": [
            "systemctl --user is-active euthernet.service",
            "systemctl --user is-enabled euthernet-refresh.timer",
            "curl -fsS http://127.0.0.1:8791/api/euthernet/summary",
        ],
        "notes": [
            "EutherNet is restored first because later restore checks use its API.",
            "State snapshots are runtime evidence and are intentionally not committed.",
        ],
    },
    {
        "name": "EutherPunk",
        "repo_path": "/home/nichlas/EutherPunk",
        "profiles": ["full", "backup"],
        "systemd": ["eutherpunkd.service"],
        "ports": ["8787"],
        "packages": ["golang", "ca-certificates", "curl"],
        "persistent_paths": [
            "/home/nichlas/EutherPunk/var/chats",
            "/home/nichlas/EutherPunk/var/settings",
            "/home/nichlas/EutherPunk/var/images",
            "/home/nichlas/.config/eutherpunk/config.toml",
        ],
        "steps": [
            "cd /home/nichlas/EutherPunk",
            "scripts/build.sh",
            "mkdir -p /home/nichlas/EutherPunk/bin /home/nichlas/EutherPunk/dist/cli /home/nichlas/.config/eutherpunk /home/nichlas/.config/systemd/user",
            "cp dist/cli/eutherpunkd-linux-amd64 /home/nichlas/EutherPunk/bin/eutherpunkd",
            "cp dist/cli/eutherpunk-linux-amd64 /home/nichlas/EutherPunk/dist/cli/eutherpunk-linux-amd64",
            "chmod +x /home/nichlas/EutherPunk/bin/eutherpunkd /home/nichlas/EutherPunk/dist/cli/eutherpunk-linux-amd64",
            "cp deploy/eutherpunk.server.toml /home/nichlas/.config/eutherpunk/config.toml",
            "cp deploy/eutherpunkd.service /home/nichlas/.config/systemd/user/eutherpunkd.service",
            "systemctl --user daemon-reload",
            "systemctl --user enable --now eutherpunkd.service",
        ],
        "verify": [
            "systemctl --user is-active eutherpunkd.service",
            "curl -fsS http://127.0.0.1:8787/api/eutherpunk/status",
            "curl -fsS http://127.0.0.1:8787/api/eutherpunk/chat -H 'Content-Type: application/json' -d '{\"message\":\"/server summary\"}'",
        ],
        "notes": [
            "Keep EutherPunk local-first; do not add hosted model API keys.",
            "The configured model endpoint must be local Ollama unless config says otherwise.",
        ],
    },
    {
        "name": "EutherPal",
        "repo_path": "/home/nichlas/EutherPal",
        "profiles": ["full", "backup"],
        "systemd": ["eutherpal.service", "eutherpal-ollama-tunnel.service"],
        "ports": ["8793", "11434"],
        "packages": ["cargo", "ca-certificates", "curl", "git", "openssh-client", "systemd"],
        "persistent_paths": [
            "/home/nichlas/EutherPal/data",
            "/home/nichlas/EutherPal/config",
            "/home/nichlas/.config/systemd/user/eutherpal.service",
            "/home/nichlas/.config/systemd/user/eutherpal-ollama-tunnel.service",
        ],
        "steps": [
            "cd /home/nichlas/EutherPal",
            "cargo build --manifest-path server/Cargo.toml --release",
            "mkdir -p /home/nichlas/.config/systemd/user",
            "cp deploy/systemd/eutherpal.service /home/nichlas/.config/systemd/user/eutherpal.service",
            "cp deploy/systemd/eutherpal-ollama-tunnel.service /home/nichlas/.config/systemd/user/eutherpal-ollama-tunnel.service",
            "systemctl --user daemon-reload",
            "systemctl --user enable --now eutherpal.service",
            "systemctl --user enable --now eutherpal-ollama-tunnel.service",
        ],
        "verify": [
            "systemctl --user is-active eutherpal.service",
            "curl -fsS http://127.0.0.1:8793/health",
            "curl -fsS http://127.0.0.1:11434/api/tags",
        ],
        "notes": [
            "EutherPal is the Swedish Monopoly thin client/game server for TV, mobile, and admin bank flow.",
            "The bank LLM should use local Ollama through the reverse SSH tunnel on 127.0.0.1:11434; do not add hosted API keys.",
            "Port 8791 belongs to EutherNet and 8787 belongs to EutherPunk, so EutherPal uses 8793.",
        ],
    },
    {
        "name": "EutherOxide",
        "repo_path": "/home/nichlas/EutherOxide",
        "profiles": ["full"],
        "systemd": ["eutherhost.service", "caddy.service", "euther-srv-clean.timer"],
        "ports": ["32162", "80", "443"],
        "packages": ["cargo", "nodejs", "npm", "caddy", "python3"],
        "persistent_paths": [
            "/home/nichlas/EutherOxide/.euther-host",
            "/home/nichlas/EutherOxide/.euther-host/eutherium/ledger.json",
            "/home/nichlas/EutherOxide/.euther-host/eutherium/inventory.json",
            "/home/nichlas/EutherOxide/.euther-host/eutherium/jox-shop.json",
            "/home/nichlas/EutherOxide/.euther-host/eutherium/jox-offers.json",
            "/home/nichlas/EutherOxide/.euther-host/eutherium/joxbox",
            "/home/nichlas/EutherOxide/.euther-host/users/*/eutherium/trophy-room.json",
            "/home/nichlas/EutherOxide/.euther-bridge",
            "/home/nichlas/roms",
            "/srv",
        ],
        "steps": [
            "cd /home/nichlas/EutherOxide",
            "scripts/build-release.sh",
            "sudo cp deploy/eutherhost.service.example /etc/systemd/system/eutherhost.service",
            "sudo cp deploy/Caddyfile /etc/caddy/Caddyfile",
            "sudo cp deploy/euther-srv-clean.service /etc/systemd/system/euther-srv-clean.service",
            "sudo cp deploy/euther-srv-clean.timer /etc/systemd/system/euther-srv-clean.timer",
            "sudo systemctl daemon-reload",
            "sudo systemctl enable --now eutherhost.service",
            "sudo systemctl enable --now caddy.service",
            "sudo systemctl enable --now euther-srv-clean.timer",
        ],
        "verify": [
            "sudo systemctl is-active eutherhost.service",
            "sudo systemctl is-active caddy.service",
            "sudo systemctl is-enabled euther-srv-clean.timer",
            "curl -fsS http://127.0.0.1:32162/health || curl -fsS http://127.0.0.1:32162",
            "test -d /home/nichlas/EutherOxide/.euther-host/eutherium",
            "test -d /home/nichlas/EutherOxide/.euther-host/eutherium/joxbox",
        ],
        "notes": [
            "Review deploy/Caddyfile domain spelling before enabling public traffic.",
            "Restore private data under /srv and /home/nichlas/roms from backups, not from git.",
            "Eutherium/Joxbox state is part of EutherOxide host state: ledger, inventory, JOX shop, offers, self-contained .jox artifacts, embedded assets, and trophy-room layouts.",
            "A .jox may be socially tradeable with unknown provenance, but EUX sale value depends on valid payload/assets hashes and an intact ownership/mutation chain.",
        ],
    },
    {
        "name": "EutherBooks",
        "repo_path": "/home/nichlas/EutherBooks",
        "profiles": ["full", "backup"],
        "systemd": ["eutherbooks.service", "eutherbooks-clean.timer"],
        "ports": ["8088"],
        "packages": ["python3", "python3-venv", "python3-pip", "curl"],
        "persistent_paths": [
            "/home/nichlas/EutherBooks/library",
            "/home/nichlas/EutherBooks/data",
            "/srv/eutherbooks/audio",
            "/home/nichlas/EutherBooks/models",
            "/home/nichlas/EutherBooks/tools",
        ],
        "steps": [
            "cd /home/nichlas/EutherBooks",
            "python3 -m venv .venv",
            ". .venv/bin/activate && pip install -e '.[dev]'",
            "scripts/download_piper_assets.sh",
            "sudo mkdir -p /srv/eutherbooks/audio",
            "sudo chown -R nichlas:nichlas /srv/eutherbooks",
            "sudo cp deploy/systemd/eutherbooks.service /etc/systemd/system/eutherbooks.service",
            "sudo cp deploy/systemd/eutherbooks-clean.service /etc/systemd/system/eutherbooks-clean.service",
            "sudo cp deploy/systemd/eutherbooks-clean.timer /etc/systemd/system/eutherbooks-clean.timer",
            "sudo systemctl daemon-reload",
            "sudo systemctl enable --now eutherbooks.service",
            "sudo systemctl enable --now eutherbooks-clean.timer",
        ],
        "verify": [
            "sudo systemctl is-active eutherbooks.service",
            "sudo systemctl is-enabled eutherbooks-clean.timer",
            "curl -fsS http://127.0.0.1:8088/health",
        ],
        "notes": [
            "Restore library and generated audio from backup storage before expecting existing books/audio.",
            "Piper assets are local runtime artifacts and may need a network download or restored cache.",
        ],
    },
    {
        "name": "EutherSight",
        "repo_path": "/home/nichlas/EutherSight",
        "profiles": ["full", "backup"],
        "systemd": [
            "docker.service",
            "euthersight-ai-worker.service",
            "eutherbird-producer.service",
            "euthersight-tunnel.service",
            "euthersight-ai-tunnel.service",
        ],
        "ports": ["5000", "7777", "8554", "8555"],
        "packages": ["docker", "docker-compose", "ffmpeg", "python3", "python3-pillow", "curl"],
        "persistent_paths": [
            "/home/nichlas/EutherSight/.env",
            "/home/nichlas/EutherSight/config",
            "/home/nichlas/EutherSight/secondsight.toml",
            "/home/nichlas/EutherSight/.euthersight-ai",
            "/home/nichlas/ai/eutherbird",
            "/run/media/nichlas/Titan/Camera_feed",
            "/run/media/nichlas/Titan/Camera_feed/eutherbird",
            "/run/media/nichlas/Titan/SecondSight",
        ],
        "steps": [
            "cd /home/nichlas/EutherSight",
            "python -m py_compile scripts/euthersight-ai-worker.py scripts/eutherbird-producer.py",
            "sudo systemctl daemon-reload",
            "sudo systemctl enable --now docker.service",
            "sudo systemctl enable --now euthersight-ai-worker.service",
            "sudo systemctl enable --now eutherbird-producer.service",
            "sudo systemctl enable --now euthersight-tunnel.service",
            "sudo systemctl enable --now euthersight-ai-tunnel.service",
        ],
        "verify": [
            "sudo systemctl is-active euthersight-ai-worker.service",
            "sudo systemctl is-active eutherbird-producer.service",
            "curl -fsS http://127.0.0.1:7777/health",
            "curl -fsS 'http://127.0.0.1:7777/api/eutherbird/observations?limit=5&min_confidence=0.15'",
            "test -d /run/media/nichlas/Titan/Camera_feed/eutherbird/observations",
            "test -d /run/media/nichlas/Titan/SecondSight/jox",
        ],
        "notes": [
            "EutherSight owns Frigate camera state, SecondSight artifacts, and the EutherBird audio observation layer.",
            "SecondSight .jox files and /run/media/nichlas/Titan/SecondSight are provenance-bearing state; restore them from backup before validating JOX history.",
            "Bird audio segments are append-only observation evidence. They can be large, but they are the trace that makes BirdNET observations replayable.",
            "The BirdNET model environment under /home/nichlas/ai/eutherbird is rebuildable if documented dependencies and cached models are available.",
            "The EutherHost UI proxies EutherSight routes, but EutherSight recovery should first verify the local worker on 127.0.0.1:7777.",
        ],
    },
]


KNOWN_RESTORE_REPOS: list[dict[str, str | list[str]]] = [
    {
        "path": "/home/nichlas/EutherNet",
        "remote": "https://github.com/NichlasEk/EutherNet",
        "branch": "main",
        "profiles": ["full", "backup"],
    },
    {
        "path": "/home/nichlas/EutherPunk",
        "remote": "https://github.com/NichlasEk/EutherPunk",
        "branch": "main",
        "profiles": ["full", "backup"],
    },
    {
        "path": "/home/nichlas/EutherPal",
        "remote": "https://github.com/NichlasEk/EutherPal",
        "branch": "main",
        "profiles": ["full", "backup"],
    },
    {
        "path": "/home/nichlas/EutherOxide",
        "remote": "https://github.com/NichlasEk/EutherOxide",
        "branch": "main",
        "profiles": ["full", "backup"],
    },
    {
        "path": "/home/nichlas/EutherBooks",
        "remote": "https://github.com/NichlasEk/EutherBooks",
        "branch": "main",
        "profiles": ["full", "backup"],
    },
    {
        "path": "/home/nichlas/EutherSight",
        "remote": "https://github.com/NichlasEk/EutherSight",
        "branch": "main",
        "profiles": ["full", "backup"],
    },
]


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


def command_summary(config: dict[str, Any]) -> int:
    summary = operational_summary(config)
    if not summary.get("ok"):
        print(summary.get("error", "summary unavailable"))
        return 1
    print(summary["summary"])
    return 0


def command_changes(config: dict[str, Any]) -> int:
    changes = drift_changes(config)
    if not changes.get("ok"):
        print(changes.get("error", "changes unavailable"))
        return 1
    print(changes["changes"])
    return 0


def command_restore_plan(config: dict[str, Any]) -> int:
    plan = restore_plan(config)
    if not plan.get("ok"):
        print(plan.get("error", "restore plan unavailable"))
        return 1
    print(plan["plan"])
    return 0


def command_restore_bundle(config: dict[str, Any], profile: str) -> int:
    bundle = restore_bundle(config, profile=profile or "full")
    if not bundle.get("ok"):
        print(bundle.get("error", "restore bundle unavailable"))
        return 1
    print(bundle["runbook"])
    print("")
    print("## Bootstrap Script")
    print("")
    print("```sh")
    print(bundle["bootstrap_script"].rstrip())
    print("```")
    print("")
    print("## Codex Prompt")
    print("")
    print(bundle["codex_prompt"])
    return 0


def command_backup_manifest(config: dict[str, Any]) -> int:
    manifest = backup_manifest(config)
    if not manifest.get("ok"):
        print(manifest.get("error", "backup manifest unavailable"))
        return 1
    print(manifest["manifest_toml"])
    return 0


def command_restore_drill(config: dict[str, Any]) -> int:
    drill = restore_drill(config)
    if not drill.get("ok"):
        print(drill.get("error", "restore drill unavailable"))
        return 1
    print(drill["drill_toml"])
    return 0


def command_server_map(config: dict[str, Any]) -> int:
    server_map = eutherverse_map(config)
    if not server_map.get("ok"):
        print(server_map.get("error", "server map unavailable"))
        return 1
    print(server_map["map_toml"])
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


def first_line(value: str) -> str:
    return value.splitlines()[0] if value else ""


def fenced_block(value: str, limit: int = 12000) -> str:
    text = value.strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n... [truncated]"
    return "```text\n" + text + "\n```"


def full_report(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first", "report": ""}

    server = snapshot["server"]
    collectors = snapshot.get("collectors", {})
    repos = parse_repos(snapshot)
    dirty_repos = [repo for repo in repos if int(repo.get("dirty_lines") or "0") > 0]

    system = collectors.get("system", {})
    systemd = collectors.get("systemd", {})
    network = collectors.get("network", {})

    lines = [
        f"# EutherNet Full Report: {server.get('name', 'server')}",
        "",
        f"- Collected at: `{snapshot.get('collected_at', '')}`",
        f"- LAN host: `{server.get('lan_host', '')}`",
        f"- Public host: `{server.get('public_host', '')}`",
        f"- Command transport: `{server.get('command_transport', 'ssh')}`",
        f"- Preflight: `{'ok' if snapshot.get('ssh_preflight', {}).get('ok') else 'failed'}`",
        "",
        "## Collector Status",
        "",
    ]

    for name, group in collectors.items():
        if name == "git_repositories":
            scan = group.get("scan", {})
            lines.append(f"- `{name}`: `{'ok' if scan.get('ok') else 'failed'}` ({len(repos)} repos)")
        elif "preflight" in group:
            lines.append(f"- `{name}`: `skipped`")
        else:
            total = len(group)
            ok = sum(1 for value in group.values() if value.get("ok"))
            lines.append(f"- `{name}`: `{ok}/{total}` commands ok")

    lines.extend(["", "## System", ""])
    system_fields = [
        ("Hostname", first_line(system.get("hostname", {}).get("stdout", ""))),
        ("Date", first_line(system.get("date", {}).get("stdout", ""))),
        ("Kernel", first_line(system.get("uname", {}).get("stdout", ""))),
        ("Uptime", first_line(system.get("uptime", {}).get("stdout", ""))),
    ]
    for label, value in system_fields:
        if value:
            lines.append(f"- {label}: `{value}`")

    memory = system.get("memory", {}).get("stdout", "")
    disk = system.get("disk", {}).get("stdout", "")
    if memory:
        lines.extend(["", "### Memory", "", fenced_block(memory)])
    if disk:
        lines.extend(["", "### Disk", "", fenced_block(disk)])

    addresses = network.get("addresses", {}).get("stdout", "")
    listening = network.get("listening_tcp_udp", {}).get("stdout", "")
    lines.extend(["", "## Network", ""])
    if addresses:
        lines.append(f"- Addresses: `{first_line(addresses)}`")
    if listening:
        lines.extend(["", "### Listening Ports", "", fenced_block(listening)])

    failed = systemd.get("failed_services", {}).get("stdout", "")
    running = systemd.get("running_services", {}).get("stdout", "")
    timers = systemd.get("timers", {}).get("stdout", "")
    lines.extend(["", "## systemd", ""])
    if failed:
        lines.extend(["### Failed Services", "", fenced_block(failed)])
    if running:
        lines.extend(["", "### Running Services", "", fenced_block(running)])
    if timers:
        lines.extend(["", "### Timers", "", fenced_block(timers)])

    lines.extend(["", "## Git Repositories", ""])
    lines.append(f"- Total: `{len(repos)}`")
    lines.append(f"- Dirty: `{len(dirty_repos)}`")
    if repos:
        lines.extend(["", "| Repo | Branch | Head | Dirty | Remote |", "| --- | --- | --- | --- | --- |"])
        for repo in repos:
            lines.append(
                "| "
                + " | ".join(
                    [
                        repo.get("path", ""),
                        repo.get("branch", "") or "detached/unknown",
                        repo.get("head", ""),
                        repo.get("dirty_lines", "0"),
                        repo.get("remote", ""),
                    ]
                )
                + " |"
            )

    return {
        "ok": True,
        "collected_at": snapshot.get("collected_at", ""),
        "repository_count": len(repos),
        "dirty_repository_count": len(dirty_repos),
        "report": "\n".join(lines).rstrip() + "\n",
    }


def operational_summary(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first", "summary": ""}

    summary = snapshot_summary(snapshot)
    lines = [
        f"EutherNet summary for {summary['server'].get('name', 'server')}",
        f"- Snapshot: {summary['collected_at']}",
        f"- Preflight: {'ok' if summary['ssh_preflight'] else 'failed'}",
        f"- Repositories: {summary['repository_count']} total, {summary['dirty_repository_count']} dirty",
        f"- Observed packages: {summary['package_count']}",
        f"- Failed services: {summary['failed_service_count']}",
        f"- Disk warnings >=85%: {summary['disk_warning_count']}",
        f"- Listening TCP ports: {summary['listening_port_count']}",
    ]
    if summary["dirty_repositories"]:
        lines.append("")
        lines.append("Dirty repositories:")
        for repo in summary["dirty_repositories"]:
            lines.append(f"- {repo['path']} ({repo.get('dirty_lines', '0')} status rows)")
    if summary["failed_services"]:
        lines.append("")
        lines.append("Failed services:")
        lines.extend(f"- {line}" for line in summary["failed_services"])
    if summary["disk_warnings"]:
        lines.append("")
        lines.append("Disk warnings:")
        for warning in summary["disk_warnings"]:
            lines.append(
                f"- {warning['mount']}: {warning['used_percent']}% used "
                f"({warning['used']} / {warning['size']})"
            )

    return {"ok": True, **summary, "summary": "\n".join(lines)}


def drift_changes(config: dict[str, Any]) -> dict[str, Any]:
    state_root = pathlib.Path(config["server"].get("state_root", "state"))
    snapshots = recent_healthy_snapshots(state_root, count=2)
    if not snapshots:
        return {"ok": False, "error": "no snapshot exists; run refresh first", "changes": ""}
    if len(snapshots) == 1:
        summary = snapshot_summary(snapshots[0])
        return {
            "ok": True,
            "baseline_only": True,
            "from": "",
            "to": summary["collected_at"],
            "changes": "Only one snapshot exists. Run another refresh to detect drift.",
            "items": [],
        }

    previous, current = snapshots
    previous_repos = {repo_key(repo): repo for repo in parse_repos(previous)}
    current_repos = {repo_key(repo): repo for repo in parse_repos(current)}

    items: list[dict[str, str]] = []
    for path in sorted(set(current_repos) - set(previous_repos)):
        items.append({"type": "repo_added", "message": path})
    for path in sorted(set(previous_repos) - set(current_repos)):
        items.append({"type": "repo_removed", "message": path})
    for path in sorted(set(previous_repos) & set(current_repos)):
        old = previous_repos[path]
        new = current_repos[path]
        if old.get("head") != new.get("head"):
            items.append(
                {
                    "type": "repo_head_changed",
                    "message": f"{path}: {old.get('head', '')} -> {new.get('head', '')}",
                }
            )
        if old.get("dirty_lines", "0") != new.get("dirty_lines", "0"):
            items.append(
                {
                    "type": "repo_dirty_changed",
                    "message": f"{path}: {old.get('dirty_lines', '0')} -> {new.get('dirty_lines', '0')} status rows",
                }
            )

    old_failed = set(failed_service_lines(systemd_stdout(previous, "failed_services")))
    new_failed = set(failed_service_lines(systemd_stdout(current, "failed_services")))
    for line in sorted(new_failed - old_failed):
        items.append({"type": "failed_service_added", "message": line})
    for line in sorted(old_failed - new_failed):
        items.append({"type": "failed_service_cleared", "message": line})

    old_ports = parse_listening_ports(previous.get("collectors", {}).get("network", {}).get("listening_tcp_udp", {}).get("stdout", ""))
    new_ports = parse_listening_ports(current.get("collectors", {}).get("network", {}).get("listening_tcp_udp", {}).get("stdout", ""))
    for port in sorted(new_ports - old_ports, key=port_sort_key):
        items.append({"type": "port_added", "message": port})
    for port in sorted(old_ports - new_ports, key=port_sort_key):
        items.append({"type": "port_removed", "message": port})

    lines = [
        f"EutherNet changes: {previous.get('collected_at', '')} -> {current.get('collected_at', '')}",
        "",
    ]
    if items:
        lines.extend(f"- {item['type']}: {item['message']}" for item in items)
    else:
        lines.append("- No tracked drift detected.")

    return {
        "ok": True,
        "baseline_only": False,
        "from": previous.get("collected_at", ""),
        "to": current.get("collected_at", ""),
        "items": items,
        "changes": "\n".join(lines),
    }


def restore_plan(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first", "plan": ""}

    summary = snapshot_summary(snapshot)
    repos = parse_repos(snapshot)
    running = snapshot.get("collectors", {}).get("systemd", {}).get("running_services", {}).get("stdout", "")
    timers = snapshot.get("collectors", {}).get("systemd", {}).get("timers", {}).get("stdout", "")
    listening = snapshot.get("collectors", {}).get("network", {}).get("listening_tcp_udp", {}).get("stdout", "")

    lines = [
        f"# Restore Plan: {summary['server'].get('name', 'server')}",
        "",
        f"- Source snapshot: `{summary['collected_at']}`",
        f"- LAN host: `{summary['server'].get('lan_host', '')}`",
        f"- Public host: `{summary['server'].get('public_host', '')}`",
        "",
        "## 1. Base System",
        "",
        "- Install a fresh OS with SSH access for user `nichlas`.",
        "- Restore SSH public key authentication before disabling password login.",
        "- Install required base packages: `git`, `curl`, `python3`, `systemd`, and service-specific runtimes.",
        "- Recreate persistent mount points and verify disk capacity before restoring data.",
        "",
        "## 2. Repositories",
        "",
    ]
    for repo in repos:
        remote = repo.get("remote", "")
        path = repo.get("path", "")
        branch = repo.get("branch", "")
        if remote:
            checkout = f"git clone {remote} {path}"
            if branch:
                checkout += f" && git -C {path} checkout {branch}"
        else:
            checkout = f"restore local-only repository/path: {path}"
        dirty = " (has local changes in snapshot)" if int(repo.get("dirty_lines") or "0") > 0 else ""
        lines.append(f"- `{path}`{dirty}: `{checkout}`")

    lines.extend(["", "## 3. Services", ""])
    important_services = [
        line.split()[0]
        for line in running.splitlines()
        if any(token in line.lower() for token in ["euther", "caddy", "ssh", "ollama"])
    ]
    if important_services:
        for service in important_services:
            lines.append(f"- Recreate and enable `{service}`.")
    else:
        lines.append("- Recreate service units from repo/deploy folders and enable them with systemd.")

    lines.extend(["", "## 4. Timers", ""])
    important_timers = []
    for line in timers.splitlines()[1:]:
        if "euther" not in line.lower():
            continue
        for part in line.split():
            if part.endswith(".timer"):
                important_timers.append(part)
                break
    if important_timers:
        for timer in important_timers:
            lines.append(f"- Recreate and enable `{timer}`.")
    else:
        lines.append("- Recreate relevant cleanup, backup, and inventory timers.")

    lines.extend(["", "## 5. Network", ""])
    ports = sorted(parse_listening_ports(listening), key=port_sort_key)
    if ports:
        lines.append("- Verify these listening TCP ports after restore: `" + ", ".join(ports) + "`.")
    lines.append("- Restore reverse proxy routes for EutherOxide, EutherPunk, EutherBooks, and EutherNet local-only access.")

    lines.extend(["", "## 6. Verification", ""])
    lines.extend(
        [
            "- `systemctl --user status euthernet.service eutherpunkd.service`",
            "- `curl -fsS http://127.0.0.1:8791/api/euthernet/status`",
            "- `curl -fsS http://127.0.0.1:8787/api/eutherpunk/status`",
            "- From chat: `/server status`, `/server changes`, `/server full report`.",
        ]
    )

    return {"ok": True, "collected_at": summary["collected_at"], "plan": "\n".join(lines) + "\n"}


def restore_profile_repos(repos: list[dict[str, str]], profile: str) -> list[dict[str, str]]:
    if profile == "backup":
        preferred = ("EutherNet", "EutherPunk", "EutherPal", "EutherOxide", "EutherBooks", "EutherMaster")
        return [
            repo for repo in repos
            if repo.get("remote") and any(part in repo.get("path", "") for part in preferred)
        ]
    return [repo for repo in repos if repo.get("remote")]


def with_known_restore_repos(repos: list[dict[str, str]], profile: str) -> list[dict[str, str]]:
    output = list(repos)
    seen = {repo.get("path", "") for repo in output}
    for known in KNOWN_RESTORE_REPOS:
        profiles = known.get("profiles", [])
        if not isinstance(profiles, list) or profile not in profiles:
            continue
        path = str(known["path"])
        if path in seen:
            continue
        output.append(
            {
                "path": path,
                "remote": str(known["remote"]),
                "branch": str(known["branch"]),
                "head": "known-restore-repo",
                "dirty_lines": "0",
            }
        )
        seen.add(path)
    return output


def service_restore_plan(repos: list[dict[str, str]], profile: str) -> list[dict[str, Any]]:
    repo_paths = {repo.get("path", "") for repo in repos}
    services: list[dict[str, Any]] = []
    for service in SERVICE_RESTORE_CATALOG:
        if profile not in service.get("profiles", []):
            continue
        repo_path = service["repo_path"]
        if repo_path not in repo_paths:
            continue
        services.append(service)
    return services


def service_package_names(services: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for service in services:
        names.update(service.get("packages", []))
    return sorted(names)


def service_restore_lines(services: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if not services:
        return ["No known services matched repositories in this restore profile."]
    for index, service in enumerate(services, start=1):
        lines.extend(
            [
                f"### {index}. {service['name']}",
                "",
                f"- Repository: `{service['repo_path']}`",
                "- Systemd units: `" + ", ".join(service.get("systemd", [])) + "`",
                "- Expected local ports: `" + ", ".join(service.get("ports", [])) + "`",
                "- Service packages: `" + ", ".join(service.get("packages", [])) + "`",
                "",
                "Persistent paths to restore or verify:",
            ]
        )
        lines.extend(f"- `{path}`" for path in service.get("persistent_paths", []))
        lines.extend(["", "Run in order:", "", "```sh"])
        lines.extend(service.get("steps", []))
        lines.extend(["```", "", "Verify:", "", "```sh"])
        lines.extend(service.get("verify", []))
        lines.extend(["```"])
        notes = service.get("notes", [])
        if notes:
            lines.extend(["", "Notes:"])
            lines.extend(f"- {note}" for note in notes)
        lines.append("")
    return lines


def restore_bundle(config: dict[str, Any], profile: str = "full") -> dict[str, Any]:
    profile = (profile or "full").strip().lower()
    if profile not in {"full", "backup"}:
        return {"ok": False, "error": "profile must be one of: full, backup"}

    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first"}

    summary = snapshot_summary(snapshot)
    repos = with_known_restore_repos(restore_profile_repos(parse_repos(snapshot), profile), profile)
    services = service_restore_plan(repos, profile)
    server = summary["server"]
    package_text = (
        snapshot.get("collectors", {})
        .get("system", {})
        .get("packages", {})
        .get("stdout", "")
    )
    observed_packages = parse_packages(package_text)
    observed_package_names = sorted(
        {
            package["name"]
            for package in observed_packages
            if package.get("name") and ":" not in package["name"]
        }
    )
    repo_commands = []
    for repo in repos:
        path = repo.get("path", "")
        if path == "/home/nichlas/EutherNet":
            continue
        remote = repo.get("remote", "")
        branch = repo.get("branch", "")
        command = f'clone_or_update {shell_quote(remote)} {shell_quote(path)}'
        if branch:
            command += f" {shell_quote(branch)}"
        repo_commands.append(command)

    base_packages = "ca-certificates curl git python3 systemd"
    if profile == "full":
        base_packages += " caddy"
    base_package_names = base_packages.split()
    service_packages = service_package_names(services)

    bootstrap_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# EutherNet generated Debian bootstrap.",
        "# Review before running. It is intentionally conservative and repo-first.",
        "",
        "need_cmd() { command -v \"$1\" >/dev/null 2>&1 || { echo \"missing command: $1\" >&2; exit 1; }; }",
        "clone_or_update() {",
        "  remote=\"$1\"",
        "  path=\"$2\"",
        "  branch=\"${3:-}\"",
        "  mkdir -p \"$(dirname \"$path\")\"",
        "  if [ -d \"$path/.git\" ]; then",
        "    git -C \"$path\" fetch --all --prune",
        "    git -C \"$path\" pull --ff-only || true",
        "  else",
        "    git clone \"$remote\" \"$path\"",
        "  fi",
        "  if [ -n \"$branch\" ]; then git -C \"$path\" checkout \"$branch\"; fi",
        "}",
        "",
        "if [ \"$(id -u)\" -eq 0 ]; then",
        "  echo \"Run as the target user with sudo available, not as root.\" >&2",
        "  exit 1",
        "fi",
        "",
        "need_cmd sudo",
        "sudo apt-get update",
        f"sudo apt-get install -y {base_packages}",
        "",
        "clone_or_update https://github.com/NichlasEk/EutherNet /home/nichlas/EutherNet main",
    ]
    bootstrap_lines.extend(repo_commands)
    bootstrap_lines.extend(
        [
            "",
            "mkdir -p /home/nichlas/.config/systemd/user",
            "cp /home/nichlas/EutherNet/deploy/euthernet.service /home/nichlas/.config/systemd/user/euthernet.service",
            "cp /home/nichlas/EutherNet/deploy/euthernet-refresh.service /home/nichlas/.config/systemd/user/euthernet-refresh.service",
            "cp /home/nichlas/EutherNet/deploy/euthernet-refresh.timer /home/nichlas/.config/systemd/user/euthernet-refresh.timer",
            "systemctl --user daemon-reload",
            "systemctl --user enable --now euthernet.service",
            "systemctl --user enable --now euthernet-refresh.timer",
            "curl -fsS -X POST http://127.0.0.1:8791/api/euthernet/refresh",
            "curl -fsS http://127.0.0.1:8791/api/euthernet/summary",
        ]
    )

    runbook_lines = [
        f"# EutherNet Codex Restore Bundle ({profile})",
        "",
        f"- Generated from snapshot: `{summary['collected_at']}`",
        f"- Server name: `{server.get('name', '')}`",
        f"- LAN host: `{server.get('lan_host', '')}`",
        f"- Public host: `{server.get('public_host', '')}`",
        "",
        "## Intended Fresh-Hardware Flow",
        "",
        "1. Install Debian on the new hardware.",
        "2. Create/restore user `nichlas` with sudo access.",
        "3. Restore SSH pubkey access.",
        "4. Clone EutherNet:",
        "",
        "```sh",
        "git clone https://github.com/NichlasEk/EutherNet /home/nichlas/EutherNet",
        "cd /home/nichlas/EutherNet",
        "```",
        "",
        "5. Start Codex in this repo and give it the Codex prompt below.",
        "6. Let Codex execute the bootstrap script step by step, validating after each phase.",
        "",
        "## Profile Meaning",
        "",
    ]
    if profile == "full":
        runbook_lines.extend(
            [
                "`full` restores the server control plane and clones all remote-backed repositories in the latest inventory.",
                "It prepares EutherNet first, then lets Codex continue service-specific recovery from repo deploy docs.",
            ]
        )
    else:
        runbook_lines.extend(
            [
                "`backup` restores the minimum control plane and key Euther repos for diagnostics/backups.",
                "It deliberately avoids enabling application services beyond EutherNet.",
            ]
        )

    runbook_lines.extend(
        [
            "",
            "## Deterministic Order",
            "",
            "1. Base OS packages.",
            "2. Compare observed package inventory against the fresh host.",
            "3. EutherNet repo and service.",
            "4. Inventory refresh.",
            "5. Remote-backed repo clone/update.",
            "6. Service-aware restore steps in the order below.",
            "7. Verification through EutherNet summary, changes, and restore-plan.",
            "",
            "## Package Inventory",
            "",
            f"- Bootstrap base packages: `{', '.join(base_package_names)}`",
            f"- Service package candidates: `{', '.join(service_packages)}`",
            f"- Observed installed packages in latest snapshot: `{len(observed_packages)}`",
            "",
            "Treat the observed package list as a comparison target, not a blind install list.",
            "Install service-specific packages when a repo deploy doc or failed verification gate proves they are needed.",
        ]
    )
    if observed_package_names:
        runbook_lines.extend(
            [
                "Top observed package names:",
                "",
                "```text",
                "\n".join(observed_package_names[:160]),
                "```",
                "",
            ]
        )
    runbook_lines.extend(["", "## Repositories In Scope", ""])
    for repo in repos:
        dirty = " dirty" if int(repo.get("dirty_lines") or "0") > 0 else ""
        runbook_lines.append(
            f"- `{repo.get('path')}` branch=`{repo.get('branch') or 'detached/unknown'}` "
            f"head=`{repo.get('head')}`{dirty} remote=`{repo.get('remote')}`"
        )

    runbook_lines.extend(["", "## Service Restore Steps", ""])
    runbook_lines.extend(service_restore_lines(services))

    runbook_lines.extend(
        [
            "",
            "## Verification Gates",
            "",
            "- `systemctl --user status euthernet.service euthernet-refresh.timer`",
            "- `curl -fsS http://127.0.0.1:8791/api/euthernet/summary`",
            "- `curl -fsS http://127.0.0.1:8791/api/euthernet/changes`",
            "- `curl -fsS http://127.0.0.1:8791/api/euthernet/restore-bundle?profile=" + profile + "`",
        ]
    )

    codex_prompt = "\n".join(
        [
            "Tja! This is a fresh Debian restore for EutherNet/EutherOxide.",
            f"Use the `{profile}` restore profile from EutherNet.",
            "Do not invent service order. Follow the generated runbook chronologically.",
            "Keep all actions local and deterministic. Do not use hosted API keys.",
            "Start by reading docs/RUNBOOK.md and the restore bundle.",
            "Run the bootstrap script step by step, inspect errors, and verify each gate before continuing.",
            "If secrets, private keys, or backups are needed, stop and ask me for the specific missing item.",
        ]
    )

    return {
        "ok": True,
        "profile": profile,
        "collected_at": summary["collected_at"],
        "manifest": {
            "server": server,
            "repositories": repos,
            "services": services,
            "base_packages": base_package_names,
            "service_packages": service_packages,
            "observed_packages": observed_packages,
            "verification": [
                "euthernet.service active",
                "euthernet-refresh.timer enabled",
                "EutherNet summary endpoint returns ok",
            ],
        },
        "runbook": "\n".join(runbook_lines) + "\n",
        "bootstrap_script": "\n".join(bootstrap_lines) + "\n",
        "codex_prompt": codex_prompt,
    }


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def current_restore_services(snapshot: dict[str, Any], profile: str = "full") -> list[dict[str, Any]]:
    repos = with_known_restore_repos(restore_profile_repos(parse_repos(snapshot), profile), profile)
    return service_restore_plan(repos, profile)


def backup_item_for_path(service: dict[str, Any], path: str) -> dict[str, str]:
    lower = path.lower()
    if any(token in lower for token in ["jox", "eutherium", "trophy-room", "ledger.json", "inventory.json", "jox-shop.json", "jox-offers.json"]):
        category = "critical"
        sensitivity = "private"
        action = "restore from backup before Eutherium/Joxbox verification; do not regenerate provenance data"
    elif any(token in lower for token in ["state", "cache", "tools", "models", ".venv"]):
        category = "rebuildable"
        sensitivity = "low"
        action = "restore from cache if available, otherwise regenerate"
    elif any(token in lower for token in ["roms", "library", "audio", "data", "chats", "settings", "images", ".config", ".euther-host", ".euther-bridge", "/srv"]):
        category = "critical"
        sensitivity = "private"
        action = "restore from backup before service verification"
    else:
        category = "review"
        sensitivity = "unknown"
        action = "inspect during restore drill"
    return {
        "service": service["name"],
        "path": path,
        "category": category,
        "sensitivity": sensitivity,
        "action": action,
    }


def toml_string(value: str) -> str:
    return json.dumps(value)


def toml_array(values: list[str]) -> str:
    return "[" + ", ".join(toml_string(value) for value in values) + "]"


def backup_manifest_toml(snapshot: dict[str, Any], items: list[dict[str, str]], critical_count: int, rebuildable_count: int) -> str:
    lines = [
        "[manifest]",
        f"collected_at = {toml_string(snapshot.get('collected_at', ''))}",
        f"server = {toml_string(snapshot.get('server', {}).get('name', ''))}",
        f"critical_count = {critical_count}",
        f"rebuildable_count = {rebuildable_count}",
        "",
    ]
    for item in items:
        lines.extend(
            [
                "[[path]]",
                f"service = {toml_string(item['service'])}",
                f"path = {toml_string(item['path'])}",
                f"category = {toml_string(item['category'])}",
                f"sensitivity = {toml_string(item['sensitivity'])}",
                f"restore_action = {toml_string(item['action'])}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def backup_manifest(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first"}

    services = current_restore_services(snapshot, "full")
    items = [
        backup_item_for_path(service, path)
        for service in services
        for path in service.get("persistent_paths", [])
    ]
    critical_count = sum(1 for item in items if item["category"] == "critical")
    rebuildable_count = sum(1 for item in items if item["category"] == "rebuildable")
    lines = [
        "# EutherNet Backup Manifest",
        "",
        f"- Snapshot: `{snapshot.get('collected_at', '')}`",
        f"- Services: `{len(services)}`",
        f"- Critical paths: `{critical_count}`",
        f"- Rebuildable/cache paths: `{rebuildable_count}`",
        "",
        "| Service | Path | Category | Sensitivity | Restore action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in items:
        lines.append(
            f"| {item['service']} | `{item['path']}` | {item['category']} | "
            f"{item['sensitivity']} | {item['action']} |"
        )
    manifest_toml = backup_manifest_toml(snapshot, items, critical_count, rebuildable_count)
    return {
        "ok": True,
        "collected_at": snapshot.get("collected_at", ""),
        "services": services,
        "items": items,
        "critical_count": critical_count,
        "rebuildable_count": rebuildable_count,
        "manifest_toml": manifest_toml,
        "manifest_md": "\n".join(lines) + "\n",
    }


def service_drill_result(service: dict[str, Any], repos: list[dict[str, str]], ports: set[str], failed_services: list[str]) -> dict[str, Any]:
    repo_paths = {repo.get("path", ""): repo for repo in repos}
    checks: list[dict[str, str]] = []
    repo = repo_paths.get(service["repo_path"])
    if repo:
        if int(repo.get("dirty_lines") or "0") > 0:
            checks.append({"status": "warn", "check": "repo", "detail": f"{repo['path']} has {repo.get('dirty_lines')} dirty status rows"})
        else:
            checks.append({"status": "pass", "check": "repo", "detail": f"{repo['path']} is tracked"})
    else:
        checks.append({"status": "warn", "check": "repo", "detail": f"{service['repo_path']} is known but not present in git inventory"})

    failed_text = "\n".join(failed_services).lower()
    for unit in service.get("systemd", []):
        if unit.lower() in failed_text:
            checks.append({"status": "fail", "check": "systemd", "detail": f"{unit} appears failed"})
        else:
            checks.append({"status": "pass", "check": "systemd", "detail": f"{unit} is not in failed-service list"})

    for port in service.get("ports", []):
        if port in ports:
            checks.append({"status": "pass", "check": "port", "detail": f"port {port} is listening"})
        else:
            checks.append({"status": "warn", "check": "port", "detail": f"port {port} is not listening in latest snapshot"})

    for path in service.get("persistent_paths", []):
        item = backup_item_for_path(service, path)
        if item["category"] == "critical":
            checks.append({"status": "warn", "check": "backup", "detail": f"critical backup path must be verified: {path}"})
        else:
            checks.append({"status": "pass", "check": "backup", "detail": f"rebuildable path documented: {path}"})

    fails = sum(1 for check in checks if check["status"] == "fail")
    warns = sum(1 for check in checks if check["status"] == "warn")
    if fails:
        status = "red"
    elif warns:
        status = "yellow"
    else:
        status = "green"
    score = max(0, 100 - fails * 35 - warns * 8)
    return {"service": service["name"], "status": status, "score": score, "checks": checks}


def restore_drill_toml(snapshot: dict[str, Any], overall_status: str, overall_score: int, results: list[dict[str, Any]]) -> str:
    lines = [
        "[drill]",
        f"collected_at = {toml_string(snapshot.get('collected_at', ''))}",
        f"server = {toml_string(snapshot.get('server', {}).get('name', ''))}",
        f"overall_status = {toml_string(overall_status)}",
        f"overall_score = {overall_score}",
        "",
    ]
    for result in results:
        lines.extend(
            [
                "[[service]]",
                f"name = {toml_string(result['service'])}",
                f"status = {toml_string(result['status'])}",
                f"score = {result['score']}",
                "",
            ]
        )
        for check in result["checks"]:
            lines.extend(
                [
                    "[[service.check]]",
                    f"status = {toml_string(check['status'])}",
                    f"name = {toml_string(check['check'])}",
                    f"detail = {toml_string(check['detail'])}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def restore_drill(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first"}

    repos = with_known_restore_repos(restore_profile_repos(parse_repos(snapshot), "full"), "full")
    services = service_restore_plan(repos, "full")
    ports = parse_listening_ports(
        snapshot.get("collectors", {}).get("network", {}).get("listening_tcp_udp", {}).get("stdout", "")
    )
    failed_services = failed_service_lines(systemd_stdout(snapshot, "failed_services"))
    results = [service_drill_result(service, repos, ports, failed_services) for service in services]
    overall_score = round(sum(result["score"] for result in results) / len(results)) if results else 0
    overall_status = "green"
    if any(result["status"] == "red" for result in results):
        overall_status = "red"
    elif any(result["status"] == "yellow" for result in results):
        overall_status = "yellow"

    lines = [
        "# EutherNet Restore Drill",
        "",
        f"- Snapshot: `{snapshot.get('collected_at', '')}`",
        f"- Overall: `{overall_status}` ({overall_score}/100)",
        f"- Services: `{len(results)}`",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result['service']}",
                "",
                f"- Status: `{result['status']}`",
                f"- Score: `{result['score']}/100`",
                "",
            ]
        )
        for check in result["checks"]:
            lines.append(f"- `{check['status']}` {check['check']}: {check['detail']}")
        lines.append("")
    drill_toml = restore_drill_toml(snapshot, overall_status, overall_score, results)

    return {
        "ok": True,
        "collected_at": snapshot.get("collected_at", ""),
        "overall_status": overall_status,
        "overall_score": overall_score,
        "results": results,
        "drill_toml": drill_toml,
        "drill_md": "\n".join(lines).rstrip() + "\n",
    }


def server_map_toml(snapshot: dict[str, Any], nodes: list[dict[str, str]], edges: list[dict[str, str]], ports: list[str], image_prompt: str) -> str:
    lines = [
        "[map]",
        f"collected_at = {toml_string(snapshot.get('collected_at', ''))}",
        f"server = {toml_string(snapshot.get('server', {}).get('name', ''))}",
        f"ports = {toml_array(ports)}",
        f"image_prompt = {toml_string(image_prompt)}",
        "",
    ]
    for node in nodes:
        lines.extend(
            [
                "[[node]]",
                f"id = {toml_string(node['id'])}",
                f"label = {toml_string(node['label'])}",
                f"type = {toml_string(node['type'])}",
                "",
            ]
        )
    for edge in edges:
        lines.extend(
            [
                "[[edge]]",
                f"from = {toml_string(edge['from'])}",
                f"to = {toml_string(edge['to'])}",
                f"label = {toml_string(edge['label'])}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def eutherverse_map(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = latest_snapshot(pathlib.Path(config["server"].get("state_root", "state")))
    if snapshot is None:
        return {"ok": False, "error": "no snapshot exists; run refresh first"}

    repos = with_known_restore_repos(restore_profile_repos(parse_repos(snapshot), "full"), "full")
    services = service_restore_plan(repos, "full")
    collectors = snapshot.get("collectors", {})
    network = collectors.get("network", {})
    running_units = parse_running_service_units(systemd_stdout(snapshot, "running_services"))
    failed_units = set(failed_service_lines(systemd_stdout(snapshot, "failed_services")))
    listening_services = parse_listening_services(network.get("listening_processes", {}).get("stdout", ""))
    ssh_connections = parse_ssh_connections(network.get("ssh_connections", {}).get("stdout", ""))
    ports = sorted(
        parse_listening_ports(
            network.get("listening_tcp_udp", {}).get("stdout", "")
        ),
        key=port_sort_key,
    )
    nodes: list[dict[str, str]] = [
        {"id": "internet", "label": "Internet", "type": "external", "status": "online", "detail": "WAN entrypoint"},
        {"id": "caddy", "label": "Caddy / apothictech.se", "type": "proxy", "status": "observed", "detail": "reverse proxy"},
        {
            "id": "server",
            "label": snapshot.get("server", {}).get("name", "EutherServer"),
            "type": "host",
            "status": "online" if snapshot.get("ssh_preflight", {}).get("ok") else "unknown",
            "detail": snapshot.get("server", {}).get("lan_host", ""),
        },
    ]
    edges: list[dict[str, str]] = [
        {"from": "internet", "to": "caddy", "label": "443/80", "type": "ingress"},
        {"from": "caddy", "to": "server", "label": "reverse proxy", "type": "proxy"},
    ]
    service_reports: list[dict[str, Any]] = []
    for service in services:
        service_id = service["name"].lower()
        units = service.get("systemd", [])
        if any(unit in running_units for unit in units):
            status = "running"
        elif any(any(unit in line for line in failed_units) for unit in units):
            status = "failed"
        else:
            status = "unknown"
        detail = ", ".join(units)
        nodes.append({"id": service_id, "label": service["name"], "type": "service", "status": status, "detail": detail})
        edges.append({"from": "server", "to": service_id, "label": ", ".join(service.get("ports", [])), "type": "hosts"})
        service_reports.append(
            {
                "name": service["name"],
                "status": status,
                "units": units,
                "ports": service.get("ports", []),
                "repo_path": service.get("repo_path", ""),
                "persistent_paths": service.get("persistent_paths", []),
            }
        )
        repo_id = f"{service_id}-repo"
        nodes.append(
            {
                "id": repo_id,
                "label": pathlib.Path(service["repo_path"]).name,
                "type": "repo",
                "status": "present",
                "detail": service["repo_path"],
            }
        )
        edges.append({"from": repo_id, "to": service_id, "label": "deploys", "type": "deploy"})
        if service["name"] == "EutherOxide":
            nodes.extend(eutherium_jox_nodes())
            edges.extend(eutherium_jox_edges(service_id))
    for item in listening_services:
        port_id = f"port-{item['protocol']}-{item['port']}".replace("/", "-")
        nodes.append(
            {
                "id": port_id,
                "label": f"{item['protocol']}:{item['port']}",
                "type": "port",
                "status": item["status"],
                "detail": item.get("process", "") or item.get("local", ""),
            }
        )
        edges.append({"from": "server", "to": port_id, "label": item.get("local", ""), "type": "listens"})
    for index, connection in enumerate(ssh_connections, start=1):
        peer_id = f"ssh-peer-{index}"
        nodes.append(
            {
                "id": peer_id,
                "label": connection["peer"],
                "type": "ssh",
                "status": connection["state"].lower(),
                "detail": connection.get("process", ""),
            }
        )
        if connection["direction"] == "inbound":
            edges.append({"from": peer_id, "to": "server", "label": "ssh inbound", "type": "ssh"})
        else:
            edges.append({"from": "server", "to": peer_id, "label": "ssh outbound", "type": "ssh"})
    nodes.extend(
        [
            {"id": "ollama", "label": "Local Ollama / qwen3-coder", "type": "ai", "status": "configured", "detail": "chat model endpoint"},
            {"id": "imagegen", "label": "Image Generator", "type": "ai", "status": "configured", "detail": "ComfyUI/image generation"},
            {"id": "backups", "label": "Backup Data", "type": "storage", "status": "planned", "detail": "restore/backup manifests"},
        ]
    )
    edges.extend(
        [
            {"from": "eutherpunk", "to": "ollama", "label": "chat model", "type": "ai"},
            {"from": "eutherpal", "to": "ollama", "label": "bank LLM via reverse SSH", "type": "ai"},
            {"from": "eutherpunk", "to": "imagegen", "label": "map render", "type": "ai"},
            {"from": "backups", "to": "eutherbooks", "label": "library/audio", "type": "backup"},
            {"from": "backups", "to": "eutheroxide", "label": "/srv, roms, host data", "type": "backup"},
        ]
    )
    lines = [
        "# EutherVerse Server Map",
        "",
        f"- Snapshot: `{snapshot.get('collected_at', '')}`",
        f"- Nodes: `{len(nodes)}`",
        f"- Edges: `{len(edges)}`",
        f"- Listening ports observed: `{', '.join(ports)}`",
        f"- SSH connections observed: `{len(ssh_connections)}`",
        "",
        "## Nodes",
        "",
    ]
    lines.extend(
        f"- `{node['id']}` ({node['type']}, {node.get('status', 'unknown')}): {node['label']} - {node.get('detail', '')}"
        for node in nodes
    )
    lines.extend(["", "## Edges", ""])
    lines.extend(f"- `{edge['from']}` -> `{edge['to']}`: {edge['label']}" for edge in edges)
    service_names = ", ".join(service["name"] for service in services)
    image_prompt = (
        "Create a detailed cyberpunk network map of the EutherVerse home server. "
        "Show a central server tower labeled EutherServer, a neon reverse proxy gate labeled apothictech.se/Caddy, "
        f"service districts labeled {service_names}, an Eutherium vault district with Joxbox artifact containers, "
        "a provenance ledger, trophy rooms, local AI cores labeled Ollama qwen3-coder and Image Generator, "
        "backup vaults for /srv, ROMs, EutherBooks library/audio, and glowing data links annotated with ports. "
        "Style: readable technical diagram, isometric cyberpunk city, dark background, neon cyan magenta amber accents, "
        "clear labels, no logos, no tiny unreadable text."
    )
    map_toml = server_map_toml(snapshot, nodes, edges, ports, image_prompt)
    return {
        "ok": True,
        "collected_at": snapshot.get("collected_at", ""),
        "nodes": nodes,
        "edges": edges,
        "ports": ports,
        "services": service_reports,
        "listening_services": listening_services,
        "ssh_connections": ssh_connections,
        "map_toml": map_toml,
        "map_md": "\n".join(lines) + "\n",
        "image_prompt": image_prompt,
    }


def eutherium_jox_nodes() -> list[dict[str, str]]:
    return [
        {
            "id": "eutherium",
            "label": "Eutherium Economy",
            "type": "domain",
            "status": "stateful",
            "detail": ".euther-host/eutherium ledger, balances, inventory, shop listings",
        },
        {
            "id": "joxbox",
            "label": "Joxbox",
            "type": "artifact-store",
            "status": "stateful",
            "detail": ".euther-host/eutherium/joxbox self-contained .jox artifacts and embedded media",
        },
        {
            "id": "jox-container",
            "label": ".jox Container",
            "type": "format",
            "status": "versioned",
            "detail": "format/version/schemaVersion, payload hash, assets hash, ownership history, mutation log",
        },
        {
            "id": "jox-provenance",
            "label": "JOX Provenance",
            "type": "integrity",
            "status": "enforced",
            "detail": "valid provenance required for EUX sale value; unknown provenance can exist but has no EUX sale value",
        },
        {
            "id": "trophy-rooms",
            "label": "Trophy Rooms",
            "type": "user-space",
            "status": "stateful",
            "detail": ".euther-host/users/*/eutherium/trophy-room.json layouts and inspectable JOX trophies",
        },
    ]


def eutherium_jox_edges(eutheroxide_id: str) -> list[dict[str, str]]:
    return [
        {"from": eutheroxide_id, "to": "eutherium", "label": "/api/eutherium/*", "type": "api-domain"},
        {"from": "eutherium", "to": "joxbox", "label": "/api/shop/joxbox/*", "type": "artifact-api"},
        {"from": "joxbox", "to": "jox-container", "label": "stores .jox + embedded assets", "type": "contains"},
        {"from": "jox-container", "to": "jox-provenance", "label": "payload/assets hash + mutation log", "type": "integrity"},
        {"from": "eutherium", "to": "trophy-rooms", "label": "inventory placement and public room inspection", "type": "ownership"},
        {"from": "jox-provenance", "to": "eutherium", "label": "validity gates EUX sale value", "type": "policy"},
    ]


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
    subparsers.add_parser("summary")
    subparsers.add_parser("changes")
    subparsers.add_parser("restore-plan")
    subparsers.add_parser("backup-manifest")
    subparsers.add_parser("restore-drill")
    subparsers.add_parser("server-map")

    restore_bundle_parser = subparsers.add_parser("restore-bundle")
    restore_bundle_parser.add_argument("--profile", default="full")

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
    if args.command == "summary":
        return command_summary(config)
    if args.command == "changes":
        return command_changes(config)
    if args.command == "restore-plan":
        return command_restore_plan(config)
    if args.command == "restore-bundle":
        return command_restore_bundle(config, args.profile)
    if args.command == "backup-manifest":
        return command_backup_manifest(config)
    if args.command == "restore-drill":
        return command_restore_drill(config)
    if args.command == "server-map":
        return command_server_map(config)
    if args.command == "ask":
        return command_ask(config, args.question)
    if args.command == "run":
        return command_run(config, args.name)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
