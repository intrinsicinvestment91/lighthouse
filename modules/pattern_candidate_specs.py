"""Closed, code-owned detector specifications for the pattern candidate contract (LHP-INTEL-04C).

`pattern_candidate.v1` began life shaped for exactly one detector: its `validate()` inlined one
detector's evidence shape, unknown vocabulary, limitation vocabulary and candidate-key material.
That neutral spine is reusable across profiles; those four things are not. This module is where
they become **detector-owned** — without letting a candidate own them.

The mechanism is a **registry**, not a plugin system. Each `DetectorCandidateSpec` is a frozen
record built here, in code; `REGISTRY` maps one exact `detector_id` to one exact spec; `get_spec`
resolves it fail-closed. There is no dynamic import, no configuration-file registry, no
model-generated schema, and no callable supplied by candidate data — the input names a detector,
and the code decides what that detector's candidates may look like. A candidate that names an
unknown detector, or whose profile or type disagrees with its detector's spec, is refused.

`pattern_candidate.PatternCandidate.validate()` calls `spec.validate_detector(candidate)` after its
own spine checks; `_raw()` calls `spec.serialize_extra`; `compute_key()` calls `spec.key_material`;
`from_dict()` closes the field set against ``SPINE_FIELDS | spec.extra_fields``. The spine keeps
every neutral invariant (identity, digest, authority pins, the no-score guarantee, the disposition
loop).

**Load order** (a deliberate cycle, broken by one lazy import): this module imports
``pattern_candidate`` and ``scientific_source_disappearance`` eagerly; ``pattern_candidate`` imports
*this* module lazily, inside the four methods above, so neither needs the other at import time.

**Pure**: no database, no filesystem, no network, no clock, no LLM.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from modules import pattern_candidate as pc
from modules import public_source_check as psc
from modules import scientific_source_disappearance as ssd

# The neutral spine and the detector-owned field set live in `pattern_candidate` (`SPINE_FIELDS`,
# `DETECTOR_OWNED_FIELDS`); a spec only declares which of the latter it uses, via `extra_fields`.


@dataclass(frozen=True)
class DetectorCandidateSpec:
    """One detector's ownership of the parts of a candidate the spine deliberately does not fix.

    Everything here is code-owned and immutable. ``validate_detector`` raises
    ``PatternCandidateValidationError`` on any violation; ``serialize_extra`` returns exactly the
    detector's extra top-level fields (so the spine can serialize them without knowing their shape);
    ``key_material`` returns the dict the candidate_key digests (so two locators serving the same
    bytes do not collide when a detector needs them not to).
    """

    detector_id: str
    profile_id: str
    candidate_type: str
    extra_fields: frozenset[str]
    required_unknowns: frozenset[str]
    conditional_unknowns: frozenset[str]
    required_limitations: frozenset[str]
    conditional_limitations: frozenset[str]
    serialize_extra: Callable[[Any], dict[str, Any]]
    key_material: Callable[[Any], dict[str, Any]]
    validate_detector: Callable[[Any], None]


# -- the repository detector (LHP-INTEL-03B), reproduced exactly -----------------------------
#
# Every check below is the repository half of the pre-04C `PatternCandidate.validate()`, moved
# verbatim so a repository candidate validates and serializes byte-for-byte as it always did. The
# helpers and vocabularies still live in `pattern_candidate`; this spec only wires them.


def _repository_serialize_extra(candidate: Any) -> dict[str, Any]:
    return {
        "supporting_occurrences": [dict(item) for item in candidate.supporting_occurrences],
        "distinct_repository_scope_count": candidate.distinct_repository_scope_count,
    }


def _repository_key_material(candidate: Any) -> dict[str, Any]:
    return {
        "profile_id": candidate.profile_id,
        "detector_id": candidate.detector_id,
        "candidate_type": candidate.candidate_type,
        "subject_entity_id": candidate.subject.get("entity_id"),
    }


def _validate_repository(candidate: Any) -> None:
    entity_id = candidate.subject["entity_id"]

    pc._require(
        bool(candidate.supporting_occurrences), "a candidate must carry supporting evidence"
    )
    for record in candidate.supporting_occurrences:
        pc._validate_occurrence(record, subject_entity_id=entity_id)

    ordered = sorted(candidate.supporting_occurrences, key=pc._occurrence_order)
    pc._require(
        [pc._occurrence_order(r) for r in candidate.supporting_occurrences]
        == [pc._occurrence_order(r) for r in ordered],
        "supporting evidence is not in canonical order",
    )

    pc._require(
        candidate.rule_id in pc.ALLOWED_RULE_IDS, f"unsupported rule: {candidate.rule_id!r}"
    )
    pc._require(
        isinstance(candidate.rule_version, int)
        and not isinstance(candidate.rule_version, bool)
        and candidate.rule_version >= 1,
        "rule_version must be a positive integer",
    )
    pc._require(
        dict(candidate.rule_parameters) == pc.REQUIRED_RULE_PARAMETERS,
        f"unsupported rule parameters: {dict(candidate.rule_parameters)!r}",
    )

    minimum = pc.REQUIRED_RULE_PARAMETERS["min_distinct_repository_scopes"]
    scopes = pc._distinct_repository_scopes(candidate.supporting_occurrences)
    pc._require(
        candidate.distinct_repository_scope_count == len(scopes),
        "distinct_repository_scope_count does not match the evidence",
    )
    pc._require(
        len(scopes) >= minimum,
        f"the rule requires at least {minimum} distinct repository scopes, "
        f"and this evidence covers {len(scopes)}",
    )

    pc._require(
        set(candidate.unknowns) == set(pc.REQUIRED_UNKNOWN_CODES),
        "the mandatory unknowns are missing or unrecognized",
    )
    pc._require(
        set(candidate.limitations) <= pc.ALLOWED_LIMITATION_CODES, "an unrecognized limitation code"
    )
    pc._require(
        set(pc.REQUIRED_LIMITATION_CODES) <= set(candidate.limitations),
        "the mandatory limitations are missing",
    )
    pc._require(
        (pc.LIMITATION_INVENTORY_INCOMPLETE in candidate.limitations)
        == any(r["coverage_state"] != "complete" for r in candidate.supporting_occurrences),
        "the coverage limitation does not match the evidence's coverage",
    )


REPOSITORY_SPEC = DetectorCandidateSpec(
    detector_id="repository_exact_content_recurrence",
    profile_id="repository_capability_discovery",
    candidate_type="repository_exact_content_recurrence",
    extra_fields=frozenset({"supporting_occurrences", "distinct_repository_scope_count"}),
    required_unknowns=pc.REQUIRED_UNKNOWN_CODES,
    conditional_unknowns=frozenset(),
    required_limitations=pc.REQUIRED_LIMITATION_CODES,
    conditional_limitations=frozenset({pc.LIMITATION_INVENTORY_INCOMPLETE}),
    serialize_extra=_repository_serialize_extra,
    key_material=_repository_key_material,
    validate_detector=_validate_repository,
)


# -- the scientific preservation detector (LHP-INTEL-04C) ------------------------------------
#
# The scientific detector's vocabulary and evidence shape live in `scientific_source_disappearance`
# (the domain module that also runs the rule); this spec validates a finished candidate against it.


def _scientific_serialize_extra(candidate: Any) -> dict[str, Any]:
    return {
        "evidence": dict(candidate.evidence)
        if isinstance(candidate.evidence, Mapping)
        else candidate.evidence
    }


def _scientific_key_material(candidate: Any) -> dict[str, Any]:
    # Keyed on the locator as well as the subject: two locators serving the same bytes are two
    # separate preservation risks and must not collapse into one logical candidate.
    evidence = candidate.evidence if isinstance(candidate.evidence, Mapping) else {}
    locator = evidence.get("locator") if isinstance(evidence.get("locator"), Mapping) else {}
    return {
        "profile_id": candidate.profile_id,
        "detector_id": candidate.detector_id,
        "candidate_type": candidate.candidate_type,
        "locator_id": locator.get("locator_id"),
        "subject_entity_id": candidate.subject.get("entity_id"),
    }


def _validate_scientific(candidate: Any) -> None:
    evidence = candidate.evidence
    pc._require(isinstance(evidence, Mapping), "scientific evidence must be a mapping")
    pc._closed_keys(evidence, ssd.EVIDENCE_SECTIONS, "scientific evidence")
    for section, keys in ssd.EVIDENCE_SECTION_KEYS.items():
        pc._closed_keys(evidence[section], keys, f"evidence.{section}")

    subject_id = candidate.subject["entity_id"]
    subject_block = evidence["subject"]
    pc._require(
        subject_block["content_entity_id"] == subject_id,
        "the evidence subject disagrees with the candidate subject",
    )
    pc._require(
        subject_id == "content:" + str(subject_block["content_digest"]),
        "the subject is not content:<its digest>",
    )
    pc._require(
        subject_block["content_digest_algorithm"] == ssd.CONTENT_DIGEST_ALGORITHM,
        "the subject digest algorithm must be sha256",
    )

    pc._require(
        evidence["source"]["source_class"] == psc.SOURCE_CLASS,
        "the source class must be public_source",
    )
    pc._require(
        evidence["source"]["privacy_class"] == psc.PRIVACY_CLASS,
        "the privacy class must be public",
    )

    trigger = evidence["triggering_not_found"]
    pc._require(trigger["outcome"] == psc.NOT_FOUND, "the triggering check must be a not_found")
    pc._require(
        trigger["http_status"] in ssd.NOT_FOUND_STATUSES,
        "the triggering not_found must be HTTP 404 or 410",
    )

    temporal = evidence["temporal_rule"]
    pc._require(temporal["temporal_rule_satisfied"] is True, "temporal_rule_satisfied must be True")
    pc._require(
        temporal["minimum_elapsed_seconds"] == ssd.MIN_ELAPSED_SECONDS,
        "unexpected minimum elapsed seconds",
    )
    elapsed = temporal["elapsed_seconds"]
    pc._require(
        isinstance(elapsed, int)
        and not isinstance(elapsed, bool)
        and elapsed >= ssd.MIN_ELAPSED_SECONDS,
        "elapsed_seconds is below the minimum",
    )

    outage_status = evidence["outage_context"]["outage_context_status"]
    pc._require(
        outage_status in {ssd.OUTAGE_LIMITED, ssd.OUTAGE_PASSED},
        "a blocked or unknown outage context cannot carry a candidate",
    )

    pc._require(evidence["rule_result"]["rule_id"] == ssd.RULE_ID, "unexpected rule id in evidence")
    pc._require(evidence["rule_result"]["rule_satisfied"] is True, "rule_satisfied must be True")

    pc._require(candidate.rule_id == ssd.RULE_ID, f"unsupported rule: {candidate.rule_id!r}")
    pc._require(
        isinstance(candidate.rule_version, int)
        and not isinstance(candidate.rule_version, bool)
        and candidate.rule_version >= 1,
        "rule_version must be a positive integer",
    )
    pc._require(
        dict(candidate.rule_parameters) == ssd.SCIENTIFIC_RULE_PARAMETERS,
        f"unsupported rule parameters: {dict(candidate.rule_parameters)!r}",
    )

    has_local = False
    for item in candidate.counterevidence:
        pc._require(isinstance(item, Mapping), "a counterevidence item must be a mapping")
        kind = item.get("type")
        pc._require(kind in ssd.COUNTEREVIDENCE_KEYS, f"unknown counterevidence type: {kind!r}")
        pc._closed_keys(item, ssd.COUNTEREVIDENCE_KEYS[kind], f"counterevidence[{kind}]")
        if kind == ssd.CE_STORED_LOCAL_COPY:
            has_local = True

    expected_unknowns = set(ssd.REQUIRED_UNKNOWNS)
    expected_limitations = set(ssd.REQUIRED_LIMITATIONS)
    if outage_status == ssd.OUTAGE_LIMITED:
        expected_unknowns.add(ssd.CONDITIONAL_UNKNOWN_OUTAGE)
        expected_limitations.add(ssd.CONDITIONAL_LIMITATION_OUTAGE)
    if has_local:
        expected_unknowns.add(ssd.CONDITIONAL_UNKNOWN_LOCAL)
    pc._require(
        set(candidate.unknowns) == expected_unknowns,
        "the scientific unknowns do not match the evidence",
    )
    pc._require(
        set(candidate.limitations) == expected_limitations,
        "the scientific limitations do not match the evidence",
    )


SCIENTIFIC_SPEC = DetectorCandidateSpec(
    detector_id=ssd.DETECTOR_ID,
    profile_id=ssd.OWNER_PROFILE_ID,
    candidate_type=ssd.CANDIDATE_TYPE,
    extra_fields=frozenset({"evidence"}),
    required_unknowns=ssd.REQUIRED_UNKNOWNS,
    conditional_unknowns=frozenset({ssd.CONDITIONAL_UNKNOWN_OUTAGE, ssd.CONDITIONAL_UNKNOWN_LOCAL}),
    required_limitations=ssd.REQUIRED_LIMITATIONS,
    conditional_limitations=frozenset({ssd.CONDITIONAL_LIMITATION_OUTAGE}),
    serialize_extra=_scientific_serialize_extra,
    key_material=_scientific_key_material,
    validate_detector=_validate_scientific,
)


# -- the registry ----------------------------------------------------------------------------

_SPECS: tuple[DetectorCandidateSpec, ...] = (REPOSITORY_SPEC, SCIENTIFIC_SPEC)

#: One exact detector id -> one exact spec. Built here, in code; never from input, a file, or a
#: model.
REGISTRY: Mapping[str, DetectorCandidateSpec] = {spec.detector_id: spec for spec in _SPECS}


def get_spec(detector_id: Any) -> DetectorCandidateSpec:
    """Resolve a detector's spec, fail-closed. An unknown detector is refused, never defaulted."""
    if not isinstance(detector_id, str) or detector_id not in REGISTRY:
        raise pc.PatternCandidateValidationError(
            f"unsupported detector: {detector_id!r} (known: {', '.join(sorted(REGISTRY))})"
        )
    return REGISTRY[detector_id]


def allowed_detector_ids() -> frozenset[str]:
    """The closed set of detector ids that have a spec. The truth a CLI selector should mirror."""
    return frozenset(REGISTRY)
