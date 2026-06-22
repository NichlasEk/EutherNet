# EutherNet Autonomy Plan

This checklist tracks the next EutherNet/EutherPunk integration slice.

## Goals

- Keep EutherNet inventory fresh without manual chat commands.
- Add short operational summaries for daily use.
- Detect drift between snapshots.
- Generate restore guidance from inventory.
- Let EutherPunk route natural server questions to EutherNet, while preserving the command allowlist.

## Checklist

- [x] Add a systemd user timer for periodic EutherNet refresh.
- [x] Add `/api/euthernet/summary` and `/server summary`.
- [x] Add `/api/euthernet/changes` and `/server changes`.
- [x] Add `/api/euthernet/restore-plan` and `/server restore plan`.
- [x] Add natural-language EutherPunk routing for common server questions.
- [x] Verify Python and Go tests locally.
- [x] Commit and push EutherNet changes.
- [x] Commit and push EutherPunk changes.
- [x] Deploy both services to `192.168.32.186`.
- [x] Run live smoke tests through EutherPunk chat.

## Safety Rules

- Keep EutherNet read-only by default.
- Keep remote commands behind named allowlist entries.
- Do not expose arbitrary shell execution to EutherPunk or the model.
- Store live server output under ignored state paths, not tracked inventory.
