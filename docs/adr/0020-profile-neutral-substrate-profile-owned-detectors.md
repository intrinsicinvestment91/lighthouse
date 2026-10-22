# ADR-0020 — A profile-neutral intelligence substrate with profile-owned detectors

- **Status:** Accepted
- **Date:** 2026-07-14
- **Packet:** LHP-ID-PRESERVE-01 (adopts the accepted conclusions of `LHP-INTEL-02C`)
- **Supersedes:** —
- **Superseded-by:** —

## Context

Lighthouse can now say **what exists** (`LHP-INTEL-01A`–`01E`), **how it is represented** (`01B`), and
**where else a fact occurs** (`02A`/`02B`). It still cannot say **why any of it matters** — deliberately.
Every layer so far was built structurally unable to score: there is no `score`, `weight`, `priority`,
`importance`, `severity` or `rank` column in any of the three fact tables, and an AST test forbids even
the *names* from appearing in the read boundary.

`LHP-INTEL-02C` was chartered to decide where facts end and interpretation begins, **before** any code
began interpreting. This ADR records the conclusions the operator accepted.

Two of them are not obvious, and both were reached by looking at the code rather than the plan:

1. **The next packet cannot be the first detector.** `repository_intake` records file *paths and names* —
   never file *content*. Its only `sha256` hashes the repository's **path**. So Lighthouse today cannot
   distinguish *"two repositories independently implement the same capability"* from *"I copied my
   Dockerfile out of the last project"* — the dominant false-positive mode for a solo operator reusing
   their own boilerplate. A capability detector shipped now would present coincidence as a pattern. The
   ordering below is forced by the threat model, not chosen for convenience.

2. **Detectors are the first runtime consumer a profile has ever had.** [ADR-0018](0018-governance-frozen-pending-runtime-intelligence.md)
   froze profile governance precisely because *nothing in Lighthouse reads a profile*. Profile-owned
   detectors are the thing that would read one. This ADR does not unfreeze governance — but it is the work
   that eventually can.

## Decision

### The platform is profile-driven; invention discovery is one profile

**Lighthouse is a sovereign, adjustable, profile-driven intelligence platform. Private Invention Discovery
is one selectable profile — not the product identity.** These remain first-class, co-equal missions:

Public Scientific Lighthouse · Private Invention Discovery · Patent and Research Tracking · Scientific
Preservation · Repository Capability Discovery · Aerospace and Defense Monitoring · Fusion Research
Monitoring · Custom Operator Profile.

The current invention-oriented development track **must not redefine every Lighthouse pattern as an
invention.**

### The split: neutral mechanics, owned interpretation

| **Profile-neutral (shared platform)** | **Profile-owned (declared per profile)** |
|---|---|
| observation contracts · normalization · fact persistence · provenance · exact identities · direct relationships · temporal history | approved source classes · source-admission policy · enabled collectors |
| **candidate, hypothesis and finding mechanics** · counterevidence · evidence references · operator review history | **detector families · pattern rules · evidence requirements · counterevidence requirements** |
| backup and restore · local query surfaces · privacy-enforcement foundations | privacy rules · local-only policy · publishing eligibility · retention · profile-specific candidate types · operator-facing outputs · disclosure-package eligibility · dead-man's-switch eligibility |

Shared names stay neutral: `Observation`, `NormalizedEntity`, `NormalizedRelationship`, `PatternCandidate`,
`Hypothesis`, `Finding`, `EvidenceReference`, `CounterevidenceReference`, `ReviewDisposition`.
Profile-specific names may specialise: `InventionCandidate`, `RepositoryCapabilityCandidate`,
`ScientificPreservationCandidate`, `DisappearingScientificSourceCandidate`, and so on.

> **Naming note.** "Detector" already means *correlator detector* in this codebase. New significance work
> says **rule** (`rule_id`, `rule_version`) to avoid the collision.

### The classes are distinct, and never collapse

**A `PatternCandidate` is not a `Hypothesis`. A `Hypothesis` is not a `Finding`. A `Finding` is not
permission to publish or execute.** Each promotion is a separate, explicit step. There is no automatic
edge anywhere in the chain — most especially none from *"a rule fired"* to *"this is true."*

**Cross-scope recurrence is a fact until a declared, profile-owned rule interprets it.** That a file, a
technology, or a document occurs in five scopes is something the store already knows and `observations
where` already reports. It is not, by itself, significance. **A repeated fact is not automatically
significant.**

### No single composite score may become truth

Six distinct things the legacy pipeline conflates are now separately named, and **must not be summed,
averaged, or collapsed into one number**: evidence completeness · rule satisfaction · epistemic confidence
· significance · priority · model probability.

- **Epistemic confidence already exists and is not significance.** The observation substrate's confidence
  is binary (`1.0` determined — *including provably absent*; `0.0` undetermined) and answers *"did I
  determine this?"*, never *"does it matter?"*
- **Significance does not exist, and this ADR does not create it.**
- The first candidate contract carries **no scalar at all** — named booleans and named counts, ordered by
  identity. This removes "ranking via sort order" structurally rather than by policy.
- **No score may promote a candidate, authorize publication, authorize execution, override missing
  evidence, or suppress counterevidence.**

The legacy OSINT scoring stack (`AlertScore`, `correlation_scoring`, `graph_edges.weight`) is
**quarantined, not mapped.** It remains in service for the OSINT track and is not a template for the fact
track. Its defects are recorded in the `LHP-INTEL-02C` report: correlation scoring is strictly
monotonic-additive (**no evidence can ever lower a score**, so disconfirmation is unrepresentable);
`graph_edges.weight` is `REAL NOT NULL` with a default of `1.0` (**silence is the strongest possible
opinion**).

### The fact substrate stays clean

**The observation and fact substrate must remain free of scores, recommendations, hypotheses, findings,
and model-generated claims.** This is currently enforced structurally — by a closed attribute vocabulary,
by a `PRAGMA table_info` test, and by AST name guards — and those guards must survive the significance
layer, not be relaxed to accommodate it.

### LLM boundary

[ADR-0009](0009-no-llm-in-deterministic-core.md) holds. An LLM may **later** *propose* hypotheses. It may
never modify a fact, verify a finding, promote a candidate, or write to the fact tables. Model output is
stored separately, digest-bound, and stamped with provider/model/version. **Model confidence and evidence
confidence are different fields in different tables, and are never summed.**

### Authority boundary

**Lighthouse determines:** *"What intelligence can be extracted from the admitted evidence?"*
**Gateway determines:** *"May a consequential action or external release occur?"*

The Gateway retains consequential approvals, external publication authorization, disclosure-package
approval, operator identity for release, trusted-contact policy, countdown policy, dead-man's-switch
enrollment/cancellation/simulation/release, and release audit history. **Local candidate dismissal is a
Lighthouse concern and does not route through the Gateway.**

The **dead-man's switch remains Gateway-controlled and unbuilt**: disabled by default, explicitly
enrolled, package-specific, simulation-first, fail-safe; unable to release arbitrary database contents,
raw private repositories or notes, or unreviewed findings; limited to operator-approved disclosure
packages; blocked by package-digest mismatch and by policy uncertainty. **Failure or uncertainty must
delay release, never accelerate it.** Nothing in this ADR implements any of it.

### The first detector does not define the platform

The first rule will belong explicitly to **Repository Capability Discovery** or **Private Invention
Discovery**. It **must not be presented as the universal Lighthouse detector.**

**A profile-portability checkpoint is required** before continued specialisation around invention
discovery: prove the shared candidate architecture carries a **non-invention** profile — preferably Public
Scientific Lighthouse or Scientific Preservation — before allowing the invention profile to redefine the
platform.

### The external invention engine is a preserved prototype lineage

The invention-discovery engine is **not the shared platform authority.** It is a preserved prototype whose
concepts may be migrated **selectively, one detector family at a time**, after the shared contracts exist.
The prototype card contract is preserved, **not adopted**. Its composite scoring is quarantined. No
engine code lives in this repository. See the Consequences below.

## Consequences

**The accepted order.** Facts first. Interpretation second. Findings only after evidence and review.

| | Packet | Purpose | Hard boundary |
|---|---|---|---|
| 1 | **LHP-ID-PRESERVE-01** | preserve the external prototype + its provenance | inventory → version control; **no migration** |
| 2 | **LHP-INTEL-03A** | **repository file-content identity** — `content_digest = SHA-256(exact raw bytes)` | **fact layer only**: no candidate, no score, no ranking, no inference, no LLM, no review state, and **no invention terminology in the shared contract** |
| 3 | **LHP-INTEL-03B** | minimal profile-neutral `pattern_candidate.v1` + **one** narrowly-scoped profile-owned deterministic rule + operator disposition surface | no finding promotion, no LLM, no publication, no execution |
| 4 | **profile-portability checkpoint** | prove the candidate architecture carries a non-invention profile | design or code; invention must not be the only citizen |
| 5 | **selective invention migration** | compare the preserved prototype against the accepted contracts; migrate one detector family at a time | quarantine the old scoring; never a bulk copy |

**`LHP-INTEL-03A` is a neutral shared-platform fact, not an invention-only feature.** File-content identity
serves Private Invention Discovery, Repository Capability Discovery, Public Scientific Lighthouse /
Scientific Preservation (mirror verification), and Patent and Research Tracking alike. It joins repository
files to standalone documents on the identity the document layer *already* uses — the digest of the bytes —
so a spec committed in a repository and the same spec sitting in `~/Documents` become **one entity with two
provenances**. Git blob SHA-1 is **rejected** as the canonical cross-source content identity: it can never
join to a document, and it would be a second identifier system.

**`03A` and `03B` must not be compressed into one packet.** Mixing a new factual source with the first
significance contract would make it impossible to prove afterwards whether a rule's behaviour came from
observation or from interpretation.

**Profile governance stays frozen** ([ADR-0018](0018-governance-frozen-pending-runtime-intelligence.md)).
This ADR builds the runtime consumer whose absence justified the freeze; it does **not** build activation,
an activation endpoint, an active-profile store, or a Gateway bridge, and it does not authorise them.
Automatic or signal-triggered activation remains forbidden permanently.

## Repository evidence

- `docs/OBSERVATION_INTAKE.md` — the fact layer this ADR draws the boundary above; its audit records why
  `AlertRecord`/`GraphEdge` were rejected as fact carriers ("neither can hold a fact without asserting an
  opinion about it").
- `modules/observations.py` — the binary confidence contract; a model with **no field for significance**.
- `modules/observation_query.py` — the read boundary that counts and lists but **never ranks**; AST-pinned
  against `_rank_scopes` / `most_common_*`.
- `modules/db.py` — the three fact tables; a `PRAGMA table_info` test proves no column can hold an opinion.
- `modules/repository_intake.py` — records file *paths*, never file *content*: the gap `LHP-INTEL-03A` closes.
- `docs/adr/0018-governance-frozen-pending-runtime-intelligence.md` — the freeze whose resume conditions
  this work bears on.

## Related ADRs

- [ADR-0005](0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) — its premise ("the engine
  exists in the Gateway") is **corrected** by this packet's inventory: the engine existed in **no
  repository at all**. Migration remains deferred; see its implementation-status note.
- [ADR-0008](0008-repository-source-packs-are-a-deterministic-observation-source.md) — **superseded in
  part**: source packs remain one observation source and never a second engine, but they enter the
  **fact-first** pipeline, not the alert/correlation/graph path.
- [ADR-0009](0009-no-llm-in-deterministic-core.md) — upheld and extended to the significance layer.
- [ADR-0018](0018-governance-frozen-pending-runtime-intelligence.md) — unchanged; profile governance stays
  frozen.
- [ADR-0019](0019-local-operator-sources-are-gateway-independent.md) — the local-source class the fact
  layer already consumes.
- [ADR-0001](0001-lighthouse-owns-engine-gateway-is-control-plane.md) — the authority split this ADR
  restates for candidates, hypotheses, findings and release.
