"""LHP-INTEL-01D-A — the observation substrate.

These tests pin the contract that `repository_intake` (01A) and `document_intake` (01C) now share:
one observation model, one confidence contract, one digest recipe, one validator, one error base.

The golden digests below were computed at commit 015ae76, *before* `modules/observations.py`
existed. They are what makes this refactor checkable rather than merely claimed: if the extraction
changed a single digest-covered byte, they go red.

LHP-INTEL-03A added two observation types to the *repository* vocabulary (`content.files`,
`content.coverage`). That legitimately changes what a full-vocabulary repository artifact digests
to — so the historical golden is now taken over the **frozen 01D-A vocabulary**, spelled out below
rather than read from the live one. It goes on proving exactly what it was written to prove, and it
is now immune to every future vocabulary change. A second golden, over the *current* vocabulary,
keeps the drift guard: a type added or renamed without anyone noticing still turns it red.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from modules import document_intake, observation_normalizer, observations, repository_intake
from modules.observations import (
    ALLOWED_CONFIDENCE,
    CONFIDENCE_OBSERVED,
    CONFIDENCE_UNDETERMINED,
    Observation,
    ObservationError,
    ObservationSpec,
    ObservationValidationError,
    SourceBoundaryError,
    canonical_digest,
    observation_from_dict,
    validate_observations,
)

# -- the golden baseline -------------------------------------------------------------------

# Each of these is a pure dict -> hash, which is why it is safe to pin: unlike a digest taken from
# a real PDF, no value here depends on the installed pypdf version.
GOLDEN_RECIPE_DIGEST = "8dc5b6a90ae92d310fedd33358f33357709831a407fb31ba6425d77ea8c55bb5"
GOLDEN_REPOSITORY_DIGEST = "3bd4c510e0fdad5821739352a341698479d717c1e913e02a531059f5d4a77b74"
GOLDEN_DOCUMENT_DIGEST = "4eddf48b38b71ec8cce890df2aee9d129731d7f16a1a2a2e8b6a7da365805d9d"

# 03A. The repository producer changed (two new types, derivation `/1` -> `/2`), so a
# full-vocabulary repository artifact digests to something new — deliberately, and only for those
# two reasons; the fixture above proves it by reproducing the old digest from the old inputs.
#
# `GOLDEN_DOCUMENT_DIGEST` above is untouched, and that is the compatibility proof this packet
# owes: extracting the shared raw-byte hasher out of `document_intake` did not move a document's
# identity by one byte. The digest was pinned before this packet existed and still holds.
GOLDEN_CURRENT_REPOSITORY_DIGEST = (
    "51863923331657b8cf3d49c7d2a2fe0b20f0f14a5b9036dcbe7c23dfc2bb7769"
)

RECIPE = {"b": [1, 2], "a": "x", "c": None, "d": {"nested": True}}

AT = "2026-07-13T00:00:00+00:00"
HEX = "a" * 64

# Values belonging to a closed set are pinned to a real member of it — a generic `["a"]` would not
# survive its producer's own validator, and an artifact the producer would reject is not a fixture
# worth digesting.
_PINNED_VALUES = {
    "git.head_state": "attached",
    "text.extraction_status": "extracted",
    "text.extractor": "builtin/1",
    "document.format": "markdown",
    # 03A. Both record shapes appear: the one that carries a digest, and one that carries none —
    # so the golden is sensitive to a change in either.
    "content.files": [
        {
            "path": "a.txt",
            "status": "hashed",
            "content_digest": "b" * 64,
            "digest_algorithm": "sha256",
            "size_bytes": 1,
        },
        {"path": "gone.txt", "status": "missing"},
    ],
    "content.coverage": {
        "tracked_files": 2,
        "accounted_files": 2,
        "hashed_files": 1,
        "hashed_bytes": 1,
        "complete": False,
    },
}
# The two branches of the confidence contract that are easy to collapse into each other, pinned
# into the golden digest so a collapse cannot pass unnoticed.
_DETERMINED_NULL = {"git.branch", "document.created_at"}  # 1.0 + None: "there provably is none"
_UNDETERMINED = {"code.languages", "text.pages_extracted"}  # 0.0 + None: "I could not establish it"


def _value_for(name: str, spec: ObservationSpec) -> object:
    if name in _PINNED_VALUES:
        return _PINNED_VALUES[name]
    return {str: "x", int: 1, bool: True, list: ["a"], dict: {"k": 1}}[spec.value_types[0]]


def _observations(vocabulary: dict[str, ObservationSpec]) -> tuple[Observation, ...]:
    """One observation per type in the vocabulary — so a golden digest is sensitive to drift in any
    type's name, its pinned source, or its nullability, not just in the handful a fixture happened
    to mention."""
    out = []
    for name, spec in vocabulary.items():
        if name in _UNDETERMINED:
            out.append(Observation(name, None, spec.source, AT, CONFIDENCE_UNDETERMINED))
        elif name in _DETERMINED_NULL:
            out.append(Observation(name, None, spec.source, AT, CONFIDENCE_OBSERVED))
        else:
            value = _value_for(name, spec)
            out.append(Observation(name, value, spec.source, AT, CONFIDENCE_OBSERVED))
    return tuple(out)


# The repository vocabulary exactly as it stood at 015ae76 — the artifact the golden digest was
# taken over. Frozen here on purpose: reading the live vocabulary would let a later packet's
# additions silently redefine what the historical proof is a proof *of*.
_HISTORICAL_REPOSITORY_TYPES = (
    "build.systems",
    "ci.configurations",
    "code.extension_counts",
    "code.languages",
    "containerization.files",
    "documentation.doc_directories",
    "documentation.readme_files",
    "git.branch",
    "git.commit_count",
    "git.first_commit_at",
    "git.head_commit",
    "git.head_state",
    "git.last_commit_at",
    "git.remotes",
    "git.root_commits",
    "git.submodules",
    "git.tag_count",
    "git.worktree_status",
    "license.files",
    "packaging.package_managers",
    "repository.is_git_repository",
    "repository.name",
    "repository.nested_repositories",
    "repository.top_level_entries",
    "repository.tracked_file_count",
    "repository.tracked_size_bytes",
    "tests.directories",
)


def _repository_observation(vocabulary: dict[str, ObservationSpec], derivation: str):
    artifact = repository_intake.RepositoryObservation(
        observation_set_id="pinned-set",
        repository_id=HEX,
        repository_path="/pinned/repository",
        observed_at=AT,
        observations=_observations(vocabulary),
        derivation_version=derivation,
    )
    artifact.artifact_digest = artifact.compute_digest()
    return artifact


@pytest.fixture
def repository_artifact() -> repository_intake.RepositoryObservation:
    """The 01D-A artifact: the 01D-A vocabulary, digested by the 01D-A producer.

    Both are pinned, and pinning them is the point. `derivation_version` is digest-covered on
    purpose, so 03A's bump to `repository_intake/2` moves the digest all by itself. Holding both
    fixed reproduces `GOLDEN_REPOSITORY_DIGEST` exactly — which says something stronger than
    "the golden was updated": it says the artifact digest moved **only** because of the two changes
    03A declared, and that nothing else in the recipe or the serialization shifted underneath them.
    """
    historical = {
        name: spec
        for name, spec in repository_intake.OBSERVATION_TYPES.items()
        if name in _HISTORICAL_REPOSITORY_TYPES
    }
    assert set(historical) == set(_HISTORICAL_REPOSITORY_TYPES), "a historical type was renamed"
    return _repository_observation(historical, "repository_intake/1")


@pytest.fixture
def current_repository_artifact() -> repository_intake.RepositoryObservation:
    """The artifact over whatever the vocabulary is *today* — the forward drift guard."""
    return _repository_observation(
        repository_intake.OBSERVATION_TYPES, repository_intake.DERIVATION_VERSION
    )


@pytest.fixture
def document_artifact() -> document_intake.DocumentObservation:
    artifact = document_intake.DocumentObservation(
        observation_set_id="pinned-set",
        document_id=HEX,
        document_path="/pinned/document.md",
        observed_at=AT,
        observations=_observations(document_intake.OBSERVATION_TYPES),
    )
    artifact.artifact_digest = artifact.compute_digest()
    return artifact


# -- digest compatibility: the refactor's central claim, made checkable ---------------------


def test_the_digest_recipe_is_unchanged():
    assert canonical_digest(RECIPE) == GOLDEN_RECIPE_DIGEST


def test_a_repository_artifact_digests_exactly_as_it_did_before_the_extraction(repository_artifact):
    assert repository_artifact.artifact_digest == GOLDEN_REPOSITORY_DIGEST
    repository_artifact.validate()  # and it still matches its own recomputed digest


def test_the_current_repository_vocabulary_digests_to_its_pinned_value(current_repository_artifact):
    """The forward guard. It goes red whenever the vocabulary or the producer changes — which is
    exactly when a human should be looking, because both are digest-covered."""
    assert current_repository_artifact.artifact_digest == GOLDEN_CURRENT_REPOSITORY_DIGEST
    current_repository_artifact.validate()


def test_the_two_repository_goldens_differ_only_by_declared_change():
    """03A changed two things and said so. Nothing else may have moved."""
    assert GOLDEN_REPOSITORY_DIGEST != GOLDEN_CURRENT_REPOSITORY_DIGEST
    added = set(repository_intake.OBSERVATION_TYPES) - set(_HISTORICAL_REPOSITORY_TYPES)
    assert added == {"content.files", "content.coverage"}
    assert repository_intake.DERIVATION_VERSION == "repository_intake/2"


def test_a_document_artifact_digests_exactly_as_it_did_before_the_extraction(document_artifact):
    assert document_artifact.artifact_digest == GOLDEN_DOCUMENT_DIGEST
    document_artifact.validate()


def test_a_serialized_artifact_is_unchanged(repository_artifact, document_artifact):
    """The digest deliberately excludes the ids and both timestamps, so it cannot on its own catch a
    field going missing from the serialized form. Pin the shape as well as the content."""
    common = {
        "schema_version",
        "serialization_version",
        "observation_set_id",
        "observed_at",
        "profile_id",
        "derivation_version",
        "lighthouse_version",
        "observations",
        "artifact_digest",
    }
    repository = repository_artifact.to_dict()
    document = document_artifact.to_dict()
    assert set(repository) == common | {"repository_id", "repository_path"}
    assert set(document) == common | {"document_id", "document_path"}

    # And serialize -> parse -> serialize is a fixed point: nothing is lost in the crossing.
    assert repository_intake.validate_observation(repository).to_dict() == repository
    assert document_intake.validate_document_observation(document).to_dict() == document


def test_there_is_exactly_one_digest_implementation():
    """Not "the same recipe" — the same function object. Two copies cannot drift if there is one."""
    assert repository_intake.canonical_digest is canonical_digest
    assert document_intake.canonical_digest is canonical_digest


def test_there_is_exactly_one_observation_validator():
    assert repository_intake.validate_observations is validate_observations
    assert document_intake.validate_observations is validate_observations


def test_both_collectors_speak_of_the_same_observation():
    """A repository fact and a document fact are the same kind of thing, and now provably so."""
    assert repository_intake.Observation is Observation
    assert document_intake.Observation is Observation


# -- the observation model -----------------------------------------------------------------


def test_an_observation_is_exactly_five_fields():
    assert [field.name for field in fields(Observation)] == [
        "observation_type",
        "value",
        "source",
        "observed_at",
        "confidence",
    ]


def test_an_observation_is_frozen():
    observation = Observation("repository.name", "x", "filesystem", AT, CONFIDENCE_OBSERVED)
    with pytest.raises(FrozenInstanceError):
        observation.value = "y"  # type: ignore[misc]


def test_a_spec_pins_the_source_of_its_type_and_cannot_be_edited():
    """`source` lives on the spec, not on the producer, so a collector cannot mislabel a fact's
    provenance."""
    spec = ObservationSpec("filesystem", (str,))
    assert spec.source == "filesystem"
    assert spec.nullable is False
    with pytest.raises(FrozenInstanceError):
        spec.source = "git_metadata"  # type: ignore[misc]


def test_an_observation_round_trips_through_its_serialized_form():
    original = Observation("repository.name", "lighthouse", "filesystem", AT, CONFIDENCE_OBSERVED)
    serialized = {
        "observation_type": original.observation_type,
        "value": original.value,
        "source": original.source,
        "observed_at": original.observed_at,
        "confidence": original.confidence,
    }
    assert observation_from_dict(serialized) == original


def test_a_serialized_observation_missing_a_key_is_rejected():
    with pytest.raises(ObservationValidationError, match="missing"):
        observation_from_dict(
            {
                "observation_type": "repository.name",
                "value": "x",
                "source": "filesystem",
                "observed_at": AT,
            }
        )


def test_a_serialized_observation_carrying_an_extra_key_is_rejected():
    with pytest.raises(ObservationValidationError, match="unexpected"):
        observation_from_dict(
            {
                "observation_type": "repository.name",
                "value": "x",
                "source": "filesystem",
                "observed_at": AT,
                "confidence": CONFIDENCE_OBSERVED,
                "importance": "high",
            }
        )


def test_a_serialized_observation_that_is_not_an_object_is_rejected():
    with pytest.raises(ObservationValidationError):
        observation_from_dict(["repository.name"])


# -- validation semantics, applied to ANY vocabulary ---------------------------------------

VOCABULARY = {
    "thing.name": ObservationSpec("filesystem", (str,)),
    "thing.count": ObservationSpec("filesystem", (int,)),
    "thing.maybe": ObservationSpec("filesystem", (str,), nullable=True),
}


def _observation(
    name: str = "thing.name",
    value: object = "x",
    source: str = "filesystem",
    at: str = AT,
    confidence: float = CONFIDENCE_OBSERVED,
) -> Observation:
    return Observation(name, value, source, at, confidence)


def test_the_vocabulary_is_closed():
    with pytest.raises(ObservationValidationError, match="unknown observation type"):
        validate_observations([_observation(name="thing.unknown")], VOCABULARY)


def test_a_type_may_not_be_observed_twice():
    with pytest.raises(ObservationValidationError, match="duplicate"):
        validate_observations([_observation(), _observation()], VOCABULARY)


def test_an_empty_observation_set_is_rejected():
    with pytest.raises(ObservationValidationError, match="observations"):
        validate_observations([], VOCABULARY)


def test_a_producer_cannot_mislabel_the_provenance_of_a_fact():
    with pytest.raises(ObservationValidationError, match="source"):
        validate_observations([_observation(source="git_metadata")], VOCABULARY)


def test_the_allowed_sources_come_from_the_vocabulary_being_validated():
    """A repository observation cannot claim a text-extractor provenance, even though that source
    is perfectly legal for a document. Deriving the allowed set per-vocabulary is strictly stronger
    than checking it against one global list."""
    with pytest.raises(ObservationValidationError, match="source"):
        validate_observations(
            [Observation("repository.name", "x", "text_extractor", AT, CONFIDENCE_OBSERVED)],
            repository_intake.OBSERVATION_TYPES,
        )


def test_an_undetermined_observation_carries_no_value():
    """Confidence 0.0 means "I could not establish this". It may never smuggle a value alongside."""
    with pytest.raises(ObservationValidationError, match="undetermined"):
        validate_observations(
            [_observation(value="x", confidence=CONFIDENCE_UNDETERMINED)], VOCABULARY
        )


def test_a_determined_observation_may_be_provably_absent():
    """The distinction this whole layer exists for: a nullable type at confidence 1.0 with value
    None is a FACT ("there is none of it"), not the absence of one."""
    validate_observations([_observation(name="thing.maybe", value=None)], VOCABULARY)


def test_a_non_nullable_type_may_not_be_null_even_when_determined():
    with pytest.raises(ObservationValidationError, match="value"):
        validate_observations([_observation(value=None)], VOCABULARY)


def test_confidence_has_exactly_two_poles():
    assert frozenset({CONFIDENCE_OBSERVED, CONFIDENCE_UNDETERMINED}) == ALLOWED_CONFIDENCE
    with pytest.raises(ObservationValidationError, match="confidence"):
        validate_observations([_observation(confidence=0.5)], VOCABULARY)


def test_confidence_is_a_float_and_a_boolean_will_not_do():
    with pytest.raises(ObservationValidationError, match="confidence"):
        validate_observations([_observation(confidence=True)], VOCABULARY)


def test_observed_at_must_be_timezone_aware():
    with pytest.raises(ObservationValidationError, match="timezone"):
        validate_observations([_observation(at="2026-07-13T00:00:00")], VOCABULARY)


def test_a_boolean_is_not_an_integer():
    """`isinstance(True, int)` is True in Python. Without the guard, count=True would validate."""
    with pytest.raises(ObservationValidationError, match="value"):
        validate_observations([_observation(name="thing.count", value=True)], VOCABULARY)


# -- the error hierarchy: one base, owned by the substrate ----------------------------------


def test_the_shared_errors_belong_to_the_substrate_not_to_a_collector():
    """Before this packet `ObservationValidationError` was a `RepositoryIntakeError`, so a
    malformed *document* reported itself as a repository failure. Ownership now sits with the
    contract."""
    for error in (ObservationValidationError, SourceBoundaryError):
        assert issubclass(error, ObservationError)
        assert not issubclass(error, repository_intake.RepositoryIntakeError)
        assert not issubclass(error, document_intake.DocumentIntakeError)


def test_both_collectors_fail_under_one_base():
    assert issubclass(repository_intake.RepositoryIntakeError, ObservationError)
    assert issubclass(document_intake.DocumentIntakeError, ObservationError)


def test_a_malformed_document_no_longer_reports_itself_as_a_repository_failure():
    with pytest.raises(ObservationError) as caught:
        document_intake.validate_document_observation({"schema_version": 1, "junk": True})
    assert not isinstance(caught.value, repository_intake.RepositoryIntakeError)


def test_both_normalize_paths_wrap_a_malformed_artifact_consistently():
    """The regression test for the bug the extraction exposed: a malformed repository was wrapped in
    a NormalizationError, but a malformed document leaked a raw validation error straight past the
    normalizer's wrapper — because that wrapper caught DocumentIntakeError and the error raised was,
    by inheritance, a *repository* one."""
    malformed = {"schema_version": 1, "junk": True}
    with pytest.raises(observation_normalizer.NormalizationError):
        observation_normalizer.normalize_repository_observation(malformed)
    with pytest.raises(observation_normalizer.NormalizedRecordError):
        observation_normalizer.normalize_document_observation(malformed)


# -- purity: the substrate is a contract, not a collector -----------------------------------

_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "re",
        "typing",
    }
)


def _imports_of(module) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module or "")
    return imported


def test_the_substrate_imports_no_collector_and_nothing_impure():
    """No database, no network, no filesystem, no subprocess, no LLM, no clock — and, above all, no
    collector: a substrate that imported one of its own consumers would be the bug this packet
    exists to remove, merely relocated."""
    imported = _imports_of(observations)
    assert imported <= _ALLOWED_IMPORTS, sorted(imported - _ALLOWED_IMPORTS)


def test_neither_collector_depends_on_the_other():
    """The dependency direction the packet establishes: both collectors import the substrate, and
    neither imports the other."""
    repository_imports = _imports_of(repository_intake)
    document_imports = _imports_of(document_intake)
    assert "modules.observations" in repository_imports
    assert "modules.observations" in document_imports
    assert "modules.document_intake" not in repository_imports
    assert "modules.repository_intake" not in document_imports


def test_the_substrate_carries_no_opinion():
    """There is no name here for score, rank, priority, importance, severity or quality — the
    observation layer states facts, and never why a fact matters."""
    forbidden = {"score", "rank", "priority", "importance", "severity", "quality", "relevance"}
    for name in dir(observations):
        assert not any(word in name.lower() for word in forbidden), name
