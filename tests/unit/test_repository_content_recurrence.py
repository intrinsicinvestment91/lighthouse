"""The first profile-owned detector (LHP-INTEL-03B).

`repository_exact_content_recurrence` answers exactly one question — *which exact raw-byte content
identities have been directly observed in at least two distinct repository scopes?* — and refuses
every question after it. It is owned by **Repository Capability Discovery**; it does not define what
significance means for Lighthouse, and it does not make every recurrence an invention.

The rule is three lines of set arithmetic. Almost all of the care here goes into what it must NOT
do: count rows instead of scopes, treat two paths in one repository as two repositories, accept a
document as a second repository, sort by how often something recurred, or emit a candidate from a
view of the evidence that was silently truncated.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from modules import repository_content_recurrence as detector
from modules.pattern_candidate import PatternCandidateError
from modules.repository_content_recurrence import (
    EntityEvidence,
    detect_content_recurrence,
)

CONTENT_A = "content:" + hashlib.sha256(b"alpha bytes\n").hexdigest()
CONTENT_B = "content:" + hashlib.sha256(b"beta bytes\n").hexdigest()

SCOPE_A = hashlib.sha256(b"/repos/a").hexdigest()
SCOPE_B = hashlib.sha256(b"/repos/b").hexdigest()
SCOPE_C = hashlib.sha256(b"/repos/c").hexdigest()
SCOPE_DOC = hashlib.sha256(b"/docs/spec.pdf").hexdigest()

ARTIFACT_A = hashlib.sha256(b"artifact-a").hexdigest()
ARTIFACT_B = hashlib.sha256(b"artifact-b").hexdigest()
ARTIFACT_C = hashlib.sha256(b"artifact-c").hexdigest()

CREATED_AT = "2026-07-14T12:00:00+00:00"


def occurrence(
    *,
    scope_id=SCOPE_A,
    scope_type="repository",
    artifact=ARTIFACT_A,
    path="Dockerfile",
    entity_id=CONTENT_A,
    coverage_state="complete",
    label="alpha",
):
    digest = entity_id.split(":", 1)[1]
    return {
        "scope_id": scope_id,
        "scope_type": scope_type,
        "scope_label": label,
        "scope_path": f"/repos/{label}",
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


def evidence(entity_id, occurrences, *, complete=True):
    return EntityEvidence(
        entity_id=entity_id, occurrences=tuple(occurrences), evidence_complete=complete
    )


def run(items):
    return detect_content_recurrence(items, created_at=CREATED_AT)


# -- the rule ------------------------------------------------------------------------------


def test_no_facts_produce_no_candidate():
    result = run([])
    assert result.candidates == ()
    assert result.skipped == ()


def test_one_repository_scope_produces_no_candidate():
    result = run([evidence(CONTENT_A, [occurrence(scope_id=SCOPE_A)])])
    assert result.candidates == ()


def test_two_repository_scopes_with_the_same_content_produce_one_candidate():
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, label="alpha"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
                ],
            )
        ]
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.subject["entity_id"] == CONTENT_A
    assert candidate.distinct_repository_scope_count == 2
    assert candidate.candidate_state == "ready_for_review"


def test_three_repository_scopes_produce_one_candidate_with_three_occurrences():
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, label="alpha"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
                    occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_C, label="gamma"),
                ],
            )
        ]
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.distinct_repository_scope_count == 3
    assert len(candidate.supporting_occurrences) == 3


def test_three_scopes_do_not_explode_into_pairwise_candidates():
    """A, B and C is ONE candidate — not A-B, A-C, B-C.

    Pairwise candidates would triple the review burden and imply a relationship between each pair
    that the facts do not contain.
    """
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, label="alpha"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
                    occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_C, label="gamma"),
                ],
            )
        ]
    )
    assert len(result.candidates) == 1
    assert len({c.candidate_key for c in result.candidates}) == 1


def test_multiple_matching_paths_in_one_repository_remain_one_scope():
    """Two identical files inside ONE repository is not recurrence across repositories.

    03A dedupes a `content:` entity per artifact, so this arrives as one entity row with two
    `file_has_content` relationships — but a careless model could still read the two paths as two
    sources.
    """
    record = occurrence(scope_id=SCOPE_A)
    record["relationships"].append(
        {
            "relationship_type": "file_has_content",
            "subject_id": f"file:{SCOPE_A}:build/Dockerfile",
            "object_id": CONTENT_A,
            "relationship_id": hashlib.sha256(b"second-path").hexdigest(),
            "relationship_digest": hashlib.sha256(b"second-path-digest").hexdigest(),
            "source_artifact_digest": ARTIFACT_A,
            "derivation_version": "observation_normalizer/2",
        }
    )
    assert run([evidence(CONTENT_A, [record])]).candidates == ()


def test_one_repository_observed_twice_is_still_one_scope():
    """The unique index is (entity_id, source_artifact_digest, derivation_version), so a repository
    re-observed after a change is TWO rows with ONE scope_id. Counting rows would call that
    recurrence — and re-observing is the normal case, not a corner one."""
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, artifact=ARTIFACT_A),
                    occurrence(scope_id=SCOPE_A, artifact=ARTIFACT_B),
                ],
            )
        ]
    )
    assert result.candidates == ()


def test_two_contents_produce_two_candidates():
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, entity_id=CONTENT_A),
                    occurrence(scope_id=SCOPE_B, entity_id=CONTENT_A, artifact=ARTIFACT_B),
                ],
            ),
            evidence(
                CONTENT_B,
                [
                    occurrence(scope_id=SCOPE_A, entity_id=CONTENT_B),
                    occurrence(scope_id=SCOPE_B, entity_id=CONTENT_B, artifact=ARTIFACT_B),
                ],
            ),
        ]
    )
    assert len(result.candidates) == 2
    assert {c.subject["entity_id"] for c in result.candidates} == {CONTENT_A, CONTENT_B}


# -- documents -----------------------------------------------------------------------------


def test_a_standalone_document_does_not_satisfy_the_repository_threshold():
    """One repository plus a matching loose document is a real and interesting fact — and it is not
    what this rule asks about. The fact stays; the candidate does not appear."""
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, scope_type="repository"),
                    occurrence(
                        scope_id=SCOPE_DOC,
                        scope_type="document",
                        artifact=ARTIFACT_B,
                        label="spec.pdf",
                    ),
                ],
            )
        ]
    )
    assert result.candidates == ()


def test_a_document_occurrence_is_not_carried_as_qualifying_evidence():
    # Two repositories qualify; the document occurrence is not evidence FOR THIS RULE, so it is not
    # presented as if it were. It remains visible through `lighthouse observations where`.
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, label="alpha"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
                    occurrence(
                        scope_id=SCOPE_DOC,
                        scope_type="document",
                        artifact=ARTIFACT_C,
                        label="spec.pdf",
                    ),
                ],
            )
        ]
    )
    candidate = result.candidates[0]
    assert candidate.distinct_repository_scope_count == 2
    assert len(candidate.supporting_occurrences) == 2
    assert all(o["scope_type"] == "repository" for o in candidate.supporting_occurrences)


def test_the_detector_does_not_mutate_the_facts_it_was_given():
    records = [
        occurrence(scope_id=SCOPE_A, label="alpha"),
        occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
    ]
    before = json.dumps(records, sort_keys=True)
    run([evidence(CONTENT_A, records)])
    assert json.dumps(records, sort_keys=True) == before


# -- ordering and determinism --------------------------------------------------------------


def test_candidates_are_ordered_by_content_entity_id():
    hi, lo = sorted([CONTENT_A, CONTENT_B], reverse=True)
    result = run(
        [
            evidence(
                hi,
                [
                    occurrence(scope_id=SCOPE_A, entity_id=hi),
                    occurrence(scope_id=SCOPE_B, entity_id=hi, artifact=ARTIFACT_B),
                ],
            ),
            evidence(
                lo,
                [
                    occurrence(scope_id=SCOPE_A, entity_id=lo),
                    occurrence(scope_id=SCOPE_B, entity_id=lo, artifact=ARTIFACT_B),
                    occurrence(scope_id=SCOPE_C, entity_id=lo, artifact=ARTIFACT_C),
                ],
            ),
        ]
    )
    ids = [c.subject["entity_id"] for c in result.candidates]
    assert ids == sorted(ids)


def test_output_is_never_sorted_by_occurrence_count():
    """The 3-occurrence candidate must NOT float to the top. Ordering by count is ranking, and
    ranking is the first thing a significance layer does."""
    few, many = sorted([CONTENT_A, CONTENT_B])
    result = run(
        [
            evidence(
                few,
                [
                    occurrence(scope_id=SCOPE_A, entity_id=few),
                    occurrence(scope_id=SCOPE_B, entity_id=few, artifact=ARTIFACT_B),
                ],
            ),
            evidence(
                many,
                [
                    occurrence(scope_id=SCOPE_A, entity_id=many),
                    occurrence(scope_id=SCOPE_B, entity_id=many, artifact=ARTIFACT_B),
                    occurrence(scope_id=SCOPE_C, entity_id=many, artifact=ARTIFACT_C),
                ],
            ),
        ]
    )
    counts = [c.distinct_repository_scope_count for c in result.candidates]
    assert counts == [2, 3]  # identity order, which happens to put the SMALLER one first
    assert counts != sorted(counts, reverse=True)


def test_the_same_facts_in_a_different_row_order_produce_byte_identical_candidates():
    a = occurrence(scope_id=SCOPE_A, label="alpha")
    b = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta")
    c = occurrence(scope_id=SCOPE_C, artifact=ARTIFACT_C, label="gamma")

    forward = run([evidence(CONTENT_A, [a, b, c])]).candidates[0]
    shuffled = run([evidence(CONTENT_A, [c, a, b])]).candidates[0]

    assert forward.candidate_id == shuffled.candidate_id
    assert json.dumps(forward.to_dict(), sort_keys=True) == json.dumps(
        shuffled.to_dict(), sort_keys=True
    )


def test_two_runs_over_the_same_facts_are_identical():
    items = [
        evidence(
            CONTENT_A,
            [
                occurrence(scope_id=SCOPE_A, label="alpha"),
                occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
            ],
        )
    ]
    assert run(items).candidates[0].candidate_id == run(items).candidates[0].candidate_id


# -- coverage and incomplete evidence ------------------------------------------------------


def test_incomplete_repository_coverage_is_disclosed_not_suppressed():
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, coverage_state="incomplete", label="alpha"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
                ],
            )
        ]
    )
    candidate = result.candidates[0]
    assert "repository_inventory_incomplete" in candidate.limitations
    assert candidate.distinct_repository_scope_count == 2


def test_incomplete_coverage_is_not_converted_into_absence():
    # "I did not account for every file in this repo" must never become "that file is not there".
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, coverage_state="incomplete"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, coverage_state="unknown"),
                ],
            )
        ]
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].counterevidence == ()


def test_a_truncated_view_of_the_evidence_produces_no_candidate():
    """The read boundary bounds its results. A candidate built from a bounded view would count
    distinct scopes over a set it does not know is complete — and would silently omit occurrences.

    03A's rule, one layer up: a truncated list reads exactly like a complete one, so withhold it.
    """
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A, label="alpha"),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B, label="beta"),
                ],
                complete=False,
            )
        ]
    )
    assert result.candidates == ()
    assert result.skipped == ((CONTENT_A, "evidence_scan_truncated"),)


def test_the_skip_vocabulary_is_closed():
    assert frozenset({"evidence_scan_truncated"}) == detector.ALLOWED_SKIP_REASONS


# -- fail closed ---------------------------------------------------------------------------


def test_an_invalid_fact_fails_closed():
    bad = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B)
    del bad["source_artifact_digest"]
    with pytest.raises(PatternCandidateError):
        run([evidence(CONTENT_A, [occurrence(scope_id=SCOPE_A), bad])])


def test_an_occurrence_carrying_a_forged_evaluative_field_fails_closed():
    bad = occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B)
    bad["importance"] = 0.99
    with pytest.raises(PatternCandidateError):
        run([evidence(CONTENT_A, [occurrence(scope_id=SCOPE_A), bad])])


def test_an_occurrence_for_the_wrong_subject_fails_closed():
    with pytest.raises(PatternCandidateError):
        run(
            [
                evidence(
                    CONTENT_A,
                    [
                        occurrence(scope_id=SCOPE_A, entity_id=CONTENT_A),
                        occurrence(scope_id=SCOPE_B, entity_id=CONTENT_B, artifact=ARTIFACT_B),
                    ],
                )
            ]
        )


def test_a_non_content_entity_fails_closed():
    with pytest.raises(PatternCandidateError):
        run(
            [
                evidence(
                    "technology:docker",
                    [
                        occurrence(scope_id=SCOPE_A, entity_id="technology:docker"),
                        occurrence(
                            scope_id=SCOPE_B, entity_id="technology:docker", artifact=ARTIFACT_B
                        ),
                    ],
                )
            ]
        )


# -- ownership -----------------------------------------------------------------------------


def test_the_owning_profile_is_repository_capability_discovery():
    assert detector.OWNER_PROFILE_ID == "repository_capability_discovery"
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B),
                ],
            )
        ]
    )
    assert result.candidates[0].profile_id == "repository_capability_discovery"


def test_the_detector_declares_a_rule_and_a_threshold_it_does_not_invent():
    assert detector.DETECTOR_ID == "repository_exact_content_recurrence"
    assert detector.CANDIDATE_TYPE == "repository_exact_content_recurrence"
    assert detector.MIN_DISTINCT_REPOSITORY_SCOPES == 2
    assert detector.RULE_ID == "exact_content_recurrence_across_repository_scopes"


def test_the_detectors_identity_is_a_member_of_the_contracts_closed_vocabulary():
    from modules import pattern_candidate as pc

    assert detector.OWNER_PROFILE_ID in pc.ALLOWED_PROFILE_IDS
    assert detector.DETECTOR_ID in pc.ALLOWED_DETECTOR_IDS
    assert detector.CANDIDATE_TYPE in pc.ALLOWED_CANDIDATE_TYPES
    assert detector.RULE_ID in pc.ALLOWED_RULE_IDS
    assert detector.RULE_PARAMETERS == pc.REQUIRED_RULE_PARAMETERS


# -- what a candidate must never say -------------------------------------------------------


def test_no_candidate_states_a_conclusion():
    result = run(
        [
            evidence(
                CONTENT_A,
                [
                    occurrence(scope_id=SCOPE_A),
                    occurrence(scope_id=SCOPE_B, artifact=ARTIFACT_B),
                ],
            )
        ]
    )
    body = json.dumps(result.candidates[0].to_dict())
    for conclusion in (
        "invention",
        "patent",
        "duplicate_work",
        "copied",
        "reusable",
        "independently",
        "suspicious",
    ):
        assert conclusion not in body.lower(), conclusion
    # "copying" appears exactly once, inside the limitation that says it is NOT proven.
    assert "recurrence_does_not_prove_copying" in body


# -- purity --------------------------------------------------------------------------------


def test_the_detector_is_pure():
    """No filesystem, no Git, no database, no network, no model, no clock.

    A clock is included on purpose: `created_at` is injected, so a detector run is a pure function
    of the facts it was handed — which is what makes "same facts, same candidate_id" provable rather
    than merely observed.
    """
    tree = ast.parse(Path(detector.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "os",
        "sqlite3",
        "subprocess",
        "pathlib",
        "socket",
        "httpx",
        "requests",
        "anthropic",
        "datetime",
        "time",
        "random",
    }
    assert not (imported & forbidden), imported & forbidden
    assert "modules.db" not in imported
    assert "modules.observation_query" not in imported  # the SERVICE reads facts, not the rule
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "typing",
        "modules.pattern_candidate",
    }, imported


def test_the_detector_declares_no_evaluative_name():
    tree = ast.parse(Path(detector.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            lowered = node.name.lower()
            assert not any(
                bad in lowered
                for bad in ("score", "rank", "importance", "priority", "severity", "significan")
            ), node.name


# -- adversarial pass 2: the false-independence attack ---------------------------------------
#
# This is the threat model. For a solo operator reusing their own boilerplate, the DOMINANT reason
# two repositories hold identical bytes is not independent invention — it is a clone, a fork, a
# template, a vendored file, a generated file, or one project that was split in two. The detector
# must be unable to tell those apart, and must say so, rather than presenting coincidence as a
# pattern. Every construction below produces the SAME candidate shape, with independence unknown.


def _candidate_for(*labels_and_artifacts):
    occurrences = [
        occurrence(scope_id=scope, artifact=artifact, label=label)
        for scope, artifact, label in labels_and_artifacts
    ]
    return run([evidence(CONTENT_A, occurrences)]).candidates[0]


@pytest.mark.parametrize(
    "story",
    [
        "two clones of one upstream",
        "a fork and its parent",
        "a project scaffolded from a template",
        "a vendored third-party file",
        "a machine-generated lockfile",
        "one monorepo split into two repositories",
        "two genuinely independent implementations",
    ],
)
def test_every_explanation_produces_the_same_candidate_and_none_is_chosen(story):
    """Seven different histories. One identical candidate. The detector cannot distinguish them —
    and the artifact says exactly that, rather than picking the flattering one."""
    candidate = _candidate_for(
        (SCOPE_A, ARTIFACT_A, "alpha"),
        (SCOPE_B, ARTIFACT_B, "beta"),
    )
    body = json.dumps(candidate.to_dict()).lower()

    # It says only what happened: these exact bytes were observed in these two repository scopes.
    assert candidate.distinct_repository_scope_count == 2
    assert candidate.rule_satisfied is True

    # And it refuses every one of the seven explanations, including the flattering one.
    assert "source_independence_not_assessed" in candidate.unknowns
    assert "repository_lineage_not_assessed" in candidate.unknowns
    assert "independent_creation_not_assessed" in candidate.unknowns
    assert "generated_file_status_unknown" in candidate.unknowns
    assert "vendored_file_status_unknown" in candidate.unknowns
    assert "template_or_boilerplate_status_unknown" in candidate.unknowns
    assert "recurrence_does_not_prove_copying" in candidate.limitations
    assert "recurrence_does_not_prove_independent_implementation" in candidate.limitations

    # No token in the artifact asserts any of the seven. Wherever such a word appears, it appears
    # as a member of the closed DENIAL vocabulary — the mandatory unknowns and limitations, which
    # are denials by construction. Tying the check to the real vocabulary (rather than to a string
    # pattern invented here) is what makes it impossible to satisfy by renaming a code.
    from modules import pattern_candidate as pc

    denials = {code.lower() for code in pc.ALLOWED_UNKNOWN_CODES | pc.ALLOWED_LIMITATION_CODES}
    for verdict in ("clone", "fork", "template", "vendor", "generated", "monorepo", "independent"):
        for token in [t for t in body.split('"') if verdict in t]:
            assert token in denials, (story, verdict, token)


def test_scope_count_is_never_an_independence_count():
    """Three scopes is not "three independent sources". It is three places the same bytes were seen,
    and it is exactly as consistent with one file copied twice."""
    candidate = _candidate_for(
        (SCOPE_A, ARTIFACT_A, "alpha"),
        (SCOPE_B, ARTIFACT_B, "beta"),
        (SCOPE_C, ARTIFACT_C, "gamma"),
    )
    assert candidate.distinct_repository_scope_count == 3
    assert "source_independence_not_assessed" in candidate.unknowns
    assert candidate.counterevidence == ()  # and the count did NOT become corroboration


def test_more_sensors_are_not_more_sources():
    """One occurrence established by two sensors is still one source. Sensors are how a fact was
    determined, not how many places it came from."""
    record = occurrence(scope_id=SCOPE_A)
    record["sensors"] = ["filesystem", "git_metadata"]
    record["supporting_observations"].append(
        {
            "observation_type": "content.files",
            "sensor": "git_metadata",
            "confidence": 1.0,
            "observed_at": "2026-07-14T11:00:00+00:00",
        }
    )
    assert run([evidence(CONTENT_A, [record])]).candidates == ()
