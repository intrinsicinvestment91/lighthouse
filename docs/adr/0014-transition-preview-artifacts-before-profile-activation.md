# ADR-0014 — Transition preview artifacts required before profile activation

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-03
- **Related ADRs:** ADR-0001 (Lighthouse owns the engine, Gateway is the control plane), ADR-0003
  (exactly one active profile, controlled switch), ADR-0013 (verified backup + isolated restore
  drill before profile activation)
- **Supersedes:** —
- **Superseded-by:** ADR-0015, in part — the digest-exclusion description below omits
  `evidence_timestamp` (always excluded in code) and predates the LHP-PROFILE-14A additive
  extension (producer-owned execution facts, serialization v2). The decision itself stands.

## Context

ADR-0003 established that exactly one profile is active at a time and switching is a controlled,
backup-gated transition. ADR-0013 added the evidence that state was actually captured and is
recoverable. What was still missing is the *review* layer: a switch must never begin with
"Activate profile X." It must begin with "Generate a complete transition preview" — a reviewable
description of what would change, what evidence supports the change, and what a human/Gateway must
approve. Approval and activation are future capabilities; the reviewable object that precedes them
did not yet exist.

## Decision

- Lighthouse gains a **deterministic transition-preview artifact** (`profile_transition_preview.v1`),
  implemented as a fail-closed module (`modules/profile_transition.py`) and one read-only CLI
  command (`profile-transition-preview`).
- The preview is **evidence, not a command**: it never activates a profile, never switches, never
  writes DB state, never executes a collector, and never contacts the Gateway. It only describes a
  proposed transition and names the approvals a future Gateway would have to collect.
- A preview reaches `ready_for_review` **only** when both profiles are valid and present, the
  schema is supported, required policy information is present, and backup evidence — a manifest id,
  a passing verification (`verified`), and a completed restore drill (`completed`) — is present and
  passing. Otherwise: missing evidence → `incomplete`; failed/unknown evidence → `blocked`;
  malformed/unavailable profile or unsupported schema → `invalid`. **Unknown never becomes
  `ready_for_review`.**
- The preview **consumes** LHP-PROFILE-02 evidence by reference (manifest id + report statuses)
  and never recreates or duplicates backup logic, database contents, file contents, or secrets.
- Profile comparison is **deterministic and declarative**: it describes differences in sources,
  collectors, graph-entity policy, correlation policy, privacy policy, publishing policy,
  local-only restriction, and DMS eligibility. It executes no change.
- The artifact carries a SHA-256 `artifact_digest` over a canonical serialization that excludes
  volatile identity fields (`preview_id`, `created_at`), so the same transition + evidence yields
  the same digest.
- **Lighthouse generates evidence; the Gateway remains the future approval authority.** The
  approval *record* (operator identity, approval timestamp, policy decision, execution
  authorization) is Gateway-owned and is **not** created by this packet.
- **This packet implements no profile activation, switching, or approval.** There is still no
  active-profile state; `current_profile` is a declared placeholder that is `None` until activation
  ships.

## Consequences

- Activation (ADR-0003) gains an explicit review precondition: a reviewable preview whose
  `transition_status` is `ready_for_review`, produced from readiness (ADR-0004) + verified backup
  and restore drill (ADR-0013).
- The command is safe to run anytime: read-only, deterministic, no mutation, no network, no
  Gateway contact. Running it with no evidence is the normal first step, not an error.
- A future Gateway approval workflow can consume the preview (and its digest) as the object under
  review without Lighthouse gaining any execution authority.

## Repository evidence

- `modules/profile_transition.py` (contract + comparison + evidence loader + builder);
  `docs/schemas/profile_transition_preview.v1.schema.json` (reference mirror; the dataclass
  `validate()` is authoritative).
- CLI: `profile-transition-preview` in `main.py`.
- Tests: `tests/unit/test_profile_transition.py`, `tests/unit/test_cli_profile_transition.py`.
- `docs/BACKUP_READINESS_AND_PROFILE_SWITCHING.md`; `docs/CAPABILITIES.md`.
