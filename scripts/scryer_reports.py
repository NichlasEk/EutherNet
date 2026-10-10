"""Bounded deterministic issue lifecycle, separate from speculative Ghost Nodes."""

import copy
import math
import re

BAD = {"failed", "degraded", "offline"}
GOOD = {"healthy", "running", "online", "reachable"}
LIMIT = 64


def restore(raw):
    if not isinstance(raw, list) or len(raw) > LIMIT:
        raise ValueError("invalid reports")
    result = []
    for r in raw:
        if not isinstance(r, dict) or not re.fullmatch(
            r"[a-zA-Z0-9_.-]{1,80}", r.get("id", "")
        ):
            raise ValueError("invalid report id")
        if r.get("state") not in {
            "pending",
            "open",
            "unconfirmed",
            "resolved",
        } or r.get("status") not in BAD | GOOD | {
            "unknown",
            "configured",
            "observed",
            "connected",
            "planned",
            "managed",
        }:
            raise ValueError("invalid report state")
        item = {
            k: r[k]
            for k in ("id", "state", "status", "stamp", "first_stamp", "last_bad_stamp")
        }
        if any(
            not isinstance(item[k], str) or len(item[k]) > 40
            for k in ("stamp", "first_stamp", "last_bad_stamp")
        ):
            raise ValueError("invalid report evidence")
        for k in ("first_at", "updated_at", "snoozed_until", "episode", "samples"):
            if (
                type(r.get(k)) not in (int, float)
                or not math.isfinite(r[k])
                or r[k] < 0
            ):
                raise ValueError("invalid report time")
            item[k] = r[k]
        item["acknowledged"] = r.get("acknowledged") is True
        result.append(item)
    return result


def update(reports, world, now):
    """Repeated reads of one snapshot never count as independent evidence."""
    nodes = {n["id"]: n for n in world["nodes"]}
    by_id = {r["id"]: r for r in reports}
    for identity, node in nodes.items():
        status = node["status"]
        r = by_id.get(identity)
        if status in BAD:
            if r is None:
                if len(reports) >= LIMIT:
                    old = next((v for v in reports if v["state"] == "resolved"), None)
                    if old is None:
                        continue
                    reports.remove(old)
                r = dict(
                    id=identity,
                    state="pending",
                    status=status,
                    stamp="",
                    first_stamp=world["stamp"],
                    last_bad_stamp=world["stamp"],
                    first_at=now,
                    updated_at=now,
                    snoozed_until=0,
                    episode=1,
                    samples=0,
                    acknowledged=False,
                )
                reports.append(r)
            elif r["state"] == "resolved":
                r.update(
                    state="pending",
                    first_stamp=world["stamp"],
                    first_at=now,
                    episode=r["episode"] + 1,
                    samples=0,
                    acknowledged=False,
                    snoozed_until=0,
                )
            if world["stamp"] != r["stamp"]:
                r["samples"] += 1
            r.update(
                status=status,
                stamp=world["stamp"],
                last_bad_stamp=world["stamp"],
                updated_at=now,
            )
            r["state"] = (
                "open" if status == "failed" or r["samples"] >= 2 else "pending"
            )
        elif r is not None:
            r.update(status=status, stamp=world["stamp"], updated_at=now)
            r["state"] = "resolved" if status in GOOD else "unconfirmed"
    for r in reports:
        if r["id"] not in nodes and r["state"] != "resolved":
            r.update(state="unconfirmed", status="unknown")


def control(reports, command, identity, now):
    r = next((r for r in reports if r["id"] == identity), None)
    if r is None:
        raise ValueError("unknown report")
    if command == "report-ack":
        r["acknowledged"] = True
    elif command == "report-snooze":
        r["snoozed_until"] = now + 3600000
    else:
        raise ValueError("unsupported report control")


def public(reports, world, now):
    items = copy.deepcopy(reports)
    for r in items:
        r["related"] = sorted(
            {
                e["from"] if e["to"] == r["id"] else e["to"]
                for e in world["edges"]
                if r["id"] in (e["from"], e["to"])
                and e["type"] in {"hosts", "dependency"}
            }
        )[:8]
        r["needs_attention"] = (
            r["state"] == "open" and not r["acknowledged"] and now >= r["snoozed_until"]
        )
    return sorted(
        items,
        key=lambda r: (
            not r["needs_attention"],
            r["state"] == "resolved",
            -r["updated_at"],
        ),
    )
