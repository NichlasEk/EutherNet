"""Shared, deterministic observer inside the existing EutherNet process.

No model calls, commands, network client, raw logs, or arbitrary file access.
The host supplies cached topology; persistence is one fixed state-root file.
"""

from __future__ import annotations
import collections
import copy
import fcntl
import datetime as dt
import json
import math
import os
import pathlib
import re
import threading
import time

ID = re.compile(r"^[a-zA-Z0-9_.-]{1,80}$")
STATUSES = {
    "online",
    "running",
    "healthy",
    "failed",
    "degraded",
    "offline",
    "unknown",
    "configured",
    "connected",
    "observed",
    "reachable",
    "managed",
    "planned",
}
KINDS = {"host", "service", "storage", "proxy", "ai"}
LANES = [
    ("external", -36, -12),
    ("proxy", -24, -6),
    ("host", -10, 0),
    ("service", 8, 0),
    ("port", 24, 6),
    ("repo", 38, -5),
    ("ssh", 48, 16),
    ("ai", 0, 22),
    ("storage", 18, 22),
]


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def iso(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return (
        parsed.astimezone(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def epoch(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000


def point(value):
    return isinstance(value, dict) and all(
        isinstance(value.get(k), (float, int))
        and math.isfinite(value[k])
        and abs(value[k]) <= 160
        for k in ("x", "z")
    )


def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["z"] - b["z"])


def project(raw):
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("nodes"), list)
        or len(raw["nodes"]) > 2000
        or not isinstance(raw.get("edges"), list)
        or len(raw["edges"]) > 8000
    ):
        raise ValueError("invalid topology")
    positions = {}
    for kind, x, base in LANES:
        lane = [n for n in raw["nodes"] if n.get("type") == kind]
        spacing = max(5.5, min(10, 62 / max(1, len(lane))))
        start = base - (len(lane) - 1) * spacing / 2
        for i, n in enumerate(lane):
            positions[n["id"]] = {"x": x, "z": start + i * spacing}
    fallback = 0
    for n in raw["nodes"]:
        if n["id"] not in positions:
            positions[n["id"]] = {"x": 0, "z": -32 + fallback * 6}
            fallback += 1
    obstacles = [p for p in positions.values() if point(p)]
    nodes = []
    seen = set()
    for n in raw["nodes"]:
        p = positions[n["id"]]
        if (
            not valid_id(n["id"])
            or n["type"] not in KINDS
            or not point(p)
            or n["id"] in seen
        ):
            continue
        seen.add(n["id"])
        nodes.append(
            {
                "id": n["id"],
                "type": n["type"],
                "status": n.get("status") if n.get("status") in STATUSES else "unknown",
                **p,
            }
        )
    edges = [
        {"from": e["from"], "to": e["to"], "type": e["type"]}
        for e in raw["edges"]
        if e.get("from") in seen
        and e.get("to") in seen
        and e.get("type") in {"hosts", "dependency", "proxy", "ai", "state", "access"}
    ]
    return {
        "stamp": iso(raw["collected_at"]),
        "nodes": nodes,
        "edges": edges,
        "obstacles": obstacles,
    }


def initial():
    return {
        "version": 1,
        "position": {"x": 0, "z": 58},
        "phase": "contemplating",
        "destination": None,
        "paused": False,
        "disabled": False,
        "nextAt": 0,
        "cursor": 0,
        "observations": [],
        "ghosts": [],
        "statuses": {},
        "stamp": "",
        "lastModelAt": 0,
        "queued": [],
        "visited": [],
    }


def observation(raw):
    if (
        not isinstance(raw, dict)
        or not valid_id(raw.get("node"))
        or raw.get("status") not in STATUSES
        or not isinstance(raw.get("hosted"), list)
        or len(raw["hosted"]) > 12
        or not all(valid_id(v) for v in raw["hosted"])
        or not isinstance(raw.get("dependents", []), list)
        or len(raw.get("dependents", [])) > 12
        or not all(valid_id(v) for v in raw.get("dependents", []))
    ):
        raise ValueError("invalid observation")
    stamp = iso(raw["stamp"])
    oid = f"{stamp}/{raw['node']}/{raw['status']}"
    if (
        raw.get("id") != oid
        or not isinstance(raw.get("at"), (int, float))
        or not math.isfinite(raw["at"])
    ):
        raise ValueError("invalid observation identity")
    return {
        "id": oid,
        "node": raw["node"],
        "stamp": stamp,
        "status": raw["status"],
        "at": raw["at"],
        "kind": "verified fact",
        "source": "EutherNet inventory",
        "outcome": "inspected" if len(raw["hosted"]) >= 2 else "nothing interesting",
        "hosted": list(raw["hosted"]),
        "dependents": [v for v in raw.get("dependents", [])[:12] if valid_id(v)],
    }


def hypothesis(o, now, status="active", pattern="shared-host", history=None):
    host = o["node"]
    links = sorted(
        set(o.get("dependents", []) if pattern == "shared-dependency" else o["hosted"])
    )
    result = {
        "id": f"{pattern}/{host}/{','.join(links)}",
        "pattern": pattern,
        "kind": "hypothesis",
        "support": copy.deepcopy(o),
        "node": host,
        "links": [host, *links],
        "evidence": [o["id"]],
        "description": f"What if the services mapped to {host} had independently tested recovery paths?",
        "assumptions": "Inventory hosts edges describe actual placement. Co-location does not establish shared backup or recovery dependencies.",
        "uncertainty": "Unknown: recovery isolation, restore success, redundancy and failure probability. Inference: co-location may create correlated downtime.",
        "benefit": "A documented recovery exercise could reveal missing independent recovery paths.",
        "risks": "A real restore exercise may consume resources or interrupt service; requires a separate owner-approved plan.",
        "test": "First compare documented recovery locations for linked services. Scryer has not performed a restore or read backup contents.",
        "status": status,
        "at": now,
    }

    if pattern == "shared-dependency":
        result.update(
            description=f"What if the services depending on {host} lost that shared dependency?",
            assumptions="Dependency edges describe a real requirement. The map does not establish fallback behavior.",
            uncertainty="Inference: a shared dependency may affect several services. Unknown: redundancy, cache behavior and actual outage impact.",
            benefit="Documenting fallback behavior could reveal a common point of failure.",
            test="Compare documented fallback behavior for linked services. No outage or failover has been triggered.",
        )
    elif pattern == "status-changes":
        history = [observation(item) for item in (history or [])][-4:]
        if (
            len(history) < 3
            or len({v["stamp"] for v in history}) != len(history)
            or any(v["node"] != host for v in history)
            or sum(a["status"] != b["status"] for a, b in zip(history, history[1:])) < 2
        ):
            raise ValueError("insufficient status change evidence")
        result.update(
            id=f"status-changes/{host}",
            links=[host],
            history=history,
            evidence=[v["id"] for v in history],
            description=f"What if the repeated reported status changes for {host} have a common cause?",
            assumptions="Inventory snapshots are comparable. Changes may reflect collector timing rather than a service problem.",
            uncertainty="Verified: differing inventory statuses. Unknown: actual uptime, root cause and whether users were affected.",
            benefit="Comparing change times with maintenance history could distinguish expected transitions from instability.",
            test="Compare the listed observation times with documented maintenance. No logs or private content have been read.",
        )
    elif pattern != "shared-host":
        raise ValueError("unknown hypothesis pattern")
    return result


def review(value):
    if value is None:
        return {"saved": False, "outcome": "untested", "note": "", "at": 0}
    if not isinstance(value, dict) or value.get("outcome") not in {
        "untested",
        "supported",
        "not-supported",
        "inconclusive",
    }:
        raise ValueError("invalid review")
    note = value.get("note", "")
    at = value.get("at", 0)
    if (
        not isinstance(note, str)
        or len(note) > 600
        or not isinstance(at, (int, float))
        or not math.isfinite(at)
    ):
        raise ValueError("invalid review note")
    return {
        "saved": value.get("saved") is True,
        "outcome": value["outcome"],
        "note": note,
        "at": at,
    }


def restore_ghost(g):
    result = hypothesis(
        observation(g["support"]),
        g["at"],
        g["status"],
        g.get("pattern", "shared-host"),
        g.get("history"),
    )
    if g.get("review") is not None:
        result["review"] = review(g["review"])
    return result


class ScryerWorld:
    def __init__(self, state_root, provider, clock=lambda: time.time() * 1000):
        self.file = pathlib.Path(state_root) / "scryer-state.json"
        self.provider = provider
        self.clock = clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.lease = None
        self.state = initial()
        self.world = {"stamp": "", "nodes": [], "edges": [], "obstacles": []}
        self.path = []
        self.blocked = set()
        self.last_refresh = 0
        self.last_save = 0
        self.last_tick = self.clock()
        self.error = ""
        self.conversation_until = 0
        self.revision = 0
        self.restore()

    def restore(self):
        if not self.file.exists():
            return
        try:
            if self.file.stat().st_size > 256_000:
                raise ValueError("oversized state")
            raw = json.loads(self.file.read_text())
            now = self.clock()
            if raw.get("version") != 1 or not point(raw.get("position")):
                raise ValueError("invalid version or position")
            state = initial()
            state["position"] = {k: raw["position"][k] for k in ("x", "z")}
            for key, limit in [
                ("observations", 64),
                ("ghosts", 24),
                ("queued", 32),
                ("visited", 2000),
            ]:
                if not isinstance(raw.get(key), list) or len(raw[key]) > limit:
                    raise ValueError("invalid memory size")
            state["observations"] = [observation(o) for o in raw["observations"]]
            state["ghosts"] = [
                restore_ghost(g)
                for g in raw["ghosts"]
                if g.get("kind") == "hypothesis"
                and g.get("status") in {"active", "dismissed", "archived"}
                and isinstance(g.get("at"), (int, float))
                and math.isfinite(g["at"])
            ]
            for key in ("queued", "visited"):
                if not all(valid_id(v) for v in raw[key]):
                    raise ValueError("invalid node identifier")
                state[key] = list(raw[key])
            state["paused"] = raw.get("paused") is True
            state["disabled"] = raw.get("disabled") is True
            state["destination"] = (
                raw.get("destination") if valid_id(raw.get("destination")) else None
            )
            state["stamp"] = iso(raw["stamp"]) if raw.get("stamp") else ""
            if not isinstance(raw.get("statuses"), dict) or len(raw["statuses"]) > 2000:
                raise ValueError("invalid statuses")
            state["statuses"] = {
                k: v
                for k, v in raw["statuses"].items()
                if valid_id(k) and v in STATUSES
            }
            state["cursor"] = max(0, int(raw.get("cursor", 0))) % 2000
            state["nextAt"] = max(now, min(float(raw.get("nextAt", 0)), now + 60000))
            if not math.isfinite(state["nextAt"]):
                raise ValueError("invalid cooldown")
            self.state = state
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            # Fail closed: keep the corrupt file available for owner diagnostics.
            self.state = initial()
            self.state["paused"] = True
            self.error = "state-invalid"
            try:
                self.file.replace(self.file.with_suffix(".invalid"))
            except OSError:
                pass

    def save(self):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.file.with_suffix(".tmp")
            with temporary.open("w") as stream:
                os.chmod(temporary, 0o600)
                json.dump(self.state, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.file)
            descriptor = os.open(self.file.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.last_save = self.clock()
            if self.error == "state-write-failed":
                self.error = ""
            return True
        except (OSError, ValueError):
            self.error = "state-write-failed"
            self.state["paused"] = True
            return False

    def ingest(self, raw):
        world = project(raw)
        with self.lock:
            s = self.state
            if s["stamp"] and epoch(world["stamp"]) < epoch(s["stamp"]):
                return
            if world["stamp"] != s["stamp"]:
                s["visited"] = []
            ids = {n["id"] for n in world["nodes"]}
            s["queued"] = [v for v in s["queued"] if v in ids]
            for n in world["nodes"]:
                if (
                    n["id"] in s["statuses"]
                    and s["statuses"][n["id"]] != n["status"]
                    and n["id"] not in s["queued"]
                ):
                    s["queued"].append(n["id"])
            s["queued"] = s["queued"][-32:]
            s["statuses"] = {n["id"]: n["status"] for n in world["nodes"]}
            s["stamp"] = world["stamp"]
            self.world = world
            # Conservative raster: 5.6 clearance includes the entire 2-unit step.
            blocked = set()
            for p in world["obstacles"]:
                for x in range(
                    math.floor((p["x"] - 6) / 2) * 2,
                    math.ceil((p["x"] + 6) / 2) * 2 + 1,
                    2,
                ):
                    for z in range(
                        math.floor((p["z"] - 6) / 2) * 2,
                        math.ceil((p["z"] + 6) / 2) * 2 + 1,
                        2,
                    ):
                        if math.hypot(x - p["x"], z - p["z"]) < 5.6:
                            blocked.add((x, z))
            self.blocked = blocked
            if any(distance(s["position"], o) < 4.5 for o in world["obstacles"]):
                safe = next(
                    (
                        {"x": -158, "z": z}
                        for z in range(-158, 159, 2)
                        if (-158, z) not in blocked
                    ),
                    None,
                )
                if safe:
                    s["position"] = safe
                else:
                    s["paused"] = True
                    self.error = "no-safe-position"
            target = next(
                (n for n in world["nodes"] if n["id"] == s["destination"]), None
            )
            self.path = self.route(target) if target else []
            if s["destination"] and not self.path:
                s["destination"] = None
                s["phase"] = "contemplating"
                s["nextAt"] = self.clock()
            hosts = {
                (e["from"], e["to"]) for e in world["edges"] if e["type"] == "hosts"
            }
            dependencies = {
                (e["to"], e["from"])
                for e in world["edges"]
                if e["type"] == "dependency"
            }
            for g in s["ghosts"]:
                if g["status"] == "active" and (
                    any(v not in ids for v in g["links"])
                    or any(
                        (g["node"], v)
                        not in (
                            dependencies
                            if g.get("pattern") == "shared-dependency"
                            else hosts
                        )
                        for v in g["links"]
                        if v != g["node"]
                    )
                ):
                    g["status"] = "archived"
            self.revision += 1
            if self.error == "inventory-unavailable":
                self.error = ""

    def route(self, target):
        if target is None:
            return []
        p = self.state["position"]
        origin = (round(p["x"] / 2) * 2, round(p["z"] / 2) * 2)
        if origin in self.blocked:
            return []
        # The snapped start also needs actual continuous clearance.
        if any(distance(p, o) < 4.5 for o in self.world["obstacles"]):
            return []
        queue = collections.deque([origin])
        parents = {origin: None}
        found = None
        while queue and len(parents) <= 26000:
            current = queue.popleft()
            d = math.hypot(current[0] - target["x"], current[1] - target["z"])
            if 4.5 <= d <= 7:
                found = current
                break
            for dx, dz in [(2, 0), (-2, 0), (0, 2), (0, -2)]:
                following = (current[0] + dx, current[1] + dz)
                if (
                    max(abs(following[0]), abs(following[1])) > 160
                    or following in self.blocked
                    or following in parents
                ):
                    continue
                parents[following] = current
                queue.append(following)
        result = []
        while found is not None:
            result.append({"x": found[0], "z": found[1]})
            found = parents[found]
        return result[::-1]

    def tick(self, seconds, now):
        with self.lock:
            s = self.state
            if s["paused"] or s["disabled"] or not self.world["nodes"]:
                return
            if now - epoch(self.world["stamp"]) > 86400000:
                s["phase"] = "contemplating"
                return
            if now < self.conversation_until:
                s["phase"] = "conversing"
                return
            if self.path:
                s["phase"] = (
                    "investigating" if s["destination"] in s["queued"] else "wandering"
                )
                target = self.path[0]
                d = distance(s["position"], target)
                step = max(0, min(seconds, 0.5)) * 4
                if d <= step:
                    s["position"] = dict(target)
                    self.path.pop(0)
                elif d:
                    s["position"] = {
                        k: s["position"][k] + (target[k] - s["position"][k]) * step / d
                        for k in ("x", "z")
                    }
                if not self.path:
                    s["phase"] = "observing"
                    s["nextAt"] = now + 4000
                self.revision += 1
                return
            if now < s["nextAt"]:
                return
            if s["destination"]:
                self.inspect(s["destination"], now)
                s["queued"] = [v for v in s["queued"] if v != s["destination"]]
                s["destination"] = None
                s["nextAt"] = now + 60000
                self.save()
                return
            nodes = self.world["nodes"]
            cursor = s["cursor"] % len(nodes)
            choices = list(
                dict.fromkeys(
                    s["queued"] + [n["id"] for n in nodes[cursor:] + nodes[:cursor]]
                )
            )
            for identity in choices[:32]:
                target = next((n for n in nodes if n["id"] == identity), None)
                path = self.route(target)
                if not path:
                    continue
                s["destination"] = identity
                s["phase"] = "investigating" if identity in s["queued"] else "wandering"
                s["cursor"] = (nodes.index(target) + 1) % len(nodes)
                self.path = path
                self.revision += 1
                self.save()
                return
            s["cursor"] = (cursor + 32) % len(nodes)
            s["phase"] = "contemplating"
            s["nextAt"] = now + 60000

    def inspect(self, identity, now):
        s = self.state
        n = next((n for n in self.world["nodes"] if n["id"] == identity), None)
        if not n or distance(s["position"], n) > 7.1 or identity in s["visited"]:
            s["phase"] = "contemplating"
            return
        hosted = sorted(
            {
                e["to"]
                for e in self.world["edges"]
                if e["type"] == "hosts" and e["from"] == identity
            }
        )[:12]
        o = observation(
            {
                "id": f"{s['stamp']}/{identity}/{n['status']}",
                "node": identity,
                "stamp": s["stamp"],
                "status": n["status"],
                "at": now,
                "hosted": hosted,
                "dependents": sorted(
                    {
                        e["from"]
                        for e in self.world["edges"]
                        if e["type"] == "dependency" and e["to"] == identity
                    }
                )[:12],
            }
        )
        s["observations"] = (s["observations"] + [o])[-64:]
        s["visited"].append(identity)
        s["phase"] = "contemplating"
        candidates = []
        if len(hosted) >= 2:
            candidates.append(hypothesis(o, now))
        if len(o["dependents"]) >= 2:
            candidates.append(hypothesis(o, now, pattern="shared-dependency"))
        history = [v for v in s["observations"] if v["node"] == identity][-4:]
        if (
            len(history) >= 3
            and len({v["stamp"] for v in history}) == len(history)
            and sum(a["status"] != b["status"] for a, b in zip(history, history[1:]))
            >= 2
        ):
            candidates.append(
                hypothesis(o, now, pattern="status-changes", history=history)
            )
        for ghost in candidates:
            if not any(g["id"] == ghost["id"] for g in s["ghosts"]):
                if len(s["ghosts"]) >= 24:
                    disposable = next(
                        (
                            g
                            for g in s["ghosts"]
                            if not g.get("review", {}).get("saved")
                        ),
                        None,
                    )
                    if disposable is None:
                        continue
                    s["ghosts"].remove(disposable)
                s["ghosts"].append(ghost)
                s["phase"] = "discovery"
        self.revision += 1

    def control(self, command, ghost_id=None, data=None):
        with self.lock:
            if command not in {
                "pause",
                "resume",
                "disable",
                "enable",
                "dismiss",
                "save",
                "review",
            }:
                raise ValueError("unsupported control")
            before = copy.deepcopy(self.state)
            if command in {"pause", "resume"}:
                self.state["paused"] = command == "pause"
            if command in {"disable", "enable"}:
                self.state["disabled"] = command == "disable"
            if command in {"dismiss", "save", "review"}:
                ghost = next(
                    (g for g in self.state["ghosts"] if g["id"] == ghost_id), None
                )
                if not ghost:
                    raise ValueError("unknown hypothesis")
                if command == "dismiss":
                    ghost["status"] = "dismissed"
                else:
                    current = review(ghost.get("review"))
                    if command == "save":
                        current["saved"] = not current["saved"]
                    else:
                        if not isinstance(data, dict):
                            raise ValueError("review required")
                        current = review(
                            {
                                **current,
                                **{
                                    k: data[k] for k in ("note", "outcome") if k in data
                                },
                            }
                        )
                    current["at"] = self.clock()
                    ghost["review"] = current
            if command in {"resume", "enable"} and self.error in {
                "state-invalid",
                "cycle-failed",
                "no-safe-position",
            }:
                self.error = ""
            self.conversation_until = 0
            self.last_refresh = 0
            self.revision += 1
            if not self.save():
                self.state = before
                self.state["paused"] = True
                raise OSError("state persistence failed")
            print(f"scryer control={command} revision={self.revision}", flush=True)
            return self.snapshot()

    def model_allowed(self):
        with self.lock:
            return not self.state["paused"] and not self.state["disabled"]

    def conversation(self):
        with self.lock:
            if not self.state["paused"] and not self.state["disabled"]:
                self.conversation_until = self.clock() + 30000
            return self.state["destination"] or (
                self.state["observations"][-1]["node"]
                if self.state["observations"]
                else None
            )

    def snapshot(self):
        with self.lock:
            return {
                "ok": True,
                "scope": "shared",
                "state": copy.deepcopy(self.state),
                "world": copy.deepcopy(self.world),
                "revision": self.revision,
                "updated_at": self.clock(),
                "diagnostics": {
                    "error": self.error,
                    "observations": len(self.state["observations"]),
                    "hypotheses": len(self.state["ghosts"]),
                },
            }

    def run(self):
        while not self.stop.is_set():
            now = self.clock()
            if (
                now - self.last_refresh >= 60000
                and not self.state["paused"]
                and not self.state["disabled"]
            ):
                self.last_refresh = now
                try:
                    self.ingest(self.provider())
                except Exception as error:
                    with self.lock:
                        self.error = "inventory-unavailable"
                        self.state["phase"] = "contemplating"
                    print(
                        f"scryer inventory-unavailable type={type(error).__name__}",
                        flush=True,
                    )
            try:
                # Failed inventory access suspends work, not fresh observations from stale cache.
                if self.error != "inventory-unavailable":
                    self.tick((now - self.last_tick) / 1000, now)
                if now - self.last_save >= 5000:
                    with self.lock:
                        self.save()
            except Exception as error:
                with self.lock:
                    self.state["paused"] = True
                    self.error = "cycle-failed"
                print(f"scryer cycle-failed type={type(error).__name__}", flush=True)
            self.last_tick = now
            self.stop.wait(0.25)

    def start(self):
        if self.thread is None:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            lease = (self.file.parent / "scryer-writer.lock").open("a")
            try:
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lease.close()
                raise
            self.lease = lease
            self.thread = threading.Thread(
                target=self.run, name="euthernet-scryer", daemon=True
            )
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.lease is None:
            return
        if self.thread:
            self.thread.join(timeout=5)
        if self.thread and self.thread.is_alive():
            return
        with self.lock:
            self.save()
        if self.lease:
            fcntl.flock(self.lease.fileno(), fcntl.LOCK_UN)
            self.lease.close()
            self.lease = None
