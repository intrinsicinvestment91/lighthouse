# Lighthouse Packet History

> **This is the historical engineering log** — it answers *"How did we get here?"* Each packet is a
> completed (or blocked) unit of work in the order it happened. It is a record, not a live queue.
>
> For *where we are now* see [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md); for *where we are going* (the
> canonical phase model) see [`ROADMAP.md`](ROADMAP.md); for *what might come next* see
> [`FUTURE_PLAN.md`](FUTURE_PLAN.md); for *why* see [`adr/`](adr/README.md).
>
> **Numbering note:** early planning (a prior revision of this file) pre-assigned packets 24–28 to
> scopes that were later re-prioritised. The numbers below reflect **what actually shipped** under each
> number (confirmed against git history and [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md)). The originally
> planned-but-unshipped scopes (time-window baseline scoring, `promote_recurring_callsigns` aircraft
> nodes, signal-quality calibration, BitAgent, DoE feed, USPTO ODP) were moved to the backlog in
> [`FUTURE_PLAN.md`](FUTURE_PLAN.md).

## Shipped state

```text
Latest shipped:  LHP-V1-OPERATOR-TRUTH-01 — walkable V1 RCD ceremony + loop
Last numbered:   28 — Static documentation site upgrade
Current phase:   D — Repository Intelligence (see ROADMAP.md); INTEL 01–04D shipped
Next track:      LHP-LIFE-RESOLVE-01 — runtime policy resolver (CORE-RESOLVE-01)
System status:   YELLOW — operational, gaps noted
Test count:      2455 passing  (canonical count lives in ROADMAP_STATUS.md)
Start9 node:     the StartOS test server (v0.1.0)
```

---

## Shipped Packets 1–22

| Packet | Title | Summary |
|--------|-------|---------|
| 1–11 | Core pipeline | Signal → Alerts → Correlation → Graph → Query → MCP → Explanation; `AlertRecord`, `CorrelationRecord`, `GraphNode`/`GraphEdge` contracts; witness signing (BIP-340 Schnorr); A3 correlator; graph persistence wired into correlator |
| 12 | Graph persistence | Graph writes wired into correlator; `graph_nodes`/`graph_edges` tables |
| 13 | SEC alert dedup | Stable `dedup_key` on accession + company + form type; no duplicate correlation rows |
| 14 | Archive source discovery repair | RSS/Atom feed-first discovery, HTML link fallback; DoD/NASA `feed_url` in config |
| 15 | Start9 data path fix | `_data_path()` helper in `config.py`; path fields use `field(default_factory=...)`; `db.py` reads `DB_PATH`; volume persistence verified |
| 16 | arXiv researcher seed externalization | Default roster loads from tracked `seed/researchers.json`; inline fallback when absent; test fixtures isolated |
| 17 | Patent provider graceful degradation | `PATENT_PROVIDER=disabled` default; no DNS failure spam; `check_patent_provider_health()` for diagnostics |
| 18 | Graph backfill | `lighthouse graph-backfill` — idempotent upsert from `correlation_events`; dry-run capable |
| 19 | Facility alias normalization | Explicit `aliases` on `FlightConfig.facilities`; flight/ADSB targeted filtering uses alias-aware matching |
| 20 | Callsign enrichment foundation | Deterministic normalization, prefix classification, `CallsignEnrichment` metadata on ADSB/flight alerts via `modules/callsigns.py` |
| 21 | Callsign historical persistence | `callsign_observations` table; ADSB/flight persist on non-dry-run; `lighthouse callsigns` CLI |
| 22 | Weighted correlation scoring | Deterministic `modules/correlation_scoring.py`; signal/evidence bonuses; no ML, no external lookup |

> **Correction (2026-10-22):** "witness signing (BIP-340 Schnorr)" above is historical. In the
> current tree the production path stores a SHA-256 witness hash with an empty signature; the
> BIP-340 helper (`modules/_schnorr.py`) is demonstration/test-only.

---

## Packet 23: Start9 Operational Hardening — **done** (2026-05-26)

**Scope:** Seed targets/entities correctly on first run; document backup/restore SOP; remove dead config
variables. **Delivered:** `seed/targets.json` (4 default profiles) seeded to `/data/targets.json`
without overwriting operator edits; archive deny-page filter in `wayback_archiver.py`; dead
`LIGHTHOUSE_DB_PATH` manifest var removed; `docs/OPERATIONS.md` Start9 backup/restore/graph-backfill SOP;
static HTML explainer site added at `docs/site/`. Deploy + validation on the StartOS test server confirmed;
findings were recorded in internal run notes. Bonus fix: hardcoded `"data/targets.json"` paths in
`modules/targets.py` and `main.py` that bypassed `LIGHTHOUSE_DATA_DIR`.

---

## Packet 24: Temporal Intelligence Foundation — **done**

**Scope:** Deterministic temporal analysis over callsign/facility history. **Delivered:**
`modules/temporal_analysis.py` (callsign recurrence metrics, recent-activity windows, facility
aggregation, graph-prep helpers); `fetch_callsign_activity` / `fetch_facility_activity` /
`fetch_recent_activity` DB helpers; `lighthouse temporal` CLI; optional read-only `temporal_context`
injected into correlator evidence. No scoring/graph/Nostr changes.

---

## Packet 25: Graph-Native Callsigns — **done**

**Scope:** Persist callsigns as first-class graph entities. **Delivered:**
`build_graph_from_correlation()` emits persistent `callsign:`, `facility:`, and `prefix:` nodes with
edges `participated_in`, `observed_via`, `observed_near`, `has_prefix`; temporal metadata injected into
callsign node properties; `lighthouse graph --entity PAT16` resolves to `callsign:PAT16`. Backfill
remains idempotent. No scoring/Nostr changes.

---

## Packet 26: Mission Recurrence & Operational Tempo — **done**

**Scope:** Operational-tempo and mission-recurrence analysis. **Delivered:** `modules/tempo_analysis.py`
(`callsign_operational_tempo`, `facility_operational_tempo`, `identify_recurring_callsigns`,
`callsign_temporal_clusters`); `lighthouse tempo` CLI; `fetch_recurring_callsigns_summary` DB helper;
optional `tempo_context` enrichment in correlator evidence (informational only, never affects scoring).
No scoring/graph/Nostr changes.

---

## Packet 27: Private Local Intelligence Gateway — **done**

**Scope:** Bounded, read-only local dispatch layer. **Delivered:** `modules/local_gateway.py` (5
intelligence methods: `callsign.summary`, `facility.summary`, `correlations.recent`, `graph.summary`,
`intelligence.daily_brief`); `GatewayRequest` model; `/local/*` FastAPI routes with loopback-only
(`127.0.0.1`/`::1`) enforcement; deterministic, bounded (`MAX_DEPTH=3`, `MAX_LIMIT=50`), read-only;
evidence-stripped responses. No scoring/graph/Nostr/collector changes.

---

## Packet 28: Static Documentation Site Upgrade — **done**

**Scope:** Bring the `docs/site/` explainer up to date. **Delivered:** static HTML site reflecting
current architecture, Start9 operation, temporal intelligence, graph-native callsigns, and the private
local gateway. (Further expanded by ROADMAP-RECONCILIATION-01 — see below.)

---

## LHP-PROFILE-01: Intelligence Profile Foundation & Backup Readiness — **done**

**Scope:** A declarative profile contract, read-only registry, and backup-readiness inspector — **no
activation.** **Delivered:** `IntelligenceProfile` (`intelligence_profile.v1`) with fail-closed
validation (rejects unknown/future schema, executable-intent keys, private+public-publishing, DMS
enrollment, `data_root` escaping `/data`); read-only registry (`modules/profile_registry.py`) over six
`seed/profiles/*.json` seeds (none production-ready); backup-boundary model + read-only inspector
(`modules/backup_readiness.py`; `READY`/`NOT_READY`/`UNKNOWN`; canonical `/data`; eligibility ≠ completed
backup); read-only `lighthouse profiles` and `lighthouse backup-readiness` CLIs; architecture/security
docs + JSON schema + `docs/site/profiles.html`. **No profile activation, no DB migration, no
runtime/collector/publishing change, no invention-engine migration, no Gateway bridge; DMS remains
blocked.** Decisions recorded as [ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md)
… [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md).

---

## ROADMAP-RECONCILIATION-01: Documentation & Architecture Reconciliation — **done**

**Scope:** Documentation-only. Reconciled contradictory planning docs into a role-separated system
([ADR-0011](adr/0011-documentation-architecture-one-source-per-topic.md)); introduced the ADR log,
`ARCHITECTURE_INDEX.md`, the phase-based `ROADMAP.md`, `SECURITY.md` (`LHP-SECURITY-01`), and
`REPOSITORY_SOURCE_PACKS.md`; corrected the product name ([ADR-0012](adr/0012-naming-lighthouse-product-repo-identifier.md))
and the test count to 856; expanded the static site. No runtime/code/schema changes.

---

## LHP-PROFILE-02: Verified Backup Manifest & Isolated Restore Drill — **done**

**Scope:** Turn "backup readiness" (an *eligibility* judgement) into *evidence* — **no activation.**
**Delivered:** `modules/backup_manifest.py` (`lighthouse_backup_manifest.v1` — deterministic
authoritative-state inventory, SHA-256 digests, safe SQLite online-backup snapshots + integrity check,
path validation rejecting absolute/`..`/duplicate entries, fail-closed on symlink escape or special
files; embeds no file contents and no secrets); `modules/backup_create.py` (explicit destination only,
staged into a temp sibling then atomically renamed, independently re-verified before finalising);
`modules/backup_verify.py` (independent re-verification + an **isolated** restore drill that refuses
`/data` and everything beneath it by design, reporting what it proved and what remains an
`unproven_guarantee`); CLIs `backup-manifest` / `backup-create` / `backup-verify` / `restore-drill`.
Read-only toward the source; a completed backup or drill **never** authorises a profile activation.
Decision: [ADR-0013](adr/0013-verified-backup-and-restore-drill-before-profile-activation.md).

---

## LHP-PROFILE-03: Deterministic Profile-Transition Preview — **done**

**Scope:** A read-only review artifact answering "what would change if we switched?" — **no activation,
no switch, no Gateway approval.** **Delivered:** `modules/profile_transition.py`
(`profile_transition_preview.v1`): a deterministic declarative diff across all policy categories, backup
evidence consumed **by reference** (status tokens + a manifest fingerprint, never contents), a SHA-256
`artifact_digest` over canonical content, and a fail-closed status ladder where `ready_for_review` is
reachable **only** with two valid profiles plus present, passing evidence — otherwise `incomplete`,
`blocked`, or `invalid`. CLI `profile-transition-preview`. Decision:
[ADR-0014](adr/0014-transition-preview-artifacts-before-profile-activation.md).

---

## LHP-PROFILE-14A: Producer-Owned Execution Facts in the Preview — **done**

**Scope:** Give the preview the facts a future consumer needs, additively — **still no activation.**
**Delivered:** a typed `target_policy_snapshot` (exactly `local_only` / `dms_eligibility` / `data_root` /
`schema_migration_required`), a `target_profile_sha256` content digest binding the whole profile (not just
the diffed fields), a `backup_manifest_sha256` binding the manifest's bytes, and nullable
`lighthouse_version` / `repository_commit` provenance stamps — all digest-covered; the changes block now
carries all eight categories on the wire. Serialization version 1→2 (its designed purpose). Also fixed a
latent LHP-PROFILE-02 bug: version detection queried the console-script name instead of the distribution,
so it always returned `None`. Decision:
[ADR-0015](adr/0015-preview-carries-producer-owned-execution-facts.md).

---

## LHP-PROFILE-14B: Activation Capability Boundary (contracts only) — **done**

**Scope:** Specify the *shape* of activation without building any of it — **no store, no endpoint, no
CLI, no Gateway change.** **Delivered:** three pure contracts. `active_profile_state.v1` — the record a
future store *would* persist, with an explicit-absence discriminator (`from_dict({})` raises; absence is
declared, never defaulted), no half-states, and a digest that `validate()` **recomputes**.
`activation_preconditions` — a clock-free, non-raising evaluator returning `ready` / `blocked` /
`invalid`, where *unknown never becomes ready*. `activation_intent.v1` — the honest "I intend to move
A→B" artifact that can represent a blocked or invalid transition and whose `authorizes_activation` is a
digest-covered constant `false` (approved ≠ activated, structurally). Decision:
[ADR-0016](adr/0016-activation-capability-boundary.md).

---

## LHP-PROFILE-14C: Activation Capability Provider Contract — **done**

**Scope:** State, machine-readably, what activation capability Lighthouse claims to provide — the answer
being *"none yet."* **Delivered:** `lighthouse_activation_capability.v1`
(`modules/activation_capability.py`), a **provider self-description**: three declared operations mapped
1:1 onto the canonical Gateway capability ids, the contracts they would exchange bound to their live
schema versions, plus required evidence, limitations and security constraints. Availability is a **closed
vocabulary whose only member is `not_implemented`** — the contract has no word for "available" — backed
by an identity-checked constant `activation_available: false` that holds even when the digest is
re-stamped. It carries none of the Gateway's policy fields, so no duplicate authority is created, and an
attestation derived from it maps to "unavailable" and can never unblock a Gateway execution plan.
**Describes activation; provides none.** Decision:
[ADR-0017](adr/0017-lighthouse-describes-activation-capability.md).

---

## LHP-DIRECTION-02: Governance Freeze & Intelligence-Engine Priority — **done** (latest)

**Scope:** Documentation and direction only — no runtime change. **Delivered:** the profile-activation
ladder is **frozen at `LHP-PROFILE-14C`**. The finding that forced it: a profile has **no runtime
consumer** in Lighthouse — every policy field it declares is, at runtime, an environment variable or a
hardcoded literal, and the first activation target names two collectors (`repository_intake`,
`document_intake`) that do not exist. An activation built today would activate metadata rather than
capability, and its post-switch validation could only assert that the record it wrote contains what it
wrote — a safety check that cannot fail. The governance contracts stay in-tree, **dormant and fully
tested**; the Gateway is frozen at `GW-PROFILE-11` and untouched. Engineering moves to the observation
intake layer that gives a profile something to govern: **`LHP-INTEL-01`** (charter:
[`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md); first code packet `LHP-INTEL-01A`, `repository_intake`).
The one governance requirement that is **not** dormant — historical provenance stamping (`profile_id` +
`derivation_version` on every observation) — is adopted by the intake layer from day one. Decisions:
[ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md),
[ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md).

---

## LHP-FULL-ROADMAP-01A: Authoritative Full-Capability Roadmap — **done** (docs-only)

**Scope:** Documentation-only. **Delivered:** [`FULL_CAPABILITY_ROADMAP.md`](FULL_CAPABILITY_ROADMAP.md) —
the authoritative intended destination from released v1.0.0 through full-capability V2: authority
hierarchy, 110 stable capability IDs across 15 groups, bundled profile completion table (all six seeds),
twelve workstream mappings, nine ordered release trains, V2 definition of done, future packet discipline,
and immediate packet queue. Integration pointers in `ROADMAP_STATUS.md`, `ARCHITECTURE_INDEX.md`,
and `docs/site/roadmap.html`. **Does not change** the v1.0.0 shipped claim (local RCD
operator foundation). No runtime, test, schema, or seed changes.

> **Correction (2026-10-22):** "all six seeds" and "110 stable capability IDs" are historical; the
> public release bundles five profile seeds and the ledger now lists 109 IDs.

---

## LHP-FULL-ROADMAP-01D: Truth and Coverage Reconciliation — **done** (docs-only)

**Scope:** Documentation-only reconciliation after final review. **Delivered:** MCP shipped-truth
alignment (`SEC-MCP-01` shipped; Gateway cross-repo blocker preserved); profile link repair;
static Phase E V1 activation truth; `SEC-AUDIT-01` / `AUTH-AUDIT-01` boundary separation; eight new
capability IDs for legacy pipeline, source packs, DMS, and config semantics; Train 1 and immediate-queue
corrections; ledger recomputed to **110** definitions. No runtime changes.

---

## LHP-FULL-ROADMAP-01E: Current Activation Truth Correction — **done** (docs-only)

**Scope:** Documentation-only. Corrected global maps that still denied activation while preserving
historical and component-specific “no activation” statements. **Delivered:** registry vs
`profile-activate` distinction in the internal agent guide; `ARCHITECTURE_INDEX.md` shipped RCD
activation architecture; `roadmap.html` Phase E V1 truth with declarative seed caveat; `PACKETS.md`
01D count fix (eight new IDs). No runtime changes.

---

## LHP-LIFE-EVID-01: Typed, Bound Activation Evidence — **done**

**Scope:** Lifecycle hardening of existing RCD activation evidence. No switch, no SciPres
activation, no Gateway, no V1 product-claim change.
**Delivered:** `BackupVerificationReport` / `RestoreDrillReport` dataclasses
(`lighthouse_backup_verification.v1` / `lighthouse_restore_drill.v1`); `manifest_sha256` is the
exact `manifest.json` file-byte digest; `artifact_digest` reuses the `active_profile_state`
canonical-JSON SHA-256 recipe (exclude only itself). `load_evidence` and
`validate_supplied_*` refuse untyped/`passed`/mismatched reports. Preview pass vocabulary is
`verified` only — create-time `passed` no longer makes `ready_for_review`.
**Capability:** LIFE-EVID-01 `partial → shipped`.
**Verification evidence:** substitution tests (same `manifest_id`, different bytes; field
edit without restamp; restamped wrong `manifest_sha256`); new
`tests/unit/test_evidence_report_typing.py`. 32 new/changed tests (31 in
`tests/unit/test_evidence_report_typing.py`, 1 rehearsal directory-bin test) failed on base
`83c16dd` and pass after the fix; a follow-up SQLite-integrity consistency check added 6
further regression tests that failed on the pre-fix tree and pass after it. Measured
2026-10-14 on local unpushed branch
`fix/lh-evid01-repair`: `pytest tests/unit -m "not integration"` (no network) —
**2540 passed, 5 skipped, 0 failed** (not maturity proof) (skips: fastapi absent —
`test_local_api`, `test_portal_intake`; jsonschema absent — test_pattern_candidate (2 tests)
and test_v1_profile_activation::test_real_active_state_validates_against_schema_mirror;
those paths not exercised). Earlier figures 2474/2500 "0 skipped" were not reproduced at
`47f25ac` (A4 measured 2486 passed, 5 skipped). Residual: preview digest closed
(activation recomputes the preview's canonical artifact digest before dry-run or real
activation in `modules/profile_activation.py`); restore is not re-run at activate
(EVID-02). Repair (`fix/lh-evid01-repair`, local): D-1 every field of both report
schemas is value-type checked in `validate()` / `from_dict` no longer coerces; D-2
`validate()` refuses contradictory `verified`/`completed` reports so they cannot make a
preview `ready_for_review` via `load_evidence`; H-1 malformed values raise
`ReportValidationError` (`load_evidence` → `TransitionEvidenceError`) instead of
`TypeError`.

---

## LHP-V1-HARDEN-01A / 01B: Restore-Report Boolean Hardening — **done**

**01A (implementation):** `validate_supplied_restore_drill_report()` in `modules/backup_verify.py`
now requires JSON literal `true` for five positive restore-report fields
(`manifest_valid`, `payload_verified`, `restored_digests_verified`, `sqlite_integrity_verified`,
`source_unchanged`); Python/JSON truthy substitutes refuse. `files_restored` must be a non-Boolean
integer > 0. Parametric adversarial coverage in `tests/unit/test_v1_profile_activation.py`.
**Capability:** SEC-RESTORE-01 `planned → shipped`. **Verification evidence:** 257 focused + 2359
full unit passes (not maturity proof). LIFE-EVID-01 was completed later by LHP-LIFE-EVID-01.

**01B (docs-only):** Reconcile FULL_CAPABILITY_ROADMAP and PACKETS with the shipped contract;
remove stale Train 1 / immediate-queue entries for HARDEN-01. No runtime changes.

---

## LHP-V1-HARDEN-02: Activation-Writer Boundary Hardening — **done**

**Scope:** Private DB writer boundary; structural single-caller guard. **Commit:** `4f5f8c4`.
**Delivered:** activation DB writer removed from sanctioned public surface; exactly one current
production reference through the private store boundary; AST regression guards on current production
Python surfaces; production transaction body unchanged except private symbol rename.
**Verification evidence:** 2363 clean-checkout unit passes (not maturity proof); independent corrective
Opus review (COMMIT READY). **LIFE-ACTIVATE-02 remains partial** — accepted static-analysis and
durability limitations: underscore privacy conventional; power-loss/process-kill crash safety unproven;
cross-process concurrency unproven; switching unimplemented.

---

## LHP-V1-HARDEN-03B: Data-Path Semantics Enforcement — **done**

**Scope:** Ratify and enforce `LIGHTHOUSE_DATA_DIR` / `DB_PATH` contract (DEPLOY-CONFIG-01 semantics
half). **Delivered:** `modules/data_paths.py` canonical resolver; `DB_PATH` derives from
`LIGHTHOUSE_DATA_DIR` when unset; empty/whitespace env refused; `data_path_divergence` backup-readiness
check; removed `init_db("data/state.db")` literals; contract tests in `tests/unit/test_data_paths.py`.
**Not claimed:** StartOS manifest/entrypoint wiring (external repo). **Verification evidence:** 2376
unit passes; disposable-path rehearsal (`ops/v1_rcd_host_rehearsal.sh`).

---

## LHP-V1-HARDEN-03D: Data-Path Corrections — **done**

**Scope:** Close documented correctness gaps in 03B without redesigning storage architecture.
**Delivered:** resolver edge cases (`/`, trim, `data_path` escape rejection); backup-readiness
divergence when `--data-dir` supplied but `db_path` derived; honest two-database documentation;
`data_paths.py` as canonical env reader wording; expanded AST init_db guard; activation
`DataPathError` translation regression.
**Not claimed:** StartOS manifest/entrypoint wiring; universal host isolation; hot env
reconfiguration; one universal SQLite store. **Verification evidence:** 2394 unit passes.

---

## LHP-LIFE-DESIGN-01: Controlled Profile-Switch Lifecycle Design — **done** (docs/ADR only)

**Scope:** Design the complete fail-closed lifecycle for an active-to-active profile switch without
implementing it. Reconcile ADR-0003's deactivate-first sequence with the shipped singleton
active-profile store, narrow RCD initial activation, admission enforcement, transition previews,
backup evidence, and current Gateway boundaries.

**Delivered:** [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) — invariants, thirteen-state
attempt journal, atomic cutover boundary, admission fence, `CORE-RESOLVE-01` prerequisite,
migration model, rollback semantics, crash/concurrency matrix, authority rule, deactivation decision
(unsupported), audit evidence, threat model and prohibited claims. Reconciliation edits across
thirteen allowed documentation paths (fifteen after C2 append-only ADR notes). ADR-0003 append-only
supersession note.

**Not claimed:** profile switching, migration, rollback, deactivation, crash recovery, runtime policy
resolution, Gateway bridge, or any production code change. **Verification evidence:** docs-only
packet; no test count change (2404 unchanged).

---

## LHP-LIFE-DESIGN-01-C2: Adversarial Lifecycle Corrections — **done** (docs/ADR only)

**Scope:** Correct ADR-0024 and supporting documentation against every High and Medium finding in
LHP-LIFE-DESIGN-01-R1. Architecture/documentation only.

**Delivered:** ADR-0024 corrections — `profile_switch_authority_projection.v1` replacing INV-9's
four-field guard; `profile_transition_control` singleton (same DB as active profile and journal);
repaired state machine and Mermaid; durability caveats; Gateway decision binding; complete mutating-
surface inventory; concurrency matrix; reordered implementation sequence (rollback before cutover).
Append-only reconciliation notes in ADR-0001 and ADR-0016. Preview authority correction
(operator-supplied `--current-profile`, not store-derived). ADR index mirror sync (`docs/site/adr.html`
ADR-0003 status). ROADMAP_STATUS ADR-0023 link fix. Prohibited-claim cells for LIFE-MIGRATE-01 and
LIFE-ROLLBACK-01.

**Follow-up recorded (not in scope):** `LHP-LIFE-TRUTH-01` — correct stale `main.py` transition-preview
CLI text; requires code + tests before lifecycle implementation.

**Not claimed:** profile switching, migration, rollback, crash safety, locally authorized SciPres switch,
or any production code change. **Verification evidence:** docs-only packet; no test count change (2404
unchanged). Fifteen dirty paths total (thirteen from C1 + ADR-0001 + ADR-0016 append-only notes).

---

## LHP-LIFE-DESIGN-01-R3: Independent Lifecycle Review — **done** (review only; verdict FIX REQUIRED)

**Scope:** Final independent commit-readiness review of the C2 lifecycle design (LHP-LIFE-DESIGN-01-R3).
Read-only review; architecture/documentation corrections deferred to C3.

**Verdict:** **FIX REQUIRED** — H1 (absent-key defaulting in §2.1 vs worked example), H2 (enrollment
digest not revalidated at cutover), M1 (`unknown_or_corrupt` pointer missing), M2 (abort generation
disagreement), M3 (incomplete retention/disclosure extraction; `content_tier: "public"` unhandled),
plus six Low findings.

**Delivered:** Independent review artifact only. C2 reconciliation edits from the prior R2 pass remain
in the branch dirty set; R3 did **not** certify those edits as commit-ready.

**Not claimed:** R3 passed; presence-collapsing extraction accepted; profile switching, migration,
rollback automation, crash safety, locally authorized SciPres switch, or any production code change.
**Verification evidence:** docs-only review packet; no test count change (2404 unchanged).

---

## LHP-LIFE-DESIGN-01-C3: Final Determinism Corrections — **done** (docs/ADR only)

**Scope:** Correct every R3 High and Medium finding plus the six Low inconsistencies. Documentation
only; preserve C2 architecture.

**Delivered:** ADR-0024 C3 corrections — presence-tagged extraction with no absent-key defaulting
(§2.1); 20 `dimension_id` dimensions with complete per-field semantics including `content_tier`
`"public"` handling (§2.2); RCD→SciPres worked example table (§2.3); atomic enrollment digest
revalidation at cutover (§7); `unknown_or_corrupt` pointer classification (§6.1, Mermaid, crash
matrix); conditional abort generation rule (§5); UTC wall-clock vs process-local monotonic clock split
(§14). Reconciliation: BACKUP_READINESS summary, INTELLIGENCE_PROFILE_ARCHITECTURE enrollment
wording, site mirrors (`profiles.html`, `roadmap.html` 04D-R1 label).

**Not claimed:** R3 passed; profile switching, migration, rollback automation, crash safety, locally
authorized SciPres switch, Gateway bridge, or any production code change. **Verification evidence:**
docs-only packet; no test count change (2404 unchanged).

---

## LHP-LIFE-DESIGN-01-C4: Final Comparison Correction — **done** (docs/ADR only)

**Scope:** Resolve R4 M1 (`source.allowed_source_types` partition) and its four Low findings without
changing the accepted C2/C3 architecture. Documentation only.

**Delivered:** ADR-0024 C4 corrections — mutually exclusive `source.allowed_source_types` partition
(disjoint RCD→SciPres worked example ⇒ `indeterminate`, not widening); consistent `backup.data_root`
wording (`absent`/`null` ⇒ `indeterminate`; `malformed` escape ⇒ unconditional refusal before
comparison, never widening/narrowing); enrollment provenance/release stamps in §16 minimum attempt
record; ADR-0001 procedural phrase removed (substantive `indeterminate` rule preserved); ROADMAP_STATUS
R3/C3/C4 accuracy.

**Not claimed:** R4 passed; profile switching, migration, rollback automation, crash safety, locally
authorized SciPres switch, Gateway bridge, or any production code change. **Verification evidence:**
docs-only packet; no test count change (2404 unchanged). C4 touched only ADR-0024, ADR-0001,
ROADMAP_STATUS, and PACKETS.md.

---

## LHP-LIFE-DESIGN-01-R5B: Independent Lifecycle Review — **done** (review only; verdict FIX REQUIRED)

**Scope:** Read-only commit-readiness review of the C4 lifecycle design (LHP-LIFE-DESIGN-01-R5B).
Architecture/documentation corrections deferred to C5.

**Verdict:** **FIX REQUIRED** — M1 (§7 cutover phase contradiction: control record must be
`cutover_pending` while migration binding also required phase `staged`), plus two Low findings (C4
missing from ADR-0024 related-packets metadata; correction history and site callout not synchronized
through C4).

**Delivered:** Independent review artifact only. C4 reconciliation edits remain in the branch dirty
set; R5B did **not** certify those edits as commit-ready.

**Not claimed:** R5B passed; profile switching, migration, rollback automation, crash safety, locally
authorized SciPres switch, Gateway bridge, or any production code change. **Verification evidence:**
docs-only review packet; no test count change (2404 unchanged).

---

## LHP-LIFE-DESIGN-01-C5: Final Commit-Gate Corrections — **done** (docs/ADR only)

**Scope:** Close every R5B finding without changing the accepted C2/C3/C4 architecture. Documentation
only.

**Delivered:** ADR-0024 C5 corrections — migration-required cutover asserts a durable, completed
`staged` journal checkpoint (bound to attempt, target identity/digest, and migration-plan digest)
instead of requiring control phase `staged` while phase is `cutover_pending`; related-packets and
ROADMAP_STATUS/PACKETS/site history synchronized with C4; R5B recorded as review evidence.

**Not claimed:** R5B passed; profile switching, migration, rollback automation, crash safety, locally
authorized SciPres switch, Gateway bridge, or any production code change. **Verification evidence:**
docs-only packet; no test count change (2404 unchanged). C5 touched only ADR-0024, ROADMAP_STATUS,
PACKETS.md, and `docs/site/roadmap.html`.

---

## LHP-TEST-ISOLATION-01: Pytest Runtime-Data Isolation — **done**

**Scope:** Keep repository pytest invocations from writing into the ignored operator `data/` tree.
Test-harness only — production modules were not changed.

**Incident:** Earlier bare pytest runs contaminated the ignored operator `data/state.db` through
the secondary callsign writer and could overwrite archive output. The contaminated callsign rows
were removed through a separately verified database repair.

**Delivered:** Commit `945fba7`. Sixteen new isolation regression cases. Pytest runtime paths
(`LIGHTHOUSE_DATA_DIR`, `DB_PATH`) are established before application imports; cacheprovider is
disabled for the repository pytest configuration; the plugin-owned temporary runtime root is
cleaned idempotently. Six sentinel tests now bind both persistence paths to their temporary DB.
Persistent AST guards cover static repo-local writes, dual binding, and global
`subprocess.Popen` replacement.

**Verification evidence:** clean-checkout result **2420 passed, 0 failed, 0 skipped**.

**Limitations:** The guarantee applies to repository pytest invocations that load
`tests.runtime_isolation`. The plugin's own import can create the two documented
`tests/__pycache__` files before its code executes. Static AST guards cannot detect every
runtime-computed path. Replacement child-process environment mappings must explicitly preserve
`PYTHONDONTWRITEBYTECODE=1`. Production path behavior was not changed. The packet does not
claim zero bytecode files, and does not claim arbitrary external pytest configurations or
production execution are covered.

---

## LHP-LIFE-TRUTH-01: Profile Lifecycle CLI Truth — **done**

**Scope:** Correct stale profile-lifecycle CLI claims in `main.py` so operator-facing text matches
shipped RCD initial activation and does not imply switching or deactivation exist. Code + tests
(commit `8399764`); documentation mirrors closed by `LHP-LIFE-TRUTH-01D`. No lifecycle
implementation.

**Delivered:** Three stale CLI claims corrected: (1) `lighthouse profiles` note states initial
activation for `repository_capability_discovery` v2 through `profile-activate`, with switching and
deactivation remaining unimplemented; (2) transition-preview missing current profile prints
operator-omission wording ("not supplied — preview does not read active-profile state") instead of
"(none active — activation not implemented)"; (3) `--current-profile` help states operator-supplied
comparison input not derived from active-profile state. Two new behavioral tests.

**Not claimed:** Profile switching, deactivation, runtime policy resolution (`CORE-RESOLVE-01`),
fencing, migration, rollback, Gateway authorization, or any change to `UX-CLI-01` capability state.
CLI truth only. Ledger remains 110 IDs / 15 groups / nine trains.

**Verification evidence:** clean-checkout result **2422 passed, 0 failed, 0 skipped**.

**Next:** `LHP-LIFE-RESOLVE-01` (`CORE-RESOLVE-01`); parallel analysis `LHP-SCPRES-GAP-01` where
already listed.

---

## LHP-V1-OPERATOR-TRUTH-01: Walkable V1 operator truth — **this packet**

**Scope:** Make the already-shipped V1 RCD operator flow coherent from README through a complete
disposable CLI-only rehearsal. Capability IDs: `PROFILE-RCD-01`, `UX-CLI-01`, `LIFE-ACTIVATE-01`
(truth only). No switch, no SciPres activation, no Gateway, no OSR `--output`, no StartOS
packaging, no v1.0.0 retag.

**Delivered:** `lighthouse restore-drill --output`; `profile-activate` help/errors name
`backup-verify` (`verified`) vs `backup-create` embed (`passed`); preview warns on `passed`
without dropping `ready_for_review`; operator docs reconciled; `ops/v1_rcd_host_rehearsal.sh`
is a full CLI-only disposable ceremony + observe/detect/review/idempotent re-detect loop.

**Not claimed:** Profile switching, deactivation, SciPres activation, hypothesis/finding,
Gateway, MCP expansion, StartOS V1 completeness, or production `data/state.db` use.

---

## Future packet requirements

Every future implementation packet must (see also [`FULL_CAPABILITY_ROADMAP.md`](FULL_CAPABILITY_ROADMAP.md)):

1. Cite one or more **roadmap capability IDs**.
2. State the exact **current-to-target status change**.
3. Identify the **owning profile** or **profile-neutral** layer.
4. List **exact paths** to be created or modified.
5. Define **tests and rehearsal gates** before implementation.
6. Preserve **authority and privacy boundaries**.
7. Update [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) **only after evidence exists**.
8. **Stop before commit, activation, release, or push** unless explicitly authorized.

No packet may claim "complete," "production-ready," "finding," or "authorized" without satisfying the
corresponding roadmap gate.

---

## Deferred / re-prioritised (moved to backlog)

The following were planned under earlier packet numbers but not shipped as scoped; they now live in
[`FUTURE_PLAN.md`](FUTURE_PLAN.md): time-window baseline scoring + `facility_baselines`; recurring-callsign
promotion to `aircraft` graph nodes; signal-quality calibration (per-facility radius / proximity decay);
BitAgent payment gate; DoE archive feed (blocked on a verified RSS URL); USPTO ODP patent adapter
(blocked on confirmed endpoint schema). Also acknowledged there: per-researcher arXiv cadence baselines,
probationary correlation auto-promotion, behavioral anomaly scoring, temporal graph edges, graph export.

---

## Guardrails (do not override without an explicit decision)

These applied to every packet and remain in force (see also [`adr/`](adr/README.md)):

- Do not write to DB during dry-run.
- Do not publish raw alert payloads or full evidence objects via Nostr.
- Do not lower the Nostr publish gate (score ≥ 7.0, ≥2 unique signals).
- Do not expose MCP publicly without authentication.
- Do not introduce an LLM into the deterministic core pipeline ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)).
- Do not bypass `AlertRecord` / `CorrelationRecord` / `GraphNode` / `GraphEdge` contracts.
- Do not mix concerns (graph, correlator, MCP, CLI) across modules.
- Do not add logging handlers or call `logging.basicConfig` in module code.
- Do not commit `.env`, `logs/`, `backups/`, or `data/` runtime files.
- Do not change DB schema without migration safety (add columns via the `_ensure_alerts_column` pattern).
- Prefer small, incremental packets — never rewrite a module wholesale.
