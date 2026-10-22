# ADR-0021 — Pattern candidates are profile-owned review objects, not findings

- **Status:** Accepted
- **Date:** 2026-07-14
- **Packet:** LHP-INTEL-03B
- **Supersedes:** —
- **Superseded-by:** —

## Context

ADR-0020 fixed the architecture: a **profile-neutral substrate** with **profile-owned detectors**, and
an ordering — *facts first, interpretation second, findings only after evidence and review*. It also
recorded why the first detector could not be built yet: the store recorded file *paths*, never file
*content*, so Lighthouse could not distinguish *"two projects independently implement this"* from
*"I copied my Dockerfile from the last project"*.

`LHP-INTEL-03A` removed that blocker by adding `content:<sha256>` — the exact raw bytes of a tracked
repository file, joinable across scopes. This ADR records what was built on top of it, and — more
importantly — the boundaries that keep it from becoming something else.

The hazard is specific and it is not hypothetical. This is the **first** layer in Lighthouse that
presents an operator with something other than a raw fact. Every previous layer was structurally
unable to have an opinion. From here on, the system *selects* — and a system that selects is one
careless sort key away from a system that ranks, and one careless field away from a system that
concludes.

## Decision

**1. Candidate mechanics are profile-neutral.** `modules/pattern_candidate.py` contains no invention
vocabulary, no repository vocabulary, and no word for significance. It knows the closed set of
profiles, detectors and candidate types that exist; it does not know what any of them *mean*.

**2. Detector rules are profile-owned.** A rule belongs to a profile, and the candidate carries
`profile_id` to say which one declared it. Rules, evidence requirements, counterevidence requirements
and privacy policy are profile-owned; the contract, the identity scheme, the persistence and the
review loop are shared.

**3. The first detector belongs to Repository Capability Discovery.**
`repository_exact_content_recurrence` (`modules/repository_content_recurrence.py`) — *a validated
`content:<sha256>` entity directly observed in at least two distinct repository scopes*. It is one
profile's rule, not Lighthouse's definition of significance, and **not** an invention detector.

**4. Exact recurrence proves only recurrence.** It does not prove copying, independent
implementation, shared capability, novelty, or importance. Two repositories holding identical bytes
may be clones, forks, templates, generated scaffolds, mirrors, vendored copies, one monorepo split in
two — or genuinely independent. **The detector chooses among none of them**, and
`source_independence_not_assessed` is a mandatory unknown on every candidate it emits. Scope count is
never read as an independence count.

**5. A PatternCandidate is not a Hypothesis.** A candidate is *"these direct facts satisfy one
declared review rule"*. A hypothesis would be a falsifiable interpretation proposed from candidates.
No hypothesis layer exists, and none is implied.

**6. A Hypothesis is not a Finding.** A finding would be a reviewed, evidence-backed conclusion
satisfying a future finding policy. No finding layer exists.

**7. A Finding is not publication or action authority.** Release remains Gateway-governed, and this
packet does not touch it.

**8. Candidate artifacts are immutable.** `candidate_id` **is** the `artifact_digest` (with a `pc_`
prefix), computed over everything except `candidate_id`, `created_at` and the digest itself. Identical
facts reproduce an identical candidate — so storage is idempotent — and any change to the evidence
produces a **new version**, never an edit. An operator who reviewed a version can trust it is still
the thing they reviewed.

**9. Dispositions are append-only and version-specific.** A disposition binds to the candidate's
`artifact_digest`, so a decision always names the exact evidence it was made against. **Exactly one
disposition per candidate version**, enforced by a `UNIQUE` index in the database — not by a
service-side check alone, because a rule only the writer honours is not a rule. Reopening and
supersession are deliberately not implemented, and are recorded as a known limitation.

**10. Local review does not involve the Gateway.** Reviewing a local intelligence candidate is not a
consequential action or an external release. The Gateway governs *"may a consequential action or
external release occur?"*; Lighthouse governs *"what facts can be extracted from admitted evidence?"*
`contacts_gateway` is a digest-covered constant `False`.

**11. Candidate-level scores are prohibited in v1.** No `score`, `importance`, `priority`, `severity`,
`relevance`, `rank`, `novelty`, `significance`, `similarity`, `probability`, or tier. **And no
candidate-level confidence scalar** — an entity is built only from *determined* observations, so an
aggregate confidence could only ever be the derived constant `1.0`: a number asserting a variability
that cannot exist, in exactly the slot where significance hides. The per-observation `confidence`
survives verbatim inside the evidence, where it is **epistemic** (*was this fact determined?*) and not
evaluative (*does it matter?*). `distinct_repository_scope_count` is a neutral count and is never a
sort key.

**12. Counterevidence, unknowns and limitations are three different things, and never collapse.**
Counterevidence is a *direct contradictory fact* — it is **empty** for this detector, because none is
available, and it is never filled with an unknown (which would dress *"I did not check"* as *"I
checked and found otherwise"*). Unknowns name what was not assessed. Limitations name what the
evidence cannot prove. All three are mandatory and closed.

**13. Source independence is not inferred.** See (4). Neither more scopes, nor more paths, nor more
sensors are more *sources*.

**14. Profile activation remains frozen.** This packet gives a profile its first runtime *consumer*,
and still activates nothing. Carrying `profile_id` proves **ownership** — who declared the rule — not
runtime activation. No active-profile record is written, no activation endpoint exists, no Gateway
bridge is built, and the `repository_capability_discovery` seed is read but never modified.
ADR-0018 stands.

**15. The multi-profile portability checkpoint remains mandatory.** Before any further
invention-oriented detector, `PatternCandidate` v1 must be shown to carry a **non-invention** profile
— Public Scientific Lighthouse or Scientific Preservation, with *Disappearing Scientific Source
Candidate* the preferred first citizen. Invention must not be the only thing this contract can
express. **Do not bulk-migrate the preserved invention engine before that checkpoint.**

## Consequences

**What became possible.** An operator can run one deterministic rule over stored facts, receive
immutable candidates, inspect the direct evidence with its provenance intact, read what the evidence
cannot prove, and record one local decision — all offline, with no model, no network, and no Gateway.

**What stayed impossible, structurally rather than by policy.** The candidate has a **closed field
set**, so a score has nowhere to live — including *inside* an evidence occurrence, which is copied
verbatim from the store and would otherwise be an unwatched door. The authority pins are
**identity-checked** (`is False`, not falsy), so neither a value edit nor a `0` turns a candidate into
a verdict. And **nothing in the codebase branches on a disposition decision** — a decision is stored,
displayed and filtered, never read to gate behaviour — so `accepted_for_followup` cannot quietly
become an approval.

**A guarantee that had to be traded honestly.** Where the evidence for a content identity is not
provably complete — because a bounded read may have truncated it — the detector emits **no candidate**
and reports the refusal. A truncated list reads exactly like a complete one, and a candidate built on
a partial view would undercount its scopes while claiming to carry all the evidence. That is 03A's own
rule, applied one layer up.

**Known limitations, recorded rather than hidden.** Evidence reflects *stored observations*, not the
current filesystem: the store is insert-only and idempotent, so it can express *"last changed"* but
not *"last looked"*. A repository observed once and never re-observed still reads as current, and a
deleted repository's facts still stand. Every candidate carries
`evidence_reflects_stored_observations_not_current_state` to say so. A run ledger would fix this; it
is named and deferred, not faked.

## Evidence

- `modules/pattern_candidate.py` — the contract; `validate()` recomputes both identities and enforces
  every closed vocabulary. Pure: no DB, no filesystem, no network, no clock, no LLM (AST-pinned).
- `modules/repository_content_recurrence.py` — the rule. `_distinct_repository_scopes()` returns a
  **set of scope ids**; the store's unique index is `(entity_id, source_artifact_digest,
  derivation_version)`, so a repository re-observed after a change is two rows with one scope id.
- `modules/pattern_candidate_service.py` — the boundary. Facts in through `observation_query`
  (`mode=ro`, validate-on-read); candidates out through `db.py` (insert-only, lazily created).
- `modules/db.py` — `pattern_candidates`, `pattern_candidate_dispositions`. No `UPDATE`, no `DELETE`,
  no `update_*`/`delete_*` function. `UNIQUE(candidate_id)` on dispositions is the one-per-version gate.
- `docs/schemas/pattern_candidate.v1.schema.json`, `docs/schemas/pattern_candidate_disposition.v1.schema.json`
  — reference mirrors; the dataclass `validate()` remains authoritative, and a stdlib drift test pins
  the two together.
- `docs/PATTERN_CANDIDATES.md` — the canonical guide.
