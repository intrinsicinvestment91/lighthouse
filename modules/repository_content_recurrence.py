"""Repository exact content recurrence — the first profile-owned detector (LHP-INTEL-03B).

Owned by the **Repository Capability Discovery** profile. It asks one question:

    Which exact raw-byte content identities have been directly observed in at least two
    distinct repository scopes?

And it answers only that. It does not say the files were copied, that one project derived from
another, that the implementation was independently invented, that the content is reusable,
important, novel or patentable, that the repositories share a capability, or that the result is a
finding. Those are different questions, and none of them can be answered from the facts this rule
reads.

**A candidate is not a verdict.** The rule firing means *"an operator should look at this"*, and the
artifact it produces carries the mandatory unknowns and limitations that say so.

Three ways the rule could quietly become wrong, and the shape that prevents each:

* **Counting rows instead of scopes.** The store's unique index is
  `(entity_id, source_artifact_digest, derivation_version)`, so one repository re-observed after a
  change is *two rows with one scope_id* — and re-observing is the normal case. The rule counts a
  **set of scope ids**, never a number of records.
* **Counting paths as sources.** Two identical files inside one repository arrive as one entity with
  two `file_has_content` relationships. One repository is one scope, however many paths it has.
* **Reasoning from a truncated view.** The read boundary bounds its results, and a bounded list
  reads exactly like a complete one. If the evidence for an entity is not known to be complete, this
  detector emits **no candidate** and says why — 03A's rule, one layer up.

**Pure**: no database, no filesystem, no Git, no network, no model, and no clock — `created_at` is
injected, so a run is a pure function of the facts it was handed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from modules.pattern_candidate import PatternCandidate, build_pattern_candidate

OWNER_PROFILE_ID = "repository_capability_discovery"

DETECTOR_ID = "repository_exact_content_recurrence"
DETECTOR_VERSION = "repository_exact_content_recurrence/1"
CANDIDATE_TYPE = "repository_exact_content_recurrence"

RULE_ID = "exact_content_recurrence_across_repository_scopes"
RULE_VERSION = 1
MIN_DISTINCT_REPOSITORY_SCOPES = 2
RULE_PARAMETERS = {"min_distinct_repository_scopes": MIN_DISTINCT_REPOSITORY_SCOPES}

SCOPE_TYPE_REPOSITORY = "repository"

#: Why an entity was not evaluated at all. Not "why it did not fire" — an entity observed in one
#: repository simply produces no candidate, which is an answer, not a refusal.
SKIP_EVIDENCE_INCOMPLETE = "evidence_scan_truncated"
ALLOWED_SKIP_REASONS = frozenset({SKIP_EVIDENCE_INCOMPLETE})


@dataclass(frozen=True)
class EntityEvidence:
    """Everything the store holds about one content identity, as the read boundary returned it.

    `evidence_complete` is the honest half: the query layer bounds its results, and the detector has
    to know whether it is looking at all of the evidence or merely at some of it.
    """

    entity_id: str
    occurrences: tuple[Mapping[str, Any], ...]
    evidence_complete: bool


@dataclass(frozen=True)
class DetectionResult:
    """What the rule found, and what it declined to look at. No serializer here on purpose: the
    SERVICE owns the reporting shape, and a second one in the pure layer would be a fork of it."""

    candidates: tuple[PatternCandidate, ...]
    skipped: tuple[tuple[str, str], ...]


def detect_content_recurrence(
    evidence: Sequence[EntityEvidence], *, created_at: str
) -> DetectionResult:
    """Apply the one declared rule to stored facts. Deterministic, and ordered by identity.

    Candidates come back sorted by their subject's entity id — **never** by how many scopes they
    recur in. Ordering by count is ranking, and ranking is the first thing a significance layer
    does; it would arrive here, quietly, as a sort key.
    """
    candidates: list[PatternCandidate] = []
    skipped: list[tuple[str, str]] = []

    for item in sorted(evidence, key=lambda record: record.entity_id):
        if not item.evidence_complete:
            skipped.append((item.entity_id, SKIP_EVIDENCE_INCOMPLETE))
            continue

        qualifying = [
            dict(record)
            for record in item.occurrences
            if record.get("scope_type") == SCOPE_TYPE_REPOSITORY
        ]
        # A set of scope ids. Not a count of rows, not a count of paths, not a count of sensors.
        scopes = {record.get("scope_id") for record in qualifying}
        if len(scopes) < MIN_DISTINCT_REPOSITORY_SCOPES:
            continue

        candidates.append(
            build_pattern_candidate(
                profile_id=OWNER_PROFILE_ID,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                candidate_type=CANDIDATE_TYPE,
                subject_entity_id=item.entity_id,
                # Only the occurrences the RULE used. A matching standalone document is a real fact
                # and stays visible through `lighthouse observations where` — it is simply not
                # evidence for a rule about repositories, and presenting it as such would inflate
                # the evidence with something that did not qualify.
                occurrences=qualifying,
                rule_id=RULE_ID,
                rule_version=RULE_VERSION,
                rule_parameters=RULE_PARAMETERS,
                created_at=created_at,
            )
        )

    return DetectionResult(candidates=tuple(candidates), skipped=tuple(skipped))
