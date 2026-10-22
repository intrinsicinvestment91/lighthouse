"""LHP-V1-PROFILE-01C/01E — backup-gated initial RCD activation.

Closed initial activation only: no_active_profile → repository_capability_discovery v2.
No switch, no Gateway, no default-active, no env bypass. Ceremony hardening (01E) requires
a verified backup root, closed contract validation, and durable evidence in active state.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from modules import db
from modules.active_profile_state import (
    ACTIVATION_SOURCE_LOCAL_CLI,
    STATE_ACTIVE,
    STATE_NO_ACTIVE_PROFILE,
    build_active_profile_state,
)
from modules.backup_create import create_backup
from modules.backup_verify import (
    ReportValidationError,
    canonical_report_digest,
    restore_drill,
    validate_supplied_restore_drill_report,
    verify_backup,
)
from modules.profile_registry import get_profile
from modules.profile_transition import (
    build_transition_preview,
    load_evidence,
    profile_content_sha256,
)
from tests.unit.active_profile_test_helpers import (
    ACTIVATED_AT,
    CONFIRMATION,
    RCD_DIGEST,
    RCD_ID,
    RCD_VERSION,
    SYNTHETIC_EVIDENCE,
    build_rcd_active_state,
    rcd_profile,
)
from tests.unit.test_backup_create import _populate

# -- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    dd = tmp_path / "data"
    _populate(dd)
    return dd


def _create_evidence_chain(tmp_path: Path, data_dir: Path) -> dict[str, Path]:
    """Real verified backup + restore drill + ready transition preview for RCD v2."""
    backup = tmp_path / "backup"
    create_backup(
        data_dir=data_dir,
        destination=backup,
        db_path=data_dir / "state.db",
        boundary=data_dir,
        manifest_id="lhbackup-rcd-01c",
        created_at="2026-08-30T00:00:00Z",
    )
    manifest = backup / "manifest.json"
    # Prefer the independent verify report (status "verified") for the strongest surface.
    verification_report = tmp_path / "verification.json"
    verification_report.write_text(json.dumps(verify_backup(backup), indent=2), encoding="utf-8")
    restore_dest = tmp_path / "restore"
    restore_report_path = tmp_path / "restore.json"
    restore_report_path.write_text(
        json.dumps(
            restore_drill(backup, restore_dest, source_data_root=data_dir),
            indent=2,
        ),
        encoding="utf-8",
    )
    evidence = load_evidence(manifest, verification_report, restore_report_path)
    preview = build_transition_preview(rcd_profile(), evidence=evidence)
    assert preview.transition_status == "ready_for_review"
    preview_path = tmp_path / "preview.json"
    preview_path.write_text(json.dumps(preview.to_dict(), indent=2), encoding="utf-8")
    return {
        "manifest": manifest,
        "verification": verification_report,
        "restore": restore_report_path,
        "preview": preview_path,
        "backup": backup,
        "data_dir": data_dir,
    }


@pytest.fixture
def evidence(tmp_path: Path, data_dir: Path) -> dict[str, Path]:
    return _create_evidence_chain(tmp_path, data_dir)


# -- vocabulary ----------------------------------------------------------------------------


def test_local_cli_confirmation_is_in_the_activation_source_vocabulary():
    from modules import active_profile_state

    assert ACTIVATION_SOURCE_LOCAL_CLI in active_profile_state.ALLOWED_ACTIVATION_SOURCES
    assert "operator_approved_transition" in active_profile_state.ALLOWED_ACTIVATION_SOURCES


def test_rcd_v2_digest_is_the_activation_pin():
    assert profile_content_sha256(rcd_profile()) == RCD_DIGEST


# -- store: missing / corrupt / singleton --------------------------------------------------


def test_read_missing_database_returns_no_active_profile_and_creates_nothing(tmp_path, monkeypatch):
    from modules import active_profile_store

    path = tmp_path / "missing.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    state = active_profile_store.read_active_profile_state()
    assert state.activation_state == STATE_NO_ACTIVE_PROFILE
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_read_existing_db_without_table_returns_no_active_and_creates_no_table(store):
    from modules import active_profile_store

    db.init_alerts_table()
    assert store.exists()
    before = set(
        r[0]
        for r in sqlite3.connect(store).execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    state = active_profile_store.read_active_profile_state()
    assert state.activation_state == STATE_NO_ACTIVE_PROFILE
    after = set(
        r[0]
        for r in sqlite3.connect(store).execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    assert after == before
    assert "active_profile_state" not in after


def test_persist_creates_singleton_row(store):
    from modules import active_profile_store

    state = build_rcd_active_state()
    result = active_profile_store._persist_initial_activation(state)
    assert result.activation_state == STATE_ACTIVE
    assert result.active_profile_id == RCD_ID
    assert result.active_profile_sha256 == RCD_DIGEST
    read = active_profile_store.read_active_profile_state()
    assert read.to_dict() == result.to_dict()


def test_idempotent_reactivation_does_not_rewrite_time_or_evidence(store):
    from modules import active_profile_store

    first = active_profile_store._persist_initial_activation(
        build_rcd_active_state(activated_at="2026-08-01T00:00:00+00:00")
    )
    second = active_profile_store._persist_initial_activation(
        build_rcd_active_state(activated_at="2026-08-30T23:00:00+00:00")
    )
    assert second.activated_at == first.activated_at
    assert second.artifact_digest == first.artifact_digest
    rows = sqlite3.connect(store).execute("SELECT COUNT(*) FROM active_profile_state").fetchone()
    assert rows[0] == 1


def test_different_active_profile_refuses_as_unsupported_switching(store):
    from modules import active_profile_store
    from modules.active_profile_store import UnsupportedProfileSwitchError

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    other = get_profile("scientific_preservation")
    alien = build_active_profile_state(
        other,
        activated_at=ACTIVATED_AT,
        activation_source=ACTIVATION_SOURCE_LOCAL_CLI,
        evidence=SYNTHETIC_EVIDENCE,
    )
    with pytest.raises(UnsupportedProfileSwitchError):
        active_profile_store._persist_initial_activation(alien)


def test_corrupt_state_fails_closed(store):
    from modules import active_profile_store
    from modules.active_profile_store import CorruptActiveProfileStateError

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    conn = sqlite3.connect(store)
    conn.execute(
        "UPDATE active_profile_state SET state_json = ?",
        ('{"activation_state":"active","tampered":true}',),
    )
    conn.commit()
    conn.close()
    with pytest.raises(CorruptActiveProfileStateError):
        active_profile_store.read_active_profile_state()


def test_multiple_rows_fail_closed(store):
    from modules import active_profile_store
    from modules.active_profile_store import CorruptActiveProfileStateError

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    conn = sqlite3.connect(store)
    # Bypass the CHECK(id=1) singleton by recreating without the constraint if needed —
    # insert a second row the store must refuse.
    row = conn.execute("SELECT state_json, artifact_digest FROM active_profile_state").fetchone()
    try:
        conn.execute(
            "INSERT INTO active_profile_state (id, state_json, artifact_digest) VALUES (2, ?, ?)",
            (row[0], "0" * 64),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # If the CHECK blocks id!=1, force a duplicate via a second table clone path:
        conn.execute("DROP TABLE active_profile_state")
        conn.execute(
            """
            CREATE TABLE active_profile_state (
                id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                artifact_digest TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO active_profile_state (id, state_json, artifact_digest) VALUES (1, ?, ?)",
            (row[0], row[1]),
        )
        conn.execute(
            "INSERT INTO active_profile_state (id, state_json, artifact_digest) VALUES (2, ?, ?)",
            (row[0], "f" * 64),
        )
        conn.commit()
    conn.close()
    with pytest.raises(CorruptActiveProfileStateError):
        active_profile_store.read_active_profile_state()


def test_concurrent_activation_attempts_yield_one_active_state(store):
    from modules import active_profile_store

    barrier = threading.Barrier(8)
    results: list = []
    errors: list = []

    def attempt():
        try:
            barrier.wait(timeout=5)
            results.append(
                active_profile_store._persist_initial_activation(build_rcd_active_state())
            )
        except Exception as exc:  # noqa: BLE001 — collect for assertion
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(attempt) for _ in range(8)]
        for f in futures:
            f.result(timeout=30)

    assert not errors
    assert len(results) == 8
    digests = {r.artifact_digest for r in results}
    assert len(digests) == 1
    rows = sqlite3.connect(store).execute("SELECT COUNT(*) FROM active_profile_state").fetchone()
    assert rows[0] == 1


def test_transaction_interruption_rolls_back(store, monkeypatch):
    from modules import active_profile_store
    from modules.active_profile_store import ActiveProfileStoreError

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated interruption")

    monkeypatch.setattr(db, "_activate_active_profile_atomic", boom)
    with pytest.raises((ActiveProfileStoreError, sqlite3.OperationalError)):
        active_profile_store._persist_initial_activation(build_rcd_active_state())
    if store.exists():
        tables = {
            r[0]
            for r in sqlite3.connect(store).execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "active_profile_state" in tables:
            count = (
                sqlite3.connect(store)
                .execute("SELECT COUNT(*) FROM active_profile_state")
                .fetchone()[0]
            )
            assert count == 0
    state = active_profile_store.read_active_profile_state()
    assert state.activation_state == STATE_NO_ACTIVE_PROFILE


# -- activation service --------------------------------------------------------------------


def test_successful_backup_gated_rcd_activation(store, evidence):
    from modules import active_profile_store, profile_activation

    result = profile_activation.activate_repository_capability_discovery(
        confirmation=CONFIRMATION,
        transition_preview=evidence["preview"],
        backup_root=evidence["backup"],
        backup_manifest=evidence["manifest"],
        verification_report=evidence["verification"],
        restore_report=evidence["restore"],
        data_dir=evidence["data_dir"],
        dry_run=False,
        now=ACTIVATED_AT,
    )
    assert result["status"] == "activated"
    assert result["active_profile_id"] == RCD_ID
    assert result["active_profile_version"] == RCD_VERSION
    assert result["active_profile_sha256"] == RCD_DIGEST
    assert result["activation_source"] == ACTIVATION_SOURCE_LOCAL_CLI
    for pin in profile_activation.AUTHORITY_PINS_FALSE:
        assert result[pin] is False
    state = active_profile_store.read_active_profile_state()
    assert state.activation_state == STATE_ACTIVE
    assert state.transition_preview_digest == result["transition_preview_digest"]
    assert state.backup_manifest_id == result["backup_manifest_id"]
    assert state.backup_root == str(evidence["backup"].resolve())
    assert state.source_data_root == str(evidence["data_dir"].resolve())


def test_dry_run_creates_no_db_table_or_state(tmp_path, monkeypatch, evidence):
    from modules import profile_activation

    path = tmp_path / "never.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    result = profile_activation.activate_repository_capability_discovery(
        confirmation=CONFIRMATION,
        transition_preview=evidence["preview"],
        backup_root=evidence["backup"],
        backup_manifest=evidence["manifest"],
        verification_report=evidence["verification"],
        restore_report=evidence["restore"],
        data_dir=evidence["data_dir"],
        dry_run=True,
        now=ACTIVATED_AT,
    )
    assert result["status"] == "previewed"
    assert not path.exists()


def test_missing_confirmation_refuses(store, evidence):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    with pytest.raises(ActivationRefused, match="confirmation"):
        profile_activation.activate_repository_capability_discovery(
            confirmation="",
            transition_preview=evidence["preview"],
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=evidence["restore"],
            data_dir=evidence["data_dir"],
        )


def test_wrong_confirmation_refuses(store, evidence):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    with pytest.raises(ActivationRefused, match="confirmation"):
        profile_activation.activate_repository_capability_discovery(
            confirmation="please activate",
            transition_preview=evidence["preview"],
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=evidence["restore"],
            data_dir=evidence["data_dir"],
        )


def test_invalid_target_in_preview_refuses(store, evidence, tmp_path, data_dir):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    # Build a ready preview for a different profile, then try to activate RCD with it.
    other = get_profile("scientific_preservation")
    ev = load_evidence(evidence["manifest"], evidence["verification"], evidence["restore"])
    preview = build_transition_preview(other, evidence=ev)
    path = tmp_path / "other-preview.json"
    path.write_text(json.dumps(preview.to_dict()), encoding="utf-8")
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            confirmation=CONFIRMATION,
            transition_preview=path,
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=evidence["restore"],
            data_dir=data_dir,
        )


def test_readiness_only_evidence_refuses(store, tmp_path, data_dir):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"status": "READY", "result": "READY"}), encoding="utf-8")
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            confirmation=CONFIRMATION,
            transition_preview=readiness,
            backup_root=tmp_path / "missing-backup",
            backup_manifest=readiness,
            verification_report=readiness,
            restore_report=readiness,
            data_dir=data_dir,
        )


def test_failed_restore_evidence_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    bad_restore = tmp_path / "bad-restore.json"
    bad_restore.write_text(json.dumps({"status": "failed", "manifest_id": "x"}), encoding="utf-8")
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            confirmation=CONFIRMATION,
            transition_preview=evidence["preview"],
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=bad_restore,
            data_dir=evidence["data_dir"],
        )


def test_forged_manifest_digest_in_preview_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    preview = json.loads(evidence["preview"].read_text(encoding="utf-8"))
    preview["evidence"]["backup_manifest_sha256"] = "a" * 64
    # Re-stamp would still fail content binding against the real file.
    forged = tmp_path / "forged-preview.json"
    forged.write_text(json.dumps(preview), encoding="utf-8")
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            confirmation=CONFIRMATION,
            transition_preview=forged,
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=evidence["restore"],
            data_dir=evidence["data_dir"],
        )


def test_data_dir_mismatch_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    other = tmp_path / "other-data"
    _populate(other)
    with pytest.raises(ActivationRefused, match="data"):
        profile_activation.activate_repository_capability_discovery(
            confirmation=CONFIRMATION,
            transition_preview=evidence["preview"],
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=evidence["restore"],
            data_dir=other,
        )


def test_idempotent_activation_via_service(store, evidence):
    from modules import profile_activation

    first = profile_activation.activate_repository_capability_discovery(
        confirmation=CONFIRMATION,
        transition_preview=evidence["preview"],
        backup_root=evidence["backup"],
        backup_manifest=evidence["manifest"],
        verification_report=evidence["verification"],
        restore_report=evidence["restore"],
        data_dir=evidence["data_dir"],
        now="2026-08-01T00:00:00+00:00",
    )
    second = profile_activation.activate_repository_capability_discovery(
        confirmation=CONFIRMATION,
        transition_preview=evidence["preview"],
        backup_root=evidence["backup"],
        backup_manifest=evidence["manifest"],
        verification_report=evidence["verification"],
        restore_report=evidence["restore"],
        data_dir=evidence["data_dir"],
        now="2026-08-30T00:00:00+00:00",
    )
    assert first["activated_at"] == second["activated_at"]
    assert first["artifact_digest"] == second["artifact_digest"]
    assert second["status"] == "already_active"


def test_other_active_state_refuses_switch_via_service(store, evidence):
    from modules import active_profile_store, profile_activation
    from modules.profile_activation import ActivationRefused

    # Plant a non-RCD active state directly (simulating an impossible future state).
    other = get_profile("scientific_preservation")
    alien = build_active_profile_state(
        other,
        activated_at=ACTIVATED_AT,
        activation_source=ACTIVATION_SOURCE_LOCAL_CLI,
        evidence=SYNTHETIC_EVIDENCE,
    )
    # Force-write via raw SQL after creating table with a one-off path
    active_profile_store._persist_initial_activation(build_rcd_active_state())
    # Now overwrite the row to a different profile to simulate foreign active state
    conn = sqlite3.connect(store)
    conn.execute(
        "UPDATE active_profile_state SET state_json = ?, artifact_digest = ?",
        (json.dumps(alien.to_dict()), alien.artifact_digest),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ActivationRefused, match="switch|active"):
        profile_activation.activate_repository_capability_discovery(
            confirmation=CONFIRMATION,
            transition_preview=evidence["preview"],
            backup_root=evidence["backup"],
            backup_manifest=evidence["manifest"],
            verification_report=evidence["verification"],
            restore_report=evidence["restore"],
            data_dir=evidence["data_dir"],
        )


# -- intake enforcement --------------------------------------------------------------------


def test_intake_refuses_without_active_profile(store, tmp_path):
    from modules.observation_intake import ObservationIntakeError, run_observation_intake
    from modules.observation_sources import ObservationSource
    from tests.unit.test_repository_intake import make_repo

    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = make_repo(workspace / "r", {"a.txt": "x\n"})
    with pytest.raises(ObservationIntakeError, match="active profile"):
        run_observation_intake(
            sources=[ObservationSource(type="repository", path=str(repo))],
            allowed_roots=[str(workspace)],
        )


def test_intake_stamps_null_profile_with_active_rcd(store, tmp_path):
    from modules import active_profile_store
    from modules.observation_intake import run_observation_intake
    from modules.observation_sources import ObservationSource
    from tests.unit.test_repository_intake import make_repo

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = make_repo(workspace / "r", {"a.txt": "x\n"})
    run = run_observation_intake(
        sources=[ObservationSource(type="repository", path=str(repo), profile_id=None)],
        allowed_roots=[str(workspace)],
    )
    assert run.count("stored") == 1
    row = (
        sqlite3.connect(store)
        .execute("SELECT artifact_json FROM observation_sets LIMIT 1")
        .fetchone()
    )
    artifact = json.loads(row[0])
    assert artifact["profile_id"] == RCD_ID


def test_intake_admits_matching_rcd_profile(store, tmp_path):
    from modules import active_profile_store
    from modules.observation_intake import run_observation_intake
    from modules.observation_sources import ObservationSource
    from tests.unit.test_repository_intake import make_repo

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = make_repo(workspace / "r", {"a.txt": "x\n"})
    run = run_observation_intake(
        sources=[ObservationSource(type="repository", path=str(repo), profile_id=RCD_ID)],
        allowed_roots=[str(workspace)],
    )
    assert run.count("stored") == 1


def test_intake_refuses_mismatched_profile(store, tmp_path):
    from modules import active_profile_store
    from modules.observation_intake import run_observation_intake
    from modules.observation_sources import ObservationSource
    from tests.unit.test_repository_intake import make_repo

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = make_repo(workspace / "r", {"a.txt": "x\n"})
    run = run_observation_intake(
        sources=[
            ObservationSource(
                type="repository",
                path=str(repo),
                profile_id="scientific_preservation",
            )
        ],
        allowed_roots=[str(workspace)],
    )
    assert run.count("failed") == 1
    assert run.outcomes[0].reason == "profile_not_admitted"


def test_intake_refuses_document_under_rcd(store, tmp_path):
    from modules import active_profile_store
    from modules.observation_intake import run_observation_intake
    from modules.observation_sources import ObservationSource

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    doc = workspace / "spec.md"
    doc.write_text("# spec\n", encoding="utf-8")
    run = run_observation_intake(
        sources=[ObservationSource(type="document", path=str(doc))],
        allowed_roots=[str(workspace)],
    )
    assert run.count("failed") == 1
    assert run.outcomes[0].reason == "source_type_not_admitted"


# -- detection enforcement -----------------------------------------------------------------


def test_detection_refuses_without_active_profile(store):
    from modules.pattern_candidate_service import PatternServiceError, run_detection

    with pytest.raises(PatternServiceError, match="active profile"):
        run_detection(dry_run=True)


def test_rcd_detection_admits_under_active_rcd(store, tmp_path):
    from modules import active_profile_store
    from modules.observation_intake import run_observation_intake
    from modules.observation_sources import ObservationSource
    from modules.pattern_candidate_service import run_detection
    from tests.unit.test_repository_intake import make_repo

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    content = "FROM scratch\n"
    alpha = make_repo(workspace / "a", {"Dockerfile": content})
    beta = make_repo(workspace / "b", {"Dockerfile": content})
    run_observation_intake(
        sources=[
            ObservationSource(type="repository", path=str(alpha)),
            ObservationSource(type="repository", path=str(beta)),
        ],
        allowed_roots=[str(workspace)],
    )
    result = run_detection()
    assert result.to_dict()["profile_activated"] is True
    assert result.to_dict()["profile_id"] == RCD_ID
    assert result.candidate_count >= 1 if hasattr(result, "candidate_count") else True
    assert len(result.candidates) >= 1


def test_scientific_detection_refuses_under_active_rcd(store):
    from modules import active_profile_store
    from modules.pattern_candidate_service import PatternServiceError, run_detection

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    with pytest.raises(PatternServiceError, match="not admitted|active profile|ownership"):
        run_detection(detector="scientific_source_disappearance_review", dry_run=True)


def test_historical_reads_and_review_remain_without_retagging(store, tmp_path):
    """Facts and candidates written under active RCD remain readable; review still works."""
    from modules import active_profile_store
    from modules.observation_intake import run_observation_intake
    from modules.observation_query import list_observations
    from modules.observation_sources import ObservationSource
    from modules.pattern_candidate_service import (
        list_candidates,
        record_disposition,
        run_detection,
        show_candidate,
    )
    from tests.unit.test_repository_intake import make_repo

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    content = "shared-bytes\n"
    a = make_repo(workspace / "a", {"x.txt": content})
    b = make_repo(workspace / "b", {"x.txt": content})
    run_observation_intake(
        sources=[
            ObservationSource(type="repository", path=str(a)),
            ObservationSource(type="repository", path=str(b)),
        ],
        allowed_roots=[str(workspace)],
    )
    det = run_detection()
    assert list_observations(limit=10)["count"] >= 1
    listed = list_candidates()
    assert listed["count"] >= 1
    cid = det.candidates[0].candidate_id
    shown = show_candidate(cid)
    assert shown["candidate"]["candidate_id"] == cid
    record_disposition(
        candidate_id=cid,
        decision="needs_more_evidence",
        operator_id="operator-test",
        reason="lineage unknown",
    )


def test_activation_imports_no_network_or_gateway(tmp_path):
    import ast
    from pathlib import Path

    for name in ("active_profile_store.py", "profile_activation.py"):
        tree = ast.parse((Path("modules") / name).read_text(encoding="utf-8"), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "gateway" not in alias.name.lower()
                    assert alias.name not in {"httpx", "requests", "urllib", "socket"}
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "gateway" not in node.module.lower()
                assert node.module.split(".")[0] not in {
                    "httpx",
                    "requests",
                    "urllib",
                    "socket",
                    "http",
                }


# -- LHP-V1-PROFILE-01E ceremony hardening -----------------------------------------------


def _activate_kwargs(evidence: dict[str, Path], **overrides):
    base = {
        "confirmation": CONFIRMATION,
        "transition_preview": evidence["preview"],
        "backup_root": evidence["backup"],
        "backup_manifest": evidence["manifest"],
        "verification_report": evidence["verification"],
        "restore_report": evidence["restore"],
        "data_dir": evidence["data_dir"],
    }
    base.update(overrides)
    return base


def test_missing_backup_root_refuses(store, evidence):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    with pytest.raises(ActivationRefused, match="backup_root"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, backup_root=None)
        )


def test_absent_backup_directory_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    missing = tmp_path / "no-such-backup"
    with pytest.raises(ActivationRefused, match="not a directory|backup_root"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, backup_root=missing)
        )


def test_profile_activate_refuses_backup_create_passed_report(store, evidence):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    passed_report = evidence["backup"] / "reports" / "verification.json"
    assert passed_report.is_file()
    embedded = json.loads(passed_report.read_text(encoding="utf-8"))
    assert embedded["status"] == "passed"

    with pytest.raises(ActivationRefused, match="backup-verify") as exc:
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, verification_report=passed_report)
        )
    message = str(exc.value)
    assert "passed" in message
    assert "verified" in message
    assert "missing required keys" not in message


def test_status_only_forged_verification_report_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    forged = tmp_path / "forged-verify.json"
    forged.write_text(
        json.dumps({"status": "verified", "manifest_id": "fake", "backup_root": "/tmp/x"}),
        encoding="utf-8",
    )
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, verification_report=forged)
        )


def test_status_only_forged_restore_report_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    forged = tmp_path / "forged-restore.json"
    forged.write_text(json.dumps({"status": "completed", "manifest_id": "fake"}), encoding="utf-8")
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, restore_report=forged)
        )


def test_manifest_without_payload_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    hollow = tmp_path / "hollow-backup"
    hollow.mkdir()
    (hollow / "manifest.json").write_text(
        evidence["manifest"].read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ActivationRefused, match="payload"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(
                evidence, backup_root=hollow, backup_manifest=hollow / "manifest.json"
            )
        )


def test_malformed_manifest_extra_field_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    bad = json.loads(evidence["manifest"].read_text(encoding="utf-8"))
    bad["importance"] = "high"
    bad_manifest = tmp_path / "bad-manifest.json"
    bad_manifest.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ActivationRefused, match="manifest"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, backup_manifest=bad_manifest)
        )


def test_mismatched_report_backup_root_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    report = json.loads(evidence["verification"].read_text(encoding="utf-8"))
    report["backup_root"] = str(tmp_path / "other-backup")
    body = {k: v for k, v in report.items() if k != "artifact_digest"}
    report["artifact_digest"] = canonical_report_digest(body)
    other = tmp_path / "mismatch-verify.json"
    other.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, verification_report=other)
        )


def test_verification_report_from_other_backup_same_manifest_id_refuses(
    store, evidence, tmp_path, data_dir
):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    other = tmp_path / "other-data"
    _populate(other)
    (other / "entities.json").write_text('{"entities": [{"other": true}]}\n', encoding="utf-8")
    other_backup = tmp_path / "other-backup"
    create_backup(
        data_dir=other,
        destination=other_backup,
        db_path=other / "state.db",
        boundary=other,
        manifest_id="lhbackup-rcd-01c",
        created_at="2026-08-30T00:00:00Z",
    )
    other_verify = tmp_path / "other-verify.json"
    other_verify.write_text(json.dumps(verify_backup(other_backup)), encoding="utf-8")
    with pytest.raises(ActivationRefused, match="manifest"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, verification_report=other_verify)
        )


def test_restore_report_from_other_backup_same_manifest_id_refuses(
    store, evidence, tmp_path, data_dir
):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    other = tmp_path / "other-data"
    _populate(other)
    (other / "entities.json").write_text('{"entities": [{"other": true}]}\n', encoding="utf-8")
    other_backup = tmp_path / "other-backup"
    create_backup(
        data_dir=other,
        destination=other_backup,
        db_path=other / "state.db",
        boundary=other,
        manifest_id="lhbackup-rcd-01c",
        created_at="2026-08-30T00:00:00Z",
    )
    other_restore = tmp_path / "other-restore.json"
    other_restore.write_text(
        json.dumps(restore_drill(other_backup, tmp_path / "other-restore", source_data_root=other)),
        encoding="utf-8",
    )
    with pytest.raises(ActivationRefused, match="manifest"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, restore_report=other_restore)
        )


def test_substituted_manifest_digest_refuses(store, evidence, tmp_path, data_dir):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    other = tmp_path / "other-data"
    _populate(other)
    other_backup = tmp_path / "other-backup"
    create_backup(
        data_dir=other,
        destination=other_backup,
        db_path=other / "state.db",
        boundary=other,
        manifest_id="lhbackup-other",
        created_at="2026-08-30T00:00:00Z",
    )
    with pytest.raises(ActivationRefused):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(
                evidence,
                backup_root=other_backup,
                backup_manifest=other_backup / "manifest.json",
            )
        )


def test_backup_changed_after_report_creation_refuses(store, evidence):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    payload_file = evidence["backup"] / "payload" / "entities.json"
    payload_file.write_text('{"entities": [{"tampered": true}]}\n', encoding="utf-8")
    with pytest.raises(ActivationRefused, match="re-verification|verification|mismatch|failed"):
        profile_activation.activate_repository_capability_discovery(**_activate_kwargs(evidence))


def test_dry_run_runs_unconditional_backup_verification(store, evidence, monkeypatch):
    from modules import profile_activation

    calls: list = []

    def spy(root):
        calls.append(root)
        from modules.backup_verify import verify_backup

        return verify_backup(root)

    monkeypatch.setattr(profile_activation, "verify_backup", spy)
    result = profile_activation.activate_repository_capability_discovery(
        **_activate_kwargs(evidence, dry_run=True, now=ACTIVATED_AT)
    )
    assert result["status"] == "previewed"
    assert len(calls) == 1
    assert calls[0] == evidence["backup"].resolve()


def test_corrupt_persisted_evidence_refuses_on_read(store):
    from modules import active_profile_store
    from modules.active_profile_store import CorruptActiveProfileStateError

    active_profile_store._persist_initial_activation(build_rcd_active_state())
    conn = sqlite3.connect(store)
    row = conn.execute("SELECT state_json FROM active_profile_state").fetchone()
    data = json.loads(row[0])
    data["backup_root"] = None
    data["artifact_digest"] = "0" * 64
    conn.execute("UPDATE active_profile_state SET state_json = ?", (json.dumps(data),))
    conn.commit()
    conn.close()
    with pytest.raises(CorruptActiveProfileStateError):
        active_profile_store.read_active_profile_state()


def test_idempotent_activation_preserves_original_evidence(store, evidence):
    from modules import profile_activation

    first = profile_activation.activate_repository_capability_discovery(
        **_activate_kwargs(evidence, now="2026-08-01T00:00:00+00:00")
    )
    second = profile_activation.activate_repository_capability_discovery(
        **_activate_kwargs(evidence, now="2026-08-30T00:00:00+00:00")
    )
    assert first["backup_manifest_sha256"] == second["backup_manifest_sha256"]
    assert first["verification_report_digest"] == second["verification_report_digest"]
    assert first["restore_report_digest"] == second["restore_report_digest"]
    assert second["status"] == "already_active"


def test_active_profile_cli_missing_state_creates_nothing(tmp_path, monkeypatch, capsys):
    import main

    path = tmp_path / "never.db"
    monkeypatch.setattr(main.db, "DB_PATH", path)
    args = type("Args", (), {"json": True})()
    main.handle_active_profile_command(args)
    captured = capsys.readouterr()
    assert '"activation_state": "no_active_profile"' in captured.out
    assert not path.exists()


def test_store_writer_is_not_public_production_surface():
    from modules import active_profile_store

    assert "persist_initial_activation" not in active_profile_store.__all__
    assert hasattr(active_profile_store, "_persist_initial_activation")


def test_active_profile_state_schema_mirror_pins_contract():
    from pathlib import Path

    from modules import active_profile_state as aps

    mirror = json.loads(
        Path("docs/schemas/active_profile_state.v1.schema.json").read_text(encoding="utf-8")
    )
    assert set(mirror["required"]) == aps._EXPECTED_KEYS
    assert set(mirror["properties"]) == aps._EXPECTED_KEYS
    assert mirror["additionalProperties"] is False


def test_real_active_state_validates_against_schema_mirror():
    jsonschema = pytest.importorskip("jsonschema")
    from pathlib import Path

    schema = json.loads(
        Path("docs/schemas/active_profile_state.v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(build_rcd_active_state().to_dict(), schema)
    jsonschema.validate(
        __import__("modules.active_profile_state", fromlist=["no_active_profile_state"])
        .no_active_profile_state()
        .to_dict(),
        schema,
    )


# -- LHP-V1-PROFILE-01G restore-drill completion binding ---------------------------------


def _restore_report_binding(tmp_path: Path, data_dir: Path) -> dict:
    chain = _create_evidence_chain(tmp_path, data_dir)
    report = json.loads(chain["restore"].read_text(encoding="utf-8"))
    manifest = json.loads(chain["manifest"].read_text(encoding="utf-8"))
    return {
        "data": report,
        "backup_root": chain["backup"],
        "manifest_id": manifest["manifest_id"],
        "source_data_root": data_dir,
    }


def test_validate_supplied_restore_drill_report_accepts_completed_drill(tmp_path, data_dir):
    binding = _restore_report_binding(tmp_path, data_dir)
    assert binding["data"]["source_unchanged"] is True
    assert binding["data"]["files_restored"] > 0
    validate_supplied_restore_drill_report(
        binding["data"],
        backup_root=binding["backup_root"],
        manifest_id=binding["manifest_id"],
        source_data_root=binding["source_data_root"],
    )


_RESTORE_DRILL_STRICT_TRUE_FIELDS = (
    "manifest_valid",
    "payload_verified",
    "restored_digests_verified",
    "sqlite_integrity_verified",
    "source_unchanged",
)

_RESTORE_DRILL_NON_TRUE_VALUES = (
    pytest.param(False, id="false"),
    pytest.param(1, id="int_one"),
    pytest.param(1.0, id="float_one"),
    pytest.param("true", id="string_true"),
    pytest.param([], id="empty_list"),
    pytest.param([True], id="truthy_list"),
    pytest.param({}, id="empty_object"),
    pytest.param({"value": True}, id="truthy_object"),
)


@pytest.mark.parametrize("field", _RESTORE_DRILL_STRICT_TRUE_FIELDS)
@pytest.mark.parametrize("bad_value", _RESTORE_DRILL_NON_TRUE_VALUES)
def test_validate_supplied_restore_drill_report_refuses_non_literal_true_booleans(
    tmp_path, data_dir, field, bad_value
):
    binding = _restore_report_binding(tmp_path, data_dir)
    report = copy.deepcopy(binding["data"])
    report[field] = bad_value
    body = {k: v for k, v in report.items() if k != "artifact_digest"}
    report["artifact_digest"] = canonical_report_digest(body)
    with pytest.raises(ReportValidationError, match=field):
        validate_supplied_restore_drill_report(
            report,
            backup_root=binding["backup_root"],
            manifest_id=binding["manifest_id"],
            source_data_root=binding["source_data_root"],
        )


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda r: r.update({"files_restored": 0}), "files_restored"),
        (lambda r: r.update({"files_restored": -1}), "files_restored"),
        (lambda r: r.update({"files_restored": True}), "files_restored"),
        (lambda r: r.update({"files_restored": "2"}), "files_restored"),
    ],
)
def test_validate_supplied_restore_drill_report_refuses_incomplete_drill(
    tmp_path, data_dir, mutator, match
):
    binding = _restore_report_binding(tmp_path, data_dir)
    report = copy.deepcopy(binding["data"])
    mutator(report)
    body = {k: v for k, v in report.items() if k != "artifact_digest"}
    report["artifact_digest"] = canonical_report_digest(body)
    with pytest.raises(ReportValidationError, match=match):
        validate_supplied_restore_drill_report(
            report,
            backup_root=binding["backup_root"],
            manifest_id=binding["manifest_id"],
            source_data_root=binding["source_data_root"],
        )


# -- LHP-V1-PROFILE-01F preview self-integrity ---------------------------------------------
# Activation must refuse a transition preview whose artifact_digest does not match its own
# canonical content (ProfileTransitionPreview.compute_digest), before any other binding.


def _tampered_preview(evidence, tmp_path: Path, mutate, *, restamp: bool = False) -> Path:
    from modules.profile_transition import ProfileTransitionPreview

    data = json.loads(evidence["preview"].read_text(encoding="utf-8"))
    mutate(data)
    if restamp:
        data["artifact_digest"] = ProfileTransitionPreview.from_dict(data).compute_digest()
    path = tmp_path / "tampered-preview.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _inject_warning(data):
    data["warnings"] = [*data["warnings"], "operator-invisible note"]


def _flip_privacy_local_only(data):
    assert data["changes"]["privacy_policy_changed"]["to"]["local_only"] is True
    data["changes"]["privacy_policy_changed"]["to"]["local_only"] = False


def _drop_security_condition(data):
    data["requirements"]["required_security_conditions"] = data["requirements"][
        "required_security_conditions"
    ][1:]


def _replace_digest(value):
    def mutate(data):
        assert data["artifact_digest"] != value
        data["artifact_digest"] = value

    return mutate


def _remove_digest(data):
    del data["artifact_digest"]


def test_intact_preview_digest_is_self_consistent(evidence):
    from modules.profile_activation import _load_preview

    preview = _load_preview(evidence["preview"])
    assert preview.artifact_digest == preview.compute_digest()


@pytest.mark.parametrize("dry_run", [True, False])
def test_intact_preview_is_accepted_in_both_modes(store, evidence, dry_run):
    from modules import profile_activation
    from modules.profile_activation import _load_preview

    result = profile_activation.activate_repository_capability_discovery(
        **_activate_kwargs(evidence, dry_run=dry_run, now=ACTIVATED_AT)
    )
    assert result["status"] == ("previewed" if dry_run else "activated")
    assert result["transition_preview_digest"] == _load_preview(evidence["preview"]).artifact_digest


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize(
    "mutate",
    [
        _inject_warning,
        _flip_privacy_local_only,
        _drop_security_condition,
        _replace_digest("a" * 64),
        _replace_digest("0" * 64),
        _replace_digest(12345),
        _remove_digest,
    ],
    ids=[
        "warning_injected",
        "privacy_local_only_flipped",
        "security_condition_dropped",
        "digest_replaced_a",
        "digest_replaced_0",
        "digest_non_string",
        "digest_removed",
    ],
)
def test_stale_or_substituted_preview_digest_refuses(store, evidence, tmp_path, mutate, dry_run):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    tampered = _tampered_preview(evidence, tmp_path, mutate)
    with pytest.raises(ActivationRefused, match="artifact_digest does not match"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, transition_preview=tampered, dry_run=dry_run)
        )
    assert not store.exists()


def test_uppercase_preview_digest_refuses(store, evidence, tmp_path):
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    tampered = _tampered_preview(
        evidence, tmp_path, lambda d: d.update(artifact_digest=d["artifact_digest"].upper())
    )
    with pytest.raises(ActivationRefused, match="artifact_digest does not match"):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, transition_preview=tampered)
        )
    assert not store.exists()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.update(target_profile_sha256="b" * 64), "live RCD v2 digest"),
        (lambda d: d.update(target_profile_version=1), "target_profile_version"),
        (lambda d: d.update(transition_status="blocked"), "not ready_for_review"),
        (lambda d: d.update(current_profile_id=RCD_ID), "foundation preview"),
        (
            lambda d: d["evidence"].update(backup_manifest_sha256="c" * 64),
            "backup_manifest_sha256",
        ),
        (lambda d: d["evidence"].update(backup_manifest_id="lhbackup-other"), "manifest"),
        (lambda d: d["evidence"].update(restore_drill_status="failed"), "passing backup evidence"),
    ],
    ids=[
        "target_sha",
        "target_version",
        "status_blocked",
        "current_profile",
        "manifest_sha",
        "manifest_id",
        "restore_failed",
    ],
)
def test_restamped_binding_tamper_still_refuses(store, evidence, tmp_path, mutate, match):
    """A self-consistent (re-stamped) forgery passes the digest check but not the bindings."""
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused
    from modules.profile_transition import ProfileTransitionPreview

    tampered = _tampered_preview(evidence, tmp_path, mutate, restamp=True)
    reloaded = ProfileTransitionPreview.from_dict(json.loads(tampered.read_text(encoding="utf-8")))
    assert reloaded.artifact_digest == reloaded.compute_digest()
    with pytest.raises(ActivationRefused, match=match):
        profile_activation.activate_repository_capability_discovery(
            **_activate_kwargs(evidence, transition_preview=tampered)
        )
    assert not store.exists()


def test_cli_dry_run_refuses_stale_preview_digest(store, evidence, tmp_path, monkeypatch, capsys):
    import main

    tampered = _tampered_preview(evidence, tmp_path, _flip_privacy_local_only)
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "profile-activate",
            "--target-profile",
            RCD_ID,
            "--target-version",
            str(RCD_VERSION),
            "--confirm",
            CONFIRMATION,
            "--transition-preview",
            str(tampered),
            "--backup-root",
            str(evidence["backup"]),
            "--backup-manifest",
            str(evidence["manifest"]),
            "--verification-report",
            str(evidence["verification"]),
            "--restore-report",
            str(evidence["restore"]),
            "--data-dir",
            str(evidence["data_dir"]),
            "--dry-run",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()
    assert exc_info.value.code == 1
    assert "artifact_digest does not match" in capsys.readouterr().err
    assert not store.exists()
