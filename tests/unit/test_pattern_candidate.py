"""The pattern candidate contract (LHP-INTEL-03B).

A candidate says *"these direct facts satisfy one declared review rule"*. It must never be able to
say *"and therefore this matters"* — so the tests below spend most of their effort proving that the
significance-shaped statement is **unrepresentable**, not merely absent: the field set is closed,
the authority pins are identity-checked constants, and every digest is recomputed on the way in
*and* on the way out.

The lesson those choices come from is 03A's: a vocabulary that only the writer honours is not a
vocabulary. Where a test forges a record it also **re-stamps the digest**, so a digest mismatch can
never be what makes the assertion pass.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from modules import pattern_candidate as pc
from modules.pattern_candidate import (
    PatternCandidate,
    PatternCandidateError,
    PatternCandidateValidationError,
    PatternDisposition,
    build_pattern_candidate,
    build_pattern_disposition,
)

# -- fixtures ------------------------------------------------------------------------------

CONTENT_A = "content:" + hashlib.sha256(b"alpha bytes\n").hexdigest()
CONTENT_B = "content:" + hashlib.sha256(b"beta bytes\n").hexdigest()

SCOPE_A = hashlib.sha256(b"/repos/a").hexdigest()
SCOPE_B = hashlib.sha256(b"/repos/b").hexdigest()
SCOPE_C = hashlib.sha256(b"/repos/c").hexdigest()
SCOPE_DOC = hashlib.sha256(b"/docs/spec.pdf").hexdigest()

ARTIFACT_A = hashlib.sha256(b"artifact-a").hexdigest()
ARTIFACT_B = hashlib.sha256(b"artifact-b").hexdigest()

PROFILE = "repository_capability_discovery"
DETECTOR = "repository_exact_content_recurrence"
DETECTOR_VERSION = "repository_exact_content_recurrence/1"
RULE = "exact_content_recurrence_across_repository_scopes"
CREATED_AT = "2026-07-14T12:00:00+00:00"


def occurrence(
    *,
    scope_id=SCOPE_A,
    scope_type="repository",
    artifact=ARTIFACT_A,
    path="Dockerfile",
    entity_id=CONTENT_A,
    coverage_state="complete",
    scope_label="alpha",
):
    """One occurrence, shaped exactly as `observation_query.entity_occurrences()` returns it."""
    digest = entity_id.split(":", 1)[1]
    return {
        "scope_id": scope_id,
        "scope_type": scope_type,
        "scope_label": scope_label,
        "scope_path": f"/repos/{scope_label}",
        "value": digest,
        "entity_digest": hashlib.sha256(f"{entity_id}{artifact}".encode()).hexdigest(),
        "source_artifact_digest": artifact,
        "derivation_version": "observation_normalizer/2",
        "observed_at": "2026-07-14T11:00:00+00:00",
        "sensors": ["filesystem"],
        "supporting_observations": [
            {
                "observation_type": "content.files",
                "sensor": "filesystem",
                "confidence": 1.0,
                "observed_at": "2026-07-14T11:00:00+00:00",
            }
        ],
        "relationships": [
            {
                "relationship_type": "file_has_content",
                "subject_id": f"file:{scope_id}:{path}",
                "object_id": entity_id,
                "relationship_id": hashlib.sha256(f"{scope_id}{path}".encode()).hexdigest(),
                "relationship_digest": hashlib.sha256(f"rel{scope_id}{path}".encode()).hexdigest(),
                "source_artifact_digest": artifact,
                "derivation_version": "observation_normalizer/2",
            }
        ],
        "coverage_state": coverage_state,
    }


def candidate(occurrences=None, *, created_at=CREATED_AT, subject=CONTENT_A, **kwargs):
    if occurrences is None:
        occurrences = [
            occurrence(scope_id=SCOPE_A, scope_label="alpha"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta"),
        ]
    return build_pattern_candidate(
        profile_id=kwargs.pop("profile_id", PROFILE),
        detector_id=kwargs.pop("detector_id", DETECTOR),
        detector_version=kwargs.pop("detector_version", DETECTOR_VERSION),
        candidate_type=kwargs.pop("candidate_type", DETECTOR),
        subject_entity_id=subject,
        occurrences=occurrences,
        rule_id=kwargs.pop("rule_id", RULE),
        rule_version=kwargs.pop("rule_version", 1),
        rule_parameters=kwargs.pop("rule_parameters", {"min_distinct_repository_scopes": 2}),
        created_at=created_at,
        **kwargs,
    )


def forge(record: PatternCandidate, **overrides) -> dict:
    """A tampered candidate whose digest is RE-STAMPED, so the digest can never be the thing that
    catches it. Whatever refuses the record has to be a real guard."""
    raw = record.to_dict()
    raw.update(overrides)
    content = {k: v for k, v in raw.items() if k not in pc.VOLATILE_FIELDS}
    raw["artifact_digest"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw["candidate_id"] = pc.CANDIDATE_ID_PREFIX + raw["artifact_digest"]
    return raw


# -- schema --------------------------------------------------------------------------------


def test_a_well_formed_candidate_validates():
    record = candidate()
    record.validate()
    assert record.candidate_state == "ready_for_review"
    assert record.distinct_repository_scope_count == 2


def test_unknown_schema_version_is_rejected():
    for version in (0, 2, 99, "1", None):
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), schema_version=version)).validate()


def test_an_unknown_field_is_rejected():
    # The closed field set is the ONLY thing standing between the store and a smuggled opinion.
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), operator_note="looks important"))


def test_malformed_candidate_id_is_rejected():
    for bad in ("", "pc_", "deadbeef", "pc_" + "z" * 64, "pk_" + "a" * 64):
        raw = candidate().to_dict()
        raw["candidate_id"] = bad
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(raw).validate()


def test_malformed_candidate_key_is_rejected():
    for bad in ("", "pk_", "pc_" + "a" * 64, "pk_" + "a" * 63):
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), candidate_key=bad)).validate()


def test_malformed_artifact_digest_is_rejected():
    raw = candidate().to_dict()
    raw["artifact_digest"] = "not-a-digest"
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw).validate()


def test_unsupported_digest_algorithm_is_rejected():
    # sha1 would make a candidate's identity incomparable with every other identity in the system.
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), digest_algorithm="sha1")).validate()


def test_unsupported_candidate_state_is_rejected():
    for state in ("verified", "approved", "finding", "published", "executing", "completed"):
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), candidate_state=state)).validate()


def test_ready_for_review_is_the_only_state_that_exists():
    assert {"ready_for_review"} == pc.ALLOWED_CANDIDATE_STATES


def test_unsupported_candidate_type_is_rejected():
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), candidate_type="invention")).validate()


def test_unsupported_detector_is_rejected():
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), detector_id="my_detector")).validate()


def test_unsupported_profile_id_is_rejected():
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), profile_id="whatever_i_like")).validate()


def test_the_recognized_profiles_do_not_drift_from_the_bundled_seeds():
    """The contract's closed profile vocabulary must be exactly what the registry ships.

    The contract is pure (it reads no seed file), so the two lists could silently diverge. This is
    the pin that stops them — and it is the only place the two are compared.
    """
    from modules import profile_registry

    bundled = {profile.profile_id for profile in profile_registry.list_bundled_profiles()}
    assert bundled == pc.ALLOWED_PROFILE_IDS


# -- identity ------------------------------------------------------------------------------


def test_same_evidence_produces_the_same_candidate_id():
    assert candidate().candidate_id == candidate().candidate_id


def test_created_at_alone_does_not_change_the_candidate_id():
    # This is what makes a re-run idempotent: the same facts must reproduce the same row.
    early = candidate(created_at="2026-07-14T00:00:00+00:00")
    late = candidate(created_at="2026-09-01T23:59:59+00:00")
    assert early.candidate_id == late.candidate_id
    assert early.artifact_digest == late.artifact_digest


def test_occurrence_order_does_not_change_the_candidate_id():
    a = occurrence(scope_id=SCOPE_A, scope_label="alpha")
    b = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta")
    assert candidate([a, b]).candidate_id == candidate([b, a]).candidate_id


def test_adding_a_repository_occurrence_changes_the_candidate_id():
    two = candidate()
    three = candidate(
        [
            occurrence(scope_id=SCOPE_A, scope_label="alpha"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta"),
            occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_B, scope_label="gamma"),
        ]
    )
    assert two.candidate_id != three.candidate_id
    assert two.candidate_key == three.candidate_key  # same subject: one logical candidate


def test_removing_an_occurrence_changes_the_candidate_id():
    three = candidate(
        [
            occurrence(scope_id=SCOPE_A, scope_label="alpha"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta"),
            occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_B, scope_label="gamma"),
        ]
    )
    assert three.candidate_id != candidate().candidate_id


def test_changing_evidence_provenance_changes_the_candidate_id():
    baseline = candidate()
    moved = candidate(
        [
            occurrence(scope_id=SCOPE_A, artifact=ARTIFACT_B, scope_label="alpha"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta"),
        ]
    )
    assert baseline.candidate_id != moved.candidate_id


def test_detector_version_change_changes_the_candidate_id():
    assert (
        candidate().candidate_id
        != candidate(detector_version="repository_exact_content_recurrence/2").candidate_id
    )


def test_the_candidate_key_groups_versions_of_one_subject():
    two = candidate()
    three = candidate(
        [
            occurrence(scope_id=SCOPE_A, scope_label="alpha"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta"),
            occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_B, scope_label="gamma"),
        ]
    )
    assert two.candidate_key == three.candidate_key
    assert two.candidate_id != three.candidate_id


def test_a_different_content_subject_produces_a_different_candidate_key():
    other = candidate(
        [
            occurrence(scope_id=SCOPE_A, entity_id=CONTENT_B, scope_label="alpha"),
            occurrence(
                scope_id=SCOPE_B, entity_id=CONTENT_B, artifact=ARTIFACT_B, scope_label="beta"
            ),
        ],
        subject=CONTENT_B,
    )
    assert candidate().candidate_key != other.candidate_key


def test_the_candidate_id_is_the_artifact_digest_and_cannot_be_forged():
    record = candidate()
    assert record.candidate_id == pc.CANDIDATE_ID_PREFIX + record.artifact_digest

    raw = record.to_dict()
    raw["candidate_id"] = pc.CANDIDATE_ID_PREFIX + "b" * 64
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw).validate()


def test_the_candidate_key_is_recomputed_on_validate():
    # A key that does not follow from (profile, detector, type, subject) would let two unrelated
    # subjects masquerade as versions of one another.
    raw = forge(candidate(), candidate_key=pc.CANDIDATE_KEY_PREFIX + "c" * 64)
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw).validate()


# -- evidence ------------------------------------------------------------------------------


def test_fewer_than_two_distinct_repository_scopes_is_rejected():
    with pytest.raises(PatternCandidateError):
        candidate([occurrence(scope_id=SCOPE_A)])


def test_two_paths_in_one_repository_do_not_satisfy_the_rule():
    """The same bytes at two paths in ONE repository is one scope, and one scope is not recurrence.

    Both occurrences are legitimate rows — 03A dedupes a `content:` entity per artifact, so this is
    the shape a repository with two identical files actually produces.
    """
    with pytest.raises(PatternCandidateError):
        candidate(
            [
                occurrence(scope_id=SCOPE_A, path="Dockerfile"),
                occurrence(scope_id=SCOPE_A, path="build/Dockerfile"),
            ]
        )


def test_duplicate_occurrence_rows_do_not_inflate_the_scope_count():
    """One repository re-observed after a change is TWO entity rows with ONE scope_id.

    The unique index is (entity_id, source_artifact_digest, derivation_version) — so this is the
    normal case, not a corner one. Counting rows instead of scopes would call it recurrence.
    """
    with pytest.raises(PatternCandidateError):
        candidate(
            [
                occurrence(scope_id=SCOPE_A, artifact=ARTIFACT_A),
                occurrence(scope_id=SCOPE_A, artifact=ARTIFACT_B),
            ]
        )


def test_a_repository_and_a_document_do_not_satisfy_this_detector():
    # The document occurrence is a real fact and stays visible through `observations where`. It just
    # does not satisfy a rule owned by a repository-scoped profile.
    with pytest.raises(PatternCandidateError):
        candidate(
            [
                occurrence(scope_id=SCOPE_A, scope_type="repository"),
                occurrence(scope_id=SCOPE_DOC, scope_type="document", scope_label="spec.pdf"),
            ]
        )


def test_the_scope_count_is_recomputed_on_validate():
    raw = forge(candidate(), distinct_repository_scope_count=7)
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw).validate()


def test_evidence_occurrences_preserve_complete_provenance():
    record = candidate()
    stored = record.to_dict()["supporting_occurrences"][0]
    for field in (
        "scope_id",
        "scope_type",
        "scope_label",
        "scope_path",
        "value",
        "entity_digest",
        "source_artifact_digest",
        "derivation_version",
        "observed_at",
        "sensors",
        "supporting_observations",
        "relationships",
        "coverage_state",
    ):
        assert field in stored, field
    assert stored["supporting_observations"][0]["confidence"] == 1.0
    assert stored["relationships"][0]["subject_id"].startswith("file:")


def test_an_occurrence_carrying_an_unknown_key_is_rejected():
    """Evidence is copied verbatim — so its SHAPE has to be closed, or an evaluative field could
    ride into the candidate inside an occurrence nobody inspected."""
    bad = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B)
    bad["importance"] = 0.9
    with pytest.raises(PatternCandidateError):
        candidate([occurrence(scope_id=SCOPE_A), bad])


def test_an_occurrence_missing_provenance_is_rejected():
    for missing in ("source_artifact_digest", "observed_at", "sensors", "scope_id"):
        bad = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B)
        del bad[missing]
        with pytest.raises(PatternCandidateError):
            candidate([occurrence(scope_id=SCOPE_A), bad])


def test_a_malformed_content_identity_is_rejected():
    for bad in (
        "content:short",
        "document:" + "a" * 64,
        "content:" + "Z" * 64,
        "technology:docker",
    ):
        with pytest.raises(PatternCandidateError):
            candidate(
                [
                    occurrence(scope_id=SCOPE_A, entity_id=bad),
                    occurrence(scope_id=SCOPE_B, entity_id=bad, artifact=ARTIFACT_B),
                ],
                subject=bad,
            )


def test_an_occurrence_of_a_different_entity_is_rejected():
    # Every occurrence must be evidence for THIS candidate's subject.
    with pytest.raises(PatternCandidateError):
        candidate(
            [
                occurrence(scope_id=SCOPE_A, entity_id=CONTENT_A),
                occurrence(scope_id=SCOPE_B, entity_id=CONTENT_B, artifact=ARTIFACT_B),
            ]
        )


def test_direct_evidence_is_sorted_deterministically():
    a = occurrence(scope_id=SCOPE_A, scope_label="alpha")
    b = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta")
    forward = candidate([a, b]).to_dict()["supporting_occurrences"]
    reverse = candidate([b, a]).to_dict()["supporting_occurrences"]
    assert forward == reverse
    keys = [(o["scope_id"], o["source_artifact_digest"], o["derivation_version"]) for o in forward]
    assert keys == sorted(keys)


# -- coverage ------------------------------------------------------------------------------


def test_incomplete_repository_coverage_is_preserved_as_a_limitation():
    """Exact recurrence is provable from two valid occurrences even where a repository's inventory
    is incomplete. The candidate stands — and says so."""
    record = candidate(
        [
            occurrence(scope_id=SCOPE_A, coverage_state="incomplete"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, coverage_state="complete"),
        ]
    )
    record.validate()
    assert "repository_inventory_incomplete" in record.limitations
    assert record.candidate_state == "ready_for_review"


def test_unknown_coverage_is_not_complete_coverage():
    # A pre-03A artifact carries no coverage observation at all. That is a third state, and it must
    # never quietly become "complete".
    record = candidate(
        [
            occurrence(scope_id=SCOPE_A, coverage_state="unknown"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, coverage_state="complete"),
        ]
    )
    assert "repository_inventory_incomplete" in record.limitations


def test_complete_coverage_everywhere_omits_the_coverage_limitation():
    assert "repository_inventory_incomplete" not in candidate().limitations


def test_an_unknown_coverage_state_is_rejected():
    with pytest.raises(PatternCandidateError):
        candidate(
            [
                occurrence(scope_id=SCOPE_A, coverage_state="probably_fine"),
                occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B),
            ]
        )


def test_coverage_incompleteness_is_never_converted_into_absence():
    # Incomplete coverage says "I did not account for every file", never "the file is not there".
    record = candidate(
        [
            occurrence(scope_id=SCOPE_A, coverage_state="incomplete"),
            occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, coverage_state="incomplete"),
        ]
    )
    assert record.distinct_repository_scope_count == 2
    assert record.rule_satisfied is True
    assert record.counterevidence == ()


# -- unknowns and limitations --------------------------------------------------------------


def test_every_required_unknown_code_is_present():
    assert set(candidate().unknowns) == set(pc.REQUIRED_UNKNOWN_CODES)


def test_the_required_unknowns_name_everything_this_detector_cannot_see():
    assert (
        frozenset(
            {
                "authorship_not_assessed",
                "independent_creation_not_assessed",
                "repository_lineage_not_assessed",
                "generated_file_status_unknown",
                "vendored_file_status_unknown",
                "template_or_boilerplate_status_unknown",
                "semantic_equivalence_not_assessed",
                "significance_not_assessed",
                "novelty_not_assessed",
                "source_independence_not_assessed",
            }
        )
        == pc.REQUIRED_UNKNOWN_CODES
    )


def test_dropping_a_required_unknown_is_rejected():
    for code in pc.REQUIRED_UNKNOWN_CODES:
        remaining = tuple(c for c in sorted(pc.REQUIRED_UNKNOWN_CODES) if c != code)
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), unknowns=list(remaining))).validate()


def test_an_unsupported_unknown_code_is_rejected():
    extra = sorted(pc.REQUIRED_UNKNOWN_CODES) + ["probably_copied"]
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), unknowns=extra)).validate()


def test_required_limitations_are_present():
    assert set(pc.REQUIRED_LIMITATION_CODES) <= set(candidate().limitations)


def test_empty_limitations_are_rejected():
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), limitations=[])).validate()


def test_dropping_a_required_limitation_is_rejected():
    for code in pc.REQUIRED_LIMITATION_CODES:
        remaining = [c for c in sorted(pc.REQUIRED_LIMITATION_CODES) if c != code]
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), limitations=remaining)).validate()


def test_an_unsupported_limitation_code_is_rejected():
    bad = sorted(pc.REQUIRED_LIMITATION_CODES) + ["probably_significant"]
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), limitations=bad)).validate()


def test_the_limitations_say_what_exact_recurrence_does_not_prove():
    assert (
        frozenset(
            {
                "exact_byte_identity_only",
                "recurrence_does_not_prove_copying",
                "recurrence_does_not_prove_independent_implementation",
                "recurrence_does_not_prove_shared_capability",
                "recurrence_does_not_prove_novelty",
                "recurrence_does_not_establish_significance",
                "operator_review_required",
                "evidence_reflects_stored_observations_not_current_state",
            }
        )
        == pc.REQUIRED_LIMITATION_CODES
    )


def test_every_code_has_exactly_one_canonical_statement():
    # The artifact stores codes; the operator reads sentences. A code with no sentence would print
    # as a bare token, and a sentence with no code would be prose nobody can pin.
    assert set(pc.LIMITATION_STATEMENTS) == set(pc.ALLOWED_LIMITATION_CODES)
    assert set(pc.UNKNOWN_STATEMENTS) == set(pc.ALLOWED_UNKNOWN_CODES)


def test_independence_may_not_be_marked_assessed():
    remaining = [
        c for c in sorted(pc.REQUIRED_UNKNOWN_CODES) if c != "source_independence_not_assessed"
    ]
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), unknowns=remaining)).validate()


def test_copying_may_not_be_marked_determined():
    # There is no vocabulary in which "copied" is expressible — not as an unknown, not as a
    # limitation, not as a field.
    assert not any("copied" in code for code in pc.ALLOWED_UNKNOWN_CODES)
    assert not any(
        code.startswith("copied") or code == "likely_copied" for code in pc.ALLOWED_LIMITATION_CODES
    )
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), limitations=["likely_copied"])).validate()


def test_significance_may_not_be_marked_determined():
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(
            forge(candidate(), unknowns=["significance_determined"])
        ).validate()


def test_counterevidence_is_empty_but_representable():
    # Empty because no direct contradictory FACT exists yet — never because the field was omitted.
    record = candidate()
    assert record.counterevidence == ()
    assert "counterevidence" in record.to_dict()


def test_an_unknown_is_never_counterevidence():
    # Filling counterevidence with an unknown would dress "I don't know" as "I checked, and no".
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(
            forge(candidate(), counterevidence=["repository_lineage_not_assessed"])
        ).validate()


# -- authority -----------------------------------------------------------------------------

AUTHORITY_FALSE = (
    "is_hypothesis",
    "is_finding",
    "is_verified",
    "is_published",
    "authorizes_action",
    "authorizes_execution",
    "authorizes_release",
    "activates_profile",
    "contacts_gateway",
    "uses_llm",
)


def test_every_authority_pin_is_false():
    record = candidate()
    for pin in AUTHORITY_FALSE:
        assert getattr(record, pin) is False, pin
    assert record.operator_review_required is True


def test_flipping_any_authority_pin_is_rejected():
    for pin in AUTHORITY_FALSE:
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), **{pin: True})).validate()


def test_an_authority_pin_cannot_be_faked_with_a_falsy_value():
    # `0` and `""` are falsy but they are not `False`. The check is identity, not truthiness — a
    # truthy check would let `1` through on a pin that has to mean exactly one thing.
    for value in (0, "", None, []):
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), is_finding=value)).validate()


def test_operator_review_required_cannot_be_turned_off():
    for value in (False, 0, None):
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(
                forge(candidate(), operator_review_required=value)
            ).validate()


def test_rule_satisfied_is_not_a_score():
    record = candidate()
    assert record.rule_satisfied is True
    assert record.rule_parameters == {"min_distinct_repository_scopes": 2}


def test_a_candidate_may_not_claim_a_rule_it_did_not_satisfy():
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(forge(candidate(), rule_satisfied=False)).validate()


# -- no score ------------------------------------------------------------------------------

FORBIDDEN_FIELDS = frozenset(
    {
        "score",
        "score_label",
        "importance",
        "priority",
        "severity",
        "relevance",
        "recommendation",
        "rank",
        "ranking",
        "novelty",
        "significance",
        "confidence_score",
        "similarity",
        "similarity_score",
        "most_common",
        "top",
        "best",
        "likely",
        "probability",
        "patentability",
        "commercial_value",
        "operator_value",
        "risk_level",
        "tier",
        "confidence",
    }
)


#: Evaluative STEMS, not exact field names. A mutation run proved why: a real, fully wired
#: `candidate_confidence` scalar sailed through an exact-name blocklist containing `confidence` and
#: `confidence_score`, because it was spelled slightly differently. An exact list only forbids the
#: names somebody already thought of; a stem forbids the CONCEPT — `recurrence_weight`, `certainty`,
#: `tier_2`, `relevance_index` and `candidate_confidence` are all the same field wearing a hat.
EVALUATIVE_STEMS = (
    "score",
    "rank",
    "importance",
    "priority",
    "severity",
    "relevance",
    "significan",
    "novel",
    "similar",
    "weight",
    "probab",
    "confidence",
    "certainty",
    "patentab",
    "risk",
    "recommend",
    "most_common",
    "commercial",
    "operator_value",
    "tier",
    "grade",
    "strength",
)

#: The single legitimate home of the word "confidence" in this contract: inside ONE supporting
#: observation, where it is EPISTEMIC (*was this fact determined?*) and not evaluative (*does it
#: matter?*). Anywhere else, at any depth, under any spelling, it is a score.
EPISTEMIC_CONFIDENCE_PATH = ("supporting_occurrences", "supporting_observations", "confidence")


def _walk_keys(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            _walk_keys(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_keys(item, out)


def _evaluative_keys(node, path=(), found=None):
    """Every key whose NAME carries an evaluative concept, with the path it was found at."""
    found = [] if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            here = (*path, key)
            if any(stem in key.lower() for stem in EVALUATIVE_STEMS):
                found.append(here)
            _evaluative_keys(value, here, found)
    elif isinstance(node, list):
        for item in node:
            _evaluative_keys(item, path, found)
    return found


def test_no_evaluative_field_exists_anywhere_in_a_candidate():
    """Recursively, and by concept rather than by spelling.

    The one place a score can hide is inside an evidence occurrence, which is copied verbatim from
    the store — and the one name it can hide *under* is any word nobody put on a blocklist. So the
    assertion is: no key anywhere carries an evaluative stem, except the epistemic `confidence` on a
    supporting observation, at exactly that path.
    """
    for path in _evaluative_keys(candidate().to_dict()):
        assert path == EPISTEMIC_CONFIDENCE_PATH, f"evaluative field at {'.'.join(path)}"

    top_level = set(candidate().to_dict())
    assert not (top_level & FORBIDDEN_FIELDS)
    assert not [key for key in top_level if any(stem in key.lower() for stem in EVALUATIVE_STEMS)]


def test_a_score_field_is_rejected_even_with_a_restamped_digest():
    for field in (
        "score",
        "importance",
        "rank",
        "novelty",
        "significance",
        "confidence",
        "candidate_confidence",
        "recurrence_weight",
    ):
        with pytest.raises(PatternCandidateValidationError):
            PatternCandidate.from_dict(forge(candidate(), **{field: 0.99}))


def test_there_is_no_aggregate_candidate_confidence():
    """Not "no field called `confidence`" — no field that IS one, under any name.

    An entity is built only from *determined* observations (01B), so a candidate-level confidence
    could only ever be the derived constant 1.0 — a number asserting a variability that cannot
    exist, sitting in exactly the slot where significance likes to hide.
    """
    record = candidate().to_dict()
    for key in record:
        assert "confidence" not in key.lower(), key
        assert "certainty" not in key.lower(), key

    # ...while the per-observation epistemic confidence survives verbatim, where it belongs.
    assert record["supporting_occurrences"][0]["supporting_observations"][0]["confidence"] == 1.0


def test_the_occurrence_count_is_a_count_and_never_a_sort_key():
    # It is a fact about the evidence. It is not a measure of how much the evidence matters.
    assert candidate().distinct_repository_scope_count == 2


def test_the_module_declares_no_evaluative_name():
    """A name guard, because a helper called `_rank_candidates` would be the first ranking."""
    tree = ast.parse(Path(pc.__file__).read_text(encoding="utf-8"))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    for name in names:
        lowered = name.lower()
        assert not any(
            bad in lowered
            for bad in ("score", "rank", "importance", "priority", "severity", "significan")
        ), name


# -- purity --------------------------------------------------------------------------------


def test_the_contract_is_pure():
    """No database, no filesystem, no network, no clock, no LLM.

    A clock is the subtle one: `created_at` is INJECTED, so two runs over the same facts produce
    byte-identical artifacts and the digest can be trusted as an identity.
    """
    tree = ast.parse(Path(pc.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {"sqlite3", "os", "subprocess", "socket", "httpx", "requests", "anthropic", "time"}
    assert not (imported & forbidden), imported & forbidden
    assert "modules.db" not in imported
    # The one non-stdlib import allowed: the observation substrate, whose canonical-JSON digest
    # recipe this contract shares rather than copying. A second copy is how two digests drift.
    assert imported <= {
        "__future__",
        "dataclasses",
        "hashlib",
        "json",
        "re",
        "typing",
        "collections.abc",
        "modules.observations",
        # The detector-spec registry (LHP-INTEL-04C). Imported lazily and itself pure — it carries
        # detector-owned vocabulary and validators, no database, filesystem, network, clock or LLM.
        "modules.pattern_candidate_specs",
    }, imported


# -- disposition ---------------------------------------------------------------------------


def disposition(**kwargs) -> PatternDisposition:
    record = kwargs.pop("candidate", None) or candidate()
    return build_pattern_disposition(
        candidate_id=kwargs.pop("candidate_id", record.candidate_id),
        candidate_artifact_digest=kwargs.pop("candidate_artifact_digest", record.artifact_digest),
        operator_id=kwargs.pop("operator_id", "operator-test"),
        decision=kwargs.pop("decision", "needs_more_evidence"),
        reason=kwargs.pop("reason", "Repository lineage has not been assessed."),
        created_at=kwargs.pop("created_at", CREATED_AT),
        **kwargs,
    )


def test_each_allowed_decision_builds():
    for decision in ("accepted_for_followup", "dismissed", "needs_more_evidence"):
        record = disposition(decision=decision)
        record.validate()
        assert record.decision == decision


def test_the_decision_vocabulary_is_closed_and_contains_no_verdict():
    assert (
        frozenset({"accepted_for_followup", "dismissed", "needs_more_evidence"})
        == pc.ALLOWED_DECISIONS
    )
    for verdict in ("approved", "verified", "finding", "published", "rejected_as_false"):
        assert verdict not in pc.ALLOWED_DECISIONS


def test_a_decision_outside_the_closed_enum_is_rejected():
    for bad in ("approved", "verified", "finding", "published", "rejected_as_false", ""):
        with pytest.raises(PatternCandidateError):
            disposition(decision=bad)


def test_operator_id_is_required():
    for bad in ("", "   ", None):
        with pytest.raises(PatternCandidateError):
            disposition(operator_id=bad)


def test_a_reason_is_required():
    for bad in ("", "   ", None):
        with pytest.raises(PatternCandidateError):
            disposition(reason=bad)


def test_the_disposition_binds_to_the_exact_candidate_version():
    record = candidate()
    decided = disposition(candidate=record)
    assert decided.candidate_id == record.candidate_id
    assert decided.candidate_artifact_digest == record.artifact_digest


def test_a_disposition_naming_a_malformed_candidate_is_rejected():
    with pytest.raises(PatternCandidateError):
        disposition(candidate_id="nonsense")
    with pytest.raises(PatternCandidateError):
        disposition(candidate_artifact_digest="nope")


def test_review_method_is_local_cli_and_operator_identity_is_not_claimed():
    """We do not claim an authentication that does not exist. A CLI flag is not an identity."""
    record = disposition()
    assert record.review_method == "local_cli"
    assert record.operator_identity_authenticated is False


def test_operator_identity_authenticated_cannot_be_asserted():
    raw = disposition().to_dict()
    raw["operator_identity_authenticated"] = True
    with pytest.raises(PatternCandidateValidationError):
        PatternDisposition.from_dict(raw).validate()


def test_an_unsupported_review_method_is_rejected():
    raw = disposition().to_dict()
    raw["review_method"] = "gateway_approval"
    with pytest.raises(PatternCandidateValidationError):
        PatternDisposition.from_dict(raw).validate()


def test_the_disposition_digest_is_deterministic_and_excludes_created_at():
    early = disposition(created_at="2026-07-14T00:00:00+00:00")
    late = disposition(created_at="2026-08-01T00:00:00+00:00")
    assert early.artifact_digest == late.artifact_digest
    assert early.disposition_id == late.disposition_id


def test_a_forged_disposition_digest_is_rejected():
    raw = disposition().to_dict()
    raw["artifact_digest"] = "f" * 64
    with pytest.raises(PatternCandidateValidationError):
        PatternDisposition.from_dict(raw).validate()


def test_a_disposition_carries_no_verdict_and_no_score():
    keys: set[str] = set()
    _walk_keys(disposition().to_dict(), keys)
    assert not (keys & FORBIDDEN_FIELDS)
    for absent in ("is_finding", "is_verified", "authorizes_action", "candidate_state"):
        assert absent not in keys


def test_an_unknown_disposition_field_is_rejected():
    raw = disposition().to_dict()
    raw["authorizes_release"] = True
    with pytest.raises(PatternCandidateValidationError):
        PatternDisposition.from_dict(raw)


def test_accepted_for_followup_is_not_a_verdict():
    """It means 'worth more investigation' — the artifact has no field in which 'true' could be
    written, which is the only way to be sure it never says so."""
    record = disposition(decision="accepted_for_followup")
    raw = record.to_dict()
    assert "is_finding" not in raw
    assert "is_verified" not in raw
    assert set(raw) == set(pc.DISPOSITION_FIELDS)


# -- the schema mirrors ---------------------------------------------------------------------
#
# The mirrors are DOCUMENTATION. This is already established for the six that predate this packet:
# "the authoritative enforcer is the module dataclass validate(), not the schema file". A mirror
# that drifts from the validator is worse than no mirror at all — it is a confident lie — so the
# drift is pinned with stdlib, and runs everywhere, with no new dependency.

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "schemas"


def _mirror(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


from modules import pattern_candidate_specs as _specs  # noqa: E402
from modules import repository_content_recurrence as _rcr  # noqa: E402
from modules import scientific_source_disappearance as _ssd  # noqa: E402

_REG = _specs.REGISTRY


def _union(attr_a, attr_b):
    return set().union(*(getattr(s, attr_a) | getattr(s, attr_b) for s in _REG.values()))


def test_the_candidate_mirror_pins_the_spine_and_the_two_branches():
    """A discriminated union (LHP-INTEL-04C): the spine is required at the top; the detector-owned
    fields (occurrences + scope count vs evidence) live in the two branches."""
    mirror = _mirror("pattern_candidate.v1.schema.json")
    assert set(mirror["required"]) == set(pc.SPINE_FIELDS)
    assert set(mirror["properties"]) == set(pc.CANDIDATE_FIELDS) | {"evidence"}
    assert mirror["additionalProperties"] is False
    assert {
        branch["if"]["properties"]["detector_id"]["const"] for branch in mirror["allOf"]
    } == set(_REG)


def test_the_candidate_mirror_pins_the_union_vocabularies():
    props = _mirror("pattern_candidate.v1.schema.json")["properties"]
    assert set(props["candidate_state"]["enum"]) == set(pc.ALLOWED_CANDIDATE_STATES)
    assert set(props["profile_id"]["enum"]) == set(pc.ALLOWED_PROFILE_IDS)
    assert set(props["detector_id"]["enum"]) == set(_REG)
    assert set(props["candidate_type"]["enum"]) == {s.candidate_type for s in _REG.values()}
    assert set(props["rule_id"]["enum"]) == {_rcr.RULE_ID, _ssd.RULE_ID}
    assert set(props["unknowns"]["items"]["enum"]) == _union(
        "required_unknowns", "conditional_unknowns"
    )
    assert set(props["limitations"]["items"]["enum"]) == _union(
        "required_limitations", "conditional_limitations"
    )
    assert props["subject"]["properties"]["entity_id"]["pattern"] == "^content:[0-9a-f]{64}$"


def test_each_branch_narrows_to_its_detectors_vocabulary():
    mirror = _mirror("pattern_candidate.v1.schema.json")
    branches = {b["if"]["properties"]["detector_id"]["const"]: b["then"] for b in mirror["allOf"]}
    for detector_id, spec in _REG.items():
        then = branches[detector_id]
        assert then["properties"]["profile_id"]["const"] == spec.profile_id
        assert then["properties"]["candidate_type"]["const"] == spec.candidate_type
        assert set(then["properties"]["unknowns"]["items"]["enum"]) == set(
            spec.required_unknowns | spec.conditional_unknowns
        )
        assert set(then["properties"]["limitations"]["items"]["enum"]) == set(
            spec.required_limitations | spec.conditional_limitations
        )
        for field in spec.extra_fields:
            assert field in then["required"]
    # The repository branch forbids `evidence`; the scientific branch forbids the repository fields.
    # (The occurrence and evidence field SCHEMAS live in the base `properties`; the branches only
    # require/forbid presence and narrow the closed vocabularies.)
    repo = branches["repository_exact_content_recurrence"]
    assert repo["not"]["required"] == ["evidence"]
    sci = branches["scientific_source_disappearance_review"]
    forbidden = {clause["required"][0] for clause in sci["not"]["anyOf"]}
    assert forbidden == {"supporting_occurrences", "distinct_repository_scope_count"}
    assert set(sci["properties"]["evidence"]["required"]) == set(_ssd.EVIDENCE_SECTIONS)


def test_the_candidate_mirror_pins_every_authority_denial():
    props = _mirror("pattern_candidate.v1.schema.json")["properties"]
    for pin in pc.AUTHORITY_PINS_FALSE:
        assert props[pin]["const"] is False, pin
    assert props["operator_review_required"]["const"] is True
    assert props["rule_satisfied"]["const"] is True


def test_the_disposition_mirror_pins_the_contract():
    mirror = _mirror("pattern_candidate_disposition.v1.schema.json")
    assert set(mirror["required"]) == set(pc.DISPOSITION_FIELDS)
    assert set(mirror["properties"]) == set(pc.DISPOSITION_FIELDS)
    assert mirror["additionalProperties"] is False

    props = mirror["properties"]
    assert set(props["decision"]["enum"]) == set(pc.ALLOWED_DECISIONS)
    assert set(props["review_method"]["enum"]) == set(pc.ALLOWED_REVIEW_METHODS)
    assert props["operator_identity_authenticated"]["const"] is False


def test_no_mirror_declares_an_evaluative_field():
    for name in (
        "pattern_candidate.v1.schema.json",
        "pattern_candidate_disposition.v1.schema.json",
    ):
        keys: set[str] = set()
        _walk_keys(_mirror(name), keys)
        offenders = (keys & FORBIDDEN_FIELDS) - {"confidence"}
        assert not offenders, (name, offenders)


def _scientific_candidate_for_mirror() -> dict:
    """A minimal valid scientific candidate, built through the detector + spine — for the jsonschema
    validation tests (which skip where jsonschema is absent). Self-contained, no test cycle."""
    from datetime import UTC, datetime, timedelta

    def chk(outcome, day, status=None, digest=None):
        stamp = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day)).isoformat()
        ident = hashlib.sha256(f"{outcome}{day}".encode()).hexdigest()
        return {
            "check_id": "psc_" + ident,
            "run_id": "r",
            "source_id": "s",
            "locator_id": "loc",
            "canonical_locator": "https://a.example.org/x",
            "outcome": outcome,
            "http_status": status,
            "content_digest": digest,
            "content_digest_algorithm": ("sha256" if digest else None),
            "body_size_bytes": (1 if digest else None),
            "content_type": ("text/csv" if digest else None),
            "checked_at": stamp,
            "adapter_id": "public_source_intake",
            "adapter_version": "public_source_intake/1",
            "policy_digest": "p" * 64,
            "artifact_digest": ident,
            "source_class": "public_source",
            "privacy_class": "public",
        }

    digest = hashlib.sha256(b"scientific bytes").hexdigest()
    data = _ssd.ScientificDetectionInput(
        locators=(
            _ssd.LocatorHistory(
                source_id="s",
                locator_id="loc",
                canonical_locator="https://a.example.org/x",
                checks=(chk("available", 1, 200, digest), chk("not_found", 10, 404)),
            ),
        ),
        local_occurrences={},
        run_digests={},
        created_at="2026-07-15T00:00:00+00:00",
    )
    item = _ssd.detect_disappearing_sources(data).candidates[0]
    from modules.pattern_candidate import finalize_candidate

    return finalize_candidate(
        PatternCandidate(
            profile_id=_ssd.OWNER_PROFILE_ID,
            detector_id=_ssd.DETECTOR_ID,
            detector_version=_ssd.DETECTOR_VERSION,
            candidate_type=_ssd.CANDIDATE_TYPE,
            subject={"entity_id": item.subject_content_entity_id, "entity_type": "content"},
            rule_id=_ssd.RULE_ID,
            rule_version=_ssd.RULE_VERSION,
            rule_parameters=dict(_ssd.SCIENTIFIC_RULE_PARAMETERS),
            unknowns=item.unknowns,
            limitations=item.limitations,
            evidence=item.evidence,
            counterevidence=item.counterevidence,
            created_at="2026-07-15T00:00:00+00:00",
        )
    ).to_dict()


def test_real_candidates_of_both_detectors_validate_against_the_mirror():
    """Runs where jsonschema is installed; skipped otherwise. The drift pins above always run."""
    jsonschema = pytest.importorskip("jsonschema")

    schema = _mirror("pattern_candidate.v1.schema.json")
    jsonschema.validate(candidate().to_dict(), schema)
    jsonschema.validate(_scientific_candidate_for_mirror(), schema)
    jsonschema.validate(
        disposition().to_dict(), _mirror("pattern_candidate_disposition.v1.schema.json")
    )


def test_the_mirror_refuses_what_the_validator_refuses():
    jsonschema = pytest.importorskip("jsonschema")

    schema = _mirror("pattern_candidate.v1.schema.json")
    for forged in (
        forge(candidate(), is_finding=True),
        forge(candidate(), candidate_state="verified"),
        forge(candidate(), score=0.9),
        forge(candidate(), unknowns=[]),
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(forged, schema)

    # Cross-detector: a repository candidate carrying `evidence` is refused by the repository
    # branch, and a scientific candidate carrying repository fields by the scientific branch.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**candidate().to_dict(), "evidence": {}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {**_scientific_candidate_for_mirror(), "distinct_repository_scope_count": 2}, schema
        )
