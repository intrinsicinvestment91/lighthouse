"""The pattern candidate service (LHP-INTEL-03B) — read facts, run one rule, persist, review.

This is the boundary. It is the only module that touches both sides:

* **facts** come in through `observation_query` — the read boundary, `mode=ro`, validate-on-read.
  The service never reaches into the observation tables, and neither does the detector;
* **candidates** go out through `db` — insert-only, lazily created, immutable.

The detector is a pure function of the facts it is handed, and the contract is a pure data model, so
everything impure — the clock, the database, the profile registry — lives here, where it can be
seen.

Three behaviours worth stating out loud, because each of them is a promise the layers below cannot
keep on their own:

**A dry run writes nothing at all.** Not a row, not a table, not the database file. `CREATE TABLE`
is a write, so the candidate tables are created on the first *insert*, never on the way in. That is
what makes "I previewed a detection" and "the store is exactly as I found it" the same statement.

**A candidate is immutable, and evidence changes produce a version.** The candidate id *is* the
artifact digest, so the same facts reproduce the same row (idempotent) and different facts produce a
different one, sharing the `candidate_key`. An operator who reviewed a version can trust that it is
still the thing they reviewed.

**Incomplete evidence produces no candidate.** The read boundary bounds its results, and a bounded
list reads exactly like a complete one — so a rule reasoning over a truncated view would count
distinct scopes across a set it does not know is whole. Where the evidence is not provably complete,
the detector is told so and stays silent, and the run says which entity it declined to evaluate.

Owning a profile is not running under one — until an operator has activated one. Detection
requires an active profile whose identity owns the chosen detector (LHP-V1-PROFILE-01C). The
service still never switches, never contacts the Gateway, and never rewrites historical
candidate ``profile_id`` metadata.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules import (
    db,
    observation_query,
    pattern_candidate_specs,
    profile_registry,
    repository_content_recurrence,
)
from modules import public_source_check as psc
from modules import scientific_source_disappearance as ssd
from modules.active_profile_state import STATE_ACTIVE
from modules.active_profile_store import (
    CorruptActiveProfileStateError,
    read_active_profile_state,
)
from modules.observation_query import MAX_LIMIT, MIN_ID_PREFIX, ObservationQueryError
from modules.pattern_candidate import (
    ALLOWED_DECISIONS,
    CANDIDATE_ID_PREFIX,
    LIMITATION_STATEMENTS,
    UNKNOWN_STATEMENTS,
    PatternCandidate,
    PatternCandidateError,
    PatternDisposition,
    build_pattern_disposition,
    finalize_candidate,
)
from modules.profile_transition import profile_content_sha256
from modules.repository_content_recurrence import (
    EntityEvidence,
    detect_content_recurrence,
)

#: Bounds on the scientific detector's ledger reads. A full page is treated as possibly truncated
#: and disclosed, never silently dropped — the discipline the repository detector uses for scopes.
MAX_LOCATORS = 1000
MAX_CHECKS_PER_LOCATOR = 2000

CONTENT_ENTITY_TYPE = "content"
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

STATUS_STORED = "stored"
STATUS_ALREADY_PRESENT = "already_present"
STATUS_PREVIEWED = "previewed"


class PatternServiceError(Exception):
    """The candidate service refused: bad input, unknown vocabulary, or an unavailable profile."""


class CandidateNotFound(PatternServiceError):
    """No candidate matches that id."""


class AmbiguousCandidateId(PatternServiceError):
    """An abbreviated id matches more than one candidate. Names them all; never guesses."""

    def __init__(self, prefix: str, matches: list[str]) -> None:
        listed = "\n  ".join(matches)
        super().__init__(
            f"{prefix!r} matches {len(matches)} candidates — use more characters:\n  {listed}"
        )
        self.prefix = prefix
        self.matches = matches


class DispositionExists(PatternServiceError):
    """This exact candidate version already carries an operator decision."""


class TamperedCandidateError(PatternServiceError):
    """A stored candidate or disposition does not satisfy its own contract."""


class ProfileUnavailable(PatternServiceError):
    """The profile that owns this detector is missing or does not validate."""


class DetectorNotAdmitted(PatternServiceError):
    """The chosen detector is not owned by the currently active profile."""


@dataclass(frozen=True)
class DetectionRun:
    """What one detection run did. Counts, never a judgement about what it found."""

    dry_run: bool
    candidates: tuple[PatternCandidate, ...]
    statuses: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]
    entities_examined: int
    scan_truncated: bool
    profile_activated: bool = False

    def count(self, status: str) -> int:
        return sum(1 for item in self.statuses if item == status)

    @property
    def stored(self) -> int:
        return self.count(STATUS_STORED)

    @property
    def already_present(self) -> int:
        return self.count(STATUS_ALREADY_PRESENT)

    @property
    def previewed(self) -> int:
        return self.count(STATUS_PREVIEWED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "detector_id": repository_content_recurrence.DETECTOR_ID,
            "detector_version": repository_content_recurrence.DETECTOR_VERSION,
            "profile_id": repository_content_recurrence.OWNER_PROFILE_ID,
            "profile_activated": self.profile_activated is True,
            "rule_id": repository_content_recurrence.RULE_ID,
            "rule_version": repository_content_recurrence.RULE_VERSION,
            "rule_parameters": dict(repository_content_recurrence.RULE_PARAMETERS),
            "entities_examined": self.entities_examined,
            "scan_truncated": self.scan_truncated,
            "candidate_count": len(self.candidates),
            "stored": self.stored,
            "already_present": self.already_present,
            "previewed": self.previewed,
            "skipped": [
                {"entity_id": entity_id, "reason": reason} for entity_id, reason in self.skipped
            ],
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "candidate_key": candidate.candidate_key,
                    "candidate_state": candidate.candidate_state,
                    "status": status,
                    "subject": dict(candidate.subject),
                    "distinct_repository_scope_count": candidate.distinct_repository_scope_count,
                    "scopes": [
                        {
                            "scope_id": occurrence["scope_id"],
                            "scope_label": occurrence["scope_label"],
                            "coverage_state": occurrence["coverage_state"],
                        }
                        for occurrence in candidate.supporting_occurrences
                    ],
                    "limitations": list(candidate.limitations),
                }
                for candidate, status in zip(self.candidates, self.statuses, strict=True)
            ],
        }


@dataclass(frozen=True)
class ScientificDetectionRun:
    """What one scientific-preservation detection run did. Counts and facts, never a judgement.

    The counts distinguish a candidate from every way a locator declined to become one, so an
    operator can see *why* the ledger produced what it did — a blocked outage, a recovery, or simply
    too little history — without any of those read as significance. Nothing here sorts by count.
    """

    dry_run: bool
    candidates: tuple[PatternCandidate, ...]
    statuses: tuple[str, ...]
    evaluated_locator_count: int
    locator_scan_truncated: bool
    reason_counts: dict[str, int]
    profile_activated: bool = False

    def count(self, status: str) -> int:
        return sum(1 for item in self.statuses if item == status)

    @property
    def stored(self) -> int:
        return self.count(STATUS_STORED)

    @property
    def already_present(self) -> int:
        return self.count(STATUS_ALREADY_PRESENT)

    @property
    def previewed(self) -> int:
        return self.count(STATUS_PREVIEWED)

    def _summary(self, candidate: PatternCandidate, status: str) -> dict[str, Any]:
        evidence = candidate.evidence or {}
        temporal = evidence.get("temporal_rule", {})
        return {
            "candidate_id": candidate.candidate_id,
            "candidate_key": candidate.candidate_key,
            "candidate_state": candidate.candidate_state,
            "status": status,
            "subject": dict(candidate.subject),
            "locator": dict(evidence.get("locator", {})),
            "prior_success_checked_at": temporal.get("prior_success_checked_at"),
            "triggering_not_found_checked_at": temporal.get("triggering_not_found_checked_at"),
            "elapsed_seconds": temporal.get("elapsed_seconds"),
            "outage_context_status": (
                evidence.get("outage_context", {}).get("outage_context_status")
            ),
            "counterevidence_count": len(candidate.counterevidence),
            "limitations": list(candidate.limitations),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "detector_id": ssd.DETECTOR_ID,
            "detector_version": ssd.DETECTOR_VERSION,
            "profile_id": ssd.OWNER_PROFILE_ID,
            "profile_activated": self.profile_activated is True,
            "rule_id": ssd.RULE_ID,
            "rule_version": ssd.RULE_VERSION,
            "evaluated_locator_count": self.evaluated_locator_count,
            "locator_scan_truncated": self.locator_scan_truncated,
            "candidate_count": len(self.candidates),
            "stored": self.stored,
            "already_present": self.already_present,
            "previewed": self.previewed,
            "blocked_broad_failure_context": self.reason_counts.get(ssd.REASON_BLOCKED_OUTAGE, 0),
            "suppressed_by_recovery": self.reason_counts.get(ssd.REASON_SUPPRESSED_BY_RECOVERY, 0),
            "insufficient_history": self.reason_counts.get(ssd.REASON_INSUFFICIENT_HISTORY, 0),
            "insufficient_elapsed_time": self.reason_counts.get(ssd.REASON_INSUFFICIENT_ELAPSED, 0),
            "no_prior_success": self.reason_counts.get(ssd.REASON_NO_PRIOR_SUCCESS, 0),
            "latest_outcome_not_triggering": self.reason_counts.get(
                ssd.REASON_LATEST_NOT_TRIGGERING, 0
            ),
            "invalid_evidence": self.reason_counts.get(ssd.REASON_INVALID_EVIDENCE, 0),
            "candidates": [
                self._summary(candidate, status)
                for candidate, status in zip(self.candidates, self.statuses, strict=True)
            ],
        }


# -- detection -----------------------------------------------------------------------------


def run_detection(
    *, detector: str | None = None, dry_run: bool = False, now: str | None = None
) -> DetectionRun | ScientificDetectionRun:
    """Run one declared detector over stored facts, storing each new candidate unless dry run.

    ``detector`` selects among a CLOSED set of detector ids — the repository recurrence detector by
    default. Service-level detection requires an active profile that owns the chosen detector
    (LHP-V1-PROFILE-01C). Pure detector unit functions remain independently testable.
    """
    chosen = detector or repository_content_recurrence.DETECTOR_ID
    owner = _detector_owner(chosen)
    _require_active_detector_admission(owner)
    if chosen == repository_content_recurrence.DETECTOR_ID:
        return _run_repository_detection(dry_run=dry_run, now=now, profile_activated=True)
    if chosen == ssd.DETECTOR_ID:
        return _run_scientific_detection(dry_run=dry_run, now=now, profile_activated=True)
    raise PatternServiceError(
        f"unknown detector: {chosen!r} "
        f"(known: {', '.join(sorted(pattern_candidate_specs.allowed_detector_ids()))})"
    )


def _detector_owner(detector_id: str) -> str:
    if detector_id == repository_content_recurrence.DETECTOR_ID:
        return repository_content_recurrence.OWNER_PROFILE_ID
    if detector_id == ssd.DETECTOR_ID:
        return ssd.OWNER_PROFILE_ID
    raise PatternServiceError(
        f"unknown detector: {detector_id!r} "
        f"(known: {', '.join(sorted(pattern_candidate_specs.allowed_detector_ids()))})"
    )


def _require_active_detector_admission(owner_profile_id: str) -> None:
    try:
        state = read_active_profile_state()
    except CorruptActiveProfileStateError as exc:
        raise PatternServiceError(
            f"active profile state is corrupt — refusing detection: {exc}"
        ) from exc
    if state.activation_state != STATE_ACTIVE:
        raise PatternServiceError(
            "no active profile — refusing detection. Activate repository_capability_discovery "
            "via `lighthouse profile-activate` before running patterns detect."
        )
    if state.active_profile_id != owner_profile_id:
        raise DetectorNotAdmitted(
            f"detector ownership not admitted under active profile "
            f"{state.active_profile_id!r} (detector owned by {owner_profile_id!r})"
        )
    try:
        profile = profile_registry.get_profile(
            state.active_profile_id, version=state.active_profile_version
        )
        profile.validate()
    except Exception as exc:  # noqa: BLE001
        raise ProfileUnavailable(
            f"the active profile is unavailable: {state.active_profile_id} ({exc})"
        ) from exc
    if profile_content_sha256(profile) != state.active_profile_sha256:
        raise PatternServiceError(
            "active profile digest does not match the live registry — refusing detection"
        )


def _persist(candidates, dry_run: bool) -> tuple[str, ...]:
    """Insert-only, and lazily: `init` is a write, so a dry run or a zero-candidate run leaves no
    trace — not a row, not a table, not the database file."""
    if dry_run:
        return tuple(STATUS_PREVIEWED for _ in candidates)
    statuses: list[str] = []
    for candidate in candidates:
        db.init_pattern_candidate_tables()
        stored = db.insert_pattern_candidate(candidate)
        statuses.append(STATUS_STORED if stored else STATUS_ALREADY_PRESENT)
    return tuple(statuses)


def _run_repository_detection(
    *, dry_run: bool, now: str | None, profile_activated: bool
) -> DetectionRun:
    _verify_owner_profile(repository_content_recurrence.OWNER_PROFILE_ID)
    created_at = now or datetime.now(UTC).isoformat()

    evidence, truncated = _collect_content_evidence()
    result = detect_content_recurrence(evidence, created_at=created_at)

    return DetectionRun(
        dry_run=dry_run,
        candidates=result.candidates,
        statuses=_persist(result.candidates, dry_run),
        skipped=result.skipped,
        entities_examined=len(evidence),
        scan_truncated=truncated,
        profile_activated=profile_activated,
    )


def _run_scientific_detection(
    *, dry_run: bool, now: str | None, profile_activated: bool
) -> ScientificDetectionRun:
    _verify_owner_profile(ssd.OWNER_PROFILE_ID)
    created_at = now or datetime.now(UTC).isoformat()

    data, locator_scan_truncated = _collect_public_source_input(created_at)
    result = ssd.detect_disappearing_sources(data)

    candidates = tuple(
        _assemble_scientific_candidate(item, created_at) for item in result.candidates
    )

    reason_counts: dict[str, int] = {}
    for _locator_id, reason in result.non_candidates:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return ScientificDetectionRun(
        dry_run=dry_run,
        candidates=candidates,
        statuses=_persist(candidates, dry_run),
        evaluated_locator_count=len(data.locators),
        locator_scan_truncated=locator_scan_truncated,
        reason_counts=reason_counts,
        profile_activated=profile_activated,
    )


def _assemble_scientific_candidate(item: Any, created_at: str) -> PatternCandidate:
    """Turn one detector output into a validated candidate. The detector chose the facts; the spine
    computes the identity and refuses anything that does not satisfy its own contract."""
    draft = PatternCandidate(
        profile_id=ssd.OWNER_PROFILE_ID,
        detector_id=ssd.DETECTOR_ID,
        detector_version=ssd.DETECTOR_VERSION,
        candidate_type=ssd.CANDIDATE_TYPE,
        subject={"entity_id": item.subject_content_entity_id, "entity_type": "content"},
        rule_id=ssd.RULE_ID,
        rule_version=ssd.RULE_VERSION,
        rule_parameters=dict(ssd.SCIENTIFIC_RULE_PARAMETERS),
        unknowns=item.unknowns,
        limitations=item.limitations,
        evidence=item.evidence,
        counterevidence=item.counterevidence,
        created_at=created_at,
    )
    return finalize_candidate(draft)


def _collect_public_source_input(created_at: str) -> tuple[ssd.ScientificDetectionInput, bool]:
    """Build the detector's input from the append-only ledger and the local fact store.

    Every check and run is re-validated on the way out (digest recomputed, closed vocabulary
    enforced), so a raw-SQL forgery cannot become evidence. The locator enumeration is bounded; a
    full page is treated as possibly truncated and disclosed, never silently narrowed.
    """
    index = db.fetch_public_source_locator_index(limit=MAX_LOCATORS)
    truncated = len(index) >= MAX_LOCATORS

    locators: list[ssd.LocatorHistory] = []
    run_digests: dict[str, str] = {}
    for entry in index:
        rows = db.fetch_public_source_checks(
            source_id=entry["source_id"], ascending=True, limit=MAX_CHECKS_PER_LOCATOR
        )
        checks = tuple(_validated_public_check(row) for row in rows)
        if not checks:
            continue
        locators.append(
            ssd.LocatorHistory(
                source_id=entry["source_id"],
                locator_id=entry["locator_id"],
                canonical_locator=checks[0]["canonical_locator"],
                checks=checks,
            )
        )
        for check in checks:
            run_id = check["run_id"]
            if run_id not in run_digests:
                run_row = db.fetch_public_source_run(run_id)
                if run_row is not None:
                    run_digests[run_id] = _validated_public_run(run_row)["artifact_digest"]

    local_occurrences: dict[str, tuple[dict[str, Any], ...]] = {}
    subjects = {
        "content:" + check["content_digest"]
        for locator in locators
        for check in locator.checks
        if check.get("outcome") == psc.AVAILABLE and check.get("content_digest")
    }
    for subject in subjects:
        occurrences = _local_occurrences(subject)
        if occurrences:
            local_occurrences[subject] = occurrences

    data = ssd.ScientificDetectionInput(
        locators=tuple(locators),
        local_occurrences=local_occurrences,
        run_digests=run_digests,
        created_at=created_at,
    )
    return data, truncated


def _validated_public_check(row: dict[str, Any]) -> dict[str, Any]:
    try:
        check = psc.PublicSourceCheck.from_dict(json.loads(row["check_json"]))
        check.validate()
    except (psc.PublicSourceCheckError, KeyError, TypeError, ValueError) as exc:
        raise TamperedCandidateError(
            f"a stored public-source check does not satisfy its own contract: {exc}"
        ) from exc
    return check.to_dict()


def _validated_public_run(row: dict[str, Any]) -> dict[str, Any]:
    try:
        run = psc.PublicSourceCheckRun.from_dict(json.loads(row["run_json"]))
        run.validate()
    except (psc.PublicSourceCheckError, KeyError, TypeError, ValueError) as exc:
        raise TamperedCandidateError(
            f"a stored public-source run does not satisfy its own contract: {exc}"
        ) from exc
    return run.to_dict()


def _local_occurrences(subject: str) -> tuple[dict[str, Any], ...]:
    """Local repository/document occurrences of the exact subject bytes — for local-copy
    counterevidence. Read-only; an absent observation store simply yields none."""
    if not db.observation_tables_exist():
        return ()
    try:
        found = observation_query.entity_occurrences(subject, limit=MAX_LIMIT)
    except ObservationQueryError:
        return ()
    return tuple(
        {
            "scope_id": occurrence["scope_id"],
            "scope_type": occurrence["scope_type"],
            "scope_label": occurrence.get("scope_label"),
            "source_artifact_digest": occurrence["source_artifact_digest"],
            "derivation_version": occurrence["derivation_version"],
        }
        for occurrence in found["occurrences"]
    )


def _collect_content_evidence() -> tuple[list[EntityEvidence], bool]:
    """Every content identity the store holds, with its occurrences and their coverage state."""
    listed = observation_query.entity_ids_by_type(CONTENT_ENTITY_TYPE)
    evidence: list[EntityEvidence] = []

    for entity_id in listed["entity_ids"]:
        found = observation_query.entity_occurrences(entity_id, limit=MAX_LIMIT)

        # A page that came back FULL may have been cut short — the read boundary bounds after it
        # sorts, and a bounded list is indistinguishable from a complete one. Treat it as incomplete
        # and let the detector decline: a candidate built on a partial view would undercount scopes
        # while claiming to carry all the evidence.
        complete = not found["scan_truncated"] and found["occurrence_count"] < MAX_LIMIT

        occurrences = []
        for occurrence in found["occurrences"]:
            occurrences.append({**occurrence, "coverage_state": _coverage_state(occurrence)})

        evidence.append(
            EntityEvidence(
                entity_id=entity_id,
                occurrences=tuple(occurrences),
                evidence_complete=complete,
            )
        )

    return evidence, listed["scan_truncated"]


def _coverage_state(occurrence: dict[str, Any]) -> str:
    """`complete`, `incomplete`, or `unknown` — three states that never collapse into two.

    An artifact written before 03A carries no coverage observation at all, and neither does a
    document. That is `unknown`: not a claim that coverage was partial, and emphatically not a claim
    that it was whole.
    """
    coverage = observation_query.artifact_coverage(
        scope_id=occurrence["scope_id"],
        source_artifact_digest=occurrence["source_artifact_digest"],
    )
    if coverage is None:
        return "unknown"
    return "complete" if coverage.get("complete") is True else "incomplete"


def _verify_owner_profile(profile_id: str) -> None:
    """The detector's profile must exist and validate. It is NOT activated.

    Carrying `profile_id` on a candidate proves who declared the rule. It does not prove — and must
    never be read as — a profile running at runtime. Activation is frozen (ADR-0018).
    """
    try:
        profile = profile_registry.get_profile(profile_id)
        profile.validate()
    except Exception as exc:
        raise ProfileUnavailable(
            f"the profile that owns this detector is unavailable: {profile_id} ({exc})"
        ) from exc


# -- reading candidates back ---------------------------------------------------------------


def list_candidates(
    *,
    profile_id: str | None = None,
    detector_id: str | None = None,
    candidate_type: str | None = None,
    candidate_key: str | None = None,
    disposition: str | None = None,
    all_versions: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Candidates, newest version per logical candidate by default.

    "Latest" means *most recently generated*, not *most correct*: it is the version that reflects
    the newest facts, and it is the one awaiting review. `--all-versions` shows the history, which
    is never rewritten.

    Filters are applied to the whole set, and the bound is applied **last** — bounding first would
    answer a different question and call it a filter.
    """
    if disposition is not None and disposition not in ALLOWED_DECISIONS:
        raise PatternServiceError(
            f"unknown disposition: {disposition!r} "
            f"(allowed: {', '.join(sorted(ALLOWED_DECISIONS))})"
        )

    bound = _bounded_limit(limit)
    if not db.pattern_candidate_tables_exist():
        return {"count": 0, "candidates": []}

    rows = db.fetch_pattern_candidates(
        profile_id=profile_id,
        detector_id=detector_id,
        candidate_type=candidate_type,
        candidate_key=candidate_key,
        limit=MAX_LIST_LIMIT,
    )
    records = [(row, _validated_candidate(row)) for row in rows]

    if not all_versions:
        # Rows arrive ordered by (candidate_key, created_at, candidate_id), so the last row for a
        # key is its newest version. A dict keeps insertion order, so the result stays key-ordered.
        newest: dict[str, tuple[dict[str, Any], PatternCandidate]] = {}
        for row, candidate in records:
            newest[candidate.candidate_key] = (row, candidate)
        records = list(newest.values())

    decisions = _dispositions_for([candidate.candidate_id for _, candidate in records])

    listed: list[dict[str, Any]] = []
    for _row, candidate in records:
        decided = decisions.get(candidate.candidate_id)
        if disposition is not None and (decided is None or decided.decision != disposition):
            continue
        listed.append(_summary(candidate, decided))

    return {"count": len(listed[:bound]), "candidates": listed[:bound]}


def show_candidate(candidate_id: str) -> dict[str, Any]:
    """One candidate in full, with its evidence, its unknowns, its limitations, and its decision."""
    resolved = resolve_candidate_id(candidate_id)
    row = db.fetch_pattern_candidate(resolved)
    if row is None:  # pragma: no cover - resolve_candidate_id already proved it exists
        raise CandidateNotFound(f"no candidate matches {candidate_id!r}")

    candidate = _validated_candidate(row)
    decided = _dispositions_for([resolved]).get(resolved)

    # The artifact stores codes; an operator reads sentences. Each detector owns its own vocabulary,
    # so the sentences are looked up per detector — and a code with no sentence falls back to itself
    # (which is already readable snake_case), never a KeyError that would corrupt the surface.
    unknown_map, limitation_map = _statement_maps(candidate.detector_id)
    return {
        "candidate": candidate.to_dict(),
        "disposition": decided.to_dict() if decided is not None else None,
        "unknown_statements": [unknown_map.get(code, code) for code in candidate.unknowns],
        "limitation_statements": [limitation_map.get(code, code) for code in candidate.limitations],
    }


def _statement_maps(detector_id: str) -> tuple[dict[str, str], dict[str, str]]:
    if detector_id == ssd.DETECTOR_ID:
        return ssd.UNKNOWN_STATEMENTS, ssd.LIMITATION_STATEMENTS
    return UNKNOWN_STATEMENTS, LIMITATION_STATEMENTS


def resolve_candidate_id(value: str) -> str:
    """Resolve a candidate id, whole or abbreviated — the same rule 02A set for observation ids.

    Accepts the full `pc_<sha256>` or an unambiguous hex prefix of at least 8 characters (git's
    abbreviated ids). One match resolves; none is an error; **two or more is an error that names
    every candidate**, never a guess. The full id is always what comes back.
    """
    if not isinstance(value, str):
        raise PatternServiceError("a candidate id must be a string")

    candidate = value.strip()
    if candidate.startswith(CANDIDATE_ID_PREFIX):
        candidate = candidate[len(CANDIDATE_ID_PREFIX) :]

    if not candidate or not all(char in "0123456789abcdef" for char in candidate):
        raise PatternServiceError(
            f"a candidate id is lowercase hexadecimal, optionally prefixed "
            f"{CANDIDATE_ID_PREFIX!r} — not {value!r}"
        )
    if len(candidate) < MIN_ID_PREFIX:
        raise PatternServiceError(
            f"a candidate id needs at least {MIN_ID_PREFIX} characters to be unambiguous"
        )
    if len(candidate) > 64:
        raise PatternServiceError(f"a candidate id is at most 64 characters, not {len(candidate)}")

    if not db.pattern_candidate_tables_exist():
        raise CandidateNotFound(f"no candidate matches {value!r}")

    if len(candidate) == 64:
        full = CANDIDATE_ID_PREFIX + candidate
        if db.fetch_pattern_candidate(full) is None:
            raise CandidateNotFound(f"no candidate matches {value!r}")
        return full

    matches = db.fetch_pattern_candidate_ids_by_prefix(candidate)
    if not matches:
        raise CandidateNotFound(f"no candidate matches {value!r}")
    if len(matches) > 1:
        raise AmbiguousCandidateId(candidate, matches)
    return matches[0]


# -- operator disposition ------------------------------------------------------------------


def record_disposition(
    *,
    candidate_id: str,
    operator_id: str,
    decision: str,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Record one local operator decision about one exact candidate version.

    It is not a finding, not a Gateway approval, and not authorization to publish or execute. It
    does not touch the candidate: the artifact an operator reviewed stays byte-for-byte what they
    reviewed, and the decision sits beside it.
    """
    resolved = resolve_candidate_id(candidate_id)
    row = db.fetch_pattern_candidate(resolved)
    if row is None:  # pragma: no cover - resolve_candidate_id already proved it exists
        raise CandidateNotFound(f"no candidate matches {candidate_id!r}")

    candidate = _validated_candidate(row)
    if db.fetch_pattern_disposition(resolved) is not None:
        raise DispositionExists(
            f"{resolved} already carries an operator decision. A candidate version is reviewed "
            f"once; when the evidence changes, the detector produces a new version to review."
        )

    try:
        disposition = build_pattern_disposition(
            candidate_id=candidate.candidate_id,
            candidate_artifact_digest=candidate.artifact_digest,
            operator_id=operator_id,
            decision=decision,
            reason=reason,
            created_at=now or datetime.now(UTC).isoformat(),
        )
    except PatternCandidateError as exc:
        raise PatternServiceError(str(exc)) from exc

    try:
        db.insert_pattern_disposition(disposition)
    except sqlite3.IntegrityError as exc:
        # The UNIQUE index is the real gate; the check above is only there to say so kindly.
        raise DispositionExists(f"{resolved} already carries an operator decision") from exc

    return {
        "candidate_id": candidate.candidate_id,
        "candidate_key": candidate.candidate_key,
        "disposition": disposition.to_dict(),
    }


# -- helpers -------------------------------------------------------------------------------


def _bounded_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIST_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise PatternServiceError(f"limit must be an integer, not {limit!r}") from exc
    if value < 1:
        raise PatternServiceError("limit must be at least 1")
    return min(value, MAX_LIST_LIMIT)


def _validated_candidate(row: dict[str, Any]) -> PatternCandidate:
    """A stored candidate, round-tripped through its own contract.

    Reads validate exactly as writes do. The digest is recomputed *and* the closed field vocabulary
    is enforced — so an evaluative field injected by raw SQL is refused on the way **out**, with or
    without a re-stamped digest. The digest alone was never going to be enough: anyone who can write
    the row can re-stamp it.
    """
    try:
        candidate = PatternCandidate.from_dict(json.loads(row["candidate_json"]))
        candidate.validate()
    except (PatternCandidateError, KeyError, TypeError, ValueError) as exc:
        raise TamperedCandidateError(
            f"the stored candidate {row.get('candidate_id')!r} does not satisfy its own "
            f"contract: {exc}"
        ) from exc

    if candidate.candidate_id != row["candidate_id"]:
        raise TamperedCandidateError(
            f"stored candidate {row['candidate_id']!r} carries an artifact with a different "
            f"identity"
        )
    return candidate


def _validated_disposition(row: dict[str, Any]) -> PatternDisposition:
    try:
        disposition = PatternDisposition.from_dict(json.loads(row["disposition_json"]))
        disposition.validate()
    except (PatternCandidateError, KeyError, TypeError, ValueError) as exc:
        raise TamperedCandidateError(
            f"the stored disposition for {row.get('candidate_id')!r} does not satisfy its own "
            f"contract: {exc}"
        ) from exc

    if disposition.candidate_id != row["candidate_id"]:
        raise TamperedCandidateError(
            f"the stored disposition for {row['candidate_id']!r} names a different candidate"
        )
    return disposition


def _dispositions_for(candidate_ids: list[str]) -> dict[str, PatternDisposition]:
    if not candidate_ids or not db.pattern_candidate_tables_exist():
        return {}
    return {
        row["candidate_id"]: _validated_disposition(row)
        for row in db.fetch_pattern_dispositions(candidate_ids)
    }


def _summary(candidate: PatternCandidate, decided: PatternDisposition | None) -> dict[str, Any]:
    """What `list` shows: enough to choose what to open, and nothing that suggests an order.

    The detector-specific half differs — a repository candidate summarizes its scopes; a scientific
    one summarizes its locator — but neither carries a count that could be read as a rank.
    """
    summary = {
        "candidate_id": candidate.candidate_id,
        "candidate_key": candidate.candidate_key,
        "candidate_state": candidate.candidate_state,
        "profile_id": candidate.profile_id,
        "detector_id": candidate.detector_id,
        "detector_version": candidate.detector_version,
        "candidate_type": candidate.candidate_type,
        "created_at": candidate.created_at,
        "subject": dict(candidate.subject),
        "disposition": (
            {
                "decision": decided.decision,
                "operator_id": decided.operator_id,
                "created_at": decided.created_at,
            }
            if decided is not None
            else None
        ),
    }
    if candidate.detector_id == ssd.DETECTOR_ID:
        evidence = candidate.evidence or {}
        summary["locator"] = dict(evidence.get("locator", {}))
        summary["outage_context_status"] = evidence.get("outage_context", {}).get(
            "outage_context_status"
        )
        summary["counterevidence_count"] = len(candidate.counterevidence)
    else:
        summary["distinct_repository_scope_count"] = candidate.distinct_repository_scope_count
        summary["scopes"] = [
            {
                "scope_id": occurrence["scope_id"],
                "scope_label": occurrence["scope_label"],
                "coverage_state": occurrence["coverage_state"],
            }
            for occurrence in candidate.supporting_occurrences
        ]
    return summary


# `ObservationQueryError` is re-exported so a caller can catch the fact layer's refusals and this
# layer's with one import, rather than reaching past the boundary to find them.
__all__ = [
    "AmbiguousCandidateId",
    "CandidateNotFound",
    "DetectionRun",
    "DispositionExists",
    "ObservationQueryError",
    "PatternServiceError",
    "ProfileUnavailable",
    "TamperedCandidateError",
    "list_candidates",
    "record_disposition",
    "resolve_candidate_id",
    "run_detection",
    "show_candidate",
]
