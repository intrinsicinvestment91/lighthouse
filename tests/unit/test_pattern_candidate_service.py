"""The candidate service: read facts, run the rule, persist, review (LHP-INTEL-03B).

This is the boundary — the only place that touches both the read-only fact store and the
candidate tables. Every test below runs against **real** observations of **real** temporary git
repositories, because the two guarantees that matter most cannot be mocked into existence:

* a dry run writes **nothing at all** — not a row, not a table, not the database file; and
* a candidate is **immutable**, so a second detection over changed facts adds a *version* rather
  than editing what an operator already reviewed.

The fixture is small and deliberate:

    alpha   Dockerfile ("FROM scratch")  +  main.py ("print(1)")
    beta    Dockerfile — THE SAME BYTES  +  app.py  ("print(2)")
    gamma   solo.py — shares nothing
    note.txt   a loose document whose bytes are IDENTICAL to alpha's main.py

So exactly one candidate is expected (the Dockerfile), and the document — which recurs with a
repository file and is a perfectly real fact — must NOT produce one, because this rule is owned by a
repository-scoped profile.
"""

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from modules import db
from modules import pattern_candidate_service as service
from modules.observation_normalizer import (
    normalize_document_observation,
    normalize_repository_observation,
)
from modules.pattern_candidate import PatternCandidate
from modules.pattern_candidate_service import (
    AmbiguousCandidateId,
    CandidateNotFound,
    DispositionExists,
    PatternServiceError,
    TamperedCandidateError,
    list_candidates,
    record_disposition,
    resolve_candidate_id,
    run_detection,
    show_candidate,
)
from modules.repository_intake import observe_repository
from tests.unit.test_repository_intake import make_repo

DOCKERFILE = "FROM scratch\n"
MAIN_PY = "print(1)\n"

SHARED_CONTENT = "content:" + hashlib.sha256(DOCKERFILE.encode()).hexdigest()
DOCUMENT_CONTENT = "content:" + hashlib.sha256(MAIN_PY.encode()).hexdigest()


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def observe(workspace: Path, *paths: Path) -> None:
    roots = (str(workspace),)
    db.init_observation_tables()
    for path in paths:
        if path.is_dir():
            result = normalize_repository_observation(
                observe_repository(str(path), allowed_roots=roots)
            )
        else:
            from modules.document_intake import observe_document

            result = normalize_document_observation(
                observe_document(str(path), allowed_roots=roots)
            )
        db.store_normalized_observation(result)


@pytest.fixture
def facts(workspace: Path, store: Path) -> dict[str, Path]:
    from tests.unit.active_profile_test_helpers import plant_active_rcd

    plant_active_rcd()
    alpha = make_repo(workspace / "alpha", {"Dockerfile": DOCKERFILE, "main.py": MAIN_PY})
    beta = make_repo(workspace / "beta", {"Dockerfile": DOCKERFILE, "app.py": "print(2)\n"})
    gamma = make_repo(workspace / "gamma", {"solo.py": "print(3)\n"})

    # The same bytes as alpha's main.py, as a standalone document. One repository + one document is
    # NOT two repositories — and the fact must survive anyway.
    note = workspace / "note.txt"
    note.write_text(MAIN_PY, encoding="utf-8")

    observe(workspace, alpha, beta, gamma, note)
    return {"alpha": alpha, "beta": beta, "gamma": gamma, "note": note, "workspace": workspace}


def tables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        return {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()


def rows(path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.execute(f"SELECT * FROM {table} ORDER BY 1"))
    finally:
        conn.close()


# -- detection -----------------------------------------------------------------------------


def test_detection_finds_the_one_exact_recurrence(facts):
    run = run_detection()
    assert len(run.candidates) == 1

    candidate = run.candidates[0]
    assert candidate.subject["entity_id"] == SHARED_CONTENT
    assert candidate.distinct_repository_scope_count == 2
    assert candidate.profile_id == "repository_capability_discovery"
    assert candidate.detector_id == "repository_exact_content_recurrence"
    assert candidate.candidate_state == "ready_for_review"
    assert {o["scope_label"] for o in candidate.supporting_occurrences} == {"alpha", "beta"}


def test_a_repository_and_a_document_produce_no_candidate_but_the_fact_survives(facts, store):
    """The loose note.txt shares bytes with alpha's main.py. That is a real, queryable fact — and it
    is not two repositories, so this rule stays silent about it."""
    run = run_detection()
    assert [c.subject["entity_id"] for c in run.candidates] == [SHARED_CONTENT]

    from modules.observation_query import entity_occurrences

    found = entity_occurrences(DOCUMENT_CONTENT)
    assert found["occurrence_count"] == 2
    assert {o["scope_type"] for o in found["occurrences"]} == {"repository", "document"}


def test_a_repository_that_shares_nothing_produces_no_candidate(facts):
    run = run_detection()
    labels = {o["scope_label"] for c in run.candidates for o in c.supporting_occurrences}
    assert "gamma" not in labels


def test_the_run_reports_its_owning_profile_as_activated_under_rcd(facts):
    run = run_detection(dry_run=True)
    report = run.to_dict()
    assert report["profile_id"] == "repository_capability_discovery"
    assert report["profile_activated"] is True


def test_detection_fails_closed_when_the_owning_profile_is_unavailable(facts, monkeypatch):
    # Ownership is verified against the bundled registry. A detector whose profile does not exist,
    # or does not validate, does not get to run "anyway".
    from modules import profile_registry

    monkeypatch.setattr(
        profile_registry, "get_profile", lambda *a, **k: (_ for _ in ()).throw(KeyError("gone"))
    )
    with pytest.raises(PatternServiceError):
        run_detection(dry_run=True)


# -- dry run: nothing at all ---------------------------------------------------------------


def test_a_dry_run_stores_nothing_and_creates_no_candidate_tables(facts, store):
    before = tables(store)
    run = run_detection(dry_run=True)

    assert run.dry_run is True
    assert len(run.candidates) == 1
    assert run.stored == 0
    assert run.previewed == 1
    assert tables(store) == before
    assert "pattern_candidates" not in tables(store)
    assert "pattern_candidate_dispositions" not in tables(store)


def test_a_dry_run_against_a_missing_database_creates_no_database(tmp_path, monkeypatch):
    from modules.pattern_candidate_service import PatternServiceError

    path = tmp_path / "absent.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    with pytest.raises(PatternServiceError, match="active profile"):
        run_detection(dry_run=True)
    assert not path.exists()


def test_detection_against_a_missing_database_creates_no_database(tmp_path, monkeypatch):
    from modules.pattern_candidate_service import PatternServiceError

    path = tmp_path / "absent.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    with pytest.raises(PatternServiceError, match="active profile"):
        run_detection()
    assert not path.exists()


def test_zero_candidates_create_no_candidate_tables(workspace, store):
    from tests.unit.active_profile_test_helpers import plant_active_rcd

    plant_active_rcd()
    solo = make_repo(workspace / "solo", {"only.py": "print(0)\n"})
    observe(workspace, solo)

    run = run_detection()
    assert run.candidates == ()
    assert "pattern_candidates" not in tables(store)


# -- persistence ---------------------------------------------------------------------------


def test_the_first_stored_candidate_creates_only_the_authorized_tables(facts, store):
    before = tables(store)
    run = run_detection()

    assert run.stored == 1
    created = tables(store) - before
    assert created == {"pattern_candidates", "pattern_candidate_dispositions"}


def test_an_identical_rerun_is_idempotent(facts, store):
    first = run_detection()
    assert first.stored == 1

    second = run_detection()
    assert second.stored == 0
    assert second.already_present == 1
    assert len(rows(store, "pattern_candidates")) == 1
    assert second.candidates[0].candidate_id == first.candidates[0].candidate_id


def test_a_rerun_does_not_rewrite_the_stored_row(facts, store):
    run_detection()
    before = rows(store, "pattern_candidates")
    run_detection(now="2027-01-01T00:00:00+00:00")
    assert rows(store, "pattern_candidates") == before


def test_changed_evidence_inserts_a_new_version_and_leaves_the_old_one_untouched(facts, store):
    first = run_detection()
    original = rows(store, "pattern_candidates")

    # A third repository with the same Dockerfile bytes: new evidence, same subject.
    delta = make_repo(facts["workspace"] / "delta", {"Dockerfile": DOCKERFILE})
    observe(facts["workspace"], delta)

    second = run_detection()
    assert second.stored == 1
    assert len(rows(store, "pattern_candidates")) == 2

    old, new = first.candidates[0], second.candidates[0]
    assert new.candidate_id != old.candidate_id  # a new version
    assert new.candidate_key == old.candidate_key  # of the same logical candidate
    assert new.distinct_repository_scope_count == 3

    # The reviewed artifact is byte-identical to what it was.
    stored_old = [r for r in rows(store, "pattern_candidates") if r[0] == old.candidate_id]
    assert stored_old == [r for r in original if r[0] == old.candidate_id]


def test_a_stored_candidate_round_trips_through_its_contract(facts, store):
    run_detection()
    raw = json.loads(rows(store, "pattern_candidates")[0][-1])
    PatternCandidate.from_dict(raw).validate()


def test_detection_writes_into_no_other_table(facts, store):
    observation_rows = {
        table: rows(store, table)
        for table in ("observation_sets", "normalized_entities", "normalized_relationships")
    }
    run_detection()
    for table, before in observation_rows.items():
        assert rows(store, table) == before, table

    # The graph and the alerts are not this pipeline's business, and it does not create them.
    assert "graph_nodes" not in tables(store)
    assert "alerts" not in tables(store)


def test_the_persistence_layer_offers_no_update_or_delete(facts):
    import inspect

    for name in (
        "insert_pattern_candidate",
        "insert_pattern_disposition",
        "init_pattern_candidate_tables",
        "fetch_pattern_candidates",
        "fetch_pattern_candidate",
        "fetch_pattern_disposition",
    ):
        source = inspect.getsource(getattr(db, name)).upper()
        assert "UPDATE " not in source, name
        assert "DELETE " not in source, name
        assert "DROP " not in source, name

    for forbidden in (
        "update_pattern_candidate",
        "delete_pattern_candidate",
        "update_pattern_disposition",
        "delete_pattern_disposition",
    ):
        assert not hasattr(db, forbidden), forbidden


# -- validate on read ----------------------------------------------------------------------


def test_a_tampered_candidate_is_refused_on_read(facts, store):
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id

    raw = json.loads(rows(store, "pattern_candidates")[0][-1])
    raw["is_finding"] = True  # the digest no longer matches — and even if it did, the pin holds
    conn = sqlite3.connect(store)
    conn.execute(
        "UPDATE pattern_candidates SET candidate_json = ? WHERE candidate_id = ?",
        (json.dumps(raw), candidate_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(TamperedCandidateError):
        show_candidate(candidate_id)


def test_a_forged_evaluative_field_is_refused_on_read_even_when_the_digest_is_restamped(
    facts, store
):
    """The digest cannot be the gate: whoever can write the row can re-stamp it. The closed field
    vocabulary is what actually refuses this."""
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id

    raw = json.loads(rows(store, "pattern_candidates")[0][-1])
    raw["importance"] = 0.99
    content = {
        k: v for k, v in raw.items() if k not in ("candidate_id", "created_at", "artifact_digest")
    }
    raw["artifact_digest"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw["candidate_id"] = "pc_" + raw["artifact_digest"]

    conn = sqlite3.connect(store)
    conn.execute(
        "UPDATE pattern_candidates SET candidate_json = ? WHERE candidate_id = ?",
        (json.dumps(raw), candidate_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(TamperedCandidateError):
        show_candidate(candidate_id)


def test_a_tampered_disposition_is_refused_on_read(facts, store):
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id
    record_disposition(
        candidate_id=candidate_id,
        operator_id="operator-test",
        decision="dismissed",
        reason="Boilerplate I wrote once and reused.",
    )

    conn = sqlite3.connect(store)
    row = conn.execute("SELECT disposition_json FROM pattern_candidate_dispositions").fetchone()
    raw = json.loads(row[0])
    raw["decision"] = "accepted_for_followup"
    conn.execute(
        "UPDATE pattern_candidate_dispositions SET disposition_json = ?", (json.dumps(raw),)
    )
    conn.commit()
    conn.close()

    with pytest.raises(TamperedCandidateError):
        show_candidate(candidate_id)


# -- listing -------------------------------------------------------------------------------


def test_listing_an_absent_store_returns_empty_and_creates_nothing(tmp_path, monkeypatch):
    path = tmp_path / "absent.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    result = list_candidates()
    assert result == {"count": 0, "candidates": []}
    assert not path.exists()


def test_listing_a_store_without_candidate_tables_returns_empty_and_mutates_nothing(facts, store):
    before = tables(store)
    result = list_candidates()
    assert result["count"] == 0
    assert tables(store) == before


def test_listing_shows_the_latest_version_per_key_by_default(facts, store):
    first = run_detection()
    delta = make_repo(facts["workspace"] / "delta", {"Dockerfile": DOCKERFILE})
    observe(facts["workspace"], delta)
    second = run_detection()

    latest = list_candidates()
    assert latest["count"] == 1
    assert latest["candidates"][0]["candidate_id"] == second.candidates[0].candidate_id

    every = list_candidates(all_versions=True)
    assert every["count"] == 2
    assert {c["candidate_id"] for c in every["candidates"]} == {
        first.candidates[0].candidate_id,
        second.candidates[0].candidate_id,
    }


def test_listing_is_deterministic_and_never_ordered_by_occurrence_count(facts, store):
    run_detection()
    once = list_candidates(all_versions=True)
    twice = list_candidates(all_versions=True)
    assert once == twice

    keys = [c["candidate_key"] for c in once["candidates"]]
    assert keys == sorted(keys)


def test_listing_filters_before_it_bounds(facts, store):
    run_detection()
    assert list_candidates(profile_id="repository_capability_discovery")["count"] == 1
    assert list_candidates(profile_id="scientific_preservation")["count"] == 0
    assert list_candidates(detector_id="repository_exact_content_recurrence")["count"] == 1
    assert list_candidates(candidate_type="repository_exact_content_recurrence")["count"] == 1


def test_listing_can_filter_by_disposition(facts, store):
    run = run_detection()
    assert list_candidates(disposition="dismissed")["count"] == 0

    record_disposition(
        candidate_id=run.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="dismissed",
        reason="Shared boilerplate.",
    )
    assert list_candidates(disposition="dismissed")["count"] == 1
    assert list_candidates(disposition="accepted_for_followup")["count"] == 0


def test_listing_carries_the_current_disposition(facts, store):
    run = run_detection()
    assert list_candidates()["candidates"][0]["disposition"] is None

    record_disposition(
        candidate_id=run.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="needs_more_evidence",
        reason="Repository lineage has not been assessed.",
    )
    listed = list_candidates()["candidates"][0]
    assert listed["disposition"]["decision"] == "needs_more_evidence"


def test_an_unknown_filter_value_is_an_error_not_an_empty_result(facts, store):
    # "That is not a word I know" and "Lighthouse knows nothing about that" are different claims.
    run_detection()
    with pytest.raises(PatternServiceError):
        list_candidates(disposition="approved")


# -- show and id resolution ----------------------------------------------------------------


def test_show_returns_the_complete_candidate(facts, store):
    run = run_detection()
    shown = show_candidate(run.candidates[0].candidate_id)

    assert shown["candidate"]["candidate_id"] == run.candidates[0].candidate_id
    assert shown["candidate"]["supporting_occurrences"]
    assert set(shown["candidate"]["unknowns"])
    assert set(shown["candidate"]["limitations"])
    assert shown["candidate"]["is_finding"] is False
    assert shown["disposition"] is None
    # The prose an operator actually reads, resolved from the stored codes.
    assert shown["limitation_statements"]
    assert shown["unknown_statements"]


def test_show_accepts_an_unambiguous_prefix_and_returns_the_full_id(facts, store):
    run = run_detection()
    full = run.candidates[0].candidate_id
    digest = full.removeprefix("pc_")

    for value in (full, digest, digest[:8], digest[:12]):
        assert show_candidate(value)["candidate"]["candidate_id"] == full
        assert resolve_candidate_id(value) == full


def test_a_short_prefix_is_rejected(facts, store):
    run = run_detection()
    digest = run.candidates[0].candidate_id.removeprefix("pc_")
    with pytest.raises(PatternServiceError):
        show_candidate(digest[:7])


def test_an_ambiguous_prefix_names_every_candidate_and_never_guesses(facts, store, monkeypatch):
    run_detection()
    monkeypatch.setattr(
        db,
        "fetch_pattern_candidate_ids_by_prefix",
        lambda *a, **k: ["pc_" + "a" * 64, "pc_" + "b" * 64],
    )
    with pytest.raises(AmbiguousCandidateId) as excinfo:
        show_candidate("aaaaaaaa")
    assert "a" * 64 in str(excinfo.value)
    assert "b" * 64 in str(excinfo.value)


def test_an_unknown_candidate_is_an_error(facts, store):
    run_detection()
    with pytest.raises(CandidateNotFound):
        show_candidate("f" * 64)


def test_a_non_hex_id_is_rejected(facts, store):
    run_detection()
    with pytest.raises(PatternServiceError):
        show_candidate("../../etc/passwd")


# -- disposition ---------------------------------------------------------------------------


def test_each_decision_is_recorded(facts, store):
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id

    result = record_disposition(
        candidate_id=candidate_id,
        operator_id="operator-test",
        decision="accepted_for_followup",
        reason="Worth checking whether beta was seeded from alpha.",
    )
    assert result["disposition"]["decision"] == "accepted_for_followup"
    assert result["disposition"]["review_method"] == "local_cli"
    assert result["disposition"]["operator_identity_authenticated"] is False
    assert result["disposition"]["candidate_artifact_digest"] == run.candidates[0].artifact_digest


def test_a_disposition_does_not_mutate_the_candidate(facts, store):
    run = run_detection()
    before = rows(store, "pattern_candidates")

    record_disposition(
        candidate_id=run.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="dismissed",
        reason="My own boilerplate.",
    )
    assert rows(store, "pattern_candidates") == before


def test_a_second_disposition_on_the_same_version_fails_closed(facts, store):
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id

    record_disposition(
        candidate_id=candidate_id,
        operator_id="operator-test",
        decision="dismissed",
        reason="First call.",
    )
    with pytest.raises(DispositionExists):
        record_disposition(
            candidate_id=candidate_id,
            operator_id="operator-test",
            decision="accepted_for_followup",
            reason="Changed my mind.",
        )
    assert len(rows(store, "pattern_candidate_dispositions")) == 1


def test_the_unique_index_is_the_gate_not_the_service_check(facts, store):
    """A rule only the writer honours is not a rule. SQLite refuses the second row itself."""
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id
    record_disposition(
        candidate_id=candidate_id, operator_id="operator-test", decision="dismissed", reason="One."
    )

    conn = sqlite3.connect(store)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pattern_candidate_dispositions (disposition_id, candidate_id, "
                "candidate_artifact_digest, schema_version, decision, operator_id, reason, "
                "review_method, operator_identity_authenticated, artifact_digest, created_at, "
                "disposition_json) VALUES (?, ?, ?, 1, 'dismissed', 'x', 'y', 'local_cli', 0, ?, "
                "'now', '{}')",
                ("pd_" + "e" * 64, candidate_id, "c" * 64, "e" * 64),
            )
    finally:
        conn.close()


def test_a_new_candidate_version_may_receive_its_own_disposition(facts, store):
    first = run_detection()
    record_disposition(
        candidate_id=first.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="dismissed",
        reason="Only two repositories.",
    )

    delta = make_repo(facts["workspace"] / "delta", {"Dockerfile": DOCKERFILE})
    observe(facts["workspace"], delta)
    second = run_detection()

    result = record_disposition(
        candidate_id=second.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="accepted_for_followup",
        reason="A third repository changes the picture.",
    )
    assert result["disposition"]["decision"] == "accepted_for_followup"
    assert len(rows(store, "pattern_candidate_dispositions")) == 2

    # The earlier decision is exactly as it was left.
    earlier = show_candidate(first.candidates[0].candidate_id)
    assert earlier["disposition"]["decision"] == "dismissed"


def test_an_operator_id_is_required(facts, store):
    run = run_detection()
    for bad in ("", "   "):
        with pytest.raises(PatternServiceError):
            record_disposition(
                candidate_id=run.candidates[0].candidate_id,
                operator_id=bad,
                decision="dismissed",
                reason="Because.",
            )


def test_a_reason_is_required(facts, store):
    run = run_detection()
    for bad in ("", "   "):
        with pytest.raises(PatternServiceError):
            record_disposition(
                candidate_id=run.candidates[0].candidate_id,
                operator_id="operator-test",
                decision="dismissed",
                reason=bad,
            )


def test_a_decision_outside_the_closed_enum_is_rejected(facts, store):
    run = run_detection()
    for bad in ("approved", "verified", "finding", "published", "rejected_as_false"):
        with pytest.raises(PatternServiceError):
            record_disposition(
                candidate_id=run.candidates[0].candidate_id,
                operator_id="operator-test",
                decision=bad,
                reason="Because.",
            )
    assert not rows(store, "pattern_candidate_dispositions")


def test_a_disposition_on_an_unknown_candidate_is_rejected(facts, store):
    run_detection()
    with pytest.raises(CandidateNotFound):
        record_disposition(
            candidate_id="f" * 64,
            operator_id="operator-test",
            decision="dismissed",
            reason="Because.",
        )


def test_a_disposition_creates_no_finding_and_contacts_nothing(facts, store):
    run = run_detection()
    result = record_disposition(
        candidate_id=run.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="accepted_for_followup",
        reason="Worth a look.",
    )
    body = json.dumps(result)
    for absent in ("is_finding", "is_verified", "authorizes", "gateway", "publish"):
        assert absent not in body.lower(), absent

    # And the candidate it points at still says, structurally, that it is none of those things.
    shown = show_candidate(run.candidates[0].candidate_id)
    assert shown["candidate"]["is_finding"] is False
    assert shown["candidate"]["contacts_gateway"] is False
    assert shown["candidate"]["operator_review_required"] is True


def test_accepted_for_followup_does_not_change_the_candidate_state(facts, store):
    run = run_detection()
    record_disposition(
        candidate_id=run.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="accepted_for_followup",
        reason="Worth a look.",
    )
    shown = show_candidate(run.candidates[0].candidate_id)
    assert shown["candidate"]["candidate_state"] == "ready_for_review"


# -- the service reads facts through the read boundary --------------------------------------


def test_the_service_reads_facts_only_through_the_read_boundary(facts):
    """It may write candidates. It may not reach into the fact tables to do it."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(service))
    called: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "db"
        ):
            called.add(node.func.attr)

    # Writes are candidate-only. Observation facts still come through observation_query; the
    # public-source ledger (the scientific detector's facts, LHP-INTEL-04C) is read through db's
    # READ-ONLY fetch_* helpers and re-validated on the way out. No db.* call here writes a fact.
    assert called <= {
        # the candidate store — writes and reads
        "init_pattern_candidate_tables",
        "pattern_candidate_tables_exist",
        "insert_pattern_candidate",
        "insert_pattern_disposition",
        "fetch_pattern_candidates",
        "fetch_pattern_candidate",
        "fetch_pattern_candidate_ids_by_prefix",
        "fetch_pattern_disposition",
        "fetch_pattern_dispositions",
        # the append-only public-source ledger — READ-ONLY
        "fetch_public_source_locator_index",
        "fetch_public_source_checks",
        "fetch_public_source_run",
        # an existence check only, never a fact row
        "observation_tables_exist",
    }, called
    # And none of them writes a fact: no ledger write, no normalized-observation write.
    fact_writes = {"store_check_run", "store_normalized_observation", "init_observation_tables"}
    assert not (called & fact_writes)


def test_the_service_never_writes_to_the_fact_store(facts, store):
    fact_tables = ("observation_sets", "normalized_entities", "normalized_relationships")
    before = {t: rows(store, t) for t in fact_tables}

    run = run_detection()
    record_disposition(
        candidate_id=run.candidates[0].candidate_id,
        operator_id="operator-test",
        decision="dismissed",
        reason="Because.",
    )
    show_candidate(run.candidates[0].candidate_id)
    list_candidates()

    for table, expected in before.items():
        assert rows(store, table) == expected, table


def test_nothing_in_the_service_ranks(facts):
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(service))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            lowered = node.name.lower()
            assert not any(
                bad in lowered
                for bad in ("score", "rank", "importance", "priority", "severity", "significan")
            ), node.name


# -- adversarial pass 3: the review-authority attack -----------------------------------------


def test_no_code_anywhere_branches_on_an_operator_decision():
    """`accepted_for_followup` cannot behave like an approval, because nothing READS it to decide.

    This is the structural form of the promise. A decision is stored, displayed, and used as an
    equality filter against a closed enum — it never gates behaviour. The moment some code says
    `if decision == "accepted_for_followup": ...`, the disposition has become authority, and this
    test is what would notice.
    """
    import ast
    from pathlib import Path

    import main as main_module
    from modules import pattern_candidate, repository_content_recurrence

    sources = [
        Path(module.__file__)
        for module in (pattern_candidate, service, repository_content_recurrence, main_module)
    ]
    decisions = {"accepted_for_followup", "dismissed", "needs_more_evidence"}

    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            literals = {
                operand.value
                for operand in (node.left, *node.comparators)
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str)
            }
            assert not (literals & decisions), (
                f"{path.name}:{node.lineno} compares against a decision value — a disposition that "
                f"gates behaviour is an authorization"
            )


def test_a_disposition_grants_no_authority_the_candidate_did_not_have(facts, store):
    run = run_detection()
    candidate_id = run.candidates[0].candidate_id

    for decision in ("accepted_for_followup",):
        record_disposition(
            candidate_id=candidate_id,
            operator_id="operator-test",
            decision=decision,
            reason="Worth investigating.",
        )

    shown = show_candidate(candidate_id)
    # The candidate is byte-for-byte what it was, including every denial.
    for pin in (
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
    ):
        assert shown["candidate"][pin] is False, pin
    assert shown["candidate"]["operator_review_required"] is True
    assert shown["candidate"]["candidate_state"] == "ready_for_review"
    # And the disposition itself claims no authenticated identity.
    assert shown["disposition"]["operator_identity_authenticated"] is False
