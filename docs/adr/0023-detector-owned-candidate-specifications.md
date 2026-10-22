# ADR-0023 — Detector-owned candidate specifications preserve a profile-neutral PatternCandidate

- **Status:** Accepted
- **Date:** 2026-07-15
- **Packet:** LHP-INTEL-04C
- **Supersedes:** —
- **Superseded-by:** —

## Context

`pattern_candidate.v1` (LHP-INTEL-03B, ADR-0021) was shaped for exactly one detector: its `validate()`
inlined the repository recurrence detector's evidence shape, unknown vocabulary, limitation vocabulary,
and candidate-key material. The LHP-INTEL-04A portability review found that the neutral spine — identity,
digest, versioning, the ten identity-checked authority pins, the no-score guarantee, the disposition
loop, insert-only persistence, the `content:<sha256>` subject — carries across profiles unchanged, but
those four detector-specific things do not. Making the contract carry a **second** detector (the
Scientific Preservation disappearance rule) required generalizing them **without letting a candidate own
them** — a candidate that could declare its own validator or vocabulary would be a hole exactly where the
no-significance guarantee lives.

This ADR records how that generalization was done, and the invariants that keep an existing repository
candidate byte-for-byte what it was.

## Decision

**1. PatternCandidate v1's spine stays profile-neutral.** Identity (`candidate_id`/`candidate_key`),
the digest recipe, versioning, the authority pins, the no-score guarantee, and the disposition loop are
unchanged and shared by every detector.

**2. Detector-specific parts become detector-owned through a closed, code-owned registry.**
`modules/pattern_candidate_specs.py` maps one exact `detector_id` to one frozen `DetectorCandidateSpec`
that owns the detector's evidence validator, unknown/limitation vocabularies, candidate-key material, and
serialized extra fields. `pattern_candidate.validate()` calls `spec.validate_detector(candidate)` after
its own spine checks; `_raw()` calls `spec.serialize_extra`; `compute_key()` calls `spec.key_material`;
`from_dict()` closes the field set against `SPINE_FIELDS | spec.extra_fields`.

**3. A candidate cannot define its own validator.** The registry is built in code — no dynamic import,
no configuration-file registry, no plugin loading, no model-generated schema, and no callable supplied by
candidate data. The input names a detector; the code decides what that detector's candidates may look like.

**4. Unknown detector ids fail closed.** `get_spec` refuses an unknown id, and `validate()` additionally
requires the candidate's `profile_id` and `candidate_type` to be the ones its detector's spec owns — so a
valid body cannot be relabelled under another detector.

**5. Existing repository candidates remain byte-identical.** Both the digest (`observations.canonical_digest`)
and the stored `candidate_json` (`db.insert_pattern_candidate`) serialize with `json.dumps(..., sort_keys=True)`,
so byte-compatibility reduces to "same key set, same values"; field order is irrelevant. The repository
spec reproduces the pre-04C validation and serialization verbatim. Golden fixtures pinned from the pre-04C
committed code (a two-scope and a three-scope candidate) prove the `candidate_id`, `candidate_key`,
`artifact_digest`, and canonical JSON are unchanged.

**6. PatternCandidate v1 remains the schema version.** `PATTERN_CANDIDATE_SCHEMA_VERSION` and
`DETERMINISTIC_SERIALIZATION_VERSION` stay `1`. No v2 was introduced; had the golden fixtures shown any
repository output change, the packet's instruction was to stop and report, not to bump a version.

**7. `scientific_source_disappearance_review` is owned by `scientific_preservation`.** Candidate type
`disappearing_scientific_source`; the profile is verified and validated at detection, never activated
(ADR-0018 stands — a candidate carries `profile_id` because a profile declared the rule).

**8. Exact prior content identity is the subject.** The subject is the `content:<sha256>` the locator last
served successfully — the same neutral identity every detector uses.

**9. Locator identity is separate, and enters the candidate key.** The scientific candidate is keyed on
`{profile, detector, type, locator_id, subject}`; the repository key material is unchanged. Two locators
serving the same bytes are two separate preservation risks and do not collapse into one candidate.

**10. `not_found` means only HTTP 404 or 410**, read from the 04B ledger. Nothing else triggers.

**11. `not_found` does not prove permanent disappearance.** It is an answer, not a verdict; the mandatory
unknowns and limitations say so on every candidate.

**12. Seven days is a declared v1 rule parameter**, a conservative choice, not a scientific truth.

**13. The broad-outage defense is deterministic and non-scoring.** Over the triggering run, if the other
locators are mostly infrastructure failures (`infra*3 >= unrelated*2`, exact integer arithmetic), the
candidate is blocked; where the context is too small to judge (`<3` unrelated), that is disclosed as an
unknown, never hidden.

**14. Known public mirrors and stored local copies are direct counterevidence.** They change the
candidate's *meaning* ("the original locator may be unavailable, while exact copies are known elsewhere"),
not its existence, and are never called independent or authoritative.

**15. Missing mirror evidence is not absence proof.** The uncertainty lives in the unknowns and
limitations; the system never claims "no copy exists" or "only known copy".

**16. Recovery produces no current candidate and mutates no history.** A later `available` check means the
locator's latest completed check is no longer `not_found`, so the current run yields no candidate; any
prior version and its disposition stay byte-identical (03B versioning already expresses this).

**17. A candidate is not a hypothesis.** **18. A candidate is not a finding.** **19. A candidate grants no
preservation, publication, disclosure, release, Gateway, or dead-man's-switch authority** — every pin is a
digest-covered, identity-checked `False`.

**20. Profile activation remains frozen (ADR-0018).** Owning a rule is not running a profile; this packet
builds no activation, no active-profile store, and no Gateway bridge.

## Consequences

`pattern_candidate.v1` now carries two profiles' detectors through one neutral spine, proving the
candidate architecture is neither invention-specific nor repository-specific. The repository detector is
provably unchanged; the scientific detector reads only validated 04B ledger facts, performs no network,
and produces one conservative review object. Each future detector still requires its own registered
specification, direct facts, closed evidence, closed unknowns and limitations, and a deterministic rule —
the registry generalizes the *mechanism*, not the *bar*.

## Repository evidence

- `modules/pattern_candidate_specs.py` — `DetectorCandidateSpec`, `REGISTRY`, `get_spec`, `REPOSITORY_SPEC`, `SCIENTIFIC_SPEC`.
- `modules/pattern_candidate.py` — `SPINE_FIELDS`/`DETECTOR_OWNED_FIELDS`, `_get_spec`, spec-routed `validate()`/`from_dict()`/`_raw()`/`compute_key()`, `finalize_candidate`.
- `modules/scientific_source_disappearance.py` — the pure rule, `EVIDENCE_SECTION_KEYS`, `INFRASTRUCTURE_FAILURE_OUTCOMES`, `REQUIRED_UNKNOWNS`/`REQUIRED_LIMITATIONS`.
- `modules/pattern_candidate_service.py` — `run_detection(detector=...)`, `_run_scientific_detection`, `_collect_public_source_input` (validate-on-read), `ScientificDetectionRun`.
- `modules/db.py` — `fetch_public_source_locator_index` (read-only bounded enumeration).
- `docs/schemas/pattern_candidate.v1.schema.json` — the discriminated-union mirror.
- `tests/unit/test_pattern_candidate_specs.py` — byte-compat goldens, registry, cross-detector refusal.
- `docs/SCIENTIFIC_PRESERVATION_CANDIDATES.md` — the operator/architecture guide.
