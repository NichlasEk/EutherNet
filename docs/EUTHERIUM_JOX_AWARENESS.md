# Eutherium JOX Awareness

EutherNet treats Eutherium, Joxbox, and `.jox` artifacts as a stateful domain
inside EutherOxide. EutherOxide still owns the economy, HTTP API, artifact
mutation rules, and trophy-room UI. EutherNet's job is to remember what must be
mapped, backed up, restored, and explained.

## Domain Boundary

- EutherOxide serves Eutherium through `/api/eutherium/*`.
- Joxbox lives under `/api/shop/joxbox/*`.
- `.jox` files are self-contained artifact containers with payload metadata,
  embedded image assets, integrity hashes, ownership history, and a mutation log.
- Trophy rooms use user-owned inventory plus per-user room layout state.

## Persistent State

These paths are part of the EutherOxide host state and should be treated as
critical during backup and restore:

- `/home/nichlas/EutherOxide/.euther-host/eutherium/ledger.json`
- `/home/nichlas/EutherOxide/.euther-host/eutherium/inventory.json`
- `/home/nichlas/EutherOxide/.euther-host/eutherium/jox-shop.json`
- `/home/nichlas/EutherOxide/.euther-host/eutherium/jox-offers.json`
- `/home/nichlas/EutherOxide/.euther-host/eutherium/joxbox`
- `/home/nichlas/EutherOxide/.euther-host/users/*/eutherium/trophy-room.json`

## Provenance Rule

Unknown or tampered `.jox` files may still exist, be shown, and be socially
traded. Eutherium sale value depends on valid provenance: payload hash, asset
hash, and ownership/mutation chain must still match the trusted local state.

## EutherNet Map

The deterministic server map adds explicit nodes for:

- `Eutherium Economy`
- `Joxbox`
- `.jox Container`
- `JOX Provenance`
- `Trophy Rooms`

Those nodes hang under EutherOxide so the map shows that the economy is not just
static web UI. It is mutable host state that must survive restore.

## Related Runtime Maps

- [Local AI Compatibility Map](LOCAL_AI_COMPATIBILITY_MAP.md) records the
  runtime model-profile shims that SecondSight depends on without committing
  local ComfyUI profile files or model weights.
