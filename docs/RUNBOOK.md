# EutherNet Runbook

## Local Inventory Run

Unlock the SSH key for the EutherOxide server before running the collector:

```sh
ssh-add ~/.ssh/euther_server
```

Then run:

```sh
make inventory
```

The collector uses `BatchMode=yes`, so it will not prompt for a password or
passphrase. If the key is not available through the agent, it fails fast and
writes a preflight error instead of repeatedly trying server commands.

## Outputs

- `inventory/server.md`: reviewed human-readable inventory.
- `inventory/server.toml`: structured inventory summary.
- `state/snapshot-*.json`: ignored runtime snapshot for local diagnosis.

The `state/` directory is intentionally ignored by git. Review generated
inventory before committing it.

## Safety Rules

- Default collectors are read-only.
- No sudo commands are used by the first collector.
- No password, passphrase, token, private key, or raw secret-bearing output
  should be committed.
- Future write actions must be explicit and disabled by default.
