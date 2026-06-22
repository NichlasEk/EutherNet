# EutherNet Plan

## Purpose

EutherNet should become a small support service for the EutherOxide server. It
will continuously map what exists on the server, summarize it in Markdown, keep
a structured TOML inventory, expose the useful parts through MCP, and use a
local AI model for periodic explanation and diagnostics.

The practical goal is not only monitoring. The service should make the server
easier to understand, repair, update, and recreate after disk or hardware
failure.

## Target Environment

- Primary host: EutherOxide server on the LAN.
- Public front: `apothictech.se`.
- Existing app context: EutherOxide host service and related repos.
- Preferred access model: SSH public key authentication.
- Secret handling: credentials, passphrases, sudo passwords, private keys, API
  tokens, and raw sensitive dumps must stay outside git.

## Core Outputs

1. `inventory/server.md`
   - Human-readable description of the host.
   - Services, ports, domains, reverse proxy rules, repos, timers, storage,
     backups, and restore notes.

2. `inventory/server.toml`
   - Machine-readable server inventory.
   - Stable identifiers for services, repos, systemd units, paths, ports,
     health checks, and restore commands.

3. `reports/`
   - Periodic snapshots and AI-written summaries.
   - Drift reports such as changed systemd units, changed git state, failed
     health checks, or unknown new listening ports.

4. MCP tools/resources
   - Query current inventory.
   - Ask for service/repo status.
   - Produce restore checklist.
   - Surface likely causes for detected failures.

## Phased Build

### Phase 1: Repo Scaffold and Safety

- Initialize git and attach the GitHub remote.
- Add `.gitignore` rules for secrets, private state, logs, live inventory, and
  local databases.
- Define the TOML schema before collecting live data.
- Add a redaction policy so command output is filtered before being written.

Done when the repo has a safe structure and can accept real implementation
without risking credential leaks.

### Phase 2: Read-Only Server Inventory

Build a read-only collector that connects over SSH and records:

- OS and kernel version.
- Hostname, users relevant to services, and timezone.
- systemd units for EutherOxide and related services.
- Listening ports with owning processes.
- Reverse proxy or web server configuration references.
- Git repositories under known roots such as `/home/nichlas`.
- Disk mounts, free space, and backup-relevant paths.
- Health check URLs for LAN and public routes.

The collector should write normalized JSON/TOML into ignored runtime state
first, then generate reviewed Markdown/TOML snapshots for committed inventory.

### Phase 3: Git Repository Tracker

Track each repository on the server:

- Path, remote URL, current branch, current commit.
- Dirty state and untracked files summary.
- Last commit date.
- Whether the repo has local-only work.
- Suggested backup priority.

This should make it obvious which repos need committing, pushing, or backing up
before server changes.

### Phase 4: Service Health and Drift Detection

Add scheduled checks:

- systemd active/failed status.
- HTTP health endpoints.
- TLS/domain reachability for public routes.
- Open port changes.
- New or removed repos.
- Git dirty state changes.
- Disk pressure.

Each run should produce a small drift report rather than rewriting everything.

### Phase 5: Local AI Summaries

Use a local model endpoint if one is already available on the machine or LAN.
The AI should only receive redacted inventory data and should produce:

- Short operational summaries.
- "What changed since last run" notes.
- Restore guidance.
- Probable causes for failed checks.
- Questions for the operator when the data is ambiguous.

The AI must not be the source of truth. It explains collected facts; collectors
and health checks provide the facts.

### Phase 6: MCP Interface

Expose EutherNet through MCP with resources such as:

- `euthernet://server/current`
- `euthernet://server/services`
- `euthernet://server/repos`
- `euthernet://reports/latest`
- `euthernet://restore/checklist`

Useful tools can include:

- `refresh_inventory`
- `check_service`
- `check_repo`
- `summarize_drift`
- `build_restore_plan`

All tools should default to read-only. Any future write operation should require
an explicit operator action.

### Phase 7: Restore Runbook

Generate and maintain a restore guide:

- Fresh OS assumptions.
- Required packages.
- Required users/groups.
- Required directories and ownership.
- Repos to clone and branches to checkout.
- systemd unit install/restart order.
- Reverse proxy/domain setup.
- Backup restore points.
- Verification commands.

The restore runbook should be generated from inventory where possible, with
manual notes for anything that cannot be discovered safely.

## First Implementation Slice

1. Create the config loader for `euthernet.toml`.
2. Add a read-only SSH collector with a small allowlist of commands.
3. Write raw runtime output under ignored `state/`.
4. Generate `inventory/server.toml` and `inventory/server.md`.
5. Add a manual `make inventory` or script entry point.
6. Run once against the LAN server and review redaction before committing any
   generated inventory.

## Open Decisions

- Implementation language: Rust is a good fit if this should become a durable
  service/MCP server; Python is faster for the first collector.
- Scheduler: systemd timer on the server, local cron, or a long-running service.
- AI endpoint: reuse an existing local model if available, otherwise leave AI
  summarization disabled until a local endpoint is confirmed.
- Public/private split: decide whether committed inventory is allowed to mention
  internal IPs and paths, or whether the repo should only contain examples plus
  encrypted/private inventory elsewhere.
