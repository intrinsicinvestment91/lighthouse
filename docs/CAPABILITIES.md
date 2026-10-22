# What is Lighthouse?

> **The easiest document in this repository.** It answers one question — *"What can Lighthouse
> actually do today?"* — in plain English, without assuming you have read the architecture. Every
> capability here is labeled with an honest status. Where something is planned rather than built, it
> says so.
>
> For *where we are going* see [`ROADMAP.md`](ROADMAP.md); for *where we are now* see
> [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md); for *why decisions were made* see [`adr/`](adr/README.md);
> to find anything, start at [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md).

Lighthouse (git repository `pattern-match-sentinel`) is a **persistent, private, local-first
intelligence platform**. It watches public information — SEC filings, military aviation transponder
feeds, research publications, government newsrooms — normalizes each observation into a scored alert,
finds moments where several independent signals point at the same organization at the same time, and
records those moments as structured, queryable intelligence.

It exists because meaningful things happen in public but rarely in one place. A company files a
disclosure. An aircraft flies near a lab. A researcher goes quiet. None of these is conclusive alone;
together, at the same time, they are worth attention. Spotting that manually across many sources would
take hours a day. Lighthouse does it continuously and deterministically.

**Why local-first.** Lighthouse is designed to run on a home server (Start9). Your intelligence data —
the accumulated graph, callsign history, and correlations — never has to leave your machine. There is
no required cloud dependency.

**Why deterministic.** The core pipeline (correlation, scoring, graph, explanation) is ordinary code,
not a language model. The same inputs always produce the same outputs, and every finding can be
explained by rules you can read ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)).

**Why graph-native.** Every correlation becomes nodes and edges in a graph, so relationships between
entities, events, and signals compound and stay queryable as the system runs.

**Why Start9.** Start9 gives a private, self-hosted home server with persistent storage and a backup
model. Lighthouse packages as a Start9 app so an operator can run private intelligence without
renting cloud infrastructure.

---

## How to read this document

Each capability carries a **status label**:

| Label | Meaning |
|-------|---------|
| ✅ **Implemented** | Built, tested, and in use today. |
| 🧩 **Partial** | Core is built; important pieces are missing or manual. |
| 🧪 **Experimental** | Works but not relied upon; may change. |
| ⚙️ **Foundation only** | Contract/architecture exists; the behavior it enables does **not** run yet. |
| 🚧 **Blocked** | Deliberately not enabled pending a specific decision or dependency. |
| 🔭 **Planned** | Designed or desired; not started. |

Repository evidence always overrides historical planning. If a plan and the code disagree, the code
wins and this document follows the code.

---

# Section 1 — Current Core Capabilities

Capabilities are grouped by the role they play. Each entry states **what it does**, **status**,
**inputs → outputs**, **why it exists**, **where it fits**, **primary modules**, **docs**, and
**tests**.

## Observation Collection

**Purpose.** Watch public data sources and turn each interesting thing into a raw observation.
**Status.** ✅ Implemented (some collectors degraded — see below).

- **Inputs:** public APIs and web pages — SEC EDGAR, OpenSky, adsb.lol, arXiv, agency newsrooms,
  USPTO/WIPO.
- **Outputs:** per-source Python dicts and JSON snapshots under `data/<source>/`, fed forward into the
  alert layer.
- **Why it exists:** intelligence starts with observation. Each collector is domain-specific and
  isolated so one failing source never stops the others (`main.py` runs every module in its own
  `try/except`).
- **Where it fits:** the first stage — `Collectors →` in the pipeline.
- **Primary modules:** `modules/sec_sentinel.py`, `flight_tracker.py`, `adsb_sentinel.py`,
  `arxiv_sentinel.py`, `narrative_analyzer.py`, `velocity_tracker.py`, `wayback_archiver.py`,
  `patent_sentinel.py`.
- **Related docs:** [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md), [`site/signals.html`](site/signals.html).
- **Related tests:** `tests/unit/test_sec_sentinel.py`, `test_flight_tracker.py`,
  `test_adsb_sentinel.py`, `test_arxiv_sentinel.py`, `test_velocity_tracker.py`.

What is watched today:

| Source | What it flags | Status |
|--------|---------------|--------|
| **SEC filings** | Material-event 8-K filings (items 1.01, 1.02, 2.01, 5.02, 8.01) from watched companies | ✅ Implemented |
| **Military aviation (ADSB)** | Military/interesting aircraft and AE-range hex codes near key facilities | ✅ Implemented |
| **Flight (OpenSky)** | Unusual aircraft around key facilities | ✅ Implemented |
| **arXiv researchers** | Unusual silence after a publication threshold | ✅ Implemented |
| **Government newsrooms** | Press releases archived + pinned to IPFS | ✅ Implemented |
| **Narrative analysis** | Tone/urgency shifts in press releases | 🧩 Requires `ANTHROPIC_API_KEY` |
| **Patent velocity** | Filing-rate anomalies per IPC class | ✅ Implemented (needs patent feed) |
| **Patent collection** | USPTO/WIPO full-text | 🧩 Provider disabled (graceful) — ODP adapter pending |

**Current limitations.** Patent collection is disabled by default (the PatentsView endpoint was
decommissioned); narrative analysis needs an optional API key; the DoE newsroom feed uses an HTML
fallback. All three degrade gracefully — the rest of the pipeline runs regardless.
**Future direction.** Recover a patent provider; confirm agency RSS feeds. See
[`FUTURE_PLAN.md`](FUTURE_PLAN.md).

## Alert Normalization (external signals)

> Not to be confused with **observation normalization** for the operator's own repositories (see
> *Local Repository Observation* below). This layer produces **scored alerts** — it is a triage
> surface, and that is exactly why the repository layer could not reuse it.

**Purpose.** Turn every raw observation into one canonical, scored, provenance-stamped alert.
**Status.** ✅ Implemented.

- **Inputs:** raw collector dicts.
- **Outputs:** `AlertRecord` objects (validated) persisted via `db.insert_alert()`; every alert carries
  `source`, `alert_type`, `score`, `score_label`, `provenance`, `payload`, `schema_version`.
- **Why it exists:** downstream stages must not care which collector produced a signal. A single
  contract makes correlation, querying, and the graph possible.
- **Where it fits:** `Collectors → Alerts →`.
- **Primary modules:** `modules/alerts.py` (contract), `modules/scoring.py` (5-dimension score),
  `modules/provenance.py`, `modules/db.py` (persistence).
- **Related docs:** [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md).
- **Related tests:** `tests/unit/test_alerts.py`, `test_scoring.py`, `test_db.py`.

**Current limitations.** Scoring dimensions are fixed weights; there is no learned scoring (by design).
**Future direction.** None required; this layer is stable.

## Weighted Correlation

**Purpose.** Find events where two or more independent signals point at the same entity within a
meaningful time window, and score them 1–10.
**Status.** ✅ Implemented.

- **Inputs:** alerts across sources.
- **Outputs:** `CorrelationRecord` events (schema v2) with a deterministic score, contributing signals,
  and deception-filter fields (`verification_status`, `source_trust_score`, `is_single_source`).
- **Why it exists:** a single signal can be coincidence; several independent signals aligned in time
  and entity are much harder to dismiss. Correlations — not raw alerts — are the publishable unit.
- **Where it fits:** `Alerts → Correlations →`.
- **Primary modules:** `modules/correlator.py` (4 detectors), `modules/correlation_scoring.py`
  (weighted scoring), `modules/correlations.py` (contract).
- **Related docs:** [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md), [`site/overview.html`](site/overview.html).
- **Related tests:** `tests/unit/test_correlator.py`, `test_correlation_scoring.py`,
  `test_correlations.py`.

**Current limitations.** Scoring uses signal mix, evidence quality, and multi-signal bonuses; it does
**not** yet weight temporal recurrence (that context is attached but informational).
**Future direction.** Time-aware correlation scoring — [`FUTURE_PLAN.md`](FUTURE_PLAN.md).

## Witness Attestation

**Purpose.** Record a deterministic witness hash for each correlation event so a later change can be detected;
cryptographic signing of that hash is a future intention.
**Status.** ✅ Implemented.

- **Inputs:** a correlation record. **Outputs:** a SHA-256 witness hash stored alongside the
  correlation; the signature field is empty in the production path (no key is passed), and the
  BIP-340 signing helper is demonstration/test-only.
- **Why it exists:** a stored SHA-256 witness hash lets a later change to a correlation be detected
  when compared against a trusted copy of the hash; on its own, a hash stored beside mutable data
  does not establish authenticity.
- **Where it fits:** attaches to correlation events before persistence.
- **Primary modules:** `modules/witness.py` (`build_witness_claim`, `witness_hash_hex`,
  `sign_witness_hash_hex`, `attach_witness`); `modules/_schnorr.py` (demonstration/test helper —
  probably adapted from the BIP-340 reference code; not for production keys; see
  [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)).
- **Related docs:** [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md). **Related tests:** `test_witness.py`, `test_schnorr.py`.

**Current limitations.** The production path passes no signing key, so correlations are stored unsigned (hash
only); the BIP-340 helper is demonstration/test-only. Survival-mode fallback also proceeds unsigned if witness
attachment fails (logged).
**Future direction.** Hash recording is stable as implemented; signed witness attestation (key
management and a production-grade signer) is future work.

## Graph Intelligence

**Purpose.** Store every correlation as a graph so relationships between entities, events, and signals
become queryable and compound over time.
**Status.** ✅ Implemented.

- **Inputs:** correlation records (live and historical). **Outputs:** `graph_nodes` / `graph_edges` in
  SQLite; depth-limited neighborhoods; deterministic human-readable explanations.
- **Why it exists:** a snapshot is not intelligence; a growing web of related evidence is. The graph
  answers "what has touched this entity, and what is nearby?"
- **Where it fits:** `Correlations → Graph →`.
- **Primary modules:** `modules/graph.py` (contract), `graph_builder.py`, `graph_backfill.py`
  (idempotent historical backfill), `graph_explainer.py`.
- **Related docs:** [`site/graph.html`](site/graph.html). **Related tests:** `test_graph.py`,
  `test_graph_builder.py`, `test_graph_backfill.py`, `test_graph_explainer.py`.

**Current limitations.** No temporal (event→precedes→event) edges yet; backfilling old data also makes
old false-positive correlations graph-visible.
**Future direction.** Temporal graph edges and graph export — [`FUTURE_PLAN.md`](FUTURE_PLAN.md).

## Callsign Intelligence

**Purpose.** Normalize and classify aircraft callsigns, persist a sighting history, and make callsigns
first-class graph entities.
**Status.** ✅ Implemented.

- **Inputs:** ADSB/flight observations. **Outputs:** `CallsignEnrichment` metadata on alerts;
  `callsign_observations` history; `callsign:` / `prefix:` / `facility:` graph nodes.
- **Why it exists:** the same military-style callsign reappearing near a facility over time is one of
  the strongest temporal signals the platform has.
- **Where it fits:** enriches the ADSB/flight collectors and feeds both the graph and temporal layers.
- **Primary modules:** `modules/callsigns.py`; persisted via `modules/db.py`; graph via
  `graph_builder.py`.
- **Related docs:** [`site/graph.html`](site/graph.html). **Related tests:** `test_callsigns.py`,
  `test_callsign_history.py` (see `tests/unit/`).

**Current limitations.** Prefix classification is deliberately conservative (known military-style
prefixes only); callsign history does **not** yet influence correlation scoring.
**Future direction.** Callsign-history weighting in scoring — deferred.

## Temporal Intelligence

**Purpose.** Treat repetition, timing, and silence as signal — recurrence, activity windows,
operational tempo, and mission recurrence.
**Status.** ✅ Implemented (informational).

- **Inputs:** `callsign_observations` and correlation timestamps. **Outputs:** recurrence metrics,
  activity windows, facility summaries, operational-tempo statistics (interval stats, weekday/hour
  distributions), recurring-callsign identification, temporal clusters.
- **Why it exists:** patterns only emerge from accumulated data — the reason Lighthouse is built to run
  continuously.
- **Where it fits:** `Graph → Temporal/Tempo →`; also injected as optional read-only context into the
  correlator.
- **Primary modules:** `modules/temporal_analysis.py`, `modules/tempo_analysis.py`; CLIs `lighthouse
  temporal`, `lighthouse tempo`.
- **Related docs:** [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md). **Related tests:**
  `test_temporal_analysis.py`, `test_tempo_analysis.py`.

**Current limitations.** Temporal context is **informational** — it enriches evidence but does not
change scores yet.
**Future direction.** Time-aware scoring — [`FUTURE_PLAN.md`](FUTURE_PLAN.md).

## Scientific Monitoring

**Purpose.** Track research activity (arXiv researcher rosters) and preserve at-risk public science.
**Status.** ✅ Monitoring implemented · ⚙️ preservation *mission* declarative.

- **Inputs:** `seed/researchers.json` roster via the arXiv Atom API; agency science newsrooms.
- **Outputs:** silence alerts; locally archived + IPFS-pinned pages.
- **Why it exists:** unusual research silence can precede breakthroughs; public science can disappear
  and is worth preserving.
- **Where it fits:** collector layer; the *preservation mission* is a declared profile, not an
  activated workflow.
- **Primary modules:** `modules/arxiv_sentinel.py`, `modules/wayback_archiver.py`; seeds
  `seed/profiles/scientific_preservation.json`, `public_scientific_lighthouse.json`,
  `fusion_research_monitoring.json`.
- **Related docs:** [`ROADMAP.md`](ROADMAP.md) Phase I. **Related tests:** `test_arxiv_sentinel.py`.

**Current limitations.** The preservation *mission* is declarative only — no profile activates it
today (see Intelligence Profiles). **Future direction.** Phase I, gated on profile activation.

## Local Observation Intake (repositories and documents)

**Purpose.** Observe **the operator's own** work — git repositories and documents, the first sources
Lighthouse has ever had for the operator's own material as opposed to the outside world — and turn what
is observed into facts with a **stable identity** the system can reason over later. **Status.** ✅
Repository observation (`LHP-INTEL-01A`), normalization (`LHP-INTEL-01B`), document observation
(`LHP-INTEL-01C`), the shared observation substrate (`LHP-INTEL-01D-A`), the runtime intake boundary
`lighthouse observe` (`LHP-INTEL-01E`), the read-only query surface `lighthouse observations`
(`LHP-INTEL-02A`) and cross-scope fact discovery `observations where|shared` (`LHP-INTEL-02B`) all
shipped. **No layer here scores anything.**

- **Inputs:** a local git repository path, or a local document (`.pdf`, `.md`/`.markdown`,
  `.txt`/`.text`) — and, optionally, the allowlisted roots it must sit under.
- **Outputs:** a validated, digest-bearing `repository_observation.v1` (27 facts) or
  `document_observation.v1` (14 facts) artifact — each fact carrying
  `type`/`value`/`source`/`timestamp`/`confidence` and **no field for significance** — normalized into
  provenanced entities and relationships in **three fact-only tables shared by both sources**. There is
  no document database and no second provenance system.
- **Identity is the point.** An observation is a transcript: `["Dockerfile", "compose.yml"]` is not
  addressable, and two repositories that both use Docker share nothing. Normalization produces
  8 entity types (`repository`, `document`, `file`, `directory`, `language`, `technology`, `remote`,
  `commit`) and 9 relationships, where `language`/`technology`/`remote`/`commit` are **global** — so two
  repositories join on `technology:docker`, and two clones of one project join on their root commit. A
  nested repository or checked-out submodule resolves to the identity it *would* have had if observed
  directly. A **document is identified by the digest of its bytes**, so the same file in two folders is
  *one entity observed twice*.
- **"No text" is not "could not read".** A document with `text.character_count == 0` at confidence
  **1.0** genuinely contains no text; one with `None` at **0.0** is a document whose text we could not
  extract (corrupt, undecodable, over bound, or no extractor installed). Collapsing those two into the
  same silence would manufacture a false conclusion, so the contract keeps them apart.
- **Metadata-first.** Document *text* is extracted only to establish facts about it — how much there is,
  what it hashes to — and is then **dropped**. The operator's document contents never reach the
  database; `text.digest` keeps a later re-extraction verifiable.
- **Host-only.** The deployed image ships no `git`, and neither the operator's repositories nor their
  documents are mounted into a network-facing container. This is a trust-boundary decision, not an
  oversight.
- **Why it exists:** Lighthouse watched SEC filings and military aircraft while knowing nothing about
  the operator's own projects. It is also the missing runtime consumer that froze profile governance
  ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)).
- **What it will never say:** which license this is, the "primary" language, the project type, quality,
  importance. A `LICENSE` file existing is a fact; what it *means* is a conclusion — and **no layer that
  draws conclusions exists yet**. That is enforced structurally: a normalized attribute key must belong
  to the closed 27-name observation vocabulary, which has no word for score, priority, importance,
  severity or quality, and no table column holds one either. There is nowhere to put an opinion.
- **Safety:** the repository is **never modified and never executed**. That is tested, not asserted:
  the whole tree, `.git/` included, is byte-identical after observing, normalizing and storing it.
  Remote-URL credentials are redacted; repository paths are host-local facts, stored and never published.
- **Idempotent by content identity:** digests exclude timestamps, so an unchanged repository observed a
  hundred times is stored once.
- **Asking what exactly a file is (`LHP-INTEL-03A`).** Every tracked repository file is identified by
  `content:<sha256>` — **`SHA-256` over its exact raw bytes, and nothing else**: not the path, not the
  name, not the mode, not the timestamps, not decoded text, not normalized line endings, and **not git's
  blob SHA-1** (which hashes a header *before* the bytes, and so could never match a loose file's
  digest). A **rename** keeps the identity; **one changed byte** replaces it. Because a standalone
  document is identified by the digest of its bytes too, *a repository file and a document with the same
  bytes are one entity with two occurrences* — and `lighthouse observations where --entity-id
  content:<sha256>` answers **"which of my projects contain these exact bytes?"** through the surface
  02B already built.
  - **Only tracked files are read.** Untracked and ignored files are never opened; symlinks are never
    followed; gitlinks are never recursed; special files are never read; and a file that **changed while
    it was being read produces no digest at all** — there is no retry, because a retry eventually catches
    *some* consistent state and presents it as the state that was observed.
  - **Incomplete coverage is always visible.** Every tracked path gets an accounted outcome in a closed
    vocabulary (`hashed`, `missing`, `unreadable`, `too_large`, `aggregate_limit_exceeded`,
    `symlink_unsupported`, `gitlink_unsupported`, `special_file_rejected`, `changed_during_read`,
    `invalid_path`), **only `hashed` may carry a digest**, and `content.coverage.complete` says whether
    the inventory is whole — so a later rule can never mistake a partial content inventory for full
    evidence.
  - **And it draws no conclusion.** Exact byte identity proves *exact byte identity*. It does not say the
    file was copied, which copy came first, who wrote it, whether two implementations were independent,
    or that any of it matters. Those are questions for a **profile-owned rule** that does not exist yet
    ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)).
- **Asking where else (`LHP-INTEL-02B`).** `lighthouse observations where --entity-id technology:docker`
  names every scope that entity was directly observed in; `lighthouse observations shared --scope-id A
  --scope-id B` returns the exact entities observed in **every** requested scope. **Cross-scope fact
  discovery performs exact lookup and direct set intersection over stored facts. It does not rank, infer,
  score, or interpret relationships.** Results are ordered by identity and *never* by count — ordering
  shared entities by how often they occur would be "the most common technology", which is a ranking. Note
  the structural consequence of the identity scheme: **two repositories that both contain a Dockerfile do
  not meet on the file** (a `file:` id embeds its scope) — they meet on `technology:docker`.
- **Asking *why it matters* — the layer that does not exist.** The significance boundary is **decided and
  accepted** ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)) but **nothing is
  built above the fact layer.** The rules, in short: **facts first, interpretation second, findings only
  after evidence and review**; a repeated fact is not automatically significant; a **candidate** is not a
  **hypothesis**, a hypothesis is not a **finding**, and a finding is **not permission** to publish or
  execute. **Detector rules are profile-owned**; the candidate/hypothesis/finding *mechanics* are
  profile-neutral. The next packet (`LHP-INTEL-03A`) is a **fact** packet, not a significance packet:
  repository file-content identity (`SHA-256` of the raw bytes), because the store today records file
  *paths*, never file *content* — and without that, "you built this twice" and "you copied this file"
  are indistinguishable.
- **Where it fits:** Phase D. No Gateway admission is required or involved — the operator's own files
  are already inside the trust boundary
  ([ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md)).
- **Primary modules:** `modules/repository_intake.py`, `modules/observation_normalizer.py`,
  `modules/observation_query.py`, `modules/db.py` (`observation_sets`, `normalized_entities`,
  `normalized_relationships`).
- **Related docs:** [`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md).
- **Related tests:** `tests/unit/test_repository_intake.py` (81),
  `tests/unit/test_observation_normalizer.py` (58).

**Current limitations.** **Host-only by decision:** the deployed image ships no `git` binary, and the
operator's repositories are not mounted into it — mounting them would extend a network-facing
container's trust boundary to cover the operator's private work, which needs its own packet. No CLI, no
`main.py` wiring, and no scheduled cadence: observation is invoked from Python. Nothing is scored,
alerted on, or added to the intelligence graph — a repository entering the graph is a claim that it
*matters*, and that layer is deliberately unbuilt. Documents (`document_intake`) are `01C`.

## Repository Source Packs (external repositories)

**Purpose.** Extend observation to *public, third-party* code repositories, treating a repository as
**one deterministic observation source** — never a second intelligence engine, never executed code.
This is a **different source class** from the local repositories above: foreign code requires Gateway
admission, the operator's own files never did.
**Status.** ⚙️ Architecture-only.

- **Inputs (designed):** admitted repository "source packs" as reference-only observations.
- **Outputs (designed):** ordinary alerts/correlations fed through the existing pipeline.
- **Why it exists:** public repositories are a rich public signal — but only as data to observe, with
  admission governed by the Gateway.
- **Where it fits:** Phase D; the *observation* side is Lighthouse, the *admission* side is the Gateway.
- **Primary modules:** none yet — declarative seed
  `seed/profiles/repository_capability_discovery.json`.
- **Related docs:** [`REPOSITORY_SOURCE_PACKS.md`](REPOSITORY_SOURCE_PACKS.md),
  [ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md).
- **Related tests:** profile validation only (`test_profile_registry.py`).

**Current limitations.** No ingestion engine is built; external repos are reference-only and are never
executed or installed. **Future direction.** Observation-tier ingestion behind Gateway admission.

## Repository Analysis / Invention Discovery

**Purpose.** Analyze repositories to discover latent inventions/capabilities.
**Status.** 🔭 Not in this repository (preserved privately; migration deferred).

- **Why it lives elsewhere today:** an invention-discovery prototype exists outside this repository
  (conceptually it belongs in Lighthouse). An inventory found it was not under version control; the
  Gateway held only a consumer/validator and one derived output. It is preserved privately, **not
  adopted** as the shared contract; migration is deferred and selective. No engine code lives here.
- **Where it fits:** future Lighthouse engine capability.
- **Primary modules:** none in this repo.
- **Related docs:**
  [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md),
  [`GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md`](GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md).

**Current limitations.** Nothing in `pattern-match-sentinel` performs invention discovery today.
**Future direction.** Selective migration into the Lighthouse engine once the boundary work lands.

## Intelligence Profiles

**Purpose.** Let one deployment be declaratively configured for a mission (which signals, entities,
keywords, policies) and — eventually — switched safely between missions.
**Status.** ⚙️ Declarative registry + **narrow initial activation** for
`repository_capability_discovery` v2 via `lighthouse profile-activate`. Switching, deactivation,
and every other bundled profile remain unimplemented.

- **Inputs:** `seed/profiles/*.json` (`intelligence_profile.v1`). **Outputs:** a validated, read-only
  registry and a `lighthouse profiles` CLI (list / show / validate / json).
- **Why it exists:** missions should be reviewable data, not code (
  [ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md)); and switching missions
  should be safe, audited, and backup-gated (
  [ADR-0003](adr/0003-exactly-one-active-profile-controlled-switch.md)).
- **Where it fits:** Phase E — the governance layer over the engine.
- **Primary modules:** `modules/profiles.py` (contract, fail-closed `validate()`),
  `modules/profile_registry.py` (read-only), `modules/profile_activation.py` (RCD v2 initial
  activation only). Five bundled profile seeds (`seed/profiles/`, one per bundled profile);
  **none production-ready**; only RCD v2 is activatable.
- **Related docs:**
  [`INTELLIGENCE_PROFILE_ARCHITECTURE.md`](INTELLIGENCE_PROFILE_ARCHITECTURE.md).
- **Related tests:** `test_profiles.py`, `test_profile_registry.py`.

**Current limitations.** Only `repository_capability_discovery` v2 can be activated
(`no_active_profile → RCD v2`). There is no profile switching, deactivation, SciPres activation,
or runtime policy resolution yet. Validation is deliberately fail-closed (it rejects unknown/future
schema, executable-intent keys, private+public-publishing, DMS enrollment, and a `data_root` outside
`/data`). **Future direction.** Backup-gated switching — Phase E, after the ADR-0024 lifecycle
packets. See [`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md).

## Pattern Candidates — the first candidate/review loop

**What it does.** Runs one deterministic, profile-declared rule over facts already in the store, and
lets you record one local decision about what it found.

```bash
lighthouse patterns detect --dry-run   # writes NOTHING — not a row, not a table, not the DB file
lighthouse patterns detect
lighthouse patterns list
lighthouse patterns show --id 9f3c1ab2
lighthouse patterns review --id 9f3c1ab2 --operator-id operator \
    --decision needs_more_evidence --reason "Repository lineage has not been assessed."
```

**The one rule so far** (`repository_exact_content_recurrence`, owned by the **Repository Capability
Discovery** profile): *a `content:<sha256>` entity directly observed in at least **two distinct
repository scopes***. In plain terms — **"the exact same bytes are in two of my projects."**

**What a candidate says:** *these direct facts satisfy one declared review rule; an operator should
look.* **What it never says:** that anything was copied, that one project derived from another, that
something was independently invented, that it is reusable, novel, patentable, or important. For a solo
operator reusing their own boilerplate, the dominant explanation for identical bytes is a clone, a
fork, a template, a generated file or a vendored copy — **the detector cannot tell those apart, so it
says so**, on every candidate, rather than picking the flattering one.

**Why you can trust it not to drift into an opinion.** The candidate has a **closed field set** — there
is no `score`, `rank`, `importance` or `significance`, and an unknown field is refused on the way in
*and* on the way out. Ten authority pins (`is_finding`, `is_verified`, `authorizes_release`,
`activates_profile`, `contacts_gateway`, …) are identity-checked constants. Candidates are ordered by
identity, **never by count** — ordering by "recurs in the most repositories" would be a ranking. And
nothing in the codebase branches on your decision, so `accepted_for_followup` cannot quietly become an
approval.

**Candidates are immutable.** The id *is* the content digest, so re-running over unchanged facts
reproduces the same candidate (nothing is duplicated), and changed evidence produces a **new version**
rather than editing the one you already reviewed. One decision per version, enforced by the database.

- **Modules:** `modules/pattern_candidate.py` (the contract), `repository_content_recurrence.py` (the
  rule), `pattern_candidate_service.py` (the boundary).
- **Related docs:** [`PATTERN_CANDIDATES.md`](PATTERN_CANDIDATES.md) ·
  [ADR-0021](adr/0021-pattern-candidates-are-review-objects-not-findings.md) ·
  [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md).
- **Related tests:** `test_pattern_candidate.py`, `test_repository_content_recurrence.py`,
  `test_pattern_candidate_service.py`, `test_cli_patterns.py`.

**Current limitations.** There is **no hypothesis layer and no finding layer** — a candidate is neither.
A disposition is a private note, not a verdict and not authorization to publish or execute. Evidence
reflects *stored observations*, not the current filesystem: the store can say *"last changed"* but not
*"last looked"*, so a repository observed once and never re-observed still reads as current (every
candidate discloses this). Reopening a decision is not implemented. **Future direction.** Prove the
contract carries a **non-invention** profile — Scientific Preservation's *Disappearing Scientific
Source Candidate* — before any further invention-oriented rule.

## Local MCP Interface

**Purpose.** Expose Lighthouse's read-only intelligence to tools and AI agents over the Model Context
Protocol, and over a private loopback dispatch layer.
**Status.** ✅ Implemented (read tools) · ✅ live-action approval gate fails closed (`LHP-SECURITY-01`).

- **Inputs:** MCP tool calls; local gateway requests. **Outputs:** bounded, read-only query results
  (`limit ≤ 100`, `depth ≤ 3`).
- **Why it exists:** intelligence is more useful when an agent can query it safely without touching the
  write path.
- **Where it fits:** `… → MCP / CLI / Local Gateway`.
- **Primary modules:** `portal/mcp_server.py` (14 tools across 5 phases); `modules/local_gateway.py`
  (5 loopback methods: `callsign.summary`, `facility.summary`, `correlations.recent`, `graph.summary`,
  `intelligence.daily_brief`; `MAX_DEPTH=3`, `MAX_LIMIT=50`; strips raw evidence).
- **Related docs:** [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md),
  [`site/local-gateway.html`](site/local-gateway.html).
- **Related tests:** `test_mcp_server.py`, `test_local_gateway.py`.

**Approval gate (fail-closed, `LHP-SECURITY-01`).** The MCP live-action gate
(`run_module`/`run_pipeline`/`publish_note`) grants approval **only** when `LIGHTHOUSE_APPROVAL_SECRET`
is configured (non-empty) *and* a matching `approval_token` is supplied. A missing/empty/whitespace
secret, a missing/empty/malformed token, unreadable configuration, or any validation exception all
**deny**. Comparison is timing-safe (`hmac.compare_digest`) and secrets/tokens are never logged. Dry-run
behavior is unchanged (the gate is only consulted for non-dry-run calls).
**Future direction.** MCP privacy/auth before public release; the Gateway-side `_is_human_approved`
fail-open remains a separate blocking cross-repo defect
([ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md)).

## Scientific Preservation Candidates (LHP-INTEL-04C)

`lighthouse patterns detect --detector scientific_source_disappearance_review` is the **second** citizen
of `pattern_candidate.v1` — the proof that the candidate architecture is not repository-specific. Owned
by the `scientific_preservation` profile, it reads only the append-only 04B check ledger (no network) and
produces one conservative `disappearing_scientific_source` candidate: a previously `available` admitted
public locator whose latest completed check is a narrow 404/410 not-found, at least seven days after the
last success, and not amid a broad outage. `not_found` is an HTTP answer, never disappearance; the subject
is the exact `content:<sha256>` last served; known public mirrors and stored local copies appear as
counterevidence (never independent, never auto-suppressing); recovery yields no current candidate and
mutates no history. The generalization that lets one contract carry two detectors is a closed,
code-owned detector-spec registry ([ADR-0023](adr/0023-detector-owned-candidate-specifications.md)), with
existing repository candidates byte-identical. See
[`SCIENTIFIC_PRESERVATION_CANDIDATES.md`](SCIENTIFIC_PRESERVATION_CANDIDATES.md). No finding, no
publication, no Gateway, no dead-man's switch; profile activation stays frozen.

## Public Source Checks (LHP-INTEL-04B)

`lighthouse public-sources` records **one neutral temporal fact** per attempt: *did Lighthouse check this admitted public locator, when, and what directly happened?* It exists because the fact store could not previously record that it *looked* (`observed_at` is excluded from every digest, failures wrote nothing), so *unchanged*, *unavailable*, and *never checked* were indistinguishable.

- **One hardened HTTPS chokepoint** (`public_source_intake.py`); network forbidden elsewhere in the fact-first pipeline. HTTPS-only, TLS verified, SSRF + DNS-rebinding defended, streaming bounded reads.
- **Empty-default host allowlist** (`LIGHTHOUSE_PUBLIC_SOURCE_HOSTS`) — empty refuses; a targets file may only narrow it.
- **Append-only check history** (never deduplicated — cadence is evidence); `content_digest = SHA-256(exact response bytes)`, joinable to repository/document content identity; **no response body stored**.
- **Closed factual outcomes** — a `not_found` is HTTP 404/410, never a claim a source is gone. **No candidate, no score, no scheduler, no LLM, no Gateway** (ADR-0022). Interpretation is a later, separate layer's job (LHP-INTEL-04C).

## Gateway Integration

**Purpose.** Let the external operator gateway (a separate application, not bundled with Lighthouse;
the thin control/approval/policy/audit plane) read Lighthouse profile state, readiness, and
evidence-stripped findings.
**Status.** 🚧 Blocked (boundary defined; live bridge not built).

- **Why it exists:** Lighthouse is the **engine**; the Gateway is the **control plane**
  ([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)). Keeping them separate
  keeps authority and observation cleanly split.
- **Where it fits:** Phase G.
- **Primary modules:** none in this repo (the Gateway is a separate application).
- **Related docs:** [`GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md`](GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md),
  [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md).

**Current limitations.** Only MCP-SSE (port 8001) is published from the Start9 package today; the live
bridge is blocked until a private REST/`/local/*` surface is exposed by an approved package-exposure
packet. **Do not confuse** this external "Gateway" with the in-repo "Local Gateway"
(`modules/local_gateway.py`). **Future direction.** Bounded, loopback/authenticated, read-only,
evidence-stripped surface.

## Start9 Integration

**Purpose.** Package Lighthouse as a private, self-hosted Start9 app with persistent storage.
**Status.** ✅ Implemented (deployed).

- **Inputs:** StartOS volume at `/data`. **Outputs:** persistent `state.db`, graph, and callsign
  history on the volume.
- **Why it exists:** private intelligence should run on hardware you own.
- **Where it fits:** deployment/runtime substrate.
- **Primary modules:** `modules/data_paths.py` (canonical resolver), `config.py` (`_data_path`),
  `modules/db.py` (`DB_PATH`), `scripts/install.sh`.
- **Related docs:** [`site/start9.html`](site/start9.html), [`OPERATIONS.md`](OPERATIONS.md).
- **Related tests:** `tests/unit/test_data_paths.py`, config/data-path unit tests.

**Current limitations.** The Start9 volume resets on uninstall — take a StartOS backup before any
package update. **Future direction.** No package-behavior changes planned in this packet.

## Backup Readiness

**Purpose.** Tell an operator, fail-closed, whether authoritative state is safely inside the `/data`
backup boundary — *before* trusting a backup — and let them inventory, copy, independently verify,
and restore-into-isolation a specific state set.
**Status.** ✅ Implemented (inspection + verified backup manifest + independent verification +
isolated restore drill) · ⛔ production restore and profile activation are separate, still-future
capabilities.

- **Inputs:** a boundary (default `/data`) and the resolved state paths. **Outputs:** `READY` /
  `NOT_READY` / `UNKNOWN` (fail-closed); `lighthouse backup-readiness`. Plus a versioned backup
  manifest and verified backup/restore-drill reports via `lighthouse backup-manifest` /
  `backup-create` / `backup-verify` / `restore-drill`.
- **Why it exists:** intelligence must survive updates and profile switches
  ([ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md),
  [ADR-0013](adr/0013-verified-backup-and-restore-drill-before-profile-activation.md)).
- **Where it fits:** operational safety, and the future backup gate for profile switching. Each
  ladder step fails closed and never implies the next; a completed backup/drill never authorizes a
  profile switch.
- **Primary modules:** `modules/backup_readiness.py` (`is_within_boundary`, `resolve_path` — rejects
  `..`/symlink escape; `inspect_backup_readiness`); `modules/backup_manifest.py`,
  `modules/backup_create.py`, `modules/backup_verify.py` (manifest, verified copy, independent
  verify + restore drill; SHA-256 + safe SQLite online-backup snapshots).
- **Related docs:**
  [`BACKUP_READINESS_AND_PROFILE_SWITCHING.md`](BACKUP_READINESS_AND_PROFILE_SWITCHING.md),
  [`OPERATIONS.md`](OPERATIONS.md). **Related tests:** `test_backup_readiness.py`.

**Current limitations.** `READY` means *eligible* — it is **not** proof a backup was performed. Restore
drills are manual today. **Future direction.** Verified backups + automated pre-switch enforcement —
Phase F.

## Transition Preview

**Purpose.** Before any future profile switch can even be *proposed*, produce a deterministic,
reviewable artifact answering: given a proposed transition and verified backup evidence, what would
change, what evidence supports it, and what would need operator approval? Foundation only — **not
activation, not approval.**
**Status.** ✅ Implemented (read-only review artifact) · ⛔ the preview itself still activates
nothing, switches nothing, and approves nothing. Narrow RCD v2 initial activation is a **separate**
command (`lighthouse profile-activate`) and requires `backup-verify` output (`status: verified`),
not `backup-create`'s embedded `passed` report.

- **Inputs:** a target profile id (and optional current profile id) from the bundled registry, plus
  optional references to LHP-PROFILE-02 evidence (`manifest.json`, verification report, restore-drill
  report). **Outputs:** a `profile_transition_preview.v1` artifact + `lighthouse
  profile-transition-preview` (human/JSON/`--output`).
- **Why it exists:** a switch must begin with "generate a complete transition preview," not "activate
  profile X" ([ADR-0014](adr/0014-transition-preview-artifacts-before-profile-activation.md)), and
  Lighthouse generates the evidence while the Gateway remains the future approval authority
  ([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)).
- **Where it fits:** the review layer between backup evidence (ADR-0013) and a future backup-gated
  switch (ADR-0003). Fail-closed: `ready_for_review` **only** with two valid profiles and present,
  passing evidence — otherwise `incomplete` / `blocked` / `invalid`; unknown never becomes ready.
- **Primary modules:** `modules/profile_transition.py` (contract + `validate()`, declarative
  `compare_profiles()`, `TransitionEvidence`/`load_evidence()` consuming LHP-PROFILE-02 reports by
  reference, `build_transition_preview()`). Deterministic SHA-256 `artifact_digest`.
- **Related docs:**
  [`BACKUP_READINESS_AND_PROFILE_SWITCHING.md`](BACKUP_READINESS_AND_PROFILE_SWITCHING.md),
  [`schemas/profile_transition_preview.v1.schema.json`](schemas/profile_transition_preview.v1.schema.json).
  **Related tests:** `test_profile_transition.py`, `test_cli_profile_transition.py`.

**Current limitations.** The preview **activates nothing, switches nothing, and approves nothing**.
`--current-profile` is operator-supplied; preview does not read active-profile state. A `passed`
create-time verification report **cannot** make preview `ready_for_review` and **cannot**
satisfy `profile-activate` — only a typed `backup-verify` report (`status: verified`) bound
to the manifest file bytes can. The approval *record* (operator identity, timestamp, decision, execution
authority) is Gateway-owned and not built here. **Future direction.** A Gateway approval workflow
consuming the preview (and its digest) as the object under review — Phase E/F.

## Activation Capability Boundary

**Purpose.** Make profile activation possible to *reason about* independently of the narrow RCD
ceremony. Three pure contracts define what an active profile IS, whether a transition's
preconditions would hold, and what a deterministic "I intend to move A→B" record looks like.
**Status.** ✅ Contracts implemented (LHP-PROFILE-14B/14C). Narrow RCD v2 initial activation
and `active_profile_store` are shipped separately (`lighthouse profile-activate`); the 14C
descriptor still describes a Gateway endpoint as `not_implemented`. **No switch, no Gateway
endpoint, no other targets.**

- **Inputs:** a `profile_transition_preview.v1` artifact (with its LHP-PROFILE-14A producer
  facts), an explicit `ActiveProfileState` (absence is constructed via
  `no_active_profile_state()`, never defaulted), and the target profile from the bundled
  registry. **Outputs:** an `active_profile_state.v1` record shape, a
  `ready`/`blocked`/`invalid` preconditions result (unknown never becomes ready), an
  `activation_intent.v1` artifact whose `authorizes_activation` is a digest-covered constant
  `false`, and (LHP-PROFILE-14C) a `lighthouse_activation_capability.v1` **descriptor** — the
  machine-readable answer to "what activation capability does Lighthouse claim to provide?",
  whose only expressible state is `not_implemented`. These contracts still do not switch,
  deactivate, or contact the Gateway. The shipped CLI ceremony is `lighthouse profile-activate`
  (RCD v2 only) — not a Gateway endpoint.
- **Why it exists:** the Gateway's canonical capability registry names
  `lighthouse.active_profile_store`, `lighthouse.profile_activation_endpoint`, and
  `lighthouse.post_switch_validation` as **missing**; each future capability should land against
  a tested contract instead of inventing one under pressure
  ([ADR-0016](adr/0016-activation-capability-boundary.md)).
- **Where it fits:** above the transition preview (it consumes the preview's digest-covered
  facts — the first Lighthouse-side consumer of `target_profile_sha256` and
  `backup_manifest_sha256`) and below any future activation capability. Approval authority
  remains Gateway-owned; "approved" and "activated" are structurally separate concepts.
- **Primary modules:** `modules/active_profile_state.py` (explicit-absence state record, no
  half states, recomputed digest), `modules/activation_preconditions.py` (read-only, clock-free
  evaluator), `modules/activation_intent.py` (honest intent artifact; its `artifact_digest` is
  the producer-side idempotency identity), `modules/activation_capability.py` (the provider
  self-description: three operations mapped 1:1 onto the canonical Gateway capability ids, the
  contracts they exchange, required evidence, limitations, security constraints — all declared
  `not_implemented`).
- **Why the descriptor cannot over-claim:** availability is a **closed state vocabulary** whose
  only member is `not_implemented` (there is no word for "available" to set), backed by an
  identity-checked constant `activation_available: false`. Neither can be reached by editing a
  value or re-stamping the digest — only by a new contract landing *with* the capability
  ([ADR-0017](adr/0017-lighthouse-describes-activation-capability.md)). It carries **no**
  Gateway-owned policy (trust floor, freshness, lifecycle status, audit status): a provider
  describes itself, it never grades itself.
- **Related docs:** [ADR-0016](adr/0016-activation-capability-boundary.md),
  [ADR-0017](adr/0017-lighthouse-describes-activation-capability.md),
  [`schemas/active_profile_state.v1.schema.json`](schemas/active_profile_state.v1.schema.json),
  [`schemas/activation_intent.v1.schema.json`](schemas/activation_intent.v1.schema.json),
  [`schemas/lighthouse_activation_capability.v1.schema.json`](schemas/lighthouse_activation_capability.v1.schema.json),
  rollback boundary in
  [`BACKUP_READINESS_AND_PROFILE_SWITCHING.md`](BACKUP_READINESS_AND_PROFILE_SWITCHING.md).
  **Related tests:** `test_active_profile_state.py`, `test_activation_preconditions.py`,
  `test_activation_intent.py`, `test_activation_capability.py`, `test_no_activation_boundary.py`.

**Current limitations.** Persistence is deliberately **absent** (artifact-only): the durable
store is itself the missing capability, so the only truthful record today is the explicit
`no_active_profile` state. Idempotency and rollback are **documented semantics only** — the
execution ledger, transition lock, and production restore remain future work owned by the
Gateway ladder. The capability descriptor **describes** activation; it provides none, and an
attestation derived from it maps to "unavailable", so Gateway execution plans stay honestly
blocked.

**Frozen (LHP-DIRECTION-02).** This ladder is deliberately closed at `14C`. A profile has **no runtime
consumer** in Lighthouse — every policy field it declares is, at runtime, an environment variable or a
hardcoded literal — so an activation today would activate metadata rather than capability, and its
post-switch validation could only assert that the record it wrote contains what it wrote. The contracts
above stay in-tree, dormant and tested. Activation resumes only when profiles have runtime consumers, the
collectors they name exist, intelligence outputs exist, and post-switch validation can actually fail;
when it does, it will be a **local operator CLI action**, not a remote Gateway mutation path
([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)). **Future direction.** The
observation intake layer first ([`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md)); then, and only then, a
profile→runtime resolver — the first thing that would ever *read* a profile.

## Security

**Purpose.** Keep dangerous actions safe-by-default and authority fail-closed.
**Status.** 🧩 Posture documented; one known fail-open defect.

- **Why it exists:** the platform can publish (Nostr), persist, and — eventually — act, so every gate
  matters.
- **Where it fits:** cross-cutting.
- **Primary evidence:** the Nostr publish gate (all of: `NOSTR_PUBLISH_CORRELATIONS=true`, not
  `dry_run`, score ≥ 7.0, ≥ 2 unique signals; only `entity`/`event_type`/`score`/`signals`/`summary`
  published); `dry_run=True` = zero side effects; the local MCP approval gate as defense-in-depth.
- **Related docs:** [`SECURITY.md`](SECURITY.md) (`LHP-SECURITY-01`),
  [`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md).

**Current limitations.** The MCP approval gate fails open when its secret is unset (ADR-0007); DMS is
blocked (ADR-0006); the full threat model is pending. **Future direction.** Fail-closed remediation and
a completed threat model before public release.

## Documentation, Static Site, Operator & Developer Experience

**Purpose.** Make the system understandable and operable.
**Status.** ✅ Implemented.

- **Documentation:** a role-separated system, one canonical source per topic
  ([ADR-0011](adr/0011-documentation-architecture-one-source-per-topic.md)); navigation hub
  [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md).
- **Static HTML site:** a dependency-free explainer mirror under `docs/site/` (open `index.html` in a
  browser — no build step). Markdown stays canonical; HTML explains.
- **Operator experience:** `lighthouse` CLI (`status`, `alerts`, `correlations`, `graph`, `callsigns`,
  `targets`, `temporal`, `tempo`, `profiles`, `backup-readiness`, `graph-backfill`), the Operator Guide
  ([`OPERATIONS.md`](OPERATIONS.md)), targeted mode, dry-run everywhere.
- **Developer experience:** `make install/lint/test`, 2455 unit tests, `pre-commit`, contracts that must
  not be bypassed.

**Current limitations.** Minor documentation debt remains (see Section 9 of the reconciliation history
in [`PACKETS.md`](PACKETS.md)). **Future direction.** Keep docs reconciled as the engine matures.

---

# Section 2 — How Lighthouse Thinks

The complete deterministic intelligence pipeline, in plain English. No step here is invented — each
maps to code.

```text
Observation            (a collector sees something public)
        ↓
Normalization          (it becomes one scored, provenance-stamped alert)
        ↓
Observation Store      (the alert is persisted in SQLite)
        ↓
Correlation            (independent signals on the same entity/time combine)
        ↓
Graph                  (the correlation becomes nodes + edges)
        ↓
Temporal Intelligence  (recurrence, timing, and silence are measured)
        ↓
Weighted Findings      (scored 1–10, witnessed, evidence attached)
        ↓
Operator Review        (a human reads it via CLI / MCP / Local Gateway)
        ↓
Gateway                (the external control plane — approval/policy/audit)   [boundary; live bridge blocked]
        ↓
Approved Action        (publishing/execution — gated, safe-by-default)
```

The first seven stages run today and are deterministic. The last two are governed: publishing is gated
and safe-by-default; the Gateway boundary is defined but the live bridge is blocked
([ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md)).

---

# Section 3 — Current Intelligence Pipeline (stage by stage)

| Stage | Inputs | Outputs | Persistence | Operator visibility | Review point | Current limitation |
|-------|--------|---------|-------------|---------------------|--------------|--------------------|
| **Observation** | Public APIs / pages | Raw collector dicts + JSON snapshots | `data/<source>/*.json` | Log output | — | Some collectors degraded |
| **Normalization** | Raw dicts | `AlertRecord` | `alerts` table | `lighthouse alerts` | — | Fixed scoring weights |
| **Observation store** | Alerts | Queryable rows | SQLite `state.db` | `lighthouse alerts --json` | — | — |
| **Correlation** | Alerts | `CorrelationRecord` (1–10) | `correlation_events` | `lighthouse correlations` | Deception filter marks single-source | No temporal scoring |
| **Graph** | Correlations | Nodes + edges | `graph_nodes`/`graph_edges` | `lighthouse graph --entity …` | — | No temporal edges |
| **Temporal** | Callsign/corr. history | Recurrence, tempo, clusters | Derived (read-only) | `lighthouse temporal` / `tempo` | — | Informational only |
| **Weighted findings** | Correlations | Scored + witnessed events | With `witness_hash`/signature | `lighthouse correlations --json` | Publish gate (≥7.0, ≥2 signals) | — |
| **Operator review** | Findings | Human reading | — | CLI / MCP / Local Gateway | **The main human review point** | — |
| **Gateway** | Findings (future) | Approval/policy/audit | External | — | Approval authority (future) | Live bridge blocked |
| **Approved action** | Approved findings | Publish/execute | — | Nostr note (gated) | Safe-by-default | Execution authority future |

---

# Section 4 — What Lighthouse Does NOT Do

These are intentional non-features, each backed by repository evidence.

- **No autonomous execution.** The core pipeline observes and reports; it does not act on the world on
  its own.
- **No autonomous publishing.** Nostr publishing requires all of: `NOSTR_PUBLISH_CORRELATIONS=true`,
  not `dry_run`, score ≥ 7.0, and ≥ 2 unique signals. Raw alerts are never published.
- **No cloud dependency.** It runs local-first on Start9; external APIs are optional inputs, not a
  required backend.
- **No hidden AI reasoning.** Correlation, scoring, graph, and explanation are deterministic code
  ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)). The optional narrative analyzer is the only
  LLM touchpoint and is outside the core.
- **No automatic profile switching.** Nothing switches or deactivates a profile. Narrow initial
  activation exists only for `repository_capability_discovery` v2 via `lighthouse profile-activate`
  (backup-gated, typed confirm, no Gateway). Profiles remain declarative data
  ([ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md),
  [ADR-0003](adr/0003-exactly-one-active-profile-controlled-switch.md)). A candidate review is not
  activation, and "approved" never means "activated". The 14C descriptor still declares the Gateway
  activation endpoint `not_implemented`
  ([ADR-0017](adr/0017-lighthouse-describes-activation-capability.md)).
- **No automatic invention publication.** The invention engine is not in this repository at all
  ([ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md)).
- **No autonomous Dead-Man's Switch.** The DMS mechanism exists but production use is blocked pending a
  threat model ([ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md)).
- **No silent approval bypass.** Live actions are gated; the one known fail-open case (unset MCP
  secret) is documented as a defect, not a feature
  ([ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md)).
- **No opaque scoring.** Every score comes from readable, deterministic rules, and correlations have a
  human-readable explanation engine.
- **No side effects in dry-run.** `dry_run=True` writes nothing — no DB, no IPFS, no Nostr.
- **No execution of external repositories.** Repository source packs are reference-only observations,
  never executed or installed
  ([ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)).

---

# Section 5 — Current Architecture (no prior knowledge assumed)

Five things are often confused. Here is what each one is:

- **Lighthouse** — this repository (`pattern-match-sentinel`). It is the **intelligence engine**:
  collectors, alerts, correlation, graph, temporal intelligence, profiles (declarative), backup
  readiness, and the read-only interfaces. Everything in this document lives here unless stated
  otherwise.
- **Gateway** — the separate Gateway application. It is the **thin control plane**: approval, policy,
  and audit. **No Gateway code lives in this repository.** The engine/control-plane split is
  [ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md); the live bridge between them
  is blocked ([ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md)).
  *(Not to be confused with the in-repo "Local Gateway," a read-only loopback dispatch layer.)*
- **Start9** — the self-hosted home-server platform Lighthouse packages for. It provides the persistent
  `/data` volume and the backup model.
- **Repository Source Packs** — a designed **observation source**: public repositories admitted (by the
  Gateway) as reference-only data for the existing pipeline. Not a second engine
  ([ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)).
- **Profiles** — declarative mission configurations (which signals/entities/keywords/policies). Data,
  not plugins; none active today.

```text
        ┌────────────────────────────────────────────┐
        │  Start9 (home server, /data volume, backup) │
        │  ┌──────────────────────────────────────┐  │
        │  │  LIGHTHOUSE  — the engine (this repo) │  │
        │  │  collectors → alerts → correlation →  │  │
        │  │  graph → temporal → findings          │  │
        │  │  profiles (declarative) · backup rdy  │  │
        │  │  read-only: CLI · MCP · Local Gateway │  │
        │  └──────────────────────────────────────┘  │
        └───────────────────┊────────────────────────┘
                            ┊  (bridge blocked — ADR-0010)
        ┌───────────────────┴────────────────────────┐
        │  GATEWAY — separate control plane           │
        │  approval · policy · audit                  │
        └─────────────────────────────────────────────┘
```

---

# Section 6 — Capability Matrix

| Capability | Purpose | Status | Primary module | Docs | Tests | Future phase |
|------------|---------|--------|----------------|------|-------|--------------|
| Signal collection | Watch public sources | ✅ (some degraded) | `modules/*_sentinel.py`, `flight_tracker.py`, `velocity_tracker.py` | [signals.html](site/signals.html) | ✅ | — |
| Patent collection | USPTO/WIPO full-text | 🧩 disabled | `modules/patent_sentinel.py` | [FUTURE_PLAN.md](FUTURE_PLAN.md) | ✅ | — |
| Alert normalization | One scored contract | ✅ | `modules/alerts.py`, `scoring.py` | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) | ✅ | — |
| Correlation + scoring | Multi-signal events | ✅ | `modules/correlator.py`, `correlation_scoring.py` | [overview.html](site/overview.html) | ✅ | — |
| Witness attestation | Integrity hash (change detection against a trusted reference) | ✅ | `modules/witness.py` | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) | ✅ | — |
| Graph intelligence | Queryable relationships | ✅ | `modules/graph*.py` | [graph.html](site/graph.html) | ✅ | temporal edges |
| Callsign intelligence | Aircraft entities/history | ✅ | `modules/callsigns.py` | [graph.html](site/graph.html) | ✅ | scoring weight |
| Temporal / tempo | Timing as signal | ✅ (informational) | `modules/temporal_analysis.py`, `tempo_analysis.py` | [ROADMAP_STATUS.md](ROADMAP_STATUS.md) | ✅ | time-aware scoring |
| Local Gateway | Loopback read-only dispatch | ✅ | `modules/local_gateway.py` | [local-gateway.html](site/local-gateway.html) | ✅ | — |
| MCP read tools | Agent-facing queries | ✅ | `portal/mcp_server.py` | [ARCHITECTURE_INDEX.md](ARCHITECTURE_INDEX.md) | ✅ | — |
| MCP approval gate | Guard live actions | 🧩 fail-open defect | `portal/mcp_server.py` | [SECURITY.md](SECURITY.md) | ✅ | Phase H |
| Intelligence Profiles | Declarative missions | ⚙️ declarative registry + RCD v2 initial activation | `modules/profiles.py`, `profile_registry.py`, `profile_activation.py` | [INTELLIGENCE_PROFILE_ARCHITECTURE.md](INTELLIGENCE_PROFILE_ARCHITECTURE.md), [V1_OPERATOR_WORKFLOW.md](V1_OPERATOR_WORKFLOW.md) | ✅ | Phase E |
| Profile activation | Safe switching | 🧩 **narrow RCD v2 initial activation shipped** — no switch, no other targets, no Gateway | `modules/profile_activation.py`, `modules/active_profile_store.py`, `modules/active_profile_state.py` | [V1_OPERATOR_WORKFLOW.md](V1_OPERATOR_WORKFLOW.md), [ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md), [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) | ✅ | Phase E |
| Backup readiness | Fail-closed eligibility | ✅ | `modules/backup_readiness.py` | [BACKUP_READINESS_AND_PROFILE_SWITCHING.md](BACKUP_READINESS_AND_PROFILE_SWITCHING.md) | ✅ | Phase F |
| Verified backup/restore | Survive updates | 🧩 manual | `docs/OPERATIONS.md` | [OPERATIONS.md](OPERATIONS.md) | — | Phase F |
| Local repository observation | Own repos as deterministic observations | ✅ shipped (LHP-INTEL-01A) — pure, host-only | `modules/repository_intake.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| Observation normalization | Observations → facts with stable identity, stored | ✅ shipped (LHP-INTEL-01B) — fact-only tables; **no scoring, no graph** | `modules/observation_normalizer.py`, `modules/db.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| Local document observation | Own PDFs/Markdown/text as deterministic observations | ✅ shipped (LHP-INTEL-01C) — metadata-first, **same fact tables**, host-only | `modules/document_intake.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| Observation substrate | The contract every observation source speaks | ✅ shipped (LHP-INTEL-01D-A) — model, confidence, digest, validator; owns no vocabulary | `modules/observations.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| Observation intake (runtime) | `lighthouse observe` — declared sources → facts | ✅ shipped (LHP-INTEL-01E) — trust boundary is `LIGHTHOUSE_OBSERVATION_ROOTS`, **empty fails closed**; host-only | `modules/observation_intake.py`, `modules/observation_sources.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| Observation query surface | `lighthouse observations` — inspect the recorded facts | ✅ shipped (LHP-INTEL-02A) — read-only enforced by SQLite (`mode=ro`); validate-on-read; **counts, never ranks** | `modules/observation_query.py`, `modules/db.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| Repository **file-content identity** | "What exactly is this file?" — `content:<sha256>` over exact raw bytes | ✅ shipped (LHP-INTEL-03A) — tracked files only, bounded streaming, fail-closed; a repository file and a standalone document with the same bytes are **one entity, two occurrences**, on the *existing* 02B surface. **No new table/column/index/CLI command** | `modules/content_identity.py`, `modules/repository_intake.py`, `modules/observation_normalizer.py` | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | ✅ | Phase D |
| The significance layer | "Why does it matter?" — scoring, ranking, discovery | 🚫 does not exist — deliberately unbuilt | — | [OBSERVATION_INTAKE.md](OBSERVATION_INTAKE.md) | — | Phase D+ |
| Repository Source Packs | External repos as observations | ⚙️ architecture-only | seed profile | [REPOSITORY_SOURCE_PACKS.md](REPOSITORY_SOURCE_PACKS.md) | — | Phase D |
| Invention discovery | Analyze repos | 🔭 preserved privately (migration deferred) | — (out of tree) | [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) | — | Phase D/H |
| Gateway bridge | Control-plane read | 🚧 blocked | — | [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md) | — | Phase G |
| Dead-Man's Switch | Timed release | 🚧 blocked | `portal/dms_checker.py` | [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md) | ✅ | Phase J |
| Safe Nostr publishing | Gated broadcast | ✅ gated | `modules/nostr_relay.py`, `main.py` | [SECURITY.md](SECURITY.md) | ✅ | — |
| Start9 packaging | Self-hosted runtime | ✅ | `config.py`, `scripts/install.sh` | [start9.html](site/start9.html) | ✅ | — |

---

# Section 7 — Operational Maturity

| Subsystem | Maturity | Why |
|-----------|----------|-----|
| SEC / ADSB / flight collection + correlation | **Operational** | Producing real signals in live Start9 runs. |
| Alert / correlation / witness contracts | **Production Ready** | Stable, fully tested, bypass-guarded. |
| Graph model + backfill | **Operational** | Live persistence; idempotent backfill proven. |
| Callsign + temporal/tempo intelligence | **Operational** | Live and deterministic (informational scoring). |
| Local Gateway + MCP read tools | **Operational** | Bounded, read-only, tested. |
| MCP approval gate | **Experimental** | Works, but fails open when secret unset (known defect). |
| Intelligence Profiles | **Foundation + narrow RCD activation** | Contract + registry + validation done; `lighthouse profile-activate` for RCD v2 only; no switch. |
| Backup readiness (inspection) | **Operational** | Fail-closed inspector live; verified backups still manual. |
| Repository Source Packs | **Architecture Complete** | Boundary + rules decided; no ingestion engine. |
| Gateway integration | **Blocked** | Boundary specified; live bridge awaits package exposure. |
| Invention discovery | **Not Started (here)** | Lives in the external Gateway; migration deferred. |
| Dead-Man's Switch | **Blocked** | Mechanism exists; production blocked pending threat model. |
| Public release | **Planning Complete** | Strategy drafted; gated on security/threat-model completion. |

---

# Section 8 — Roadmap Context

Lighthouse reasons about direction in **phases A–K**, not packet numbers. This is a summary; the
canonical source is [`ROADMAP.md`](ROADMAP.md).

- **Completed:** **A** Foundation, **B** Graph Intelligence, **C** Temporal Intelligence.
- **Current phase:** **D** Repository Intelligence — local observation intake through pattern
  candidates and OSR export are **shipped**; external source-pack ingestion remains
  architecture-only. Narrow RCD v2 initial activation is shipped; switching is not.
- **Next major phase:** still **D** for external source packs. Profile **switching** remains
  frozen pending the ADR-0024 lifecycle packets (`LHP-LIFE-RESOLVE-01` next). A profile-aware
  runtime resolver is not yet built
  ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)).
- **Blocked / future:** **G** Gateway bridge (ADR-0010), **H** Execution Authority, **I** Scientific
  Preservation (declarative), **J** Dead-Man's Switch (ADR-0006), **K** Public Release.

See [`ROADMAP.md`](ROADMAP.md) for each phase's purpose, dependencies, and success criteria, and
[`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) for the live status matrix.

---

# Section 9 — Glossary

- **Observation** — a single piece of public information a collector sees (e.g. an 8-K filing, an
  aircraft sighting). The raw input to everything.
- **Alert** — an observation normalized into the canonical `AlertRecord`: scored, provenance-stamped,
  persisted. The common currency of the pipeline.
- **Correlation** — a structured event where two or more independent signals point at the same entity
  within a meaningful time window, scored 1–10. The **publishable unit**.
- **Temporal Intelligence** — patterns derived from *timing*: recurrence, activity windows, operational
  tempo, silence.
- **Graph Entity** — a node in the correlation graph (a company, facility, researcher, callsign, or
  prefix) connected by edges to events and signals.
- **Repository Observation / Source Pack** — a public code repository admitted as *reference-only*
  observation data — never executed, never a second engine.
- **Finding** — a scored, witnessed correlation ready for human review.
- **Profile** — a declarative mission configuration (signals, entities, keywords, policies). Data, not
  code. Only `repository_capability_discovery` v2 can be activated today; switching is not shipped.
- **Gateway** — the external control plane (approval/policy/audit). Distinct from the in-repo "Local
  Gateway."
- **Local Gateway** — an in-repo, read-only, loopback dispatch layer that returns evidence-stripped
  intelligence.
- **MCP** — Model Context Protocol; how AI agents query Lighthouse's read-only tools.
- **Operator** — the human running Lighthouse and reviewing its findings.
- **Approval** — an explicit gate that a live action must pass before it happens (fail-closed by intent).
- **Deterministic** — same inputs always produce the same outputs; no hidden model, fully explainable.
- **Derivation** — the record of *how* a piece of intelligence was produced (provenance), important for
  profile switching so past findings are never silently reinterpreted.

---

# Section 10 — Frequently Asked Questions

**What is Lighthouse?** A private, local-first platform that watches public signals, correlates
multi-source evidence, and surfaces only high-confidence findings for human review.

**Why is it different from an LLM?** Its core is deterministic code, not a model. Findings are
reproducible and explainable by rules you can read ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)).
The only LLM touchpoint is an optional narrative analyzer outside the core.

**Does it require cloud services?** No. It runs local-first on Start9. External APIs are optional
inputs, not a required backend.

**Can it execute actions?** Not autonomously. It observes and reports. Publishing is gated and
safe-by-default; broader execution authority is a future, Gateway-owned capability.

**Why is the Gateway separate?** Clean separation of powers: Lighthouse is the engine (observation and
intelligence); the Gateway is the thin control plane (approval, policy, audit)
([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)).

**What is an Intelligence Profile?** A declarative mission configuration — data describing which signals
and entities to watch and under what policy. Reviewable, not a plugin. Only RCD v2 can be activated
(`lighthouse profile-activate`); switching and every other profile remain unimplemented.

**Why is everything local?** Privacy and ownership. Your accumulated intelligence stays on hardware you
control.

**Why Start9?** It provides a private, self-hosted home server with persistent storage and a backup
model — the right substrate for private, always-on intelligence.

**Is it done?** No. Phases A–C are complete and the engine is operational. Narrow RCD v2 initial
activation is shipped; profile switching, SciPres activation, the Gateway bridge, and public
release are still ahead ([`ROADMAP.md`](ROADMAP.md)).

---

# Section 11 — Lighthouse In One Page

> **Lighthouse** is a persistent, private, local-first intelligence platform. It watches public data —
> SEC filings, military aviation transponder feeds, research publications, government newsrooms —
> normalizes each observation into a scored alert, and finds the moments where several independent
> signals converge on the same organization at the same time. Those moments become **correlations**:
> scored, integrity-hashed, graph-linked findings that a human reviews.
>
> Its core is **deterministic** — correlation, scoring, and explanation are ordinary code, not a
> language model, so every finding is reproducible and explainable. It is **graph-native**: intelligence
> compounds over time as entities, events, callsigns, and facilities accumulate relationships. It is
> **temporally aware**: repetition, timing, and silence are treated as signal. And it is
> **local-first**: it runs on a self-hosted Start9 home server, so your intelligence never has to leave
> your machine.
>
> Lighthouse is deliberately restrained. It does not act autonomously, does not publish raw alerts,
> does not switch missions on its own, and uses no hidden AI in its core. Publishing is gated and
> safe-by-default; the external **Gateway** (a separate control plane) owns approval and policy.
>
> **Today:** SEC + aviation correlation, graph and callsign intelligence, temporal analysis, a
> read-only MCP interface, and a private local gateway are operational on Start9, backed by 2455 unit
> tests. Narrow RCD v2 initial activation is shipped (`lighthouse profile-activate`); switching is
> not. Lighthouse can now also **observe your own git repositories** deterministically — without
> modifying or executing them (`LHP-INTEL-01A`) — and **normalize** what it sees into stored facts with
> a stable identity, so two of your projects that share a technology, a remote, or a root commit share
> an entity (`LHP-INTEL-01B`), and the operator's documents enter the same fact boundary metadata-first
> (`LHP-INTEL-01C`). `lighthouse observe` runs that pipeline over the sources you declare
> (`LHP-INTEL-01E`), and `lighthouse observations` lets you **inspect what was recorded**
> (`LHP-INTEL-02A`) — read-only, and enforced as such by the store itself. Since `LHP-INTEL-03A` every
> tracked file also carries the identity of its **exact bytes**, so *"which of my projects contain this
> exact file?"* is answerable — and a copied file is finally distinguishable from an independently
> written one. A candidate is **not** a finding (ADR-0021). **Next:** the significance layer that is
> allowed to say a thing *matters* remains unbuilt. Profile **switching** stays frozen
> ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md),
> [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)).
>
> Built for operators, engineers, and researchers who want defensible intelligence from public data,
> on hardware they own.

---

# Section 12 — Repository Inventory

- **Implemented modules (`modules/`, 34 files):** collectors — `sec_sentinel`, `flight_tracker`,
  `adsb_sentinel`, `arxiv_sentinel`, `narrative_analyzer`, `velocity_tracker`, `wayback_archiver`,
  `patent_sentinel`; contracts — `alerts`, `correlations`, `graph`, `profiles`, `targets`; engines —
  `correlator`, `correlation_scoring`, `scoring`, `witness`, `graph_builder`, `graph_backfill`,
  `graph_explainer`, `temporal_analysis`, `tempo_analysis`, `callsigns`, `signal_router`; interfaces —
  `local_gateway`, `db`, `state_db`; governance — `profile_registry`, `backup_readiness`; support —
  `provenance`, `proxy_rotator`, `retry`, `validator`, `nostr_relay`, `_schnorr`.
- **Major documentation (`docs/`):** `ARCHITECTURE_INDEX.md`, `ROADMAP.md`, `ROADMAP_STATUS.md`,
  `PACKETS.md`, `FUTURE_PLAN.md`, `SECURITY.md`, `REPOSITORY_SOURCE_PACKS.md`,
  `INTELLIGENCE_PROFILE_ARCHITECTURE.md`, `GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md`,
  `BACKUP_READINESS_AND_PROFILE_SWITCHING.md`, `PROFILE_SECURITY_RISKS.md`, `OPERATIONS.md`, and **this
  file** (`CAPABILITIES.md`).
- **Static HTML pages (`docs/site/`, 19 pages):** `index`, `overview`, `capabilities`, `day-to-day`,
  `operator`, `targets`, `start9`, `local-gateway`, `signals`, `graph`, `profiles`, `architecture`,
  `gateway-boundary`, `source-packs`, `security`, `adr`, `developer`, `roadmap`, `troubleshooting`.
- **ADRs (`docs/adr/`, 19):** 0001 engine/gateway boundary · 0002 declarative profiles · 0003
  one-active/backup-gated switch · 0004 backup fail-closed/`/data` · 0005 invention-engine-deferred ·
  0006 DMS-blocked · 0007 gates-fail-closed · 0008 source-packs-are-observation · 0009 no-LLM-core ·
  0010 live-bridge-blocked · 0011 doc-architecture · 0012 naming · 0013 verified-backup-before-activation ·
  0014 transition-preview-before-activation · 0015 preview-carries-producer-facts · 0016
  activation-capability-boundary · 0017 lighthouse-describes-activation · 0018 **governance-frozen** ·
  0019 local-operator-sources-are-gateway-independent.
- **Roadmap:** phases A–K ([`ROADMAP.md`](ROADMAP.md)); current phase **D**; profile governance frozen at
  `LHP-PROFILE-14C`.
- **Current test count:** **2455 unit tests** (canonical count lives in
  [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md); update both together).
- **Package maturity:** **YELLOW — operational with known gaps.** Deployed on Start9; real signal value
  from SEC + aviation; engine phases A–C complete, governance phases E–K foundation/blocked/future.

---

*This document mirrors the canonical Markdown docs — it summarizes and links rather than duplicating.
When capability status changes, update this file together with [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md).
The browsable version is [`site/capabilities.html`](site/capabilities.html).*
