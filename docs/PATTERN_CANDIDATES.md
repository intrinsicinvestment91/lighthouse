# Pattern Candidates

> **Status:** shipped (`LHP-INTEL-03B`). Decision record: [ADR-0021](adr/0021-pattern-candidates-are-review-objects-not-findings.md).
> Architecture: [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md).

This is the layer where Lighthouse stops only *recording* and starts *presenting*. It is therefore the
layer that has to be most careful about what it does not say.

## What a pattern candidate is

> **These direct facts satisfy one declared review rule. An operator should look.**

That is the whole claim. A candidate is a versioned, immutable review object created because facts
already in the store satisfied a deterministic rule that a profile declared in advance.

## What a pattern candidate is not

A candidate does **not** say that anything was copied, that one project derived from another, that an
implementation was independently invented, that content is reusable, important, novel or patentable,
that two repositories share a capability, or that any of it should be published. It is not a
hypothesis, not a finding, and not permission to do anything.

Those are not promises in a docstring. They are structural:

| The claim | What stops it |
|---|---|
| "…and it's important" | The field set is **closed**. There is no `score`, `importance`, `rank`, `severity`, `novelty` or `significance`, and an unknown field is rejected on the way in *and* on the way out — including inside an evidence occurrence. |
| "…and it's confirmed" | Ten authority pins (`is_finding`, `is_verified`, `authorizes_release`, `activates_profile`, `contacts_gateway`, …) are **identity-checked** constants: `is False`, not merely falsy. A `0` will not pass. |
| "…and this one matters most" | Candidates are ordered by **identity**, never by count. Ordering by "recurs in the most repositories" is a ranking, and a ranking is a significance model wearing a sort key. |
| "…and the operator approved it" | Nothing in the codebase **branches** on a disposition. A decision is stored, displayed and filtered — never read to gate behaviour. |

## The three questions, kept apart

| Question | Layer | State |
|---|---|---|
| *What is a fact?* | observations (01A–01E) | done |
| *Where else does it occur?* | cross-scope discovery (02A/02B), content identity (03A) | done |
| *Do these facts satisfy a declared rule?* | **pattern candidate (03B)** | **done** |
| *What might this mean?* | hypothesis | **does not exist** |
| *What is true?* | finding | **does not exist** |
| *May this be released?* | Gateway | out of scope |

A candidate is not a hypothesis. A hypothesis is not a finding. A finding is not permission to
publish or execute. Each arrow is a separate, deliberate step, and none of them are taken here.

## Why facts and candidates stay in separate stores

The fact tables (`observation_sets`, `normalized_entities`, `normalized_relationships`) have **no
column that can hold an opinion** — no `score`, `weight`, `priority`, `importance`, `severity` or
`rank` — and a `PRAGMA table_info` test keeps it that way. Candidates live in their own two tables.
A detector bug therefore cannot corrupt a fact, and re-running a detector cannot rewrite history.

The detector never touches SQLite. It reads through `observation_query` — the same `mode=ro`,
validate-on-read boundary the CLI uses — and receives plain data. The service is the only module that
touches both sides.

## Why detectors are profile-owned

Lighthouse is a **profile-driven platform** (ADR-0020). Private Invention Discovery is *one* profile,
alongside Public Scientific Lighthouse, Scientific Preservation, Patent and Research Tracking,
Repository Capability Discovery, Aerospace/Defense and Fusion.

A repeated fact is not automatically significant, and *what counts as significant depends on what you
are looking for*. So: **shared mechanics stay profile-neutral; rules, evidence requirements and
privacy policy are profile-owned.** The candidate contract knows the closed set of profiles that
exist. It does not know what any of them mean.

**Ownership is not activation.** A candidate carries `profile_id` because a profile *declared the
rule that produced it*. No profile is running. Nothing is activated. Profile activation remains frozen
(ADR-0018), and `activates_profile` is a digest-covered constant `False`.

## The first detector: `repository_exact_content_recurrence`

**Owner:** `repository_capability_discovery`.
**Rule:** a validated `content:<sha256>` entity directly observed in **at least two distinct
repository scopes**.

Why this one first? Because it is the smallest rule that produces a real, checkable result from facts
that already exist, and because its central difficulty *is* the threat model:

> For a solo operator reusing their own boilerplate, the dominant reason two repositories hold
> identical bytes is **not** independent invention. It is a clone, a fork, a template, a vendored
> file, a generated file, or one project that was split in two.

The detector cannot tell those apart — so it says so, on every candidate, rather than picking the
flattering explanation. `source_independence_not_assessed` is mandatory.

Three counting rules do most of the work:

- **One repository is one scope, however many times it was observed.** The store's unique index is
  `(entity_id, source_artifact_digest, derivation_version)`, so a repository re-observed after a
  change is *two rows with one scope id*. Counting rows would call that "recurrence" — and re-observing
  is the normal case, not a corner one.
- **One repository is one scope, however many paths it holds.** Two identical files in one project is
  not recurrence across projects.
- **A repository plus a standalone document is not two repositories.** That fact is real and stays
  fully visible through `lighthouse observations where` — it just does not satisfy a rule owned by a
  repository-scoped profile.

And: **three repositories produce one candidate, not three pairs.** Pairwise candidates would triple
the review burden and imply a relationship between each pair that the facts do not contain.

## Evidence, unknowns, limitations — and counterevidence

Four different things, which must never collapse into one another.

**Evidence** is verbatim. Each occurrence carries the scope, the path (as relationship evidence), the
source artifact digest, the derivation version, the observation timestamp, the sensors, and the
per-observation `confidence` — read straight off the re-validated artifact. Nothing is aggregated.
That `confidence` is **epistemic** (*did I determine this fact?*), never evaluative (*does it
matter?*), and there is no candidate-level confidence at all.

**Unknowns** name what was not assessed. All ten are mandatory:
`authorship_not_assessed` · `independent_creation_not_assessed` · `repository_lineage_not_assessed` ·
`generated_file_status_unknown` · `vendored_file_status_unknown` ·
`template_or_boilerplate_status_unknown` · `semantic_equivalence_not_assessed` ·
`significance_not_assessed` · `novelty_not_assessed` · `source_independence_not_assessed`.

An unknown that can be omitted is an unknown that quietly becomes a *"no"*.

**Limitations** name what the evidence cannot prove — always present, and printed in front of the
operator rather than buried in `--json`.

**Counterevidence** is a *direct contradictory fact*. For this detector the list is **empty**, because
no such fact is available yet. It is never filled with an unknown: dressing *"I did not check"* as
*"I checked and found otherwise"* is precisely the failure this whole track exists to avoid.

## Coverage: two different completeness claims

- **Rule evidence complete** — the occurrences needed to prove exact recurrence are present and valid.
- **Repository inventory complete** — every eligible file in a repository was accounted for under 03A's
  bounds.

**These are not the same statement.** Exact recurrence is provable from two valid occurrences even if
one repository's inventory was incomplete — so an incomplete scope does *not* invalidate a candidate.
It adds the `repository_inventory_incomplete` limitation and marks the occurrence's `coverage_state`.
Incomplete coverage is disclosed; it is never converted into absence, and never presented as complete.

`coverage_state` has **three** values, and they never collapse into two: `complete`, `incomplete`, and
`unknown` (the artifact records no coverage at all — every artifact written before 03A looks like
this). `unknown` is not `complete`.

One further honesty: where a bounded read may have truncated the evidence for an identity, the
detector emits **no candidate** and reports the refusal. A truncated list reads exactly like a complete
one.

## Operator disposition

```bash
lighthouse patterns review --id pc_9f3c1ab2… \
    --operator-id operator \
    --decision needs_more_evidence \
    --reason "Repository lineage has not been assessed."
```

Three decisions, and the vocabulary is closed:

| Decision | Means | Does **not** mean |
|---|---|---|
| `accepted_for_followup` | "This deserves more investigation." | "This is true." |
| `dismissed` | "I don't want to pursue this exact version." | The candidate is deleted. It is not. |
| `needs_more_evidence` | "I can't make a useful decision from what's here." | Anything about whether it matters. |

There is deliberately no `approved`, `verified`, `finding`, `published` or `rejected_as_false`.

**Why `accepted_for_followup` is not a finding.** A finding would be a reviewed, evidence-backed
conclusion under a policy that does not exist yet. A disposition is a note to yourself. And it cannot
grow into authority by accident, because **nothing reads it to decide anything** — a structural test
asserts that no code anywhere compares against a decision value.

**One disposition per candidate version.** It binds to the candidate's `artifact_digest`, so a decision
always names the exact evidence it was made against. A second decision on the same version fails
closed — enforced by a `UNIQUE` index, not by a service check that somebody could forget to call. When
the evidence changes, the detector produces a **new version** (same `candidate_key`, new
`candidate_id`), which may receive its own decision. The old candidate and its decision are never
touched.

Reopening and supersession are **not implemented**, deliberately. Recorded as a limitation.

**`operator_identity_authenticated` is `false`, always.** A CLI flag is a label, not an identity, and
pinning the denial is more honest than omitting the field.

## Why the Gateway is not involved

The boundary (ADR-0019): **Lighthouse** answers *"what facts and intelligence can be extracted from
admitted evidence?"* The **Gateway** answers *"may a consequential action or external release occur?"*

Reviewing a local candidate on your own machine is neither consequential nor a release. Routing local
dismissals through an approval system would make the Gateway a bottleneck on thinking, and would
teach the system that a local note is a kind of authorization. It is not. `contacts_gateway` is a
constant `False`.

## Why the invention engine is not being migrated

The preserved invention-discovery prototype is valuable prior work and is **preserved privately, not
adopted**. Its composite scoring and card contract are not the shared significance authority, and a
bulk copy would import exactly the conclusions this contract exists to withhold. No engine code lives
in this repository.

Migration happens **one detector family at a time**, after the portability checkpoint below, with the
old scoring quarantined.

## The next checkpoint: prove this carries a non-invention profile

Before any further invention-oriented detector, `PatternCandidate` v1 must be shown to support the
original **Public Scientific Lighthouse** and **Scientific Preservation** missions. The preferred
first citizen is the **Disappearing Scientific Source Candidate**:

> a previously observed public source becomes unavailable · an exact archived copy exists or does not ·
> papers, patents or repositories still reference it · byte identity changes · mirrors exist or are
> missing · preservation state remains unknown.

That candidate must be able to say *"this public scientific artifact may be at risk of disappearance"*
— and must **not** automatically say *"this work is scientifically important."*

If one missing neutral **fact** blocks it, that fact gets built before any detector does. Same
ordering as 03A → 03B, for the same reason.

**Invention must not be the only citizen this contract can hold.**

## Update — the portability checkpoint resolved (LHP-INTEL-04A → 04B → 04C)

The portability checkpoint above was executed as **LHP-INTEL-04A** (accepted). Its finding: `pattern_candidate.v1` is portable in its neutral spine, but Scientific Preservation is blocked on a missing **fact** — the store could not record that Lighthouse *looked* at a source. **LHP-INTEL-04B** (shipped) adds that fact: an append-only public-source check ledger (`lighthouse public-sources`; ADR-0022) — network facts only, **no candidate**. **LHP-INTEL-04C** (next) will add the first non-invention detector, `disappearing_scientific_source`, owned by `scientific_preservation`, over those facts — reusing this contract's neutral spine through one bounded generalization (detector-owned unknowns/limitations/evidence), leaving repository candidates byte-identical. A candidate is still not a hypothesis, and a hypothesis is still not a finding.

## A second detector, one neutral spine (LHP-INTEL-04C)

Since LHP-INTEL-04C, `pattern_candidate.v1` carries **two** detectors — the repository recurrence rule
above and the Scientific Preservation `disappearing_scientific_source` rule (see
[`SCIENTIFIC_PRESERVATION_CANDIDATES.md`](SCIENTIFIC_PRESERVATION_CANDIDATES.md)) — through the same
profile-neutral spine. The generalization is a **closed, code-owned detector-spec registry**
(`modules/pattern_candidate_specs.py`, [ADR-0023](adr/0023-detector-owned-candidate-specifications.md)):
each `detector_id` maps to one frozen `DetectorCandidateSpec` that owns that detector's evidence shape,
unknown/limitation vocabularies, and candidate-key material. A candidate cannot supply its own validator,
an unknown detector fails closed, and — proven by golden fixtures — **existing repository candidates are
byte-identical** (still schema version 1, no v2). Pick the detector with `patterns detect --detector …`;
the default stays `repository_exact_content_recurrence`.

## Commands

```bash
# Run the one declared rule over stored facts. --dry-run writes NOTHING — not a row, not a
# table, not the database file.
lighthouse patterns detect --dry-run --json
lighthouse patterns detect

# The latest version of each candidate, with its decision if it has one.
lighthouse patterns list
lighthouse patterns list --all-versions --json
lighthouse patterns list --disposition needs_more_evidence

# One candidate in full: evidence, provenance, unknowns, limitations, decision.
# Takes the full pc_<sha256> or an unambiguous >=8-char hex prefix.
lighthouse patterns show --id 9f3c1ab2

# One local decision about one exact candidate version.
lighthouse patterns review --id 9f3c1ab2 --operator-id operator \
    --decision needs_more_evidence --reason "Repository lineage has not been assessed."
```

## Storage

| Table | Shape |
|---|---|
| `pattern_candidates` | `candidate_id` (PK, = `pc_` + artifact digest) · `candidate_key` · `schema_version` · `profile_id` · `detector_id` · `detector_version` · `candidate_type` · `artifact_digest` (UNIQUE) · `created_at` · `candidate_json` |
| `pattern_candidate_dispositions` | `disposition_id` (PK) · `candidate_id` (**UNIQUE**) · `candidate_artifact_digest` · `schema_version` · `decision` · `operator_id` · `reason` · `review_method` · `operator_identity_authenticated` · `artifact_digest` (UNIQUE) · `created_at` · `disposition_json` |

**Insert-only.** No `UPDATE`, no `DELETE`, and no `update_*`/`delete_*` function exists in `db.py` for
either table. **Lazily created** — on the first insert, never on a read — so a dry run, an empty store
and a machine that has never detected anything all leave no database behind. **Validated on read**:
every stored artifact is round-tripped through its contract, which recomputes the digest *and* enforces
the closed field set, so a forged evaluative field injected by raw SQL is refused on the way **out** —
with or without a re-stamped digest.

Every index backs a named operator query, verified with `EXPLAIN QUERY PLAN`. (An index on
`decision` was written, found to back no query, and removed.)
