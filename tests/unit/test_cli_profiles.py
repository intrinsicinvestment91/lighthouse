"""Unit tests for the read-only `profiles` CLI command in main.py."""

import json

import pytest

import main


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def test_profiles_list_default(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["profiles"])
    out = capsys.readouterr().out
    assert "Bundled intelligence profiles: 5" in out
    assert "public_scientific_lighthouse" in out
    assert "scientific_preservation" in out
    # Exactly one may be active; initial RCD activation is available; switch/deactivate are not.
    assert "one profile may be active" in out
    assert "Initial activation is available for repository_capability_discovery v2" in out
    assert "profile-activate" in out
    assert "Switching and deactivation remain unimplemented" in out
    assert "Activation is NOT implemented" not in out


def test_profiles_list_json(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["profiles", "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, list)
    assert len(parsed) == 5
    assert {p["profile_id"] for p in parsed} >= {"public_scientific_lighthouse"}


def test_profiles_show(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["profiles", "--show", "scientific_preservation"])
    out = capsys.readouterr().out
    assert "scientific_preservation" in out
    assert "private: True" in out
    assert "public_publishing: False" in out


def test_profiles_show_json(monkeypatch, capsys):
    _run_cli_with_args(
        monkeypatch, ["profiles", "--show", "public_scientific_lighthouse", "--json"]
    )
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["profile_id"] == "public_scientific_lighthouse"


def test_profiles_show_missing_exits_nonzero(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc_info:
        _run_cli_with_args(monkeypatch, ["profiles", "--show", "nope_profile"])
    assert exc_info.value.code == 1
    assert "Error" in capsys.readouterr().out


def test_profiles_validate_pass(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["profiles", "--validate"])
    out = capsys.readouterr().out
    assert "Profile validation: PASS" in out
    assert "Profiles: 5" in out


def test_profiles_validate_json(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["profiles", "--validate", "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["valid"] is True
    assert parsed["profile_count"] == 5


def test_transition_preview_human_output_does_not_infer_no_active_profile(monkeypatch, capsys):
    """Missing --current-profile is operator omission — not a claim that nothing is active."""
    _run_cli_with_args(
        monkeypatch,
        ["profile-transition-preview", "--target-profile", "scientific_preservation"],
    )
    out = capsys.readouterr().out
    assert "not supplied — preview does not read active-profile state" in out
    assert "none active" not in out
    assert "activation not implemented" not in out.lower()


def test_transition_preview_current_profile_help_is_truthful(monkeypatch, capsys):
    """--current-profile is operator-supplied comparison input; preview does not read the store."""
    with pytest.raises(SystemExit) as exc_info:
        _run_cli_with_args(monkeypatch, ["profile-transition-preview", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--current-profile" in out
    assert "operator-supplied" in out
    assert "does not derive" in out or "does not read" in out
    assert "active-profile state" in out
    assert "none active until activation ships" not in out


def test_profile_activate_help_names_backup_verify(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc_info:
        _run_cli_with_args(monkeypatch, ["profile-activate", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--verification-report" in out
    assert "backup-verify" in out
    assert "passed" in out
    assert "verified" in out
    assert "--restore-report" in out
    assert "restore-drill" in out
