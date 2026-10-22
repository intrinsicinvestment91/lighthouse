"""The observation substrate (LHP-INTEL-01D-A).

One fact, observed from one source, at one moment, with one confidence — and the rules that decide
whether such a record is well formed. This is the model every Lighthouse observation source speaks:
`repository_intake` (01A) and `document_intake` (01C) today, and whatever comes next.

It used to live inside `repository_intake`, which meant `document_intake` imported its model, its
digest recipe, its validator *and its exception hierarchy* from a sibling collector. Two sources
made that awkward; a third would have made it wrong, because the substrate would be owned by
whichever collector happened to be written first. A malformed *document* even reported itself as a
`RepositoryIntakeError` — the ownership problem in its purest observable form.

So: the contract lives here, and the collectors import it.

**This module states what a fact IS. It never says why a fact matters.** There is no name here for
score, priority, importance, severity or relevance, and there is nowhere to put one. Significance is
a later layer's question, and giving the substrate a word for it is how that layer arrives by
accident.

Pure: no database, no filesystem, no network, no subprocess, no clock, no LLM — and, above all, no
collector. A substrate that imported one of its own consumers would be the original bug, relocated.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DETERMINISTIC_SERIALIZATION_VERSION = 1
DIGEST_ALGORITHM = "sha256"

# The confidence contract, and it is binary on purpose. A scale invites a producer to hedge, and a
# hedge is an opinion wearing a number. Either a fact was established or it was not:
#
#   1.0  determined   — INCLUDING "determined to be absent" (a nullable type with value None: the
#                       document provably has no text, the repository provably has no submodules)
#   0.0  undetermined — the value is ALWAYS None: we could not establish it, and we say so
#
# Collapsing those two is the failure this whole layer exists to prevent: "I could not enumerate the
# languages" must never be readable as "it contains no code".
CONFIDENCE_OBSERVED = 1.0
CONFIDENCE_UNDETERMINED = 0.0
ALLOWED_CONFIDENCE = frozenset({CONFIDENCE_OBSERVED, CONFIDENCE_UNDETERMINED})

# Shapes shared by every observation artifact, defined once because they were previously copied into
# each collector — and the copies had already begun to drift.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DERIVATION_RE = re.compile(r"^[a-z0-9_]+/[0-9]+$")

_OBSERVATION_KEYS = frozenset({"observation_type", "value", "source", "observed_at", "confidence"})


class ObservationError(Exception):
    """Base class for every fail-closed observation condition, whatever the source.

    Both collectors' bases inherit from this, so one `except ObservationError` catches any intake
    failure — a repository one, a document one, or a violation of the shared contract below.
    """


class SourceBoundaryError(ObservationError):
    """The path resolves outside every allowed root (ADR-0019: a local source is one under an
    explicitly configured root, never a claim made inside an observation)."""


class ObservationValidationError(ObservationError):
    """An observation artifact is malformed, inconsistent, or does not match its digest."""


# -- the model -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationSpec:
    """Static semantics of one observation type: where its evidence comes from, and what shape its
    value may take. `source` lives here rather than on the producer so a collector can never
    mislabel the provenance of a fact."""

    source: str
    value_types: tuple[type, ...]
    nullable: bool = False


@dataclass(frozen=True)
class Observation:
    """One fact. The five fields are the whole contract; there is no field for significance."""

    observation_type: str
    value: Any
    source: str
    observed_at: str
    confidence: float


# -- the digest ----------------------------------------------------------------------------


def canonical_digest(content: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON — the same recipe every Lighthouse contract digest uses.

    Shared, not copied: every observation source digests through this one function, so two sources
    cannot quietly disagree about what the same content hashes to.
    """
    try:
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ObservationValidationError(f"observation content is not serializable: {exc}") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -- validation ----------------------------------------------------------------------------


def validate_observations(observations: Any, vocabulary: Mapping[str, ObservationSpec]) -> None:
    """The observation-model rules, applied to *any* vocabulary: a closed set of types, no
    duplicates, a source pinned by the type rather than chosen by the producer, a UTC timestamp, and
    the binary confidence contract (determined, or undetermined and therefore valueless).

    These are properties of the observation *model*, not of any one source. One implementation on
    purpose: a fail-closed validator that exists twice is a fail-open waiting to happen, because the
    copies drift and the weaker copy is the one that decides.

    A vocabulary's own closed-value rules (git head states, extraction statuses) are NOT enforced
    here — they belong to the source that defines them, and teaching the substrate about git would
    be the same ownership mistake in the opposite direction.
    """
    if not isinstance(observations, (list, tuple)) or not observations:
        raise ObservationValidationError("observations must be a non-empty sequence")
    allowed_sources = frozenset(spec.source for spec in vocabulary.values())
    seen: set[str] = set()
    for observation in observations:
        if not isinstance(observation, Observation):
            raise ObservationValidationError("every observation must be an Observation")
        spec = vocabulary.get(observation.observation_type)
        if spec is None:
            raise ObservationValidationError(
                f"unknown observation type: {observation.observation_type!r}"
            )
        if observation.observation_type in seen:
            raise ObservationValidationError(
                f"duplicate observation type: {observation.observation_type!r}"
            )
        seen.add(observation.observation_type)
        _validate_source(observation, spec, allowed_sources)
        _require_utc_timestamp(
            f"{observation.observation_type}.observed_at", observation.observed_at
        )
        _validate_confidence(observation)
        _validate_value(observation, spec)


def observation_from_dict(item: Any) -> Observation:
    if not isinstance(item, dict):
        raise ObservationValidationError("every observation must be an object")
    keys = set(item)
    missing = _OBSERVATION_KEYS - keys
    if missing:
        raise ObservationValidationError(f"observation is missing required keys: {sorted(missing)}")
    unexpected = keys - _OBSERVATION_KEYS
    if unexpected:
        raise ObservationValidationError(
            f"observation carries unexpected keys: {sorted(unexpected)}"
        )
    return Observation(
        observation_type=item["observation_type"],
        value=item["value"],
        source=item["source"],
        observed_at=item["observed_at"],
        confidence=item["confidence"],
    )


def _validate_source(
    observation: Observation, spec: ObservationSpec, allowed_sources: frozenset[str]
) -> None:
    # The allowed sources are the ones THIS vocabulary declares, not a global list. That is
    # strictly stronger: a repository observation still cannot claim a text-extractor provenance,
    # because the repository vocabulary has no observation type that comes from one.
    if observation.source not in allowed_sources:
        raise ObservationValidationError(
            f"{observation.observation_type}: unknown source {observation.source!r}"
        )
    if observation.source != spec.source:
        raise ObservationValidationError(
            f"{observation.observation_type}: source must be {spec.source!r}, "
            f"got {observation.source!r}"
        )


def _validate_confidence(observation: Observation) -> None:
    confidence = observation.confidence
    # `bool` subclasses `int`, not `float`, so this also rejects `True` — which matters, because
    # `True == 1.0` would otherwise satisfy the membership test below.
    if not isinstance(confidence, float):
        raise ObservationValidationError(
            f"{observation.observation_type}: confidence must be a float (1.0 or 0.0)"
        )
    if confidence not in ALLOWED_CONFIDENCE:
        raise ObservationValidationError(
            f"{observation.observation_type}: confidence must be 1.0 or 0.0, got {confidence!r}"
        )
    if confidence == CONFIDENCE_UNDETERMINED and observation.value is not None:
        raise ObservationValidationError(
            f"{observation.observation_type}: an undetermined observation must carry no value"
        )


def _validate_value(observation: Observation, spec: ObservationSpec) -> None:
    if observation.confidence == CONFIDENCE_UNDETERMINED:
        return  # value is None — already enforced above
    label, value = observation.observation_type, observation.value
    if value is None:
        if not spec.nullable:
            raise ObservationValidationError(f"{label}: value may not be null")
        return
    if bool in spec.value_types:
        if not isinstance(value, bool):
            raise ObservationValidationError(f"{label}: value must be a boolean")
        return
    # `isinstance(True, int)` is True — without this, `commit_count = True` would validate.
    if isinstance(value, bool):
        raise ObservationValidationError(f"{label}: value must not be a boolean")
    if not isinstance(value, spec.value_types):
        allowed = [kind.__name__ for kind in spec.value_types]
        raise ObservationValidationError(f"{label}: value must be one of {allowed}")


def _require_exact_int(label: str, value: Any, expected: int) -> None:
    # `bool` subclasses `int`, so `True == 1` would otherwise slip through.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObservationValidationError(f"{label} must be an integer")
    if value != expected:
        raise ObservationValidationError(
            f"unsupported {label}: {value!r} (this build understands {expected})"
        )


def _require_utc_timestamp(label: str, value: Any) -> None:
    if not isinstance(value, str):
        raise ObservationValidationError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ObservationValidationError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ObservationValidationError(f"{label} must be timezone-aware")
