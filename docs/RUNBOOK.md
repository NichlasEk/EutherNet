# EutherNet Runbook

## Local Inventory Run

Unlock the SSH key for the EutherOxide server before running the collector:

```sh
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/euther_server
```

Then run:

```sh
make inventory
```

The collector uses `BatchMode=yes`, so it will not prompt for a password or
passphrase. If the key is not available through the agent, it fails fast and
writes a preflight error instead of repeatedly trying server commands.

If `ssh-add` prints `Could not open a connection to your authentication agent`,
the shell does not have an active SSH agent. Run the `eval "$(ssh-agent -s)"`
line above in the same terminal, then run `ssh-add` again.

## Outputs

- `inventory/server.md`: reviewed human-readable inventory.
- `inventory/server.toml`: structured inventory summary.
- `state/snapshot-*.json`: ignored runtime snapshot for local diagnosis.

The `state/` directory is intentionally ignored by git. Review generated
inventory before committing it.

## Asking EutherNet

After a successful inventory run, ask local questions from the latest snapshot:

```sh
make status
make repos
make summary
make changes
make restore-plan
make restore-bundle PROFILE=full
make restore-bundle PROFILE=backup
make ask Q="hur mår servern?"
make ask Q="vilka repos finns?"
```

If local Ollama is running, `make ask` sends the redacted inventory context to
the configured local model. If the model is unavailable, it falls back to the
deterministic inventory answer.

Run simple read-only remote commands through named aliases:

```sh
make run CMD=health
make run CMD=failed-services
make run CMD=listening-ports
```

The aliases are defined in `euthernet.toml`. Avoid adding broad shell aliases.
The intended model is that MCP and AI use these same named operations instead
of arbitrary shell access.

## MCP

Run the stdio MCP server locally:

```sh
make mcp
```

It exposes:

- `euthernet://server/current`
- `euthernet://server/summary`
- `euthernet://server/repos`

Tools:

- `refresh_inventory`
- `status`
- `ask`
- `run_command`

## Local HTTP API

Run the local HTTP API:

```sh
make serve
```

Default bind address:

```text
http://127.0.0.1:8791
```

When EutherNet runs on the workstation, `euthernet.toml` uses
`command_transport = "ssh"` and collects from the server through pubkey SSH.
When EutherNet runs on the EutherOxide server itself, use
`deploy/euthernet.server.toml`; it uses `command_transport = "local"` and does
not need an SSH agent.

Endpoints:

```text
GET  /api/euthernet/status
GET  /api/euthernet/repos
GET  /api/euthernet/inventory
GET  /api/euthernet/commands
GET  /api/euthernet/report
GET  /api/euthernet/summary
GET  /api/euthernet/changes
GET  /api/euthernet/restore-plan
GET  /api/euthernet/restore-bundle?profile=full
GET  /api/euthernet/restore-bundle?profile=backup
POST /api/euthernet/ask
POST /api/euthernet/refresh
POST /api/euthernet/run
```

Examples:

```sh
curl -fsS http://127.0.0.1:8791/api/euthernet/status
curl -fsS http://127.0.0.1:8791/api/euthernet/repos
curl -fsS http://127.0.0.1:8791/api/euthernet/report
curl -fsS http://127.0.0.1:8791/api/euthernet/summary
curl -fsS http://127.0.0.1:8791/api/euthernet/changes
curl -fsS http://127.0.0.1:8791/api/euthernet/restore-plan
curl -fsS 'http://127.0.0.1:8791/api/euthernet/restore-bundle?profile=full'
curl -fsS 'http://127.0.0.1:8791/api/euthernet/restore-bundle?profile=backup'
curl -fsS -X POST http://127.0.0.1:8791/api/euthernet/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"vilka repos är dirty?"}'
curl -fsS -X POST http://127.0.0.1:8791/api/euthernet/run \
  -H 'Content-Type: application/json' \
  -d '{"name":"disk"}'
```

`/api/euthernet/run` accepts only configured command names from the allowlist.
It does not accept raw shell commands.

## EutherPunk Integration Shape

EutherPunk should call the local HTTP API instead of reading EutherNet files or
opening SSH itself.

Recommended config shape:

```toml
[euthernet]
enabled = true
url = "http://127.0.0.1:8791"
```

Recommended EutherPunk chat tools:

- `server_status` -> `GET /api/euthernet/status`
- `server_repos` -> `GET /api/euthernet/repos`
- `server_full_report` -> `GET /api/euthernet/report`
- `server_summary` -> `GET /api/euthernet/summary`
- `server_changes` -> `GET /api/euthernet/changes`
- `server_restore_plan` -> `GET /api/euthernet/restore-plan`
- `server_restore_bundle` -> `GET /api/euthernet/restore-bundle?profile=full|backup`
- `server_refresh` -> `POST /api/euthernet/refresh`
- `server_run` -> `POST /api/euthernet/run`

For the first chat UI pass, slash commands are enough:

```text
/server status
/server repos
/server summary
/server changes
/server restore plan
/server restore bundle
/server restore bundle backup
/server full report
/server refresh
/server run disk
```

The LLM can later suggest these same tool calls, but the server should continue
to enforce the allowlist.

## Server Service

Install the user service on the EutherOxide server:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/euthernet.service ~/.config/systemd/user/euthernet.service
cp deploy/euthernet-refresh.service ~/.config/systemd/user/euthernet-refresh.service
cp deploy/euthernet-refresh.timer ~/.config/systemd/user/euthernet-refresh.timer
systemctl --user daemon-reload
systemctl --user enable --now euthernet.service
systemctl --user enable --now euthernet-refresh.timer
systemctl --user status euthernet.service
systemctl --user list-timers euthernet-refresh.timer
```

Verify locally on the server:

```sh
curl -fsS http://127.0.0.1:8791/api/euthernet/status
```

## Fresh Hardware Restore Bundle

The restore bundle is intended for a fresh Debian install where Codex should
bring the server back in a deterministic order.

On the new machine:

```sh
git clone https://github.com/NichlasEk/EutherNet /home/nichlas/EutherNet
cd /home/nichlas/EutherNet
make restore-bundle PROFILE=full
```

Then start Codex in `/home/nichlas/EutherNet` and give it the generated Codex
prompt. Codex should follow the runbook chronologically, run the bootstrap
script step by step, and stop if secrets, private keys, or backups are missing.

Profiles:

- `full`: restore the EutherNet control plane, clone all remote-backed repos
  from the latest inventory, and continue service-specific recovery from each
  repo's own deploy docs.
- `backup`: restore the EutherNet control plane and key Euther repos for a
  smaller diagnostics/backup host.

The bundle separates base packages from observed packages:

- Base packages are the small apt set installed by the bootstrap script.
- Service package candidates come from the generated service-aware restore
  catalog.
- Observed packages come from the latest server snapshot using `dpkg-query` or
  `pacman -Q`. Treat them as a comparison target, not a blind install list.

The restore bundle currently emits service-aware restore steps for known repos
when they appear in the latest inventory: EutherNet, EutherPunk, EutherOxide,
and EutherBooks. Each service entry includes package candidates, persistent
paths, ordered commands, verification commands, and notes for Codex.

## Safety Rules

- Default collectors are read-only.
- No sudo commands are used by the first collector.
- No password, passphrase, token, private key, or raw secret-bearing output
  should be committed.
- Future write actions must be explicit and disabled by default.
