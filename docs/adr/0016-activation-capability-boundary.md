# ADR-0016 — Activation capability boundary: contracts before capability

- **Status:** Accepted
- **Date:** 2026-07-12
- **Packet:** LHP-PROFILE-14B

## Context

Profile activation is the most consequential state transition Lighthouse will ever perform, and
none of its machinery exists: the Gateway's canonical capability registry lists
`lighthouse.active_profile_store`, `lighthouse.profile_activation_endpoint`,
`lighthouse.post_switch_validation`, and `lighthouse.production_restore` as **missing**, and every
Gateway execution plan is honestly `blocked` on them. Before any of those capabilities can be
built safely, activation must be possible to *reason about*: what an active profile IS, what
preconditions must hold, what an intention to transition looks like as evidence, and what
rollback can and cannot promise. LHP-PROFILE-14A supplied the producer facts (typed policy
snapshot, content digests, provenance stamps); this packet supplies the boundary those facts
feed.

## Decision

Ship the **contract layer only** — three pure stdlib modules, no storage, no endpoint, no CLI,
no Gateway change. The goal is "make activation possible to reason about," explicitly not "make
activation possible."

### 1. `active_profile_state.v1` (`modules/active_profile_state.py`)

The record shape the future `lighthouse.active_profile_store` would persist (the Gateway's
`active_profile_record`, postcondition `exactly_one_active_profile`). Rules:

- **Absence is explicit, never inferred.** A discriminator `activation_state ∈
  {"active", "no_active_profile"}` declares the state; `no_active_profile_state()` is the only
  zero-argument constructor; `from_dict({})` raises. There is no implicit default.
- **No half states.** `active` ⇒ all five identity facts (`active_profile_id`,
  `active_profile_version`, `activated_at`, `activation_source`, `active_profile_sha256`)
  present and valid; `no_active_profile` ⇒ all five exactly `None`.
- **`activation_source` is a closed vocabulary** (`{"operator_approved_transition"}`): the only
  sanctioned future writer is an operator-approved, Gateway-authorized transition
  (ADR-0001/ADR-0003). Extending it is a contract change, not a runtime convenience.
- **`artifact_digest` covers everything, including the absent state**, and `validate()`
  *recomputes* it — flipping `no_active_profile` → `active` in stored bytes is detectable.
  (The transition preview's digest is trusted-never-recomputed because the Gateway must not
  re-derive Lighthouse serialization; this record is Lighthouse-owned end to end, so
  recompute-in-validate is the stronger and correct posture.)

| Field | Purpose | Future consumer | Why required |
|---|---|---|---|
| `activation_state` | absence declared, never inferred | future activation packet (store); execution-plan `verify_activation_outcome` | `exactly_one_active_profile` needs explicit presence/absence |
| `active_profile_id` / `active_profile_version` | which profile is active | execution plan (`active_equals_target`); Gateway `runtime_current_profile_verification` (deferred guarantee) | the record's subject; capability inputs `profile_id`/`profile_version` |
| `activated_at` | when | audit system; execution-plan record-keeping | temporal provenance of governing state |
| `activation_source` | how | audit system | closed set ⇒ no unaudited writers |
| `active_profile_sha256` | active profile's canonical content digest | future post-switch validation (compare vs authorized `target_profile_sha256`) | id+version cannot detect in-place content drift (the 14A lesson) |
| `artifact_digest` | tamper evidence incl. absent state | future store read-back integrity; audit | stored-byte flips must fail validation |

### 2. Activation-preconditions evaluator (`modules/activation_preconditions.py`)

A read-only, deterministic, clock-free, non-raising evaluator answering: *would this transition
be allowed to proceed if activation existed?* Output `ready | blocked | invalid` with stable
snake-case reason tokens; precedence `invalid > blocked > ready`; **unknown never becomes
ready** — anything missing, failed, or undeterminable is at most `blocked`; structurally
unusable inputs (preview or current state failing their own contracts) are `invalid`. `ready`
is a statement about preconditions, never an authorization.

Checks, in order: preview parses and passes its own `validate()`; `transition_status ==
ready_for_review`; the 14A producer facts are present (`target_policy_snapshot`,
`target_profile_sha256`, `backup_manifest_sha256`); the preview's own `artifact_digest` is
well-formed (the preview contract deliberately never inspects it — Gateway-trust rule — but
nothing downstream can bind by a malformed digest); backup evidence present and passing
(verification ∈ {`verified`,`passed`}, drill == `completed`); the target profile is supplied,
valid, matches the preview's target reference, and its **recomputed**
`profile_content_sha256()` equals the preview's `target_profile_sha256`; the declared current
state validates and agrees with the preview's `current_profile_id`/`current_profile_version`
pair (both-null foundation included). `current_state` has no default — callers construct
explicit absence.

**"Required capability facts" interpretation:** the packet's sixth check is read as *the 14A
producer-owned execution facts are present* — the facts a future activation consumes. Gateway
capability *attestations* are Gateway-owned governance evidence and are deliberately **not**
consumed on the Lighthouse side (the boundary: Lighthouse owns intelligence and facts; the
Gateway owns governance, approval, and execution policy). Deliberately absent: any gate on
`deterministic_serialization_version` (self-attested, forgeable — the 14A rule stands) and any
freshness/staleness check (the Gateway's execution plan owns expiry; this evaluator is
time-independent).

### 3. `activation_intent.v1` (`modules/activation_intent.py`)

A deterministic, digest-bearing declaration — "I intend to move from profile A to profile B" —
bridging the transition preview and future activation; the natural payload for the activation
endpoint's `transition_reference` input. It is **not** approval, authorization, or execution:
`authorizes_activation` is a digest-covered constant `False`, identity-checked (`1`, `"false"`,
and `True` all rejected), so no path exists where an approved-looking intent becomes an
activated profile.

It is an **honest artifact**: `build_activation_intent()` runs the evaluator and embeds its
true result (decision + reasons); blocked and invalid transitions are representable — recorded,
never executable. `validate()` strictens `ready`: every reference present (target content
digest, `preview_id` + `preview_digest`, `backup_manifest_id` + `backup_manifest_sha256`) and
reasons empty iff ready. References are **bindings, never copies** — `preview_digest`
transitively binds every digest-covered producer fact in the preview. The builder demands a
*valid* current state (the evaluator merely answers questions about garbage; the builder
declares intent, and nothing truthful can be declared from an undeclarable state) and a preview
that at least names a target; everything else degrades into the embedded result.

| Field | Purpose | Future consumer | Why required |
|---|---|---|---|
| `intent_id` / `created_at` | volatile handles (digest-EXCLUDED) | audit system | identity lives in the digest, not the id |
| `current_profile_id` + `current_profile_version` | declared "from"; both-null = foundation | Gateway authorization (same names its shipped classifier reads); future activation (re-verify vs store) | half declarations rejected |
| `target_profile_id` / `target_profile_version` | declared "to" | future activation; execution-plan `request_profile_activation` inputs | an intent without a target is meaningless |
| `target_profile_sha256` | content-bound target | future activation (recompute vs live registry at ceremony) | id+version don't bind content |
| `preview_id` + `preview_digest` | binds the reviewed preview + all producer facts | Gateway authorization (binding names match the shipped approval contract); audit | producer-fact references by reference, not copies |
| `backup_manifest_id` + `backup_manifest_sha256` | evidence references by id AND content | execution-plan `recheck_backup_evidence_tokens`; future activation | content binding per 14A |
| `preconditions_decision` + `preconditions_reasons` | the validation result as-of build | future activation (refuses non-ready and re-evaluates); audit | honest record |
| `authorizes_activation` (const `False`) | anti-confusion pin | Gateway authorization (refusal hook, mirrors its `Literal[False]` pins) | "approved" ≠ "activated", structurally |
| `artifact_digest` | tamper evidence AND producer-side idempotency identity | future activation (`transition_reference`); audit | deterministic artifact requirement |

### 4. Persistence: none (artifact-only)

Analyzed and decided **against** storage in this packet. The durable store *is* the missing
`lighthouse.active_profile_store` capability, owned by a future, separately-approved packet;
adding storage now would create exactly the mutation surface this packet's security tests must
prove absent, and no shipped consumer reads a stored record yet. The state contract defines the
**shape** the future store persists — shape now, storage later. Until a store exists, the only
truthful record is the explicit `no_active_profile` state, constructed on demand.

### 5. Idempotency semantics (documented only)

Two activation attempts are **the same intended transition** iff their intent
`artifact_digest` values are equal — identical digest-covered content (declared current,
target id/version/content-sha, preview binding, evidence binding, embedded decision).
`intent_id`/`created_at` are handles, not identity. The Gateway's `idempotency_key` (sha256
over approval id/digest + preview id/digest + target + plan schema version) is a *different*
identity: the intent digest identifies *what is intended*; the Gateway key identifies *an
authorized execution attempt*. **Duplicate prevention is owned elsewhere and later**: the
future execution ledger (key never reused), the transition lock (keyed
`target_profile_id` + `preview_digest`), and the activation endpoint's single-request /
no-blind-retry semantics. None of that exists in this packet.

### 6. Rollback boundary (documented only; no rollback code)

Grounded in the shipped Gateway plan's `STANDARD_ROLLBACK_LIMITATIONS` and the LHP-PROFILE-02
drill guarantees (full operator-facing text:
`docs/BACKUP_READINESS_AND_PROFILE_SWITCHING.md`):

- **Rollback can restore:** the files inventoried in a verified backup manifest, into an
  isolated empty destination — proven by the shipped restore drill (including safe SQLite
  snapshots).
- **Rollback cannot restore:** anything not in the backup — observations/alerts/graph writes
  made after the backup; published Nostr events (irrevocable) and IPFS pins; data collected
  under a newly-activated profile (a pointer-style deactivation does not purge it —
  `pointer_rollback_does_not_purge_data_collected_after_activation`); secrets and environment
  outside `/data`.
- **Production restore ≠ restore drill:** the drill refuses `/data` and everything beneath it
  by design, never stops or starts the service, never performs cutover, and proves
  recoverability into isolation only. A production restore overwrites the live volume, requires
  `service_stopped` + `backup_verified` preconditions with fail-closed never-partial semantics,
  and is the still-missing `lighthouse.production_restore` capability. Until an activation
  capability exists there is no rollback target at all
  (`no_activation_capability_no_rollback_target`).

## Explicitly not implemented

Profile switching · activation endpoint · CLI activation command · automatic profile selection ·
Gateway bridge · StartOS changes · MCP mutation · collector/publishing/DMS changes · rollback
execution · migrations · background workers · any storage/table/durable state · any Gateway
repository change · any path where "approved" = "activated". Consumer-less candidates cut
during design: a serialization-version gate in the evaluator; a `current_state_digest` on the
intent (no durable store exists — a digest over an ephemeral in-memory object binds nothing);
version/commit stamps on the intent (already digest-covered inside the bound preview); a
per-check result map.

## Consequences

- Activation is now specifiable: the future store persists `active_profile_state.v1`, the
  future endpoint consumes an `activation_intent.v1` reference and re-runs the preconditions
  evaluator, and post-switch validation compares `active_profile_sha256` against the authorized
  `target_profile_sha256`. Each future capability lands against a tested contract instead of
  inventing one under pressure.
- The evaluator is the first Lighthouse-side consumer of 14A's `target_profile_sha256`
  (recompute-vs-registry) and `backup_manifest_sha256` facts, closing the loop the producer
  packet opened.
- A standing regression file (`tests/unit/test_no_activation_boundary.py`) pins the CLI
  subcommand set, the absence of any activation surface on the profile-family modules, and the
  closed decision/source vocabularies — widening any of these is now a conscious, test-breaking
  act.
- The Gateway classifier companion (consuming the 14A typed snapshot and retiring the two
  frozen prose tokens) remains **owed** to a future Gateway-side packet; the tokens stay frozen
  verbatim (`test_gateway_consumer_contract_keys_and_frozen_vocabulary`).

## Repository evidence

- `modules/active_profile_state.py`, `modules/activation_preconditions.py`,
  `modules/activation_intent.py` — the three contracts.
- `modules/profile_transition.py` — `profile_content_sha256()` promoted to public (single
  source of truth for the canonical profile-content digest; the evaluator's recompute must
  never drift from the preview's).
- `docs/schemas/active_profile_state.v1.schema.json`,
  `docs/schemas/activation_intent.v1.schema.json` — reference mirrors (dataclass validators
  stay authoritative).
- `tests/unit/test_active_profile_state.py`, `tests/unit/test_activation_preconditions.py`,
  `tests/unit/test_activation_intent.py`, `tests/unit/test_no_activation_boundary.py`.

## Related

- ADR-0003 (exactly one active profile; controlled switch) — this packet implements its record
  **shape**, not its mechanism; the "docs must not claim activation exists" consequence stands.
- ADR-0015 (producer-owned execution facts) — the facts this boundary consumes.
- ADR-0013/ADR-0004 (backup evidence + fail-closed data boundary) — the evidence the
  preconditions gate on.
- Gateway capability definitions (`lighthouse.active_profile_store`,
  `lighthouse.profile_activation_endpoint`, `lighthouse.post_switch_validation`,
  `lighthouse.production_restore`) — the missing capabilities whose shapes these contracts
  define.

## Reconciled by ADR-0024 (2026-09-02, LHP-LIFE-DESIGN-01-C2)

**Shipped since this ADR was written:** narrow backup-gated initial activation of
`repository_capability_discovery` v2 via `lighthouse profile-activate` (`LHP-V1-PROFILE-01C/01E`).
`ALLOWED_ACTIVATION_SOURCES` now includes `operator_local_cli_confirmation` for that ceremony
(`modules/active_profile_state.py`) alongside the planned `operator_approved_transition`.

**Switching is distinct:** active-to-active switching is not implemented. Future switches require
**distinct** `activation_source` values (`operator_local_switch_confirmation`,
`gateway_authorized_switch` — planned in ADR-0024 §13, not coded) and contract work including
`profile_switch_authority_projection.v1` (`LHP-LIFE-AUTHPROJ-01`). The "Explicitly not implemented"
list above remains accurate for switching, the Gateway bridge, and non-RCD activation.
