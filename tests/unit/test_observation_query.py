"""LHP-INTEL-02A — the read-only observation query surface.

Five packets built a fact pipeline and the facts accumulated where nobody could see them. This is
the layer that answers *"show me what Lighthouse knows"* — and it is defined just as much by what it
refuses to answer: *"tell me what Lighthouse thinks."*

The claims, each pinned rather than asserted in prose:

* **A read never writes.** Not a row, not a table, not the database file. `sqlite3.connect(path)`
  *creates* the file, so a naive read on a machine that has never observed anything would leave a
  database behind — destroying the very guarantee 01E established. Reads go through a `mode=ro`
  connection, so SQLite itself refuses the write; the protection is a mechanism, not a promise.
* **Reads validate, exactly as writes do.** Every row is round-tripped through its contract, which
  recomputes the digest *and* enforces the closed attribute vocabulary. So a forged `importance`
  attribute is rejected coming out of the store, whether or not its digest was re-stamped.
* **Nothing is ranked.** `stats` orders its counts by name. Ordering them by count descending would
  be "the most common technology" — the first ranking this system ever built — and it is not built
  here.
* **`confidence` survives, and is not significance.** It is epistemic (*did I determine this?*), not
  evaluative (*does it matter?*). The vocabulary guard allows it deliberately.
* **Output is deterministic**, and the clock is never read: `--since`/`--until` compare against
  stored timestamps, and no timestamp is generated during a read.
* **An ambiguous id fails closed**, naming every candidate — never the closest, never the first.
"""

import ast
import dataclasses
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from modules import db, observation_query
from modules.document_intake import observe_document
from modules.observation_normalizer import (
    ENTITY_TYPES,
    OBSERVATION_SCOPE_TYPES,
    RELATIONSHIP_TYPES,
    normalize_document_observation,
    normalize_repository_observation,
)
from modules.observation_query import (
    MIN_ID_PREFIX,
    SENSORS,
    AmbiguousObservationId,
    ObservationNotFound,
    ObservationQueryError,
    list_observations,
    observation_stats,
    resolve_observation_id,
    search_observations,
    show_observation,
)
from modules.repository_intake import observe_repository
from tests.unit.test_repository_intake import make_repo

# -- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Persistence points at a throwaway database — the real data/state.db is never opened."""
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture
def empty_store(store: Path) -> Path:
    """The tables exist, and there is nothing in them. Not the same as no store at all."""
    db.init_observation_tables()
    return store


@pytest.fixture
def observed(tmp_path: Path, store: Path) -> dict:
    """One repository and one document, observed for real and stored — what the rest lean on."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    repo = make_repo(
        workspace / "widget",
        {"README.md": "# widget\n", "Dockerfile": "FROM scratch\n", "main.py": "print(1)\n"},
    )
    doc = workspace / "spec.md"
    doc.write_text("# spec\n\nthe body of the document\n", encoding="utf-8")

    roots = (str(workspace),)
    repo_set = normalize_repository_observation(observe_repository(str(repo), allowed_roots=roots))
    doc_set = normalize_document_observation(observe_document(str(doc), allowed_roots=roots))

    db.init_observation_tables()
    assert db.store_normalized_observation(repo_set) is True
    assert db.store_normalized_observation(doc_set) is True

    return {
        "workspace": workspace,
        "repository": repo,
        "document": doc,
        "repository_set": repo_set,
        "document_set": doc_set,
        "repository_id": repo_set.normalization_digest,
        "document_id": doc_set.normalization_digest,
    }


def file_digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# -- the empty store: a fresh box answers honestly, and stays fresh -------------------------


def test_a_read_against_no_database_at_all_creates_no_database(store: Path):
    """The headline. `sqlite3.connect()` CREATES the file — so a careless read would leave a
    database behind on a machine that has never observed anything, which is exactly the guarantee
    01E's dry run established. Nothing here may write, and "nothing" includes the file itself."""
    assert not store.exists()

    assert list_observations() == {"count": 0, "observations": [], "scan_truncated": False}

    assert not store.exists(), "a read created the database"


def test_every_command_works_against_no_database_at_all(store: Path):
    assert list_observations()["count"] == 0
    assert search_observations(scope_type="repository")["count"] == 0
    assert observation_stats()["observation_sets"] == 0
    with pytest.raises(ObservationNotFound):
        show_observation("0" * 64)

    assert not store.exists()


def test_a_database_without_the_observation_tables_reads_as_empty(store: Path):
    """A DB carrying alerts but no observations is not an error. It is zero facts."""
    db.init_alerts_table()
    assert store.exists()

    assert list_observations()["count"] == 0
    assert observation_stats()["entities"] == 0


def test_empty_tables_read_as_empty(empty_store: Path):
    assert list_observations() == {"count": 0, "observations": [], "scan_truncated": False}
    assert search_observations()["count"] == 0
    stats = observation_stats()
    assert stats["observation_sets"] == 0
    assert stats["repositories"] == 0
    assert stats["documents"] == 0
    assert stats["entities"] == 0
    assert stats["relationships"] == 0


def test_an_empty_store_is_not_an_error_exit(empty_store: Path):
    """The packet is explicit: `{"count": 0}`, not a failure."""
    result = list_observations()
    assert result["count"] == 0
    assert result["observations"] == []


# -- list ----------------------------------------------------------------------------------


def test_list_returns_every_stored_observation(observed: dict):
    result = list_observations()
    assert result["count"] == 2
    ids = {record["id"] for record in result["observations"]}
    assert ids == {observed["repository_id"], observed["document_id"]}


def test_a_listed_record_carries_its_scope_and_its_sensors(observed: dict):
    result = list_observations()
    by_id = {record["id"]: record for record in result["observations"]}

    repository = by_id[observed["repository_id"]]
    assert repository["scope_type"] == "repository"
    assert repository["scope_path"] == str(observed["repository"])
    # The real provenance, read out of the stored artifact: a repository is observed through the
    # filesystem AND through git metadata. It is not "observed by repository_intake" — that would
    # confuse the subject of an observation with the sensor that established it.
    assert repository["sensors"] == ["filesystem", "git_metadata"]

    document = by_id[observed["document_id"]]
    assert document["scope_type"] == "document"
    assert document["sensors"] == ["filesystem", "text_extractor"]


def test_no_record_claims_a_collector_as_its_source(observed: dict):
    """The packet's original example said `"source": "repository_intake"`. There is no such fact
    in the store, and inventing it would fabricate provenance. Corrected: sensors, from the
    artifact."""
    for record in list_observations()["observations"]:
        assert "source" not in record
        assert "repository_intake" not in json.dumps(record)
        assert "document_intake" not in json.dumps(record)


def test_the_full_digest_is_always_the_id_that_comes_back(observed: dict):
    for record in list_observations()["observations"]:
        assert len(record["id"]) == 64
        assert record["id"] == record["id"].lower()


def test_list_is_deterministic_across_calls(observed: dict):
    first = json.dumps(list_observations())
    second = json.dumps(list_observations())
    assert first == second


def test_list_ordering_is_total(observed: dict):
    """`ORDER BY stored_at DESC` alone is not a total order — two rows stored in the same run can
    share a timestamp. The digest tiebreak is what makes the output reproducible."""
    ordered = [r["id"] for r in list_observations()["observations"]]
    for _ in range(5):
        assert [r["id"] for r in list_observations()["observations"]] == ordered


def test_list_respects_its_limit(observed: dict):
    assert list_observations(limit=1)["count"] == 1


def test_a_limit_is_bounded(observed: dict):
    assert list_observations(limit=10_000_000)["count"] == 2  # clamped, not refused

    for bad in (0, -1):
        with pytest.raises(ObservationQueryError):
            list_observations(limit=bad)


# -- search --------------------------------------------------------------------------------


def test_search_by_scope_type(observed: dict):
    repositories = search_observations(scope_type="repository")
    assert repositories["count"] == 1
    assert repositories["observations"][0]["id"] == observed["repository_id"]

    documents = search_observations(scope_type="document")
    assert documents["count"] == 1
    assert documents["observations"][0]["id"] == observed["document_id"]


def test_search_by_sensor(observed: dict):
    """`git_metadata` is only established by a repository; `text_extractor` only by a document."""
    git = search_observations(sensor="git_metadata")
    assert [r["id"] for r in git["observations"]] == [observed["repository_id"]]

    text = search_observations(sensor="text_extractor")
    assert [r["id"] for r in text["observations"]] == [observed["document_id"]]

    # Both are established through the filesystem.
    assert search_observations(sensor="filesystem")["count"] == 2


def test_search_by_observation_type(observed: dict):
    assert search_observations(observation_type="git.branch")["count"] == 1
    assert search_observations(observation_type="text.character_count")["count"] == 1


def test_search_by_digest(observed: dict):
    result = search_observations(digest=observed["document_id"])
    assert result["count"] == 1
    assert result["observations"][0]["id"] == observed["document_id"]


def test_search_by_stored_date_window(observed: dict):
    assert search_observations(since="2000-01-01T00:00:00+00:00")["count"] == 2
    assert search_observations(until="2000-01-01T00:00:00+00:00")["count"] == 0
    assert search_observations(since="2000-01-01", until="2999-01-01")["count"] == 2


def test_search_filters_compose(observed: dict):
    assert search_observations(scope_type="repository", sensor="filesystem")["count"] == 1
    # A repository is never observed through a text extractor: the conjunction is empty.
    assert search_observations(scope_type="repository", sensor="text_extractor")["count"] == 0


def test_an_unknown_filter_value_is_rejected_not_silently_empty(observed: dict):
    """A typo must be an error. Returning zero rows would read as 'Lighthouse knows nothing about
    that', which is a different and much worse claim than 'that is not a word I know'."""
    with pytest.raises(ObservationQueryError, match="sensor"):
        search_observations(sensor="repository_intake")
    with pytest.raises(ObservationQueryError, match="scope_type"):
        search_observations(scope_type="repositories")
    with pytest.raises(ObservationQueryError, match="observation_type"):
        search_observations(observation_type="git.importance")
    with pytest.raises(ObservationQueryError, match="digest"):
        search_observations(digest="not-a-digest")
    with pytest.raises(ObservationQueryError, match="since"):
        search_observations(since="yesterday")


def test_a_filter_is_applied_before_the_limit_not_after(
    observed: dict, monkeypatch: pytest.MonkeyPatch
):
    """The subtle one. `sensor` lives inside the stored artifact, which has no index, so it is
    applied in Python — and it MUST be applied to the whole matching set, not to a page that SQL
    already truncated. Filtering a truncated page silently returns the wrong answer."""
    # The repository sorts after the document (or before — either way, a limit of 1 in SQL would
    # keep only one of them, and the sensor filter would then find the wrong one half the time).
    result = search_observations(sensor="git_metadata", limit=1)
    assert result["count"] == 1
    assert result["observations"][0]["id"] == observed["repository_id"]

    result = search_observations(sensor="text_extractor", limit=1)
    assert result["count"] == 1
    assert result["observations"][0]["id"] == observed["document_id"]


def test_a_bounded_scan_says_so_rather_than_lying(observed: dict, monkeypatch: pytest.MonkeyPatch):
    """The artifact-side filters scan, and the scan is bounded like every other read here. When the
    bound bites, the answer says it was truncated instead of quietly being incomplete."""
    monkeypatch.setattr(observation_query, "_SCAN_LIMIT", 1)
    result = search_observations(sensor="filesystem")
    assert result["scan_truncated"] is True

    assert list_observations()["scan_truncated"] is False


# -- show ----------------------------------------------------------------------------------


def test_show_by_full_digest(observed: dict):
    record = show_observation(observed["repository_id"])
    assert record["id"] == observed["repository_id"]
    assert record["scope_type"] == "repository"


def test_show_by_unambiguous_prefix(observed: dict):
    """Git's abbreviated object ids. Not a second identifier system — the canonical id is the full
    SHA-256, and it is what comes back out."""
    prefix = observed["repository_id"][:MIN_ID_PREFIX]
    record = show_observation(prefix)
    assert record["id"] == observed["repository_id"]
    assert len(record["id"]) == 64


def test_a_prefix_shorter_than_the_minimum_is_refused(observed: dict):
    short = observed["repository_id"][: MIN_ID_PREFIX - 1]
    with pytest.raises(ObservationQueryError, match=str(MIN_ID_PREFIX)):
        show_observation(short)


def test_an_ambiguous_prefix_fails_closed_and_names_every_candidate(observed: dict):
    """Never the closest match, never the first — every candidate, and no result."""
    repo_id = observed["repository_id"]
    doc_id = observed["document_id"]

    # Force a collision: rewrite the document's digest to share the repository's first 8 characters.
    collision = repo_id[:MIN_ID_PREFIX] + doc_id[MIN_ID_PREFIX:]
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE observation_sets SET normalization_digest = ? WHERE normalization_digest = ?",
            (collision, doc_id),
        )

    with pytest.raises(AmbiguousObservationId) as caught:
        show_observation(repo_id[:MIN_ID_PREFIX])

    assert set(caught.value.candidates) == {repo_id, collision}
    assert repo_id in str(caught.value)
    assert collision in str(caught.value)


def test_an_unknown_id_is_not_found(observed: dict):
    with pytest.raises(ObservationNotFound):
        show_observation("0" * 64)
    with pytest.raises(ObservationNotFound):
        show_observation("0" * MIN_ID_PREFIX)


def test_a_non_hex_id_is_refused(observed: dict):
    for bad in ("zzzzzzzz", "not a digest", "", "0x" + "0" * 62):
        with pytest.raises(ObservationQueryError):
            show_observation(bad)


def test_an_id_is_case_insensitive_because_hex_is(observed: dict):
    record = show_observation(observed["repository_id"].upper())
    assert record["id"] == observed["repository_id"]


def test_show_preserves_provenance(observed: dict):
    """The packet's requirement, in full: source, digest, confidence, observed timestamp, and the
    derivation that produced the record."""
    record = show_observation(observed["repository_id"])

    assert record["artifact_digest"] == observed["repository_set"].source_artifact_digest
    assert record["derivation_version"] == "observation_normalizer/2"
    assert record["observed_at"]
    assert record["stored_at"]

    observations = record["observations"]
    assert observations, "the artifact's observations are the provenance"
    for observation in observations:
        assert observation["source"] in SENSORS  # the sensor, verbatim from the artifact
        assert observation["confidence"] in (0.0, 1.0)
        assert observation["observed_at"]
        assert observation["observation_type"]


def test_show_returns_the_entities_and_relationships_of_that_set(observed: dict):
    record = show_observation(observed["repository_id"])
    normalized = observed["repository_set"]

    assert len(record["entities"]) == len(normalized.entities)
    assert len(record["relationships"]) == len(normalized.relationships)
    assert record["entity_count"] == len(normalized.entities)
    assert record["relationship_count"] == len(normalized.relationships)

    types = {entity["entity_type"] for entity in record["entities"]}
    assert "repository" in types
    assert types <= ENTITY_TYPES


def test_show_returns_only_that_sets_entities(observed: dict):
    """Scoped by the artifact that produced them, not by the scope: one repository observed twice is
    two artifacts, and `show` must expand exactly the one that was asked for."""
    document = show_observation(observed["document_id"])
    # 03A: the document subject, plus the neutral `content:` identity of its bytes — the id a
    # repository file holding those same bytes would also get. Two entities, one of each.
    assert {entity["entity_type"] for entity in document["entities"]} == {"document", "content"}
    # Still no relationships. The two ids carry the SAME digest, so an edge between them would be
    # derivable from either endpoint alone — decoration, not evidence.
    assert document["relationships"] == []


def test_the_subject_entity_carries_the_observed_attributes(observed: dict):
    record = show_observation(observed["repository_id"])
    subject = next(e for e in record["entities"] if e["entity_type"] == "repository")
    assert subject["attributes"], "the subject of an observation carries what was observed"
    assert "repository.name" in subject["attributes"]


def test_undetermined_observations_are_reported_not_hidden(observed: dict):
    """ "I could not determine the languages" must never read as "it contains no code"."""
    record = show_observation(observed["repository_id"])
    assert isinstance(record["undetermined"], list)


# -- stats ---------------------------------------------------------------------------------


def test_stats_counts_what_is_stored(observed: dict):
    stats = observation_stats()
    assert stats["observation_sets"] == 2
    assert stats["repositories"] == 1
    assert stats["documents"] == 1
    assert stats["entities"] == len(observed["repository_set"].entities) + len(
        observed["document_set"].entities
    )
    assert stats["relationships"] == len(observed["repository_set"].relationships) + len(
        observed["document_set"].relationships
    )


def test_stats_reports_every_entity_type_even_the_absent_ones(observed: dict):
    stats = observation_stats()
    assert set(stats["entity_types"]) == ENTITY_TYPES
    assert set(stats["relationship_types"]) == RELATIONSHIP_TYPES
    assert stats["entity_types"]["repository"] == 1
    assert stats["entity_types"]["document"] == 1


def test_stats_is_ordered_by_name_and_never_by_count(observed: dict):
    """The quiet one that matters. A dict of counts ordered by count descending is a ranking — "the
    most common technology" — and it would be the first ranking this system ever built. Names."""
    stats = observation_stats()
    assert list(stats["entity_types"]) == sorted(ENTITY_TYPES)
    assert list(stats["relationship_types"]) == sorted(RELATIONSHIP_TYPES)

    counts = list(stats["entity_types"].values())
    assert counts != sorted(counts, reverse=True) or len(set(counts)) <= 1, (
        "the entity-type counts came out in descending order — if that is not a coincidence, "
        "this layer has started ranking"
    )


def test_stats_distinguishes_facts_from_observations_of_facts(observed: dict):
    stats = observation_stats()
    assert stats["distinct_entities"] <= stats["entities"]


def test_the_scope_vocabulary_is_still_exactly_two(observed: dict):
    """`stats` names `repositories` and `documents` explicitly. If a third scope type is ever added,
    this test fails and forces stats to be updated rather than silently under-reporting."""
    assert {"repository", "document"} == OBSERVATION_SCOPE_TYPES


# -- security: a read is a read ------------------------------------------------------------


def test_the_query_connection_refuses_to_write(observed: dict):
    """The protection is a mechanism, not a promise: SQLite itself rejects the write. Swap `mode=ro`
    for an ordinary connection and this test goes red."""
    conn = db.get_readonly_connection()
    try:
        for statement in (
            "DELETE FROM observation_sets",
            "UPDATE normalized_entities SET value = 'tampered'",
            "INSERT INTO normalized_relationships (relationship_id) VALUES ('x')",
            "CREATE TABLE evil (id INTEGER)",
            "DROP TABLE observation_sets",
        ):
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
                conn.execute(statement)
    finally:
        conn.close()


def test_the_database_is_byte_identical_after_every_command(observed: dict, store: Path):
    before = file_digest(store)

    list_observations()
    search_observations(scope_type="repository", sensor="filesystem")
    show_observation(observed["repository_id"])
    observation_stats()
    resolve_observation_id(observed["document_id"][:MIN_ID_PREFIX])

    assert file_digest(store) == before, "a read changed the store"


def test_an_injected_score_is_rejected_on_the_way_out(observed: dict):
    """Writes validate. So must reads — otherwise a tampered store serves opinions as facts. Here
    the digest no longer matches the content, and that alone is fatal."""
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_entities SET attributes_json = ? WHERE entity_type = 'repository'",
            (json.dumps({"score": 0.9}),),
        )

    with pytest.raises(ObservationQueryError, match="digest|attribute|tamper"):
        show_observation(observed["repository_id"])


def test_an_injected_ranking_is_rejected_even_with_a_restamped_digest(observed: dict):
    """The forger does the arithmetic too: content edited, digest recomputed, consistent. The closed
    attribute vocabulary is the gate that remains — there is no word for `importance` in it."""
    subject = next(
        entity
        for entity in observed["repository_set"].entities
        if entity.entity_type == "repository"
    )
    forged = dataclasses.replace(subject, attributes={"importance": 0.99})
    forged = dataclasses.replace(forged, digest=forged.compute_digest())
    assert forged.digest == forged.compute_digest(), "the forgery is internally consistent"

    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_entities SET attributes_json = ?, digest = ? WHERE entity_id = ?",
            (json.dumps(forged.attributes), forged.digest, forged.entity_id),
        )

    with pytest.raises(ObservationQueryError, match="importance|attribute"):
        show_observation(observed["repository_id"])


# -- the vocabulary: this layer has no word for significance --------------------------------

_FORBIDDEN = (
    "score",
    "score_label",
    "importance",
    "important",
    "priority",
    "severity",
    "rank",
    "ranking",
    "relevance",
    "recommendation",
    "recommended",
    "significance",
    "significant",
    "interesting",
    "notable",
    "quality",
    "weight",
)


def _keys(payload: object) -> set[str]:
    if isinstance(payload, dict):
        found = set(payload)
        for value in payload.values():
            found |= _keys(value)
        return found
    if isinstance(payload, list):
        found: set[str] = set()
        for item in payload:
            found |= _keys(item)
        return found
    return set()


def test_no_command_can_emit_a_word_for_significance(observed: dict):
    payloads = [
        list_observations(),
        search_observations(scope_type="repository"),
        show_observation(observed["repository_id"]),
        observation_stats(),
    ]
    for payload in payloads:
        emitted = {key.lower() for key in _keys(payload)}
        assert not emitted & set(_FORBIDDEN), emitted & set(_FORBIDDEN)


def test_confidence_is_preserved_and_is_not_significance(observed: dict):
    """Deliberately absent from the forbidden list. Confidence is epistemic — *did I determine
    this?* — not evaluative — *does it matter?*. Dropping it to satisfy a keyword ban would delete
    the distinction 01C exists for: "no text" (0 at 1.0) vs "could not read it" (None at 0.0)."""
    record = show_observation(observed["document_id"])
    confidences = {observation["confidence"] for observation in record["observations"]}
    assert confidences <= {0.0, 1.0}
    assert 1.0 in confidences


def test_the_module_has_no_name_for_an_opinion():
    source = Path(observation_query.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {
        node.arg for node in ast.walk(tree) if isinstance(node, ast.arg) and node.arg is not None
    }
    for forbidden in _FORBIDDEN:
        assert not any(forbidden in name.lower() for name in names), forbidden


# -- purity: what this module may do, and what it may not -----------------------------------

_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "datetime",
        "json",
        "re",
        "typing",
        "modules.db",
        "modules.observation_normalizer",
        # 02B reads supporting observations out of the stored artifact, so it must know the artifact
        # is intact first. It re-validates through the CONTRACT OF THE PRODUCER THAT WROTE IT —
        # which recomputes the artifact's own digest — rather than growing a second copy of the
        # digest recipe. A duplicated fail-closed check is a fail-open waiting to happen (01D-A).
        # These are pure validators; the call and clock guards below still hold this module to no
        # I/O, no subprocess and no execution of its own.
        "modules.document_intake",
        "modules.observations",
        "modules.repository_intake",
    }
)

# The only `db.` names this module may touch. Every one of them is a SELECT. `init_*`, `insert_*`,
# `store_*`, `upsert_*` and the read-write `execute`/`fetchall` helpers are, by their absence,
# unreachable — so the read boundary cannot grow a write by accident.
_ALLOWED_DB_ATTRIBUTES = frozenset(
    {
        "observation_tables_exist",
        "fetch_observation_set",
        "fetch_observation_sets",
        "fetch_observation_set_digests_by_prefix",
        "fetch_normalized_entities",
        "fetch_normalized_relationships",
        "count_observation_sets_by_scope_type",
        "count_normalized_entities",
        "count_distinct_normalized_entities",
        "count_normalized_entities_by_type",
        "count_normalized_relationships",
        "count_normalized_relationships_by_type",
        # LHP-INTEL-02B — all three are SELECTs, like every other name in this set.
        "fetch_scope_ids_by_prefix",
        "fetch_scope_entity_ids",
        "fetch_relationships_for_entities",
        # LHP-INTEL-03B — the two reads a rule needs before it can ask anything: which identities
        # exist, and whether the artifact that recorded one accounted for the whole repository.
        # SELECTs, like the rest. The candidate tables are the SERVICE's business, and no name from
        # them appears here — this boundary reads facts and nothing else.
        "fetch_distinct_entity_ids_by_type",
        "fetch_observation_set_by_artifact",
    }
)

_CLOCK = frozenset({"now", "utcnow", "today", "time", "monotonic", "perf_counter"})


def _module_tree() -> ast.Module:
    return ast.parse(Path(observation_query.__file__).read_text(encoding="utf-8"))


def test_the_module_imports_nothing_it_should_not():
    imported: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "modules":
                # `from modules import db` — expand, or the allowlist would wave through
                # `from modules import anything`.
                imported |= {f"modules.{alias.name}" for alias in node.names}
            else:
                imported.add(node.module)

    assert imported <= _ALLOWED_IMPORTS, imported - _ALLOWED_IMPORTS


def test_the_module_touches_no_writing_function_in_db():
    used = {
        node.attr
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "db"
    }
    assert used <= _ALLOWED_DB_ATTRIBUTES, used - _ALLOWED_DB_ATTRIBUTES


def test_the_module_never_asks_what_time_it_is():
    """It may *parse* a timestamp; it may never *generate* one. A read that stamps its output with
    the current time is not deterministic, and the packet's rule is explicit: stored timestamps
    only."""
    called = {
        node.func.attr
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & _CLOCK, called & _CLOCK


def test_the_module_runs_nothing_and_reaches_nowhere():
    source = Path(observation_query.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for forbidden in (
        "os",
        "sys",
        "subprocess",
        "socket",
        "sqlite3",
        "pathlib",
        "httpx",
        "anthropic",
        "importlib",
        "pickle",
    ):
        assert forbidden not in imported, forbidden


def test_the_sensor_vocabulary_is_read_from_the_collectors_not_redeclared():
    """Three sensors, and they are the collectors' own declarations. A fourth copy of "what a
    sensor is called" is how two vocabularies quietly drift — 01D-A exists to stop exactly that."""
    assert {"filesystem", "git_metadata", "text_extractor"} == SENSORS


def test_no_observation_type_maps_to_two_different_sensors():
    """The 27-type and 14-type vocabularies are merged. If they ever shared a key with a different
    sensor, the merge would silently pick one — so pin that they do not."""
    from modules.observation_normalizer import (
        DOCUMENT_OBSERVATION_TYPES,
        REPOSITORY_OBSERVATION_TYPES,
    )

    shared = set(REPOSITORY_OBSERVATION_TYPES) & set(DOCUMENT_OBSERVATION_TYPES)
    for name in shared:
        assert REPOSITORY_OBSERVATION_TYPES[name].source == DOCUMENT_OBSERVATION_TYPES[name].source


# -- the two reads a rule needs (LHP-INTEL-03B) ---------------------------------------------
#
# Both are profile-neutral FACT reads, added to the read boundary rather than forked out of it: the
# CLI does not reach into `db.py`, and neither does a detector. Neither of these ranks, scores or
# interprets — one lists identities, the other reports a coverage fact verbatim.


def test_entity_ids_by_type_lists_identities_in_identity_order(observed):
    from modules.observation_query import entity_ids_by_type

    result = entity_ids_by_type("content")
    assert result["entity_type"] == "content"
    assert result["count"] == len(result["entity_ids"]) > 0
    assert result["entity_ids"] == sorted(result["entity_ids"])
    assert all(entity_id.startswith("content:") for entity_id in result["entity_ids"])
    assert result["scan_truncated"] is False


def test_entity_ids_by_type_deduplicates_across_scopes(observed):
    """One identity observed in two scopes is two ROWS and one identity. A rule that enumerated rows
    would ask about the same content twice and build the same candidate twice."""
    from modules.observation_query import entity_ids_by_type

    ids = entity_ids_by_type("content")["entity_ids"]
    assert len(ids) == len(set(ids))


def test_entity_ids_by_type_rejects_an_unknown_type(observed):
    # An unknown filter value is an error, never an empty result — "that is not a word I know" and
    # "Lighthouse knows nothing about that" are different claims (the 02A rule).
    from modules.observation_query import ObservationQueryError, entity_ids_by_type

    with pytest.raises(ObservationQueryError):
        entity_ids_by_type("invention")


def test_entity_ids_by_type_on_an_absent_store_is_empty_and_creates_nothing(store):
    from modules.observation_query import entity_ids_by_type

    assert entity_ids_by_type("content") == {
        "entity_type": "content",
        "count": 0,
        "entity_ids": [],
        "scan_truncated": False,
    }
    assert not store.exists()


def test_artifact_coverage_reports_the_stored_coverage_fact(observed):
    from modules.observation_query import artifact_coverage, entity_ids_by_type, entity_occurrences

    content_id = entity_ids_by_type("content")["entity_ids"][0]
    occurrence = next(
        o for o in entity_occurrences(content_id)["occurrences"] if o["scope_type"] == "repository"
    )
    coverage = artifact_coverage(
        scope_id=occurrence["scope_id"],
        source_artifact_digest=occurrence["source_artifact_digest"],
    )
    assert coverage["complete"] is True
    assert coverage["tracked_files"] == coverage["hashed_files"] == 3


def test_artifact_coverage_is_none_where_the_artifact_holds_no_coverage_fact(observed):
    """A document carries no content inventory, and neither does any artifact written before 03A.

    `None` is a THIRD state. A caller that reads it as `complete` is claiming evidence that was
    never gathered — which is precisely how an absence becomes a fact nobody checked.
    """
    from modules.observation_query import artifact_coverage, entity_ids_by_type, entity_occurrences

    content_ids = entity_ids_by_type("content")["entity_ids"]
    document_occurrence = next(
        o
        for content_id in content_ids
        for o in entity_occurrences(content_id)["occurrences"]
        if o["scope_type"] == "document"
    )
    assert (
        artifact_coverage(
            scope_id=document_occurrence["scope_id"],
            source_artifact_digest=document_occurrence["source_artifact_digest"],
        )
        is None
    )


def test_artifact_coverage_of_an_unknown_artifact_is_none(observed):
    from modules.observation_query import artifact_coverage

    assert artifact_coverage(scope_id="f" * 64, source_artifact_digest="e" * 64) is None
