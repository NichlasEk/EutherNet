# Eutherium Award Flow

This document describes how another local project can award Eutherium through
EutherNet without learning EutherOxide internals. EutherPal is the first live
caller, but the same shape should be reused by future games, tools, and social
apps.

## Ownership Boundary

- EutherOxide owns EutherHost users, passwords, app login, the Eutherium ledger,
  balances, user data folders, and UI surfaces.
- EutherNet is the local backbone endpoint for project-to-project awards. It
  validates the recipient against EutherOxide host users, appends ledger entries,
  and writes a per-user account log.
- Calling apps should not edit EutherOxide files directly. They should call
  EutherNet and let it perform validation, idempotency, and logging.

## Current Live Shape

- EutherNet award endpoint: `POST /api/euthernet/eutherium/award`
- Typical local URL on the server: `http://127.0.0.1:8791/api/euthernet/eutherium/award`
- EutherNet config path: `euthernet.toml`
- Eutherium host state root: `[eutherium].host_dir`, currently expected to point
  at `/home/nichlas/EutherOxide/.euther-host` on the server.

## Recipient Eligibility

A recipient is eligible when:

1. The username exists in EutherOxide `.euther-host/users.toml`.
2. The user is not marked `banned = true`.
3. The caller is making a server-side award for a real project event.

There is intentionally no separate EutherPal permission today. A newly created
EutherHost user can receive Eutherium awards once EutherOxide knows about the
user and EutherNet can read that host user file.

`can_award_eutherium` is for admin/tools that are allowed to issue awards, not
for ordinary users receiving a prize.

## Caller Login Pattern

For apps with human clients, use EutherOxide login to bind a local player or
profile to a real EutherHost user:

1. The mobile/client app asks for EutherHost username and password.
2. The project server calls EutherOxide `/api/app/login`.
3. EutherOxide verifies the Argon2 password hash in `.euther-host/users.toml`.
4. The project stores only the verified username or an app token, depending on
   the project needs. It should not store the plaintext password.
5. When a prize event happens, the project sends the verified username to
   EutherNet as the award recipient.

EutherPal currently uses this model: players may call themselves anything in the
game, but the server also knows which verified EutherHost user should receive a
winner prize.

## Award Request

Minimum request body:

```json
{
  "user": "nichlas",
  "amount": 1000,
  "reason": "EutherPål vinst i rum PAL-001",
  "source": "eutherpal",
  "idempotencyKey": "PAL-1700000000000-nichlas-win"
}
```

Optional fields:

```json
{
  "createdBy": "eutherpal"
}
```

Field rules:

- `user`: EutherHost username. `userId` is also accepted for compatibility.
- `amount`: integer EUX amount. It should be positive for awards.
- `reason`: human-readable explanation shown in ledgers and account logs.
- `source`: stable project id, for example `eutherpal`, `eutherdogs`,
  `euthersight`, or `eutherpunk`.
- `createdBy`: service/admin id that issued the award. Use the project service
  name unless a real admin user is relevant.
- `idempotencyKey`: required for practical integrations. It should represent the
  unique event, not just the user.

Good idempotency examples:

- `PAL-<game_id>-<user>-win`
- `dogshow-<season>-<round>-<user>-first-place`
- `quest-<quest_id>-<user>-completion`

Bad idempotency examples:

- `nichlas`
- `winner`
- current timestamp generated after every retry

## EutherNet Behavior

On a valid new award, EutherNet:

1. Loads EutherOxide host users from `[eutherium].host_dir/users.toml`.
2. Rejects unknown or banned users.
3. Computes an idempotent ledger entry id from `source` and `idempotencyKey`.
4. Appends to `.euther-host/eutherium/ledger.json`.
5. Appends a readable account entry to
   `.euther-host/user-data/<user>/eutherium/account-log.toml`.
6. Returns JSON including whether the award was new and the resulting balance.

On duplicate idempotency key, EutherNet returns success-like JSON with no second
ledger append. Callers can safely retry after timeouts.

## Account Log

Each successful new award writes an entry like this to the user TOML account
log:

```toml
[[entry]]
id = "euthernet-eutherpal-PAL-1700000000000-nichlas-win"
type = "award"
source = "eutherpal"
reason = "EutherPål vinst i rum PAL-001"
amount = 1000
created_by = "eutherpal"
idempotency_key = "PAL-1700000000000-nichlas-win"
project = "EutherPål"
event = "game_won"
```

The log is meant to be readable by humans and future admin UI. It is not a
replacement for `ledger.json`; it is a per-user explanation trail.

## Example Curl

```sh
curl -fsS -X POST http://127.0.0.1:8791/api/euthernet/eutherium/award \
  -H 'Content-Type: application/json' \
  --data '{
    "user": "nichlas",
    "amount": 1000,
    "reason": "EutherPål vinst i rum PAL-001",
    "source": "eutherpal",
    "createdBy": "eutherpal",
    "idempotencyKey": "PAL-example-nichlas-win"
  }'
```

Use a throwaway test user or a clearly named low-value test event for live
probes. Avoid issuing real prize amounts just to test connectivity.

## Adding A New Project

For a new project, do this:

1. Add client login or account linking against EutherOxide `/api/app/login` if
   human identity matters.
2. Store the verified EutherHost username with the local project profile.
3. Define project-specific award rules in the project config or TOML. Keep the
   prize amounts visible and editable.
4. Call EutherNet `/api/euthernet/eutherium/award` from server-side code only.
5. Use stable `source` and idempotency keys.
6. Surface success/failure to the user, but do not block gameplay or UI on slow
   award paths unless the award itself is the whole action.
7. Add one project-specific smoke test that confirms the generated request body,
   without awarding real EUX.

## Operational Checks

After deployment or when debugging:

```sh
curl -fsS http://127.0.0.1:8791/api/euthernet/health
curl -fsS http://127.0.0.1:8791/api/euthernet/map | head
```

For award checks, prefer a known test user and tiny amount. Confirm:

- EutherNet returns HTTP 200 for a valid user.
- Duplicate idempotency key does not append a second ledger row.
- Unknown or banned users are rejected.
- The user account log receives a readable entry for new awards.

## Security Notes

- Do not commit passwords, API tokens, SSH passphrases, or raw production user
  files.
- Do not let browser clients call the award endpoint directly. The caller should
  be a trusted local service.
- Do not grant Eutherium based only on a display name. Always resolve to a real
  EutherHost user first.
- Keep EutherNet local-only unless a later auth layer is added for remote
  project callers.
