# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Release prep for a public repository: removed the private `private_invention_discovery` profile (seed file, `ALLOWED_PROFILE_IDS` in `modules/pattern_candidate.py`, and the `profile_id` enum in `docs/schemas/pattern_candidate.v1.schema.json`; five bundled profiles remain), so stored pattern candidates or transition previews that name it no longer validate; removed the PaaS deploy configs (`fly.toml`, `railway.json`, `Procfile`); replaced the arXiv researcher roster and its built-in fallback with ten synthetic records; documented StartOS packaging and its limits in the README.
- Attribution (2026-10-22): LICENSE copyright and the `pyproject.toml` author changed from "Lighthouse
  contributors" to intrinsicinvestment91 (the project's public GitHub username). Third-party code
  remains noticed separately in `THIRD_PARTY_NOTICES.md`.
- Removed personal identifiers from the tree (2026-10-21): author and copyright were set to
  "Lighthouse contributors", project URLs and one schema `$id` use
  `https://example.invalid/lighthouse` placeholders, and a test path uses `/home/you`. The README CI
  badge was removed and the Wayback archiver's browser user agent now links to the placeholder URL.
  The SEC contact is now the `SEC_USER_AGENT` setting with no default; the SEC sentinel refuses to
  run while it is unset or has no contact email.

## [1.0.1] - Unreleased (local release candidate; not tagged)

Patch release on the v1.0.0 RCD operator foundation (tag `v1.0.0` at `3419c76`). Changes since v1.0.0,
from `git log v1.0.0..` (19 commits). No profile switching, SciPres activation, Gateway authority, or V1 product-claim change.

### Fixed

- Restore reports require literal booleans (`180587e`).
- Lighthouse data-path semantics centralised (`88ad12d`).
- Profile lifecycle CLI claims corrected: initial RCD activation is described without implying controlled switching or deactivation exist (`8399764`).
- RCD V1 operator workflow made walkable (`b2daa22`; LHP-V1-OPERATOR-TRUTH-01).
- Activation evidence bindings hardened (`76b6b3f`); the transition-preview digest is verified before activation (`47f25ac`) (LHP-LIFE-EVID-01).
- Evidence-report repair (`f438b7f`): full field value-type checks for `lighthouse_backup_verification.v1` / `lighthouse_restore_drill.v1` (no `from_dict` coercion); `verified`/`completed` status must agree with the required details, including SQLite integrity coverage; malformed values raise `ReportValidationError`.
- `ops/v1_rcd_host_rehearsal.sh` cleans up its temp root on exit/INT/TERM, requires an explicit `LIGHTHOUSE_BIN`, and defaults to placeholder seeds (`83c16dd`, `f438b7f`).

### Changed

- The profile-activation database writer is now private (`4f5f8c4`).
- `pyproject.toml` version set to `1.0.1` (it previously read `0.1.0`, while v1.0.0 was tagged); classifier `Programming Language :: Python :: 3.13` added.

### Tests and CI

- Pytest runtime data isolated from the operator database (`945fba7`); Wayback unit-test isolation fixed (`7739ab0`).
- Pytest invocation fixed in CI and operator commands (`c2d8a88`); scheduled integration CI made non-vacuous (`e05f791`); Ruff CI green again (`d04d993`).
- Validated locally with the committed locks on Python 3.13.9 and 3.11.14: unit suite 2554 passed, 0 failed, 0 skipped on each.

### Documentation

- Full-capability roadmap (`cf5f358`); activation-writer boundary (`05aef43`); controlled profile-switch lifecycle, design only (`d854dfc`); pytest runtime isolation (`620ab21`); profile lifecycle CLI truth packet (`6688556`).

## Summary of changes since 0.1.0 (pre-existing narrative, covers v1.0.0 and later)

Since 0.1.0, Lighthouse evolved from a three-module OSINT archiver into a persistent, graph-based
intelligence platform. Summarised by material change (engineering detail: [`docs/PACKETS.md`](docs/PACKETS.md);
direction: [`docs/ROADMAP.md`](docs/ROADMAP.md); decisions: [`docs/adr/`](docs/adr/README.md)):

### Changed — typed activation evidence (LHP-LIFE-EVID-01)

- `backup-verify` and `restore-drill` emit typed, versioned reports
  (`lighthouse_backup_verification.v1` / `lighthouse_restore_drill.v1`) bound to
  `manifest_sha256` (exact `manifest.json` file bytes) with an `artifact_digest` that uses
  the same canonical-JSON SHA-256 recipe as `active_profile_state.v1`.
- `load_evidence` and `profile-activate` refuse untyped, create-time `passed`, and
  mismatched-but-individually-valid reports. A `passed` embed no longer makes a
  transition preview `ready_for_review`.
- No profile switching, SciPres activation, Gateway authority, or V1 product-claim change.

### Fixed — walkable V1 operator truth (LHP-V1-OPERATOR-TRUTH-01)

- `lighthouse restore-drill --output PATH` writes the restore-drill report (including a returned
  `status: failed` report before exit 1). `RestoreDrillError` still writes no file.
- `profile-activate` help and refusal name `lighthouse backup-verify` (`status: verified`) and
  refuse `backup-create`'s embedded `reports/verification.json` (`status: passed`) with an
  explicit error. `--restore-report` must come from `restore-drill`.
- `profile-transition-preview` historically accepted `passed` for `ready_for_review` and warned
  that activation requires `backup-verify` output. LHP-LIFE-EVID-01 later refused `passed` for
  preview readiness.
- Operator docs (README, CAPABILITIES, ROADMAP_STATUS, site, V1 workflow, PACKETS) no longer
  claim “no activation.” Switching, SciPres activation, and StartOS exposure of this loop remain
  unimplemented.
- `ops/v1_rcd_host_rehearsal.sh` is a full disposable CLI-only ceremony and RCD loop.

### Fixed — profile lifecycle CLI truth (LHP-LIFE-TRUTH-01)

- Corrected three stale profile-lifecycle CLI claims in `main.py` (commit `8399764`): `lighthouse
  profiles` now states initial activation for `repository_capability_discovery` v2 through
  `profile-activate`, with switching and deactivation remaining unimplemented; transition-preview
  missing current profile no longer claims "none active — activation not implemented";
  `--current-profile` help states operator-supplied comparison input not derived from
  active-profile state.
- Two new behavioral tests. Clean-checkout verification: 2422 passed, 0 failed, 0 skipped.
- CLI truth only — no profile switching, deactivation, runtime policy resolution, fencing,
  migration, rollback, or Gateway authorization was added; `UX-CLI-01` / `CORE-RESOLVE-01`
  capability states unchanged.

### Fixed — pytest runtime-data isolation (LHP-TEST-ISOLATION-01)

- Repository pytest invocations now establish `LIGHTHOUSE_DATA_DIR` and `DB_PATH` outside the
  checkout before application imports, disable cacheprovider, and clean the plugin-owned
  temporary runtime root idempotently (commit `945fba7`).
- Six sentinel tests bind both persistence paths to their temporary DB so the secondary
  callsign writer cannot reach the ignored operator `data/state.db`.
- Sixteen isolation regression cases and persistent AST guards (static repo-local writes, dual
  binding, global `subprocess.Popen` replacement).
- Earlier bare pytest runs had contaminated that ignored operator database and could overwrite
  archive output; the contaminated callsign rows were removed through a separately verified
  database repair. Production modules were not changed.
- The guarantee applies only to repository pytest invocations that load
  `tests.runtime_isolation`. The plugin's own import can create the two documented
  `tests/__pycache__` files before its code executes. Static AST guards cannot detect every
  runtime-computed path. Replacement child-process environment mappings must explicitly
  preserve `PYTHONDONTWRITEBYTECODE=1`.

### Fixed — data-path semantics enforcement (LHP-V1-HARDEN-03B / DEPLOY-CONFIG-01 semantics)

- `modules/data_paths.py` — one canonical resolver for `LIGHTHOUSE_DATA_DIR` (the data directory
  itself) and `DB_PATH` (explicit full-file override, or `$LIGHTHOUSE_DATA_DIR/state.db` when unset).
  Empty or whitespace-only values for either variable are refused (`DataPathError`).
- `config._data_path`, `modules/db.DB_PATH`, `modules/targets.DEFAULT_TARGETS_PATH`, and
  `profile_activation._resolve_data_dir` now delegate to the shared resolver — setting only
  `LIGHTHOUSE_DATA_DIR` can no longer silently split alert/graph state from legacy `state_db` tables.
- `inspect_backup_readiness` reports `data_path_divergence` when a non-empty legacy repo-relative
  `data/state.db` exists and differs from the configured database path.
- Removed remaining `init_db("data/state.db")` literals from production modules.

### Added — the second detector: a Scientific Preservation review candidate (LHP-INTEL-04C)

- `lighthouse patterns detect --detector scientific_source_disappearance_review` — a `scientific_preservation`-owned rule over the 04B check ledger that produces one conservative `disappearing_scientific_source` candidate: a previously `available` admitted public locator whose latest completed check is a narrow 404/410 not-found, ≥7 days after the last success, and not amid a broad outage. It reads only stored facts and performs **no network**. `not_found` is an HTTP answer, never disappearance.
- `pattern_candidate.v1` now carries **two** detectors through one profile-neutral spine via a **closed, code-owned detector-spec registry** (`modules/pattern_candidate_specs.py`, ADR-0023) — a candidate cannot supply its own validator or vocabulary, an unknown detector fails closed, and **existing repository candidates are byte-identical** (proven by golden fixtures; no PatternCandidate v2). The schema mirror became a discriminated union keyed on `detector_id`.
- The first non-empty **counterevidence**: known public mirrors and stored local exact copies are shown (never called independent, never auto-suppressing). Recovery yields no current candidate and mutates no history. A candidate authorizes no preservation, publication, disclosure, release, Gateway contact, or dead-man's switch; profile activation stays frozen (ADR-0018).

### Added — public-source check facts: the first network fact layer (LHP-INTEL-04B)

- `lighthouse public-sources check|runs|history|show` — an operator-invoked HTTPS check of admitted public locators and an **append-only** history of those checks (successes, failures, repeats, recoveries), so *unchanged*, *unavailable*, and *never checked* are finally distinguishable. It creates no candidate and states no meaning.
- One hardened network chokepoint (`modules/public_source_intake.py`); network stays forbidden everywhere else in the fact-first pipeline (`test_public_source_network_boundary.py`).
- `LIGHTHOUSE_PUBLIC_SOURCE_HOSTS` is the authoritative allowlist — **empty by default, and empty refuses**; a targets file may only narrow it. HTTPS-only, TLS verified, SSRF + DNS-rebinding defended, streaming bounded reads, `content_digest = SHA-256(exact response bytes)`, no response body stored.
- A closed outcome vocabulary that never collapses a failure into "gone": a `not_found` is HTTP 404/410, never disappearance (ADR-0022). No candidate, no score, no scheduler, no LLM, no Gateway.

### Added — pattern candidates: the first candidate/review loop (LHP-INTEL-03B)

`lighthouse patterns detect|list|show|review`. The first layer that presents an operator with
something other than a raw fact — and therefore the first that had to be built unable to tell them
what to think. Decision record: [ADR-0021](docs/adr/0021-pattern-candidates-are-review-objects-not-findings.md);
guide: [`docs/PATTERN_CANDIDATES.md`](docs/PATTERN_CANDIDATES.md).

- **A candidate says one thing:** *"these direct facts satisfy one declared review rule; an operator
  should look."* It is not a hypothesis, not a finding, and not permission to do anything.
- **Mechanics are profile-neutral; the rule is profile-owned.** `modules/pattern_candidate.py`
  (`pattern_candidate.v1` + `pattern_candidate_disposition.v1`) and
  `modules/repository_content_recurrence.py` — the first detector, owned by
  **`repository_capability_discovery`**: *a validated `content:<sha256>` entity directly observed in at
  least two **distinct repository scopes***. **Ownership is not activation** — profile activation stays
  frozen (ADR-0018), and `activates_profile` / `contacts_gateway` are digest-covered constant `false`.
- **The counting rule is the load-bearing line.** The store's unique index is `(entity_id,
  source_artifact_digest, derivation_version)`, so **one repository re-observed after a change is two
  rows with one scope id** — and re-observing is the *normal* case. The rule counts a **set of scope
  ids**. Two paths in one repository are one scope; a repository plus a standalone document is not two
  repositories (that fact stays fully visible through `observations where`); three repositories produce
  **one** candidate, never three pairs.
- **Immutable, versioned, idempotent.** `candidate_id` **is** the artifact digest, so identical facts
  reproduce an identical row and changed evidence mints a **new version** sharing the `candidate_key` —
  never an edit to what an operator already reviewed. Two insert-only tables, created **lazily on the
  first insert**: a dry run writes nothing at all, not even the database file.
- **Significance is unrepresentable, not merely absent.** Closed field set (an unknown key is refused in
  *and* out, including inside an evidence occurrence copied verbatim from the store); ten
  identity-checked authority pins (`is False`, not falsy); ordering by identity, never by count; and
  **no candidate-level confidence** — an entity is built only from *determined* observations, so an
  aggregate could only ever be the constant `1.0`.
- **Ten mandatory unknowns, eight mandatory limitations.** Including `source_independence_not_assessed`:
  two repositories with identical bytes may be clones, forks, templates, generated scaffolds, mirrors,
  vendored copies, or one monorepo split in two — **the detector chooses among none of them**, and scope
  count is never read as an independence count. Counterevidence is a *direct contradictory fact*, is
  empty here, and is never filled with an unknown.
- **Incomplete evidence produces no candidate.** `entity_occurrences()` bounds *after* it sorts, so a
  full page is indistinguishable from a complete one; the detector declines and reports
  `evidence_scan_truncated`. Coverage keeps **three** states (`complete`/`incomplete`/`unknown`), and
  incomplete coverage is disclosed — never converted into absence.
- **A disposition is a note, not authority.** `accepted_for_followup` | `dismissed` |
  `needs_more_evidence` — no `approved`, no `verified`, no `finding`. **One per candidate version**,
  bound to its `artifact_digest` and enforced by a `UNIQUE` index rather than a service check. **Nothing
  in the codebase branches on a decision**, so it cannot become an approval by accident.
  `operator_identity_authenticated` is constant `false` — a CLI flag is not an identity.
- **No new fact table, column or migration.** The read boundary gained two profile-neutral fact reads
  (`entity_ids_by_type`, `artifact_coverage`); the observation, graph and alert tables are untouched.

### Changed — the significance boundary (LHP-INTEL-02C, adopted by LHP-ID-PRESERVE-01)

- **The boundary between facts and interpretation is now explicit and accepted**
  ([ADR-0020](docs/adr/0020-profile-neutral-substrate-profile-owned-detectors.md)). **Facts first.
  Interpretation second. Findings only after evidence and review.** A repeated fact is not automatically
  significant; a candidate is not a hypothesis; a hypothesis is not a finding; **a finding is not
  permission to publish or execute.** No single composite score may become truth, and the observation/fact
  substrate stays free of scores, recommendations, hypotheses, findings, and model-generated claims.
- **Lighthouse is recorded as a profile-driven platform.** Private Invention Discovery is **one** profile;
  Public Scientific Lighthouse, Scientific Preservation, Patent and Research Tracking, Repository
  Capability Discovery, Aerospace/Defense and Fusion monitoring remain first-class. **Shared mechanics stay
  profile-neutral; detector rules, evidence/counterevidence requirements and privacy/publishing policy are
  profile-owned.**
- **Next work is a *fact* packet, not a detector** — `LHP-INTEL-03A`, repository file-content identity
  (`SHA-256` over exact raw bytes). Rationale: `repository_intake` records file *paths*, never file
  *content*, so *"two repositories independently implement this"* and *"I copied my Dockerfile from the
  last project"* are currently indistinguishable. A detector shipped first would present coincidence as a
  pattern.

### Fixed — documentation that contradicted the code

- **The `AlertRecord` rule was false.** The internal agent guide required that "alerts must use
  `AlertRecord` … do not hand-craft alert dicts", but **`AlertRecord` is never instantiated in
  production**: all 7 sentinels import only the `ALERT_SCHEMA_VERSION` constant, hand-build a `dict`,
  and `db.insert_alert()` reads it with `.get()` without ever calling `AlertRecord.validate()`.
  Documented honestly; standardization is recorded as **separate, deferred debt** — no alert migration
  was performed.
- **The source-pack pipeline was stale.** `REPOSITORY_SOURCE_PACKS.md` and ADR-0008 described source packs
  flowing through *observation → alert → correlation → graph*. That path was **rejected by the
  implementation** in `LHP-INTEL-01B` (a triage contract cannot carry a fact without scoring it). Both now
  record the **fact-first** pipeline; ADR-0008 is **superseded in part**, not rewritten.
- **ADR-0005's location premise was wrong.** It stated the invention engine "already exists … in the
  separate Gateway application". A forensic inventory proved it was in **no git repository at all**.
  Corrected via an implementation-status note; the decision itself stands.
- **ADR-0018's collector premise expired.** It stated that `repository_intake` and `document_intake`
  "neither exists anywhere in the codebase"; both shipped. Its §4 resume condition 2 is now satisfied.
  **The governance freeze still holds** — conditions 1 and 3 remain unmet.
- **A fixed security defect was still documented as live.** `SECURITY.md` listed the authority gate as
  "⚠️ Defect — currently fails open when secret unset"; it was made fail-closed in `0cbbfcd`.

### Added — repository file-content identity (LHP-INTEL-03A)

- **`modules/content_identity.py` — one raw-byte SHA-256 recipe, defined once.** Streaming (1 MiB
  chunks), bounded, fail-closed. `document_intake` now delegates to it; its digests are **byte-identical**
  (proven by a golden pinned before this packet existed). Two copies of the recipe already existed and
  happened to agree — a third would have been the packet where they stopped agreeing.
- **A new profile-neutral global entity: `content:<sha256>`** — `SHA-256` over a file's **exact raw
  bytes**, and nothing else. Not the path, not the name, not the mode, not the timestamps, not decoded
  text, not normalized line endings, and **not git's blob SHA-1** (which hashes a `blob <len>\0` header
  *before* the bytes, so it could never match a standalone document's digest — the repository↔document
  join would have been structurally impossible; a `sha1` algorithm label is now rejected by `validate()`).
- **The join this enables:** a tracked repository file and a loose document holding the same bytes are
  **one entity with two occurrences**, answerable through the *existing* 02B surface —
  `lighthouse observations where --entity-id content:<sha256>`. **No new table, column, index, migration,
  or CLI command**; the read boundary already indexed relationships by both endpoints.
- **Two new repository observation types**, `content.files` and `content.coverage`. Every tracked path
  receives an accounted outcome in a closed 10-member status vocabulary, and **only `hashed` may carry a
  digest** — enforced by `RepositoryObservation.validate()`, not merely declared.
- **Both derivation versions bumped** (`repository_intake/2`, `observation_normalizer/2`). The
  normalizer's bump is **forced, not cosmetic**: a previously-observed document's *artifact* digest is
  unchanged, so re-normalizing it under `/1` would collide on the unique
  `(entity_id, source_artifact_digest, derivation_version)` index. The store stays insert-only.

### Security — LHP-INTEL-03A

- **Symlinks are never followed** (`O_NOFOLLOW`, plus an `lstat`/`fstat` inode comparison so the file
  that was *checked* is the file that is *read*, not merely one with the same name). **Gitlinks are never
  recursed**, **special files are never read** (a FIFO would block forever), untracked and ignored files
  are never opened, and a tracked path whose resolved location escapes the repository is refused.
- **A file that changes during the read produces no digest at all**, and there is no retry — a retry
  eventually catches *some* consistent state and presents it as the state that was observed.
- **Incomplete coverage cannot masquerade as complete.** The file-count bound **withholds the whole
  inventory** (undetermined) rather than truncating it, because a truncated list reads exactly like a
  complete one; the byte bounds keep the inventory and mark individual entries; `content.coverage`
  survives either way to say `complete: false`.
- **No raw bytes are ever stored** — only the digest and factual metadata. Error messages carry the safe
  relative path, the status and the declared bound; never file contents.

### Fixed — LHP-INTEL-03A

- **A vacuous test, found by mutation.** Replacing the streamed read with a whole-file slurp passed every
  behavioural test — a slurp produces the *same digest* — so the bound was a claim, not a property. It is
  now proven structurally: no `.read()` may be called without a size.
- **A closed vocabulary that was declared but never enforced.** Nothing validated the per-file records, so
  a forged `{"status": "important"}` — or, far worse, a **fabricated digest on a file we had admitted we
  could not read** — would have been accepted. The digest could not be the gate, because anyone able to
  write a record can re-stamp it; `_validate_content_files()` is.
- **`observations where` now reports relationship endpoints.** Every pre-03A relationship had the scope as
  its subject and the queried entity as its object, so naming them again said nothing. `file_has_content`
  is the first whose subject is neither — it is a `file:` — and without the endpoints the store would have
  held *"these bytes are in this repository"* while refusing to say **where**.

### Added — external prototype preservation (LHP-ID-PRESERVE-01)

- **The invention-discovery prototype was under no version control at all**, and is now preserved
  privately. The Gateway held only a consumer/validator and one derived output of the untracked
  engine: **the output was version-controlled while the engine that produced it was not.**
- Preserved privately with a provenance manifest (databases and live runtime data excluded).
- **Preserved, not adopted.** The prototype card contract is **not** the shared platform contract; its
  composite scoring is **quarantined**. Migration is deferred and will be **selective — one detector
  family at a time, never a bulk copy** — after a profile-portability checkpoint proves the candidate
  architecture carries a non-invention profile. No engine code lives in this repository.

### Added
- **Multi-source intelligence pipeline** — SEC, flight, and ADSB collectors feeding a scored
  `AlertRecord` → A3 correlation (`CorrelationRecord`) → graph → query flow, with BIP-340 witness
  attestation and deterministic weighted correlation scoring.
- **Graph intelligence** — persistent entity/event/signal graph with idempotent historical backfill,
  depth-limited traversal, graph-native callsign/facility/prefix entities, and a deterministic
  explanation engine.
- **Temporal intelligence** — callsign history, recurrence metrics, operational tempo, and mission
  recurrence analysis (`lighthouse temporal` / `lighthouse tempo`).
- **Intelligence Profile foundation** — a declarative, fail-closed `IntelligenceProfile` contract and
  read-only registry over bundled seed profiles. Declarative only; no activation.
- **Backup-readiness framework** — a read-only, fail-closed inspector enforcing the Start9 `/data`
  backup boundary (`lighthouse backup-readiness`).
- **Verified backup manifest & isolated restore drill** (LHP-PROFILE-02) — a versioned, deterministic
  backup manifest (`lighthouse_backup_manifest.v1`; SHA-256, safe SQLite online-backup snapshots, no
  contents/secrets embedded) plus `lighthouse backup-manifest` / `backup-create` / `backup-verify` /
  `restore-drill`. Read-only toward the source; writes only to explicit operator destinations; fails
  closed on symlink/traversal/special-file/overlap/`/data` hazards. A completed backup or restore
  drill does **not** authorize a profile switch and is not a production restore
  ([ADR-0013](docs/adr/0013-verified-backup-and-restore-drill-before-profile-activation.md)).
- **Profile transition preview** (LHP-PROFILE-03) — a deterministic, read-only review artifact
  (`profile_transition_preview.v1`; `lighthouse profile-transition-preview`) that describes a proposed
  profile transition, its declarative policy diff, and the backup evidence supporting it. It consumes
  LHP-PROFILE-02 evidence by reference and fails closed: `ready_for_review` only with two valid
  profiles and present, passing evidence — otherwise `incomplete` / `blocked` / `invalid`. It performs
  **no activation, no switch, and no Gateway approval** (approval authority remains future and
  Gateway-owned; [ADR-0014](docs/adr/0014-transition-preview-artifacts-before-profile-activation.md)).
- **Producer-owned execution facts in the transition preview** (LHP-PROFILE-14A) — the preview now
  carries, additively within schema v1 (serialization v2), the structured facts downstream governance
  previously recovered from string tokens: a typed `target_policy_snapshot` (`local_only`,
  `dms_eligibility`, `data_root`, `schema_migration_required`), the `local_only_changed` /
  `dms_eligibility_changed` scalar diff categories (all 8 diff categories now serialized), a
  `backup_manifest_sha256` content fingerprint (evidence binding upgraded from id-only), a
  `target_profile_sha256` over the target profile's full canonical content, and
  `lighthouse_version` / `repository_commit` provenance stamps — all digest-covered.
  `ready_for_review` additionally requires these facts (fail-closed, unconditional). Still **no
  activation, no switch, no Gateway approval**
  ([ADR-0015](docs/adr/0015-preview-carries-producer-owned-execution-facts.md)).
- **Activation capability boundary** (LHP-PROFILE-14B) — three pure contracts that make profile
  activation possible to *reason about* without making it possible to *do*:
  `active_profile_state.v1` (the record shape a future `lighthouse.active_profile_store` would
  persist — explicit absence via a declared `no_active_profile` state, no half states, recomputed
  tamper-evident digest), a read-only activation-preconditions evaluator
  (`ready`/`blocked`/`invalid`; unknown never becomes ready; first consumer of the 14A
  `target_profile_sha256` recompute-vs-registry check), and `activation_intent.v1` (a
  deterministic "I intend to move A→B" bridge artifact that embeds the true evaluator result and
  carries a digest-covered `authorizes_activation: false` pin — approved ≠ activated,
  structurally). Persistence deliberately absent (artifact-only); idempotency semantics and the
  rollback boundary are documented, not implemented. **No activation, no store, no endpoint, no
  CLI command, no switching, no Gateway changes**
  ([ADR-0016](docs/adr/0016-activation-capability-boundary.md)).
- **Activation capability descriptor** (LHP-PROFILE-14C) — `lighthouse_activation_capability.v1`:
  Lighthouse's machine-readable **self-description** of the profile-activation capability it
  *would* provide — three operations mapped 1:1 onto the canonical Gateway capability ids
  (`lighthouse.profile_activation_endpoint` / `active_profile_store` / `post_switch_validation`,
  mirroring each definition's inputs, outputs and mutating flag), the contracts they exchange
  (`profile_transition_preview.v1`, `activation_intent.v1`, `active_profile_state.v1`, bound to
  their live schema versions), plus required evidence, limitations and security constraints.
  Availability is a **closed state vocabulary whose only member is `not_implemented`**, backed by
  an identity-checked constant `activation_available: false` — unreachable by a value edit *or* a
  re-stamped digest. It carries none of the Gateway's policy fields (trust floor, freshness,
  lifecycle/audit status), so no duplicate authority is created, and an attestation derived from
  it maps to "unavailable" — it can never unblock a Gateway execution plan. **Describes
  activation; provides none: no store, no endpoint, no CLI command, no Gateway changes**
  ([ADR-0017](docs/adr/0017-lighthouse-describes-activation-capability.md)).
- **Profile governance frozen; intelligence engine prioritised** (LHP-DIRECTION-02) — profile
  activation is **deferred**, not cancelled. A profile has **no runtime consumer** in Lighthouse:
  every policy field it declares (`enabled_collectors`, `local_only`, `min_publish_score`,
  `public_publishing`, `data_root`) is, at runtime, an environment variable or a hardcoded literal,
  and the pinned first-activation target names two collectors that do not exist. An activation built
  today would therefore activate metadata rather than capability, and its post-switch validation
  could only assert that the record it wrote contains what it wrote. The governance contracts
  (`LHP-PROFILE-01`…`14C`) remain in-tree, **dormant and fully tested**; engineering moves to the
  observation intake layer that gives a profile something to govern
  ([ADR-0018](docs/adr/0018-governance-frozen-pending-runtime-intelligence.md)).
- **Observation intake chartered** (LHP-INTEL-01) — the deterministic, provenanced, metadata-first
  ingestion layer for the operator's own repositories and documents, reusing the existing
  `AlertRecord` → `db.py` → graph contracts (no new storage system, no new contract). Local operator
  sources are a **distinct, Gateway-independent observation class** — external Repository Source
  Packs still require Gateway admission, local ones never did
  ([ADR-0019](docs/adr/0019-local-operator-sources-are-gateway-independent.md),
  [`docs/OBSERVATION_INTAKE.md`](docs/OBSERVATION_INTAKE.md)). Charter only — no collector code ships
  in this packet.
- **Deterministic repository observation** (LHP-INTEL-01A) — `modules/repository_intake.py` observes
  the operator's own git repositories and returns a validated, digest-bearing
  `repository_observation.v1` artifact: 27 observation types over a closed vocabulary, each carrying
  `type`/`value`/`source`/`timestamp`/`confidence` and **no field for significance**. Confidence is
  deterministic and binary — `1.0` determined (*including provably absent*), `0.0` undetermined (value
  always `null`) — and an enumeration exceeding its bound becomes undetermined rather than being
  silently truncated. The SHA-256 `artifact_digest` excludes timestamps, so an unchanged repository
  yields the same digest forever (the content-identity dedup key the normalization layer needs).
  **Pure**: no database, no network, no LLM, no scoring, no writes. Read-only toward the source as a
  *tested* property — the tree, `.git/` included, is byte-identical after a run — which requires
  `GIT_OPTIONAL_LOCKS=0` (otherwise `git status` rewrites `.git/index`) and `-c core.fsmonitor=false`
  (a repository-local fsmonitor hook is code execution). Remote-URL credentials are redacted. The
  layer states facts and never why they matter; scoring, persistence, and graph linkage are the next
  packet's job ([`docs/OBSERVATION_INTAKE.md`](docs/OBSERVATION_INTAKE.md)).
- **Observation normalization** (LHP-INTEL-01B) — `modules/observation_normalizer.py` turns a
  `repository_observation.v1` transcript into **primitives that have identity**, persisted to three
  **fact-only** tables in `modules/db.py` (`observation_sets`, `normalized_entities`,
  `normalized_relationships`). Seven entity types and nine relationships; `language` / `technology` /
  `remote` / `commit` are **global**, so two repositories that both use Docker now share
  `technology:docker`, two clones of a project share their root commit, and a nested repository or
  checked-out submodule resolves to the identity it *would* have had if observed directly. Three
  guarantees are structural rather than aspirational: **nothing observed is lost** (a repository
  entity's attributes are *exactly* the determined observations, and the source artifact is stored
  verbatim); **an opinion is unrepresentable** (an attribute key must belong to the closed 27-name
  observation vocabulary, which has no word for score, priority, importance, severity or quality —
  and no table column holds one either); and **undetermined is not absent** (a bounded-out enumeration
  yields no entities and is reported, so "I could not list the languages" can never read as "this
  repository contains no code"). Digests exclude timestamps, so storage is **insert-only and
  idempotent by content identity** — an unchanged repository observed a hundred times is stored once.
  **It does not score.** The original plan routed observations through `AlertRecord` → `db.insert_alert`
  → `GraphNode`/`GraphEdge`; an audit rejected it, because `AlertRecord` is a triage contract
  (`score`/`score_label`, `--min-score`, the publish gate) and `GraphEdge` *requires* a `weight` — so
  neither can hold a fact without also asserting an opinion about it. The layer allowed to say a thing
  matters does not exist yet, deliberately
  ([`docs/OBSERVATION_INTAKE.md`](docs/OBSERVATION_INTAKE.md)).
- **Document observation intake** (LHP-INTEL-01C) — `modules/document_intake.py` observes local
  documents (PDF, Markdown, plain text) and emits a validated `document_observation.v1` artifact,
  normalized through **the same** fact boundary: no document database, no second provenance system.
  Its central guarantee is a **distinction**: *"this document contains no text"* (`character_count` 0 at
  confidence **1.0**) is a **fact**, while *"I could not extract its text"* (value `None` at **0.0**) is
  the **absence** of one — corrupt, undecodable, over bound, or no extractor installed. The two must
  never collapse into the same silence, and 01A's confidence model already encoded exactly that, so this
  reuses it rather than inventing a second one. A document's **identity is the digest of its bytes,
  never of its extracted text**, so the same file in two folders is *one entity observed twice* and no
  truncation bound can make two documents collide. **Metadata-first:** text is extracted to establish
  facts *about* it and then dropped — the operator's document contents never reach the database, and
  `text.digest` keeps a later re-extraction verifiable. Bounds are honest on two axes (over
  `max_file_bytes` → refused *unread*; over `max_text_characters`/`max_pages` → **truncated with explicit
  metadata**), the extractor's version is digest-covered (a pypdf upgrade records a *new* observation
  rather than silently revising the old claim), and HTML and office formats are **rejected deliberately**
  — a tag-stripper that mangles entities and `<script>` is exactly the silent information loss this layer
  forbids. Host-only, like repository observation: neither repos nor documents are mounted into the
  deployed container. **It does not score.**
- **The shared observation substrate** (LHP-INTEL-01D-A) — `modules/observations.py`: the observation
  model, the binary confidence contract, the one canonical digest recipe and the shared error base, now
  defined **once** and imported by both collectors instead of copied into each. The extraction was not
  cosmetic: the two copies of a fail-closed validator had *already* drifted one packet apart, and a
  malformed **document** was reporting itself as a *repository* failure — escaping its wrapper entirely.
  Behaviour preservation is proven by three golden digests pinned from the pre-refactor commit.
- **Operator-controlled observation intake** (LHP-INTEL-01E) — `lighthouse observe`. Observing your own
  repositories and documents no longer requires writing Python. The **trust boundary** is
  `LIGHTHOUSE_OBSERVATION_ROOTS`, and it is **empty by default, which fails closed**: with no roots
  configured, `observe` *refuses* rather than implicitly trusting the filesystem. `data/observation_sources.json`
  is the **work list, not the boundary** — it may only *narrow* what is observed, and widening it is
  **unrepresentable** rather than merely rejected, because an allowlist living in the same file as the
  paths it authorizes would be self-authorizing. A dry run writes nothing at all — not a row, not a table,
  not the database file. It counts what happened and stops.
- **The observation query surface** (LHP-INTEL-02A) — `lighthouse observations list|show|search|stats`:
  the operator can finally *see* the facts that had been accumulating where nobody could look. It is a
  **read boundary**, not a CLI feature: `modules/observation_query.py` is where the closed filter
  vocabulary, the bounds and validate-on-read live, and every later consumer (MCP, a UI, a report) comes
  through it rather than reaching into `db.py`. **"Read-only" is a mechanism, not a promise** — every
  query runs on a `mode=ro` SQLite connection, so the database itself rejects the write; and because a
  plain `sqlite3.connect()` *creates* a missing file, a careless read would have left a `state.db` behind
  on a machine that had never observed anything. It counts, it lists, it shows; **it never ranks**.
- **Cross-scope fact discovery** (LHP-INTEL-02B) — `lighthouse observations where|shared`, answering
  *"where else have I built this?"*. **Exact** entity lookup and **exact** set intersection over facts
  that were already stored: *cross-scope fact discovery performs exact lookup and direct set intersection
  over stored facts — it does not rank, infer, score, or interpret relationships.* No new table, column,
  index or migration; the joins had been in the store since 01B and nobody could ask for them. Evidence is
  **verbatim**: each occurrence carries the observations that established it, with their own sensors,
  confidences and timestamps, read off a re-validated artifact — there is no aggregate confidence and no
  scalar relationship, because an occurrence has zero, one, or several of each. Results order by identity
  and **never** by count: ordering shared entities by how often they occur would be "the most common
  technology", which is a ranking.
- **Private Local Gateway** — a bounded, read-only, loopback-only intelligence dispatch surface
  (`/local/*`).
- **Matured read-only MCP interface** — query and graph/correlation tools for AI-native access, with a
  human-approval gate on live actions.
- **Start9 integration** — persistent-volume data path, target seeding, and operational SOPs.
- **Documentation architecture** — role-separated docs (index, roadmap, status, history, backlog),
  an ADR log, a consolidated security overview, and an expanded static documentation site.

### Changed
- **The observation boundary generalized from "repository" to "scope"** (LHP-INTEL-01C) —
  `repository_id`/`repository_path` became `scope_id`/`scope_path` across `NormalizedEntity`,
  `NormalizedRelationship`, `NormalizedObservationSet` and the three fact tables, plus a closed,
  digest-covered `scope_type ∈ {repository, document}`. The 01B boundary was correct but
  repository-shaped in three structural places, each of which failed a document **closed**: every record
  required a SHA-256 `repository_id`, only a `repository` entity could carry attributes, and attribute
  keys had to belong to the repository vocabulary. A scope is *"the place I looked"*, and its id keeps
  exactly the rule it already had (SHA-256 of a canonical absolute path), so no validator was weakened;
  `scope_type` is digest-covered rather than a mere label because it decides **which producer's validator
  owns the carried artifact**. This is the *opposite* verdict from the `AlertRecord` audit above, and the
  contrast is the point: you **fork** a model that is semantically wrong (a score cannot hold a fact
  without asserting an opinion about it), and you **widen the door** on one that is merely named for one
  kind of subject. Behaviour-preserving; no migration (nothing was deployed with the old names).
- **`pypdf` added** as a runtime dependency (lazily imported, so an install without it degrades to an
  honest `extractor_unavailable` observation rather than crashing).

### Fixed
- **Backup-manifest `lighthouse_version` was always null** — the provenance helper queried the
  non-existent distribution `"lighthouse"` (the console-script name); it now queries
  `pattern-match-sentinel`, and `generate_manifest` also stamps `repository_commit` from git HEAD
  by default when available (LHP-PROFILE-14A).

### Security
- **`LHP-SECURITY-01` — MCP approval gate fails closed.** `portal/mcp_server.py`'s `_is_human_approved`
  (guarding `run_module`/`run_pipeline`/`publish_note`) now **denies** on a missing/empty/whitespace
  `LIGHTHOUSE_APPROVAL_SECRET`, a missing/empty/malformed token, unreadable configuration, or any
  validation exception — closing a fail-open path that previously auto-approved live actions when no
  secret was set. Token comparison is timing-safe (`hmac.compare_digest`); secrets/tokens are never
  logged. Dry-run behavior is unchanged. A regression test reproduces the original fail-open behavior.

### Notes
- The deterministic core carries no LLM; the narrative analyzer remains an optional edge.
- Dead-Man's-Switch production behavior is **blocked** pending a threat model.

## [0.1.0] - 2026-04-25

### Added
- **Module 1 — Patent Sentinel**: USPTO PatentsView API + WIPO PATENTSCOPE REST API polling for IPC classes C22C (HEAs), G21B (fusion), B64G (spacecraft/propulsion)
- **Module 2 — Wayback Archiver**: Playwright-based crawler for DoD, NASA, and DoE newsrooms with keyword filtering and IPFS archival
- **Module 3 — Nostr Bot**: BIP-340 Schnorr-signed NIP-01 event publishing to public Nostr relays
- CLI entry point (`lighthouse`) with `--since-days`, `--dry-run`, and `--module` flags
- Cross-run deduplication via SQLite cache (`data/state.db`) — prevents duplicate Nostr publishes
- Exponential-backoff retry logic on all HTTP and WebSocket operations (`tenacity`)
- Startup config validation: key format, IPFS reachability, relay connectivity
- Per-agency Playwright CSS selectors for higher-precision article discovery
- Playwright full-text fallback for flagged patents with empty abstracts
- Structured logging with `run_id` threaded through all module log output
- `--dry-run` mode: full fetch/filter pipeline with IPFS and Nostr skipped
- `docker-compose.yml` with IPFS (Kubo) sidecar
- Complete unit test suite (`tests/unit/`) with integration test stubs (`tests/integration/`)
- GitHub Actions CI: lint + unit tests on every push/PR; nightly integration tests; PyPI release workflow
- `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.gitignore`, `.env.example`
