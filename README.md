# EutherNet

EutherNet is planned as a local-first mapping and diagnostics service for the
EutherOxide host server. Its job is to keep a readable server map, a structured
TOML inventory, and an MCP-facing interface that can help diagnose, maintain,
and eventually rebuild the server if the hardware fails.

The first target host is the EutherOxide server on the LAN. Public-facing
details can be documented here, but credentials, private keys, sudo passwords,
tokens, and raw dumps from live services must stay outside git.

## Starting Points

- [docs/EUTHERNET_PLAN.md](docs/EUTHERNET_PLAN.md) captures the staged plan.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) explains how to run the first collector.
- [examples/euthernet.example.toml](examples/euthernet.example.toml) sketches
  the intended configuration shape.

## First Collector

```sh
make inventory
```

The collector is read-only, uses SSH public key authentication, and fails fast
if the server key is not available through the local SSH agent.

## Working Rule

Treat this repository as the durable public/private-safe control plane:
architecture, commands, schemas, inventory format, runbooks, and generated
summaries belong here. Secrets and machine-local runtime state do not.
