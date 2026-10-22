# Observation Intake (LHP-INTEL-01)

> Status: **the whole fact layer is shipped** — repository observation (`LHP-INTEL-01A`),
> normalization (`01B`), document observation (`01C`), the shared substrate (`01D-A`), intake
> (`01E`), the read boundary (`02A`), cross-scope discovery (`02B`) and exact content identity
> (`03A`). `modules/repository_intake.py` observes the operator's own git repositories and emits a
> validated, digest-bearing `repository_observation.v1` artifact; `modules/observation_normalizer.py`
> turns those facts into provenanced entities and relationships with stable identity, persisted to
> three **fact-only** tables. **No layer here scores anything, and none of them can.**
>
> **The first layer allowed to *select* now exists, and it is not this one.**
> `LHP-INTEL-03B` added pattern candidates: *"these direct facts satisfy one declared review rule."*
> It lives in its own contract, its own tables and its own document —
> [`PATTERN_CANDIDATES.md`](PATTERN_CANDIDATES.md) — and it still may not say that anything **matters**.
> A candidate is not a hypothesis; a hypothesis is not a finding
> ([ADR-0021](adr/0021-pattern-candidates-are-review-objects-not-findings.md)).
>
> Decisions: [ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md) (why this track
> came first), [ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md) (why it is not
> blocked), [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md) (facts before
> significance; profile-owned detectors).

## Why this exists

Lighthouse observes the outside world well — SEC filings, aircraft, patents, papers — and turns those
observations into scored alerts, correlations, and a graph. It observes **the operator's own work** not at
all.

That is the gap. The mission the operator actually wants — *understand my repositories and documents;
find the duplicate work, the reusable capability, the invention hiding across projects* — needs an
**observation intake layer** that Lighthouse does not have. Two bundled profiles already name the two
collectors it requires (`repository_intake`, `document_intake`), and **neither exists**. That absence is
what froze the profile-activation ladder ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md)):
there is no point governing a mission that nothing can perform.

**Mission:** *build the observation layer that lets Lighthouse reliably notice meaningful information in
the operator's own material.* Not "build AI."

## This is not a second intelligence engine

Lighthouse **is** the intelligence engine ([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md));
repository work is explicitly *"not a second intelligence engine"*
([ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md),
[`REPOSITORY_SOURCE_PACKS.md`](REPOSITORY_SOURCE_PACKS.md)). This layer adds **inputs**, not a parallel
core. It ends where every other collector ends: a normalized, provenanced observation handed to the
existing pipeline.

```text
     "What exists?"      "How is it consistently represented?"      "Why does it matter?"
source → collector → observation → normalized facts + provenance → ??? → alerts / graph / correlation
         └─ LHP-INTEL-01A ─┘       └────── LHP-INTEL-01B ──────┘    └── not built, deliberately ──┘
```

**Three questions, three layers, and the seams between them are the design rule — not a convenience.**
Observation states a fact. Normalization gives that fact a stable identity so the system can speak
about it consistently. Only the third layer is allowed to say a thing *matters*, and **nothing in
Lighthouse occupies it yet.**

> **Correction (LHP-INTEL-01B).** An earlier revision of this document — and of the internal agent guide —
> said `01B` was *"the layer allowed to score"*, routing observations through `AlertRecord` →
> `db.insert_alert` → `GraphNode`/`GraphEdge`. **That was wrong**, and the audit below says why: those
> contracts cannot represent a fact without also asserting an opinion about it. Normalization is not
> interpretation. The layer that decides significance is a later packet that does not exist yet.

> **Correction (LHP-ID-PRESERVE-01, 2026-07-14).** The sentence below — repeated from
> [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) — says the
> invention-discovery engine "remains in the Gateway". **It never was there.** An inventory found it was
> not under version control; the Gateway held only a consumer/validator and one derived output. It is
> now preserved privately with a provenance manifest. Migration remains deferred, and is now
> **selective, one detector family at a time**
> ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)).

The invention-discovery prototype is preserved privately and migrates later (selectively), without
duplication ([ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md)). **This
packet moves observation, not that engine.**

## Two source classes

| | External Repository Source Pack | **Local operator source (this layer)** |
|---|---|---|
| Origin | public / third-party repository | the operator's own filesystem |
| Admission | **Gateway-approved** ([ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)) | **none required** — already inside the trust boundary |
| Blocked on | Phase G + [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md) | **nothing** |
| Executed / installed / imported | **never** | **never** |
| Status | architecture-only ([`REPOSITORY_SOURCE_PACKS.md`](REPOSITORY_SOURCE_PACKS.md)) | **the active track** |

The distinction is load-bearing and is recorded as
[ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md). A local source is one that
resides under an **explicitly configured root** — never a claim made inside an observation, or the local
class becomes a way to smuggle foreign repositories past admission.

## Reuse rules (audited — do not create a duplicate system)

Reuse the machinery, **not the assumptions**. Most of what this layer needs already exists — but the
first draft of this table said an observation *"fits `AlertRecord` as-is"*, and that was the leak the
audit later caught: `AlertRecord` is a **triage** contract (score, label, alert feed) and `GraphEdge`
requires a **weight**. Neither can hold a fact without also asserting an opinion about it, so `01B`
adds its own fact-only records and tables — see the persistence audit below.

| Need | Reuse | Where |
|---|---|---|
| ~~Observation contract~~ | ~~`AlertRecord`~~ → **superseded**: `repository_observation.v1` (01A) then `normalized_observation.v1` (01B) | `modules/repository_intake.py`, `modules/observation_normalizer.py` |
| ~~Scoring~~ | ~~`AlertScore`~~ → **nothing here scores.** Deferred to the significance layer | — |
| Provenance | `ProvenanceRecord` — `content_hash`, `retrieved_at`, `local_path`, `metadata` | `modules/provenance.py` |
| Persistence | `modules/db.py` — the single persistence layer, extended with three **new fact-only tables** | `modules/db.py` |
| Digest recipe | SHA-256 over canonical JSON — one recipe platform-wide (drift-pinned by test) | `modules/profile_transition.py` |
| Graph | **not yet** — a repository entering the intelligence graph is a claim that it matters | `modules/graph.py` |
| File hashing | the chunked `sha256_file` reader (no whole-file reads into memory) | `modules/backup_manifest.py` |
| Deterministic file listing | sorted `glob` with an explicit pattern | `modules/profile_registry.py` |
| Git metadata | the existing narrow, read-only, fail-silent `subprocess` call (`git rev-parse`) | `modules/backup_manifest.py` |
| Collector shape | `async def run_x(conn, …, dry_run=False) -> list[dict]`, per-module `try/except` in `main.py` | `modules/sec_sentinel.py`, `main.py` |
| Config | dataclass + `get_*_cfg()` getter + `_data_path()` | `config.py` |

### Five rules, each one preventing a real defect

Rule 1 (persist through `modules/db.py`) and rule 5 (content-identity dedup) **bind at normalization**
and are honored by `LHP-INTEL-01B`. Rules 2–3 concern **alerts and scoring**, which normalization does
not do — they bind at the significance layer, which is not built. See the persistence audit below for
why `01B` extends `db.py` with new fact-only tables instead of reusing `AlertRecord`.

1. **Extend `modules/db.py`; never `modules/state_db.py`.** There is already one duplicate-storage scar
   in this repo: both modules define a `correlation_events` table with diverging columns, and
   `state_db.insert_correlation_event()` is dead code while `db.insert_correlation()` is the real writer.
   That is exactly the drift this charter exists to prevent. `modules/db.py` is the single persistence
   surface ([`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md)).
2. ~~**Build alerts through `AlertRecord(...).to_dict()`.**~~ **Superseded — an observation is not an
   alert.** An alert is a triage primitive: it goes in the operator's feed, it is filtered by
   `--min-score`, and it feeds the correlator and the publish gate. "This repository has 231 tracked
   files" is a fact, not an event worth waking anyone for. Binds only at the significance layer.
3. ~~**Score deliberately; do not skip scoring.**~~ **Superseded — this layer does not score at all.**
   The original rule reasoned from "every alert carries a score" to "so give the observation an
   informational one", which is backwards: the conclusion should have been *observations are not
   alerts*. Assigning even an "informational" score is still an assertion about significance made by a
   layer forbidden to make one.
4. **`local_path` is populated, and never leaves the box.** The MCP surface already strips local paths
   before responding, because *"path strings leak host layout."* `repository_path` and `repository_id`
   (a digest **of a path**) are host-local facts: stored, never published.
5. **Deduplicate on content identity.** `01A` supplies it and `01B` uses it: an unchanged repository
   reproduces the same `artifact_digest`, hence the same `normalization_digest`, so
   `store_normalized_observation()` writes it exactly once. A whole-dict hash would re-insert on every
   cosmetic change; a timestamp in the digest would re-insert on every run.

## Safety boundaries

The intake layer **may**: read, ingest, index, compare, analyze, discover.

It **may not**, ever:

- modify, move, or write to any source it observes;
- **execute, install, or import** anything it ingests — the reference-only rule
  ([`REPOSITORY_SOURCE_PACKS.md`](REPOSITORY_SOURCE_PACKS.md)) applies to local sources exactly as to
  foreign ones;
- reach outside an explicitly configured root;
- publish anything externally;
- activate or switch a profile, or change any configuration;
- introduce an LLM into the deterministic core ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)).

`dry_run=True` means **zero side effects**, including no database writes.

Source immutability is not an aspiration but a **tested property**: `LHP-INTEL-01A` hashes the source
tree before and after a run and asserts it is unchanged.

## Provenance from day one

Every observation records **which profile and derivation version produced it** (`profile_id` +
`derivation_version`), per the historical-provenance requirement in
[`INTELLIGENCE_PROFILE_ARCHITECTURE.md`](INTELLIGENCE_PROFILE_ARCHITECTURE.md). A future mission switch
must never silently reinterpret past intelligence.

This is the **one governance requirement that is not dormant** — and it is also the seed of the eventual
profile→runtime binding: the derivation stamp is the first thing in Lighthouse that will ever *read* a
profile. That is how the frozen ladder ([ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md))
eventually becomes useful, from the intelligence side rather than the governance side.

## What the observations are *for* (not collector sprawl)

[`FUTURE_PLAN.md`](FUTURE_PLAN.md) names *"broad collector sprawl — new APIs without an interpretation
plan"* an explicit non-goal. So the interpretation plan comes first:

- **Entities.** A repository and a document become graph nodes (`repo:`, `document:`) with deterministic,
  content-derived ids, alongside the `entity:` / `callsign:` / `facility:` nodes already in the graph.
- **Relationships.** `repo → contains → document`, `document → mentions → entity`, and — the point of the
  whole exercise — **capability and duplication edges across the operator's own projects**: the same idea,
  implemented twice, in two repositories that never knew about each other.
- **Queryable through what already exists.** `lighthouse graph`, the explanation engine, and the read-only
  MCP tools need no new interface to answer *"where else have I built this?"*

Ingestion that produces no answerable question is an ingestion trophy, not intelligence.

## Success criteria (the milestone's)

1. Lighthouse can ingest repository information.
2. Lighthouse can ingest document information.
3. Every observation carries provenance.
4. Ingestion is deterministic — the same source yields the same observation and the same digest.
5. Sources are provably unchanged.
6. Tests prove: repeatability · provenance preservation · source immutability · determinism · dry-run
   writes nothing · dedup is idempotent across re-runs.

## The observation contract — `repository_observation.v1`

Module: `modules/repository_intake.py`. Entry point:

```python
observe_repository(path, *, allowed_roots=None, profile_id=None, observed_at=None)
    -> RepositoryObservation          # validated, digest-bearing; raises on every failure
```

An **observation** is five fields and nothing more — `observation_type`, `value`, `source`
(`filesystem` | `git_metadata`), `observed_at`, `confidence`. There is deliberately **no field for
significance**. The artifact wrapping them carries the repository's identity, the provenance stamps,
and a SHA-256 `artifact_digest`.

**Confidence is deterministic and binary** — it is not an AI confidence:

| | Meaning | Value |
|---|---|---|
| `1.0` | **determined** — the fact is known, *including provably absent* | the fact (`[]`, `false`, `null` all mean "absent, and we are sure") |
| `0.0` | **undetermined** — the fact could not be established within bounds | always `null` |

That distinction is the point: "this repository has no remotes" and "I could not read the remotes"
are different facts, and a consumer must never confuse them. It also drives the one bounding rule:
**an enumeration that exceeds its cap becomes undetermined, never silently truncated** — a clipped
list presented as complete is a lie; an honest "I don't know" is not.

**27 observation types**, a closed vocabulary (an unknown type is rejected; a type's `source` is pinned
by the vocabulary, so a producer cannot mislabel where a fact came from):

- *Presence, from the filesystem* — `repository.name`, `repository.top_level_entries`,
  `repository.nested_repositories`, `license.files`, `documentation.readme_files`,
  `documentation.doc_directories`, `build.systems`, `packaging.package_managers`, `ci.configurations`,
  `containerization.files`, `tests.directories`.
- *Content, over git's tracked-file set* — `repository.is_git_repository`,
  `repository.tracked_file_count`, `repository.tracked_size_bytes`, `code.extension_counts`,
  `code.languages`. Enumerating through `git ls-files` is why `node_modules/`, `.venv/`, and build
  artifacts never inflate a count: git already ignores them.
- *Git's own answers* — `git.head_state` (`attached` | `detached` | `unborn`), `git.branch`,
  `git.head_commit`, `git.root_commits`, `git.commit_count`, `git.tag_count`, `git.remotes`,
  `git.submodules`, `git.worktree_status`, `git.first_commit_at`, `git.last_commit_at`.

**Never observed, because each is a conclusion rather than a fact:** which license this is (MIT vs
Apache), the "primary" language, the project type, quality, activity level, importance. A `LICENSE`
file existing is a fact; what it *says* is an interpretation, and interpretation is `01B`'s job.

**No content ever leaves the repository.** Not file bodies, not commit messages, not diffs — metadata
only. Content identity is already supplied by git itself (`git.head_commit`), so nothing here needs to
hash a byte of the operator's source.

### The digest, and why it is stable

`artifact_digest` = SHA-256 over canonical JSON (`sort_keys`, compact separators — the same recipe as
every other Lighthouse contract). It covers every fact and the producer's identity, and excludes
exactly the volatile fields: `observation_set_id`, both levels of `observed_at`, and itself.

So **an unchanged repository produces the same digest forever**, and any change to anything observed
produces a new one. That is what makes the digest a *content identity* rather than a timestamp — and
it is the `dedup_key` `01B` needs.

### Reading a repository without touching it

Source immutability is a **tested property**, not an aspiration: the test hashes the entire tree —
`.git/` included — before and after a run and asserts it is byte-identical.

It is not free. Every git call goes through one chokepoint with a from-scratch environment, and two
settings there are load-bearing:

- **`GIT_OPTIONAL_LOCKS=0`** — without it, `git status` refreshes the stat cache and **rewrites
  `.git/index`**. That is a write to the repository we promised only to look at. (Removing this
  setting turns the immutability test red — it was verified by doing exactly that.)
- **`-c core.fsmonitor=false`** — a repository-local `core.fsmonitor` hook is **code execution**, and a
  command-line `-c` overrides repository config. Nothing observed here is ever executed.

The environment is built from scratch rather than inherited, so an ambient `GIT_DIR`/`GIT_WORK_TREE`
cannot redirect the read, and the operator's own `~/.gitconfig` cannot alter what is observed. Only
read-only plumbing subcommands are ever run. **Remote URLs are credential-redacted** (userinfo is the
one place a token can hide in a URL); scp-style `git@host:path` is preserved verbatim, because there
`git` is the SSH user, not a secret.

## Update — public-source check facts (LHP-INTEL-04B)

This charter covers the *local* observation track (repositories and documents). A sibling fact layer, **LHP-INTEL-04B**, adds the operator's first *networked* facts: `lighthouse public-sources` records an append-only history of HTTPS checks of admitted public locators through one hardened chokepoint (`public_source_intake.py`; ADR-0022). It reuses the raw-byte `content:<sha256>` recipe (so a public artifact can share identity with a local file or document) but keeps its own append-only ledger — the observation store stays deduplicated-by-content, while a check ledger must retain repeats because cadence is evidence. It creates no candidate and states no significance. See [`PUBLIC_SOURCE_CHECKS.md`](PUBLIC_SOURCE_CHECKS.md).

## Sequence

```text
LHP-DIRECTION-02   freeze governance, charter this layer                    ← done
        ↓
LHP-INTEL-01       charter + reuse audit (this document)                    ← done
        ↓
LHP-INTEL-01A      repository_intake — deterministic observation, no writes ← done
        ↓
LHP-INTEL-01B      observation_normalizer — facts with identity, stored     ← done
        ↓
LHP-INTEL-01C      document_intake — PDF/Markdown/text, same fact boundary       ← done
        ↓
LHP-INTEL-01D-A    observations — the shared contract gets its own home         ← done
        ↓
LHP-INTEL-01E      observation_intake — `lighthouse observe`, the runtime boundary ← done
        ↓
LHP-INTEL-02A/02B  observation_query — see them; ask where else they occur      ← done
        ↓
LHP-INTEL-02C      the significance BOUNDARY — decided, accepted as ADR-0020    ← done (analysis)
        ↓
LHP-ID-PRESERVE-01 the external prototype preserved under version control       ← done
        ↓
LHP-INTEL-03A      repository FILE-CONTENT identity — SHA-256(raw bytes)        ← done (a fact, not a score)
        ↓
LHP-INTEL-03B      pattern_candidate.v1 + ONE profile-owned rule + disposition  ← NEXT (the first interpretation)
        ↓
                   profile-portability checkpoint — prove a NON-invention profile
        ↓
                   selective invention migration — one detector family at a time
        ↓
                   profile runtime consumers (a profile→runtime resolver)
        ↓
                   activation reconsidered — on evidence, per ADR-0018 §4
```

> **The boundary, now that it is decided ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)).**
> **Facts first. Interpretation second. Findings only after evidence and review.** A repeated fact is not
> automatically significant. A **candidate** is not a **hypothesis**; a hypothesis is not a **finding**; a
> finding is **not permission** to publish or execute. **Detector rules are PROFILE-OWNED**; the
> candidate/hypothesis/finding mechanics are **profile-neutral**. No single composite score may become
> truth — the first candidate contract carries **no scalar at all**.
>
> **The fact packet came first, and it has shipped.** `LHP-INTEL-03A` closed the gap that blocked any
> trustworthy first rule: `repository_intake` recorded file *paths and names* — **never file content** —
> so *"two repositories independently implement this"* and *"I copied my Dockerfile out of the last
> project"* were **the same observation**. They are now distinguishable, because
> `content:<sha256>` is `SHA-256(exact raw bytes)` and a copy has the same bytes.
>
> **That distinguishability is evidence, not a verdict.** 03A says only *these scopes contain these
> exact bytes*. It does not say the file was copied, which came first, who wrote it, or that any of it
> matters. Two repositories sharing a `Dockerfile` byte-for-byte is a **fact**; calling it "duplicated
> work" or "a reusable component" is `03B`'s job — under a **profile-owned rule**, with counterevidence
> (shared root commit, shared remote, vendoring, an in-repo copy), and still short of a finding.

### `LHP-INTEL-01A` — shipped

`modules/repository_intake.py`. Pure: no database, no network, no LLM, no config, no CLI, no `main.py`
wiring — it returns an object and writes nothing. Read-only toward the source as a **tested** property.

### `LHP-INTEL-01B` — observation normalization (shipped)

`modules/observation_normalizer.py` (pure) + three fact-only tables in `modules/db.py`.

An observation is a *transcript*: 27 facts about one repository, 14 of them collections
(`["Dockerfile", "compose.yml"]`, `{"Python": 122}`). Nothing inside a transcript is addressable — two
repositories that both use Docker share nothing. Normalization turns those facts into **primitives with
identity**: `technology:docker` is the same entity wherever it is seen, a root commit is the same entity
in every clone of a project, and a file reported by two different observations becomes one entity that
remembers both.

| | |
|---|---|
| **Entities** (7) | `repository` · `file` · `directory` · `language` · `technology` · `remote` · `commit` |
| **Relationships** (9) | `contains_file` · `contains_directory` · `contains_repository` · `has_submodule` · `written_in` · `uses_technology` · `has_remote` · `has_root_commit` · `has_head_commit` |
| **Scope** | `file`/`directory` are repository-scoped; `language`/`technology`/`remote`/`commit` are **global** — that is what lets two repositories join |

A repository found inside another gets the identity it *would* have had if observed directly
(`sha256` of its canonical absolute path — 01A's own rule), so a nested clone and a checked-out
submodule resolve to **one** entity carrying **both** provenances.

**Three properties, each structural rather than aspirational — and each verified by deliberately
breaking the guard and watching a test go red:**

1. **Nothing observed is lost.** The repository entity's `attributes` are *exactly* the determined
   observations: `set(attributes) == {o.observation_type for o in artifact if o.confidence == 1.0}`.
   One assertion, not an inspection. The source artifact is also carried verbatim into the store.
2. **An opinion is unrepresentable.** An attribute key must be a member of the closed 27-name
   observation vocabulary — which contains no word for *score*, *priority*, *importance*, *severity* or
   *quality*. There is nowhere to put an opinion, and no database column to hold one. (The test forges a
   record with `importance` **and re-stamps its digest**, so the vocabulary is the only gate left.)
3. **Undetermined is not absent.** A `confidence == 0.0` observation contributes no entities and is
   reported in `undetermined_observations`. Otherwise "I could not enumerate the languages" would look
   exactly like "this repository contains no code" — a false conclusion manufactured by the plumbing.

Digests exclude timestamps, so an unchanged repository normalizes to byte-identical records forever;
`store_normalized_observation()` is therefore **insert-only and idempotent by content identity** —
observing the same repository a hundred times stores it once.

### The persistence decision (audited before `db.py` was touched)

Every normalized object must carry seven fields — *identity, type, value, source_observation,
derivation_version, timestamp, digest* — and **no object may exist without provenance**. Measured
against that, no existing store qualifies, and two fail *structurally*:

| Store | Why it cannot hold a normalized fact |
|---|---|
| `alerts` / `AlertRecord` | It is a **triage surface**. Columns `score`/`score_label`; the whole query surface (`fetch_alerts_min_score`, `lighthouse alerts --label`), the correlator, and the Nostr publish gate (`score >= 7.0`) assume a score. "231 tracked files" is not an alert. |
| `graph_nodes` | **No column exists for provenance, time, or digest** — and `upsert_graph_node` *overwrites*, destroying history. Provenance could only be smuggled into the untyped `properties_json` bag. |
| `graph_edges` | **`weight REAL NOT NULL`, validated `0.0–1.0`.** A weight is a claim about strength: the contract *requires an opinion*. |

→ **A new intelligence storage boundary**, inside `modules/db.py` (which is still the one persistence
layer, and already hosts four independent domains). `observation_sets` · `normalized_entities` ·
`normalized_relationships`: insert-only, fully provenanced, and with **no score, weight, priority,
importance, severity or rank column** — pinned by a `PRAGMA table_info` test, so the schema itself
cannot hold an opinion.

**Nothing in `01B` writes a `GraphNode` or a `GraphEdge.`** A repository entering the intelligence graph
is a decision that it *matters* — that is question three, and it gets its own packet.

### The Docker/git decision (the 01A finding — decided, not papered over)

**Repository observation is a HOST-ONLY capability. The Dockerfile is not touched.**

Adding `git` to the `python:3.11-slim` image would make the code *run* but give it **nothing to
observe**: the deployed container is the OSINT worker (Start9/Fly/Railway), and the operator's
repositories are not mounted into it. Mounting them would extend a network-facing container's trust
boundary to cover the operator's private work — a security decision deserving its own packet and ADR,
not a silent `apt-get install git`. `observe_repository()` already fails closed with
`GitUnavailableError`, which is the correct behavior there. The normalizer and the store are pure
Python and work in the container regardless (verified: `import main` succeeds with no git binary
present), so if a future packet *does* mount repositories, only the Dockerfile changes — no contract
does.

### `LHP-INTEL-01C` — document observation (shipped)

`modules/document_intake.py` → `document_observation.v1`, normalized through **the same** boundary.
No document database. No second provenance system. No relationships (see below).

**The distinction the packet exists for.** Extraction can fail, and a failure that reads as an answer is
worse than no answer at all:

| | means | value | confidence |
|---|---|---|---|
| `empty` | **this document contains no text** — a fact | `0` | **1.0** |
| `extracted` / `truncated` | text obtained (truncation is stated, never silent) | `N` | 1.0 |
| `unreadable` / `extractor_unavailable` / `too_large` | **I could not read its text** — the absence of a fact | `None` | **0.0** |

01A's confidence model already encoded exactly this (`1.0` = determined *including provably absent*;
`0.0` = undetermined, value always `None`), so 01C **reuses it rather than inventing a second, subtly
different one**. `text.extraction_status` is itself always determined: what happened when we read the
file is a fact either way. Every member of the status vocabulary is reachable — a vocabulary with dead
entries lies about what the system can tell you.

**Identity is the bytes, never the text.** `document.content_digest` is the SHA-256 of the file, so the
same document copied to a second path is *one entity observed twice*, and no truncation bound can make
two different documents collide. `document_id` (SHA-256 of the canonical absolute path) is a **different
digest answering a different question** — *where I looked*, not *what I found*.

> ⚠️ **This invariant was nearly untested.** The first test used a *markdown* fixture, whose extracted
> text is a byte-identical UTF-8 decode of the file — so `text.digest` and `document.content_digest`
> coincide and the assertion was **vacuous**. A mutation run (key identity on the text instead of the
> bytes) passed all 115 tests. A PDF is the sharp case. Were identity keyed on text, a PDF and a `.txt`
> saying the same words would silently **merge into one entity**, and every empty document in the
> library would collapse into `sha256("")`.

**Metadata-first: the text is measured, then dropped.** Text is extracted to establish facts *about* it
(`text.character_count`, `text.line_count`, `text.digest`, `text.truncated`) and is **not** carried in
the artifact and never reaches the database. The packet's own required-field list stops at metadata +
extraction status. `text.digest` keeps a later re-extraction verifiable, so nothing is lost — and
putting the operator's private document contents into SQLite is a decision with its own consequences,
which a future packet must make deliberately rather than inherit by accident.

**The extractor is part of the observation.** pypdf's output can change between releases, so
`text.extractor` is recorded and digest-covered. The honest promise is therefore *"an unchanged
document, read by the same extractor, yields the same digest forever"* — a pypdf upgrade genuinely
changes what we could read, and the insert-only store records a **new** observation rather than silently
revising the old claim. pypdf is imported **lazily**: a stripped install degrades to an honest
`extractor_unavailable`, never to "this document is blank".

**Bounds on two axes, never silent.** Over `max_file_bytes` (64 MiB) → refused *unread* (`too_large`);
over `max_text_characters` (5M) or `max_pages` (5 000) → **truncated with explicit metadata**. The
content digest is streamed in chunks, so identity never depends on being able to hold the document in
memory: a file too large to *extract* is still one we can *identify*.

**Formats are a closed set: `.pdf`, `.md`/`.markdown`, `.txt`/`.text`.** HTML and the office formats are
**rejected deliberately.** The packet permitted HTML "only if already simple"; it is not. A tag-stripper
that quietly mangles entities, `<script>` and whitespace is precisely the "silently lose information"
the packet forbids, so we decline rather than half-support.

**Bytes that cannot be read at all fail closed** (`DocumentUnreadableError`) rather than degrading to an
observation — without a content digest a document has no identity, and a record that cannot identify its
own subject is not an observation of it. Text that cannot be extracted from *readable* bytes is the
other case, and stays an honest undetermined observation.

**No relationships, deliberately.** A lone document has none. A `format:pdf` node would be a join of
almost no value when `document.format` is already an attribute, and inventing an edge to make the model
look richer is *interpretation* — the question this layer does not answer. The join that **would**
matter, a document living inside an observed repository, is unavailable for a concrete reason: **01A
observes file *paths*, not file *content digests*, so there is nothing to join on.** That is the precise
unlock for a future packet.

### The boundary generalization (01C) — widen a right model, fork a wrong one

01B's fact boundary was **repository-shaped in three structural places**, and each *failed a document
closed*: every record required a SHA-256 `repository_id`; only a `repository` entity could carry
attributes; attribute keys had to belong to the repository vocabulary.

So 01C renamed `repository_id`/`repository_path` → **`scope_id`/`scope_path`** and added a closed,
digest-covered **`scope_type ∈ {repository, document}`**. A scope is *"the place I looked"*; its id keeps
exactly the rule it already had (SHA-256 of a canonical absolute path), so no validator was weakened.
`scope_type` is digest-covered rather than a label because it decides **which producer's validator owns
the carried artifact** — a set that lied about what it observed would otherwise be checked by the wrong
rules.

**This is the opposite verdict from the `AlertRecord` audit above, and the distinction is the lesson.**
`AlertRecord` is *semantically* wrong for facts: `score`/`weight` mean it cannot hold a fact without also
asserting an opinion about it. No rename fixes that — you **fork**. `NormalizedEntity` was *semantically
right* and merely **named and gated for one subject kind** — you don't fork a correct model, you **widen
its door**. Forking here would have produced exactly the "parallel document system" and "duplicated
provenance system" the packet forbids.

Nothing was deployed with the old column names (01B was uncommitted), so the rename cost no migration
then and would have cost one forever after.

### The Docker/git decision, restated for documents

Unchanged, and now load-bearing for a second reason: **document observation is host-only too.** The image
ships no `git`, and neither the operator's repositories *nor their documents* are mounted into a
network-facing container. The image does now carry `pypdf` (via `requirements.lock`), and `import main`
in a git-less container is verified to still work.

### `LHP-INTEL-01D-A` — the observation substrate (shipped)

`modules/observations.py`. A refactor, not a capability: the observation contract moved out of
`repository_intake` and into its own module, and the two collectors became importers of it.

The model had been living in the first collector that needed it, so `document_intake` imported its model,
its digest recipe, its validator **and its exception hierarchy** from a sibling collector. Two sources made
that awkward. A third would have made it wrong — the substrate would be owned by whichever collector
happened to be written first.

**The audit found the ownership problem was not theoretical. It had already produced a bug:**

| a malformed… | raised | wrapped by the normalizer? |
|---|---|---|
| repository artifact | `NormalizationError` | yes |
| **document artifact** | **`ObservationValidationError`** — which was, by inheritance, a **`RepositoryIntakeError`** | **no — it escaped** |

`_require_valid_document` caught `DocumentIntakeError`, but document validation raised a *repository*
error, so the wrapper never fired. A malformed document reported itself as a repository failure. Both
paths still failed closed, so nothing was unsafe — but nothing in 150 tests noticed, either, which is
exactly how it survived 01C.

`ObservationError` is now the shared base; `RepositoryIntakeError` and `DocumentIntakeError` both inherit
from it; `ObservationValidationError` and `SourceBoundaryError` belong to neither collector, because they
never did.

**What moved, and what pointedly did not.** The substrate owns the model (`Observation`, `ObservationSpec`),
the confidence contract, `canonical_digest()`, `validate_observations()`, `observation_from_dict()`, and the
shared errors. It does **not** own either *vocabulary* — 27 repository types and 14 document types are a
source's own business — and it does not own a vocabulary's closed value sets. The `git.head_state` check
moved *out* of the shared validator and into `RepositoryObservation.validate()`, where
`DocumentObservation.validate()` had always checked its own `text.extraction_status`. **Teaching the
substrate what a git head is would have been the same ownership mistake, pointed the other way.**

**The copies had already drifted.** `_require_exact_int` and `_require_utc_timestamp` were copy-pasted into
both collectors in 01C, and one packet later the two versions no longer matched — different messages, though
(fortunately) equal strength. This is the hazard 01C's own commentary named: *a fail-closed validator that
exists twice is a fail-open waiting to happen, because the copies drift and the weaker copy is the one that
decides.* It drifted in exactly one packet. The document-side messages were retired in favour of the
originals, which the normalizer also uses.

**Behaviour preservation is proven, not asserted.** Three golden digests — the digest recipe, a
full-27-type repository artifact and a full-14-type document artifact — were computed at the 01C commit
*before* the extraction and pinned as literals. They are safe to pin precisely because each is a pure
`dict → hash`, unlike a digest taken from a real PDF (whose value depends on the installed pypdf). All
three reproduce byte-for-byte, and the 1511 pre-existing tests still pass with every assertion untouched.

### `LHP-INTEL-01E` — the runtime intake boundary (shipped)

Four packets of observation machinery existed and **nothing called any of them**. You could not observe a
repository without opening a Python REPL. 01E is the caller, and nothing more: *"make observations possible
without manually writing Python."*

`modules/observation_sources.py` (the operator's declared work list) + `modules/observation_intake.py` (the
boundary that runs the collectors, normalizes, and persists) + `ObservationConfig` in `config.py` + one CLI
subcommand, `lighthouse observe`. It **counts what happened and stops** — no scoring, no ranking, no alerts,
no graph projection, no discovery, no scheduling, no LLM, no MCP tool.

#### The trust model, and why it has two halves

```text
LIGHTHOUSE_OBSERVATION_ROOTS   (env — the INITIAL SOURCE of the policy, not the policy)
        ↓
config.py → ObservationConfig.allowed_roots     ← the AUTHORITATIVE trust boundary
        ↓
data/observation_sources.json  (the WORK LIST — may narrow, can never widen)
        ↓
observe_repository(path, allowed_roots=…) / observe_document(path, allowed_roots=…)
```

**Both collectors accept `allowed_roots=None`, and `None` means *no boundary at all*.** That single fact
decides the whole design: if this boundary ever passed `None` downward, every path on the disk would become
observable and nothing else in the system would notice. So the service never passes `None` — with no roots
configured it **refuses the entire run**. Empty is a valid configuration and it *fails closed*; it must
never degrade into "no boundary, so observe anything". A mutation test pins exactly this: swap
`allowed_roots=roots` for `allowed_roots=None` and the outside-root and symlink-escape tests go red.

**The work list is not the boundary, and cannot become one.** An allowlist stored in the same file as the
paths it authorizes is decorative — `{"allowed_roots": ["/"], "sources": [{"path": "/etc/shadow"}]}` would
pass its own check. It would catch an operator's typo, not a bad edit to that file. So the roots live in
config (a separate control plane: the deployment sets them, the data file cannot), and widening is made
**unrepresentable rather than merely rejected** — the sources schema has *no field in which a root could be
written*, and an unknown key is refused. The same discipline the substrate uses to make significance
unrepresentable, pointed at a different hazard.

Consequently `modules/data_paths.py` is the canonical reader of `LIGHTHOUSE_DATA_DIR` and
`DB_PATH`; `config.py` reads `LIGHTHOUSE_OBSERVATION_ROOTS` for the observation trust boundary.
`modules/targets.py` resolves `targets.json` via `data_paths.data_path()` — not a second env read.
An AST test forbids `os` outright in both observation modules.

#### What the boundary guarantees

* **Dry run writes nothing at all** — not a row, not a table, not the database file. `init_observation_tables()`
  is *itself* a write, so it is called lazily on first store. That one change collapses "everything was
  refused" and "dry run" into the *same* guarantee instead of two similar-sounding ones.
* **Repeat intake is idempotent** because identity is content: an unchanged repository normalizes to the
  same digest forever, so `store_normalized_observation` returns `False` and the run honestly reports
  `already_present` rather than pretending it did work.
* **A bad source fails itself, never the run** (`main.py`'s module-fault-isolation rule), reporting a
  **closed reason token** — `source_outside_allowed_roots`, `source_not_found`, `not_a_git_repository`,
  `git_unavailable`, `unsupported_document_format`, `document_unreadable`, `invalid_observation`,
  `normalization_failed`, `unexpected_error` — rather than a stack trace.
* **Reading is not writing**: the observed tree, `.git/` included, is byte-identical afterwards (tested).
* **No new execution capability.** The boundary has no subprocess of its own, no `os`, no network, no LLM.
  The only subprocess in the whole layer remains 01A's single hardened git chokepoint, which 01E calls but
  does not reimplement.

#### The CLI decision (reviewed, not smuggled)

`test_no_activation_boundary.py` regexes `add_parser("…")` — it pins **subcommands**, not `--module`
choices. So a `--module observe` value would have slipped in with *zero* test edits, while a real `observe`
subcommand cost one line in `EXPECTED_SUBCOMMANDS` (16 → 17). That asymmetry is a trap. Every local,
synchronous, non-network operation in this CLI is already a subcommand (`graph-backfill` is the exact
analogue: idempotent local DB write, `--dry-run`, `--json`), whereas all 13 `--module` values are async
**network** collectors that emit `AlertRecord`s and pass through `validate_config()`. Choosing `--module`
to dodge a one-line review gate would have been picking the semantically wrong home to avoid deliberation.
The subcommand was added deliberately, and the activation guard beneath it is unchanged.

`--source PATH` is a **filter over already-declared sources**, never a way to name a new one — otherwise the
work list would stop being the work list and the boundary would be one careless flag away from being
whatever the caller typed.

#### Still host-only

The deployed image has no `git`, and neither the operator's repositories nor their documents are mounted
into it. The container therefore configures no roots, and `lighthouse observe` refuses — which is the honest
outcome, not a bug. Verified in the image.

### `LHP-INTEL-02A` — the observation query surface (shipped)

01E closed the write gap and immediately opened its mirror: the facts now **accumulated where nobody could
see them**. `lighthouse observe` wrote three tables and printed a count, and there was no way to ask what
was in them short of opening `sqlite3`. 02A is the read side — *"show me what Lighthouse knows"*, and never
*"tell me what Lighthouse thinks."*

`modules/observation_query.py` (the read boundary) + a read-only query family in `modules/db.py` + one
nested CLI subcommand, `lighthouse observations list|show|search|stats`. No scoring, no ranking, no
significance, no inference, no LLM, no MCP, no scheduling. It counts, it lists, it shows.

#### The read boundary

```text
CLI  ·  MCP (future)  ·  UI (future)  ·  reports (future)
        ↓
modules/observation_query.py     ← shape · bounds · closed filter vocabulary · validate-on-read
        ↓
modules/db.py                    ← SQL only, and only through a mode=ro connection
        ↓
observation_sets · normalized_entities · normalized_relationships
```

The CLI does not reach into `db.py`. Neither will the next consumer. Storage layout stays behind one door,
so an MCP tool or a report is later a change to a *consumer*, never to the query layer.

#### The packet correction: a repository is a subject, not a sensor

The packet as written specified `"source": "repository_intake"` in its `list` example. **That was wrong,
and the implementation does not follow it** — it conflated the *scope* being observed with the *producer*
of an observation. A repository is a thing being looked at, not a thing that looks. Deriving
`repository → repository_intake` would have fabricated provenance the store does not contain.

The substrate already records the real thing, verbatim in `artifact_json`: every `Observation` carries the
`source` pinned by its `ObservationSpec` — the **sensor** that established it. There are exactly three, and
they are the collectors' own declarations:

| sensor | declared in |
|---|---|
| `filesystem` | `repository_intake.SOURCE_FILESYSTEM`, `document_intake.SOURCE_FILESYSTEM` |
| `git_metadata` | `repository_intake.SOURCE_GIT` |
| `text_extractor` | `document_intake.SOURCE_EXTRACTOR` |

So the surface emits **`sensors: [...]`** and filters with **`--sensor`**. There is no `source` field and no
`--source` flag. No new column, no contract change — a query-layer correction only.

#### What "read-only" means here, and why it is a mechanism

`sqlite3.connect(path)` **creates the database file** when it is missing. A careless read on a machine that
has never observed anything would therefore have left a `data/state.db` behind — destroying the exact
guarantee 01E established (a refused or dry run creates nothing). And an ordinary connection permits every
write, leaving "this layer is read-only" as a promise kept by convention.

So every query goes through `db.get_readonly_connection()` — a `mode=ro` URI connection. A missing database
raises rather than being created, and INSERT / UPDATE / DELETE / CREATE / DROP all raise *attempt to write a
readonly database*. **The protection is a mechanism, not a promise**, which is what makes the packet's own
requirement — "mutation testing should prove removing the protection causes failure" — meaningful: there is
something to remove. Swap `mode=ro` for an ordinary connection and
`test_the_query_connection_refuses_to_write` goes red.

The empty cases are answered without writing: `db.observation_tables_exist()` checks the file, then
`sqlite_master`. No store means zero facts, which is a true answer and not an error — `{"count": 0}`, exit 0.

#### Reads validate, exactly as writes do

`NormalizedEntity.validate()` recomputes the digest **and** enforces the closed attribute vocabulary. Both
gates were already there for writes; 02A runs every row back through the contract on the way *out*. So a
forged `{"importance": 0.9}` attribute injected by raw SQL is refused when read — by the digest recompute if
the forger was lazy, and by the closed vocabulary if the forger re-stamped the digest. A tampered fact store
is a **signal**, so the failure is loud (`TamperedObservationError`), not papered over.

#### Nothing is ranked, and the quiet place that would have started it

`stats` returns counts, and orders them **by name**. Ordering a dict of counts by count descending is "the
most common technology" — a ranking, and it would have been the first one this system ever built. Every
member of the closed vocabulary appears, even at zero, so the shape does not depend on the data.

`confidence` **is** preserved, and is deliberately not on the forbidden-vocabulary list: it is *epistemic*
(did I determine this?), not *evaluative* (does it matter?). Dropping it to satisfy a keyword ban would
delete the distinction 01C exists for — "no text" (count `0` at confidence `1.0`) versus "I could not read
it" (`None` at `0.0`).

#### Determinism, and the clock

Output is byte-stable for a given store: the query layer writes its keys in a fixed order, and the SQL
`ORDER BY` clauses carry a tiebreak — the obvious sort keys were **not unique** (two sets can share a
`stored_at`; one entity observed in two artifacts is two rows with the same `entity_id`), so the previous
reads were not actually reproducible. `--since`/`--until` compare against *stored* timestamps. This module
may **parse** a timestamp; it may never ask what time it is, and an AST test pins that difference.

#### Abbreviated ids

`show --id` accepts the full 64-character digest **or an unambiguous hex prefix of at least 8 characters** —
git's abbreviated object ids, and deliberately nothing more. Exactly one match resolves; none is an error;
**two or more is an error that names every candidate**. Never the closest match, never the first, never
fuzzy, never semantic. The canonical identifier remains the full SHA-256 everywhere the system speaks — the
prefix is accepted as operator *input* only, and the full digest is always what comes back out.

#### The filters that scan, and the one that would have lied

`sensor` and `observation_type` live *inside* the stored artifact, which has no index, so they are applied
in Python. They must be applied to the **whole** matching set and never to a page SQL has already truncated
— filtering a truncated page silently returns the wrong answer and looks exactly like the right one. The
scan is bounded like every other read surface here, and when the bound bites the answer says
`scan_truncated: true` rather than quietly being incomplete.

#### The CLI decision (the pin got stronger, not weaker)

`observations list|show|search|stats` is this CLI's **first nested subcommand**. The boundary test regexes
`add_parser("…")` across all of `main.py`, so `list`/`show`/`search`/`stats` would have landed in the same
flat set as `alerts` and `graph` — leaving the pin unable to tell a top-level command from a sub-action. So
the test now **buckets by receiver** and pins all three sets: top-level (18), the observation actions (4),
and — the load-bearing one — **the union of every `add_parser` call in the file**, so a parser hung off any
new receiver still has to be accounted for. The activation guard beneath it is untouched.

### `LHP-INTEL-02B` — cross-scope fact discovery (shipped)

02A let an operator see observation **sets**. It could not answer the question the normalized model was
*built* to answer: **"where else have I built this?"** 01B gave `technology:docker` a global identity
precisely so two repositories could meet on it; 01C gave a document an identity derived from its **bytes**
precisely so the same file in two places would be one entity observed twice. Both joins had been sitting in
the store, unreachable, ever since.

`lighthouse observations where` and `lighthouse observations shared` reach them, and stop there:

> **Cross-scope fact discovery performs exact lookup and direct set intersection over stored facts. It does
> not rank, infer, score, or interpret relationships.**

Both live in `modules/observation_query.py` — the 02A read boundary, **extended, not forked**. No new table,
no new column, no new index, no schema change: 02B is read-surface-only.

#### An occurrence is an entity row, not a relationship

`normalized_entities.scope_id` is a **stored column**, so *"this entity was directly observed in this
scope"* is a fact the store already holds — it does not have to be inferred from an edge, and it must not
be. Two of the best cross-scope queries have **no relationships at all**:

- a **document** scope emits exactly one entity and **zero relationships** (01C, deliberately);
- a **repository** entity is only ever a relationship *subject*, never an object.

A relationship-only model would have answered *"the same document lives in two places"* with silence.
Relationships are therefore attached as **additional** evidence of *how* — a list, empty where the store
holds none, which is a fact rather than a gap.

#### Which entities can actually join (the packet's own table was wrong)

| entity type | id shape | joins across scopes? |
|---|---|---|
| `language` `technology` `remote` `commit` | `type:value` | **yes** — global |
| `repository` | `repository:<sha256 of the canonical abs path>` | **yes** — a nested clone and the repository itself are one entity with two provenances |
| `document` | `document:<sha256 of the BYTES>` | **yes** — the same file at two paths is one entity, two scopes |
| `file` `directory` | `type:<scope_id>:<name>` | **never** — the id embeds its scope |

The consequence an operator *will* trip over: **"both repositories have a Dockerfile" does not appear as a
shared `file:` entity.** `file:<A>:Dockerfile` ≠ `file:<B>:Dockerfile`, by construction. The join is
`technology:docker`. Somebody who does not know that will think `shared` is broken, so a test pins it.

#### Packet correction — evidence is verbatim, and it lives where it is stored

The packet's occurrence example carried a scalar `confidence: 1.0` and a scalar
`relationship_type`/`relationship_digest`. The store can honestly produce neither, and the correction was
made **before** implementation (operator-approved, 2026-07-14):

> *"The initial occurrence example assumed one confidence value and one relationship per occurrence. The
> persisted model supports observation-level confidence and zero-to-many relationships, so the query
> contract exposes the validated stored evidence verbatim."*

- **No aggregate `confidence`.** Confidence belongs to an individual *observation*, and 01B builds an entity
  only out of observations it **determined** — so an occurrence-level scalar would be a derived constant
  `1.0` forever, asserting a variability that cannot exist, in exactly the slot where significance likes to
  hide. `supporting_observations[]` carries each observation's own `confidence`, `sensor` and `observed_at`,
  read verbatim off the **validated** stored artifact.
- **No scalar relationship.** An occurrence has zero, one, or *several*: a nested repository is both
  `contains_repository` **and** `has_submodule`; a single-commit repository's commit is both its
  `has_root_commit` and its `has_head_commit`. A scalar would have had to choose.
- **No top-level `value` or `digest`** — and this one only became visible while implementing. Both **vary
  per occurrence**: an entity's digest covers the artifact it came from, and the same document bytes can be
  filed under two different names (`note.md` and `note-backup.md` are one entity with two values). Hoisting
  either onto the entity would force a choice between two true answers. The `entity` block therefore carries
  only what the identity itself fixes: `entity_id` and `entity_type`.

#### Entity ids are exact; scope ids may be abbreviated

A **scope** id is `sha256(canonical absolute path)` for *both* kinds of scope — uniformly a digest, so 02A's
abbreviation rule carries over unchanged (≥8 hex chars, one match resolves, **two or more names every
candidate**).

An **entity** id is *not* uniformly a digest (`technology:docker`, `language:Python`, `remote:<url>`), so
there is **no prefix form for it**. A prefix rule that applied only to the digest-tailed types would be a
type-conditional second identifier system, and `technology:doc` resolving to `technology:docker` is fuzzy
matching wearing a hat. The id is split on the **first colon only** — a remote's id contains a URL, and a
URL contains colons.

#### An unknown scope fails closed; an unknown entity does not

They are different claims, and only one of them is answerable.

- `where --entity-id technology:rust` on a store that has never seen Rust → `{"occurrence_count": 0}`,
  **exit 0**. The id is well-formed; Lighthouse simply knows nothing about it.
- `shared --scope-id <never observed>` → **exit 1**. Returning an empty intersection would say *"these two
  repositories share nothing"* about a scope that was never observed. An unanswerable question is not a
  negative answer.

#### Ordering, bounds, and the index decision

Shared entities sort by `(entity_type, entity_id)`; occurrences sort by `(scope_id, source_artifact_digest,
derivation_version)` — **total**, because that triple *is* the unique index. **Nothing sorts by count.**
Ordering shared entities by occurrence count would be "the most common technology" — the first ranking this
system ever built, arriving by accident.

The intersection is computed over the **whole** of each scope and only then bounded; bounding first would
answer a different question and call it an intersection. `EXPLAIN QUERY PLAN` says both required lookups are
already index-supported (`idx_normalized_entity_identity (entity_id=?)`, `idx_normalized_entities_scope
(scope_id=?)`), so **no index was added**. Relationship evidence is one bounded *covering-index* scan per
command — never one per occurrence — and when a bound bites, the answer reports `scan_truncated: true`
rather than passing a partial answer off as a complete one.

*Deferred, disclosed:* `idx_normalized_relationships_object` would turn that scan into a seek. It cannot be
created through a `mode=ro` connection (so it would only materialize on a later write), and correctness
never depends on it. It belongs to a persistence packet, not a read-surface one.

#### Reads validate — including the artifact

`supporting_observations` are read out of the stored artifact, so the artifact is re-validated through **the
contract of the producer that wrote it** (`scope_type` decides which — that is exactly why the column is
digest-covered), and the recomputed `artifact_digest` must equal the one the entity rows cite. A substituted
artifact is not evidence. This is why the read boundary imports the two collectors' *validators*: a second
copy of a fail-closed check is a fail-open waiting to happen (01D-A).

### Traps for the next packet

1. **Any new CLI subcommand will fail `tests/unit/test_no_activation_boundary.py`**, which pins the exact
   subcommand set (**18 top-level names since 02A added `observations`**, plus its **6** nested actions
   since 02B added `where` and `shared`). Do not trust a remembered number — the nested count has already
   changed once; read the test.
   Updating that pin must be a reviewed act, never an incidental fix. Two asymmetries make this a trap: the
   pin regexes `add_parser("…")`, so a new **`--module` value is not pinned at all** and would slip in
   silently (do not reach for `--module` to avoid the review — it is the wrong home anyway; every `--module`
   value is an async network collector); and since 02A the pin buckets **by receiver**, so a parser attached
   to a *new* subparser variable must be added to the union assertion or the test fails, by design.
2. Tests that touch persistence must isolate the database
   (`monkeypatch.setattr(db, "DB_PATH", tmp_path / "x.db")`), or they write to the developer's real
   `data/state.db` and fail in CI — which has no `data/` at all — with `no such table`.
3. **`pypdf` is not in the developer's shell environment by default.** `python` on this machine resolves
   to a venv that happens to have it; the project's own `.venv` does not, and it reached the lock files
   only in 01C. CI installs `requirements-dev.lock`, so **a test that assumes an unlocked dependency
   passes locally and fails in CI**. Related: the locked pypdf (6.x) differs from the shell's (5.x) —
   never assert a pypdf version or a resulting digest literal.
4. **A third observation source imports `modules.observations`, never a sibling collector.** That is now
   enforced, not merely advised: the AST allowlists in `test_document_intake.py` and
   `test_repository_intake.py` no longer permit a collector to import a collector. Its *vocabulary* and
   any closed value sets stay in the new module; only the model is shared. (01D-A did this extraction;
   `_fact`/`_undetermined`/`_bounded` are still repository-private and are the natural next promotion
   when a second source actually needs them — do not copy them, move them.)
5. **`allowed_roots=None` means *no boundary*, in both collectors.** Any new caller of `observe_repository`
   or `observe_document` must pass real roots — sourced from `ObservationConfig`, never from the
   environment directly, and never from the observation or the work list itself. 01E's rule stands: with no
   roots configured, **refuse**; never fall back to observing anything. Cadence is still an open question —
   `observe` is invoked by hand, and nothing schedules it. That is deliberate; a scheduler is not a missing
   feature, it is a decision nobody has made.
6. **Mutation-test the load-bearing claim, and check that the fixture can actually see the difference.**
   Four times now, a guard has been weaker than it looked (01B: the digest check fired, not the
   vocabulary check; 01C: a markdown fixture made two different digests coincide; 01D-A: *nothing at all*
   covered the document error path, which is how that bug shipped; 02B: every shared entity in the first
   fixture had exactly three occurrences, so "ordered by identity, never by count" was a test that could
   not tell the two apart — a shared git remote, present in one scope from the start and in the other only
   after a second observation, made the counts differ and the guard real). Break the guard, and confirm the
   *right* test goes red — by name.
7. **A read must not create the database.** `sqlite3.connect(path)` creates a missing file, so any new
   reader must go through `db.get_readonly_connection()` / `db.observation_tables_exist()` — never
   `get_connection()`, never `init_*`. This is not fussiness: an ordinary connection in a read path would
   silently undo 01E's "a refused or dry run writes nothing at all", and no test outside this layer would
   notice.
8. **New consumers go through `modules/observation_query.py`, not `modules/db.py`.** That boundary exists so
   that an MCP tool, a report, or a UI is a change to a *consumer* — the storage layout stays behind one
   door, and the closed filter vocabulary, the bounds, and validate-on-read are enforced once rather than
   re-implemented per caller. A consumer that reaches past it inherits none of those guarantees.
9. **A `LIMIT` before a filter, an intersection, or a sort is a wrong answer that looks exactly like a right
   one.** 02A hit this with its artifact-side filters; 02B hits it again with `shared` (bounding each
   scope's entities *before* intersecting them returns a plausible, deterministic, completely wrong set).
   Filter, intersect, and order over the **whole** matching set; bound last; and when a bound bites, say
   `scan_truncated: true` instead of quietly being incomplete.
10. **The next thing this layer is missing is not a query — it is a reason to care.** An operator can now
    ask *what exists* and *where else it exists*, and gets exact stored facts. Nothing in the system yet
    answers **why any of it matters**, and nothing here may start to: no score, no rank, no similarity, no
    inference, no "related projects". That question is `LHP-INTEL-02C`'s to *decide*, not any packet's to
    answer by accident — and the way it arrives by accident is one convenience function at a time
    (`_rank_scopes`, `most_common_technology`, a count-descending sort). An AST test pins the names.
