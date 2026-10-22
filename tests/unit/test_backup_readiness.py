"""Unit tests for backup-boundary safety and readiness inspection.

These tests cover the two net-new capabilities: canonical path/boundary safety (traversal and
symlink escape) and the fail-closed READY / NOT_READY / UNKNOWN state machine.
"""

from modules.backup_readiness import (
    CATEGORY_AUTHORITATIVE,
    CATEGORY_RECONSTRUCTABLE,
    CATEGORY_RUNTIME_EPHEMERAL,
    STATE_CATEGORIES,
    inspect_backup_readiness,
    is_within_boundary,
    resolve_path,
)

# -- path / boundary safety --------------------------------------------------------------


def test_data_path_accepted():
    assert is_within_boundary("/data/lighthouse.db", "/data") is True


def test_nested_data_path_accepted():
    assert is_within_boundary("/data/db/lighthouse.sqlite3", "/data") is True
    assert is_within_boundary("/data/graph/state.db", "/data") is True


def test_boundary_itself_accepted():
    assert is_within_boundary("/data", "/data") is True


def test_external_absolute_path_rejected():
    assert is_within_boundary("/tmp/lighthouse.db", "/data") is False
    assert is_within_boundary("/root/lighthouse.db", "/data") is False
    assert is_within_boundary("/var/lib/lighthouse/lighthouse.db", "/data") is False


def test_traversal_escape_rejected():
    assert is_within_boundary("/data/../tmp/lighthouse.db", "/data") is False


def test_relative_ambiguous_path_rejected():
    # A relative path cannot be proven under an absolute boundary.
    assert is_within_boundary("data/state.db", "/data") is False


def test_missing_path_is_deterministic():
    # Non-existent but under the boundary: still resolves deterministically as within.
    assert is_within_boundary("/data/does/not/exist.db", "/data") is True


def test_symlink_escape_rejected(tmp_path):
    boundary = tmp_path / "data"
    boundary.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.db").write_text("x")
    link = boundary / "link.db"
    link.symlink_to(outside / "secret.db")
    # The link appears to be under the boundary, but resolves outside it.
    assert is_within_boundary(link, boundary) is False


def test_resolve_path_normalizes():
    assert resolve_path("/data/../data/x") == resolve_path("/data/x")


# -- state classification ----------------------------------------------------------------


def test_authoritative_and_reconstructable_classified():
    assert STATE_CATEGORIES["state_db"] == CATEGORY_AUTHORITATIVE
    assert STATE_CATEGORIES["entities_json"] == CATEGORY_AUTHORITATIVE
    assert STATE_CATEGORIES["patents_dir"] == CATEGORY_RECONSTRUCTABLE
    assert STATE_CATEGORIES["logs_dir"] == CATEGORY_RUNTIME_EPHEMERAL


# -- readiness state machine -------------------------------------------------------------


def _populate(data_dir):
    for name in ("state.db", "entities.json", "targets.json"):
        (data_dir / name).write_text("x")
    (data_dir / "archives").mkdir()


def test_complete_safe_inventory_is_ready(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _populate(dd)
    result = inspect_backup_readiness(
        boundary=str(tmp_path), data_dir=str(dd), db_path=str(dd / "state.db")
    )
    assert result["status"] == "READY"
    assert result["authoritative_state_complete"] is True


def test_external_authoritative_path_is_not_ready(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _populate(dd)
    result = inspect_backup_readiness(
        boundary=str(tmp_path), data_dir=str(dd), db_path="/tmp/escape.db"
    )
    assert result["status"] == "NOT_READY"
    assert any(c["check_id"] == "db_path_override" for c in result["checks"])


def test_legacy_repo_relative_db_divergence_is_not_ready(tmp_path, monkeypatch):
    """Orphaned data/state.db under CWD blocks when runtime DB moved elsewhere."""
    dd = tmp_path / "relocated"
    dd.mkdir()
    _populate(dd)
    legacy = tmp_path / "data"
    legacy.mkdir()
    (legacy / "state.db").write_bytes(b"sqlite-legacy-bytes")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", str(dd))
    monkeypatch.delenv("DB_PATH", raising=False)
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", dd / "state.db")

    result = inspect_backup_readiness(boundary=str(tmp_path))
    assert result["status"] == "NOT_READY"
    assert any(c["check_id"] == "data_path_divergence" for c in result["checks"])


def test_legacy_divergence_reported_with_explicit_data_dir(tmp_path, monkeypatch):
    """--data-dir must not disable divergence when db_path is still derived."""
    dd = tmp_path / "relocated"
    dd.mkdir()
    _populate(dd)
    legacy = tmp_path / "data"
    legacy.mkdir()
    (legacy / "state.db").write_bytes(b"sqlite-legacy-bytes")

    monkeypatch.chdir(tmp_path)
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", dd / "state.db")

    result = inspect_backup_readiness(
        boundary=str(tmp_path),
        data_dir=str(dd),
        db_path=None,
    )
    assert result["status"] == "NOT_READY"
    assert any(c["check_id"] == "data_path_divergence" for c in result["checks"])


def test_explicit_db_path_suppresses_legacy_divergence_check(tmp_path, monkeypatch):
    """An explicitly supplied db_path must not run the derived-path divergence check."""
    dd = tmp_path / "relocated"
    dd.mkdir()
    _populate(dd)
    legacy = tmp_path / "data"
    legacy.mkdir()
    (legacy / "state.db").write_bytes(b"sqlite-legacy-bytes")

    monkeypatch.chdir(tmp_path)

    result = inspect_backup_readiness(
        boundary=str(tmp_path),
        data_dir=str(dd),
        db_path=str(dd / "state.db"),
    )
    assert not any(c["check_id"] == "data_path_divergence" for c in result["checks"])


def test_missing_authoritative_category_is_unknown(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    # Boundary is valid but nothing has been created yet -> pre-runtime UNKNOWN.
    result = inspect_backup_readiness(
        boundary=str(tmp_path), data_dir=str(dd), db_path=str(dd / "state.db")
    )
    assert result["status"] == "UNKNOWN"
    assert result["status"] != "READY"


def test_ambiguous_configuration_is_not_ready(tmp_path):
    # A relative data dir cannot be proven under the absolute boundary.
    result = inspect_backup_readiness(boundary="/data", data_dir="data", db_path="data/state.db")
    assert result["status"] == "NOT_READY"


def test_reconstructable_missing_does_not_block(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _populate(dd)  # only authoritative items; reconstructable dirs absent
    result = inspect_backup_readiness(
        boundary=str(tmp_path), data_dir=str(dd), db_path=str(dd / "state.db")
    )
    # Missing reconstructable module dirs must not prevent READY.
    assert result["status"] == "READY"


def test_unknown_never_treated_as_ready(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    result = inspect_backup_readiness(
        boundary=str(tmp_path), data_dir=str(dd), db_path=str(dd / "state.db")
    )
    assert result["status"] == "UNKNOWN"
    assert result["backup_performed"] is False


def test_inspection_performs_no_mutation(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _populate(dd)
    before = sorted(p.name for p in dd.iterdir())
    inspect_backup_readiness(boundary=str(tmp_path), data_dir=str(dd), db_path=str(dd / "state.db"))
    after = sorted(p.name for p in dd.iterdir())
    assert before == after


def test_ready_does_not_assert_backup_performed(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _populate(dd)
    result = inspect_backup_readiness(
        boundary=str(tmp_path), data_dir=str(dd), db_path=str(dd / "state.db")
    )
    assert result["status"] == "READY"
    assert result["backup_performed"] is False
    assert "does NOT assert" in result["note"]
