# Lighthouse — Implementation Status

> This document answers **"Where are we now?"** — current progress, the implementation matrix, active
> work, and immediate next milestones. For *direction* see [`ROADMAP.md`](ROADMAP.md); for *history* see
> [`PACKETS.md`](PACKETS.md); for *backlog* see [`FUTURE_PLAN.md`](FUTURE_PLAN.md); for *decisions* see
> [`adr/`](adr/README.md). Navigation hub: [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md).

## Documentation authority

**Shipped and verified state lives here.** The implementation matrix and milestones below are the
authoritative record of what is implemented and tested.

**Intended destination and ordered work** — from v1.0.0 through full-capability V2 — lives in
[`FULL_CAPABILITY_ROADMAP.md`](FULL_CAPABILITY_ROADMAP.md). That document uses stable capability IDs,
release trains, and profile completion gates. It must **never** silently upgrade planned work into
shipped status; when capability state changes, update this file **only after evidence exists**
(rehearsal, tests, or verified deployment).

Authority order: (1) this file · (2) FULL_CAPABILITY_ROADMAP · (3) ADRs · (4) profile seeds · (5)
packets · (6) prototype artifacts (design evidence only).

## Current Operational Status

```text
YELLOW — Operational with known gaps
```

- **Current phase:** **D — Repository Intelligence** (in progress). Phases A–C complete. Profile
  governance contracts remain at `LHP-PROFILE-14C`; **narrow initial activation** for
  `repository_capability_discovery` v2 is shipped on the finish-state branch
  (`LHP-V1-PROFILE-01C/01E`). Broader activation, profile switching, and Gateway authority remain
  deferred ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)). See
  [`ROADMAP.md`](ROADMAP.md).
- **Tests:** **2500 unit tests passing** (verified via the repository pytest configuration on
  clean checkout: **2500 passed, 0 failed, 0 skipped**). This is the single source of truth for
  the count; update it here.
- **Lighthouse V1 (RCD slice):** see [`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md). V1 =
  RCD operator loop + backup-gated initial RCD activation + observe/detect admission + OSR producer
  export. **Not** V1: SciPres activation/switch, live host ceremony, invention migration, Gateway
  authority, `uv` packaging.
- **Packaging:** canonical dependency locks are `requirements*.lock` (pip-compile via `make lock`).
  Accidental `uv.lock` files are gitignored — adopt `uv` only in a dedicated packaging packet.
- **Start9:** deployed on the StartOS test server (v0.1.0). Data-path contract verified —
  `LIGHTHOUSE_DATA_DIR=/data`; `DB_PATH` derives to `/data/state.db` when unset (explicit
  `DB_PATH=/data/state.db` still recommended for `podman exec`).
- **Working modules:** SEC sentinel, flight tracker, ADSB sentinel, correlator (A3) with weighted
  scoring, graph query CLI + backfill, graph-native callsigns, temporal + tempo analysis, MCP read
  tools, witness signing, targeted mode, private local gateway, safe Nostr heartbeat.
- **Validated signal value:** ADSB + flight + SEC correlations; DARPA / `government_defense_labs`
  military aviation; target routing (`lighthouse --module targeted`).
- **Degraded:** patent sentinel (provider disabled by default — graceful degradation; ODP adapter
  deferred); narrative analyzer (requires `ANTHROPIC_API_KEY`); DoE archive feed (HTML fallback only).

> **Correction (2026-10-22):** "witness signing" is historical wording; production stores a SHA-256
> witness hash with an empty signature, and the BIP-340 helper is demonstration/test-only.

## Implementation Matrix

Status legend: ✅ implemented · 🧩 partial · ⚙️ foundation only · 🚧 blocked · 🔭 planned.

| Capability | Status | Repo location | Tests | Docs |
|------------|--------|---------------|-------|------|
| Signal sentinels (SEC, flight, ADSB, arXiv, narrative, velocity) | ✅ | `modules/*_sentinel.py`, `flight_tracker.py`, `velocity_tracker.py` | ✅ | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) |
| Patent sentinel | 🧩 disabled (graceful) | `modules/patent_sentinel.py` | ✅ | [FUTURE_PLAN.md](FUTURE_PLAN.md) |
| Alert contract | ✅ | `modules/alerts.py` | ✅ | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) |
| Correlation + weighted scoring | ✅ | `modules/correlator.py`, `correlation_scoring.py` | ✅ | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) |
| Witness attestation | ✅ | `modules/witness.py` | ✅ | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) |
| Graph model + persistence + backfill | ✅ | `modules/graph*.py` | ✅ | [site/graph.html](site/graph.html) |
| Graph-native callsigns | ✅ | `modules/graph_builder.py`, `callsigns.py` | ✅ | [site/graph.html](site/graph.html) |
| Temporal + tempo analysis | ✅ (informational) | `modules/temporal_analysis.py`, `tempo_analysis.py` | ✅ | this file |
| Local Gateway (read-only dispatch) | ✅ | `modules/local_gateway.py`, `portal/app.py` | ✅ | [site/local-gateway.html](site/local-gateway.html) |
| MCP read tools | ✅ | `portal/mcp_server.py` | ✅ | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) |
| MCP approval gate (Lighthouse-local) | ✅ shipped — fail-closed (`LHP-SECURITY-01`) | `portal/mcp_server.py` | ✅ | [SECURITY.md](SECURITY.md), [ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md), [PROFILE_SECURITY_RISKS.md](PROFILE_SECURITY_RISKS.md) risk 2 Lighthouse RESOLVED |
| Intelligence Profiles | ⚙️ declarative registry + **narrow RCD v2 initial activation** (`lighthouse profile-activate`); switching/deactivation **not implemented** | `modules/profiles.py`, `profile_registry.py`, `profile_activation.py`, `seed/profiles/` | ✅ | [INTELLIGENCE_PROFILE_ARCHITECTURE.md](INTELLIGENCE_PROFILE_ARCHITECTURE.md) · [V1_OPERATOR_WORKFLOW.md](V1_OPERATOR_WORKFLOW.md) |
| Profile activation / switching | 🧩 **narrow initial activation shipped** — `no_active_profile → repository_capability_discovery` v2 via `lighthouse profile-activate` (backup-gated, typed confirm, no switch/Gateway); `active_profile_state` store + observe/detect admission; active-to-active switching **not implemented** ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)); non-RCD targets **deferred** | `modules/profile_activation.py`, `modules/active_profile_store.py`, `modules/active_profile_state.py`, `modules/activation_preconditions.py`, `modules/activation_intent.py`, `modules/activation_capability.py` | ✅ | [V1_OPERATOR_WORKFLOW.md](V1_OPERATOR_WORKFLOW.md) · [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) · [ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md) · [ADR-0016](adr/0016-activation-capability-boundary.md) |
| Observed-source reference export (OSR) | ✅ shipped (LHP-INTEL-04D-R1) — `lighthouse public-sources export-osr`; producer-side `observed_source_reference.v0` from one validated `available` check; authority pins const `false`; cross-repo consumer interop **trial only** | `modules/observed_source_reference.py`, `modules/observed_source_exporter.py` | ✅ | [PUBLIC_SOURCE_CHECKS.md](PUBLIC_SOURCE_CHECKS.md) |
| Scientific Preservation detector (04C) | ✅ shipped — `scientific_source_disappearance_review` over the 04B ledger; second detector via closed spec registry | `modules/scientific_source_disappearance.py`, `modules/pattern_candidate_specs.py` | ✅ | [SCIENTIFIC_PRESERVATION_CANDIDATES.md](SCIENTIFIC_PRESERVATION_CANDIDATES.md) · [ADR-0023](adr/0023-detector-owned-candidate-specifications.md) |
| Backup readiness (inspection) | ✅ | `modules/backup_readiness.py` | ✅ | [BACKUP_READINESS_AND_PROFILE_SWITCHING.md](BACKUP_READINESS_AND_PROFILE_SWITCHING.md) |
| Verified backup / restore drill | 🧩 manual (ID-P1B) | `docs/OPERATIONS.md` | — | [OPERATIONS.md](OPERATIONS.md) |
| Repository Source Packs (external, Gateway-admitted) | ⚙️ architecture-only | `seed/profiles/repository_capability_discovery.json` | — | [REPOSITORY_SOURCE_PACKS.md](REPOSITORY_SOURCE_PACKS.md) |
| Observation intake — repository observation | ✅ shipped (LHP-INTEL-01A) — deterministic, pure, host-only | `modules/repository_intake.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Observation intake — normalization + storage | ✅ shipped (LHP-INTEL-01B) — facts with identity; fact-only tables; **no scoring** | `modules/observation_normalizer.py`, `modules/db.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Observation intake — documents | ✅ shipped (LHP-INTEL-01C) — PDF/Markdown/text, metadata-first, **same fact tables**; **no scoring** | `modules/document_intake.py`, `modules/observation_normalizer.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Observation intake — shared substrate | ✅ shipped (LHP-INTEL-01D-A) — the observation model, confidence contract and digest recipe both collectors speak; **defined once** | `modules/observations.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Observation intake — runtime boundary | ✅ shipped (LHP-INTEL-01E) — `lighthouse observe`; trust boundary is `LIGHTHOUSE_OBSERVATION_ROOTS`, **empty fails closed**; dry run writes nothing at all | `modules/observation_intake.py`, `modules/observation_sources.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Observation query surface (read boundary) | ✅ shipped (LHP-INTEL-02A) — `lighthouse observations list\|show\|search\|stats`; SQLite-enforced `mode=ro`, validate-on-read; **counts, never ranks** | `modules/observation_query.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Cross-scope fact discovery | ✅ shipped (LHP-INTEL-02B) — `observations where\|shared`; exact lookup + exact set intersection; **no rank, no inference, no score, no similarity**; no schema change | `modules/observation_query.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| Repository **file-content identity** | ✅ shipped (LHP-INTEL-03A) — `content:<sha256>` = `SHA-256(exact raw bytes)`; tracked files only, bounded streaming, fail-closed; path identity and content identity stay **separate**; a repository file and a standalone document with the same bytes are **one entity, two occurrences**, on the *existing* 02B surface. **No new table/column/index/migration/CLI command.** Exact byte identity proves exact byte identity — **nothing about copying, independence, or significance** | `modules/content_identity.py`, `repository_intake.py`, `observation_normalizer.py` | ✅ | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) |
| **Pattern candidates + first profile-owned detector** | ✅ shipped (LHP-INTEL-03B) — `lighthouse patterns detect\|list\|show\|review`; `pattern_candidate.v1` (profile-neutral, immutable, closed field set) + `repository_exact_content_recurrence` (owned by **repository_capability_discovery**): *a `content:<sha256>` observed in ≥2 **distinct repository scopes***. **No score, no rank, no aggregate confidence, no hypothesis, no finding, no publication, no activation.** 10 mandatory unknowns + 8 mandatory limitations; `source_independence_not_assessed` always. Append-only disposition, **one per candidate version** (UNIQUE index). Dry run writes nothing — not even the DB file | `modules/pattern_candidate.py`, `repository_content_recurrence.py`, `pattern_candidate_service.py` | ✅ | [PATTERN_CANDIDATES.md](PATTERN_CANDIDATES.md) · [ADR-0021](adr/0021-pattern-candidates-are-review-objects-not-findings.md) |
| The significance layer ("why does it matter?") | 🚫 does not exist — deliberately unbuilt. **Boundary decided and accepted** (LHP-INTEL-02C → ADR-0020): facts first, interpretation second, findings only after evidence and review; **detector rules are profile-owned**, the substrate stays profile-neutral | — | — | [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md) |
| Invention-discovery prototype (external) | ✅ preserved (LHP-ID-PRESERVE-01) — was in **no git repo at all**; now preserved privately with a provenance manifest. **Preserved, not adopted**; composite scoring quarantined; no engine code in this repository | — (out of tree) | n/a | [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) |
| Invention engine | 🔭 preserved privately (migration deferred) | — (out of tree) | — | [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) |
| Live Gateway bridge | 🚧 blocked | — | — | [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md) |
| Dead-Man's Switch | 🚧 blocked (mechanism present) | `portal/dms_checker.py`, `portal/app.py` | ✅ | [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md) |
| Safe Nostr publishing | ✅ gated | `modules/nostr_relay.py`, `main.py` | ✅ | [SECURITY.md](SECURITY.md) |

## Completed Milestones (engine)

Phases A–C are complete (see [`ROADMAP.md`](ROADMAP.md)). Highlights: Signal → Alerts → Correlation →
Graph → Query → MCP → Explanation → Local Gateway pipeline; `AlertRecord`/`CorrelationRecord`/
`GraphNode`/`GraphEdge` contracts; BIP-340 witness signing; graph persistence + idempotent backfill;
graph-native callsigns; temporal + tempo intelligence; weighted correlation scoring; Start9 volume
persistence; controlled high-signal Nostr publishing; the Intelligence Profile foundation
(`LHP-PROFILE-01`: contract + read-only registry + backup-readiness inspector, six declarative seeds,
none production-ready, **no activation**); the verified backup manifest + isolated restore drill
(`LHP-PROFILE-02`: `backup-manifest` / `backup-create` / `backup-verify` / `restore-drill`,
fail-closed, read-only toward source, **no activation**); and the deterministic profile-transition
preview (`LHP-PROFILE-03`: `profile-transition-preview`, a read-only review artifact that consumes
backup evidence and fails closed to non-`ready_for_review` without it, **no activation, no Gateway
approval**); and the producer-owned execution facts in that preview (`LHP-PROFILE-14A`: typed
`target_policy_snapshot`, all eight change categories on the wire, manifest + target-profile content
digests, provenance stamps — additive within schema v1, serialization v2,
[ADR-0015](adr/0015-preview-carries-producer-owned-execution-facts.md), **still no activation**);
and the activation capability boundary (`LHP-PROFILE-14B`: pure contracts only —
`active_profile_state.v1` with explicit absence, a read-only `ready`/`blocked`/`invalid`
preconditions evaluator where unknown never becomes ready, and the `activation_intent.v1` bridge
artifact whose `authorizes_activation` is a digest-covered constant `false`; documented-only
idempotency semantics and rollback boundary;
[ADR-0016](adr/0016-activation-capability-boundary.md), **no store, no endpoint, no CLI command,
still no activation** — superseded for narrow RCD v2 initial activation by `LHP-V1-PROFILE-01C/01E`,
which shipped `active_profile_store` + `lighthouse profile-activate`; active-to-active switching
remains unbuilt); and the activation capability descriptor (`LHP-PROFILE-14C`:
`lighthouse_activation_capability.v1` — Lighthouse's machine-readable self-description of the
activation capability it *would* provide, with three operations mapped 1:1 onto the canonical
Gateway capability ids, the contracts they exchange, required evidence, limitations and security
constraints. Availability is a **closed state vocabulary whose only member is `not_implemented`**
plus an identity-checked constant `activation_available: false` — unreachable by a value edit or a
re-stamped digest — and it carries no Gateway-owned policy, so no duplicate authority is created;
[ADR-0017](adr/0017-lighthouse-describes-activation-capability.md), **describes activation,
provides none**). Full chronology: [`PACKETS.md`](PACKETS.md).

> **Correction (2026-10-22):** "BIP-340 witness signing" and "six declarative seeds" are historical.
> Production stores a SHA-256 witness hash with an empty signature (BIP-340 helper
> demonstration/test-only), and the public release bundles five profile seeds.

**That ladder is now closed.** `LHP-DIRECTION-02` froze profile governance at `14C`: a profile has **no
runtime consumer** in Lighthouse (every policy field it declares is, at runtime, an env var or a
literal), so an activation today would activate metadata rather than capability — and its post-switch
validation could only assert that the record it wrote contains what it wrote. The contracts stay in-tree,
dormant and tested; engineering moves to the runtime intelligence capability that would give a profile
something to govern ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)).

## Active Work & Immediate Next Milestones

1. **Shipped intelligence track (`LHP-INTEL-01`–`04D-R1`)** — observation intake through OSR
   export is **done**, not the current implementation track. Charter:
   [`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md). Historical packets below remain the record.
   - **`LHP-INTEL-01A` — done.** `modules/repository_intake.py` observes the operator's own git
     repositories deterministically and emits a validated, digest-bearing `repository_observation.v1`
     artifact. Pure: no database, no scoring, no graph, no writes. Source immutability is a tested
     property (the tree, `.git/` included, is byte-identical after a run).
   - **`LHP-INTEL-01B` — done.** Observation normalization: `modules/observation_normalizer.py` (pure)
     turns those facts into provenanced entities/relationships with **stable, joinable identity**
     (`technology:docker` is the same entity in every repository that has a Dockerfile), persisted to
     three **fact-only** tables in `db.py`. It **does not score**. The original plan — route through
     `AlertRecord` → graph — was audited and rejected: `AlertRecord` is a triage contract and
     `GraphEdge` requires a `weight`, so neither can hold a fact without asserting an opinion about it.
   - **`LHP-INTEL-01C` — done.** `modules/document_intake.py` observes local documents (PDF, Markdown,
     plain text) and normalizes them through **the same** fact boundary — no document database, no
     second provenance system. It is **metadata-first**: text is extracted to establish facts *about*
     it and then dropped, so the operator's document contents never reach the database. Its central
     guarantee is a distinction: *"this document has no text"* (count `0`, confidence `1.0`) is a
     **fact**; *"I could not extract its text"* (`None`, confidence `0.0`) is the **absence** of one,
     and the two may never collapse into the same silence. Identity is the digest of a document's
     **bytes**, never of its extracted text, so a copy is one entity observed twice and no truncation
     bound can make two documents collide. The 01B boundary was **generalized, not forked**
     (`repository_id` → `scope_id`, plus a closed `scope_type`): it was semantically right and merely
     named for one kind of subject — the opposite of the `AlertRecord` verdict above.
   - **`LHP-INTEL-01D-A` — done.** `modules/observations.py` — the substrate both collectors speak: the
     observation model, the binary confidence contract, the one canonical digest recipe, and the shared
     error base. Extracted from `repository_intake` because two copies of a fail-closed validator had
     *already* begun to drift, and because a malformed **document** was reporting itself as a repository
     failure — a real bug, uncovered by the extraction and fixed by it.
   - **`LHP-INTEL-01E` — done.** `lighthouse observe` — the runtime consumer the layer had spent four
     packets without. The trust boundary is `LIGHTHOUSE_OBSERVATION_ROOTS`, and it is **empty by default,
     which fails closed**: with no roots configured, `observe` *refuses* rather than implicitly trusting
     the filesystem. `data/observation_sources.json` is the **work list, not the boundary** — it may only
     narrow what is observed, and widening is *unrepresentable*, not merely rejected.
   - **`LHP-INTEL-02A` — done.** `lighthouse observations list|show|search|stats` — the read boundary.
     "Read-only" is a **mechanism, not a promise**: every query goes through a `mode=ro` connection, so
     SQLite itself rejects the write — and a read against a machine that has never observed anything
     **creates no database**. Reads validate exactly as writes do. It counts and lists and shows; it
     **never ranks**.
   - **`LHP-INTEL-02B` — done.** `lighthouse observations where|shared` — cross-scope fact discovery on
     the same boundary. *Exact* entity lookup and *exact* set intersection over facts already stored:
     **it does not rank, infer, score, or interpret relationships.** No new table, column, index or
     migration — the joins were already in the store; nobody could ask for them.
   - **`LHP-INTEL-02C` — done (analysis); accepted as
     [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md).** The significance boundary
     is now explicit. Its central finding: **the first detector is blocked on a fact the store does not
     hold.** `repository_intake` records file *paths and names*, never file *content* — so Lighthouse
     cannot distinguish *"two repos independently implement this"* from *"I copied my Dockerfile from the
     last project"*. **The ordering below is forced by the threat model, not chosen for convenience.**
   - **`LHP-ID-PRESERVE-01` — done.** The invention-discovery prototype was in **no git repository at
     all** (an inventory found it was not under version control; the Gateway held only a
     consumer/validator and one derived output — **the output was version-controlled while the engine
     that produced it was not**). Preserved privately with a provenance manifest; databases and live
     runtime data excluded. **Preserved, not adopted**; composite scoring quarantined; no engine code
     in this repository.
   - **`LHP-INTEL-03A` — done.** **Repository file-content identity**: `content:<sha256>` =
     `SHA-256(exact raw bytes)`, tracked files only, bounded streaming, fail-closed; path identity and
     content identity kept **separate**, and both kept. A repository file and a standalone document with
     the same bytes are now **one entity with two occurrences** — answerable through the *existing* 02B
     surface (`observations where --entity-id content:<sha256>`), with **no new table, column, index,
     migration or CLI command**. Every tracked path gets an accounted outcome in a closed 10-member
     status vocabulary; **only `hashed` may carry a digest**, enforced by `validate()` rather than
     merely declared. Symlinks are not followed, gitlinks are not recursed, special files are not read,
     and a file that changed under the read yields **no digest at all**. The **file-count** bound
     withholds the whole inventory rather than truncating it (a truncated list reads like a complete
     one); the **byte** bounds mark individual entries; `content.coverage.complete` says which happened.
     **Fact layer only** — no candidate, no significance, no score, no ranking, no inference, no LLM, no
     review state, and no invention terminology in the shared contract.
   - **`LHP-INTEL-03B` — done.** Profile-neutral `pattern_candidate.v1` + **one** profile-owned rule
     (`repository_exact_content_recurrence`, owned by `repository_capability_discovery`) + operator
     disposition — `lighthouse patterns`. No score, no rank, no promotion, no activation (ADR-0021).
   - **`LHP-INTEL-04A` — done (analysis); accepted.** The profile-portability checkpoint. Finding:
     `pattern_candidate.v1` is portable in its neutral spine, with one concrete limitation
     (detector-owned vocabularies, deferred to 04C); Scientific Preservation is blocked on a missing
     **fact** — the store could not record that Lighthouse *looked* — so a fact packet precedes the rule.
   - **`LHP-INTEL-04B` — done.** Hardened public-source check facts: `lighthouse public-sources`, one
     HTTPS chokepoint + an append-only check ledger; network forbidden elsewhere; a `not_found` is HTTP
     404/410, never disappearance (ADR-0022). Fact layer only — no candidate, no score, no LLM.
   - **`LHP-INTEL-04C` — done.** `scientific_preservation`-owned `disappearing_scientific_source` over
     the check facts, reusing the candidate spine via one bounded generalization (ADR-0023).
   - **`LHP-INTEL-04D-R1` — done.** Producer-side `observed_source_reference.v0` export from one
     validated `available` public-source check (`lighthouse public-sources export-osr`).
   - **`LHP-V1-RCD-01A` — done.** Isolated RCD operator loop documented and proven
     (`docs/V1_OPERATOR_WORKFLOW.md`, `tests/unit/test_v1_operator_loop.py`).
   - **`LHP-V1-PROFILE-01C/01E` — done (finish-state branch).** Backup-gated initial activation of
     `repository_capability_discovery` v2; `active_profile_store`; observe/detect admission;
     historical reads/reviews ungated. **Not** profile switching or SciPres activation.
   - **`LHP-LIFE-DESIGN-01` — done (docs/ADR only).** Controlled profile-switch lifecycle design
     ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)): invariants, attempt journal,
     cutover boundary, admission fence, migration/rollback limits. **No implementation.**
   - **`LHP-LIFE-DESIGN-01-C2` — done (docs/ADR only).** Adversarial corrections per R1 review:
     authority projection, transition-control record, state-machine repairs, mutating-surface inventory,
     reordered implementation sequence. **No implementation.**
   - **`LHP-LIFE-DESIGN-01-R3` — done (review only; verdict FIX REQUIRED).** Read-only
     commit-readiness review of C2; architecture/documentation corrections deferred to C3.
     **No implementation.**
   - **`LHP-LIFE-DESIGN-01-C3` — done (docs/ADR only).** Final determinism corrections per R3
     High/Medium findings and six Low inconsistencies. **No implementation.**
   - **`LHP-LIFE-DESIGN-01-C4` — done (docs/ADR only).** Final comparison correction per R4
     findings (`source.allowed_source_types` partition, `backup.data_root` wording, enrollment
     provenance stamps). **No implementation.**
   - **`LHP-LIFE-DESIGN-01-R5B` — done (review only; verdict FIX REQUIRED).** Read-only
     commit-readiness review of C4 lifecycle design; state-machine/history corrections deferred to C5.
     **No implementation.**
   - **`LHP-LIFE-DESIGN-01-C5` — done (docs/ADR only).** Final commit-gate corrections per R5B
     findings (cutover phase assertion; C4/history synchronization). **No implementation.**
   - **`LHP-TEST-ISOLATION-01` — done (test harness).** Repository pytest runtime-data isolation
     (commit `945fba7`): paths established before application imports; cacheprovider disabled;
     six sentinel tests bind both persistence paths to their temporary DB; 16 isolation
     regression cases. Production modules unchanged. Guarantee applies to repository pytest
     invocations that load `tests.runtime_isolation`. Isolation verification evidence at
     completion: **2420 passed, 0 failed, 0 skipped**.
   - **`LHP-LIFE-TRUTH-01` — done.** Commit `8399764`. Three stale CLI claims corrected in
     `main.py`: profiles list states initial RCD v2 activation via `profile-activate` while
     switching/deactivation remain unimplemented; transition-preview current-profile omission no
     longer claims "none active — activation not implemented"; `--current-profile` help states
     operator-supplied input not store-derived. Two new behavioral tests. Clean-checkout:
     **2422 passed, 0 failed, 0 skipped**. CLI truth only — no lifecycle implementation;
     `UX-CLI-01` / `CORE-RESOLVE-01` capability states unchanged.
   - **`LHP-V1-OPERATOR-TRUTH-01` — done.** Walkable V1: `restore-drill --output`,
     explicit `backup-verify` vs `backup-create` `passed` refusal, reconciled operator docs,
     full CLI-only disposable rehearsal. No switching, no SciPres activation, no Gateway.
   - **`LHP-LIFE-EVID-01` — done.** Typed verify/restore reports bound to
     `manifest.json` file bytes; `artifact_digest` matches the `active_profile_state` recipe;
     substitution and untyped/`passed` evidence refuse. Preview `ready_for_review` requires
     `status=verified`. Earlier figures **2474 passed, 0 failed, 0 skipped** and
     **2500 passed, 0 failed, 0 skipped** were not reproduced at `47f25ac` (A4 measured
     2486 passed, 5 skipped). Measured 2026-10-14 on local unpushed branch
     `fix/lh-evid01-repair` (base `83c16dd`): `pytest tests/unit -m "not integration"`
     (no network) — **2540 passed, 5 skipped, 0 failed**; 32 of the new/changed tests
     (incl. `tests/unit/test_evidence_report_typing.py`) failed on the base and pass after
     the fix; a follow-up SQLite-integrity consistency check added 6 further regression
     tests that failed on the pre-fix tree and pass after it. Skips are
     environment-dependent (fastapi absent: `test_local_api`, `test_portal_intake`;
     jsonschema absent: test_pattern_candidate (2 tests) and
     test_v1_profile_activation::test_real_active_state_validates_against_schema_mirror)
     — those paths were not exercised. Preview digest closed: activation recomputes the
     preview's canonical artifact digest before dry-run or real activation
     (`modules/profile_activation.py`). Residual: restore is not re-run at activate
     (EVID-02). Repair (`fix/lh-evid01-repair`, local): D-1 full field value-type
     checks / no `from_dict` coercion; D-2 status/detail consistency so contradictory
     reports cannot make a preview `ready_for_review`; H-1 malformed values raise
     `ReportValidationError` instead of `TypeError`. No switching, SciPres activation,
     or Gateway.
   - **Next lifecycle packet:** `LHP-LIFE-EVID-02` (optional residual: restore↔verify pair
     bind) or `LHP-LIFE-RESOLVE-01` (runtime policy
     resolver, `CORE-RESOLVE-01`);
     parallel analysis `LHP-SCPRES-GAP-01` (Scientific Preservation activation readiness);
     then **selective** invention migration — one detector family at a time, never a bulk copy.
2. **Phase D** — keep external source packs observation-tier and Gateway-admitted
   ([ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)); they are a
   *different source class* from the local operator sources `LHP-INTEL-01` ingests
   ([ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md)).
3. **Profile activation** — **partially resumed for RCD only** on the finish-state branch
   (`lighthouse profile-activate` → `repository_capability_discovery` v2). Active-to-active
   switching design is complete ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)) but
   **not implemented**. SciPres activation, Gateway bridge, and automatic activation remain
   **not built** ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)).
4. **BitAgent payment gate** — deferred until ≥2 weeks of Start9 live data has accumulated (backlog:
   [`FUTURE_PLAN.md`](FUTURE_PLAN.md)).
5. **Patent ODP recovery** — remains gracefully degraded until a confirmed adapter
   ([`FUTURE_PLAN.md`](FUTURE_PLAN.md)).

## Active Operational Concerns

- **Profile switch lifecycle** — design complete ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md));
  R3 and R5B reviews (FIX REQUIRED) and C3/C4/C5 docs-only corrections applied (typed projection
  semantics, `indeterminate` refuses unconditionally, pointer-bound recovery, Gateway widening-only
  authorization, migration cutover checkpoint binding). Implementation not started. Any future switch
  packet must follow ADR-0024's state machine, cutover boundary, and default-deny authority rule
  (`locally_activatable` enrollment currently empty).
- SEC dedup fixed (stable `dedup_key` on accession + company + form type).
- ADSB military/interesting classification still incomplete.
- DoE archive target: no verified RSS feed yet (HTML selector/fallback only).
- DoD archive: defense.gov redirects to war.gov; archiver follows the redirect; update `ArchiveConfig`
  once a canonical feed is confirmed.
- Patent provider disabled: `search.patentsview.org` decommissioned; set
  `PATENT_PROVIDER=legacy_patentsview` only if a working endpoint is found.
- `entities.json` needs periodic EDGAR hand-validation before trusting correlator entity matching.
- Start9 volume resets on uninstall — take a StartOS backup before any package update to preserve
  historical intelligence (`state.db`, callsign history, graph). See
  [ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md) and [`OPERATIONS.md`](OPERATIONS.md).

## Handoff Pointers

- **Direction / phases:** [`ROADMAP.md`](ROADMAP.md) · **History:** [`PACKETS.md`](PACKETS.md) ·
  **Backlog:** [`FUTURE_PLAN.md`](FUTURE_PLAN.md) · **Decisions:** [`adr/`](adr/README.md).
- **Security:** [`SECURITY.md`](SECURITY.md) (`LHP-SECURITY-01`) + [`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md).
- **Architecture / module map:** [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md). **Operator SOP:** [`OPERATIONS.md`](OPERATIONS.md).
- **Target profiles:** `data/targets.json` (`fusion_research`, `aerospace_defense`,
  `advanced_research_institutions`, `government_defense_labs`); validate with
  `lighthouse targets --show-routes`.
- **Dry-runs:** `lighthouse --module all --dry-run --since-days 7` (no DB writes, no IPFS, no Nostr).
- **Graph backfill (Start9):** `lighthouse graph-backfill --dry-run --limit 100`, review, then without
  `--dry-run`. Caution: old false-positive correlations also become graph-visible.
