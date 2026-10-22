from pathlib import Path

SCRIPT_PATH = Path("scripts/run_lighthouse_prod.sh")


def _read_script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_script_exists() -> None:
    assert SCRIPT_PATH.exists()


def test_script_uses_strict_shell_flags() -> None:
    content = _read_script()
    assert "set -euo pipefail" in content


def test_script_runs_clean_env_unit_precheck() -> None:
    content = _read_script()
    assert (
        "env -u NOSTR_PRIVATE_KEY_HEX -u NOSTR_PUBLISH_CORRELATIONS pytest tests/unit -v --tb=short"
        in content
    )


def test_script_validates_entities_seed() -> None:
    content = _read_script()
    assert "python scripts/validate_entities.py data/entities.json" in content


def test_script_writes_logs_to_logs_dir() -> None:
    content = _read_script()
    assert "mkdir -p logs backups" in content
    assert 'log_path="logs/live-${timestamp}.log"' in content


def test_script_backs_up_state_db_if_present() -> None:
    content = _read_script()
    assert 'if [[ -f "data/state.db" ]]; then' in content
    assert 'cp data/state.db "$backup_path"' in content
    assert "backups/state-before-run-" in content


def test_script_loads_dotenv_only_if_present() -> None:
    content = _read_script()
    assert 'if [[ -f ".env" ]]; then' in content
    assert "source .env" in content
