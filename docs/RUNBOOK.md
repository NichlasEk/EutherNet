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

## Safety Rules

- Default collectors are read-only.
- No sudo commands are used by the first collector.
- No password, passphrase, token, private key, or raw secret-bearing output
  should be committed.
- Future write actions must be explicit and disabled by default.
