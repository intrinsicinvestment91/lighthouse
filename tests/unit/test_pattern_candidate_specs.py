"""Detector-owned candidate specifications (LHP-INTEL-04C).

The generalization that lets `pattern_candidate.v1` carry a second detector without touching one
byte of an existing repository candidate. Two obligations are tested here:

* **Byte-for-byte backward compatibility** — a repository candidate built after the generalization
  serializes to the same canonical JSON, and reproduces the same `candidate_id`, `candidate_key`
  and `artifact_digest`, as it did before. The golden values below were pinned from the pre-04C
  committed code (HEAD 9a32fe3 + the three 04B commits) and must never move. Both the digest and the
  stored `candidate_json` are `json.dumps(..., sort_keys=True)`, so this reduces to "same key set,
  same values"; if it ever fails, the generalization changed a repository candidate and must stop —
  not bump a schema version.
* **A closed, code-owned registry** — one exact `DetectorCandidateSpec` per detector id, resolved
  fail-closed. A candidate cannot supply its own validator, unknown vocabulary, or evidence schema.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from modules.pattern_candidate import build_pattern_candidate

# -- byte-compat fixtures (self-contained; shape = observation_query.entity_occurrences) ----

CONTENT_A = "content:" + hashlib.sha256(b"alpha bytes\n").hexdigest()
SCOPE_A = hashlib.sha256(b"/repos/a").hexdigest()
SCOPE_B = hashlib.sha256(b"/repos/b").hexdigest()
SCOPE_C = hashlib.sha256(b"/repos/c").hexdigest()
ARTIFACT_A = hashlib.sha256(b"artifact-a").hexdigest()
ARTIFACT_B = hashlib.sha256(b"artifact-b").hexdigest()
ARTIFACT_C = hashlib.sha256(b"artifact-c").hexdigest()
CREATED_AT = "2026-07-14T12:00:00+00:00"


def _occurrence(
    *,
    scope_id,
    artifact,
    scope_label,
    path="Dockerfile",
    entity_id=CONTENT_A,
    coverage_state="complete",
):
    digest = entity_id.split(":", 1)[1]
    return {
        "scope_id": scope_id,
        "scope_type": "repository",
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


def _repository_candidate(occurrences):
    return build_pattern_candidate(
        profile_id="repository_capability_discovery",
        detector_id="repository_exact_content_recurrence",
        detector_version="repository_exact_content_recurrence/1",
        candidate_type="repository_exact_content_recurrence",
        subject_entity_id=CONTENT_A,
        occurrences=occurrences,
        rule_id="exact_content_recurrence_across_repository_scopes",
        rule_version=1,
        rule_parameters={"min_distinct_repository_scopes": 2},
        created_at=CREATED_AT,
    )


TWO_SCOPE = [
    _occurrence(scope_id=SCOPE_A, artifact=ARTIFACT_A, scope_label="alpha"),
    _occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, scope_label="beta"),
]
THREE_SCOPE = [
    *TWO_SCOPE,
    _occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_C, scope_label="gamma"),
]

# Pinned from pre-04C code. These are the load-bearing bytes: they must never move.
GOLDEN = {
    "two_scope": {
        "candidate_id": "pc_f5ab9c39282539aad7bfa479070b2525659368d41545c4b1c93cd605dff74c51",
        "candidate_key": "pk_3d4fe054ae83c59982d9dbe214d500d68cab63e4e44616c73f56f64134b9fc30",
        "artifact_digest": "f5ab9c39282539aad7bfa479070b2525659368d41545c4b1c93cd605dff74c51",
        "stored_json_sha256": "59078924e13539a6bbfafb46d237583f43d943b9e1b949ac76e9ac6e5bc68bb2",
    },
    "three_scope": {
        "candidate_id": "pc_744f02e533ad0672738b01020124067999f0355ab53d8626cbe045ff30a46f1d",
        "candidate_key": "pk_3d4fe054ae83c59982d9dbe214d500d68cab63e4e44616c73f56f64134b9fc30",
        "artifact_digest": "744f02e533ad0672738b01020124067999f0355ab53d8626cbe045ff30a46f1d",
        "stored_json_sha256": "918e605e68cee8063d5cb23e210753fe732d7e4405d00b0397b3809016a060e3",
    },
}


@pytest.mark.parametrize(
    "name,occurrences", [("two_scope", TWO_SCOPE), ("three_scope", THREE_SCOPE)]
)
def test_repository_candidate_is_byte_identical_to_pre_04c(name, occurrences):
    candidate = _repository_candidate(occurrences)
    golden = GOLDEN[name]
    assert candidate.candidate_id == golden["candidate_id"]
    assert candidate.candidate_key == golden["candidate_key"]
    assert candidate.artifact_digest == golden["artifact_digest"]
    # The stored form: exactly db.insert_pattern_candidate's json.dumps(payload, sort_keys=True).
    stored = json.dumps(candidate.to_dict(), sort_keys=True)
    assert hashlib.sha256(stored.encode()).hexdigest() == golden["stored_json_sha256"]


def test_two_and_three_scope_share_a_key_but_not_an_id():
    """Same subject/profile/detector/type => same candidate_key across evidence versions; different
    evidence => different candidate_id. The versioning contract, unchanged by the generalization."""
    two = _repository_candidate(TWO_SCOPE)
    three = _repository_candidate(THREE_SCOPE)
    assert two.candidate_key == three.candidate_key
    assert two.candidate_id != three.candidate_id


# -- the registry ----------------------------------------------------------------------------

import datetime  # noqa: E402

from modules import scientific_source_disappearance as ssd  # noqa: E402
from modules.pattern_candidate import (  # noqa: E402
    PatternCandidate,
    PatternCandidateValidationError,
    finalize_candidate,
)
from modules.pattern_candidate_specs import (  # noqa: E402
    REGISTRY,
    allowed_detector_ids,
    get_spec,
)

DIGEST_X = hashlib.sha256(b"dataset-X").hexdigest()
_DAY = 24 * 3600


def _iso_day(day):
    return datetime.datetime.fromtimestamp(1_800_000_000 + day * _DAY, tz=datetime.UTC).isoformat()


def _chk(
    *,
    outcome,
    day,
    http_status=None,
    content_digest=None,
    run_id="run-1",
    locator_id="loc-1",
    source_id="src-1",
    canonical="https://a.example.org/x",
    seq=0,
):
    algo = "sha256" if content_digest else None
    ident = hashlib.sha256(f"{locator_id}{day}{outcome}{seq}".encode()).hexdigest()
    return {
        "check_id": "psc_" + ident,
        "run_id": run_id,
        "source_id": source_id,
        "locator_id": locator_id,
        "canonical_locator": canonical,
        "outcome": outcome,
        "http_status": http_status,
        "content_digest": content_digest,
        "content_digest_algorithm": algo,
        "body_size_bytes": 512 if content_digest else None,
        "content_type": "text/csv" if content_digest else None,
        "checked_at": _iso_day(day),
        "adapter_id": "public_source_intake",
        "adapter_version": "public_source_intake/1",
        "policy_digest": "p" * 64,
        "artifact_digest": ident,
        "source_class": "public_source",
        "privacy_class": "public",
    }


def _detect_input(
    checks,
    *,
    locator_id="loc-1",
    source_id="src-1",
    canonical="https://a.example.org/x",
    extra_locators=(),
    local=None,
):
    subject = ssd.LocatorHistory(
        source_id=source_id,
        locator_id=locator_id,
        canonical_locator=canonical,
        checks=tuple(checks),
    )
    data = ssd.ScientificDetectionInput(
        locators=(subject, *extra_locators),
        local_occurrences=dict(local or {}),
        run_digests={},
        created_at=_iso_day(999),
    )
    return ssd.detect_disappearing_sources(data)


def _scientific_candidate(candidate_input=None, *, created_at="2026-07-15T00:00:00+00:00"):
    if candidate_input is None:
        result = _detect_input(
            [
                _chk(outcome="available", day=1, http_status=200, content_digest=DIGEST_X),
                _chk(outcome="not_found", day=10, http_status=404),
            ]
        )
        candidate_input = result.candidates[0]
    draft = PatternCandidate(
        profile_id=ssd.OWNER_PROFILE_ID,
        detector_id=ssd.DETECTOR_ID,
        detector_version=ssd.DETECTOR_VERSION,
        candidate_type=ssd.CANDIDATE_TYPE,
        subject={"entity_id": candidate_input.subject_content_entity_id, "entity_type": "content"},
        rule_id=ssd.RULE_ID,
        rule_version=ssd.RULE_VERSION,
        rule_parameters=dict(ssd.SCIENTIFIC_RULE_PARAMETERS),
        unknowns=candidate_input.unknowns,
        limitations=candidate_input.limitations,
        evidence=candidate_input.evidence,
        counterevidence=candidate_input.counterevidence,
        created_at=created_at,
    )
    return finalize_candidate(draft)


def test_registry_resolves_both_detectors():
    assert (
        get_spec("repository_exact_content_recurrence").profile_id
        == "repository_capability_discovery"
    )
    scientific = get_spec("scientific_source_disappearance_review")
    assert scientific.profile_id == "scientific_preservation"
    assert scientific.candidate_type == "disappearing_scientific_source"


def test_allowed_detector_ids_are_closed():
    assert allowed_detector_ids() == frozenset(
        {"repository_exact_content_recurrence", "scientific_source_disappearance_review"}
    )
    assert set(REGISTRY) == allowed_detector_ids()


@pytest.mark.parametrize(
    "bad", ["unknown_detector", "", None, 123, "repository_exact_content_recurrenc"]
)
def test_get_spec_fails_closed_on_unknown_detector(bad):
    with pytest.raises(PatternCandidateValidationError):
        get_spec(bad)


def test_a_spec_is_immutable():
    # A frozen dataclass raises FrozenInstanceError (a subclass of AttributeError) on assignment.
    with pytest.raises(AttributeError):
        get_spec("scientific_source_disappearance_review").detector_id = "tampered"


def test_a_valid_scientific_candidate_round_trips():
    candidate = _scientific_candidate()
    assert candidate.candidate_id.startswith("pc_")
    assert candidate.detector_id == "scientific_source_disappearance_review"
    assert candidate.candidate_type == "disappearing_scientific_source"
    # to_dict validates; from_dict + validate accepts it back; and it has no repository fields.
    raw = candidate.to_dict()
    assert "evidence" in raw
    assert "supporting_occurrences" not in raw
    assert "distinct_repository_scope_count" not in raw
    restored = PatternCandidate.from_dict(raw)
    restored.validate()
    assert restored.candidate_id == candidate.candidate_id


def test_scientific_candidate_key_includes_the_locator():
    """Two locators serving the same bytes are two separate risks — different keys, same subject."""
    same_bytes = [
        _chk(outcome="available", day=1, http_status=200, content_digest=DIGEST_X),
        _chk(outcome="not_found", day=10, http_status=404),
    ]
    a = _scientific_candidate(
        _detect_input(
            same_bytes, locator_id="loc-A", canonical="https://a.example.org/x"
        ).candidates[0]
    )
    b = _scientific_candidate(
        _detect_input(
            same_bytes, locator_id="loc-B", canonical="https://b.example.org/x"
        ).candidates[0]
    )
    assert a.subject == b.subject
    assert a.candidate_key != b.candidate_key


# -- cross-detector refusal ------------------------------------------------------------------


def test_a_repository_candidate_cannot_carry_scientific_evidence():
    raw = _repository_candidate(TWO_SCOPE).to_dict()
    raw["evidence"] = {"anything": True}
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw)


def test_a_scientific_candidate_cannot_carry_repository_fields():
    raw = _scientific_candidate().to_dict()
    raw["distinct_repository_scope_count"] = 2
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw)


def test_a_candidate_with_an_unknown_detector_is_rejected():
    raw = _scientific_candidate().to_dict()
    raw["detector_id"] = "some_unknown_detector"
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw)


def test_a_detector_under_the_wrong_profile_is_rejected():
    candidate = _scientific_candidate()
    raw = candidate.to_dict()
    raw["profile_id"] = "repository_capability_discovery"
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw).validate()


def test_a_scientific_candidate_using_repository_unknowns_is_rejected():
    candidate = _scientific_candidate()
    raw = candidate.to_dict()
    raw["unknowns"] = sorted(
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
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw).validate()


# -- no significance, in the second detector too ---------------------------------------------

from tests.unit.test_pattern_candidate import _evaluative_keys  # noqa: E402


def test_a_scientific_candidate_carries_no_evaluative_field_anywhere():
    """The repository candidate has ONE epistemic-confidence exception; the scientific candidate has
    none at all. Every key in its serialized form — top and nested — must be non-evaluative."""
    assert _evaluative_keys(_scientific_candidate().to_dict()) == []


@pytest.mark.parametrize(
    "field",
    [
        "disappearance_probability",
        "preservation_score",
        "source_importance",
        "risk_weight",
        "recurrence_strength",
        "scientific_priority",
        "confidence_score",
    ],
)
def test_a_scientific_evaluative_field_is_unrepresentable(field):
    # Top level: the closed field set refuses it before any digest is even considered.
    raw = _scientific_candidate().to_dict()
    raw[field] = 0.99
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(raw)

    # Nested inside the evidence, and RE-STAMPED — so the digest cannot be what catches it. The
    # closed section vocabulary has to be the guard. (A mutation run proved this: without the
    # re-stamp, this test passed on a digest mismatch even after the section check was removed.)
    nested = _scientific_candidate().to_dict()
    nested["evidence"]["rule_result"][field] = 0.99
    volatile = ("candidate_id", "created_at", "artifact_digest")
    content = {k: v for k, v in nested.items() if k not in volatile}
    nested["artifact_digest"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    nested["candidate_id"] = "pc_" + nested["artifact_digest"]
    with pytest.raises(PatternCandidateValidationError):
        PatternCandidate.from_dict(nested).validate()
