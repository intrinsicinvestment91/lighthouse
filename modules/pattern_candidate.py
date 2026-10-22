"""Profile-neutral pattern candidate contract (LHP-INTEL-03B).

A **pattern candidate** is a versioned review object created because direct stored facts satisfy one
declared deterministic rule. It is not a hypothesis, not a finding, and not permission to do
anything. It says exactly this much:

    "These direct facts satisfy one declared review rule. An operator should look."

And it must never be able to say more. That is enforced structurally rather than promised:

* **The field set is closed.** ``from_dict()`` refuses an unknown key, so a ``score`` or an
  ``importance`` has nowhere to live — including *inside* an evidence occurrence, which is copied
  verbatim from the store and would otherwise be an unwatched door.
* **The authority pins are identity-checked constants.** ``is_finding`` must be ``False`` — not
  falsy, ``False`` — so neither a value edit nor a ``0`` can turn a candidate into a verdict.
* **Every digest is recomputed**, on the way in and on the way out. But the digest is deliberately
  *not* the gate for the vocabulary: anyone who can write a record can re-stamp it, which is the
  lesson 03A learned when a closed status vocabulary turned out to be declared and unenforced.

Mechanics here are profile-NEUTRAL. The *rule* is profile-owned and lives in its own module
(``repository_content_recurrence``); this contract only knows the closed vocabulary of profiles,
detectors and candidate types that exist. Private Invention Discovery is one profile among several,
and nothing in this file is shaped for it.

**Pure**: no database, no filesystem, no network, no clock, no LLM. ``created_at`` is *injected*, so
the same facts always produce a byte-identical artifact, and the digest can be trusted as an
identity.
The one non-stdlib import is the observation substrate's ``canonical_digest`` — shared, not copied,
because two copies of a digest recipe are how two digests eventually disagree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from modules.observations import ObservationError, canonical_digest

PATTERN_CANDIDATE_SCHEMA_VERSION = 1
PATTERN_DISPOSITION_SCHEMA_VERSION = 1
DETERMINISTIC_SERIALIZATION_VERSION = 1
GENERATOR_VERSION = "pattern_candidate/1"
DIGEST_ALGORITHM = "sha256"

CANDIDATE_ID_PREFIX = "pc_"
CANDIDATE_KEY_PREFIX = "pk_"
DISPOSITION_ID_PREFIX = "pd_"

CANDIDATE_STATE_READY = "ready_for_review"

#: The only state a candidate can be in. There is no word here for verified, approved, finding,
#: published, executing or completed — so a promotion cannot be expressed, only rejected.
ALLOWED_CANDIDATE_STATES = frozenset({CANDIDATE_STATE_READY})

#: Every profile Lighthouse bundles. Closed so that a forged artifact cannot invent an owner; kept
#: in step with `seed/profiles/` by a drift test, because this module is pure and reads no seed.
ALLOWED_PROFILE_IDS = frozenset(
    {
        "aerospace_defense_monitoring",
        "fusion_research_monitoring",
        "public_scientific_lighthouse",
        "repository_capability_discovery",
        "scientific_preservation",
    }
)

ALLOWED_DETECTOR_IDS = frozenset({"repository_exact_content_recurrence"})
ALLOWED_CANDIDATE_TYPES = frozenset({"repository_exact_content_recurrence"})
ALLOWED_RULE_IDS = frozenset({"exact_content_recurrence_across_repository_scopes"})
REQUIRED_RULE_PARAMETERS = {"min_distinct_repository_scopes": 2}

ALLOWED_SCOPE_TYPES = frozenset({"repository", "document"})

#: Three states, and they never collapse into two. "I accounted for every file" (`complete`), "I
#: could not" (`incomplete`), and "this artifact predates content coverage entirely" (`unknown`) are
#: different claims, and only the first may be read as full evidence.
ALLOWED_COVERAGE_STATES = frozenset({"complete", "incomplete", "unknown"})

ALLOWED_DECISIONS = frozenset({"accepted_for_followup", "dismissed", "needs_more_evidence"})
ALLOWED_REVIEW_METHODS = frozenset({"local_cli"})

#: What this detector cannot see. Every code is MANDATORY: an unknown that can be omitted is an
#: unknown that quietly becomes a "no".
REQUIRED_UNKNOWN_CODES = frozenset(
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
ALLOWED_UNKNOWN_CODES = REQUIRED_UNKNOWN_CODES

#: What exact byte recurrence does not prove. Always present.
REQUIRED_LIMITATION_CODES = frozenset(
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
#: Emitted only when a cited repository's content inventory was not fully accounted for.
LIMITATION_INVENTORY_INCOMPLETE = "repository_inventory_incomplete"
ALLOWED_LIMITATION_CODES = REQUIRED_LIMITATION_CODES | {LIMITATION_INVENTORY_INCOMPLETE}

UNKNOWN_STATEMENTS: dict[str, str] = {
    "authorship_not_assessed": "Who wrote these bytes has not been assessed.",
    "independent_creation_not_assessed": (
        "Whether these implementations were created independently has not been assessed."
    ),
    "repository_lineage_not_assessed": (
        "Whether these repositories share a lineage (fork, clone, split) has not been assessed."
    ),
    "generated_file_status_unknown": "Whether this file is machine-generated is unknown.",
    "vendored_file_status_unknown": "Whether this file was vendored from elsewhere is unknown.",
    "template_or_boilerplate_status_unknown": (
        "Whether this file came from a template or scaffold is unknown."
    ),
    "semantic_equivalence_not_assessed": (
        "Only exact bytes were compared; semantic equivalence has not been assessed."
    ),
    "significance_not_assessed": "Whether this recurrence matters has not been assessed.",
    "novelty_not_assessed": "Whether these bytes are novel has not been assessed.",
    "source_independence_not_assessed": (
        "The occurrences are not known to be independent sources; they may be copies, forks, "
        "mirrors, vendored files, or one project split in two."
    ),
}

LIMITATION_STATEMENTS: dict[str, str] = {
    "exact_byte_identity_only": "Exact byte identity proves only exact byte identity.",
    "recurrence_does_not_prove_copying": "Recurrence does not prove that anything was copied.",
    "recurrence_does_not_prove_independent_implementation": (
        "Recurrence does not prove independent implementation."
    ),
    "recurrence_does_not_prove_shared_capability": (
        "Recurrence does not prove that these repositories share a capability."
    ),
    "recurrence_does_not_prove_novelty": "Recurrence does not prove novelty.",
    "recurrence_does_not_establish_significance": "Recurrence does not establish significance.",
    "operator_review_required": "This candidate requires operator review.",
    "evidence_reflects_stored_observations_not_current_state": (
        "Evidence reflects the observations Lighthouse has stored, not the current state of the "
        "filesystem; a scope observed once and never re-observed still reads as current."
    ),
    LIMITATION_INVENTORY_INCOMPLETE: (
        "At least one cited repository's content inventory was not fully accounted for, so its "
        "absence of other matching files is not evidence of absence."
    ),
}

#: Excluded from the digest, and ONLY these. `candidate_id` is circular (it is the digest);
#: `created_at` is a wall-clock stamp, and excluding it is what makes a re-run idempotent.
VOLATILE_FIELDS = frozenset({"candidate_id", "created_at", "artifact_digest"})
DISPOSITION_VOLATILE_FIELDS = frozenset({"disposition_id", "created_at", "artifact_digest"})

#: One evidence occurrence, exactly as `observation_query.entity_occurrences()` records it, plus the
#: coverage state of the artifact it came from. Closed, because evidence is copied verbatim — an
#: open shape would let an evaluative field ride into the candidate inside an occurrence.
OCCURRENCE_FIELDS = frozenset(
    {
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
    }
)
SUPPORTING_OBSERVATION_FIELDS = frozenset(
    {"observation_type", "sensor", "confidence", "observed_at"}
)
RELATIONSHIP_FIELDS = frozenset(
    {
        "relationship_type",
        "subject_id",
        "object_id",
        "relationship_id",
        "relationship_digest",
        "source_artifact_digest",
        "derivation_version",
    }
)

CANDIDATE_FIELDS = (
    "schema_version",
    "candidate_id",
    "candidate_key",
    "created_at",
    "generator_version",
    "deterministic_serialization_version",
    "artifact_digest",
    "digest_algorithm",
    "profile_id",
    "detector_id",
    "detector_version",
    "candidate_type",
    "candidate_state",
    "subject",
    "supporting_occurrences",
    "counterevidence",
    "rule_id",
    "rule_version",
    "rule_parameters",
    "distinct_repository_scope_count",
    "rule_satisfied",
    "unknowns",
    "limitations",
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
    "operator_review_required",
)

#: The fields a single detector adds beyond the neutral spine — a discriminated union keyed on
#: `detector_id`. Repository candidates carry the first two; scientific candidates carry `evidence`.
#: `evidence` is deliberately NOT in `CANDIDATE_FIELDS` (that tuple is the repository shape, pinned
#: by the schema drift test); it enters a candidate only through its detector's `extra_fields`.
DETECTOR_OWNED_FIELDS = frozenset(
    {"supporting_occurrences", "distinct_repository_scope_count", "evidence"}
)
#: The closed set every detector shares. Per candidate the valid key set is
#: `SPINE_FIELDS | spec.extra_fields`, resolved from the detector's spec.
SPINE_FIELDS = frozenset(CANDIDATE_FIELDS) - DETECTOR_OWNED_FIELDS

DISPOSITION_FIELDS = (
    "schema_version",
    "disposition_id",
    "candidate_id",
    "candidate_artifact_digest",
    "created_at",
    "operator_id",
    "decision",
    "reason",
    "review_method",
    "operator_identity_authenticated",
    "artifact_digest",
    "digest_algorithm",
)

#: Every authority a candidate does NOT have. Each is checked by identity, never truthiness.
AUTHORITY_PINS_FALSE = (
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

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_ID = re.compile(r"^content:[0-9a-f]{64}$")


class PatternCandidateError(Exception):
    """A candidate or disposition is malformed, unsupported, or unsatisfied."""


class PatternCandidateValidationError(PatternCandidateError):
    """A stored or supplied artifact does not hold up: tampered, forged, or out of vocabulary."""


def _get_spec(detector_id: Any):
    """Resolve a detector's spec, lazily.

    The import is deliberately function-local: `pattern_candidate_specs` imports this module at load
    time to build the repository spec, so importing it back at *our* load time would be a cycle. By
    the time any candidate is built, validated or serialized, the registry is safe to import.
    """
    from modules.pattern_candidate_specs import get_spec

    return get_spec(detector_id)


# -- helpers -------------------------------------------------------------------------------


def _digest(content: Mapping[str, Any]) -> str:
    try:
        return canonical_digest(dict(content))
    except ObservationError as exc:  # pragma: no cover - defensive
        raise PatternCandidateValidationError(
            f"candidate content is not serializable: {exc}"
        ) from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PatternCandidateValidationError(message)


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PatternCandidateValidationError(f"{label} must be a non-empty string")
    return value


def _require_hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise PatternCandidateValidationError(f"{label} must be a 64-character lowercase sha256")
    return value


def _require_prefixed(value: Any, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise PatternCandidateValidationError(f"{label} must start with {prefix!r}")
    _require_hex64(value[len(prefix) :], label)
    return value


def _require_false(value: Any, label: str) -> None:
    # Identity, not truthiness: `0` and `""` are falsy, and a pin that accepts them is a pin that
    # can be turned off by someone who never had to say the word False.
    if value is not False:
        raise PatternCandidateValidationError(f"{label} must be exactly False")


def _require_true(value: Any, label: str) -> None:
    if value is not True:
        raise PatternCandidateValidationError(f"{label} must be exactly True")


def _closed_keys(record: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    if not isinstance(record, Mapping):
        raise PatternCandidateValidationError(f"{label} must be a mapping")
    unknown = set(record) - allowed
    if unknown:
        raise PatternCandidateValidationError(
            f"{label} carries unknown field(s): {sorted(unknown)} — the vocabulary is closed"
        )
    missing = allowed - set(record)
    if missing:
        raise PatternCandidateValidationError(f"{label} is missing field(s): {sorted(missing)}")


def _occurrence_order(record: Mapping[str, Any]) -> tuple[str, str, str]:
    # The unique index, therefore a total order. Never a count.
    return (
        str(record["scope_id"]),
        str(record["source_artifact_digest"]),
        str(record["derivation_version"]),
    )


def _validate_occurrence(record: Any, *, subject_entity_id: str) -> None:
    _closed_keys(record, OCCURRENCE_FIELDS, "occurrence")

    _require_hex64(record["scope_id"], "occurrence scope_id")
    _require(
        record["scope_type"] in ALLOWED_SCOPE_TYPES,
        f"occurrence scope_type must be one of {sorted(ALLOWED_SCOPE_TYPES)}",
    )
    _require_text(record["scope_label"], "occurrence scope_label")
    _require_text(record["scope_path"], "occurrence scope_path")
    _require_text(record["value"], "occurrence value")
    _require_hex64(record["entity_digest"], "occurrence entity_digest")
    _require_hex64(record["source_artifact_digest"], "occurrence source_artifact_digest")
    _require_text(record["derivation_version"], "occurrence derivation_version")
    _require_text(record["observed_at"], "occurrence observed_at")
    _require(
        record["coverage_state"] in ALLOWED_COVERAGE_STATES,
        f"occurrence coverage_state must be one of {sorted(ALLOWED_COVERAGE_STATES)}",
    )

    # The occurrence must be evidence for THIS subject: a `content:` entity's value is its digest.
    _require(
        record["value"] == subject_entity_id.split(":", 1)[1],
        "occurrence does not carry the candidate's subject content digest",
    )

    sensors = record["sensors"]
    _require(
        isinstance(sensors, Sequence) and not isinstance(sensors, str) and bool(sensors),
        "occurrence sensors must be a non-empty list",
    )

    supporting = record["supporting_observations"]
    _require(
        isinstance(supporting, Sequence) and not isinstance(supporting, str) and bool(supporting),
        "occurrence supporting_observations must be a non-empty list",
    )
    for item in supporting:
        _closed_keys(item, SUPPORTING_OBSERVATION_FIELDS, "supporting observation")

    relationships = record["relationships"]
    _require(
        isinstance(relationships, Sequence) and not isinstance(relationships, str),
        "occurrence relationships must be a list",
    )
    for item in relationships:
        _closed_keys(item, RELATIONSHIP_FIELDS, "relationship")


def _distinct_repository_scopes(occurrences: Sequence[Mapping[str, Any]]) -> set[str]:
    """A SET of scope ids — never a row count.

    One repository re-observed after a change is two entity rows with one scope_id (the unique index
    is `(entity_id, source_artifact_digest, derivation_version)`), and two identical files inside
    one
    repository are two relationships on one entity. Counting rows would call either of those
    "recurrence across repositories", which is the exact false positive this detector exists to
    avoid.
    """
    return {
        str(record["scope_id"])
        for record in occurrences
        if record.get("scope_type") == "repository"
    }


def _limitations_for(occurrences: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    codes = set(REQUIRED_LIMITATION_CODES)
    if any(record.get("coverage_state") != "complete" for record in occurrences):
        codes.add(LIMITATION_INVENTORY_INCOMPLETE)
    return tuple(sorted(codes))


# -- the candidate -------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PatternCandidate:
    """One immutable candidate version. Same facts, same detector => same `candidate_id`."""

    profile_id: str
    detector_id: str
    detector_version: str
    candidate_type: str
    subject: dict[str, Any]
    rule_id: str
    rule_version: int
    rule_parameters: dict[str, Any]
    unknowns: tuple[str, ...]
    limitations: tuple[str, ...]
    created_at: str

    # Detector-owned evidence. A repository candidate carries occurrences + a scope count; a
    # scientific candidate carries a structured `evidence` object. Each detector's spec serializes
    # exactly its own extra fields, so a repository candidate's key set stays byte-for-byte itself.
    supporting_occurrences: tuple[dict[str, Any], ...] = ()
    distinct_repository_scope_count: int = 0
    evidence: dict[str, Any] | None = None

    counterevidence: tuple[Any, ...] = ()
    candidate_state: str = CANDIDATE_STATE_READY
    rule_satisfied: bool = True

    is_hypothesis: bool = False
    is_finding: bool = False
    is_verified: bool = False
    is_published: bool = False
    authorizes_action: bool = False
    authorizes_execution: bool = False
    authorizes_release: bool = False
    activates_profile: bool = False
    contacts_gateway: bool = False
    uses_llm: bool = False
    operator_review_required: bool = True

    schema_version: int = PATTERN_CANDIDATE_SCHEMA_VERSION
    generator_version: str = GENERATOR_VERSION
    deterministic_serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    digest_algorithm: str = DIGEST_ALGORITHM
    candidate_key: str = ""
    artifact_digest: str = ""
    candidate_id: str = ""

    # -- serialization --

    def _raw(self) -> dict[str, Any]:
        raw = {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "candidate_key": self.candidate_key,
            "created_at": self.created_at,
            "generator_version": self.generator_version,
            "deterministic_serialization_version": self.deterministic_serialization_version,
            "artifact_digest": self.artifact_digest,
            "digest_algorithm": self.digest_algorithm,
            "profile_id": self.profile_id,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "candidate_type": self.candidate_type,
            "candidate_state": self.candidate_state,
            "subject": dict(self.subject),
            "counterevidence": list(self.counterevidence),
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_parameters": dict(self.rule_parameters),
            "rule_satisfied": self.rule_satisfied,
            "unknowns": list(self.unknowns),
            "limitations": list(self.limitations),
            "is_hypothesis": self.is_hypothesis,
            "is_finding": self.is_finding,
            "is_verified": self.is_verified,
            "is_published": self.is_published,
            "authorizes_action": self.authorizes_action,
            "authorizes_execution": self.authorizes_execution,
            "authorizes_release": self.authorizes_release,
            "activates_profile": self.activates_profile,
            "contacts_gateway": self.contacts_gateway,
            "uses_llm": self.uses_llm,
            "operator_review_required": self.operator_review_required,
        }
        # The detector adds exactly its own extra fields (repository: occurrences + scope count;
        # scientific: `evidence`). Order is irrelevant — digest and stored JSON both sort keys.
        raw.update(_get_spec(self.detector_id).serialize_extra(self))
        return raw

    def digest_content(self) -> dict[str, Any]:
        """Everything the digest covers — which is everything except the three volatile fields.

        Evidence, unknowns, limitations, ownership, detector version, rule parameters and every
        authority pin are all inside. There is nowhere to edit that the digest does not see.
        """
        return {k: v for k, v in self._raw().items() if k not in VOLATILE_FIELDS}

    def compute_digest(self) -> str:
        return _digest(self.digest_content())

    def compute_key(self) -> str:
        # Key material is detector-owned. The repository detector keys on
        # {profile, detector, type, subject}; a detector that must distinguish two locators serving
        # the same bytes (scientific) adds the locator. The repository material is unchanged, so its
        # key is byte-for-byte what it always was.
        return CANDIDATE_KEY_PREFIX + _digest(_get_spec(self.detector_id).key_material(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self._raw()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PatternCandidate:
        # Resolve the detector first: it decides the valid key set. An unknown detector fails closed
        # here, before any field is read.
        detector_id = raw.get("detector_id") if isinstance(raw, Mapping) else None
        spec = _get_spec(detector_id)
        _closed_keys(raw, SPINE_FIELDS | spec.extra_fields, "candidate")
        kwargs: dict[str, Any] = {
            "schema_version": raw["schema_version"],
            "candidate_id": raw["candidate_id"],
            "candidate_key": raw["candidate_key"],
            "created_at": raw["created_at"],
            "generator_version": raw["generator_version"],
            "deterministic_serialization_version": raw["deterministic_serialization_version"],
            "artifact_digest": raw["artifact_digest"],
            "digest_algorithm": raw["digest_algorithm"],
            "profile_id": raw["profile_id"],
            "detector_id": raw["detector_id"],
            "detector_version": raw["detector_version"],
            "candidate_type": raw["candidate_type"],
            "candidate_state": raw["candidate_state"],
            "subject": dict(raw["subject"]),
            "counterevidence": tuple(raw["counterevidence"]),
            "rule_id": raw["rule_id"],
            "rule_version": raw["rule_version"],
            "rule_parameters": dict(raw["rule_parameters"]),
            "rule_satisfied": raw["rule_satisfied"],
            "unknowns": tuple(raw["unknowns"]),
            "limitations": tuple(raw["limitations"]),
            "is_hypothesis": raw["is_hypothesis"],
            "is_finding": raw["is_finding"],
            "is_verified": raw["is_verified"],
            "is_published": raw["is_published"],
            "authorizes_action": raw["authorizes_action"],
            "authorizes_execution": raw["authorizes_execution"],
            "authorizes_release": raw["authorizes_release"],
            "activates_profile": raw["activates_profile"],
            "contacts_gateway": raw["contacts_gateway"],
            "uses_llm": raw["uses_llm"],
            "operator_review_required": raw["operator_review_required"],
        }
        # Detector-owned fields, read only where the detector declares them. `_closed_keys` above
        # already refused any that do not belong, so a repository candidate cannot smuggle evidence.
        if "supporting_occurrences" in spec.extra_fields:
            kwargs["supporting_occurrences"] = tuple(
                dict(item) for item in raw["supporting_occurrences"]
            )
        if "distinct_repository_scope_count" in spec.extra_fields:
            kwargs["distinct_repository_scope_count"] = raw["distinct_repository_scope_count"]
        if "evidence" in spec.extra_fields:
            kwargs["evidence"] = dict(raw["evidence"])
        return cls(**kwargs)

    # -- the gate --

    def validate(self) -> None:
        """Fail closed. Recomputes both identities, and enforces every closed vocabulary.

        The digest cannot be the only gate — anyone who can write a record can re-stamp it — so the
        vocabularies below are checked on their own terms.
        """
        _require(
            self.schema_version == PATTERN_CANDIDATE_SCHEMA_VERSION,
            f"unsupported candidate schema version: {self.schema_version!r}",
        )
        _require(
            self.deterministic_serialization_version == DETERMINISTIC_SERIALIZATION_VERSION,
            "unsupported serialization version",
        )
        _require(
            self.digest_algorithm == DIGEST_ALGORITHM,
            f"unsupported digest algorithm: {self.digest_algorithm!r}",
        )
        _require_text(self.generator_version, "generator_version")
        _require_text(self.created_at, "created_at")

        # -- ownership: who declared this rule, and what did it produce. The detector resolves to
        # exactly one spec (fail closed on an unknown detector); the profile and candidate type must
        # be the ones that spec owns, so a valid body cannot be relabelled under another detector.
        _require(
            self.profile_id in ALLOWED_PROFILE_IDS,
            f"unsupported profile: {self.profile_id!r}",
        )
        spec = _get_spec(self.detector_id)
        _require(
            self.profile_id == spec.profile_id,
            f"detector {self.detector_id!r} is owned by {spec.profile_id!r}, "
            f"not {self.profile_id!r}",
        )
        _require(
            self.candidate_type == spec.candidate_type,
            f"detector {self.detector_id!r} produces {spec.candidate_type!r}, "
            f"not {self.candidate_type!r}",
        )
        _require_text(self.detector_version, "detector_version")
        _require(
            self.candidate_state in ALLOWED_CANDIDATE_STATES,
            f"unsupported candidate state: {self.candidate_state!r}",
        )

        # -- subject: every detector's subject is a content identity (LHP-INTEL-03A)
        _closed_keys(self.subject, frozenset({"entity_id", "entity_type"}), "subject")
        entity_id = self.subject["entity_id"]
        _require(
            isinstance(entity_id, str) and bool(_CONTENT_ID.match(entity_id)),
            "subject entity_id must be content:<64-character lowercase sha256>",
        )
        _require(self.subject["entity_type"] == "content", "subject entity_type must be 'content'")

        # -- a candidate exists because a rule fired; that is a fact, not a score
        _require_true(self.rule_satisfied, "rule_satisfied")

        # -- detector-owned: evidence shape, rule parameters, unknown/limitation vocabulary, and any
        # detector-specific invariant. Delegated to the spec, which is code-owned and closed.
        spec.validate_detector(self)

        # Counterevidence is a list of direct contradictory FACTS. An unknown is not one of those,
        # and dressing "I did not check" as "I checked and found otherwise" is the whole game.
        _require(isinstance(self.counterevidence, tuple), "counterevidence must be a list")
        for item in self.counterevidence:
            _require(
                not (isinstance(item, str) and item in ALLOWED_UNKNOWN_CODES),
                "an unknown is not counterevidence",
            )

        # -- authority: nothing here authorizes anything
        for pin in AUTHORITY_PINS_FALSE:
            _require_false(getattr(self, pin), pin)
        _require_true(self.operator_review_required, "operator_review_required")

        # -- identity, recomputed last so a forged one cannot ride a valid body. The key material is
        # detector-owned; the digest covers the whole serialized body, extra fields included.
        expected_key = self.compute_key()
        _require_prefixed(self.candidate_key, CANDIDATE_KEY_PREFIX, "candidate_key")
        _require(
            self.candidate_key == expected_key,
            "candidate_key does not follow from the detector's key material",
        )

        expected_digest = self.compute_digest()
        _require_hex64(self.artifact_digest, "artifact_digest")
        _require(
            self.artifact_digest == expected_digest,
            "artifact_digest does not match the candidate's content",
        )
        _require_prefixed(self.candidate_id, CANDIDATE_ID_PREFIX, "candidate_id")
        _require(
            self.candidate_id == CANDIDATE_ID_PREFIX + expected_digest,
            "candidate_id is not this candidate's artifact digest",
        )


def build_pattern_candidate(
    *,
    profile_id: str,
    detector_id: str,
    detector_version: str,
    candidate_type: str,
    subject_entity_id: str,
    occurrences: Sequence[Mapping[str, Any]],
    rule_id: str,
    rule_version: int,
    rule_parameters: Mapping[str, Any],
    created_at: str,
    counterevidence: Sequence[Any] = (),
) -> PatternCandidate:
    """Build one candidate and validate it. Raises rather than emit an unsatisfied candidate."""
    if not isinstance(subject_entity_id, str) or not _CONTENT_ID.match(subject_entity_id):
        raise PatternCandidateValidationError(
            f"subject must be a content identity, not {subject_entity_id!r}"
        )

    records = [dict(record) for record in occurrences]
    for record in records:
        _validate_occurrence(record, subject_entity_id=subject_entity_id)
    records.sort(key=_occurrence_order)

    scopes = _distinct_repository_scopes(records)
    minimum = REQUIRED_RULE_PARAMETERS["min_distinct_repository_scopes"]
    if len(scopes) < minimum:
        raise PatternCandidateError(
            f"the rule requires at least {minimum} distinct repository scopes, "
            f"and this evidence covers {len(scopes)}"
        )

    draft = PatternCandidate(
        profile_id=profile_id,
        detector_id=detector_id,
        detector_version=detector_version,
        candidate_type=candidate_type,
        subject={"entity_id": subject_entity_id, "entity_type": "content"},
        supporting_occurrences=tuple(records),
        counterevidence=tuple(counterevidence),
        rule_id=rule_id,
        rule_version=rule_version,
        rule_parameters=dict(rule_parameters),
        distinct_repository_scope_count=len(scopes),
        unknowns=tuple(sorted(REQUIRED_UNKNOWN_CODES)),
        limitations=_limitations_for(records),
        created_at=created_at,
    )
    return finalize_candidate(draft)


def finalize_candidate(draft: PatternCandidate) -> PatternCandidate:
    """Stamp a draft's detector-owned key and its identity, then validate.

    Both detectors' builders end here — the repository builder above, and the scientific builder in
    the service — so the key/digest/candidate_id/validate path is defined exactly once. The key
    material is spec-driven, so a draft need only carry its detector-owned fields; this computes the
    rest and refuses to return anything that does not satisfy its own contract.
    """
    keyed = replace(draft, candidate_key=draft.compute_key())
    digest = keyed.compute_digest()
    record = replace(keyed, artifact_digest=digest, candidate_id=CANDIDATE_ID_PREFIX + digest)
    record.validate()
    return record


# -- the disposition -----------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PatternDisposition:
    """One local operator decision about one exact candidate version.

    It is not a finding, not a Gateway approval, and not authorization to publish or execute — and
    it has no field in which any of those could be written. `accepted_for_followup` means "worth
    more investigation", never "true".
    """

    candidate_id: str
    candidate_artifact_digest: str
    operator_id: str
    decision: str
    reason: str
    created_at: str

    review_method: str = "local_cli"
    #: We do not claim an authentication that does not exist. A CLI flag is not an identity.
    operator_identity_authenticated: bool = False

    schema_version: int = PATTERN_DISPOSITION_SCHEMA_VERSION
    digest_algorithm: str = DIGEST_ALGORITHM
    artifact_digest: str = ""
    disposition_id: str = ""

    def _raw(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "disposition_id": self.disposition_id,
            "candidate_id": self.candidate_id,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "created_at": self.created_at,
            "operator_id": self.operator_id,
            "decision": self.decision,
            "reason": self.reason,
            "review_method": self.review_method,
            "operator_identity_authenticated": self.operator_identity_authenticated,
            "artifact_digest": self.artifact_digest,
            "digest_algorithm": self.digest_algorithm,
        }

    def digest_content(self) -> dict[str, Any]:
        return {k: v for k, v in self._raw().items() if k not in DISPOSITION_VOLATILE_FIELDS}

    def compute_digest(self) -> str:
        return _digest(self.digest_content())

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self._raw()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PatternDisposition:
        _closed_keys(raw, frozenset(DISPOSITION_FIELDS), "disposition")
        return cls(
            schema_version=raw["schema_version"],
            disposition_id=raw["disposition_id"],
            candidate_id=raw["candidate_id"],
            candidate_artifact_digest=raw["candidate_artifact_digest"],
            created_at=raw["created_at"],
            operator_id=raw["operator_id"],
            decision=raw["decision"],
            reason=raw["reason"],
            review_method=raw["review_method"],
            operator_identity_authenticated=raw["operator_identity_authenticated"],
            artifact_digest=raw["artifact_digest"],
            digest_algorithm=raw["digest_algorithm"],
        )

    def validate(self) -> None:
        _require(
            self.schema_version == PATTERN_DISPOSITION_SCHEMA_VERSION,
            f"unsupported disposition schema version: {self.schema_version!r}",
        )
        _require(
            self.digest_algorithm == DIGEST_ALGORITHM,
            f"unsupported digest algorithm: {self.digest_algorithm!r}",
        )
        _require_prefixed(self.candidate_id, CANDIDATE_ID_PREFIX, "candidate_id")
        _require_hex64(self.candidate_artifact_digest, "candidate_artifact_digest")
        _require(
            self.candidate_id == CANDIDATE_ID_PREFIX + self.candidate_artifact_digest,
            "the disposition's candidate_id and candidate_artifact_digest disagree",
        )
        _require_text(self.operator_id, "operator_id")
        _require_text(self.reason, "reason")
        _require_text(self.created_at, "created_at")
        _require(
            self.decision in ALLOWED_DECISIONS,
            f"unsupported decision: {self.decision!r} (allowed: {sorted(ALLOWED_DECISIONS)})",
        )
        _require(
            self.review_method in ALLOWED_REVIEW_METHODS,
            f"unsupported review method: {self.review_method!r}",
        )
        _require_false(self.operator_identity_authenticated, "operator_identity_authenticated")

        expected = self.compute_digest()
        _require_hex64(self.artifact_digest, "artifact_digest")
        _require(
            self.artifact_digest == expected,
            "artifact_digest does not match the disposition's content",
        )
        _require_prefixed(self.disposition_id, DISPOSITION_ID_PREFIX, "disposition_id")
        _require(
            self.disposition_id == DISPOSITION_ID_PREFIX + expected,
            "disposition_id is not this disposition's artifact digest",
        )


def build_pattern_disposition(
    *,
    candidate_id: str,
    candidate_artifact_digest: str,
    operator_id: str,
    decision: str,
    reason: str,
    created_at: str,
) -> PatternDisposition:
    """Build one disposition and validate it. Required fields are required, not defaulted."""
    draft = PatternDisposition(
        candidate_id=candidate_id if isinstance(candidate_id, str) else "",
        candidate_artifact_digest=(
            candidate_artifact_digest if isinstance(candidate_artifact_digest, str) else ""
        ),
        operator_id=operator_id if isinstance(operator_id, str) else "",
        decision=decision if isinstance(decision, str) else "",
        reason=reason if isinstance(reason, str) else "",
        created_at=created_at,
    )
    digest = draft.compute_digest()
    record = replace(draft, artifact_digest=digest, disposition_id=DISPOSITION_ID_PREFIX + digest)
    record.validate()
    return record
