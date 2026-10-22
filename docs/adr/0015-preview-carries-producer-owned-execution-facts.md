# ADR-0015 — Transition preview carries producer-owned execution facts (additive, serialization v2)

- **Status:** Accepted
- **Date:** 2026-07-12
- **Related packets:** LHP-PROFILE-14A (first slice of the producer bundle ordered by
  LHP/GW-DIRECTION-01)
- **Related ADRs:** ADR-0001 (Lighthouse owns the engine, Gateway is the control plane),
  ADR-0013 (verified backup + restore drill before activation), ADR-0014 (transition preview
  artifacts before activation)
- **Supersedes:** ADR-0014 in part — the digest-exclusion description only (see below)
- **Superseded-by:** —

## Context

The Gateway's shipped governance stack (its approval, intake, execution-plan, and execution-
authorization contracts) consumes the `profile_transition_preview.v1` artifact but had to
*recover* execution-relevant meaning from it: reading `local_only` out of a policy-diff dict,
matching the exact prose string `"operator approval: dead-man's-switch eligibility change"`,
prefix-matching `"target data_root resolves under /data ("`, and *inferring* the
schema-migration answer from foundation status. Its execution-authorization decision record
(§16) explicitly asked the producer to replace that recovery with first-class typed fields.
Separately, the backup manifest was bound by `manifest_id` only (a disclosed content-binding
gap), the preview carried no producer provenance, and the profile content outside the eight
diffed policy categories was invisible to the digest.

## Decision

- **Additive evolution within `schema_version` 1.** All four shipped Gateway consumers read
  the preview as a plain dict via `.get()` (audited 2026-07-12); additive keys are inert for
  them, while a v2 bump would be rejected wholesale by their fail-closed version gates. The
  contract's own `deterministic_serialization_version` field — built for exactly this — is
  bumped **1 → 2** because the digest content composition changed; future values fail closed.
  `generator_version` becomes `1.1.0`.
- The preview gains **producer-owned execution facts**, all digest-covered:
  - `target_policy_snapshot` — exactly `{local_only, dms_eligibility, data_root,
    schema_migration_required}` (the Gateway's §16 ask, verbatim field names), derived only
    from a **validated** target profile, else `null`. `schema_migration_required` is `False`
    by construction: `intelligence_profile.v1` defines no migration vocabulary and rejects
    executable intent.
  - `changes.local_only_changed` and `changes.dms_eligibility_changed` — the two scalar
    diffs the producer always computed but never serialized (8-of-8 categories now on the
    wire). They are **siblings** of the six dict-diff categories, never nested inside them
    (the Gateway's retention scan inspects key names inside those four blocks).
  - `evidence.backup_manifest_sha256` — SHA-256 over the referenced manifest file bytes,
    upgrading evidence binding from id-only to content binding.
  - `target_profile_sha256` — SHA-256 over the target profile's full canonical serialized
    content, binding the policy blocks the diff does not cover
    (observation/temporal/interface/backup/compatibility), so a consumer can prove the
    profile is byte-identical to the one reviewed by recomputing from the live registry.
  - `lighthouse_version` + `repository_commit` — provenance stamps (nullable when
    distribution metadata / git are absent, e.g. s9pk deployments), sourced from
    `modules/backup_manifest.py` detection helpers shared with the backup manifest.
- **Digest coverage is deliberate for the stamps.** The digest's stability promise is
  time-invariance (same transition + evidence ⇒ same digest), **not** install-invariance.
  Uncovered provenance would be tamper-editable and therefore useless to its consumers (a
  future execution ledger and activation-ceremony revalidation). The full, corrected
  exclusion list is exactly: `preview_id`, `created_at`, `evidence_timestamp`, and
  `artifact_digest` itself. (ADR-0014 described the exclusions as `preview_id`/`created_at`
  only; `evidence_timestamp` was always excluded in code — this ADR supersedes that
  description.)
- **`ready_for_review` is strictened unconditionally**: it now additionally requires the
  typed snapshot and both content digests. The rule is *not* gated on
  `deterministic_serialization_version`, which is self-attested by the artifact and would be
  a permanent forgeable downgrade knob. Consequence: a pre-14A artifact claiming `ready`
  fails validation under new code (previews are ephemeral review artifacts — regenerate);
  pre-14A `incomplete`/`blocked`/`invalid` artifacts still validate.
- **Evidence status vocabulary is contract-documented, not enum-enforced at runtime.** The
  pass sets stay module constants (`VERIFICATION_PASS_STATUSES = {"verified", "passed"}` —
  two tokens because two legitimate report types exist; `RESTORE_DRILL_PASS_STATUS =
  "completed"`) and are now pinned in the schema mirror. A runtime enum over verbatim
  report statuses would false-reject faithful `blocked`/`incomplete` previews.
- **Legacy vocabulary freeze.** Until the Gateway companion change (PROFILE-14B) consumes
  the typed snapshot, the shipped Gateway classifier still matches the two prose tokens
  above verbatim. This packet freezes them with a consumer-contract test; they must not be
  reworded before the companion lands.

## Consequences

- The Gateway companion (PROFILE-14B) can retire its string-token recovery by reading
  `target_policy_snapshot` and the two scalar change categories; the execution ledger
  (PROFILE-12/13) can record content-bound evidence and provenance; the first activation
  ceremony (PROFILE-15) can re-hash the manifest, recompute the profile digest against the
  live registry, and compare producer version at preview time vs execution time.
- Fresh previews of identical transitions produced by *different* installs now carry
  different digests (correct: different producer = different artifact); within one install
  the digest remains time-invariant, and no test or consumer pinned concrete digest values.
- The backup manifest's `lighthouse_version` field is truthful for the first time: the
  detection helper previously queried the non-existent distribution `"lighthouse"` and
  always returned null; it now queries `pattern-match-sentinel`, and `generate_manifest`
  also defaults `repository_commit` from git when the caller supplies none.

## Repository evidence

- `modules/profile_transition.py` (snapshot/profile-digest helpers, extended contract,
  strictened `validate()`); `modules/backup_manifest.py` (`detect_lighthouse_version`,
  `detect_repository_commit`, truthful `generate_manifest` defaults).
- `docs/schemas/profile_transition_preview.v1.schema.json` (reference mirror; the dataclass
  `validate()` is authoritative).
- Tests: `tests/unit/test_profile_transition.py` (incl. the Gateway consumer-contract /
  frozen-vocabulary pin), `tests/unit/test_cli_profile_transition.py`,
  `tests/unit/test_backup_manifest.py`.
- `docs/BACKUP_READINESS_AND_PROFILE_SWITCHING.md`.
