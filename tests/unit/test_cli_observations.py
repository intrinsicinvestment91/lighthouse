"""`lighthouse observations` — the operator's window onto the fact store (LHP-INTEL-02A).

End to end through `main.cli_main()`, against a real temporary database. The guarantees that matter
at this boundary are the ones an operator can actually trip over:

* an empty store answers `{"count": 0}` and **exits 0** — it is not an error to know nothing;
* a read leaves **no database behind** on a machine that has never observed anything;
* `--json` stdout stays parseable, because every error goes to stderr;
* a bad argument exits non-zero rather than guessing what was meant;
* nothing any of the four commands does changes a single byte of the store.
"""

import hashlib
import json
from pathlib import Path

import pytest

import config
import main
from config import ObservationConfig
from modules import db
from modules.document_intake import observe_document
from modules.observation_normalizer import (
    normalize_document_observation,
    normalize_repository_observation,
)
from modules.repository_intake import observe_repository
from tests.unit.test_repository_intake import make_repo


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(
        config,
        "observation_cfg",
        ObservationConfig(sources_path=str(tmp_path / "sources.json"), allowed_roots=[]),
    )
    return path


@pytest.fixture
def observed(tmp_path: Path, store: Path) -> dict:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = make_repo(
        workspace / "widget", {"README.md": "# widget\n", "Dockerfile": "FROM scratch\n"}
    )
    doc = workspace / "notes.md"
    doc.write_text("# notes\n", encoding="utf-8")

    roots = (str(workspace),)
    repo_set = normalize_repository_observation(observe_repository(str(repo), allowed_roots=roots))
    doc_set = normalize_document_observation(observe_document(str(doc), allowed_roots=roots))

    db.init_observation_tables()
    db.store_normalized_observation(repo_set)
    db.store_normalized_observation(doc_set)
    return {
        "repository_id": repo_set.normalization_digest,
        "document_id": doc_set.normalization_digest,
    }


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["lighthouse", "observations", *argv])
    main.cli_main()


def file_digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# -- an empty store is not an error --------------------------------------------------------


def test_list_on_an_empty_store_prints_zero_and_exits_clean(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "list", "--json")
    assert json.loads(capsys.readouterr().out) == {
        "count": 0,
        "observations": [],
        "scan_truncated": False,
    }


def test_a_read_on_a_fresh_machine_leaves_no_database_behind(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """The guarantee 01E established, held on the read side too."""
    assert not store.exists()
    for argv in (["list"], ["stats"], ["search", "--scope-type", "repository"]):
        run_cli(monkeypatch, *argv)
        capsys.readouterr()
    assert not store.exists(), "a read created the database"


def test_stats_on_an_empty_store_is_all_zeros(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "stats", "--json")
    stats = json.loads(capsys.readouterr().out)
    assert stats["observation_sets"] == 0
    assert stats["entities"] == 0


# -- the four commands ---------------------------------------------------------------------


def test_list_reports_both_observations(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "list", "--json")
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 2
    assert {record["id"] for record in result["observations"]} == {
        observed["repository_id"],
        observed["document_id"],
    }


def test_search_narrows_by_sensor(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "search", "--sensor", "git_metadata", "--json")
    result = json.loads(capsys.readouterr().out)
    assert [record["id"] for record in result["observations"]] == [observed["repository_id"]]


def test_show_accepts_an_abbreviated_id_and_returns_the_full_one(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "show", "--id", observed["repository_id"][:8], "--json")
    record = json.loads(capsys.readouterr().out)
    assert record["id"] == observed["repository_id"]
    assert record["entities"], "show expands the set it resolved"


def test_stats_counts_what_was_stored(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "stats", "--json")
    stats = json.loads(capsys.readouterr().out)
    assert stats["repositories"] == 1
    assert stats["documents"] == 1
    assert stats["entities"] > 0


def test_human_output_renders_without_json(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "list")
    out = capsys.readouterr().out
    assert "2 observation(s)" in out
    assert "repository" in out
    assert "git_metadata" in out


# -- refusing, rather than guessing --------------------------------------------------------


def test_an_unknown_sensor_exits_nonzero_and_says_so_on_stderr(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch, "search", "--sensor", "repository_intake", "--json")
    assert exit_code.value.code == 1

    captured = capsys.readouterr()
    assert "sensor" in captured.err
    assert captured.out == "", "an error on stdout would corrupt --json"


def test_an_unknown_id_exits_nonzero(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch, "show", "--id", "0" * 64)
    assert exit_code.value.code == 1
    assert "no observation matches" in capsys.readouterr().err


def test_a_short_prefix_exits_nonzero(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch, "show", "--id", "abc")
    assert exit_code.value.code == 1
    assert "hexadecimal" in capsys.readouterr().err


def test_observations_with_no_action_exits_nonzero(
    observed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch)
    assert exit_code.value.code == 1
    assert "list, show, search, stats" in capsys.readouterr().err


# -- a read is a read ----------------------------------------------------------------------


def test_no_command_changes_a_byte_of_the_store(
    observed: dict, store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    before = file_digest(store)

    run_cli(monkeypatch, "list", "--json")
    run_cli(monkeypatch, "search", "--scope-type", "document", "--json")
    run_cli(monkeypatch, "show", "--id", observed["document_id"], "--json")
    run_cli(monkeypatch, "stats", "--json")
    capsys.readouterr()

    assert file_digest(store) == before, "a read changed the store"


# -- cross-scope fact discovery (LHP-INTEL-02B) ---------------------------------------------


@pytest.fixture
def cross_scope(tmp_path: Path, store: Path) -> dict:
    """Two repositories that genuinely share a technology and a language, and nothing invented."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repositories = {
        "alpha": make_repo(
            workspace / "alpha", {"Dockerfile": "FROM scratch\n", "main.py": "print(1)\n"}
        ),
        "beta": make_repo(
            workspace / "beta", {"Dockerfile": "FROM scratch\n", "main.py": "print(2)\n"}
        ),
    }
    roots = (str(workspace),)
    results = {
        name: normalize_repository_observation(observe_repository(str(path), allowed_roots=roots))
        for name, path in repositories.items()
    }

    db.init_observation_tables()
    for result in results.values():
        db.store_normalized_observation(result)
    return {name: result.scope_id for name, result in results.items()}


def test_where_reports_every_scope_an_entity_was_observed_in(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "where", "--entity-id", "technology:docker", "--json")

    result = json.loads(capsys.readouterr().out)
    assert result["entity"]["entity_id"] == "technology:docker"
    assert result["occurrence_count"] == 2
    assert {o["scope_id"] for o in result["occurrences"]} == set(cross_scope.values())
    assert all(o["sensors"] == ["filesystem"] for o in result["occurrences"])


def test_where_on_an_unknown_entity_is_an_empty_result_and_exits_zero(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "where", "--entity-id", "technology:rust", "--json")

    result = json.loads(capsys.readouterr().out)
    assert result["occurrence_count"] == 0


def test_where_on_a_malformed_entity_id_exits_nonzero(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch, "where", "--entity-id", "docker", "--json")
    assert exit_code.value.code == 1

    captured = capsys.readouterr()
    assert "entity id" in captured.err
    assert captured.out == "", "an error on stdout would corrupt --json"


def test_shared_returns_the_exact_intersection(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(
        monkeypatch,
        "shared",
        "--scope-id",
        cross_scope["alpha"],
        "--scope-id",
        cross_scope["beta"],
        "--json",
    )

    result = json.loads(capsys.readouterr().out)
    ids = {entity["entity_id"] for entity in result["shared_entities"]}
    assert "technology:docker" in ids
    assert "language:Python" in ids
    assert result["shared_entity_count"] == len(result["shared_entities"])
    for entity in result["shared_entities"]:
        assert {o["scope_id"] for o in entity["occurrences"]} == set(cross_scope.values())


def test_shared_accepts_abbreviated_scope_ids(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(
        monkeypatch,
        "shared",
        "--scope-id",
        cross_scope["alpha"][:8],
        "--scope-id",
        cross_scope["beta"][:8],
        "--json",
    )

    result = json.loads(capsys.readouterr().out)
    assert {scope["scope_id"] for scope in result["scopes"]} == set(cross_scope.values())
    assert all(len(scope["scope_id"]) == 64 for scope in result["scopes"])


def test_shared_with_a_single_scope_exits_nonzero(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch, "shared", "--scope-id", cross_scope["alpha"], "--json")
    assert exit_code.value.code == 1
    assert "two scopes" in capsys.readouterr().err


def test_shared_with_an_unknown_scope_exits_nonzero_rather_than_reporting_nothing_in_common(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_code:
        run_cli(monkeypatch, "shared", "--scope-id", cross_scope["alpha"], "--scope-id", "0" * 64)
    assert exit_code.value.code == 1

    captured = capsys.readouterr()
    assert "no observed scope matches" in captured.err
    assert "0" not in captured.out, "an unanswerable question must not look like an empty answer"


def test_cross_scope_human_output_states_facts_only(
    cross_scope: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    run_cli(monkeypatch, "where", "--entity-id", "technology:docker")
    where = capsys.readouterr().out
    run_cli(
        monkeypatch,
        "shared",
        "--scope-id",
        cross_scope["alpha"],
        "--scope-id",
        cross_scope["beta"],
    )
    shared = capsys.readouterr().out

    assert "technology:docker" in where
    assert "occurrence(s)" in where
    assert "observed in every requested scope" in shared
    for text in (where, shared):
        for word in ("score", "rank", "important", "similar", "recommend", "most common"):
            assert word not in text.lower(), word


def test_cross_scope_reads_leave_no_database_behind(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    assert not store.exists()

    run_cli(monkeypatch, "where", "--entity-id", "technology:docker", "--json")
    assert json.loads(capsys.readouterr().out)["occurrence_count"] == 0

    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "shared", "--scope-id", "a" * 64, "--scope-id", "b" * 64)
    capsys.readouterr()

    assert not store.exists(), "a read created a database"


def test_no_cross_scope_command_changes_a_byte_of_the_store(
    cross_scope: dict, store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    before = file_digest(store)

    run_cli(monkeypatch, "where", "--entity-id", "technology:docker", "--json")
    run_cli(
        monkeypatch,
        "shared",
        "--scope-id",
        cross_scope["alpha"],
        "--scope-id",
        cross_scope["beta"],
        "--json",
    )
    capsys.readouterr()

    assert file_digest(store) == before, "a read changed the store"
