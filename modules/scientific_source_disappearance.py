"""Disappearing scientific source detector (LHP-INTEL-04C) — a pure, profile-owned rule engine.

Owned by the **Scientific Preservation** profile. It reads validated public-source check facts (the
04B ledger) and asks one conservative question per admitted locator:

    A previously available public source's most recent completed check returned a narrow 404/410
    not-found, at least seven days after the last time it was seen, and not amid a broad outage.
    Should an operator look?

And it answers only that. It does **not** say the source permanently disappeared, was removed,
suppressed, or is important; that a copy is authoritative or independent; or that any preservation,
publication, disclosure, or release is authorized. A not-found is an HTTP answer, never a verdict.

Three ways the rule could quietly become wrong, and the shape that prevents each:

* **Reading a recovery as a disappearance.** Only the *latest* completed check can trigger, and only
  when it is a narrow not-found. A source that came back has an available latest check, and is
  reported ``suppressed_by_recovery`` — never a candidate.
* **Reading an outage as a disappearance.** A deterministic, non-scoring defense: if the triggering
  run's *other* locators are mostly infrastructure failures, the candidate is blocked. Where the
  context is too small to judge, that is disclosed as an unknown, not hidden.
* **Confusing the locator with the bytes.** The subject is the exact ``content:<sha256>`` last
served,
  and the candidate is keyed on ``(locator, subject)`` — two locators serving the same bytes are two
  separate risks, and a locator that later serves *different* bytes is a different one.

**Pure**: no database, no filesystem, no network (it never fetches — it reads facts a fetch already
recorded), no clock (``created_at`` is injected), and no LLM. It builds no candidate object: it
returns
structured inputs, and the service assembles and validates the candidate through the detector spec.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules import public_source_check as psc

OWNER_PROFILE_ID = "scientific_preservation"
DETECTOR_ID = "scientific_source_disappearance_review"
DETECTOR_VERSION = "scientific_source_disappearance_review/1"
CANDIDATE_TYPE = "disappearing_scientific_source"

RULE_ID = "public_source_disappearance_review"
RULE_VERSION = 1
OUTAGE_RULE_VERSION = 1

#: Seven days, as a declared v1 threshold — a conservative choice, not a scientific truth.
MIN_ELAPSED_SECONDS = 7 * 24 * 3600

CONTENT_DIGEST_ALGORITHM = "sha256"
#: The only HTTP statuses that are a factual not-found. 403, 451, 5xx and the rest are their own
#: facts.
NOT_FOUND_STATUSES = frozenset({404, 410})

#: Outcomes that indicate the *fetcher* could not reach or complete, not that a source answered. A
#: run
#: dominated by these is a broad outage, and a not-found within it is not trustworthy evidence of
#: disappearance. Deliberately excludes not_found / auth / rate_limited / client_error /
#: policy_refused
#: / redirected_failure — those are answers, not infrastructure. server_error is excluded too: a 5xx
#: is
#: the origin responding, not the path to it failing.
INFRASTRUCTURE_FAILURE_OUTCOMES = frozenset(
    {
        psc.DNS_FAILURE,
        psc.TLS_FAILURE,
        psc.CONNECTION_FAILURE,
        psc.TIMEOUT,
        psc.ADAPTER_FAILURE,
        psc.UNKNOWN,
        psc.RUN_BYTE_BUDGET_EXCEEDED,
        psc.INCOMPLETE_RESPONSE,
    }
)

OUTAGE_BLOCKED = "blocked_broad_failure_context"
OUTAGE_LIMITED = "limited_outage_context"
OUTAGE_PASSED = "passed_outage_context"

#: Mandatory on every candidate. An unknown that can be omitted is one that quietly becomes a "no".
REQUIRED_UNKNOWNS = frozenset(
    {
        "permanent_disappearance_not_established",
        "source_owner_intent_unknown",
        "scientific_importance_not_assessed",
        "authenticity_not_assessed",
        "archive_search_coverage_incomplete",
        "mirror_search_coverage_incomplete",
        "legal_preservation_rights_unknown",
        "source_relocation_not_fully_assessed",
        "future_recovery_possible",
        "publication_eligibility_not_assessed",
    }
)
CONDITIONAL_UNKNOWN_OUTAGE = "broad_outage_context_limited"
CONDITIONAL_UNKNOWN_LOCAL = "local_copy_currentness_unknown"
ALLOWED_UNKNOWNS = REQUIRED_UNKNOWNS | {CONDITIONAL_UNKNOWN_OUTAGE, CONDITIONAL_UNKNOWN_LOCAL}

#: What a narrow not-found does not prove. All mandatory; the outage one is added under limited
#: context.
REQUIRED_LIMITATIONS = frozenset(
    {
        "not_found_does_not_prove_permanent_disappearance",
        "source_configuration_or_relocation_may_explain_not_found",
        "mirror_and_archive_coverage_is_incomplete",
        "exact_byte_identity_proves_exact_bytes_only",
        "public_copy_does_not_prove_authenticity",
        "local_copy_fact_may_not_reflect_current_filesystem_state",
        "candidate_is_not_a_finding",
        "candidate_requires_operator_review",
        "candidate_does_not_authorize_preservation",
        "candidate_does_not_authorize_publication",
        "candidate_does_not_authorize_disclosure",
        "candidate_does_not_authorize_release",
        "candidate_does_not_contact_gateway",
        "candidate_does_not_enroll_dead_mans_switch",
    }
)
CONDITIONAL_LIMITATION_OUTAGE = "broad_outage_context_is_limited"
ALLOWED_LIMITATIONS = REQUIRED_LIMITATIONS | {CONDITIONAL_LIMITATION_OUTAGE}

#: The operator reads sentences; the artifact stores codes. Resolved here so the two cannot drift.
UNKNOWN_STATEMENTS: dict[str, str] = {
    "permanent_disappearance_not_established": (
        "Whether this source has permanently disappeared has not been established."
    ),
    "source_owner_intent_unknown": "Whether the source owner intended this is unknown.",
    "scientific_importance_not_assessed": (
        "Whether this source matters scientifically is unassessed."
    ),
    "authenticity_not_assessed": "Whether the prior bytes are authentic has not been assessed.",
    "archive_search_coverage_incomplete": "No exhaustive archive search was performed.",
    "mirror_search_coverage_incomplete": "No exhaustive mirror search was performed.",
    "legal_preservation_rights_unknown": "Any legal right to preserve this source is unknown.",
    "source_relocation_not_fully_assessed": (
        "Whether the source merely moved has not been fully assessed."
    ),
    "future_recovery_possible": "The source may still recover in future.",
    "publication_eligibility_not_assessed": "Whether anything here may be published is unassessed.",
    CONDITIONAL_UNKNOWN_OUTAGE: (
        "Too few unrelated checks ran alongside this one to judge a broad outage."
    ),
    CONDITIONAL_UNKNOWN_LOCAL: "Whether the known local copy still exists on disk now is unknown.",
}

LIMITATION_STATEMENTS: dict[str, str] = {
    "not_found_does_not_prove_permanent_disappearance": (
        "A not-found response does not prove permanent disappearance."
    ),
    "source_configuration_or_relocation_may_explain_not_found": (
        "A configuration change or relocation may explain the not-found."
    ),
    "mirror_and_archive_coverage_is_incomplete": "Mirror and archive coverage is incomplete.",
    "exact_byte_identity_proves_exact_bytes_only": "Exact byte identity proves only exact bytes.",
    "public_copy_does_not_prove_authenticity": "A public copy does not prove authenticity.",
    "local_copy_fact_may_not_reflect_current_filesystem_state": (
        "A stored local-copy fact may not reflect the current filesystem."
    ),
    "candidate_is_not_a_finding": "This candidate is not a finding.",
    "candidate_requires_operator_review": "This candidate requires operator review.",
    "candidate_does_not_authorize_preservation": "This candidate authorizes no preservation.",
    "candidate_does_not_authorize_publication": "This candidate authorizes no publication.",
    "candidate_does_not_authorize_disclosure": "This candidate authorizes no disclosure.",
    "candidate_does_not_authorize_release": "This candidate authorizes no release.",
    "candidate_does_not_contact_gateway": "This candidate contacts no Gateway.",
    "candidate_does_not_enroll_dead_mans_switch": "This candidate enrolls no dead-man's switch.",
    CONDITIONAL_LIMITATION_OUTAGE: (
        "The broad-outage context is limited: fewer than three unrelated checks were available."
    ),
}

#: Why a locator produced no candidate. Each names one fact about the history, never a judgement.
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_PRIOR_SUCCESS = "no_prior_success"
REASON_LATEST_NOT_TRIGGERING = "latest_outcome_not_triggering"
REASON_INSUFFICIENT_ELAPSED = "insufficient_elapsed_time"
REASON_SUPPRESSED_BY_RECOVERY = "suppressed_by_recovery"
REASON_BLOCKED_OUTAGE = "blocked_broad_failure_context"
REASON_INVALID_EVIDENCE = "invalid_evidence"
NON_CANDIDATE_REASONS = frozenset(
    {
        REASON_INSUFFICIENT_HISTORY,
        REASON_NO_PRIOR_SUCCESS,
        REASON_LATEST_NOT_TRIGGERING,
        REASON_INSUFFICIENT_ELAPSED,
        REASON_SUPPRESSED_BY_RECOVERY,
        REASON_BLOCKED_OUTAGE,
        REASON_INVALID_EVIDENCE,
    }
)

#: Direct, exact-byte counterevidence types. Nothing guessed, semantic, or title-matched is here.
CE_CURRENT_PUBLIC_MIRROR = "current_public_exact_mirror"
CE_HISTORICAL_PUBLIC_COPY = "historical_public_exact_copy"
CE_STORED_LOCAL_COPY = "stored_local_exact_copy"

#: The closed shape of a candidate's `evidence` block: exactly these sections, each with exactly its
#: own keys. The detector produces this and the detector spec validates it, so the two cannot drift.
EVIDENCE_SECTIONS = frozenset(
    {
        "source",
        "locator",
        "subject",
        "prior_success",
        "triggering_not_found",
        "temporal_rule",
        "outage_context",
        "rule_result",
        "provenance",
    }
)
EVIDENCE_SECTION_KEYS: dict[str, frozenset[str]] = {
    "source": frozenset({"source_id", "source_class", "privacy_class"}),
    "locator": frozenset({"locator_id", "canonical_locator"}),
    "subject": frozenset({"content_entity_id", "content_digest", "content_digest_algorithm"}),
    "prior_success": frozenset(
        {
            "check_id",
            "run_id",
            "checked_at",
            "outcome",
            "http_status",
            "adapter_id",
            "adapter_version",
            "policy_digest",
            "artifact_digest",
            "content_digest",
            "content_digest_algorithm",
            "body_size_bytes",
            "content_type",
        }
    ),
    "triggering_not_found": frozenset(
        {
            "check_id",
            "run_id",
            "checked_at",
            "outcome",
            "http_status",
            "adapter_id",
            "adapter_version",
            "policy_digest",
            "artifact_digest",
        }
    ),
    "temporal_rule": frozenset(
        {
            "prior_success_checked_at",
            "triggering_not_found_checked_at",
            "elapsed_seconds",
            "minimum_elapsed_seconds",
            "temporal_rule_satisfied",
        }
    ),
    "outage_context": frozenset(
        {
            "triggering_run_id",
            "triggering_run_target_count",
            "unrelated_check_count",
            "infrastructure_failure_count",
            "infrastructure_failure_outcomes",
            "outage_context_status",
            "referenced_check_ids",
            "outage_rule_version",
        }
    ),
    "rule_result": frozenset(
        {
            "rule_id",
            "rule_version",
            "rule_satisfied",
            "minimum_elapsed_seconds",
            "not_found_statuses",
            "reason_codes",
        }
    ),
    "provenance": frozenset(
        {
            "check_artifact_digests",
            "triggering_run_id",
            "run_artifact_digest",
            "detector_version",
            "profile_owner",
        }
    ),
}

#: The closed shape of each counterevidence type. An unknown type, or an unknown key inside one, is
#: refused — counterevidence is copied verbatim from facts and would otherwise be an unwatched door.
COUNTEREVIDENCE_KEYS: dict[str, frozenset[str]] = {
    CE_CURRENT_PUBLIC_MIRROR: frozenset(
        {
            "type",
            "source_id",
            "locator_id",
            "canonical_locator",
            "check_id",
            "checked_at",
            "content_entity_id",
            "content_digest",
            "artifact_digest",
            "adapter_id",
            "adapter_version",
        }
    ),
    CE_HISTORICAL_PUBLIC_COPY: frozenset(
        {
            "type",
            "source_id",
            "locator_id",
            "canonical_locator",
            "check_id",
            "checked_at",
            "content_entity_id",
            "content_digest",
            "artifact_digest",
        }
    ),
    CE_STORED_LOCAL_COPY: frozenset(
        {
            "type",
            "scope_id",
            "scope_type",
            "scope_label",
            "source_artifact_digest",
            "derivation_version",
            "content_entity_id",
        }
    ),
}

#: The rule parameters a scientific candidate carries on the spine's `rule_parameters` field.
SCIENTIFIC_RULE_PARAMETERS: dict[str, Any] = {"minimum_elapsed_seconds": MIN_ELAPSED_SECONDS}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LocatorHistory:
    """One admitted locator's complete, validated check timeline, chronological ascending."""

    source_id: str
    locator_id: str
    canonical_locator: str
    checks: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ScientificDetectionInput:
    """Everything the rule reads. Assembled by the service from the ledger and the fact store — the
    detector itself queries nothing.

    ``local_occurrences`` maps a ``content:<sha256>`` entity id to the local repository/document
    occurrences of those exact bytes (for local-copy counterevidence). ``run_digests`` maps a run id
    to that run's artifact digest (for provenance). Both may be empty.
    """

    locators: tuple[LocatorHistory, ...]
    local_occurrences: Mapping[str, Sequence[Mapping[str, Any]]]
    run_digests: Mapping[str, str]
    created_at: str


@dataclass(frozen=True)
class ScientificCandidateInput:
    """One reviewable candidate's inputs, ready for the service to assemble and validate."""

    subject_content_entity_id: str
    locator_id: str
    evidence: dict[str, Any]
    unknowns: tuple[str, ...]
    limitations: tuple[str, ...]
    counterevidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ScientificDetectionResult:
    """What the rule found, and what it declined to make a candidate of, with the reason for
    each."""

    candidates: tuple[ScientificCandidateInput, ...]
    non_candidates: tuple[tuple[str, str], ...]


def detect_disappearing_sources(data: ScientificDetectionInput) -> ScientificDetectionResult:
    """Apply the one declared rule to stored facts. Deterministic, and ordered by identity.

    Candidates come back ordered by ``(locator_id, subject content id)`` — never by how many mirrors
    or failures they carry. Ordering by count is ranking, and ranking is where significance hides.
    """
    candidates: list[ScientificCandidateInput] = []
    non_candidates: list[tuple[str, str]] = []

    for location in sorted(
        data.locators, key=lambda item: (item.locator_id, item.canonical_locator)
    ):
        outcome = _evaluate_locator(location, data)
        if isinstance(outcome, ScientificCandidateInput):
            candidates.append(outcome)
        else:
            non_candidates.append((location.locator_id, outcome))

    candidates.sort(key=lambda c: (c.locator_id, c.subject_content_entity_id))
    return ScientificDetectionResult(
        candidates=tuple(candidates), non_candidates=tuple(non_candidates)
    )


def _evaluate_locator(
    location: LocatorHistory, data: ScientificDetectionInput
) -> ScientificCandidateInput | str:
    checks = location.checks
    if len(checks) < 2:
        return REASON_INSUFFICIENT_HISTORY

    # The store's total order — ascending (checked_at, check_id). Re-sorted defensively so the rule
    # never depends on the order the caller happened to pass.
    ordered = sorted(checks, key=_check_order)
    latest = ordered[-1]
    earlier = ordered[:-1]

    if not _is_triggering_not_found(latest):
        if latest.get("outcome") == psc.AVAILABLE and any(
            _is_triggering_not_found(check) for check in earlier
        ):
            # A not-found that later came back. There is no current disappearance to review.
            return REASON_SUPPRESSED_BY_RECOVERY
        return REASON_LATEST_NOT_TRIGGERING

    if (
        latest.get("source_class") != psc.SOURCE_CLASS
        or latest.get("privacy_class") != psc.PRIVACY_CLASS
    ):
        return REASON_INVALID_EVIDENCE

    prior = _most_recent_available(earlier)
    if prior is None:
        return REASON_NO_PRIOR_SUCCESS

    digest = prior.get("content_digest")
    if not (
        _is_sha256(digest) and prior.get("content_digest_algorithm") == CONTENT_DIGEST_ALGORITHM
    ):
        return REASON_INVALID_EVIDENCE

    elapsed = _elapsed_seconds(prior.get("checked_at"), latest.get("checked_at"))
    if elapsed is None:
        return REASON_INVALID_EVIDENCE
    if elapsed < MIN_ELAPSED_SECONDS:
        return REASON_INSUFFICIENT_ELAPSED

    all_checks = [check for item in data.locators for check in item.checks]
    outage = _assess_outage(latest.get("run_id"), location.locator_id, all_checks)
    if outage["outage_context_status"] == OUTAGE_BLOCKED:
        return REASON_BLOCKED_OUTAGE

    subject = "content:" + digest
    evidence = _build_evidence(location, prior, latest, elapsed, outage, data.run_digests)

    unknowns = set(REQUIRED_UNKNOWNS)
    limitations = set(REQUIRED_LIMITATIONS)
    if outage["outage_context_status"] == OUTAGE_LIMITED:
        unknowns.add(CONDITIONAL_UNKNOWN_OUTAGE)
        limitations.add(CONDITIONAL_LIMITATION_OUTAGE)

    counterevidence = _build_counterevidence(subject, digest, location, data)
    if any(item["type"] == CE_STORED_LOCAL_COPY for item in counterevidence):
        unknowns.add(CONDITIONAL_UNKNOWN_LOCAL)

    return ScientificCandidateInput(
        subject_content_entity_id=subject,
        locator_id=location.locator_id,
        evidence=evidence,
        unknowns=tuple(sorted(unknowns)),
        limitations=tuple(sorted(limitations)),
        counterevidence=tuple(counterevidence),
    )


# -- rule primitives -------------------------------------------------------------------------


def _check_order(check: Mapping[str, Any]) -> tuple[str, str]:
    return (str(check.get("checked_at")), str(check.get("check_id")))


def _is_triggering_not_found(check: Mapping[str, Any]) -> bool:
    return check.get("outcome") == psc.NOT_FOUND and check.get("http_status") in NOT_FOUND_STATUSES


def _most_recent_available(earlier: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for check in reversed(list(earlier)):
        if check.get("outcome") == psc.AVAILABLE:
            return check
    return None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.match(value))


def _elapsed_seconds(earlier_at: Any, later_at: Any) -> int | None:
    try:
        earlier = datetime.fromisoformat(earlier_at)
        later = datetime.fromisoformat(later_at)
    except (TypeError, ValueError):
        return None
    return int((later - earlier).total_seconds())


def _assess_outage(
    run_id: Any, triggering_locator_id: str, all_checks: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    run_checks = [check for check in all_checks if check.get("run_id") == run_id]

    # One representative per unrelated locator (dedupe by locator_id), so a repeated row cannot
    # inflate the context, and the triggering locator never judges its own disappearance.
    by_locator: dict[str, Mapping[str, Any]] = {}
    for check in run_checks:
        locator_id = check.get("locator_id")
        if locator_id == triggering_locator_id:
            continue
        existing = by_locator.get(locator_id)
        if existing is None or _check_order(check) > _check_order(existing):
            by_locator[locator_id] = check

    unrelated = list(by_locator.values())
    infra = [
        check for check in unrelated if check.get("outcome") in INFRASTRUCTURE_FAILURE_OUTCOMES
    ]
    count = len(unrelated)
    if count < 3:
        status = OUTAGE_LIMITED
    elif len(infra) * 3 >= count * 2:  # >= two-thirds, in exact integer arithmetic
        status = OUTAGE_BLOCKED
    else:
        status = OUTAGE_PASSED

    return {
        "triggering_run_id": run_id,
        "triggering_run_target_count": len({check.get("locator_id") for check in run_checks}),
        "unrelated_check_count": count,
        "infrastructure_failure_count": len(infra),
        "infrastructure_failure_outcomes": sorted({check.get("outcome") for check in infra}),
        "outage_context_status": status,
        "referenced_check_ids": sorted(str(check.get("check_id")) for check in unrelated),
        "outage_rule_version": OUTAGE_RULE_VERSION,
    }


def _check_ref(check: Mapping[str, Any], *, full: bool) -> dict[str, Any]:
    ref = {
        "check_id": check.get("check_id"),
        "run_id": check.get("run_id"),
        "checked_at": check.get("checked_at"),
        "outcome": check.get("outcome"),
        "http_status": check.get("http_status"),
        "adapter_id": check.get("adapter_id"),
        "adapter_version": check.get("adapter_version"),
        "policy_digest": check.get("policy_digest"),
        "artifact_digest": check.get("artifact_digest"),
    }
    if full:
        ref.update(
            {
                "content_digest": check.get("content_digest"),
                "content_digest_algorithm": check.get("content_digest_algorithm"),
                "body_size_bytes": check.get("body_size_bytes"),
                "content_type": check.get("content_type"),
            }
        )
    return ref


def _build_evidence(
    location: LocatorHistory,
    prior: Mapping[str, Any],
    latest: Mapping[str, Any],
    elapsed: int,
    outage: Mapping[str, Any],
    run_digests: Mapping[str, str],
) -> dict[str, Any]:
    digest = prior["content_digest"]
    return {
        "source": {
            "source_id": location.source_id,
            "source_class": latest.get("source_class"),
            "privacy_class": latest.get("privacy_class"),
        },
        "locator": {
            "locator_id": location.locator_id,
            "canonical_locator": location.canonical_locator,
        },
        "subject": {
            "content_entity_id": "content:" + digest,
            "content_digest": digest,
            "content_digest_algorithm": prior.get("content_digest_algorithm"),
        },
        "prior_success": _check_ref(prior, full=True),
        "triggering_not_found": _check_ref(latest, full=False),
        "temporal_rule": {
            "prior_success_checked_at": prior.get("checked_at"),
            "triggering_not_found_checked_at": latest.get("checked_at"),
            "elapsed_seconds": elapsed,
            "minimum_elapsed_seconds": MIN_ELAPSED_SECONDS,
            "temporal_rule_satisfied": True,
        },
        "outage_context": dict(outage),
        "rule_result": {
            "rule_id": RULE_ID,
            "rule_version": RULE_VERSION,
            "rule_satisfied": True,
            "minimum_elapsed_seconds": MIN_ELAPSED_SECONDS,
            "not_found_statuses": sorted(NOT_FOUND_STATUSES),
            "reason_codes": [
                "prior_success_content_observed",
                "latest_completed_check_is_narrow_not_found",
                "elapsed_at_least_minimum",
                "outage_context_not_broad",
            ],
        },
        "provenance": {
            "check_artifact_digests": sorted(
                {prior.get("artifact_digest"), latest.get("artifact_digest")}
            ),
            "triggering_run_id": latest.get("run_id"),
            "run_artifact_digest": run_digests.get(latest.get("run_id")),
            "detector_version": DETECTOR_VERSION,
            "profile_owner": OWNER_PROFILE_ID,
        },
    }


def _build_counterevidence(
    subject: str, digest: str, location: LocatorHistory, data: ScientificDetectionInput
) -> list[dict[str, Any]]:
    counterevidence: list[dict[str, Any]] = []

    for other in data.locators:
        if other.locator_id == location.locator_id or not other.checks:
            continue
        same_digest = [check for check in other.checks if check.get("content_digest") == digest]
        if not same_digest:
            continue
        other_latest = sorted(other.checks, key=_check_order)[-1]
        if (
            other_latest.get("outcome") == psc.AVAILABLE
            and other_latest.get("content_digest") == digest
        ):
            counterevidence.append(
                {
                    "type": CE_CURRENT_PUBLIC_MIRROR,
                    "source_id": other.source_id,
                    "locator_id": other.locator_id,
                    "canonical_locator": other.canonical_locator,
                    "check_id": other_latest.get("check_id"),
                    "checked_at": other_latest.get("checked_at"),
                    "content_entity_id": subject,
                    "content_digest": digest,
                    "artifact_digest": other_latest.get("artifact_digest"),
                    "adapter_id": other_latest.get("adapter_id"),
                    "adapter_version": other_latest.get("adapter_version"),
                }
            )
        else:
            latest_same = sorted(same_digest, key=_check_order)[-1]
            counterevidence.append(
                {
                    "type": CE_HISTORICAL_PUBLIC_COPY,
                    "source_id": other.source_id,
                    "locator_id": other.locator_id,
                    "canonical_locator": other.canonical_locator,
                    "check_id": latest_same.get("check_id"),
                    "checked_at": latest_same.get("checked_at"),
                    "content_entity_id": subject,
                    "content_digest": digest,
                    "artifact_digest": latest_same.get("artifact_digest"),
                }
            )

    for occurrence in data.local_occurrences.get(subject, ()):
        counterevidence.append(
            {
                "type": CE_STORED_LOCAL_COPY,
                "scope_id": occurrence.get("scope_id"),
                "scope_type": occurrence.get("scope_type"),
                "scope_label": occurrence.get("scope_label"),
                "source_artifact_digest": occurrence.get("source_artifact_digest"),
                "derivation_version": occurrence.get("derivation_version"),
                "content_entity_id": subject,
            }
        )

    counterevidence.sort(
        key=lambda item: (
            item["type"],
            str(item.get("locator_id", "")),
            str(item.get("scope_id", "")),
            str(item.get("check_id", "")),
        )
    )
    return counterevidence
