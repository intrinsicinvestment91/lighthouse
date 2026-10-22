"""The runtime observation intake boundary (LHP-INTEL-01E).

The observation layer had four packets and no caller. This is the caller: it takes the sources the
operator declared, checks each against the configured roots, runs the deterministic collectors,
normalizes what they saw, and records the facts. That is the whole job.

**It never says why any of it matters.** There is no score here, no rank, no priority, and no name
for one — significance is a later layer's question, and a boundary that starts summarizing is how
that layer arrives by accident. This module counts what happened and stops.

Three properties do the safety work:

* **The roots are supplied, always.** `observe_repository` and `observe_document` both accept
  `allowed_roots=None`, and `None` means *no boundary at all* — every path on the disk becomes
  observable and nothing else in the system would notice. So this module never passes `None`: with
  no roots configured it **refuses the entire run** rather than degrading into "observe anything".
* **Dry run means nothing happens.** Not "no rows" — no database. `modules.db` is not called at all,
  not even to create the tables, because creating a table is a write.
* **A bad source fails itself, not the run** (`main.py`'s module-fault-isolation rule), and reports
  a closed reason token rather than a stack trace.

Reads. Never writes to what it observes, never reaches the network, never invokes an LLM, and adds
no execution capability of its own — the only subprocess in this layer remains 01A's single hardened
git chokepoint, which this module calls but does not reimplement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from modules import db
from modules.active_profile_state import STATE_ACTIVE
from modules.active_profile_store import (
    CorruptActiveProfileStateError,
    read_active_profile_state,
)
from modules.document_intake import (
    DocumentNotFoundError,
    DocumentUnreadableError,
    UnsupportedDocumentFormatError,
    observe_document,
)
from modules.observation_normalizer import (
    NormalizationError,
    NormalizedObservationSet,
    normalize_document_observation,
    normalize_repository_observation,
)
from modules.observation_sources import ObservationSource
from modules.observations import ObservationValidationError, SourceBoundaryError
from modules.profile_registry import get_profile
from modules.profile_transition import profile_content_sha256
from modules.repository_intake import (
    GitUnavailableError,
    NotAGitRepositoryError,
    RepositoryNotFoundError,
    observe_repository,
)

logger = logging.getLogger(__name__)

# What can happen to one declared source. Closed, and there is no word here for "interesting".
INTAKE_STATUSES = frozenset({"stored", "already_present", "previewed", "failed"})

# Why a source was refused — a closed vocabulary of reason tokens, so a caller can branch on the
# outcome without parsing prose, and so a failure never leaks a stack trace to the operator.
_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (SourceBoundaryError, "source_outside_allowed_roots"),
    (RepositoryNotFoundError, "source_not_found"),
    (DocumentNotFoundError, "source_not_found"),
    (NotAGitRepositoryError, "not_a_git_repository"),
    (GitUnavailableError, "git_unavailable"),
    (UnsupportedDocumentFormatError, "unsupported_document_format"),
    (DocumentUnreadableError, "document_unreadable"),
    (ObservationValidationError, "invalid_observation"),
    (NormalizationError, "normalization_failed"),
)

# Closed admission-refusal tokens (LHP-V1-PROFILE-01C). Distinct from collector failures.
REASON_PROFILE_NOT_ADMITTED = "profile_not_admitted"
REASON_SOURCE_TYPE_NOT_ADMITTED = "source_type_not_admitted"
REASON_COLLECTOR_NOT_ADMITTED = "collector_not_admitted"


class ProfileAdmissionError(Exception):
    """One declared source is refused by the active profile's admission policy."""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        super().__init__(message or reason)


class ObservationIntakeError(Exception):
    """The run itself cannot proceed — a configuration failure, not a source failure."""


@dataclass(frozen=True)
class SourceOutcome:
    """What happened to one declared source. Facts about the observation, never about its worth."""

    source_type: str
    path: str
    status: str
    scope_id: str | None = None
    artifact_digest: str | None = None
    normalization_digest: str | None = None
    entities: int = 0
    relationships: int = 0
    undetermined: int = 0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "path": self.path,
            "status": self.status,
            "scope_id": self.scope_id,
            "artifact_digest": self.artifact_digest,
            "normalization_digest": self.normalization_digest,
            "entities": self.entities,
            "relationships": self.relationships,
            "undetermined": self.undetermined,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class IntakeRun:
    dry_run: bool
    allowed_roots: tuple[str, ...]
    outcomes: tuple[SourceOutcome, ...]

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "allowed_roots": list(self.allowed_roots),
            "sources": len(self.outcomes),
            "stored": self.count("stored"),
            "already_present": self.count("already_present"),
            "previewed": self.count("previewed"),
            "failed": self.count("failed"),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


def run_observation_intake(
    *,
    sources: list[ObservationSource],
    allowed_roots: list[str],
    dry_run: bool = False,
) -> IntakeRun:
    """Observe every declared source that resolves inside an allowed root. Persist the facts.

    `allowed_roots` has no default and an empty one is fatal — see the module docstring. `dry_run`
    observes and normalizes but touches no database whatsoever.

    New intake requires an active profile (LHP-V1-PROFILE-01C). Sources are admitted against that
    profile's declared source/collector policy; a null ``profile_id`` is stamped with the active
    profile id. Existing stored observations are never rewritten.
    """
    roots = tuple(str(root) for root in allowed_roots if str(root).strip())
    if not roots:
        raise ObservationIntakeError(
            "no allowed roots configured — refusing to observe. Set LIGHTHOUSE_OBSERVATION_ROOTS "
            "to the directories you want Lighthouse to be able to look at."
        )

    active_profile = _require_active_profile_for_intake()

    outcomes: list[SourceOutcome] = []
    tables_ready = False

    for source in sources:
        try:
            source.validate()
            admitted = _admit_source(source, active_profile)
            normalized = _normalize(admitted, roots=roots)

            if dry_run:
                status = "previewed"
            else:
                # Created on first use, not up front: a run that stores nothing must write nothing,
                # and a CREATE TABLE is a write. This makes "everything was refused" and "dry run"
                # the same guarantee instead of two similar-sounding ones.
                if not tables_ready:
                    db.init_observation_tables()
                    tables_ready = True
                # Insert-only and idempotent by content identity: False means these exact facts are
                # already recorded, and nothing was rewritten, re-timed, or repaired.
                recorded = db.store_normalized_observation(normalized)
                status = "stored" if recorded else "already_present"

            outcomes.append(_outcome(admitted, status, normalized))

        except Exception as exc:  # one bad source fails itself, never the run
            reason = _reason_for(exc)
            logger.error(
                "Observation source failed: %s (%s) — %s", source.path, reason, exc, exc_info=True
            )
            outcomes.append(
                SourceOutcome(
                    source_type=source.type,
                    path=source.path,
                    status="failed",
                    reason=reason,
                )
            )

    return IntakeRun(dry_run=dry_run, allowed_roots=roots, outcomes=tuple(outcomes))


def _require_active_profile_for_intake():
    try:
        state = read_active_profile_state()
    except CorruptActiveProfileStateError as exc:
        raise ObservationIntakeError(
            f"active profile state is corrupt — refusing new intake: {exc}"
        ) from exc
    if state.activation_state != STATE_ACTIVE:
        raise ObservationIntakeError(
            "no active profile — refusing new intake. Activate repository_capability_discovery "
            "via `lighthouse profile-activate` before observing."
        )
    try:
        profile = get_profile(state.active_profile_id, version=state.active_profile_version)
        profile.validate()
    except Exception as exc:  # noqa: BLE001 — fail closed
        raise ObservationIntakeError(
            f"active profile is unavailable — refusing new intake: {exc}"
        ) from exc
    live_digest = profile_content_sha256(profile)
    if live_digest != state.active_profile_sha256:
        raise ObservationIntakeError(
            "active profile digest does not match the live registry — refusing new intake"
        )
    return profile


def _admit_source(source: ObservationSource, active_profile) -> ObservationSource:
    """Admit or refuse one source against the active profile. May stamp a null profile_id."""
    allowed_types = set(active_profile.source_policy.get("allowed_source_types") or ())
    if source.type not in allowed_types:
        raise ProfileAdmissionError(
            REASON_SOURCE_TYPE_NOT_ADMITTED,
            f"source type {source.type!r} is not admitted by active profile "
            f"{active_profile.profile_id!r}",
        )

    enabled = set(active_profile.collector_policy.get("enabled_collectors") or ())
    collector = "repository_intake" if source.type == "repository" else "document_intake"
    if collector not in enabled:
        raise ProfileAdmissionError(
            REASON_COLLECTOR_NOT_ADMITTED,
            f"collector {collector!r} is not admitted by active profile "
            f"{active_profile.profile_id!r}",
        )

    if source.profile_id is None:
        return ObservationSource(
            type=source.type,
            path=source.path,
            enabled=source.enabled,
            profile_id=active_profile.profile_id,
        )
    if source.profile_id != active_profile.profile_id:
        raise ProfileAdmissionError(
            REASON_PROFILE_NOT_ADMITTED,
            f"source profile_id {source.profile_id!r} does not match active profile "
            f"{active_profile.profile_id!r}",
        )
    return source


def _outcome(
    source: ObservationSource, status: str, normalized: NormalizedObservationSet
) -> SourceOutcome:
    return SourceOutcome(
        source_type=source.type,
        path=source.path,
        status=status,
        scope_id=normalized.scope_id,
        artifact_digest=normalized.source_artifact_digest,
        normalization_digest=normalized.normalization_digest,
        entities=len(normalized.entities),
        relationships=len(normalized.relationships),
        undetermined=len(normalized.undetermined_observations),
    )


def _normalize(source: ObservationSource, *, roots: tuple[str, ...]) -> NormalizedObservationSet:
    # `allowed_roots=roots` on every call, unconditionally. Passing None here would silently remove
    # the trust boundary from the entire system.
    if source.type == "repository":
        artifact = observe_repository(
            source.path, allowed_roots=roots, profile_id=source.profile_id
        )
        return normalize_repository_observation(artifact)

    artifact = observe_document(source.path, allowed_roots=roots, profile_id=source.profile_id)
    return normalize_document_observation(artifact)


def _reason_for(exc: Exception) -> str:
    if isinstance(exc, ProfileAdmissionError):
        return exc.reason
    for kind, reason in _REASONS:
        if isinstance(exc, kind):
            return reason
    return "unexpected_error"
