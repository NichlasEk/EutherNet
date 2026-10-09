"""Fixed, metadata-only mirror health export and bounded EutherNet reader.

No backup contents, paths, filenames, credentials or arbitrary commands cross SSH.
"""

import datetime as dt
import json
import pathlib
import subprocess
import threading
import time

_LOCK = threading.Lock()
_CACHE = (0, None)


def timestamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def summarize(report, checked_at, now, copy_at=None):
    if (
        not isinstance(report, dict)
        or checked_at > now + 60
        or now - checked_at > 8 * 3600
    ):
        return {"status": "unknown", "reason": "health-check-missing-or-stale"}
    datasets = {}
    for name in ("accounts", "state", "media"):
        source = report.get("datasets", {}).get(name, {})
        epoch = source.get("latest_epoch")
        count = source.get("backup_count")
        if (
            type(epoch) is not int
            or not 0 < epoch <= now + 60
            or type(count) is not int
            or not 0 < count < 1000000
        ):
            return {"status": "unknown", "reason": "incomplete-health-report"}
        datasets[name] = {
            "copies": count,
            "latest_backup_at": timestamp(epoch),
            "age_seconds": max(0, int(now - epoch)),
        }
    checksums = report.get("all_checksums_ok") is True
    timers = report.get("timer_enabled") is True and report.get("timer_active") is True
    fresh = all(d["age_seconds"] <= 48 * 3600 for d in datasets.values())
    healthy = report.get("ok") is True and checksums and timers and fresh
    return {
        "status": "healthy" if healthy else "failed",
        "checked_at": timestamp(checked_at),
        "source": "workstation .88 local checksum checker",
        "checksums_ok": checksums,
        "timer_ok": timers,
        "backup_fresh": fresh,
        "datasets": datasets,
        "last_successful_copy_at": copy_at,
        "reason": "verified" if healthy else "checksum-timer-or-freshness-check-failed",
    }


def export():
    run = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            "eutherhost-users-mirror-health.service",
            "-n",
            "80",
            "-o",
            "json",
            "--no-pager",
        ],
        capture_output=True,
        timeout=8,
        check=True,
    )
    report = None
    checked = 0
    for line in run.stdout.splitlines():
        try:
            entry = json.loads(line)
            value = json.loads(entry.get("MESSAGE", ""))
            if isinstance(value, dict) and value.get("label") == "workstation-mirror":
                report, checked = value, int(entry["__REALTIME_TIMESTAMP"]) / 1000000
        except (ValueError, TypeError, KeyError):
            continue
    copy_at = None
    state = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            "eutherhost-users-mirror.service",
            "-p",
            "Result",
            "-p",
            "ExecMainExitTimestamp",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    props = dict(
        line.split("=", 1) for line in state.stdout.splitlines() if "=" in line
    )
    if props.get("Result") == "success" and props.get("ExecMainExitTimestamp"):
        copy_at = props["ExecMainExitTimestamp"][:100]
    summary = summarize(report, checked, time.time(), copy_at)
    if props.get("Result") not in {None, "success"}:
        summary.update(status="failed", reason="latest-copy-attempt-failed")
    health = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            "eutherhost-users-mirror-health.service",
            "-p",
            "Result",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    if "Result=success" not in health.stdout:
        summary.update(status="failed", reason="health-check-execution-failed")
    return summary


def describe(value):
    if value["status"] == "unknown":
        return (
            "Mirror health unavailable or older than 8 hours; no current health claim."
        )
    parts = [
        "Checksums: " + ("passed" if value.get("checksums_ok") else "not verified"),
        "check time: " + str(value.get("checked_at", "unknown")),
        "backup schedule: " + ("active" if value.get("timer_ok") else "not active"),
        "last successful copy: "
        + str(value.get("last_successful_copy_at") or "unknown"),
    ]
    for name, data in value.get("datasets", {}).items():
        parts.append(
            f"{name}: {data['copies']} encrypted files, newest backup {data['latest_backup_at']}"
        )
    if value["status"] == "failed":
        parts.append("Check failed: " + value.get("reason", "unknown"))
    return "; ".join(parts)


def read_remote():
    global _CACHE
    with _LOCK:
        now = time.monotonic()
        if now - _CACHE[0] < 60 and _CACHE[1] is not None:
            return dict(_CACHE[1])
        value = {"status": "unknown", "reason": "mirror-health-unavailable"}
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-F",
                    "/dev/null",
                    "-i",
                    str(pathlib.Path.home() / ".ssh/euther_mirror_health"),
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    "ConnectTimeout=3",
                    "nichlas@192.168.32.88",
                    "mirror-health",
                ],
                capture_output=True,
                timeout=12,
                check=True,
            )
            if len(result.stdout) > 8192:
                raise ValueError("oversized report")
            raw = json.loads(result.stdout)
            if raw.get("status") not in {"healthy", "failed", "unknown"}:
                raise ValueError("invalid status")
            # Project again; do not trust arbitrary SSH output as public metadata.
            value = {
                k: raw[k]
                for k in (
                    "status",
                    "reason",
                    "checked_at",
                    "checksums_ok",
                    "timer_ok",
                    "backup_fresh",
                    "last_successful_copy_at",
                    "source",
                    "datasets",
                )
                if k in raw
            }
            if raw["status"] != "unknown":
                checked = dt.datetime.fromisoformat(raw["checked_at"]).timestamp()
                if not 0 <= time.time() - checked <= 8 * 3600:
                    value = {"status": "unknown", "reason": "health-check-stale"}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass
        _CACHE = (now, value)
        return dict(value)


if __name__ == "__main__":
    try:
        print(json.dumps(export()))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        print(json.dumps({"status": "unknown", "reason": "health-export-unavailable"}))
