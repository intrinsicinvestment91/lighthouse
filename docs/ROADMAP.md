# Lighthouse — Architectural Roadmap

> **Canonical roadmap.** This document answers *"Where is Lighthouse going?"* It describes the vision
> and the architecture **phases** (A–K), superseding milestone/packet ordering as the way we reason
> about direction. For *where we are today* see [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md); for *how we
> got here* see [`PACKETS.md`](PACKETS.md); for *what might come later* see
> [`FUTURE_PLAN.md`](FUTURE_PLAN.md); for *why* see [`adr/`](adr/README.md).

## Vision

Lighthouse turns scattered public information into structured, defensible intelligence. It is a
**persistent, private, local-first intelligence platform** — not a collector framework and not a cron
job that dumps JSON. Its long-term operating model is **graph-native, temporally-aware correlation** of
multi-source public signals, with a strict split between the **engine** (Lighthouse) and the **control
plane** (the external operator gateway, a separate application not bundled with Lighthouse —
[ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)).

### It is a profile-driven platform, not a single-mission application

Lighthouse is a **sovereign, adjustable, profile-driven** intelligence platform
([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)). These are **first-class,
co-equal missions** — no one of them is the product identity:

**Public Scientific Lighthouse** · **Private Invention Discovery** · **Patent and Research Tracking** ·
**Scientific Preservation** · **Repository Capability Discovery** · **Aerospace and Defense Monitoring** ·
**Fusion Research Monitoring** · **Custom Operator Profile**

The **shared platform stays profile-neutral** — observation contracts, normalization, fact persistence,
provenance, exact identities, direct relationships, temporal history, candidate/hypothesis/finding
mechanics, counterevidence, operator review history, backup and restore, local query surfaces, and
privacy-enforcement foundations.

**Profiles own the interpretation** — approved source classes, enabled collectors, **detector families and
pattern rules**, evidence and counterevidence requirements, privacy and publishing policy, retention,
profile-specific candidate types, operator-facing outputs, disclosure-package eligibility, and
dead-man's-switch eligibility.

The current invention-oriented work **must not redefine every Lighthouse pattern as an invention.**

Enduring principles: deterministic core (no LLM — [ADR-0009](adr/0009-no-llm-in-deterministic-core.md));
declarative, reviewable policy (profiles are data, not code —
[ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md)); fail-closed safety
([ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md),
[ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md)); preserve intelligence
across change (backup-gated, provenance-stamped switches —
[ADR-0003](adr/0003-exactly-one-active-profile-controlled-switch.md)).

## Architectural evolution

```text
A Foundation → B Graph → C Temporal → D Repository Intelligence  (engine maturity)
                                          ↓
E Profiles → F Backup/Restore → G Gateway → H Execution Authority  (governed operation)
                                          ↓
I Scientific Preservation → J Dead-Man's Switch → K Public Release  (mission surfaces)
```

Phases A–C are complete; D is in progress; E–K are foundation-only, blocked, or future. Phase order is
a dependency guide, not a strict schedule — several later phases are gated on specific ADRs.

---

## Phase A — Foundation ✅ Complete

- **Purpose:** a reliable multi-source observation → alert → correlation core with fault isolation and
  attestable output.
- **Completed:** SEC/velocity/narrative/flight/ADSB (and arXiv/patent) sentinels; `AlertRecord` /
  `CorrelationRecord` contracts; A3 correlator with deterministic weighted scoring; witness hashing
  (BIP-340 signing helper demonstration/test-only); read-only MCP tools; `config.py` getter pattern;
  per-module `try/except` isolation.
- **Remaining:** none (maintenance only).
- **Dependencies:** none. **Blocking decisions:** [ADR-0009](adr/0009-no-llm-in-deterministic-core.md).
- **Risks:** collector endpoint decay (e.g. patent provider disabled). **Success criteria:** met —
  production runs produce real SEC + aviation signals.

## Phase B — Graph Intelligence ✅ Complete

- **Purpose:** make relationships between entities, events, and signals queryable.
- **Completed:** `GraphNode`/`GraphEdge`; SQLite persistence wired into the correlator; idempotent
  historical backfill (`lighthouse graph-backfill`); depth-limited traversal; graph-native `callsign:`
  / `facility:` / `prefix:` nodes; deterministic explanation engine.
- **Remaining:** temporal graph edges (event→precedes→event) — tracked in [`FUTURE_PLAN.md`](FUTURE_PLAN.md).
- **Dependencies:** Phase A. **Blocking decisions:** none.
- **Risks:** historical false-positive correlations become graph-visible on backfill. **Success
  criteria:** met — `lighthouse graph --entity …` and MCP graph tools return meaningful neighborhoods.

## Phase C — Temporal Intelligence ✅ Complete

- **Purpose:** treat repetition, timing, and silence as signal.
- **Completed:** callsign enrichment + historical persistence (`callsign_observations`);
  `temporal_analysis` (recurrence metrics, activity windows, facility aggregation); `tempo_analysis`
  (operational tempo, mission recurrence, temporal clusters); `lighthouse temporal` / `lighthouse
  tempo` CLIs; optional read-only temporal/tempo context injected into correlator evidence.
- **Remaining:** time-aware correlation *scoring* (currently informational only) — [`FUTURE_PLAN.md`](FUTURE_PLAN.md).
- **Dependencies:** Phases A–B. **Blocking decisions:** none.
- **Risks:** none material. **Success criteria:** met — recurring callsign/facility patterns are
  queryable and deterministic.

## Phase D — Repository Intelligence 🔄 In progress

- **Purpose:** extend observation to code repositories and documents, and make derived intelligence
  locally consumable by agents.
- **Completed:** the private **Local Gateway** (`modules/local_gateway.py`) — bounded, read-only,
  loopback dispatch (5 methods); the declarative `repository_capability_discovery` profile seed.
- **Remaining — this is the active track (`LHP-INTEL-01`):** the **observation intake layer**. Two source
  classes, deliberately separated ([ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md)):
  **local operator sources** (the operator's own repositories and documents — inside the trust boundary,
  no admission needed, **not blocked**; charter: [`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md)), and
  **external Repository Source Packs** (third-party repos — Gateway-admitted, still architecture-only;
  [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md),
  [ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)).
- **Shipped (`LHP-INTEL-01A`–`02B`):** observe → normalize → persist → inspect → ask *where else*. The
  layer accumulates facts and **cannot score**, deliberately and structurally.
- **The significance boundary is decided and accepted** (`LHP-INTEL-02C` →
  [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)). **Facts first. Interpretation
  second. Findings only after evidence and review.** The accepted order:

  | | Packet | Purpose | Boundary |
  |---|---|---|---|
  | ✅ | **LHP-ID-PRESERVE-01** | preserve the external invention-discovery prototype + its provenance | inventory → version control; **no migration** |
  | ✅ | **LHP-INTEL-03A** | **repository file-content identity** — `content_digest = SHA-256(exact raw bytes)`; joins repository files to standalone documents on the identity the document layer already uses | **fact layer only** — no candidate, no significance, no score, no ranking, no inference, no LLM, **no invention terminology in the shared contract** |
  | ✅ | **LHP-INTEL-03B** | minimal **profile-neutral** `pattern_candidate.v1` + **one** narrowly-scoped **profile-owned** deterministic rule (`repository_exact_content_recurrence`, owned by `repository_capability_discovery`) + operator disposition — `lighthouse patterns` | no finding promotion, no LLM, no publication, no execution; **no score, no rank, no activation** (ADR-0021) |
  | ✅ | **LHP-INTEL-04A** | **multi-profile portability checkpoint** (analysis) — **accepted**: `pattern_candidate.v1` is portable in its neutral spine; Scientific Preservation is blocked on a missing **fact** (the store could not record that Lighthouse *looked*), so a fact packet precedes the rule | one concrete limitation (detector-owned vocabularies) deferred to 04C; invention is not the only citizen |
  | ✅ | **LHP-INTEL-04B** | **hardened public-source check facts** — `lighthouse public-sources`, one HTTPS chokepoint + append-only check ledger; `content_digest = SHA-256(exact bytes)`; a `not_found` is HTTP 404/410, never disappearance (ADR-0022) | **fact layer only** — no candidate, no score, no scheduler, no body storage, no LLM, no Gateway |
  | ✅ | **LHP-INTEL-04C** | **Disappearing Scientific Source Candidate** — `scientific_preservation`-owned `disappearing_scientific_source` over the check facts, carried by a **closed detector-owned candidate-spec registry** (ADR-0023) with repository candidates byte-identical (no v2); conservative trigger from present facts only; first non-empty counterevidence; deterministic outage defense; recovery mutates no history | no finding, no publication, no Gateway/DMS; **the non-invention portability proof is complete** |
  | | **profile-portability checkpoint** | prove the candidate architecture carries a **non-invention** profile (Public Scientific Lighthouse / Scientific Preservation) before the invention profile redefines the platform | invention must not be the only citizen |
  | | **selective invention migration** | compare the preserved prototype against the accepted contracts; migrate **one detector family at a time** | quarantine the old scoring; **never a bulk copy** |

  **Why a fact packet comes before the first detector, and not the reverse:** `repository_intake` records
  file *paths and names* — **never file content**. So the system cannot yet distinguish *"two repositories
  independently implement the same capability"* from *"I copied my Dockerfile out of the last project"* —
  the dominant false-positive mode for a solo operator. A detector shipped first would present coincidence
  as a pattern. **The ordering is forced by the threat model.**
- **Dependencies:** Phase A pipeline. External source packs additionally need Gateway admission (Phase G);
  **local operator sources need nothing further.**
- **Blocking decisions:** [ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)
  (source packs are one observation source, not a second engine — **superseded in part**: they enter the
  fact-first pipeline, not the alert/correlation/graph path);
  [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md) (profile-neutral substrate,
  profile-owned detectors).
- **Risks:** scope creep into a parallel engine; treating external repos as anything but reference-only;
  **significance entering the fact layer accidentally** — through a convenience helper, a sort order, a
  graph weight, an alert score, or opaque model output.
- **Success criteria:** ingested sources feed the **fact substrate** as ordinary provenanced observations,
  deterministically, leaving every source unchanged; and no fact table, sort order or helper can hold an
  opinion about what matters.

## Phase E — Intelligence Profiles 🚧 Frozen at LHP-PROFILE-14C (activation deferred)

- **Purpose:** let one deployment be declaratively configured for a mission, switched safely.
- **Completed (LHP-PROFILE-01/02/03/14A/14B/14C):** `IntelligenceProfile` contract
  (`intelligence_profile.v1`) with fail-closed validation; read-only registry over the bundled
  profile seeds (five in this release; none production-ready); read-only `lighthouse profiles` CLI;
  verified backup manifest + isolated restore drill; the deterministic transition preview with
  producer-owned execution facts; the activation boundary contracts and the activation capability
  descriptor — all **dormant, no activation**.
- **Frozen (`LHP-DIRECTION-02`):** a profile has **no runtime consumer** — every policy field it declares
  is, at runtime, an env var or a literal — so an activation today would activate metadata, not
  capability, and its post-switch validation could only assert that the record it wrote contains what it
  wrote. Governance stops here until the intelligence engine exists
  ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)).
- **Remaining:** Resolved/Proposed/Active states — operator overrides, resolved configuration,
  activation, switching, and derivation-provenance stamping. **Nothing activates a profile today.**
- **Dependencies:** **a profile-aware runtime** — something that resolves a profile into effective
  configuration, and collectors that the profiles actually name (`LHP-INTEL-01`). This, not Phase F/G, is
  the binding prerequisite. Phase F (backup gate) is satisfied; Phase G (Gateway authorization) is
  required only for a *remote* switch, which is explicitly not the first activation.
- **Blocking decisions:** [ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md) (the
  freeze and its four resume conditions),
  [ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md),
  [ADR-0003](adr/0003-exactly-one-active-profile-controlled-switch.md).
- **Risks:** a switch silently reinterpreting past intelligence (mitigated by the provenance requirement,
  which `LHP-INTEL-01` adopts from day one); governance drifting further ahead of function.
- **Success criteria:** exactly-one-active enforcement; audited, reversible, backup-gated switch — and a
  post-switch validation that can actually fail.

## Phase F — Backup & Restore Verification 🧩 Partial

- **Purpose:** guarantee intelligence survives updates and profile switches.
- **Completed:** read-only fail-closed readiness inspector (`READY`/`NOT_READY`/`UNKNOWN`, canonical
  `/data` boundary, symlink/traversal-safe); proven live-backup method (ID-P1B) in
  [`OPERATIONS.md`](OPERATIONS.md).
- **Remaining:** verified backups and **restore drills** (readiness asserts *eligibility*, not a
  performed backup); automated pre-switch backup enforcement.
- **Dependencies:** none for inspection; StartOS backup for verification.
- **Blocking decisions:** [ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md).
- **Risks:** `DB_PATH`/`data_root` escaping `/data` (fails closed today). **Success criteria:** a
  restore drill reproduces `state.db` + graph + callsign history intact.

## Phase G — Gateway Integration 🚧 Blocked

- **Purpose:** let the Gateway read profile state, readiness, and evidence-stripped findings.
- **Completed:** the ownership boundary and the future read-only contract are specified.
- **Remaining:** a package-side private REST/`/local/*` surface, then the live bridge.
- **Dependencies:** a separate approved package-exposure packet.
- **Blocking decisions:** [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md)
  (blocked; only MCP-SSE:8001 is published today).
- **Risks:** exposing an unreviewed network surface. **Success criteria:** Gateway reads Lighthouse
  over a bounded, loopback/authenticated, read-only, evidence-stripped surface.

## Phase H — Execution Authority 🔭 Future

- **Purpose:** move execution/approval authority to the Gateway while Lighthouse keeps a fail-closed
  local gate as defense-in-depth.
- **Completed:** the live MCP approval gate exists in Lighthouse (current exception to ADR-0001).
- **Remaining:** Gateway-side authority; remediate the current fail-open-when-secret-unset behavior.
- **Dependencies:** Phase G. **Blocking decisions:**
  [ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md).
- **Risks:** any gate defaulting to "approve". **Success criteria:** all authority gates fail closed;
  Lighthouse gate remains an independent layer.

## Phase I — Scientific Preservation 🔭 Future (declarative today)

- **Purpose:** the public scientific / preservation mission (archival of at-risk public science).
- **Completed:** declarative profile seeds (`public_scientific_lighthouse`, `scientific_preservation`,
  `fusion_research_monitoring`); IPFS archival in the collector layer.
- **Remaining:** the preservation workflow as an activated mission (depends on Phase E activation).
- **Dependencies:** Phase E. **Blocking decisions:** [ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md).
- **Risks:** mission scope without activation. **Success criteria:** an activated preservation profile
  drives archival end-to-end.

## Phase J — Dead-Man's Switch ⛔ Blocked

- **Purpose:** a governed, gated timed-release capability.
- **Completed:** a portal DMS mechanism exists (register/checkin/broadcast).
- **Remaining:** a dedicated threat model + release state machine before *any* production behavior.
- **Dependencies:** Phase H (fail-closed release policy is Gateway-owned).
- **Blocking decisions:** [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md)
  (**production use blocked**; profiles may declare eligibility only).
- **Risks:** ungated ciphertext broadcast (the exact reason it is blocked). **Success criteria:** a
  reviewed threat model and a gated, witnessed release path — uncertainty delays release.

## Phase K — Public Release 🔭 Future

- **Purpose:** open the engine for public/community use.
- **Completed:** MIT-licensed codebase; the open-source strategy (open the engine, keep tuning private)
  is drafted in [`FUTURE_PLAN.md`](FUTURE_PLAN.md).
- **Remaining:** threat-model completion; MCP privacy/authentication; documentation completeness.
- **Dependencies:** Phases F–J maturity. **Blocking decisions:** [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md),
  [ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md).
- **Risks:** exposing tuning/operational strategy. **Success criteria:** a public engine with private
  intelligence data and safe defaults.

---

## Phase status summary

| Phase | Name | Status |
|-------|------|--------|
| A | Foundation | ✅ Complete |
| B | Graph Intelligence | ✅ Complete |
| C | Temporal Intelligence | ✅ Complete |
| D | Repository Intelligence | 🔄 In progress — active track (`LHP-INTEL`); 01A–04C shipped (04C: second profile-owned detector, ADR-0023) |
| E | Intelligence Profiles | 🚧 Frozen at 14C (ADR-0018; no activation) |
| F | Backup & Restore Verification | 🧩 Partial (eligibility only) |
| G | Gateway Integration | 🚧 Blocked (ADR-0010) |
| H | Execution Authority | 🔭 Future |
| I | Scientific Preservation | 🔭 Future (declarative) |
| J | Dead-Man's Switch | ⛔ Blocked (ADR-0006) |
| K | Public Release | 🔭 Future |
