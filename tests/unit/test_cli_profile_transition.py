"""Unit tests for the `profile-transition-preview` CLI command (LHP-PROFILE-03).

Read-only, deterministic, no activation. Covers help text, JSON/human output, nonzero exit on
an unavailable target, evidence wiring from LHP-PROFILE-02 reports, and the invariant that a
preview without passing evidence is never ready_for_review. All fixtures are temporary; the
command must never write DB state or contact the Gateway.
"""

import json
from pathlib import Path

import pytest

import main
from tests.unit.test_profile_transition import _write_typed_bound_reports


def _run(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _write_evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"manifest_id": "lhbackup-xyz", "created_at": "2026-07-10T00:00:00+00:00"})
    )
    verification, restore = _write_typed_bound_reports(tmp_path, manifest)
    return manifest, verification, restore


def test_help_lists_command(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["profile-transition-preview", "--help"])
    out = capsys.readouterr().out
    assert "profile-transition-preview" in out or "target-profile" in out


def test_help_names_backup_verify_vs_create_passed(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["profile-transition-preview", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "backup-verify" in out
    assert "profile-activate" in out
    assert "passed" in out or "verified" in out


def test_json_output_without_evidence_is_not_ready(monkeypatch, capsys):
    _run(
        monkeypatch,
        [
            "profile-transition-preview",
            "--target-profile",
            "scientific_preservation",
            "--json",
        ],
    )
    report = json.loads(capsys.readouterr().out)
    assert report["target_profile_id"] == "scientific_preservation"
    assert report["transition_status"] != "ready_for_review"


def test_human_output_runs(monkeypatch, capsys):
    _run(
        monkeypatch,
        ["profile-transition-preview", "--target-profile", "scientific_preservation"],
    )
    out = capsys.readouterr().out
    assert "Transition preview" in out
    assert "activation" in out.lower()


def test_unknown_target_exits_nonzero(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            ["profile-transition-preview", "--target-profile", "no_such_profile_xyz"],
        )
    assert exc.value.code != 0


def test_full_evidence_reaches_ready(monkeypatch, capsys, tmp_path):
    manifest, verification, restore = _write_evidence(tmp_path)
    _run(
        monkeypatch,
        [
            "profile-transition-preview",
            "--target-profile",
            "scientific_preservation",
            "--backup-manifest",
            str(manifest),
            "--verification-report",
            str(verification),
            "--restore-report",
            str(restore),
            "--json",
        ],
    )
    report = json.loads(capsys.readouterr().out)
    assert report["transition_status"] == "ready_for_review"
    assert report["evidence"]["backup_manifest_id"] == "lhbackup-xyz"


def test_output_file_written(monkeypatch, capsys, tmp_path):
    out = tmp_path / "preview.json"
    _run(
        monkeypatch,
        [
            "profile-transition-preview",
            "--target-profile",
            "scientific_preservation",
            "--output",
            str(out),
        ],
    )
    assert out.is_file()
    saved = json.loads(out.read_text())
    assert saved["artifact_digest"]


def test_ready_json_carries_producer_execution_facts(monkeypatch, capsys, tmp_path):
    # LHP-PROFILE-14A: a ready preview carries the typed target-policy snapshot, the two
    # content digests, and the provenance stamps (nullable), at serialization v2.
    manifest, verification, restore = _write_evidence(tmp_path)
    _run(
        monkeypatch,
        [
            "profile-transition-preview",
            "--target-profile",
            "scientific_preservation",
            "--backup-manifest",
            str(manifest),
            "--verification-report",
            str(verification),
            "--restore-report",
            str(restore),
            "--json",
        ],
    )
    report = json.loads(capsys.readouterr().out)
    assert report["transition_status"] == "ready_for_review"
    assert report["deterministic_serialization_version"] == 2
    assert report["target_policy_snapshot"] == {
        "local_only": True,
        "dms_eligibility": True,
        "data_root": "/data/profiles/scientific_preservation",
        "schema_migration_required": False,
    }
    assert len(report["target_profile_sha256"]) == 64
    assert len(report["evidence"]["backup_manifest_sha256"]) == 64
    assert set(report["changes"]) >= {"local_only_changed", "dms_eligibility_changed"}


def test_unreadable_evidence_exits_nonzero(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            [
                "profile-transition-preview",
                "--target-profile",
                "scientific_preservation",
                "--backup-manifest",
                str(tmp_path / "missing.json"),
            ],
        )
    assert exc.value.code != 0
