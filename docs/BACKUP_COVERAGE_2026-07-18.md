# Backup coverage audit — 2026-07-18

This is a secret-free, read-only inventory of durable EutherVerse data on:

- `192.168.32.186` (`EutherServer`)
- `192.168.32.88` (`apansson`)

The audit compares the live EutherNet persistent-path catalog with active
systemd backup timers, observed backup artifacts, checksum/restore-test output,
path existence, and approximate disk usage. Sizes are point-in-time values and
may change.

## Status vocabulary

- **Protected**: an automatic backup exists on another machine and its current
  verification succeeds.
- **Rebuildable**: source or generated state can be recreated from tracked code
  and documented dependencies. Keeping a cache may save time, but it is not the
  only copy of irreplaceable data.
- **No backup**: no automatic off-host copy was observed. This includes paths
  that might later be declared disposable; until that decision is explicit,
  the conservative classification is no backup.

## Executive result

| Status | Result |
| --- | --- |
| Protected | EutherID state/secrets and EutherHost `users.toml` |
| Rebuildable | EutherNet snapshots, build targets, downloaded models/tools |
| No backup | Application content, Eutherium/Joxbox history, chat/images, sync feed, books/audio, music, ROMs, and `.88` camera/SecondSight state |

The two existing protected flows are healthy. The largest immediate risk is
not account recovery; it is the body of application and provenance data outside
those two narrow backup scopes.

## Protected now

| Owner | Data | Server copy | Off-host copy | Verification |
| --- | --- | --- | --- | --- |
| EutherID | SQLite state, `/etc/eutherid`, service unit | Daily archive under `/srv/backups/eutherid`, 30-day retention | Daily pull to `.88` under `Backups/EutherID` | SQLite integrity check, archive checksum, and off-host restore test; two current archives observed |
| EutherHost | `.euther-host/users.toml` | Daily age-encrypted file under `/srv/backups/eutheroxide`, 30-day retention | Restricted read-only pull to `.88` under `Backups/EutherOxide` | Age header, SHA-256, freshness, timer state; two current encrypted copies observed |

Neither flow stores a decryption passphrase in its automated pull job.

## Rebuildable data

| Host | Path | Approx. size | Reason |
| --- | --- | ---: | --- |
| `.186` | `/home/nichlas/EutherNet/state` | small/runtime | Inventory snapshots can be recollected |
| `.186` | `/srv/eutheroxide/target` | 8.9 GiB | Rust/build output |
| `.186` | `/srv/eutheroxide/src-tauri-target` | 1.9 GiB | Tauri build output |
| `.186` | `/home/nichlas/EutherBooks/models` | 121 MiB | Downloadable model assets |
| `.186` | `/home/nichlas/EutherBooks/tools` | 77 MiB | Reinstallable tooling |

`/srv/eutheroxide/apps` (4.0 GiB) and `/srv/eutheroxide/euther-openra`
(781 MiB) are not marked rebuildable yet. Their contents should first be
proven reproducible from repositories and release inputs.

## No automatic off-host backup observed on `.186`

| Priority | Service | Path or scope | Approx. size | Why it matters |
| --- | --- | --- | ---: | --- |
| P0 | EutherOxide | `.euther-host` except protected `users.toml` | 366 MiB | Contains Eutherium ledger/inventory/shop/Joxbox, user data, shopping lists, social chat, audit/action state, and runtime configuration |
| P0 | EutherSync | `/home/nichlas/euthersync-storage` | 337 MiB | User feed, devices, library and sync metadata |
| P0 | EutherPunk | `var/chats`, `var/settings`, `var/images` | 674 MiB | Private conversation state, settings and generated images |
| P0 | EutherBooks | `library` and `data` | 76 MiB | Source library and processing metadata needed to reproduce books/audio |
| P0 | EutherStudio | `/srv/eutherstudio/users` | 182 MiB | Per-user generated music and metadata |
| P0 | EutherOxide | `/home/nichlas/roms` | 660 MiB | User-supplied runtime content; not recoverable from git |
| P1 | EutherBooks | `/srv/eutherbooks/audio` | 25 GiB | Generated audio is reproducible only if source library, settings and TTS stack survive; regeneration is expensive |
| P1 | EutherOxide | `.euther-bridge` | 20 KiB | Small host integration state; cheap to include |
| P1 | EutherPal | `data`, `config`, deployed user units | tens of KiB | Small but service-specific state |
| P1 | EutherPunk | `~/.config/eutherpunk` | 8 KiB | Runtime configuration |
| P1 | EutherSync | deployed system unit/drop-ins | small | Required to reproduce the live service boundary |
| P1 | EutherStudio | `/srv/eutherstudio/config` and `jobs` | about 208 KiB | Runtime configuration and job metadata |
| Review | EutherOxide | `/srv/eutheroxide/apps` and `euther-openra` | 4.8 GiB | Treat as unprotected until a clean rebuild proves it disposable |

The broad `/srv` entry in the older manifest is too coarse. It mixes protected
archives, rebuildable build output, and irreplaceable application content. New
backup work should use explicit include paths rather than copying all of `/srv`.

## `.88`-owned data

The EutherSight entries in the server restore catalog do not exist on `.186`;
they belong to `.88` and must not be reported as missing server paths.

| Priority | Path | Approx. size | Classification |
| --- | --- | ---: | --- |
| P0 | `/run/media/nichlas/Titan/SecondSight` | 965 MiB | No backup; provenance-bearing `.jox`/SecondSight state |
| P0 | `/home/nichlas/EutherSight/config`, `.env`, `secondsight.toml` | 42 MiB | No backup; live camera and worker configuration |
| P1 | `/home/nichlas/EutherSight/.euthersight-ai` | 103 MiB | No backup observed; split configuration/state from rebuildable caches before backing up |
| P1 | `/home/nichlas/ai/eutherbird` | 2.5 GiB | No backup observed; inventory model/cache versus observation state before inclusion |
| Policy | `/run/media/nichlas/Titan/Camera_feed` | 752 GiB | No second copy observed; continuous video needs a retention policy, not an unconditional full mirror |

The Titan volume is a separate 7.3 TiB Btrfs filesystem, but a separate disk is
not an off-host backup. Hardware loss, theft, fire, or filesystem damage can
still remove both live camera and SecondSight state.

## Recommended implementation order

1. **Small critical server set**: create one encrypted, explicit-path backup
   covering Eutherium/Joxbox, EutherSync, EutherPunk, EutherBooks source/data,
   EutherStudio users/config, EutherPal state, runtime units, and `.euther-bridge`.
   Mirror it from `.186` to `.88` using the existing restricted-pull pattern.
2. **ROMs**: include separately so retention and legal/user-content handling can
   differ from private application state.
3. **EutherBooks audio**: decide between backing up all 25 GiB or backing up only
   source/settings plus a documented regeneration drill.
4. **SecondSight**: push or pull the 965 MiB dataset from `.88` to a protected
   area on `.186` until the garage NAS becomes the third copy.
5. **Camera feed**: define retention tiers (recent full video, event clips,
   metadata/observations) before adding backup traffic.
6. **Garage NAS**: make it the third copy with pull-only credentials and
   versioned or append-only snapshots.

## Acceptance gates for the next backup job

- Explicit allowlisted paths; no blanket `/home`, `/srv`, or Titan copy.
- Consistent capture for SQLite/databases before archiving.
- Encryption before data leaves its owner host.
- Atomic output plus SHA-256 verification.
- Restricted pull identity with no shell and no remote deletion.
- Retention on the producing host; no automatic deletion on the mirror.
- Six-hour health check and a visible EutherNet status.
- A restore drill that checks structure and application-specific integrity, not
  merely that an archive can be listed.

## Evidence snapshot

- Audit time: 2026-07-18, Europe/Paris.
- Live EutherNet snapshot: `2026-07-18T14:49:31+00:00`.
- EutherNet manifest reported 22 critical paths and 3 rebuildable paths before
  coverage was compared with real backup jobs.
- `.186` `/srv`: 837.7 GiB filesystem, about 40.5 GiB used.
- `.88` Titan: 7.3 TiB filesystem, about 2.6 TiB used.
- Only EutherID and EutherHost-user backup timers were observed on `.186`.
- EutherID off-host and EutherHost mirror timers were observed on `.88`.

