"""The read-only observation query surface (LHP-INTEL-02A).

Five packets built a fact pipeline — observe, normalize, persist — and the facts accumulated where
nobody could see them. This is the layer that answers *"show me what Lighthouse knows"*, and it is
defined just as much by the question it refuses: *"tell me what Lighthouse thinks."* There is no
score here, no rank, no priority, and no name for one. It counts, it lists, it shows. That is all.

It is a **boundary**, not a convenience. The CLI does not reach into `modules.db` — it comes through
here, and so will every later consumer (MCP, a UI, a report). Storage layout stays behind one door.

Four properties do the work:

* **A read never writes — and "never" includes the database file.** `sqlite3.connect(path)`
  *creates* a missing database, so a careless read on a machine that has never observed anything
  would leave one behind, destroying the guarantee 01E established. Every query here goes through
  `db.get_readonly_connection()` (`mode=ro`), so SQLite itself rejects INSERT / UPDATE / DELETE /
  CREATE. The protection is a mechanism, not a promise; a mutation test removes it and watches the
  suite go red.
* **Reads validate, exactly as writes do.** Every stored record is round-tripped through its
  contract, which recomputes the digest *and* enforces the closed attribute vocabulary. A forged
  `importance` attribute is therefore refused on the way *out* of the store — with or without a
  re-stamped digest — and refused loudly, because a tampered fact store is a signal.
* **Nothing is ranked.** `stats` orders its counts by name. Ordering them by count descending would
  be "the most common technology" — the first ranking this system ever built.
* **The clock is never read.** `since`/`until` are compared against *stored* timestamps. This module
  may parse a timestamp; it may never ask what time it is, because a read that stamps its own output
  with the current time is not deterministic.

**A note on provenance, and a correction.** The first draft of this surface was specified to
report `"source": "repository_intake"`. That was wrong: it confused the *subject* of an observation
with the *sensor* that established it. A repository is a thing being looked at, not a thing that
looks. Deriving a producer name from `scope_type` would have fabricated provenance the store does
not hold. The substrate already records the real thing — every `Observation` carries the `source`
pinned by its `ObservationSpec` — so this layer reports **`sensors`**, read verbatim out of the
stored artifact, and filters on them. No new column, no contract change.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from modules import db
from modules.document_intake import validate_document_observation
from modules.observation_normalizer import (
    DETERMINISTIC_SERIALIZATION_VERSION,
    DOCUMENT_OBSERVATION_TYPES,
    ENTITY_TYPES,
    OBSERVATION_SCOPE_TYPES,
    RELATIONSHIP_TYPES,
    REPOSITORY_OBSERVATION_TYPES,
    SCOPE_DOCUMENT,
    SCOPE_REPOSITORY,
    NormalizedEntity,
    NormalizedRecordError,
    NormalizedRelationship,
)
from modules.observations import ObservationError
from modules.repository_intake import validate_observation as validate_repository_observation

OBSERVATION_QUERY_SCHEMA_VERSION = 1

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

# Git's abbreviated object ids: enough characters to be unambiguous in practice, and an error rather
# than a guess when they are not.
MIN_ID_PREFIX = 8

# An intersection is over scopes an operator can actually hold in their head, and every scope costs
# one indexed query. Bounded like everything else here.
MAX_SCOPE_IDS = 10

# The artifact-side filters have no index to lean on, so they scan — and, like every other read
# surface in this system, the scan is bounded. When the bound bites, the answer says so.
_SCAN_LIMIT = 5_000

# One observation set's own records. Bounded by what a single collector produced, not by the store.
_SET_RECORD_LIMIT = 10_000

# The identity projection an intersection is computed over. Above 01B's own per-artifact record cap,
# so it can only bite on a scope observed many times — and when it does, the answer discloses it
# rather than quietly returning a smaller intersection than the store holds.
_SCOPE_ENTITY_ID_LIMIT = 50_000

# `object_id` carries no index, so relationship evidence is a covering-index scan. One per command —
# never one per occurrence — and capped.
_RELATIONSHIP_SCAN_LIMIT = 2_000

#: How many distinct entities of one type may be enumerated in a single question (LHP-INTEL-03B).
_ENTITY_ID_SCAN_LIMIT = 20_000

#: The 03A observation that says whether a repository's file inventory was fully accounted for.
_COVERAGE_OBSERVATION = "content.coverage"

# Which producer's contract owns a stored artifact. `scope_type` is digest-covered precisely because
# it decides this: the artifact is re-validated by the same code that wrote it, so a swapped payload
# fails its own digest recompute rather than being read as evidence.
_ARTIFACT_VALIDATORS = {
    SCOPE_REPOSITORY: validate_repository_observation,
    SCOPE_DOCUMENT: validate_document_observation,
}

# A scope's human label is a fact somebody observed, not a basename somebody derived.
_SCOPE_LABEL_OBSERVATION = {
    SCOPE_REPOSITORY: "repository.name",
    SCOPE_DOCUMENT: "document.name",
}

# The observation vocabulary, merged from the two collectors that declare it — read, never
# re-declared. A fourth copy of "what a sensor is called" is exactly the drift 01D-A was written to
# end. Every value here is an `ObservationSpec.source`: the sensor that established the fact.
OBSERVATION_TYPES: dict[str, str] = {
    name: spec.source
    for vocabulary in (REPOSITORY_OBSERVATION_TYPES, DOCUMENT_OBSERVATION_TYPES)
    for name, spec in vocabulary.items()
}

SENSORS = frozenset(OBSERVATION_TYPES.values())

_HEX = re.compile(r"^[0-9a-f]+$")


class ObservationQueryError(Exception):
    """The question cannot be answered as asked. Fail closed: an unknown word is an error, never an
    empty result — "that is not a word I know" and "Lighthouse knows nothing about that" are very
    different claims, and only one of them is true."""


class ObservationNotFound(ObservationQueryError):
    """No observation carries that id."""


class AmbiguousObservationId(ObservationQueryError):
    """An abbreviated id matches more than one observation. Every candidate is named; none is
    chosen. There is no closest match here, and no first match either."""

    def __init__(self, prefix: str, candidates: list[str]) -> None:
        self.prefix = prefix
        self.candidates = list(candidates)
        listed = "\n  ".join(self.candidates)
        super().__init__(
            f"{prefix!r} matches {len(self.candidates)} observations — name one of them:\n"
            f"  {listed}"
        )


class ScopeNotFound(ObservationQueryError):
    """No observed scope carries that id.

    Fail closed, and deliberately not "an empty intersection": *"these two repositories share
    nothing"* is a lie about a scope that was never observed. An unanswerable question is not a
    negative answer.
    """


class AmbiguousScopeId(ObservationQueryError):
    """An abbreviated scope id matches more than one scope. Every candidate is named; none is
    chosen."""

    def __init__(self, prefix: str, candidates: list[str]) -> None:
        self.prefix = prefix
        self.candidates = list(candidates)
        listed = "\n  ".join(self.candidates)
        super().__init__(
            f"{prefix!r} matches {len(self.candidates)} scopes — name one of them:\n  {listed}"
        )


class TamperedObservationError(ObservationQueryError):
    """A stored record no longer satisfies its own contract."""


# -- the surface ---------------------------------------------------------------------------


def list_observations(*, limit: int | None = None) -> dict[str, Any]:
    """Every observation Lighthouse has recorded, newest first."""
    return search_observations(limit=limit)


def search_observations(
    *,
    scope_type: str | None = None,
    sensor: str | None = None,
    observation_type: str | None = None,
    digest: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The declared filters, and no others. No fuzzy matching, no semantic matching, no ranking."""
    bound = _bounded_limit(limit)
    scope_type = _require_member("scope_type", scope_type, OBSERVATION_SCOPE_TYPES)
    sensor = _require_member("sensor", sensor, SENSORS)
    observation_type = _require_member(
        "observation_type", observation_type, frozenset(OBSERVATION_TYPES)
    )
    digest = _require_digest("digest", digest)
    since = _require_timestamp("since", since)
    until = _require_timestamp("until", until)

    if not db.observation_tables_exist():
        return {"count": 0, "observations": [], "scan_truncated": False}

    # `sensor` and `observation_type` live inside the stored artifact, which has no index — so they
    # are applied here rather than in SQL. They must be applied to the WHOLE matching set and never
    # to a page SQL has already truncated: filtering a truncated page silently returns the wrong
    # answer, and looks exactly like the right one.
    inspects_artifact = sensor is not None or observation_type is not None

    rows = db.fetch_observation_sets(
        limit=_SCAN_LIMIT if inspects_artifact else bound,
        scope_type=scope_type,
        digest=digest,
        since=since,
        until=until,
    )
    truncated = inspects_artifact and len(rows) == _SCAN_LIMIT

    records = [_observation_record(row) for row in rows]
    if inspects_artifact:
        records = [
            record
            for record in records
            if (sensor is None or sensor in record["sensors"])
            and (observation_type is None or observation_type in record["observation_types"])
        ]
    records = records[:bound]

    return {"count": len(records), "observations": records, "scan_truncated": truncated}


def show_observation(observation_id: str) -> dict[str, Any]:
    """One observation, expanded: what was looked at, what was established, and by which sensor.

    The stored artifact's observations are returned verbatim, so provenance survives the trip —
    each one carries its sensor, its confidence, and when it was observed.
    """
    digest = resolve_observation_id(observation_id)
    row = db.fetch_observation_set(digest)
    if row is None:  # resolve() proved it existed; a race is still a not-found, never a guess
        raise ObservationNotFound(f"no observation matches {observation_id!r}")

    artifact_digest = row["source_artifact_digest"]
    entities = [
        _entity_record(entity)
        for entity in db.fetch_normalized_entities(
            source_artifact_digest=artifact_digest, limit=_SET_RECORD_LIMIT
        )
    ]
    relationships = [
        _relationship_record(relationship)
        for relationship in db.fetch_normalized_relationships(
            source_artifact_digest=artifact_digest, limit=_SET_RECORD_LIMIT
        )
    ]

    return {
        **_observation_record(row),
        "observations": json.loads(row["artifact_json"]).get("observations", []),
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "entities": entities,
        "relationships": relationships,
    }


def observation_stats() -> dict[str, Any]:
    """Counts. Not "the most", not "the biggest", not "the interesting ones" — how many."""
    known = db.observation_tables_exist()
    by_scope = db.count_observation_sets_by_scope_type() if known else {}
    by_entity_type = db.count_normalized_entities_by_type() if known else {}
    by_relationship_type = db.count_normalized_relationships_by_type() if known else {}

    return {
        "observation_sets": sum(by_scope.values()),
        "repositories": by_scope.get(SCOPE_REPOSITORY, 0),
        "documents": by_scope.get(SCOPE_DOCUMENT, 0),
        "entities": db.count_normalized_entities() if known else 0,
        # How many distinct things are known, as against how many times they were observed.
        "distinct_entities": db.count_distinct_normalized_entities() if known else 0,
        "relationships": db.count_normalized_relationships() if known else 0,
        # Ordered by NAME, and every member of the closed vocabulary appears even at zero. Ordering
        # these by count descending would be "the most common technology" — a ranking — and this
        # layer does not rank. Counting is the whole job.
        "entity_types": {name: by_entity_type.get(name, 0) for name in sorted(ENTITY_TYPES)},
        "relationship_types": {
            name: by_relationship_type.get(name, 0) for name in sorted(RELATIONSHIP_TYPES)
        },
    }


def resolve_observation_id(value: str) -> str:
    """Resolve an observation id, whole or abbreviated.

    Git's abbreviated object ids, and deliberately nothing more: deterministic hexadecimal prefix
    matching, and no other kind. Exactly one match resolves it; none is an error; **two or more is
    an error that names every candidate** — never the closest, never the first, never a guess. The
    canonical identifier remains the full SHA-256 everywhere the system speaks it, and the full
    digest is always what comes back.
    """
    candidate = _require_hex("observation id", value)

    if not db.observation_tables_exist():
        raise ObservationNotFound(f"no observation matches {candidate!r}")

    if len(candidate) == 64:
        if db.fetch_observation_set(candidate) is None:
            raise ObservationNotFound(f"no observation matches {candidate!r}")
        return candidate

    matches = db.fetch_observation_set_digests_by_prefix(candidate)
    if not matches:
        raise ObservationNotFound(f"no observation matches {candidate!r}")
    if len(matches) > 1:
        raise AmbiguousObservationId(candidate, matches)
    return matches[0]


# -- cross-scope fact discovery (LHP-INTEL-02B) ---------------------------------------------


def entity_occurrences(entity_id: Any, *, limit: int | None = None) -> dict[str, Any]:
    """Every scope in which one exact entity was directly observed.

    **An occurrence is an entity row.** `normalized_entities.scope_id` is a stored column, so
    "directly observed in this scope" is a fact the store already holds — it does not have to be
    inferred from an edge, and it must not be: a document scope carries *zero* relationships (01C),
    and a repository entity is only ever a relationship *subject*. A relationship-only model would
    answer "the same document lives in two places" with silence.

    Relationships are attached as *additional* evidence of **how** — a list, empty where the store
    holds none, which is a fact rather than a gap.

    A well-formed id that names nothing is an empty result, not an error: *"that is not a word I
    know"* and *"Lighthouse knows nothing about that"* are different claims.
    """
    identity = _require_entity_id(entity_id)
    bound = _bounded_limit(limit)

    empty: dict[str, Any] = {
        "entity": identity,
        "occurrence_count": 0,
        "occurrences": [],
        "scan_truncated": False,
    }
    if not db.observation_tables_exist():
        return empty

    rows = db.fetch_normalized_entities(entity_ids=[identity["entity_id"]], limit=_SET_RECORD_LIMIT)
    if not rows:
        return empty
    truncated = len(rows) >= _SET_RECORD_LIMIT

    # Order, THEN bound: a limit applied before the ordering would return a deterministic answer to
    # a different question. The key is total — it is the unique index.
    rows.sort(key=_occurrence_order)
    selected = rows[:bound]

    sets = _observation_sets_for(selected)
    relationships, relationships_truncated = _relationships_for([identity["entity_id"]])
    occurrences = [_occurrence_record(row, sets, relationships) for row in selected]

    return {
        "entity": identity,
        "occurrence_count": len(occurrences),
        "occurrences": occurrences,
        "scan_truncated": truncated or relationships_truncated,
    }


def shared_entities(scope_ids: Any, *, limit: int | None = None) -> dict[str, Any]:
    """The exact entities directly observed in EVERY requested scope.

    Set intersection over stored identities — not similarity, not overlap, not a score. An entity
    missing from one requested scope is not shared, and three scopes means all three.

    Two structural consequences worth knowing before reading a result:

    * `file:` and `directory:` ids embed their scope, so they can never be shared. Two repositories
      that both contain a Dockerfile do not meet on the file — they meet on `technology:docker`.
    * `repository:` and `document:` ids are digests of a path and of *bytes* respectively, so they
      **can** be shared: a repository observed directly and as somebody's nested clone is one
      entity, and so is the same document filed in two places.
    """
    requested = _resolve_scope_ids(scope_ids)
    bound = _bounded_limit(limit)

    truncated = False
    per_scope: list[set[str]] = []
    for scope_id in requested:
        observed = db.fetch_scope_entity_ids(scope_id, limit=_SCOPE_ENTITY_ID_LIMIT)
        truncated = truncated or len(observed) >= _SCOPE_ENTITY_ID_LIMIT
        per_scope.append(set(observed))

    # The intersection is computed over the WHOLE of each scope, and only then bounded. Bounding
    # first would answer a different question and call it an intersection.
    common = sorted(set.intersection(*per_scope), key=_entity_order)
    selected = common[:bound]
    if not selected:
        return {
            "scopes": _scope_records(requested),
            "shared_entity_count": 0,
            "shared_entities": [],
            "scan_truncated": truncated,
        }

    rows: list[dict[str, Any]] = []
    for scope_id in requested:
        fetched = db.fetch_normalized_entities(
            scope_id=scope_id, entity_ids=selected, limit=_SET_RECORD_LIMIT
        )
        truncated = truncated or len(fetched) >= _SET_RECORD_LIMIT
        rows.extend(fetched)

    sets = _observation_sets_for(rows)
    relationships, relationships_truncated = _relationships_for(selected, scope_ids=requested)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=_occurrence_order):
        grouped.setdefault(row["entity_id"], []).append(
            _occurrence_record(row, sets, relationships)
        )

    shared: list[dict[str, Any]] = []
    for entity_id in selected:
        occurrences = grouped.get(entity_id, [])
        covered = {occurrence["scope_id"] for occurrence in occurrences}
        if covered != set(requested):
            # Every shared entity must carry evidence for every requested scope. If it does not, the
            # store disagrees with itself (or changed underneath the query) — say so, do not shrug.
            raise TamperedObservationError(
                f"{entity_id!r} is recorded in every requested scope, but its stored rows do not "
                f"cover them"
            )
        shared.append(
            {
                "entity_id": entity_id,
                "entity_type": entity_id.split(":", 1)[0],
                "occurrences": occurrences,
            }
        )

    return {
        "scopes": _scope_records(requested),
        "shared_entity_count": len(shared),
        "shared_entities": shared,
        "scan_truncated": truncated or relationships_truncated,
    }


def resolve_scope_id(value: str) -> str:
    """Resolve a scope id, whole or abbreviated.

    A scope id is `sha256(canonical absolute path)` for *both* kinds of scope, so — unlike an entity
    id — it is uniformly a digest, and 02A's abbreviation rule carries over unchanged: one match
    resolves, none is an error, and **two or more is an error that names every candidate**.
    """
    candidate = _require_hex("scope id", value)

    if not db.observation_tables_exist():
        raise ScopeNotFound(f"no observed scope matches {candidate!r}")

    matches = db.fetch_scope_ids_by_prefix(candidate)
    if not matches:
        raise ScopeNotFound(f"no observed scope matches {candidate!r}")
    if len(matches) > 1:
        raise AmbiguousScopeId(candidate, matches)
    return matches[0]


def entity_ids_by_type(entity_type: str, *, limit: int | None = None) -> dict[str, Any]:
    """Every distinct entity of one type, in identity order (LHP-INTEL-03B).

    The enumeration a rule needs before it can ask about anything: *which content identities does
    the store hold at all?* It counts nothing and ranks nothing — it lists.

    An unknown type is an **error**, never an empty result: "that is not a word I know" and
    "Lighthouse knows nothing about that" are different claims, and 02A settled which is which.
    """
    if entity_type not in ENTITY_TYPES:
        raise ObservationQueryError(
            f"unknown entity type: {entity_type!r} (known: {', '.join(sorted(ENTITY_TYPES))})"
        )

    bound = _ENTITY_ID_SCAN_LIMIT if limit is None else min(int(limit), _ENTITY_ID_SCAN_LIMIT)
    if not db.observation_tables_exist():
        return {
            "entity_type": entity_type,
            "count": 0,
            "entity_ids": [],
            "scan_truncated": False,
        }

    ids = db.fetch_distinct_entity_ids_by_type(entity_type, limit=bound)
    return {
        "entity_type": entity_type,
        "count": len(ids),
        "entity_ids": ids,
        # The caller has to be able to tell "these are all of them" from "these are some of them".
        "scan_truncated": len(ids) >= bound,
    }


def artifact_coverage(*, scope_id: str, source_artifact_digest: str) -> dict[str, Any] | None:
    """The content-coverage fact one artifact recorded, read verbatim off the re-validated artifact.

    03A's `content.coverage` says whether every tracked file in a repository was accounted for. That
    matters to any rule that reasons about what a repository *contains*, because incomplete coverage
    means the absence of a file is not evidence that the file is absent.

    Returns `None` when the artifact carries no coverage observation at all — which is what every
    artifact written before 03A looks like. `None` is a **third state**: it is not `complete`, and a
    caller that treats it as complete is claiming evidence that was never gathered.
    """
    stored = db.fetch_observation_set_by_artifact(scope_id, source_artifact_digest)
    if stored is None:
        return None

    artifact = _validated_artifact(stored)
    for observation in artifact.observations:
        if observation.observation_type == _COVERAGE_OBSERVATION:
            # `confidence == 0.0` means the collector could not determine coverage — undetermined,
            # which is not the same as "coverage was incomplete" and must not be flattened into it.
            if observation.confidence == 0.0 or observation.value is None:
                return None
            return dict(observation.value)
    return None


def _occurrence_record(
    row: dict[str, Any],
    sets: dict[tuple[str, str], tuple[dict[str, Any], Any]],
    relationships: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    """One entity, in one scope, as one artifact recorded it — evidence verbatim.

    There is no aggregate `confidence` here, and that is deliberate. Confidence belongs to an
    individual observation, and 01B builds an entity only out of observations it *determined* — so a
    scalar would be a derived constant `1.0` forever, asserting a variability that cannot exist, in
    exactly the slot where significance likes to hide. The per-observation confidences are emitted
    as they were stored, and nothing is collapsed.
    """
    entity = _entity_record(row)  # validates: digest recomputed, attribute vocabulary closed
    key = (entity["scope_id"], entity["source_artifact_digest"])
    stored = sets.get(key)
    if stored is None:
        raise TamperedObservationError(
            f"stored entity {entity['entity_id']!r} names an observation set the store does not "
            f"hold — its provenance cannot be verified"
        )
    observation_set, artifact = stored
    observations = {item.observation_type: item for item in artifact.observations}

    supporting: list[dict[str, Any]] = []
    for observation_type in entity["source_observations"]:
        item = observations.get(observation_type)
        if item is None:
            raise TamperedObservationError(
                f"stored entity {entity['entity_id']!r} rests on an observation its own artifact "
                f"does not carry: {observation_type!r}"
            )
        supporting.append(
            {
                "observation_type": item.observation_type,
                # The real provenance, read off the validated artifact. NOT a collector name derived
                # from `scope_type` — a repository is the subject of an observation, not a sensor.
                "sensor": item.source,
                "confidence": item.confidence,
                "observed_at": item.observed_at,
            }
        )

    return {
        "scope_id": observation_set["scope_id"],
        "scope_type": observation_set["scope_type"],
        "scope_label": _scope_label(observation_set, observations),
        "scope_path": observation_set["scope_path"],
        # `value` and the digest live HERE, not on the entity: an entity's digest covers the
        # artifact it came from, and the same document bytes can be filed under two different names.
        # Hoisting either one up would force a choice between two true answers.
        "value": entity["value"],
        "entity_digest": entity["digest"],
        "source_artifact_digest": entity["source_artifact_digest"],
        "derivation_version": entity["derivation_version"],
        "observed_at": entity["observed_at"],
        "sensors": sorted({item["sensor"] for item in supporting}),
        "supporting_observations": supporting,
        # Zero, one, or several. A scalar would have had to choose, and a document has none at all.
        "relationships": relationships.get(
            (entity["entity_id"], entity["source_artifact_digest"]), []
        ),
    }


def _observation_sets_for(
    rows: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], tuple[dict[str, Any], Any]]:
    """The observation set behind each row, with its artifact validated exactly once."""
    wanted = {(row["scope_id"], row["source_artifact_digest"]) for row in rows}
    sets: dict[tuple[str, str], tuple[dict[str, Any], Any]] = {}
    for scope_id in sorted({scope for scope, _ in wanted}):
        for stored in db.fetch_observation_sets(scope_id=scope_id, limit=_SET_RECORD_LIMIT):
            key = (stored["scope_id"], stored["source_artifact_digest"])
            if key in wanted and key not in sets:
                sets[key] = (stored, _validated_artifact(stored))
    return sets


def _validated_artifact(observation_set: dict[str, Any]) -> Any:
    """Re-validate a stored artifact through the contract of the producer that wrote it.

    `scope_type` decides which contract owns it — which is exactly why that column is
    digest-covered. The producer's `validate()` recomputes the artifact's own digest, and the
    recomputed digest must equal the one the entity rows point at: a substituted artifact is not
    evidence.
    """
    validator = _ARTIFACT_VALIDATORS.get(observation_set["scope_type"])
    if validator is None:
        raise TamperedObservationError(
            f"observation set {observation_set['normalization_digest']!r} claims an unknown scope "
            f"type: {observation_set['scope_type']!r}"
        )
    try:
        artifact = validator(json.loads(observation_set["artifact_json"]))
    except (ObservationError, KeyError, TypeError, ValueError) as exc:
        raise TamperedObservationError(
            f"the stored artifact of observation set "
            f"{observation_set['normalization_digest']!r} does not satisfy its own contract: {exc}"
        ) from exc

    if artifact.artifact_digest != observation_set["source_artifact_digest"]:
        digest = observation_set["normalization_digest"]
        raise TamperedObservationError(
            f"observation set {digest!r} carries an artifact whose digest is not the one its "
            f"records cite"
        )
    return artifact


def _relationships_for(
    entity_ids: Sequence[str], *, scope_ids: Sequence[str] | None = None
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], bool]:
    """Every direct relationship touching these entities, keyed by (entity, artifact).

    One bounded scan for the whole command — never one per occurrence — and every row validated
    before it is believed. A relationship is only ever evidence for the artifact that recorded it.
    """
    rows = db.fetch_relationships_for_entities(
        entity_ids, scope_ids=scope_ids, limit=_RELATIONSHIP_SCAN_LIMIT
    )
    truncated = len(rows) >= _RELATIONSHIP_SCAN_LIMIT

    wanted = set(entity_ids)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        record = _relationship_record(row)  # validates: digest recomputed, predicate closed
        for endpoint in {record["subject_id"], record["object_id"]} & wanted:
            grouped.setdefault((endpoint, record["source_artifact_digest"]), []).append(
                {
                    "relationship_type": record["relationship_type"],
                    # The endpoints, added by 03A because they stopped being redundant. Every 02B
                    # relationship had the *scope* as its subject and the queried entity as its
                    # object, so naming them again said nothing. `file_has_content` is the first
                    # whose subject is neither — it is a `file:` — and without it the store would
                    # hold "these bytes are in this repository" while refusing to say where.
                    "subject_id": record["subject_id"],
                    "object_id": record["object_id"],
                    "relationship_id": record["relationship_id"],
                    "relationship_digest": record["digest"],
                    "source_artifact_digest": record["source_artifact_digest"],
                    "derivation_version": record["derivation_version"],
                }
            )
    return grouped, truncated


def _scope_records(scope_ids: Sequence[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for scope_id in scope_ids:
        stored = db.fetch_observation_sets(scope_id=scope_id, limit=1)
        if not stored:
            raise ScopeNotFound(f"no observed scope matches {scope_id!r}")
        observation_set = stored[0]
        artifact = _validated_artifact(observation_set)
        observations = {item.observation_type: item for item in artifact.observations}
        records.append(
            {
                "scope_id": observation_set["scope_id"],
                "scope_type": observation_set["scope_type"],
                "scope_label": _scope_label(observation_set, observations),
                "scope_path": observation_set["scope_path"],
            }
        )
    return sorted(records, key=lambda record: record["scope_id"])


def _scope_label(observation_set: dict[str, Any], observations: dict[str, Any]) -> str:
    """A scope's label is an observed fact — `repository.name` / `document.name` — never a basename
    this layer derived. A display string is not an identity, and it may not become one by habit."""
    observation_type = _SCOPE_LABEL_OBSERVATION.get(observation_set["scope_type"])
    item = observations.get(observation_type) if observation_type else None
    if item is None or not isinstance(item.value, str) or not item.value:
        raise TamperedObservationError(
            f"observation set {observation_set['normalization_digest']!r} carries no observed name "
            f"for its scope"
        )
    return item.value


def _require_entity_id(value: Any) -> dict[str, str]:
    """Exact, always — and only the type is derived from it, because only the type is guaranteed.

    An entity id is NOT uniformly a digest: `technology:docker`, `language:Python` and
    `remote:<url>` are value-based. So there is no prefix rule here. One for the digest-tailed types
    alone would be a type-conditional second identifier system, and `technology:doc` resolving to
    `technology:docker` is fuzzy matching wearing a hat.

    The value is split on the FIRST colon only: a remote's id is `remote:<url>`, and a URL has
    colons in it.
    """
    if not isinstance(value, str) or not value.strip():
        raise ObservationQueryError(f"entity id must be a non-empty string: {value!r}")

    candidate = value.strip()
    entity_type, _, tail = candidate.partition(":")
    if entity_type not in ENTITY_TYPES or not tail:
        raise ObservationQueryError(
            f"entity id must be `<type>:<value>`, with the type one of "
            f"{', '.join(sorted(ENTITY_TYPES))}: {value!r}"
        )
    return {"entity_id": candidate, "entity_type": entity_type}


def _resolve_scope_ids(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ObservationQueryError("scopes must be given as a list of scope ids")
    if len(values) < 2:
        raise ObservationQueryError(
            "an intersection needs at least two scopes — one scope's entities are not shared with "
            "anything, they are simply its entities"
        )
    if len(values) > MAX_SCOPE_IDS:
        raise ObservationQueryError(
            f"at most {MAX_SCOPE_IDS} scopes may be intersected at once (asked for {len(values)})"
        )

    resolved: list[str] = []
    for value in values:
        scope_id = resolve_scope_id(value)
        if scope_id in resolved:
            raise ObservationQueryError(
                f"scope {scope_id!r} was requested twice — a scope intersected with itself is not "
                f"a question"
            )
        resolved.append(scope_id)
    return resolved


def _occurrence_order(row: dict[str, Any]) -> tuple[str, str, str]:
    """Total, because it is the unique index: one entity observed in two artifacts is two rows."""
    return (row["scope_id"], row["source_artifact_digest"], row["derivation_version"])


def _entity_order(entity_id: str) -> tuple[str, str]:
    """Identity order, never occurrence count. By count would be "the most common technology" — the
    first ranking this system ever built, arriving by accident."""
    return (entity_id.split(":", 1)[0], entity_id)


# -- shaping -------------------------------------------------------------------------------


def _observation_record(row: dict[str, Any]) -> dict[str, Any]:
    """One stored observation, rendered. Keys are written in a fixed order, so the JSON a caller
    receives is byte-stable without the serializer having to sort anything."""
    observations = json.loads(row["artifact_json"]).get("observations", [])
    return {
        "id": row["normalization_digest"],
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "scope_path": row["scope_path"],
        # The real provenance: which sensors established the facts in this observation. NOT a
        # collector name derived from the scope — see the module docstring.
        "sensors": sorted({item["source"] for item in observations}),
        "observation_types": sorted({item["observation_type"] for item in observations}),
        "observation_count": len(observations),
        "artifact_digest": row["source_artifact_digest"],
        "observation_set_id": row["source_observation_set_id"],
        "observed_at": row["observed_at"],
        "stored_at": row["stored_at"],
        "derivation_version": row["derivation_version"],
        "schema_version": row["schema_version"],
        "lighthouse_version": row["lighthouse_version"],
        "profile_id": row["profile_id"],
        # "I could not determine the languages" must never be readable as "it contains no code".
        "undetermined": json.loads(row["undetermined_json"]),
    }


def _entity_record(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return NormalizedEntity.from_dict(_entity_payload(row)).to_dict()
    except (NormalizedRecordError, KeyError, TypeError, ValueError) as exc:
        raise TamperedObservationError(
            f"stored entity {row.get('entity_id')!r} does not satisfy its own contract: {exc}"
        ) from exc


def _relationship_record(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return NormalizedRelationship.from_dict(_relationship_payload(row)).to_dict()
    except (NormalizedRecordError, KeyError, TypeError, ValueError) as exc:
        raise TamperedObservationError(
            f"stored relationship {row.get('relationship_id')!r} does not satisfy its own "
            f"contract: {exc}"
        ) from exc


def _entity_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": row["schema_version"],
        # Not a column. The digest covers it, so a record written under a future serialization
        # version would fail the recompute rather than be quietly mistaken for one of ours.
        "serialization_version": DETERMINISTIC_SERIALIZATION_VERSION,
        "entity_id": row["entity_id"],
        "entity_type": row["entity_type"],
        "value": row["value"],
        "attributes": json.loads(row["attributes_json"]),
        "scope_id": row["scope_id"],
        "source_observation_set_id": row["source_observation_set_id"],
        "source_artifact_digest": row["source_artifact_digest"],
        "source_observations": json.loads(row["source_observations_json"]),
        "derivation_version": row["derivation_version"],
        "observed_at": row["observed_at"],
        "digest": row["digest"],
    }


def _relationship_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": row["schema_version"],
        "serialization_version": DETERMINISTIC_SERIALIZATION_VERSION,
        "relationship_id": row["relationship_id"],
        "relationship_type": row["relationship_type"],
        "subject_id": row["subject_id"],
        "object_id": row["object_id"],
        "value": row["value"],
        "scope_id": row["scope_id"],
        "source_observation_set_id": row["source_observation_set_id"],
        "source_artifact_digest": row["source_artifact_digest"],
        "source_observations": json.loads(row["source_observations_json"]),
        "derivation_version": row["derivation_version"],
        "observed_at": row["observed_at"],
        "digest": row["digest"],
    }


# -- the closed vocabularies, enforced ------------------------------------------------------


def _bounded_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ObservationQueryError(f"limit must be a positive integer: {limit!r}")
    return min(limit, MAX_LIMIT)


def _require_member(label: str, value: str | None, vocabulary: frozenset[str]) -> str | None:
    if value is None:
        return None
    if value not in vocabulary:
        raise ObservationQueryError(
            f"unknown {label}: {value!r} (known: {', '.join(sorted(vocabulary))})"
        )
    return value


def _require_hex(label: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ObservationQueryError(f"{label} must be a string")
    # Hexadecimal is case-insensitive, so accepting either case is not a normalization of meaning.
    # Nothing else about the input is adjusted, and nothing is guessed.
    candidate = value.strip().lower()
    if not _HEX.match(candidate) or not MIN_ID_PREFIX <= len(candidate) <= 64:
        raise ObservationQueryError(
            f"{label} must be {MIN_ID_PREFIX}-64 hexadecimal characters — the full digest, or an "
            f"unambiguous prefix of at least {MIN_ID_PREFIX} of it: {value!r}"
        )
    return candidate


def _require_digest(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    candidate = _require_hex(label, value)
    if len(candidate) != 64:
        raise ObservationQueryError(
            f"{label} must be a full 64-character digest — `observations show --id` is the surface "
            f"that resolves an abbreviated one: {value!r}"
        )
    return candidate


def _require_timestamp(label: str, value: str | None) -> str | None:
    """Parse a timestamp; never generate one.

    The store is UTC throughout, so a value with no offset is read as UTC — the local timezone is
    never consulted, and neither is the clock. Stored timestamps are UTC ISO-8601, which means a
    lexicographic comparison against them is a chronological one.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ObservationQueryError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ObservationQueryError(
            f"{label} must be an ISO-8601 timestamp (2026-07-01, or "
            f"2026-07-01T12:00:00+00:00): {value!r}"
        ) from exc
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed.isoformat()
