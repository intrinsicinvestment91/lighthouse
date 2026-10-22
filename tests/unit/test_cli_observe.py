"""LHP-INTEL-01E — `lighthouse observe`, the operator's way in.

The mission of this packet was "make observations possible without manually writing Python", and
this is the command that does it. It is a **local, read-only observation command, not an agent
capability**: it observes exactly what the operator declared, inside exactly the roots the operator
configured, and it never decides that anything it found is interesting.

These tests run the real thing end to end — a real git repository, a real document, a real
(throwaway) database. `db.DB_PATH` and the observation config are both pointed at temp paths, so
the developer's `data/state.db` is never opened and a clean CI checkout with no `data/` at all
behaves identically.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import config
import main
from config import ObservationConfig
from modules import db
from tests.unit.test_repository_intake import make_repo

# -- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture
def repo(workspace: Path) -> Path:
    return make_repo(workspace / "widget", {"README.md": "# widget\n", "src/w.py": "x = 1\n"})


def configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    roots: list[Path],
    sources: list[dict],
) -> Path:
    """Point the CLI at a temp sources file and temp roots — the operator's two declarations."""
    from tests.unit.active_profile_test_helpers import plant_active_rcd

    plant_active_rcd()
    sources_path = tmp_path / "observation_sources.json"
    sources_path.write_text(json.dumps({"schema_version": 1, "sources": sources}), encoding="utf-8")
    monkeypatch.setattr(
        config,
        "observation_cfg",
        ObservationConfig(
            sources_path=str(sources_path),
            allowed_roots=[str(root) for root in roots],
        ),
    )
    return sources_path


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["main.py", "observe", *args])
    main.cli_main()


def table_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


# -- it works ------------------------------------------------------------------------------


def test_observe_persists_a_declared_repository(
    monkeypatch, capsys, tmp_path, workspace, repo, store
):
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(repo)}],
    )

    run_cli(monkeypatch)

    out = capsys.readouterr().out
    assert "stored" in out
    assert store.exists()


def test_observe_json_reports_the_outcome(monkeypatch, capsys, tmp_path, workspace, repo, store):
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(repo)}],
    )

    run_cli(monkeypatch, "--json")

    payload = json.loads(capsys.readouterr().out)
    assert payload["stored"] == 1
    assert payload["failed"] == 0
    assert payload["dry_run"] is False
    assert payload["outcomes"][0]["normalization_digest"]
    # It reports what exists. It never reports what matters.
    assert "score" not in payload["outcomes"][0]


def test_observing_twice_stores_once(monkeypatch, capsys, tmp_path, workspace, repo, store):
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(repo)}],
    )

    run_cli(monkeypatch, "--json")
    first = json.loads(capsys.readouterr().out)

    run_cli(monkeypatch, "--json")
    second = json.loads(capsys.readouterr().out)

    assert first["stored"] == 1
    assert second["already_present"] == 1
    assert second["stored"] == 0
    assert (
        second["outcomes"][0]["normalization_digest"]
        == first["outcomes"][0]["normalization_digest"]
    )


# -- dry run -------------------------------------------------------------------------------


def test_dry_run_writes_no_database(monkeypatch, capsys, tmp_path, workspace, repo, store):
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(repo)}],
    )

    run_cli(monkeypatch, "--dry-run", "--json")

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["previewed"] == 1
    assert "observation_sets" not in table_names(store)


# -- refusal -------------------------------------------------------------------------------


def test_no_configured_roots_refuses_and_exits_nonzero(
    monkeypatch, capsys, tmp_path, workspace, repo, store
):
    """The container's honest behaviour: nothing is mounted, no roots are set, so it refuses."""
    configure(
        monkeypatch,
        tmp_path,
        roots=[],
        sources=[{"type": "repository", "path": str(repo)}],
    )

    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch)

    assert exit_info.value.code == 1
    assert "LIGHTHOUSE_OBSERVATION_ROOTS" in capsys.readouterr().err
    assert "observation_sets" not in table_names(store)


def test_a_missing_sources_file_refuses_and_exits_nonzero(
    monkeypatch, capsys, tmp_path, workspace, store
):
    monkeypatch.setattr(
        config,
        "observation_cfg",
        ObservationConfig(
            sources_path=str(tmp_path / "absent.json"),
            allowed_roots=[str(workspace)],
        ),
    )

    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch)

    assert exit_info.value.code == 1
    assert "not found" in capsys.readouterr().err
    assert "observation_sets" not in table_names(store)


def test_a_failing_source_exits_nonzero(monkeypatch, capsys, tmp_path, workspace, store):
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(workspace / "absent")}],
    )

    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch)

    assert exit_info.value.code == 1
    assert "source_not_found" in capsys.readouterr().out


# -- --source is a filter, never a way to name a new path ----------------------------------


def test_source_selects_one_declared_source(monkeypatch, capsys, tmp_path, workspace, repo, store):
    other = make_repo(workspace / "other", {"README.md": "# other\n"})
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[
            {"type": "repository", "path": str(repo)},
            {"type": "repository", "path": str(other)},
        ],
    )

    run_cli(monkeypatch, "--source", str(repo), "--json")

    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"] == 1
    assert payload["outcomes"][0]["path"] == str(repo)


def test_source_cannot_introduce_an_undeclared_path(
    monkeypatch, capsys, tmp_path, workspace, repo, store
):
    """`--source` filters the declared list. It is not a way to hand the CLI an arbitrary path.

    If it were, the sources file would stop being the work list and the boundary would be one
    careless flag away from being whatever the caller typed.
    """
    undeclared = make_repo(workspace / "undeclared", {"README.md": "# nope\n"})
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(repo)}],
    )

    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "--source", str(undeclared))

    assert exit_info.value.code == 1
    assert "not a declared observation source" in capsys.readouterr().err
    assert "observation_sets" not in table_names(store)


def test_a_disabled_source_is_not_observed(monkeypatch, capsys, tmp_path, workspace, repo, store):
    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[{"type": "repository", "path": str(repo), "enabled": False}],
    )

    run_cli(monkeypatch, "--json")

    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"] == 0
    assert "observation_sets" not in table_names(store)
