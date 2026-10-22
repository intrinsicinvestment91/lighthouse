"""LHP-INTEL-01B — observation normalization.

Normalization answers exactly one question: *how can the system consistently represent what
exists?* It never answers *why does it matter?* Four properties carry this packet, and each is
pinned here as a structural impossibility rather than a convention:

* **Nothing observed is lost.** The repository entity's attributes are exactly the determined
  observations — proved by one set equality, not by inspection.
* **An opinion is unrepresentable.** Attribute keys must belong to the closed 27-name observation
  vocabulary, which contains no word for score, priority, importance, severity or quality. There
  is nowhere to put an opinion, and no table column to hold one.
* **Undetermined never reads as absent.** A bounded-out enumeration yields no entities and *is*
  reported — otherwise "I could not enumerate the languages" would look exactly like "this
  repository contains no code".
* **The same observation always normalizes the same way**, and storing it twice stores it once.

Fixtures build real git repositories locally. No network. Every database test points `db.DB_PATH`
at a temp file, so the developer's real `data/state.db` is never opened (and a clean CI checkout,
which has no `data/` at all, behaves identically).
"""

import ast
import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from modules import db, observation_normalizer, repository_intake
from modules.document_intake import observe_document
from modules.observation_normalizer import (
    DERIVATION_VERSION,
    ENTITY_TYPES,
    NORMALIZED_OBSERVATION_SCHEMA_VERSION,
    RELATIONSHIP_TYPES,
    NormalizationError,
    NormalizedEntity,
    NormalizedRecordError,
    NormalizedRelationship,
    normalize_document_observation,
    normalize_repository_observation,
    normalized_record_digest,
    validate_normalized_entity,
    validate_normalized_relationship,
)
from modules.repository_intake import (
    CONFIDENCE_OBSERVED,
    CONFIDENCE_UNDETERMINED,
    OBSERVATION_TYPES,
    observe_repository,
)
from tests.unit.test_repository_intake import make_repo, tree_digest

# -- fixtures ------------------------------------------------------------------------------

_PINNED_AT = "2026-07-13T00:00:00+00:00"


@pytest.fixture
def observed(tmp_path: Path):
    """A representative repository: python + docker + CI + docs + tests + license + a remote."""
    repo = make_repo(
        tmp_path / "widget",
        {
            "README.md": "# widget\n",
            "LICENSE": "MIT\n",
            "pyproject.toml": "[project]\nname='widget'\n",
            "requirements.txt": "httpx\n",
            "Dockerfile": "FROM python:3.11-slim\n",
            "docker-compose.yml": "services: {}\n",
            "src/widget.py": "x = 1\n",
            "docs/guide.md": "# guide\n",
            "tests/test_widget.py": "def test(): pass\n",
            ".github/workflows/ci.yml": "on: push\n",
        },
    )
    return observe_repository(repo, observed_at=_PINNED_AT)


@pytest.fixture
def normalized(observed):
    return normalize_repository_observation(observed)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point persistence at a throwaway database. The real data/state.db is never touched."""
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_observation_tables()
    return path


def entities_of(result, entity_type: str) -> list[NormalizedEntity]:
    return [entity for entity in result.entities if entity.entity_type == entity_type]


def values_of(result, entity_type: str) -> set[str]:
    return {entity.value for entity in entities_of(result, entity_type)}


def relations_of(result, relationship_type: str) -> list[NormalizedRelationship]:
    return [rel for rel in result.relationships if rel.relationship_type == relationship_type]


# -- the model -----------------------------------------------------------------------------


def test_a_repository_becomes_exactly_one_repository_entity(observed, normalized):
    repositories = entities_of(normalized, "repository")
    own = [e for e in repositories if e.entity_id == f"repository:{observed.repository_id}"]
    assert len(own) == 1
    assert own[0].value == "widget"


def test_the_vocabulary_is_closed_and_pinned():
    assert NORMALIZED_OBSERVATION_SCHEMA_VERSION == 1
    assert DERIVATION_VERSION == "observation_normalizer/2"
    assert {
        "commit",
        "content",  # 03A — exact raw bytes; global, so a repository file and a document meet on it
        "directory",
        "document",
        "file",
        "language",
        "remote",
        "repository",
        "technology",
    } == ENTITY_TYPES
    assert {
        "contains_directory",
        "contains_file",
        "contains_repository",
        "file_has_content",  # 03A — this scoped path had these exact bytes. Nothing more.
        "has_head_commit",
        "has_remote",
        "has_root_commit",
        "has_submodule",
        "uses_technology",
        "written_in",
    } == RELATIONSHIP_TYPES


def test_every_entity_and_relationship_uses_the_closed_vocabulary(normalized):
    assert {e.entity_type for e in normalized.entities} <= ENTITY_TYPES
    assert {r.relationship_type for r in normalized.relationships} <= RELATIONSHIP_TYPES


def test_markers_normalize_into_technologies(normalized):
    technologies = values_of(normalized, "technology")
    # pyproject.toml -> build system; requirements.txt -> package manager; Dockerfile -> docker;
    # docker-compose.yml -> docker_compose; .github/workflows/ -> github_actions.
    assert {"python", "pip", "docker", "docker_compose", "github_actions"} <= technologies


def test_languages_normalize_into_shared_global_entities(tmp_path: Path):
    """Two repositories that both contain Python share one `language:Python` identity. Giving
    facts a stable, joinable identity is the entire reason this layer exists."""
    first = normalize_repository_observation(
        observe_repository(make_repo(tmp_path / "a", {"a.py": "x = 1\n"}), observed_at=_PINNED_AT)
    )
    second = normalize_repository_observation(
        observe_repository(make_repo(tmp_path / "b", {"b.py": "y = 2\n"}), observed_at=_PINNED_AT)
    )
    assert "language:Python" in {e.entity_id for e in first.entities}
    assert "language:Python" in {e.entity_id for e in second.entities}
    assert first.scope_id != second.scope_id


def test_files_and_directories_are_scoped_to_their_repository(observed, normalized):
    readme = next(e for e in entities_of(normalized, "file") if e.value == "README.md")
    assert readme.entity_id == f"file:{observed.repository_id}:README.md"
    docs = next(e for e in entities_of(normalized, "directory") if e.value == "docs")
    assert docs.entity_id == f"directory:{observed.repository_id}:docs"


def test_one_file_seen_through_two_observations_becomes_one_entity_with_both_provenances(
    normalized,
):
    """README.md is reported by both `repository.top_level_entries` and
    `documentation.readme_files`. Merging those into one entity that remembers both sources
    *is* normalization."""
    readmes = [e for e in entities_of(normalized, "file") if e.value == "README.md"]
    assert len(readmes) == 1
    assert set(readmes[0].source_observations) == {
        "content.files",  # 03A: it is a tracked file too, and now identified by its bytes
        "documentation.readme_files",
        "repository.top_level_entries",
    }


def test_a_nested_repository_gets_the_identity_it_would_have_if_observed_directly(tmp_path: Path):
    parent = make_repo(tmp_path / "parent", {"a.py": "x = 1\n"})
    child = make_repo(parent / "vendor" / "child", {"b.py": "y = 2\n"})
    child_observed = observe_repository(child, observed_at=_PINNED_AT)

    result = normalize_repository_observation(observe_repository(parent, observed_at=_PINNED_AT))

    nested = [e for e in entities_of(result, "repository") if e.value == "child"]
    assert len(nested) == 1
    assert nested[0].entity_id == f"repository:{child_observed.repository_id}"
    assert relations_of(result, "contains_repository")


def test_a_relationship_points_from_the_repository_at_the_thing_observed(observed, normalized):
    uses = relations_of(normalized, "uses_technology")
    subject = f"repository:{observed.repository_id}"
    assert {rel.subject_id for rel in uses} == {subject}
    assert "technology:docker" in {rel.object_id for rel in uses}


def test_a_repository_with_no_remote_produces_no_remote_entity(tmp_path: Path):
    result = normalize_repository_observation(
        observe_repository(make_repo(tmp_path / "solo", {"a.py": "x\n"}), observed_at=_PINNED_AT)
    )
    assert entities_of(result, "remote") == []
    assert relations_of(result, "has_remote") == []


def test_remotes_normalize_into_entities_keyed_by_url(tmp_path: Path):
    repo = make_repo(tmp_path / "remoted", {"a.py": "x\n"})
    _git_config(repo, "remote.origin.url", "https://example.invalid/widget.git")
    result = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    assert values_of(result, "remote") == {"https://example.invalid/widget.git"}
    assert relations_of(result, "has_remote")


def test_commits_normalize_into_entities(observed, normalized):
    head = _by_type(observed)["git.head_commit"]
    assert f"commit:{head}" in {e.entity_id for e in normalized.entities}
    assert relations_of(normalized, "has_head_commit")
    assert relations_of(normalized, "has_root_commit")


def test_an_unborn_repository_yields_no_commit_entities(tmp_path: Path):
    repo = make_repo(tmp_path / "unborn", {"a.py": "x\n"}, commit=False)
    result = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    assert entities_of(result, "commit") == []
    assert relations_of(result, "has_head_commit") == []


def test_a_symlink_entry_is_not_claimed_to_be_a_file(tmp_path: Path):
    repo = make_repo(tmp_path / "linky", {"real.py": "x\n"})
    (repo / "link.py").symlink_to(repo / "real.py")
    result = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    assert "link.py" not in values_of(result, "file")
    assert "real.py" in values_of(result, "file")


# -- provenance: nothing is lost, nothing exists without it ---------------------------------


def test_no_determined_observation_is_lost_in_normalization(observed, normalized):
    """The one assertion behind 'never lose the original observation reference'."""
    repository = next(
        e for e in normalized.entities if e.entity_id == f"repository:{observed.repository_id}"
    )
    determined = {
        o.observation_type for o in observed.observations if o.confidence == CONFIDENCE_OBSERVED
    }
    assert set(repository.attributes) == determined
    for observation in observed.observations:
        if observation.confidence == CONFIDENCE_OBSERVED:
            assert repository.attributes[observation.observation_type] == observation.value


def test_the_source_artifact_is_carried_verbatim(observed, normalized):
    assert normalized.source_artifact == observed.to_dict()
    assert normalized.source_artifact_digest == observed.artifact_digest
    assert normalized.source_observation_set_id == observed.observation_set_id


def test_every_record_carries_the_seven_mandatory_fields(normalized):
    for entity in normalized.entities:
        assert entity.entity_id  # identity
        assert entity.entity_type  # type
        assert entity.value  # value
        assert entity.source_observations  # source_observation
        assert entity.source_artifact_digest
        assert entity.derivation_version == DERIVATION_VERSION  # derivation_version
        assert entity.observed_at  # timestamp
        assert entity.digest  # digest
    for rel in normalized.relationships:
        assert rel.relationship_id and rel.relationship_type and rel.value
        assert rel.source_observations and rel.source_artifact_digest
        assert rel.derivation_version == DERIVATION_VERSION
        assert rel.observed_at and rel.digest


def test_an_entity_without_provenance_cannot_exist(normalized):
    entity = normalized.entities[0]
    orphan = dict(entity.to_dict(), source_observations=[])
    with pytest.raises(NormalizedRecordError, match="provenance|source_observations"):
        validate_normalized_entity(orphan)


def test_a_relationship_without_provenance_cannot_exist(normalized):
    rel = normalized.relationships[0]
    orphan = dict(rel.to_dict(), source_observations=[])
    with pytest.raises(NormalizedRecordError, match="provenance|source_observations"):
        validate_normalized_relationship(orphan)


def test_the_observed_timestamp_is_inherited_from_the_observation(normalized):
    assert normalized.observed_at == _PINNED_AT
    assert all(e.observed_at == _PINNED_AT for e in normalized.entities)
    assert all(r.observed_at == _PINNED_AT for r in normalized.relationships)


# -- undetermined is not absent -------------------------------------------------------------


def test_an_undetermined_observation_produces_no_entities_and_is_reported(observed):
    """A bounded-out enumeration must never be silently indistinguishable from an empty one."""
    blinded = _with_observation(observed, "code.languages", None, CONFIDENCE_UNDETERMINED)
    result = normalize_repository_observation(blinded)

    assert entities_of(result, "language") == []
    assert relations_of(result, "written_in") == []
    assert "code.languages" in result.undetermined_observations
    # ... and it is NOT smuggled into attributes as though it had been determined.
    repository = next(
        e for e in result.entities if e.entity_id == f"repository:{observed.repository_id}"
    )
    assert "code.languages" not in repository.attributes


def test_a_determined_empty_enumeration_is_reported_as_determined(tmp_path: Path):
    """'This repository has no license file' is a fact, and must not be confused with 'unknown'."""
    result = normalize_repository_observation(
        observe_repository(make_repo(tmp_path / "bare", {"a.py": "x\n"}), observed_at=_PINNED_AT)
    )
    repository = entities_of(result, "repository")[0]
    assert repository.attributes["license.files"] == []
    assert "license.files" not in result.undetermined_observations


# -- no opinions: structurally impossible ---------------------------------------------------


def test_an_attribute_key_outside_the_observation_vocabulary_is_rejected(normalized):
    """The closed 27-name vocabulary has no word for importance, so there is nowhere to put one.

    The forged record is **re-stamped with a correct digest**, so the digest check cannot be what
    rejects it. The vocabulary is the only gate left standing — which is the claim being made.
    """
    repository = next(e for e in normalized.entities if e.entity_type == "repository")
    for opinion in ("importance", "score", "priority", "quality", "severity", "rank"):
        assert opinion not in OBSERVATION_TYPES
        forged = dataclasses.replace(
            repository, attributes=dict(repository.attributes, **{opinion: 0.9})
        )
        forged = dataclasses.replace(forged, digest=forged.compute_digest())
        with pytest.raises(NormalizedRecordError, match="observation type"):
            forged.validate()


def test_only_a_repository_entity_may_carry_attributes(normalized):
    """Also re-stamped: the attribute rule must reject this on its own merits."""
    language = next(e for e in normalized.entities if e.entity_type == "language")
    assert language.attributes == {}
    forged = dataclasses.replace(language, attributes={"git.commit_count": 3})
    forged = dataclasses.replace(forged, digest=forged.compute_digest())
    with pytest.raises(NormalizedRecordError, match="attributes"):
        forged.validate()


def test_the_module_exposes_no_interpretation_surface():
    forbidden = (
        "score",
        "rank",
        "quality",
        "priority",
        "severity",
        "importance",
        "recommend",
        "classify",
        "weight",
        "alert",
    )
    surface = [name for name in dir(observation_normalizer) if not name.startswith("__")]
    for name in surface:
        assert not any(word in name.lower() for word in forbidden), name


def test_the_storage_schema_cannot_hold_an_opinion(store):
    """The database itself has no column an opinion could be written into."""
    forbidden = ("score", "weight", "priority", "importance", "severity", "rank", "label")
    with sqlite3.connect(store) as conn:
        for table in ("observation_sets", "normalized_entities", "normalized_relationships"):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            assert columns, f"{table} was not created"
            for column in columns:
                assert not any(word in column.lower() for word in forbidden), f"{table}.{column}"


# -- determinism -----------------------------------------------------------------------------


def test_the_same_observation_produces_the_same_normalized_output(observed):
    first = normalize_repository_observation(observed)
    second = normalize_repository_observation(observed)
    assert first.normalization_digest == second.normalization_digest
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )


def test_re_observing_an_unchanged_repository_normalizes_identically(tmp_path: Path):
    """Different clock, same repository: the normalization digest is a content identity."""
    repo = make_repo(tmp_path / "stable", {"a.py": "x\n"})
    first = normalize_repository_observation(
        observe_repository(repo, observed_at="2026-01-01T00:00:00+00:00")
    )
    second = normalize_repository_observation(
        observe_repository(repo, observed_at="2027-09-09T09:09:09+00:00")
    )
    assert first.normalization_digest == second.normalization_digest
    assert {e.digest for e in first.entities} == {e.digest for e in second.entities}


def test_a_changed_repository_normalizes_differently(tmp_path: Path):
    repo = make_repo(tmp_path / "changing", {"a.py": "x\n"})
    before = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    after = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    assert before.normalization_digest != after.normalization_digest
    assert "technology:docker" not in {e.entity_id for e in before.entities}
    assert "technology:docker" in {e.entity_id for e in after.entities}


def test_records_are_emitted_in_a_stable_order(normalized):
    assert [e.entity_id for e in normalized.entities] == sorted(
        e.entity_id for e in normalized.entities
    )
    assert [r.relationship_id for r in normalized.relationships] == sorted(
        r.relationship_id for r in normalized.relationships
    )


def test_the_digest_recipe_matches_the_observation_layer(normalized):
    """One canonical serialization across the whole platform, pinned against drift."""
    sample = {"b": [3, 1], "a": {"z": 1, "y": 2}}
    assert normalized_record_digest(sample) == repository_intake.repository_observation_digest(
        sample
    )


def test_a_record_digest_is_a_content_identity_not_a_timestamp(normalized):
    entity = normalized.entities[0]
    restamped = dict(entity.to_dict(), observed_at="2030-01-01T00:00:00+00:00")
    assert validate_normalized_entity(restamped).digest == entity.digest


# -- fail closed -----------------------------------------------------------------------------


def test_an_unvalidated_artifact_is_rejected():
    with pytest.raises(NormalizationError):
        normalize_repository_observation({"schema_version": 1})


def test_an_artifact_whose_digest_does_not_match_its_content_is_rejected(observed):
    tampered = observed.to_dict()
    tampered["scope_path"] = "/tmp/somewhere-else"
    with pytest.raises(NormalizationError):
        normalize_repository_observation(tampered)


def test_an_unknown_observation_type_fails_closed(observed):
    tampered = observed.to_dict()
    tampered["observations"].append(
        {
            "observation_type": "repository.vibes",
            "value": "great",
            "source": "filesystem",
            "observed_at": _PINNED_AT,
            "confidence": 1.0,
        }
    )
    with pytest.raises(NormalizationError):
        normalize_repository_observation(tampered)


def test_an_unknown_entity_type_is_rejected(normalized):
    tampered = dict(normalized.entities[0].to_dict(), entity_type="invention")
    with pytest.raises(NormalizedRecordError, match="entity_type"):
        validate_normalized_entity(tampered)


def test_an_unknown_relationship_type_is_rejected(normalized):
    tampered = dict(normalized.relationships[0].to_dict(), relationship_type="is_better_than")
    with pytest.raises(NormalizedRecordError, match="relationship_type"):
        validate_normalized_relationship(tampered)


def test_an_unknown_scope_type_is_rejected(normalized):
    """The scope vocabulary is closed. A producer cannot invent a kind of thing to have observed."""
    forged = dataclasses.replace(normalized, scope_type="invention")
    forged = dataclasses.replace(forged, normalization_digest=forged.compute_digest())
    with pytest.raises(NormalizedRecordError, match="scope_type"):
        forged.validate()


def test_the_scope_type_is_digest_covered(normalized):
    """What kind of thing was observed is part of the content identity, not a label bolted on
    afterwards — so it cannot be rewritten without breaking the digest."""
    relabelled = dataclasses.replace(normalized, scope_type="document")
    assert relabelled.compute_digest() != normalized.normalization_digest


def test_a_tampered_record_digest_is_rejected(normalized):
    tampered = dict(normalized.entities[0].to_dict(), digest="0" * 64)
    with pytest.raises(NormalizedRecordError, match="digest"):
        validate_normalized_entity(tampered)


def test_a_record_edited_and_then_restamped_is_still_rejected(normalized):
    """A forger who also recomputes the digest produces a *self-consistent* lie. Identity is the
    second lock: `entity_id` must still derive from the type and value it claims."""
    entity = next(e for e in normalized.entities if e.entity_type == "technology")
    forged = dataclasses.replace(entity, value="something_else")
    forged = dataclasses.replace(forged, digest=forged.compute_digest())
    with pytest.raises(NormalizedRecordError, match="entity_id"):
        forged.validate()


def test_an_entity_id_that_does_not_match_its_type_and_value_is_rejected(normalized):
    entity = next(e for e in normalized.entities if e.entity_type == "language")
    tampered = dict(entity.to_dict(), entity_id="language:Fortran")
    with pytest.raises(NormalizedRecordError, match="entity_id"):
        validate_normalized_entity(tampered)


def test_a_relationship_id_that_does_not_match_its_triple_is_rejected(normalized):
    rel = normalized.relationships[0]
    tampered = dict(rel.to_dict(), relationship_id="rel:" + "a" * 64)
    with pytest.raises(NormalizedRecordError, match="relationship_id"):
        validate_normalized_relationship(tampered)


def test_a_future_schema_version_is_rejected(normalized):
    tampered = dict(normalized.entities[0].to_dict(), schema_version=2)
    with pytest.raises(NormalizedRecordError, match="schema_version"):
        validate_normalized_entity(tampered)


def test_a_boolean_schema_version_is_rejected(normalized):
    tampered = dict(normalized.entities[0].to_dict(), schema_version=True)
    with pytest.raises(NormalizedRecordError, match="schema_version"):
        validate_normalized_entity(tampered)


def test_a_missing_key_is_rejected(normalized):
    tampered = normalized.entities[0].to_dict()
    del tampered["scope_id"]
    with pytest.raises(NormalizedRecordError, match="missing"):
        validate_normalized_entity(tampered)


def test_an_unexpected_key_is_rejected(normalized):
    tampered = dict(normalized.entities[0].to_dict(), invention_score=0.99)
    with pytest.raises(NormalizedRecordError, match="unexpected"):
        validate_normalized_entity(tampered)


def test_a_record_round_trips_through_serialization(normalized):
    for entity in normalized.entities:
        assert validate_normalized_entity(entity.to_dict()) == entity
    for rel in normalized.relationships:
        assert validate_normalized_relationship(rel.to_dict()) == rel


# -- purity ----------------------------------------------------------------------------------


def test_the_normalizer_imports_nothing_impure():
    """Nothing that touches a database, a network, a subprocess, or an LLM. `datetime` is here to
    *parse* a timestamp, never to read one — which the next test proves separately."""
    source = Path(observation_normalizer.__file__).read_text(encoding="utf-8")
    allowed = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "pathlib",
        "typing",
        "modules.document_intake",
        "modules.observations",
        "modules.repository_intake",
    }
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "") in allowed, node.module


def test_the_normalizer_never_reads_the_clock():
    """Normalization is a pure function of its input. A clock read would make the same observation
    normalize differently on Tuesday, and the digests would stop being content identities."""
    source = Path(observation_normalizer.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"now", "utcnow", "today", "time", "monotonic"}, (
                node.func.attr
            )


def test_the_normalizer_performs_no_io_at_all():
    """No file is opened, nothing is executed, nothing is imported dynamically. Checked against
    the syntax tree, so a docstring naming a hazard cannot false-positive."""
    source = Path(observation_normalizer.__file__).read_text(encoding="utf-8")
    forbidden_calls = {"open", "exec", "eval", "compile", "__import__", "input"}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, node.func.id


def test_normalizing_a_repository_never_modifies_it(tmp_path: Path, store):
    """The observation layer promised not to touch the source. Normalizing and storing it
    must not quietly break that promise."""
    repo = make_repo(tmp_path / "untouched", {"a.py": "x\n", "Dockerfile": "FROM scratch\n"})
    before = tree_digest(repo)

    result = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    db.store_normalized_observation(result)

    assert tree_digest(repo) == before


# -- persistence: facts only, insert-only, idempotent ----------------------------------------


def test_a_normalized_set_is_stored_with_its_entities_and_relationships(normalized, store):
    written = db.store_normalized_observation(normalized)
    assert written is True

    sets = db.fetch_observation_sets()
    assert len(sets) == 1
    assert sets[0]["normalization_digest"] == normalized.normalization_digest
    assert sets[0]["scope_id"] == normalized.scope_id

    stored_entities = db.fetch_normalized_entities(limit=500)
    assert len(stored_entities) == len(normalized.entities)
    stored_relationships = db.fetch_normalized_relationships(limit=500)
    assert len(stored_relationships) == len(normalized.relationships)


def test_the_original_observation_survives_in_the_store(normalized, store, observed):
    db.store_normalized_observation(normalized)
    stored = db.fetch_observation_sets()[0]
    assert json.loads(stored["artifact_json"]) == observed.to_dict()


def test_storing_the_same_observation_twice_stores_it_once(normalized, store):
    assert db.store_normalized_observation(normalized) is True
    assert db.store_normalized_observation(normalized) is False

    assert len(db.fetch_observation_sets()) == 1
    assert len(db.fetch_normalized_entities(limit=500)) == len(normalized.entities)
    assert len(db.fetch_normalized_relationships(limit=500)) == len(normalized.relationships)


def test_re_observing_an_unchanged_repository_writes_nothing_new(tmp_path: Path, store):
    """01A's timestamp-free digest exists precisely so this is true."""
    repo = make_repo(tmp_path / "idem", {"a.py": "x\n"})
    first = normalize_repository_observation(
        observe_repository(repo, observed_at="2026-01-01T00:00:00+00:00")
    )
    second = normalize_repository_observation(
        observe_repository(repo, observed_at="2027-02-02T02:02:02+00:00")
    )
    assert db.store_normalized_observation(first) is True
    assert db.store_normalized_observation(second) is False
    assert len(db.fetch_observation_sets()) == 1


def test_a_changed_repository_is_stored_as_a_new_set(tmp_path: Path, store):
    repo = make_repo(tmp_path / "moving", {"a.py": "x\n"})
    before = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    db.store_normalized_observation(before)

    # Languages are computed over git's *tracked* set, so the new file must actually be committed.
    (repo / "b.rs").write_text("fn main() {}\n", encoding="utf-8")
    _git_commit_all(repo)
    after = normalize_repository_observation(observe_repository(repo, observed_at=_PINNED_AT))
    assert db.store_normalized_observation(after) is True

    assert len(db.fetch_observation_sets()) == 2
    assert "Rust" in {e["value"] for e in db.fetch_normalized_entities(entity_type="language")}


def test_stored_entities_can_be_filtered_by_type_and_repository(normalized, store):
    db.store_normalized_observation(normalized)
    technologies = db.fetch_normalized_entities(entity_type="technology", limit=100)
    assert technologies
    assert {row["entity_type"] for row in technologies} == {"technology"}

    scoped = db.fetch_normalized_entities(scope_id=normalized.scope_id, limit=500)
    assert len(scoped) == len(normalized.entities)
    assert db.fetch_normalized_entities(scope_id="0" * 64) == []


def test_a_malformed_record_cannot_reach_the_database(store, normalized):
    """The persistence boundary validates. A record built outside the normalizer, whose content
    no longer matches its digest, is rejected before a row is written."""
    tampered = dataclasses.replace(normalized.entities[0], value="tampered")
    with pytest.raises(NormalizedRecordError):
        db.insert_normalized_entity(tampered)
    assert db.fetch_normalized_entities() == []


def test_the_store_is_insert_only_and_never_overwrites(normalized, store):
    db.store_normalized_observation(normalized)
    with sqlite3.connect(store) as conn:
        conn.execute("UPDATE normalized_entities SET value = 'tampered' WHERE id = 1")
    # A second store of the same content must not "repair" or overwrite the tampered row:
    # the set is already known, so nothing is written at all.
    assert db.store_normalized_observation(normalized) is False
    with sqlite3.connect(store) as conn:
        row = conn.execute("SELECT value FROM normalized_entities WHERE id = 1").fetchone()
    assert row[0] == "tampered"


# -- helpers -----------------------------------------------------------------------------------


def _by_type(artifact) -> dict[str, object]:
    return {o.observation_type: o.value for o in artifact.observations}


def _git_config(repo: Path, key: str, value: str) -> None:
    from tests.unit.test_repository_intake import _git

    _git(repo, "config", key, value)


def _git_commit_all(repo: Path) -> None:
    from tests.unit.test_repository_intake import _git

    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "change")


def _with_observation(artifact, observation_type: str, value, confidence: float):
    """Rebuild an artifact with one observation replaced, re-digested so it stays valid."""
    observations = [
        repository_intake.Observation(
            observation_type=o.observation_type,
            value=value if o.observation_type == observation_type else o.value,
            source=o.source,
            observed_at=o.observed_at,
            confidence=(confidence if o.observation_type == observation_type else o.confidence),
        )
        for o in artifact.observations
    ]
    rebuilt = repository_intake.RepositoryObservation(
        observation_set_id=artifact.observation_set_id,
        repository_id=artifact.repository_id,
        repository_path=artifact.repository_path,
        observed_at=artifact.observed_at,
        observations=tuple(observations),
        profile_id=artifact.profile_id,
        derivation_version=artifact.derivation_version,
        lighthouse_version=artifact.lighthouse_version,
    )
    rebuilt.artifact_digest = rebuilt.compute_digest()
    rebuilt.validate()
    return rebuilt


# -- documents reuse this boundary (LHP-INTEL-01C) ---------------------------------------------


@pytest.fixture
def document(tmp_path: Path) -> Path:
    path = tmp_path / "paper.md"
    path.write_text("# Title\n\nsome body text\n", encoding="utf-8")
    return path


@pytest.fixture
def document_set(document: Path):
    return normalize_document_observation(observe_document(document, observed_at=_PINNED_AT))


def document_entity(result):
    """The document subject. Since 03A a document set also carries a neutral `content:` entity —
    the same bytes under the id a repository file would get — and `content:` sorts before
    `document:`, so `entities[0]` is no longer the subject."""
    subjects = [e for e in result.entities if e.entity_type == "document"]
    assert len(subjects) == 1
    return subjects[0]


def content_entity(result):
    contents = [e for e in result.entities if e.entity_type == "content"]
    assert len(contents) == 1
    return contents[0]


def test_a_document_normalizes_into_one_subject_and_one_neutral_content_identity(
    document_set, document
):
    """Both, not either. `document:` is a subject — it has a name and carries every determined
    fact. `content:` is the bytes — no name, no attributes — and is the id a repository file with
    those same bytes would get. Replacing the subject with the content entity would lose the
    document; omitting the content entity would leave nothing for a repository file to join to."""
    import hashlib

    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    assert len(document_set.entities) == 2

    subject = document_entity(document_set)
    assert subject.entity_id == f"document:{digest}"
    assert subject.value == "paper.md"

    content = content_entity(document_set)
    assert content.entity_id == f"content:{digest}"
    assert content.attributes == {}  # the bytes have no attributes, and no name of their own


def test_a_document_is_identified_by_its_bytes_not_by_its_extracted_text(tmp_path: Path):
    """A PDF is the sharp case: its bytes and its text are nothing alike.

    (This test exists because the markdown fixture above CANNOT catch this — a .md file's extracted
    text is a byte-identical UTF-8 decode of the file, so the two digests coincide and the
    assertion is vacuous. Keying identity on the text instead of the bytes passed that test
    silently; a mutation run found it.)
    """
    import hashlib

    from tests.unit.test_document_intake import make_pdf

    pdf = make_pdf(tmp_path / "p.pdf", ["Hello Lighthouse"])
    artifact = observe_document(pdf, observed_at=_PINNED_AT)
    result = normalize_document_observation(artifact)
    text_digest = next(
        o.value for o in artifact.observations if o.observation_type == "text.digest"
    )

    subject = document_entity(result)
    assert subject.entity_id == f"document:{hashlib.sha256(pdf.read_bytes()).hexdigest()}"
    assert subject.entity_id != f"document:{text_digest}"
    # And the neutral identity is the BYTES too — never the text. If it were keyed on the text, a
    # repository file and this document would join on what they *say* rather than what they *are*.
    assert content_entity(result).entity_id == subject.entity_id.replace("document:", "content:")


def test_two_documents_with_the_same_text_but_different_bytes_stay_different(tmp_path: Path):
    """A PDF and a text file that say the same words are two documents, not one. Were identity the
    digest of the extracted TEXT, they would silently merge into a single entity."""
    from tests.unit.test_document_intake import make_pdf

    pdf = make_pdf(tmp_path / "hello.pdf", ["Hello Lighthouse"])
    txt = tmp_path / "hello.txt"
    txt.write_text("Hello Lighthouse", encoding="utf-8")

    from_pdf = normalize_document_observation(observe_document(pdf, observed_at=_PINNED_AT))
    from_txt = normalize_document_observation(observe_document(txt, observed_at=_PINNED_AT))
    assert document_entity(from_pdf).entity_id != document_entity(from_txt).entity_id
    assert content_entity(from_pdf).entity_id != content_entity(from_txt).entity_id


def test_a_truncation_bound_cannot_make_two_documents_collide(tmp_path: Path):
    """Two long documents sharing a prefix truncate to the same TEXT. They must remain two
    documents — which they do, because a bound never touches the bytes we identify them by."""
    first, second = tmp_path / "one.txt", tmp_path / "two.txt"
    first.write_text("shared prefix ... AAAA", encoding="utf-8")
    second.write_text("shared prefix ... BBBB", encoding="utf-8")

    one = normalize_document_observation(
        observe_document(first, observed_at=_PINNED_AT, max_text_characters=8)
    )
    two = normalize_document_observation(
        observe_document(second, observed_at=_PINNED_AT, max_text_characters=8)
    )
    assert (
        document_entity(one).attributes["text.digest"]
        == document_entity(two).attributes["text.digest"]
    )
    assert document_entity(one).entity_id != document_entity(two).entity_id
    assert content_entity(one).entity_id != content_entity(two).entity_id


def test_a_document_emits_no_relationships(document_set):
    """A lone document has none. Inventing one to make the model look richer is interpretation."""
    assert document_set.relationships == ()


def test_the_same_document_in_two_places_is_one_entity_with_two_provenances(tmp_path: Path):
    """The join documents exist for: identity is the bytes, so a copy does not become a stranger."""
    here, there = tmp_path / "a" / "p.md", tmp_path / "b" / "p.md"
    for path in (here, there):
        path.parent.mkdir(parents=True)
        path.write_text("# same\n", encoding="utf-8")

    one = normalize_document_observation(observe_document(here, observed_at=_PINNED_AT))
    two = normalize_document_observation(observe_document(there, observed_at=_PINNED_AT))

    assert document_entity(one).entity_id == document_entity(two).entity_id  # one entity
    assert content_entity(one).entity_id == content_entity(two).entity_id  # one content identity
    assert one.scope_id != two.scope_id  # two places
    assert one.scope_type == two.scope_type == "document"


def test_nothing_observed_about_a_document_is_lost(document_set):
    """The entity's attributes are EXACTLY the determined observations — the same structural
    guarantee the repository entity carries, now for a second source."""
    carried = document_set.source_artifact["observations"]
    determined = {o["observation_type"] for o in carried if o["confidence"] == 1.0}
    assert set(document_entity(document_set).attributes) == determined


def test_an_undetermined_document_fact_is_reported_not_dropped(tmp_path: Path):
    """A PDF with no extractor available: the text facts are undetermined and SAID SO, never
    silently rendered as "this document is blank"."""
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\nnot really a pdf\n%%EOF\n")
    result = normalize_document_observation(observe_document(pdf, observed_at=_PINNED_AT))
    assert "text.character_count" in result.undetermined_observations
    assert "text.character_count" not in document_entity(result).attributes


def test_a_document_entity_cannot_carry_an_opinion_either(document_set):
    """The widened vocabulary is still closed. Forged AND re-stamped, so the digest cannot be what
    rejects it — the only gate left is that no observation type is named `importance`."""
    entity = document_entity(document_set)
    forged = dataclasses.replace(entity, attributes={**entity.attributes, "importance": 0.9})
    forged = dataclasses.replace(forged, digest=forged.compute_digest())
    with pytest.raises(NormalizedRecordError, match="not an observation type"):
        forged.validate()


def test_a_document_set_carrying_a_repository_artifact_is_rejected(document_set, normalized):
    """`scope_type` decides which producer's validator owns the carried artifact, so a set cannot
    mislabel what it observed and be checked by the wrong rules."""
    forged = dataclasses.replace(
        document_set,
        source_artifact=normalized.source_artifact,
        source_artifact_digest=normalized.source_artifact_digest,
    )
    forged = dataclasses.replace(forged, normalization_digest=forged.compute_digest())
    with pytest.raises(NormalizedRecordError):
        forged.validate()


def test_a_document_is_stored_in_the_same_tables_as_a_repository(store, document_set, normalized):
    """No document database, no second provenance system: one fact boundary, two sources."""
    assert db.store_normalized_observation(normalized) is True
    assert db.store_normalized_observation(document_set) is True

    sets = db.fetch_observation_sets(limit=10)
    assert {row["scope_type"] for row in sets} == {"repository", "document"}
    assert len(db.fetch_normalized_entities(entity_type="document", limit=10)) == 1


def test_re_observing_an_unchanged_document_writes_nothing_new(store, document, document_set):
    assert db.store_normalized_observation(document_set) is True
    again = normalize_document_observation(
        observe_document(document, observed_at="2030-01-01T00:00:00+00:00")
    )
    assert again.normalization_digest == document_set.normalization_digest
    assert db.store_normalized_observation(again) is False  # idempotent by content identity
    assert len(db.fetch_observation_sets(limit=10)) == 1


def test_normalizing_a_document_never_modifies_it(tmp_path: Path, store):
    # `store` writes its database into tmp_path, so digest the document's OWN directory — otherwise
    # this would hash the database we just created rather than the document we promised not to
    # touch.
    library = tmp_path / "library"
    library.mkdir()
    (library / "a.md").write_text("# hi\n", encoding="utf-8")
    before = tree_digest(library)
    result = normalize_document_observation(
        observe_document(library / "a.md", observed_at=_PINNED_AT)
    )
    db.store_normalized_observation(result)
    assert tree_digest(library) == before
