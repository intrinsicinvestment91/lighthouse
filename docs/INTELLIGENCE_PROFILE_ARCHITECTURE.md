# Intelligence Profile Architecture

> Status: **foundation + narrow initial activation** (packets LHP-PROFILE-01, LHP-V1-PROFILE-01C).
> This packet defines the profile contract, a read-only registry, validation, and backup-readiness
> inspection. **Narrow backup-gated initial activation** of `repository_capability_discovery` v2 is
> shipped (`lighthouse profile-activate`). It does **not** implement active-to-active switching,
> deactivation, the invention-discovery engine, or a live Gateway bridge. Switching design:
> [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md).

## What an Intelligence Profile is

An **Intelligence Profile** is a versioned, **declarative policy bundle** that describes which
mission a Lighthouse deployment is configured for — e.g. the public scientific mission, private
invention discovery, or aerospace/defense monitoring. It declares *what* is allowed (source
categories, which collectors, observation tiers, graph/correlation/temporal participation,
privacy and publishing policy, backup requirements, interface eligibility, DMS eligibility,
compatibility) — never *how* to execute anything.

A profile is **not a plugin.** A profile may never declare arbitrary executable modules or shell
commands. `modules/profiles.py` structurally rejects executable-intent keys
(`module(s)`, `command`, `cmd`, `shell`, `exec`, `entrypoint`, `eval`, `script`, `run`,
`callable`, `import`, `subprocess`) anywhere inside any policy block, at any depth. This keeps
profiles safe, reviewable configuration rather than code.

The contract lives in `modules/profiles.py` (`IntelligenceProfile`,
`INTELLIGENCE_PROFILE_SCHEMA_VERSION = 1`) and mirrors the existing `modules/targets.py`
idiom (dataclass + `validate()` raising `ValueError` + `to_dict()`/`from_dict()` + a
`load_*`/`validate_*` pair). A human/Gateway-facing JSON Schema is provided at
`docs/schemas/intelligence_profile.v1.schema.json`, but the **dataclass validator is
authoritative** — we deliberately do not add a `jsonschema` runtime dependency.

## Why declarative, not a plugin system

- **Reviewability.** A profile can be read, diffed, and approved as data. There is no code path
  to audit per profile.
- **Fail-closed.** Validation rejects unknown/future `schema_version`, invalid identifiers,
  non-`draft/experimental/ready_for_validation` status, executable-intent keys, private profiles
  that would publish publicly, DMS enrollment/activation declarations, and backup `data_root`s
  that escape `/data`.
- **Safety.** The blast radius of a bad profile is a validation error, never arbitrary execution.

## Contract invariants (enforced by `IntelligenceProfile.validate()`)

1. `profile_id` matches `^[a-z][a-z0-9_]*$` (stable, machine-safe) and is non-empty; `display_name`
   is non-empty.
2. `profile_version` is an integer ≥ 1. `schema_version` is an integer, **rejected if `< 1` or
   `> INTELLIGENCE_PROFILE_SCHEMA_VERSION`** — unknown/future versions fail closed.
3. `status ∈ {draft, experimental, ready_for_validation}`. There is no `production`/`active`
   status.
4. No executable-intent keys anywhere in any policy block.
5. A **private** profile (`privacy_policy.local_only == true`) may not set
   `publishing_policy.public_publishing == true`. Private profiles default to no publishing.
6. `dead_mans_switch_policy.eligible` is a boolean (defaults `false`). Any of
   `enrolled/activated/active/enabled/armed/scheduled` being truthy is rejected — **eligibility is
   declarative and never implies enrollment.**
7. `backup_policy.backup_required_on_activation` is a required boolean. If a `data_root` is
   declared it must canonically resolve under `/data`.
8. `compatibility` keys must be recognized; unknown compatibility declarations fail closed.

## Five state distinctions (only the first two are implemented)

A profile passes through separate, deliberately-distinguished states:

| State | Meaning | Implemented in this packet? |
|-------|---------|------------------------------|
| **Installed** | The definition exists (a bundled `seed/profiles/*.json`, or a future operator override under `/data/profiles/`). | Yes — bundled only. |
| **Validated** | The definition passes the contract. | Yes — registry + `validate_profile`. |
| **Resolved** | `Bundled + operator overrides + Start9 env/secrets → validated resolved configuration`. | No — future. |
| **Proposed** | A resolved configuration staged for an audited switch (backup-gated). | Contract only — `activation_intent.v1` ([ADR-0016](adr/0016-activation-capability-boundary.md), LHP-PROFILE-14B) defines the deterministic "I intend to move A→B" artifact; nothing proposes automatically and an intent authorizes nothing. |
| **Active** | The profile that governs new intake and derivation. Exactly one at a time. | **Partial** — `active_profile_state.v1` store + narrow RCD v2 initial activation shipped (`lighthouse profile-activate`, `lighthouse active-profile`). Active-to-active switching not implemented ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)). |

## Exactly one active profile

Semantics ([ADR-0003](adr/0003-exactly-one-active-profile-controlled-switch.md), partially superseded
by [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)): **at most one profile is active at
any time.** Switching is a *controlled state transition* — a fenced, compare-and-set active-to-active
cutover that never passes through `no_active_profile` — not an in-place edit. Rollback is **bounded**:
pointer rollback when provable; otherwise `recovery_required` or operator restore from backup — not
guaranteed reversible automation (see [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) §11).
It must be preceded by a successful backup (see
[`BACKUP_READINESS_AND_PROFILE_SWITCHING.md`](BACKUP_READINESS_AND_PROFILE_SWITCHING.md)).

**Shipped:** narrow initial activation (`no_active_profile → repository_capability_discovery` v2).
**Not shipped:** active-to-active switching, deactivation, migration, rollback automation, runtime policy
resolution. Local switch authority requires a complete `profile_switch_authority_projection.v1` and
explicit **code/release-owned** `locally_activatable` enrollment — **not a seed field** (currently
empty — no bundled profile is locally switchable; matching the four 14A snapshot values confers
nothing). Gateway is mandatory for **widening**
switches; **`indeterminate` dimensions refuse unconditionally** ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) §2,
§13).

## Historical provenance requirement (design, not migrated)

Because a profile change alters how new observations and findings are derived, historical records
must eventually retain **which profile and derivation version produced them**, so a switch never
silently reinterprets or rewrites past intelligence. This packet does **not** migrate the DB or
add provenance columns; it records the requirement so future observation/finding work carries a
`profile_id` + `derivation_version` stamp. No existing record is changed.

## Why the invention engine is not migrated here

Per the approved analysis, the invention-discovery engine (card types, composite scoring, detectors)
already exists outside this repository, fixture-backed and fail-closed. The Lighthouse-centric
decision means that engine will eventually move here — but only **after** the profile contract,
backup readiness, and the live-interface boundary are separately proven. The
`private_invention_discovery` profile is not bundled in the public release.
See [`GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md`](GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md).

## Read-only operator surface

```bash
lighthouse profiles                         # list bundled profiles
lighthouse profiles --show <profile_id>     # show one
lighthouse profiles --validate              # validate all bundled profiles
lighthouse profiles --json                  # machine-readable
lighthouse backup-readiness                 # inspect backup readiness (read-only)
```

The commands above never activate a profile, mutate configuration, write approval state, move
data, or trigger a backup. Activation and active-profile status are separate commands
(`lighthouse profile-activate`, `lighthouse active-profile`) — see
[`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md) and
[ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md).
