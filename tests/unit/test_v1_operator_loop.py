"""V1 candidate slice — repository_capability_discovery operator loop (LHP-V1-RCD-01A).

Exercises the real CLI handlers end-to-end on disposable git fixtures and a temporary database:

  active RCD → observe (dry-run) → observe → observations list/where → patterns detect →
  patterns list/show/review → patterns detect again (idempotent)

No host datastore, no network, no scoring. Activation is planted as the post-activation
store state (the ceremony is covered by LHP-V1-PROFILE-01C tests).
"""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest

import config
import main
from config import ObservationConfig
from modules import db
from modules.observation_sources import load_sources
from tests.unit.test_repository_intake import make_repo

DOCKERFILE = "FROM scratch\n"
SHARED_CONTENT = "content:" + hashlib.sha256(DOCKERFILE.encode()).hexdigest()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    from tests.unit.active_profile_test_helpers import plant_active_rcd

    plant_active_rcd()
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch):
    """Fail closed if any code path attempts a real socket connect."""

    def _blocked(*_args, **_kwargs):
        raise AssertionError("network connect invoked during V1 RCD operator loop")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    return None


def configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    roots: list[Path],
    sources: list[dict],
) -> Path:
    sources_path = tmp_path / "observation_sources.json"
    sources_path.write_text(
        json.dumps({"schema_version": 1, "sources": sources}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        config,
        "observation_cfg",
        ObservationConfig(
            sources_path=str(sources_path),
            allowed_roots=[str(root) for root in roots],
        ),
    )
    return sources_path


def invoke(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["lighthouse", *argv])
    main.cli_main()


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


def test_v1_repository_capability_discovery_loop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    workspace: Path,
    store: Path,
    no_network,
):
    alpha = make_repo(workspace / "alpha", {"Dockerfile": DOCKERFILE, "main.py": "print(1)\n"})
    beta = make_repo(workspace / "beta", {"Dockerfile": DOCKERFILE, "app.py": "print(2)\n"})

    configure(
        monkeypatch,
        tmp_path,
        roots=[workspace],
        sources=[
            {
                "type": "repository",
                "path": str(alpha),
                "enabled": True,
                "profile_id": "repository_capability_discovery",
            },
            {
                "type": "repository",
                "path": str(beta),
                "enabled": True,
                "profile_id": "repository_capability_discovery",
            },
        ],
    )

    # -- dry-run observe writes no observation rows (active-profile table may exist) ----
    invoke(monkeypatch, "observe", "--dry-run", "--json")
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["previewed"] == 2
    assert preview["stored"] == 0
    assert preview["failed"] == 0
    assert "observation_sets" not in tables(store)

    # -- persist observations -----------------------------------------------------------
    invoke(monkeypatch, "observe", "--json")
    persisted = json.loads(capsys.readouterr().out)
    assert persisted["dry_run"] is False
    assert persisted["stored"] == 2
    assert persisted["failed"] == 0
    assert store.exists()
    digests = sorted(outcome["normalization_digest"] for outcome in persisted["outcomes"])
    assert len(digests) == 2
    assert all(len(d) == 64 for d in digests)

    # Re-observe: identical content → already_present (deterministic identity)
    invoke(monkeypatch, "observe", "--json")
    again_obs = json.loads(capsys.readouterr().out)
    assert again_obs["already_present"] == 2
    assert again_obs["stored"] == 0
    assert sorted(o["normalization_digest"] for o in again_obs["outcomes"]) == digests

    # -- query returns the stored facts -------------------------------------------------
    invoke(monkeypatch, "observations", "list", "--json")
    listed_obs = json.loads(capsys.readouterr().out)
    assert listed_obs["count"] == 2

    invoke(monkeypatch, "observations", "where", "--entity-id", SHARED_CONTENT, "--json")
    where = json.loads(capsys.readouterr().out)
    assert where["occurrence_count"] == 2
    assert where["entity"]["entity_id"] == SHARED_CONTENT
    scope_ids = {occ["scope_id"] for occ in where["occurrences"]}
    assert len(scope_ids) == 2

    # -- detect dry-run writes nothing new ----------------------------------------------
    before_tables = tables(store)
    invoke(monkeypatch, "patterns", "detect", "--dry-run", "--json")
    detect_preview = json.loads(capsys.readouterr().out)
    assert detect_preview["dry_run"] is True
    assert detect_preview["candidate_count"] == 1
    assert detect_preview["stored"] == 0
    assert detect_preview["detector_id"] == "repository_exact_content_recurrence"
    assert detect_preview["profile_id"] == "repository_capability_discovery"
    assert detect_preview["profile_activated"] is True
    assert tables(store) == before_tables
    assert "pattern_candidates" not in tables(store)

    # -- persist the candidate ----------------------------------------------------------
    invoke(monkeypatch, "patterns", "detect", "--json")
    detect_live = json.loads(capsys.readouterr().out)
    assert detect_live["candidate_count"] == 1
    assert detect_live["stored"] == 1
    assert "pattern_candidates" in tables(store)

    invoke(monkeypatch, "patterns", "list", "--json")
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1
    candidate_id = listed["candidates"][0]["candidate_id"]
    assert candidate_id.startswith("pc_")

    invoke(monkeypatch, "patterns", "show", "--id", candidate_id, "--json")
    shown = json.loads(capsys.readouterr().out)
    candidate = shown["candidate"]
    assert candidate["profile_id"] == "repository_capability_discovery"
    assert candidate["detector_id"] == "repository_exact_content_recurrence"
    assert candidate["distinct_repository_scope_count"] == 2
    assert candidate["subject"]["entity_id"] == SHARED_CONTENT
    assert len(candidate["supporting_occurrences"]) == 2
    assert candidate["is_finding"] is False
    assert candidate["activates_profile"] is False

    # -- review disposition persists ----------------------------------------------------
    invoke(
        monkeypatch,
        "patterns",
        "review",
        "--id",
        candidate_id,
        "--operator-id",
        "v1-operator",
        "--decision",
        "needs_more_evidence",
        "--reason",
        "Repository lineage has not been assessed.",
        "--json",
    )
    disposition = json.loads(capsys.readouterr().out)
    assert disposition["disposition"]["decision"] == "needs_more_evidence"
    assert disposition["disposition"]["operator_identity_authenticated"] is False
    assert disposition["candidate_id"] == candidate_id

    invoke(monkeypatch, "patterns", "list", "--disposition", "needs_more_evidence", "--json")
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["count"] == 1
    assert reviewed["candidates"][0]["disposition"]["decision"] == "needs_more_evidence"

    # -- re-detect is idempotent --------------------------------------------------------
    invoke(monkeypatch, "patterns", "detect", "--json")
    redetect = json.loads(capsys.readouterr().out)
    assert redetect["stored"] == 0
    assert redetect["already_present"] == 1

    invoke(monkeypatch, "patterns", "list", "--json")
    assert json.loads(capsys.readouterr().out)["count"] == 1

    # -- all state stayed in the temporary database -------------------------------------
    assert store.exists()
    assert store.parent == tmp_path
    assert "pattern_candidate_dispositions" in tables(store)


def test_v1_observation_sources_example_matches_schema():
    """Example must satisfy the closed observation_sources contract (load_sources)."""
    example = Path(__file__).resolve().parents[2] / "examples" / "observation_sources.v1.json"
    payload = json.loads(example.read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"schema_version", "sources"}
    assert payload["schema_version"] == 1

    config_loaded = load_sources(example)
    assert len(config_loaded.sources) == 2
    for source in config_loaded.sources:
        assert source.type == "repository"
        assert source.enabled is True
        assert source.path.startswith("/path/to/approved/root/")
        # Declarative ownership metadata only — not an activation claim.
        assert source.profile_id == "repository_capability_discovery"
        assert "http://" not in source.path
        assert "https://" not in source.path
