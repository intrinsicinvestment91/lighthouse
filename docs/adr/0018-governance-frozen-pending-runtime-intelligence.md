# ADR-0018 — Profile governance is frozen at LHP-PROFILE-14C; the intelligence engine comes first

- **Status:** Accepted
- **Date:** 2026-07-12
- **Packet:** LHP-DIRECTION-02

## Context

Six Lighthouse packets (`LHP-PROFILE-01/02/03/14A/14B/14C`) and eight Gateway packets built a complete
governance ladder for profile activation: a declarative profile contract, verified backup + isolated
restore drill, a deterministic transition preview, producer-owned execution facts, activation boundary
contracts, and a machine-readable capability descriptor. Neither system can activate a profile.

That ladder answered a good question — *how would Lighthouse safely change missions, if it had missions
worth changing?* — and answered it well. The feasibility review that preceded this ADR asked the prior
question: **what runtime capability would a mission actually control?**

The answer is: **none. Nothing in Lighthouse reads a profile.**

| Profile policy field | Runtime readers | Where the behavior actually lives today |
|---|---|---|
| `collector_policy.enabled_collectors` | **0** | `data/targets.json` → `modules/signal_router.py` → `--module targeted` |
| `privacy_policy.local_only` | **0** | the concept does not exist in the runtime |
| `correlation_policy.min_publish_score` | **0** | a hardcoded literal (`main.py`, the publish gate) |
| `publishing_policy.gated_correlations_only` | **0** | the four-condition Nostr gate in `main.py` |
| `publishing_policy.public_publishing` | **1** — the read-only display command | `NOSTR_PUBLISH_CORRELATIONS` env (`config.py`) |
| `backup_policy.data_root` | **0** | `LIGHTHOUSE_DATA_DIR` env (`config.py`) |

Every knob a profile declares is, in the running system, an environment variable or a literal. The only
code that touches a profile is `handle_profiles_command` in `main.py` — a read-only display that itself
prints *"Activation is NOT implemented."*

Worse, the first activation target the roadmap had pinned, a private profile, declares
exactly two collectors — `repository_intake` and `document_intake` — and **neither exists anywhere in the
codebase**. The profile's own metadata says `"engine_migrated": false`.

**An activation built today would activate metadata, not capability.**

## Decision

**Profile governance is frozen at `LHP-PROFILE-14C`.** The contracts remain in-tree, dormant, and fully
tested. Engineering moves to the runtime intelligence capability that would give a profile something to
govern (`LHP-INTEL-01`, [`OBSERVATION_INTAKE.md`](../OBSERVATION_INTAKE.md)).

### 1. Why activation is not merely premature — it is undefined

A profile switch today would change a configuration record without changing any observable behavior of
the system. There is no mechanism by which it *could*: no component resolves a profile into runtime
configuration.

The sharpest consequence is that the planned safety check would be a **tautology**.
`lighthouse.post_switch_validation` exists to answer *"is the runtime now actually running the profile we
asked for?"* With no consumer, the only proposition it can evaluate is *"the record I just wrote contains
what I just wrote."* It would validate the write, not the switch.

**A safety check that cannot fail is worse than no safety check** — it manufactures confidence. That
alone disqualifies the previously-pinned "restricted foundation activation" as a *first* activation: it
cannot exercise the one mechanism that would make activation safe.

### 2. What is frozen

Not built, and not to be built until §4 is satisfied: the active-profile store; the activation endpoint;
a Gateway→Lighthouse bridge; the Gateway execution ledger and transition lock; any further governance
contract.

And one permanent prohibition, independent of §4: **no automatic or signal-triggered activation, ever.**
An activation triggered by ingested data would let externally-sourced OSINT content change the
collector's own posture — an adversary-controlled feedback loop into configuration. Any future activation
is **operator-initiated and machine-validated**, never machine-initiated.

### 3. What the governance work leaves behind — and what is used immediately

The ladder is preserved as infrastructure, not abandoned:

- **Verified backup + restore evidence** (`LHP-PROFILE-02`, [ADR-0013](0013-verified-backup-and-restore-drill-before-profile-activation.md)) —
  real disaster-recovery machinery that works today and is independent of profiles.
- **The deterministic transition preview** (`LHP-PROFILE-03`/`14A`, [ADR-0014](0014-transition-preview-artifacts-before-profile-activation.md),
  [ADR-0015](0015-preview-carries-producer-owned-execution-facts.md)) — a genuine review artifact, and
  *more* useful attached to a human-applied configuration change than to an automated one.
- **The activation boundary + capability contracts** ([ADR-0016](0016-activation-capability-boundary.md),
  [ADR-0017](0017-lighthouse-describes-activation-capability.md)) — dormant, and precise enough that a
  future activation can be built without re-deriving its safety argument under pressure.
- **The historical provenance requirement** ([`INTELLIGENCE_PROFILE_ARCHITECTURE.md`](../INTELLIGENCE_PROFILE_ARCHITECTURE.md)) —
  observations must record *which profile and derivation version produced them*, so a switch never
  silently reinterprets past intelligence. **This is the one governance requirement that is not dormant:
  `LHP-INTEL-01` adopts it from day one.** It is also the seed of the future profile→runtime binding —
  the first thing in Lighthouse that will ever *read* a profile.

That last point is what makes the freeze coherent rather than wasteful: the governance work produced a
constraint the intelligence work must honor **now**, and honoring it is what will eventually make
activation meaningful.

### 4. When activation resumes

All four conditions, verifiable in the repository, not in prose:

1. Profiles have **runtime consumers** — something resolves a profile into effective configuration.
2. The collectors a profile names **exist**.
3. Intelligence **outputs** exist that a mission choice would change.
4. **Post-switch validation can verify real behavior** — i.e. the check in §1 can fail.

When it resumes, the first activation is a **local, operator-invoked CLI action** on the Lighthouse box:
operator requests → Lighthouse validates preconditions → atomic active-profile write → post-switch
validation → audit record. It introduces **no new network surface** and requires no Gateway bridge. The
Gateway's governance artifacts remain useful as review, approval, and evidence surfaces — not as a
mutation path.

A future activation capability must also be **structurally incapable of widening exposure** (refusing
`local_only` true→false, `public_publishing` false→true, and any `data_root` escape). All four facts are
already carried in the `14A` typed policy snapshot, so the guard is nearly free — and it keeps rollback
in the "rewrite one record" regime, where it is provable.

### 5. Correcting the roadmap's stated dependency

[`ROADMAP.md`](../ROADMAP.md) gated Phase E (activation) on Phase F (backup) and Phase G (Gateway).
Both are real prerequisites for a *remote* switch, but neither is the binding one. Activation is gated on
**a profile-aware runtime** — a prerequisite that was never scheduled, and the reason activation has no
denotation today. The roadmap is corrected accordingly.

## Consequences

- The activation ladder stops at `14C` with **no code removed and no contract weakened**. Nothing that
  was built is discarded; it is dormant and tested (`test_no_activation_boundary.py` continues to pin the
  no-activation surface).
- The Gateway is frozen at `GW-PROFILE-11`. Its execution ledger, transition lock, and the
  `gateway.lighthouse_bridge` are reclassified from *"required"* to **"required only for remote/fleet
  activation"** — a possible future evolution, not the next step. **No Gateway change is made by this
  packet.**
- Engineering effort moves to Phase D — the phase the roadmap already called current, whose ingestion
  engine was never built.
- The next milestone is **`LHP-INTEL-01`** (charter: [`OBSERVATION_INTAKE.md`](../OBSERVATION_INTAKE.md)),
  followed by `LHP-INTEL-01A`, the first collector.
- Any future packet proposing activation, an activation endpoint, a bridge, or a further governance
  contract must first show §4 is satisfied. This ADR is the gate.

## Repository evidence

- `main.py` — `handle_profiles_command` is the only profile consumer in the codebase; it is read-only and
  prints "Activation is NOT implemented." The publish gate (`score >= 7.0 and len(set(signals)) >= 2`) is
  a hardcoded literal, not a profile field.
- A private profile seed — `"enabled_collectors": ["repository_intake",
  "document_intake"]`, `"engine_migrated": false`; neither collector name resolves to any module.
- `modules/targets.py`, `modules/signal_router.py`, `seed/targets.json` — the mission-configuration
  mechanism that *is* runtime-bound, and is more expressive than `collector_policy`.
- `modules/profiles.py` — `ALLOWED_STATUSES` deliberately contains no `production`/`active` status; of the
  six bundled seeds (historical count; the public release bundles five profiles — the private profile is
  not bundled), four are `draft`, one `experimental`, one `ready_for_validation`.
- `modules/activation_capability.py` — declares `implementation_state: not_implemented` and
  `activation_available: false`; an attestation derived from it can never satisfy a Gateway requirement.
- `docs/INTELLIGENCE_PROFILE_ARCHITECTURE.md` — the historical provenance requirement adopted by
  `LHP-INTEL-01`.

> **Public release note (2026-10-21):** the private invention-discovery profile seed is not bundled in
> this public release; five profiles ship in `seed/profiles/`.

## Implementation status (2026-07-14) — condition §4.2 is now satisfied; the freeze holds

**The freeze stands. One of its factual premises has expired.**

This ADR states that a profile's two named collectors — `repository_intake` and `document_intake` —
*"neither exists anywhere in the codebase"*, and the Repository-evidence line above says *"neither collector
name resolves to any module."* **Both statements are now false.** `modules/repository_intake.py` shipped in
`LHP-INTEL-01A` and `modules/document_intake.py` in `01C`; both are running, tested, and reachable from
`lighthouse observe`.

So **§4 resume condition 2 ("the collectors a profile names exist") is satisfied.**

Conditions **1** (profiles have runtime consumers) and **3** (intelligence outputs exist that a mission
choice would change) remain **unsatisfied** — and are exactly what the significance layer creates.
[ADR-0020](0020-profile-neutral-substrate-profile-owned-detectors.md) makes detector rules **profile-owned**,
which would give a profile its first runtime consumer. Condition **4** (post-switch validation can verify
real behaviour) follows from 1 and 3.

**Nothing here unfreezes the full ladder.** Activation, the activation endpoint, the Gateway bridge,
and any further governance contracts beyond the **narrow RCD initial-activation ceremony**
(`LHP-V1-PROFILE-01C/01E`: `no_active_profile → repository_capability_discovery` v2, backup-gated,
no switch) remain out of scope. Automatic or signal-triggered activation remains **permanently
forbidden**. This note exists so that a future packet evaluating §4 grades itself against the code
rather than against this ADR's stale prose.

## Related ADRs

- [ADR-0020](0020-profile-neutral-substrate-profile-owned-detectors.md) — builds the runtime consumer whose
  absence justified this freeze; does **not** unfreeze it.
- [ADR-0003](0003-exactly-one-active-profile-controlled-switch.md) — the switch semantics this ADR defers
  (not reverses); exactly-one-active still holds when activation is eventually built.
- [ADR-0016](0016-activation-capability-boundary.md) · [ADR-0017](0017-lighthouse-describes-activation-capability.md) —
  the contracts frozen here, dormant and intact.
- [ADR-0013](0013-verified-backup-and-restore-drill-before-profile-activation.md) ·
  [ADR-0014](0014-transition-preview-artifacts-before-profile-activation.md) — the ladder's evidence and
  review artifacts, retained as infrastructure.
- [ADR-0005](0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) ·
  [ADR-0008](0008-repository-source-packs-are-a-deterministic-observation-source.md) — the intelligence
  work this ADR prioritizes; source packs stay observation-tier, never a second engine.
- [ADR-0010](0010-live-gateway-bridge-blocked-pending-package-exposure.md) — the bridge, now explicitly
  *not* on the critical path.
