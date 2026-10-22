"""Observation normalization — `normalized_observation.v1` (LHP-INTEL-01B).

Three questions, three layers, and only the middle one lives here:

    Observation   (LHP-INTEL-01A)  "What exists?"
    Normalization (this module)    "How can the system consistently represent what exists?"
    Intelligence  (later)          "Why does it matter?"

`repository_intake` hands over a transcript: 27 facts about one repository, 14 of them collections
(`["Dockerfile", "compose.yml"]`, `{"Python": 122}`). Nothing inside a transcript is addressable.
Two repositories that both use Docker share nothing; two clones of one project share nothing.

This module turns those facts into **primitives that have identity**: `technology:docker` is the
same entity wherever it is observed, a repository's root commit is the same entity in every clone,
and a file seen through two different observations is one entity that remembers both. That — and
nothing more — is normalization.

**It states no opinion, and cannot.** There is no score, no rank, no priority, no importance. That
is enforced structurally rather than by convention: a repository entity's `attributes` may only be
keyed by a member of the closed 27-name observation vocabulary, and that vocabulary contains no
word for significance. There is nowhere to put an opinion, so none can be stored.

**Nothing observed is lost.** The repository entity's attributes are *exactly* the determined
observations, and the source artifact is carried verbatim — so a consumer can always get back to
what was actually seen.

**Undetermined is not absent.** An observation that could not be established (an enumeration that
hit its bound) contributes no entities and is reported in `undetermined_observations`. Without
that, "I could not list the languages" would be indistinguishable from "this repository contains
no code" — a false conclusion manufactured by the plumbing.

**Deterministic and pure.** No database, no filesystem, no network, no clock: it is a function
from one validated artifact to one validated set of records. Digests exclude timestamps, so an
unchanged repository normalizes to byte-identical records forever — which is what lets the
persistence layer store the same observation twice and write it once.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.document_intake import OBSERVATION_TYPES as DOCUMENT_OBSERVATION_TYPES
from modules.document_intake import DocumentObservation, validate_document_observation
from modules.observations import CONFIDENCE_OBSERVED, ObservationError
from modules.repository_intake import OBSERVATION_TYPES as REPOSITORY_OBSERVATION_TYPES
from modules.repository_intake import RepositoryObservation, validate_observation

# The union of every observation vocabulary — the closed set of things a normalized fact is allowed
# to assert. It is what makes an opinion *unrepresentable* rather than merely discouraged: there is
# no member here named score, priority, importance, severity or quality, so there is nowhere to put
# one. Widening it is how a new observation source joins; it is not a place to add a verdict.
OBSERVED_FACT_TYPES = frozenset(REPOSITORY_OBSERVATION_TYPES) | frozenset(
    DOCUMENT_OBSERVATION_TYPES
)

NORMALIZED_OBSERVATION_SCHEMA_VERSION = 1
DETERMINISTIC_SERIALIZATION_VERSION = 1
# Bumped by 03A, and the bump is FORCED rather than cosmetic. A previously-observed document's
# artifact digest is unchanged by this packet (its collector did not change), so re-normalizing it
# under the old derivation version would produce the same `document:` entity against the same
# `(entity_id, source_artifact_digest, derivation_version)` — the unique index — while the set
# around it now also carries a `content:` entity. The insert would collide. A new derivation
# genuinely happened; saying so is what keeps the store insert-only.
DERIVATION_VERSION = "observation_normalizer/2"

# -- the closed vocabularies ----------------------------------------------------------------

ENTITY_REPOSITORY = "repository"
ENTITY_DOCUMENT = "document"
ENTITY_FILE = "file"
ENTITY_DIRECTORY = "directory"
ENTITY_LANGUAGE = "language"
ENTITY_TECHNOLOGY = "technology"
ENTITY_REMOTE = "remote"
ENTITY_COMMIT = "commit"
# Exact bytes, identified by `SHA-256(raw bytes)` and nothing else (03A). Profile-neutral by
# construction: it is not a repository's file and not a document's body, it is the content that both
# of them turned out to be made of — which is precisely why they can meet on it.
ENTITY_CONTENT = "content"

ENTITY_TYPES = frozenset(
    {
        ENTITY_COMMIT,
        ENTITY_CONTENT,
        ENTITY_DIRECTORY,
        ENTITY_DOCUMENT,
        ENTITY_FILE,
        ENTITY_LANGUAGE,
        ENTITY_REMOTE,
        ENTITY_REPOSITORY,
        ENTITY_TECHNOLOGY,
    }
)

# A file exists only inside its repository; a language exists everywhere. That distinction is the
# whole point of the identity scheme: global entities are what let two repositories join.
_SCOPED_ENTITY_TYPES = frozenset({ENTITY_DIRECTORY, ENTITY_FILE})

# Entities whose id is a digest rather than their value. A repository is identified by the digest of
# its path (where it is); a document by the digest of its bytes (what it is) — so the same document
# copied to a second location is ONE entity observed twice, which is the join documents exist for.
# Content is identified by the digest of its bytes too, and is deliberately a SEPARATE id from the
# document's: `document:` is a subject that carries attributes and has a name, `content:` is the
# bytes and has neither. A repository file has no document subject, so without the neutral id there
# would be nothing for it to join *to*.
_DIGEST_IDENTITY = frozenset({ENTITY_CONTENT, ENTITY_DOCUMENT, ENTITY_REPOSITORY})

# The kind of thing an observation was made *of*. A scope is "the place I looked"; its id is always
# the SHA-256 of a canonical absolute path, whatever kind of thing lives there.
SCOPE_REPOSITORY = "repository"
SCOPE_DOCUMENT = "document"

OBSERVATION_SCOPE_TYPES = frozenset({SCOPE_DOCUMENT, SCOPE_REPOSITORY})

# The entity that *is* the subject of an observation — the only one allowed to carry the full set of
# observed attributes. A `language:Python` entity is derived from a repository's facts; it does not
# own them, and it must not pretend to.
SUBJECT_ENTITY_TYPES = frozenset({ENTITY_DOCUMENT, ENTITY_REPOSITORY})

REL_CONTAINS_DIRECTORY = "contains_directory"
REL_CONTAINS_FILE = "contains_file"
REL_CONTAINS_REPOSITORY = "contains_repository"
REL_FILE_HAS_CONTENT = "file_has_content"
REL_HAS_HEAD_COMMIT = "has_head_commit"
REL_HAS_REMOTE = "has_remote"
REL_HAS_ROOT_COMMIT = "has_root_commit"
REL_HAS_SUBMODULE = "has_submodule"
REL_USES_TECHNOLOGY = "uses_technology"
REL_WRITTEN_IN = "written_in"

RELATIONSHIP_TYPES = frozenset(
    {
        REL_CONTAINS_DIRECTORY,
        REL_CONTAINS_FILE,
        REL_CONTAINS_REPOSITORY,
        REL_FILE_HAS_CONTENT,
        REL_HAS_HEAD_COMMIT,
        REL_HAS_REMOTE,
        REL_HAS_ROOT_COMMIT,
        REL_HAS_SUBMODULE,
        REL_USES_TECHNOLOGY,
        REL_WRITTEN_IN,
    }
)

# Declared lookup tables, not judgements — the same class of operation `repository_intake` already
# performs when it maps `pyproject.toml` to the build system "python". A marker file is evidence of
# a technology; saying so adds no opinion and loses no fact, because the marker file remains an
# entity in its own right.
_CONTAINER_TECHNOLOGY = {
    ".dockerignore": "docker",
    "Containerfile": "docker",
    "Dockerfile": "docker",
    "compose.yaml": "docker_compose",
    "compose.yml": "docker_compose",
    "docker-compose.yaml": "docker_compose",
    "docker-compose.yml": "docker_compose",
}

_CI_TECHNOLOGY = {
    ".circleci/config.yml": "circleci",
    ".drone.yml": "drone",
    ".gitlab-ci.yml": "gitlab_ci",
    ".travis.yml": "travis_ci",
    "Jenkinsfile": "jenkins",
    "azure-pipelines.yml": "azure_pipelines",
}

# CI systems that own a directory rather than a single file.
_CI_PREFIX_TECHNOLOGY = ((".github/workflows/", "github_actions"),)

# A bound on the records one artifact may produce. The input is already bounded by the observation
# layer, so this can only trip on a hand-built artifact — in which case, fail rather than expand.
_MAX_RECORDS = 25_000

_HEX_DIGITS = frozenset("0123456789abcdef")


class NormalizationError(Exception):
    """Base class for every fail-closed normalization condition (single catch point)."""


class NormalizedRecordError(NormalizationError):
    """A normalized record is malformed, inconsistent, or does not match its digest."""


# -- the records ----------------------------------------------------------------------------

_ENTITY_KEYS = frozenset(
    {
        "schema_version",
        "serialization_version",
        "entity_id",
        "entity_type",
        "value",
        "attributes",
        "scope_id",
        "source_observation_set_id",
        "source_artifact_digest",
        "source_observations",
        "derivation_version",
        "observed_at",
        "digest",
    }
)

_RELATIONSHIP_KEYS = frozenset(
    {
        "schema_version",
        "serialization_version",
        "relationship_id",
        "relationship_type",
        "subject_id",
        "object_id",
        "value",
        "scope_id",
        "source_observation_set_id",
        "source_artifact_digest",
        "source_observations",
        "derivation_version",
        "observed_at",
        "digest",
    }
)

_SET_KEYS = frozenset(
    {
        "schema_version",
        "serialization_version",
        "source_observation_set_id",
        "source_artifact_digest",
        "source_schema_version",
        "source_artifact",
        "scope_type",
        "scope_id",
        "scope_path",
        "observed_at",
        "entities",
        "relationships",
        "undetermined_observations",
        "derivation_version",
        "lighthouse_version",
        "profile_id",
        "normalization_digest",
    }
)


@dataclass(frozen=True)
class NormalizedEntity:
    """One thing that exists, with an identity that is stable across repositories and across time.

    Carries the seven fields every normalized object must have — identity, type, value,
    source observation, derivation version, timestamp, digest — and no field for significance.
    """

    entity_id: str
    entity_type: str
    value: str
    scope_id: str
    source_observation_set_id: str
    source_artifact_digest: str
    source_observations: tuple[str, ...]
    observed_at: str
    attributes: dict[str, Any] = field(default_factory=dict)
    derivation_version: str = DERIVATION_VERSION
    schema_version: int = NORMALIZED_OBSERVATION_SCHEMA_VERSION
    serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    digest: str = ""

    def _content(self) -> dict[str, Any]:
        """Digest-covered content. Excludes exactly `observed_at`, `source_observation_set_id`
        and the digest itself — so the digest is a content identity, not a timestamp, and an
        unchanged repository re-observed tomorrow reproduces it exactly."""
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "value": self.value,
            "attributes": self.attributes,
            "scope_id": self.scope_id,
            "source_artifact_digest": self.source_artifact_digest,
            "source_observations": sorted(self.source_observations),
            "derivation_version": self.derivation_version,
        }

    def compute_digest(self) -> str:
        return normalized_record_digest(self._content())

    def validate(self) -> None:
        """Fail closed, and recompute the digest: a forger who edits the value and re-stamps the
        digest still has to explain why `entity_id` no longer derives from what it claims to be."""
        _require_exact_int(
            "schema_version", self.schema_version, NORMALIZED_OBSERVATION_SCHEMA_VERSION
        )
        _require_exact_int(
            "serialization_version",
            self.serialization_version,
            DETERMINISTIC_SERIALIZATION_VERSION,
        )
        if self.entity_type not in ENTITY_TYPES:
            raise NormalizedRecordError(f"unknown entity_type: {self.entity_type!r}")
        if not isinstance(self.value, str) or not self.value:
            raise NormalizedRecordError("value must be a non-empty string")
        _require_sha256("scope_id", self.scope_id)
        self._validate_identity()
        self._validate_attributes()
        _require_provenance(self)
        _require_utc_timestamp("observed_at", self.observed_at)
        _require_derivation_version(self.derivation_version)
        _require_sha256("digest", self.digest)
        if self.digest != self.compute_digest():
            raise NormalizedRecordError("digest mismatch — the content does not match its digest")

    def _validate_identity(self) -> None:
        """`entity_id` is derived, never asserted: every id must be reproducible from the record."""
        if not isinstance(self.entity_id, str) or not self.entity_id.startswith(
            f"{self.entity_type}:"
        ):
            raise NormalizedRecordError(
                f"entity_id must begin with {self.entity_type!r}: {self.entity_id!r}"
            )
        tail = self.entity_id[len(self.entity_type) + 1 :]
        if self.entity_type in _DIGEST_IDENTITY:
            # A repository's identity is its own path digest — which, for a nested repository, is
            # NOT the id of the repository we were observing when we found it. A document's identity
            # is the digest of its bytes, so a copy is the same entity and an edit is a new one.
            if not _is_sha256(tail):
                raise NormalizedRecordError(
                    f"entity_id for a {self.entity_type} must carry a SHA-256 identity"
                )
            return
        expected = (
            f"{self.scope_id}:{self.value}"
            if self.entity_type in _SCOPED_ENTITY_TYPES
            else self.value
        )
        if tail != expected:
            raise NormalizedRecordError(
                f"entity_id does not derive from its type and value: {self.entity_id!r}"
            )

    def _validate_attributes(self) -> None:
        if not isinstance(self.attributes, dict):
            raise NormalizedRecordError("attributes must be an object")
        if self.entity_type not in SUBJECT_ENTITY_TYPES and self.attributes:
            raise NormalizedRecordError(
                f"attributes may only be carried by the subject of an observation, "
                f"not {self.entity_type!r}"
            )
        for key in self.attributes:
            # The observation vocabulary is closed and has no word for score, priority, importance,
            # severity or quality. This is what makes an opinion *unrepresentable* rather than
            # merely discouraged.
            if key not in OBSERVED_FACT_TYPES:
                raise NormalizedRecordError(
                    f"attribute key is not an observation type: {key!r} — a normalized fact may "
                    "only assert what was observed"
                )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Validates first — an invalid record never serializes."""
        self.validate()
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "value": self.value,
            "attributes": dict(sorted(self.attributes.items())),
            "scope_id": self.scope_id,
            "source_observation_set_id": self.source_observation_set_id,
            "source_artifact_digest": self.source_artifact_digest,
            "source_observations": sorted(self.source_observations),
            "derivation_version": self.derivation_version,
            "observed_at": self.observed_at,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: Any) -> NormalizedEntity:
        """Strict, fail-closed deserialization: missing and unexpected keys both raise."""
        _require_exact_keys("normalized entity", data, _ENTITY_KEYS)
        record = cls(
            entity_id=data["entity_id"],
            entity_type=data["entity_type"],
            value=data["value"],
            scope_id=data["scope_id"],
            source_observation_set_id=data["source_observation_set_id"],
            source_artifact_digest=data["source_artifact_digest"],
            source_observations=tuple(_require_list("source_observations", data)),
            observed_at=data["observed_at"],
            attributes=data["attributes"],
            derivation_version=data["derivation_version"],
            schema_version=data["schema_version"],
            serialization_version=data["serialization_version"],
            digest=data["digest"],
        )
        record.validate()
        return record


@dataclass(frozen=True)
class NormalizedRelationship:
    """One observed connection between two entities. The predicate is the fact; there is no
    strength, no weight, and no confidence — a relationship either was observed or was not."""

    relationship_id: str
    relationship_type: str
    subject_id: str
    object_id: str
    value: str
    scope_id: str
    source_observation_set_id: str
    source_artifact_digest: str
    source_observations: tuple[str, ...]
    observed_at: str
    derivation_version: str = DERIVATION_VERSION
    schema_version: int = NORMALIZED_OBSERVATION_SCHEMA_VERSION
    serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    digest: str = ""

    def _content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "subject_id": self.subject_id,
            "object_id": self.object_id,
            "value": self.value,
            "scope_id": self.scope_id,
            "source_artifact_digest": self.source_artifact_digest,
            "source_observations": sorted(self.source_observations),
            "derivation_version": self.derivation_version,
        }

    def compute_digest(self) -> str:
        return normalized_record_digest(self._content())

    def validate(self) -> None:
        _require_exact_int(
            "schema_version", self.schema_version, NORMALIZED_OBSERVATION_SCHEMA_VERSION
        )
        _require_exact_int(
            "serialization_version",
            self.serialization_version,
            DETERMINISTIC_SERIALIZATION_VERSION,
        )
        if self.relationship_type not in RELATIONSHIP_TYPES:
            raise NormalizedRecordError(f"unknown relationship_type: {self.relationship_type!r}")
        _require_entity_reference("subject_id", self.subject_id)
        _require_entity_reference("object_id", self.object_id)
        if self.value != _relationship_value(
            self.subject_id, self.relationship_type, self.object_id
        ):
            raise NormalizedRecordError("value must be the canonical form of its own triple")
        if self.relationship_id != _relationship_id(
            self.subject_id, self.relationship_type, self.object_id
        ):
            raise NormalizedRecordError(
                f"relationship_id does not derive from its triple: {self.relationship_id!r}"
            )
        _require_sha256("scope_id", self.scope_id)
        _require_provenance(self)
        _require_utc_timestamp("observed_at", self.observed_at)
        _require_derivation_version(self.derivation_version)
        _require_sha256("digest", self.digest)
        if self.digest != self.compute_digest():
            raise NormalizedRecordError("digest mismatch — the content does not match its digest")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "subject_id": self.subject_id,
            "object_id": self.object_id,
            "value": self.value,
            "scope_id": self.scope_id,
            "source_observation_set_id": self.source_observation_set_id,
            "source_artifact_digest": self.source_artifact_digest,
            "source_observations": sorted(self.source_observations),
            "derivation_version": self.derivation_version,
            "observed_at": self.observed_at,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: Any) -> NormalizedRelationship:
        _require_exact_keys("normalized relationship", data, _RELATIONSHIP_KEYS)
        record = cls(
            relationship_id=data["relationship_id"],
            relationship_type=data["relationship_type"],
            subject_id=data["subject_id"],
            object_id=data["object_id"],
            value=data["value"],
            scope_id=data["scope_id"],
            source_observation_set_id=data["source_observation_set_id"],
            source_artifact_digest=data["source_artifact_digest"],
            source_observations=tuple(_require_list("source_observations", data)),
            observed_at=data["observed_at"],
            derivation_version=data["derivation_version"],
            schema_version=data["schema_version"],
            serialization_version=data["serialization_version"],
            digest=data["digest"],
        )
        record.validate()
        return record


@dataclass(frozen=True)
class NormalizedObservationSet:
    """Everything one observation normalizes into, plus the observation itself, carried verbatim."""

    source_observation_set_id: str
    source_artifact_digest: str
    source_schema_version: int
    source_artifact: dict[str, Any]
    scope_type: str
    scope_id: str
    scope_path: str
    observed_at: str
    entities: tuple[NormalizedEntity, ...]
    relationships: tuple[NormalizedRelationship, ...]
    undetermined_observations: tuple[str, ...] = ()
    lighthouse_version: str | None = None
    profile_id: str | None = None
    derivation_version: str = DERIVATION_VERSION
    schema_version: int = NORMALIZED_OBSERVATION_SCHEMA_VERSION
    serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    normalization_digest: str = ""

    def _content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "source_artifact_digest": self.source_artifact_digest,
            "source_schema_version": self.source_schema_version,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "scope_path": self.scope_path,
            "derivation_version": self.derivation_version,
            "lighthouse_version": self.lighthouse_version,
            "profile_id": self.profile_id,
            "entities": sorted(entity.digest for entity in self.entities),
            "relationships": sorted(rel.digest for rel in self.relationships),
            "undetermined_observations": sorted(self.undetermined_observations),
        }

    def compute_digest(self) -> str:
        return normalized_record_digest(self._content())

    def validate(self) -> None:
        _require_exact_int(
            "schema_version", self.schema_version, NORMALIZED_OBSERVATION_SCHEMA_VERSION
        )
        _require_exact_int(
            "serialization_version",
            self.serialization_version,
            DETERMINISTIC_SERIALIZATION_VERSION,
        )
        if self.scope_type not in OBSERVATION_SCOPE_TYPES:
            raise NormalizedRecordError(f"unknown scope_type: {self.scope_type!r}")
        _require_sha256("scope_id", self.scope_id)
        if not isinstance(self.scope_path, str) or not self.scope_path:
            raise NormalizedRecordError("scope_path must be a non-empty string")
        _require_sha256("source_artifact_digest", self.source_artifact_digest)
        _require_utc_timestamp("observed_at", self.observed_at)
        _require_derivation_version(self.derivation_version)

        # The carried artifact is re-validated, and must be the one the digest claims. Provenance
        # that is not checked is provenance that can be swapped. `scope_type` decides which
        # producer's validator owns it — which is why it is a digest-covered field and not a label:
        # a set that lies about what kind of thing it observed would be checked by the wrong rules.
        try:
            if self.scope_type == SCOPE_DOCUMENT:
                carried = validate_document_observation(self.source_artifact).artifact_digest
            else:
                carried = validate_observation(self.source_artifact).artifact_digest
        except ObservationError as exc:
            raise NormalizedRecordError(f"the carried source artifact is invalid: {exc}") from exc
        if carried != self.source_artifact_digest:
            raise NormalizedRecordError("source_artifact does not match source_artifact_digest")

        for key in self.undetermined_observations:
            if key not in OBSERVED_FACT_TYPES:
                raise NormalizedRecordError(f"unknown undetermined observation type: {key!r}")

        known: set[str] = set()
        for entity in self.entities:
            entity.validate()
            if entity.source_artifact_digest != self.source_artifact_digest:
                raise NormalizedRecordError(f"{entity.entity_id} came from another observation")
            known.add(entity.entity_id)
        for rel in self.relationships:
            rel.validate()
            if rel.source_artifact_digest != self.source_artifact_digest:
                raise NormalizedRecordError(f"{rel.relationship_id} came from another observation")
            # A relationship that points at nothing is not a fact.
            for endpoint in (rel.subject_id, rel.object_id):
                if endpoint not in known:
                    raise NormalizedRecordError(
                        f"relationship endpoint has no entity: {endpoint!r}"
                    )

        _require_sha256("normalization_digest", self.normalization_digest)
        if self.normalization_digest != self.compute_digest():
            raise NormalizedRecordError(
                "normalization_digest mismatch — the content does not match its digest"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "source_observation_set_id": self.source_observation_set_id,
            "source_artifact_digest": self.source_artifact_digest,
            "source_schema_version": self.source_schema_version,
            "source_artifact": self.source_artifact,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "scope_path": self.scope_path,
            "observed_at": self.observed_at,
            "entities": [entity.to_dict() for entity in self.entities],
            "relationships": [rel.to_dict() for rel in self.relationships],
            "undetermined_observations": sorted(self.undetermined_observations),
            "derivation_version": self.derivation_version,
            "lighthouse_version": self.lighthouse_version,
            "profile_id": self.profile_id,
            "normalization_digest": self.normalization_digest,
        }

    @classmethod
    def from_dict(cls, data: Any) -> NormalizedObservationSet:
        _require_exact_keys("normalized observation set", data, _SET_KEYS)
        record = cls(
            source_observation_set_id=data["source_observation_set_id"],
            source_artifact_digest=data["source_artifact_digest"],
            source_schema_version=data["source_schema_version"],
            source_artifact=data["source_artifact"],
            scope_type=data["scope_type"],
            scope_id=data["scope_id"],
            scope_path=data["scope_path"],
            observed_at=data["observed_at"],
            entities=tuple(
                NormalizedEntity.from_dict(item) for item in _require_list("entities", data)
            ),
            relationships=tuple(
                NormalizedRelationship.from_dict(item)
                for item in _require_list("relationships", data)
            ),
            undetermined_observations=tuple(_require_list("undetermined_observations", data)),
            lighthouse_version=data["lighthouse_version"],
            profile_id=data["profile_id"],
            derivation_version=data["derivation_version"],
            schema_version=data["schema_version"],
            serialization_version=data["serialization_version"],
            normalization_digest=data["normalization_digest"],
        )
        record.validate()
        return record


def normalized_record_digest(content: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON — the same recipe every Lighthouse contract digest uses."""
    try:
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise NormalizedRecordError(f"record content is not serializable: {exc}") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_normalized_entity(raw: Any) -> NormalizedEntity:
    """Parse and fully validate a serialized entity. Fail-closed; never returns a partial."""
    return NormalizedEntity.from_dict(raw)


def validate_normalized_relationship(raw: Any) -> NormalizedRelationship:
    """Parse and fully validate a serialized relationship. Fail-closed."""
    return NormalizedRelationship.from_dict(raw)


def validate_normalized_observation_set(raw: Any) -> NormalizedObservationSet:
    """Parse and fully validate a serialized normalization result. Fail-closed."""
    return NormalizedObservationSet.from_dict(raw)


# -- normalization ---------------------------------------------------------------------------


def normalize_repository_observation(
    artifact: RepositoryObservation | dict[str, Any],
) -> NormalizedObservationSet:
    """Normalize one `repository_observation.v1` artifact into entities and relationships.

    Pure: no database, no filesystem, no network, no clock. The same artifact always yields the
    same records, and an unchanged repository observed a year apart yields the same digests.

    Every input is re-validated even when it arrives as a dataclass — a record that was not
    checked here is a record that could have been edited after it was checked elsewhere.
    """
    observation = _require_valid_artifact(artifact)

    determined: dict[str, Any] = {}
    undetermined: list[str] = []
    for item in observation.observations:
        if item.confidence == CONFIDENCE_OBSERVED:
            determined[item.observation_type] = item.value
        else:
            undetermined.append(item.observation_type)

    if "repository.name" not in determined:
        raise NormalizationError(
            "cannot normalize an observation with no determined repository name"
        )

    scope_id = observation.repository_id
    subject_id = _entity_id(ENTITY_REPOSITORY, "", identity=scope_id)

    # The repository entity is derived from *every* determined observation, and carries them all:
    # that is what makes "nothing observed is lost" a property rather than an aspiration.
    drafts: dict[str, dict[str, Any]] = {
        subject_id: {
            "entity_type": ENTITY_REPOSITORY,
            "value": determined["repository.name"],
            "sources": set(determined),
            "attributes": dict(determined),
        }
    }
    links: dict[str, dict[str, Any]] = {}

    def add_entity(entity_type: str, value: str, source: str, *, identity: str = "") -> str:
        entity_id = _entity_id(entity_type, value, scope_id=scope_id, identity=identity)
        draft = drafts.setdefault(
            entity_id, {"entity_type": entity_type, "value": value, "sources": set()}
        )
        draft["sources"].add(source)
        return entity_id

    def add_link(relationship_type: str, object_id: str, source: str, *, subject: str = "") -> None:
        # The subject is the repository unless a caller names another one. 03A is the first
        # relationship whose subject is not the repository — a *file* has content; the repository
        # merely contains the file — so the subject became a parameter rather than a constant.
        origin = subject or subject_id
        relationship_id = _relationship_id(origin, relationship_type, object_id)
        link = links.setdefault(
            relationship_id,
            {
                "subject_id": origin,
                "relationship_type": relationship_type,
                "object_id": object_id,
                "sources": set(),
            },
        )
        link["sources"].add(source)

    def add_pair(
        entity_type: str, value: str, relation: str, source: str, *, identity: str = ""
    ) -> None:
        add_link(relation, add_entity(entity_type, value, source, identity=identity), source)

    # The repository's shape. A symlink or a socket is observed, but it is not claimed to be a file.
    for entry in determined.get("repository.top_level_entries") or []:
        kind, name = entry.get("kind"), entry.get("name")
        if kind == "file":
            add_pair(ENTITY_FILE, name, REL_CONTAINS_FILE, "repository.top_level_entries")
        elif kind == "directory":
            add_pair(ENTITY_DIRECTORY, name, REL_CONTAINS_DIRECTORY, "repository.top_level_entries")

    for source, relation in (
        ("license.files", REL_CONTAINS_FILE),
        ("documentation.readme_files", REL_CONTAINS_FILE),
        ("containerization.files", REL_CONTAINS_FILE),
        ("ci.configurations", REL_CONTAINS_FILE),
    ):
        for name in determined.get(source) or []:
            add_pair(ENTITY_FILE, name, relation, source)

    for source in ("documentation.doc_directories", "tests.directories"):
        for name in determined.get(source) or []:
            add_pair(ENTITY_DIRECTORY, name, REL_CONTAINS_DIRECTORY, source)

    # A marker file is evidence of a technology; both the file and the technology are facts.
    for source in ("build.systems", "packaging.package_managers"):
        for entry in determined.get(source) or []:
            add_pair(ENTITY_TECHNOLOGY, entry["name"], REL_USES_TECHNOLOGY, source)
            add_pair(ENTITY_FILE, entry["marker"], REL_CONTAINS_FILE, source)

    for name in determined.get("containerization.files") or []:
        technology = _CONTAINER_TECHNOLOGY.get(name)
        if technology:
            add_pair(ENTITY_TECHNOLOGY, technology, REL_USES_TECHNOLOGY, "containerization.files")

    for path in determined.get("ci.configurations") or []:
        technology = _ci_technology(path)
        if technology:
            add_pair(ENTITY_TECHNOLOGY, technology, REL_USES_TECHNOLOGY, "ci.configurations")

    for language in determined.get("code.languages") or {}:
        add_pair(ENTITY_LANGUAGE, language, REL_WRITTEN_IN, "code.languages")

    for entry in determined.get("git.remotes") or []:
        add_pair(ENTITY_REMOTE, entry["url"], REL_HAS_REMOTE, "git.remotes")

    # A repository found inside this one gets the identity it *would* have had if it had been
    # observed directly — which is how a checked-out submodule and a nested clone become one thing.
    for source, relation in (
        ("repository.nested_repositories", REL_CONTAINS_REPOSITORY),
        ("git.submodules", REL_HAS_SUBMODULE),
    ):
        for relative in determined.get(source) or []:
            nested = _nested_repository_id(observation.repository_path, relative)
            add_pair(ENTITY_REPOSITORY, Path(relative).name, relation, source, identity=nested)

    for sha in determined.get("git.root_commits") or []:
        add_pair(ENTITY_COMMIT, sha, REL_HAS_ROOT_COMMIT, "git.root_commits")

    head_commit = determined.get("git.head_commit")
    if head_commit:
        add_pair(ENTITY_COMMIT, head_commit, REL_HAS_HEAD_COMMIT, "git.head_commit")

    # Content identity (03A). ONLY a `hashed` outcome becomes an entity: a file we could not read,
    # or chose not to, has no content identity, and inventing one for it would turn "I don't know"
    # into a claim. Every other outcome is already accounted for in the artifact, which is where it
    # belongs — as the absence of a fact, not as a fact about absence.
    for record in determined.get("content.files") or []:
        if record.get("status") != "hashed":
            continue
        digest, relative = record["content_digest"], record["path"]
        # Deduped by `add_entity`: two paths with the same bytes are ONE content entity, which is
        # also what the unique index requires. Both paths survive — as two relationships.
        content_id = add_entity(ENTITY_CONTENT, digest, "content.files", identity=digest)
        file_id = add_entity(ENTITY_FILE, relative, "content.files")
        add_link(REL_FILE_HAS_CONTENT, content_id, "content.files", subject=file_id)

    if len(drafts) + len(links) > _MAX_RECORDS:
        raise NormalizationError(
            f"observation normalizes to more than {_MAX_RECORDS} records — refusing to expand"
        )

    entities = tuple(
        sorted(
            (_build_entity(entity_id, draft, observation) for entity_id, draft in drafts.items()),
            key=lambda entity: entity.entity_id,
        )
    )
    relationships = tuple(
        sorted(
            (
                _build_relationship(relationship_id, link["subject_id"], link, observation)
                for relationship_id, link in links.items()
            ),
            key=lambda rel: rel.relationship_id,
        )
    )

    result = NormalizedObservationSet(
        source_observation_set_id=observation.observation_set_id,
        source_artifact_digest=observation.artifact_digest,
        source_schema_version=observation.schema_version,
        source_artifact=observation.to_dict(),
        scope_type=SCOPE_REPOSITORY,
        scope_id=scope_id,
        scope_path=observation.repository_path,
        observed_at=observation.observed_at,
        entities=entities,
        relationships=relationships,
        undetermined_observations=tuple(sorted(undetermined)),
        lighthouse_version=observation.lighthouse_version,
        profile_id=observation.profile_id,
    )
    result = replace(result, normalization_digest=result.compute_digest())
    result.validate()
    return result


def normalize_document_observation(
    artifact: DocumentObservation | dict[str, Any],
) -> NormalizedObservationSet:
    """Normalize one `document_observation.v1` artifact into the SAME fact boundary.

    A document becomes exactly one entity, identified by the digest of its bytes — so the same
    document found in two places is one entity that remembers both, and an edited document is a
    different one. Its `attributes` are precisely the determined observations: nothing observed is
    lost, and nothing that was not observed is invented.

    **It emits no relationships, and that is a decision rather than an omission.** A document on its
    own has none. A `format:pdf` node would be a join of almost no value when `document.format` is
    already an attribute, and inventing an edge to make the model look richer is interpretation —
    which is the question this layer does not answer.

    **Since 03A it emits a second entity, and no edge between them.** The join that used to be
    impossible — a document that also lives inside an observed repository — is available now that
    repository files are identified by their bytes. The neutral `content:<sha256>` entity is that
    join, and it is emitted *alongside* the document rather than instead of it: a document is a
    subject with a name and attributes, content is bytes with neither. What is NOT emitted is a
    `document_has_content` edge, because both ids carry the SAME digest — a document's identity
    already IS the digest of its bytes — so the edge would be derivable from either endpoint
    alone. An edge that carries no information is decoration, and decoration in an evidence chain
    is how a fact layer starts asserting things.

    Pure: no database, no filesystem, no network, no clock.
    """
    observation = _require_valid_document(artifact)

    determined: dict[str, Any] = {}
    undetermined: list[str] = []
    for item in observation.observations:
        if item.confidence == CONFIDENCE_OBSERVED:
            determined[item.observation_type] = item.value
        else:
            undetermined.append(item.observation_type)

    # Identity is what we FOUND (the bytes); the scope is where we LOOKED (the path). Two digests,
    # two questions, and conflating them is how a copied file becomes two unrelated documents.
    content_digest = determined["document.content_digest"]
    entity_id = _entity_id(ENTITY_DOCUMENT, "", identity=content_digest)

    entity = NormalizedEntity(
        entity_id=entity_id,
        entity_type=ENTITY_DOCUMENT,
        value=determined["document.name"],
        scope_id=observation.document_id,
        source_observation_set_id=observation.observation_set_id,
        source_artifact_digest=observation.artifact_digest,
        source_observations=tuple(sorted(determined)),
        observed_at=observation.observed_at,
        attributes=dict(determined),
    )
    entity = replace(entity, digest=entity.compute_digest())

    # The neutral content identity: the same bytes, under the same id a repository file would get.
    # It carries no attributes — it is not a subject, and the bytes have no name of their own.
    content = NormalizedEntity(
        entity_id=_entity_id(ENTITY_CONTENT, "", identity=content_digest),
        entity_type=ENTITY_CONTENT,
        value=content_digest,
        scope_id=observation.document_id,
        source_observation_set_id=observation.observation_set_id,
        source_artifact_digest=observation.artifact_digest,
        source_observations=("document.content_digest",),
        observed_at=observation.observed_at,
    )
    content = replace(content, digest=content.compute_digest())

    result = NormalizedObservationSet(
        source_observation_set_id=observation.observation_set_id,
        source_artifact_digest=observation.artifact_digest,
        source_schema_version=observation.schema_version,
        source_artifact=observation.to_dict(),
        scope_type=SCOPE_DOCUMENT,
        scope_id=observation.document_id,
        scope_path=observation.document_path,
        observed_at=observation.observed_at,
        entities=tuple(sorted((entity, content), key=lambda item: item.entity_id)),
        relationships=(),
        undetermined_observations=tuple(sorted(undetermined)),
        lighthouse_version=observation.lighthouse_version,
        profile_id=observation.profile_id,
    )
    result = replace(result, normalization_digest=result.compute_digest())
    result.validate()
    return result


# -- construction helpers ----------------------------------------------------------------------


def _require_valid_document(
    artifact: DocumentObservation | dict[str, Any],
) -> DocumentObservation:
    # `ObservationError`, not `DocumentIntakeError`: a document artifact fails validation by raising
    # `ObservationValidationError` — a *shared* error, not a document-flavoured one. Catching only
    # the collector's own base let that error escape this wrapper entirely (LHP-INTEL-01D-A).
    try:
        if isinstance(artifact, DocumentObservation):
            artifact.validate()
            return artifact
        return validate_document_observation(artifact)
    except ObservationError as exc:
        raise NormalizedRecordError(f"the document observation is invalid: {exc}") from exc


def _require_valid_artifact(
    artifact: RepositoryObservation | dict[str, Any],
) -> RepositoryObservation:
    try:
        if isinstance(artifact, RepositoryObservation):
            artifact.validate()
            return artifact
        return validate_observation(artifact)
    except ObservationError as exc:
        raise NormalizationError(f"cannot normalize an invalid observation: {exc}") from exc


def _build_entity(
    entity_id: str, draft: dict[str, Any], observation: RepositoryObservation
) -> NormalizedEntity:
    entity = NormalizedEntity(
        entity_id=entity_id,
        entity_type=draft["entity_type"],
        value=draft["value"],
        attributes=dict(sorted(draft.get("attributes", {}).items())),
        scope_id=observation.repository_id,
        source_observation_set_id=observation.observation_set_id,
        source_artifact_digest=observation.artifact_digest,
        source_observations=tuple(sorted(draft["sources"])),
        observed_at=observation.observed_at,
    )
    return replace(entity, digest=entity.compute_digest())


def _build_relationship(
    relationship_id: str,
    subject_id: str,
    link: dict[str, Any],
    observation: RepositoryObservation,
) -> NormalizedRelationship:
    relationship = NormalizedRelationship(
        relationship_id=relationship_id,
        relationship_type=link["relationship_type"],
        subject_id=subject_id,
        object_id=link["object_id"],
        value=_relationship_value(subject_id, link["relationship_type"], link["object_id"]),
        scope_id=observation.repository_id,
        source_observation_set_id=observation.observation_set_id,
        source_artifact_digest=observation.artifact_digest,
        source_observations=tuple(sorted(link["sources"])),
        observed_at=observation.observed_at,
    )
    return replace(relationship, digest=relationship.compute_digest())


def _entity_id(entity_type: str, value: str, *, scope_id: str = "", identity: str = "") -> str:
    if entity_type in _DIGEST_IDENTITY:
        return f"{entity_type}:{identity}"
    if entity_type in _SCOPED_ENTITY_TYPES:
        return f"{entity_type}:{scope_id}:{value}"
    return f"{entity_type}:{value}"


def _relationship_value(subject_id: str, relationship_type: str, object_id: str) -> str:
    return f"{subject_id} {relationship_type} {object_id}"


def _relationship_id(subject_id: str, relationship_type: str, object_id: str) -> str:
    """Identity from the triple, hashed over canonical JSON so that a separator character inside
    a path or a URL can never make two different triples collide."""
    canonical = json.dumps(
        [subject_id, relationship_type, object_id], separators=(",", ":"), sort_keys=False
    )
    return f"rel:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _nested_repository_id(repository_path: str, relative_path: str) -> str:
    """The identity a nested repository would have if it were observed in its own right.

    `repository_intake` derives a repository's id from the SHA-256 of its canonical absolute path,
    so the same rule applied to the parent's canonical path plus the relative path reproduces it
    exactly. No filesystem access: the parent's path is already canonical, and the scan that found
    this repository never followed a symlink to get there.
    """
    return hashlib.sha256(str(Path(repository_path) / relative_path).encode("utf-8")).hexdigest()


def _ci_technology(path: str) -> str | None:
    technology = _CI_TECHNOLOGY.get(path)
    if technology:
        return technology
    for prefix, named in _CI_PREFIX_TECHNOLOGY:
        if path.startswith(prefix):
            return named
    return None


# -- validation helpers ------------------------------------------------------------------------


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX_DIGITS


def _require_sha256(label: str, value: Any) -> None:
    if not _is_sha256(value):
        raise NormalizedRecordError(f"{label} must be a 64-char lowercase hex SHA-256")


def _require_exact_int(label: str, value: Any, expected: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NormalizedRecordError(f"{label} must be an integer")
    if value != expected:
        raise NormalizedRecordError(
            f"unsupported {label}: {value!r} (this build understands {expected})"
        )


def _require_utc_timestamp(label: str, value: Any) -> None:
    if not isinstance(value, str):
        raise NormalizedRecordError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise NormalizedRecordError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise NormalizedRecordError(f"{label} must be timezone-aware")


def _require_derivation_version(value: Any) -> None:
    # A record produced by a different derivation is not ours to reinterpret: fail, never assume.
    if value != DERIVATION_VERSION:
        raise NormalizedRecordError(
            f"unsupported derivation_version: {value!r} (this build produces {DERIVATION_VERSION})"
        )


def _require_provenance(record: NormalizedEntity | NormalizedRelationship) -> None:
    """No object may exist without provenance — the one rule with no exception."""
    if (
        not isinstance(record.source_observation_set_id, str)
        or not record.source_observation_set_id
    ):
        raise NormalizedRecordError("source_observation_set_id must be a non-empty string")
    _require_sha256("source_artifact_digest", record.source_artifact_digest)
    sources = record.source_observations
    if not isinstance(sources, (list, tuple)) or not sources:
        raise NormalizedRecordError(
            "source_observations must be non-empty — a normalized fact without provenance is not "
            "a fact"
        )
    if len(set(sources)) != len(sources):
        raise NormalizedRecordError("source_observations must not repeat")
    for source in sources:
        if source not in OBSERVED_FACT_TYPES:
            raise NormalizedRecordError(f"unknown source observation type: {source!r}")


def _require_entity_reference(label: str, value: Any) -> None:
    if not isinstance(value, str) or ":" not in value:
        raise NormalizedRecordError(f"{label} must be an entity id")
    if value.split(":", 1)[0] not in ENTITY_TYPES:
        raise NormalizedRecordError(f"{label} does not name a known entity type: {value!r}")


def _require_exact_keys(label: str, data: Any, expected: frozenset[str]) -> None:
    if not isinstance(data, dict):
        raise NormalizedRecordError(f"{label} must be an object")
    keys = set(data)
    missing = expected - keys
    if missing:
        raise NormalizedRecordError(f"{label} is missing required keys: {sorted(missing)}")
    unexpected = keys - expected
    if unexpected:
        raise NormalizedRecordError(f"{label} carries unexpected keys: {sorted(unexpected)}")


def _require_list(label: str, data: dict[str, Any]) -> list[Any]:
    value = data[label]
    if not isinstance(value, list):
        raise NormalizedRecordError(f"{label} must be a list")
    return value
