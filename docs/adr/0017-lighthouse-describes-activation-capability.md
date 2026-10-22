# ADR-0017 — Lighthouse describes its activation capability; the Gateway defines and trusts it

- **Status:** Accepted
- **Date:** 2026-07-12
- **Packet:** LHP-PROFILE-14C

## Context

LHP-PROFILE-14A gave the transition preview its producer-owned execution facts; LHP-PROFILE-14B
gave activation a boundary that can be *reasoned about* (state record, preconditions evaluator,
intent artifact). Neither answers the question a future Gateway **capability attestation** must
ask before anything can be authorized: *what activation capability does Lighthouse claim to
provide, and is it available?*

Today that answer exists only as prose spread across ADR-0003, ADR-0013, ADR-0016 and the
capability docs. Prose cannot be attested, cross-checked, or bound by a digest — so whoever
eventually builds the bridge re-derives it under pressure, which is exactly how an unsafe
activation path gets invented.

The Gateway already knows what it wants (read-only audit of the separate Gateway application, this
packet; no Gateway file was modified). Its canonical registry defines three lighthouse activation
capabilities, all with `repo_audit_status: "missing"`:

| Gateway capability id | inputs | outputs | mutating | failure semantics |
|---|---|---|---|---|
| `lighthouse.profile_activation_endpoint` | `target_profile_id`, `target_profile_version`, `transition_reference` | `activation_result` | yes | `abort_and_flag_operator_no_blind_retry` |
| `lighthouse.active_profile_store` | `profile_id`, `profile_version` | `active_profile_record` | yes | `fail_closed_ambiguous_state_blocks` |
| `lighthouse.post_switch_validation` | `expected_profile_id` | `validation_report` | no | `record_and_continue_verification` |

Its attestation model joins to a provider by `provider_type` (`"lighthouse"`) + `provider_id`
(`"pattern-match-sentinel"` — the value it pins as `EXPECTED_LIGHTHOUSE_REPOSITORY`) +
`provider_version` / `provider_commit`, carries a bounded `restrictions` list (≤16 × ≤256 chars)
and an `evidence_reference` (≤512 chars, convention `"<schema>:<id>:<digest>"`).

## Decision

Ship **one pure contract**: `lighthouse_activation_capability.v1`
(`modules/activation_capability.py`) — a **provider self-description**. Lighthouse states what it
*would* provide, what it *would* require, what it cannot do, and that it is **not implemented**.

**A description of a capability is not a capability.** Nothing activates, switches, stores,
executes, dispatches, or contacts the Gateway. There is no endpoint, no store, no CLI command.

### 1. Availability is a closed state, not a boolean that can flip

The packet's requirement is `activation_available = false` — "do not create a boolean that can
accidentally become true; prefer closed-state vocabulary." Both, layered:

- **`implementation_state`** is a closed vocabulary whose **only member in v1 is
  `"not_implemented"`**. The contract has no word for "available": claiming availability is a
  contract change that must land *with* the capability, never a value edit. Crucially this holds
  **even when the digest is re-stamped** — a tamperer who controls the bytes controls the digest,
  so the rule cannot depend on the digest, and it doesn't.
- **`activation_available`** is additionally pinned to the constant `False` and checked by
  **identity** (`is not False`), so `True`, `1`, `0`, `"false"` and `None` all fail. This mirrors
  the Gateway's own `Literal[False]` authority pins and gives a future authorization packet one
  unambiguous refusal hook.

### 2. Operations map 1:1 onto the canonical Gateway capabilities

`declared_operations` carries one record per Gateway capability, each mirroring that definition's
`mutating_capability` / `required_inputs` / `guaranteed_outputs` field for field, so a future
attestation can cross-check the provider's claim against the definition it attests to:

| operation_id | gateway_capability_id | mutates | required inputs | produced outputs |
|---|---|---|---|---|
| `request_activation` | `lighthouse.profile_activation_endpoint` | yes | `target_profile_id`, `target_profile_version`, `transition_reference` | `activation_result` |
| `record_active_profile` | `lighthouse.active_profile_store` | yes | `profile_id`, `profile_version` | `active_profile_record` |
| `validate_post_switch` | `lighthouse.post_switch_validation` | no | `expected_profile_id` | `validation_report` |

**The id finding.** The packet names this capability `lighthouse.profile_activation`. That bare id
appears **nowhere** in the Gateway (zero hits across `app/`, `tests/`, `docs/`); the canonical id
carries the `_endpoint` suffix. Rather than silently renaming the operator's id or shipping an id
the Gateway cannot bind, the descriptor keeps **both**: `capability_id` is the Lighthouse-side
umbrella (the activation surface as a whole), and every operation names the canonical Gateway id
it would satisfy. A future attestation must be keyed on the **Gateway** ids — an attestation for
the umbrella id would match no definition and be rejected by the Gateway's evaluator. Tests pin
both facts, including the `_endpoint` suffix.

### 3. The three contracts, referenced but never executed

`contract_references` binds the artifacts an activation would exchange, each by name **and live
schema version**, using roles lifted from the Gateway's own input/output vocabulary:

| role | contract | why this role |
|---|---|---|
| `review_artifact` | `profile_transition_preview.v1` | the object under review (LHP-PROFILE-03/14A) |
| `transition_reference` | `activation_intent.v1` | the activation endpoint's required `transition_reference` input (LHP-PROFILE-14B) |
| `active_profile_record` | `active_profile_state.v1` | the active-profile store's guaranteed output (LHP-PROFILE-14B) |

The expected `(contract, schema_version)` pairs are derived from the **imported** schema-version
constants, so a future contract bump that forgets this descriptor fails validation loudly instead
of shipping a stale claim. An output with **no** matching role is a machine-discoverable gap:
`activation_result` and `validation_report` have no Lighthouse contract yet, and the artifact says
so rather than implying one exists.

### 4. What the descriptor deliberately does NOT carry (no duplicate authority)

Absent by design: `minimum_trust_level`, `freshness_required`, `maximum_age_seconds`,
`status` (draft/accepted/…), `repo_audit_status`, `required_for_execution`,
`required_for_authorization`, `definition_digest`, and every attestation status dimension.

**Those are Gateway-owned policy.** A provider that graded its own trustworthiness, set its own
freshness window, or declared its own capability "accepted" would be a second, competing
capability registry — precisely the duplicate authority this packet forbids. Lighthouse describes;
the Gateway defines what it requires and decides whether to believe the claim (ADR-0001). A test
recursively scans the serialized descriptor and fails if any Gateway-owned key name appears.

Also absent: `created_at` and any descriptor id. The descriptor is a **static self-description**,
not an event — so it has no volatile fields, its digest excludes only itself, and equivalent builds
produce an identical digest. And **no CLI command**: 14B's `test_no_activation_boundary.py` pins
the exact 16-subcommand set, and that pin is unchanged by this packet.

### 5. Field consumers (packet design rule — no consumer, no field)

| Field | Consumer |
|---|---|
| `capability_id`, `capability_version` | future Gateway attestation (join); audit |
| `provider_type`, `provider_id`, `provider_version`, `provider_commit` | future Gateway attestation (its four provider fields, 1:1) |
| `implementation_state` | future Gateway attestation (→ `availability_status: "unavailable"`); future authorization |
| `activation_available` | future Gateway authorization (refusal hook); audit |
| `declared_operations` | future Gateway attestation (one attestation per `gateway_capability_id`); future execution planning (inputs/outputs) |
| `contract_references` | future activation packet; execution planning (what to put in `transition_reference`) |
| `required_evidence` | future authorization; execution planning (`recheck_backup_evidence_tokens`) |
| `limitations` | future attestation (its bounded `restrictions`); audit |
| `security_constraints` | future authorization (must refuse a transition that violates one); execution planning |
| `artifact_digest` | future attestation (`evidence_reference`); audit |

## Gateway compatibility review (read-only; no Gateway change)

1. **The registry can represent this capability today.** The three Gateway definitions already
   exist with `owning_component: "lighthouse"` and `expected_provider_types: ["lighthouse"]`, and
   the descriptor supplies exactly the provider-side facts an attestation needs.
2. **No Gateway changes are needed yet.** Consuming the descriptor (building attestations from it)
   is a future Gateway packet; nothing in this contract requires a Gateway edit to be correct.
3. **No duplicate authority is created** — see §4.
4. **The descriptor cannot unblock anything.** An honest attestation derived from it maps
   `implementation_state: "not_implemented"` → `availability_status: "unavailable"`, which derives
   `attestation_status: "unusable"`, which can never satisfy a requirement — so every Gateway
   execution plan stays honestly `blocked` on the missing capabilities. Describing a capability
   moves nothing toward executing one; that is the intended and tested property.

**Cross-repo debt recorded** (not fixed here — this packet may not modify the Gateway):
a Gateway-side packet must consume this descriptor to produce capability attestations; and the
Gateway's execution plan step 8 (`request_profile_activation`) lists `idempotency_key` among its
`required_inputs` while its own `lighthouse.profile_activation_endpoint` definition requires
`transition_reference` — a Gateway-internal divergence to reconcile there. The LHP-PROFILE-14A
frozen Gateway prose tokens also remain frozen until that companion packet lands.

## Consequences

- Lighthouse's activation claim is now **machine-readable, versioned, digest-covered and
  fail-closed** — attestable rather than re-derivable.
- The claim is **honest by construction**: the only state it can express is "not implemented", and
  no digest re-stamping, value edit, or field addition can change that without a new contract.
- The first capability that ships (most likely the store) must land **with** a vocabulary change
  here — a deliberate, reviewable act, not a quiet flag flip.
- **Nothing changed operationally.** No activation, no store, no endpoint, no CLI, no Gateway or
  StartOS change.

## Repository evidence

- `modules/activation_capability.py` — the contract, closed vocabularies, canonical content,
  `describe_activation_capability()`, `capability_evidence_reference()`.
- `tests/unit/test_activation_capability.py` — over-claim, tamper, digest-coverage, Gateway-pin and
  duplicate-authority tests; `tests/unit/test_no_activation_boundary.py` — the standing
  no-activation regression (CLI subcommand pin unchanged; the new module joins the surface sweep).
- `docs/schemas/lighthouse_activation_capability.v1.schema.json` — reference mirror (the dataclass
  `validate()` remains authoritative).

## Related ADRs

- [ADR-0001](0001-lighthouse-owns-engine-gateway-is-control-plane.md) — the authority split this
  descriptor respects (Lighthouse describes; the Gateway approves).
- [ADR-0003](0003-exactly-one-active-profile-controlled-switch.md) — the invariant the descriptor
  publishes as `exactly_one_active_profile`.
- [ADR-0016](0016-activation-capability-boundary.md) — the contracts this descriptor references.
- [ADR-0013](0013-verified-backup-and-restore-drill-before-profile-activation.md) — the evidence
  the descriptor declares as required.
