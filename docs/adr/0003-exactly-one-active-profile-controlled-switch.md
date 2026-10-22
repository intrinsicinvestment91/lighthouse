# ADR-0003 — Exactly one active profile; switching is a controlled, backup-gated transition

- **Status:** Accepted — implementation deferred
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

A profile change alters how new observations and findings are derived. If switching were an in-place
edit, a change could silently reinterpret or rewrite past intelligence, and two profiles could appear
active at once. The contract already distinguishes five states (Installed, Validated, Resolved,
Proposed, Active) but ships only the first two.

## Decision

- **At most one profile is active at any time.**
- Switching is a *controlled state transition*, not an in-place edit:
  `deactivate current → (fail-closed backup gate) → activate next`. It is reversible and audited, and
  must be preceded by a successful backup (ADR-0004).
- Historical records must eventually retain **which profile and derivation version produced them**
  (`profile_id` + `derivation_version`) so a switch never reinterprets past intelligence.

**Deferred:** no activation mechanism, no `active_profile` record, and no provenance columns ship yet.
Activation is a future, separately-approved packet. The Gateway authorizes and audits the switch
(ADR-0001); Lighthouse enforces the backup gate and stores its own evidence.

## Consequences

- Profile activation work is unblocked in design but explicitly not implemented; docs must not claim
  activation exists.
- The future DB migration that adds derivation provenance is pre-committed in principle here.

## Repository evidence

- `docs/INTELLIGENCE_PROFILE_ARCHITECTURE.md` (five-state model; "exactly one active profile";
  historical provenance requirement — "This packet ships no activation mechanism").
- `modules/profiles.py` `ALLOWED_STATUSES = {"draft", "experimental", "ready_for_validation"}`
  (deliberately no `active`/`production` status).
- `docs/BACKUP_READINESS_AND_PROFILE_SWITCHING.md` (switch workflow, backup gate).

## Superseded in part by ADR-0024 (2026-09-02, LHP-LIFE-DESIGN-01)

[ADR-0024](0024-controlled-profile-switch-lifecycle.md) supersedes **only** the literal
`deactivate current → (fail-closed backup gate) → activate next` sequence in the Decision above.
That deactivate-first ordering would persist `no_active_profile` mid-flight and violate the
singleton invariant (INV-1/INV-2 in ADR-0024). The replacement is a fenced, compare-and-set
active-to-active cutover that never passes through absence.

**What survives intact from this ADR:**

- Exactly one authoritative active profile at any time.
- Fail-closed backup gating on every switch ([ADR-0004](0004-backup-readiness-fail-closed-data-boundary.md)).
- Historical `profile_id` + `derivation_version` provenance on records.
- Gateway authorization and audit for switches that widen exposure ([ADR-0001](0001-lighthouse-owns-engine-gateway-is-control-plane.md)).

**What is partially superseded:**

- The 2026-07-10 "Deferred: no activation mechanism, no `active_profile` record" line was true
  when written. Narrow backup-gated initial activation of `repository_capability_discovery` v2 is
  now shipped (`LHP-V1-PROFILE-01C/01E`). Provenance columns and active-to-active switching
  remain unbuilt.

The front-matter `Superseded-by` field was left unchanged deliberately under LHP-LIFE-DESIGN-01's
append-only constraint; see the ADR index row for the current supersession note.
