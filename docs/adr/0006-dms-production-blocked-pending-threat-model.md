# ADR-0006 — DMS production use is blocked pending a threat model

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

A Dead-Man's-Switch (DMS) mechanism exists in the portal (`portal/dms_checker.py`, `--module dms-check`,
`dms_registrations`/`dms_broadcasts` tables). It can broadcast stored ciphertext to Nostr once a
deadline lapses **with no score, approval, or witness gate** — unlike the four-condition correlation
publish gate. That is a highest-severity release-safety risk.

## Decision

- **DMS production use is blocked.** No timed-release behavior may ship until a dedicated threat model
  and release state-machine initiative is completed. Uncertainty must **delay** release, never
  accelerate it.
- Profiles may declare **DMS eligibility only** (`dead_mans_switch_policy.eligible`, boolean, default
  `false`). The contract **rejects** any enrollment/activation declaration
  (`enrolled/activated/active/enabled/armed/scheduled`). No profile can enroll itself.
- Release policy, when it exists, is Gateway-owned (ADR-0001) and must fail closed (ADR-0007).

## Consequences

- The existing DMS code remains present but must not be operated in production; this is a policy block,
  not a code removal.
- Phase J of the roadmap is explicitly blocked on this ADR.

## Repository evidence

- `docs/PROFILE_SECURITY_RISKS.md` risk 1 ("DMS production use is blocked").
- `modules/profiles.py` (DMS eligibility boolean; enrollment keys rejected).
- `portal/dms_checker.py`, `portal/app.py` (mechanism present; `NOSTR_PRIVATE_KEY_HEX` required to
  broadcast).
