"""Synthetic tests for ops/v1_rcd_host_rehearsal.sh cleanup and bin resolution."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REHEARSAL_SCRIPT = REPO_ROOT / "ops" / "v1_rcd_host_rehearsal.sh"
STUB_SOURCE = REPO_ROOT / "tests" / "fixtures" / "rehearsal_stub" / "lighthouse"
RUN_TIMEOUT = 60


def _list_names(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir())


def _rehearsal_dirs(tmpdir: Path) -> list[Path]:
    return sorted(tmpdir.glob("lighthouse-v1-rehearsal-*"))


def _prepare_stub(tmp_path: Path) -> Path:
    stub = tmp_path / "lighthouse"
    shutil.copyfile(STUB_SOURCE, stub)
    stub.chmod(0o755)
    return stub


def _base_env(tmp_path: Path, *, lighthouse_bin: str | None) -> dict[str, str]:
    tmpdir = tmp_path / "t"
    home = tmp_path / "home"
    tmpdir.mkdir()
    home.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(tmpdir),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    if lighthouse_bin is not None:
        env["LIGHTHOUSE_BIN"] = lighthouse_bin
    return env


def _run(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REHEARSAL_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
        check=False,
    )


@pytest.fixture
def outside_snapshot(tmp_path: Path):
    parent = tmp_path.parent
    before = _list_names(parent)
    yield
    assert _list_names(parent) == before


def test_normal_cleanup(tmp_path: Path, outside_snapshot) -> None:
    stub = _prepare_stub(tmp_path)
    env = _base_env(tmp_path, lighthouse_bin=str(stub))
    result = _run(tmp_path, env)
    assert result.returncode == 0
    assert _rehearsal_dirs(tmp_path / "t") == []


def test_fail_at_backup_verify_cleanup(tmp_path: Path, outside_snapshot) -> None:
    stub = _prepare_stub(tmp_path)
    env = _base_env(tmp_path, lighthouse_bin=str(stub))
    env["STUB_FAIL_AT"] = "backup-verify"
    result = _run(tmp_path, env)
    assert result.returncode == 1
    assert _rehearsal_dirs(tmp_path / "t") == []


@pytest.mark.parametrize("sig,expected_rc", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_signal_during_observe_cleanup(
    tmp_path: Path, outside_snapshot, sig: int, expected_rc: int
) -> None:
    stub = _prepare_stub(tmp_path)
    env = _base_env(tmp_path, lighthouse_bin=str(stub))
    env["STUB_SLEEP_AT"] = "observe"
    ready_marker = tmp_path / "ready.marker"
    env["STUB_READY_FILE"] = str(ready_marker)
    stdout_path = tmp_path / "rehearsal.stdout"
    stderr_path = tmp_path / "rehearsal.stderr"
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_f,
        stderr_path.open("w", encoding="utf-8") as stderr_f,
    ):
        proc = subprocess.Popen(
            ["bash", str(REHEARSAL_SCRIPT)],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=stdout_f,
            stderr=stderr_f,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 30
        while not ready_marker.exists():
            if time.monotonic() >= deadline:
                pytest.fail("timed out waiting for STUB_READY_FILE marker")
            if proc.poll() is not None:
                pytest.fail(f"rehearsal exited early with rc={proc.returncode}")
            time.sleep(0.05)
        os.killpg(proc.pid, sig)
        assert proc.wait(timeout=30) == expected_rc
        assert _rehearsal_dirs(tmp_path / "t") == []
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_keep_rehearsal(tmp_path: Path, outside_snapshot) -> None:
    stub = _prepare_stub(tmp_path)
    env = _base_env(tmp_path, lighthouse_bin=str(stub))
    env["KEEP_REHEARSAL"] = "1"
    result = _run(tmp_path, env)
    assert result.returncode == 0
    kept = _rehearsal_dirs(tmp_path / "t")
    assert len(kept) == 1
    shutil.rmtree(kept[0])


def test_missing_bin_and_venv(tmp_path: Path, outside_snapshot) -> None:
    env = _base_env(tmp_path, lighthouse_bin=None)
    result = _run(tmp_path, env)
    assert result.returncode != 0
    assert "set LIGHTHOUSE_BIN or LIGHTHOUSE_VENV" in result.stderr
    assert _list_names(tmp_path / "t") == []


def test_default_seed_mode_placeholder(tmp_path: Path, outside_snapshot) -> None:
    stub = _prepare_stub(tmp_path)
    env = _base_env(tmp_path, lighthouse_bin=str(stub))
    env["KEEP_REHEARSAL"] = "1"
    result = _run(tmp_path, env)
    assert result.returncode == 0
    kept = _rehearsal_dirs(tmp_path / "t")
    assert len(kept) == 1
    root = kept[0]
    assert (root / "data" / "entities.json").read_text(encoding="utf-8") == "{}\n"
    assert (root / "data" / "targets.json").read_text(encoding="utf-8") == "{}\n"
    shutil.rmtree(root)


def test_venv_resolves_bin(tmp_path: Path, outside_snapshot) -> None:
    venv = tmp_path / "venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "lighthouse"
    shutil.copyfile(STUB_SOURCE, stub)
    stub.chmod(0o755)
    env = _base_env(tmp_path, lighthouse_bin=None)
    env["LIGHTHOUSE_VENV"] = str(venv)
    result = _run(tmp_path, env)
    assert result.returncode == 0
    assert _rehearsal_dirs(tmp_path / "t") == []


def test_use_seed_copies_real_seed_bytes(tmp_path: Path, outside_snapshot) -> None:
    stub = _prepare_stub(tmp_path)
    env = _base_env(tmp_path, lighthouse_bin=str(stub))
    env["REHEARSAL_USE_SEED"] = "1"
    env["KEEP_REHEARSAL"] = "1"
    result = _run(tmp_path, env)
    assert result.returncode == 0
    assert "Seed mode: real" in result.stdout
    kept = _rehearsal_dirs(tmp_path / "t")
    assert len(kept) == 1
    root = kept[0]
    for name in ("entities.json", "targets.json"):
        expected = (REPO_ROOT / "seed" / name).read_bytes()
        assert (root / "data" / name).read_bytes() == expected
    shutil.rmtree(root)


def test_stub_on_path_without_bin_refuses(tmp_path: Path, outside_snapshot) -> None:
    path_dir = tmp_path / "pathbin"
    path_dir.mkdir()
    stub = path_dir / "lighthouse"
    shutil.copyfile(STUB_SOURCE, stub)
    stub.chmod(0o755)
    called = tmp_path / "stub_called.txt"
    env = _base_env(tmp_path, lighthouse_bin=None)
    env["PATH"] = f"{path_dir}:/usr/bin:/bin"
    env["STUB_CALLED_FILE"] = str(called)
    result = _run(tmp_path, env)
    assert result.returncode != 0
    assert "set LIGHTHOUSE_BIN or LIGHTHOUSE_VENV" in result.stderr
    assert not called.exists()
    assert _rehearsal_dirs(tmp_path / "t") == []


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_non_executable_bin_refuses(tmp_path: Path, outside_snapshot, kind: str) -> None:
    if kind == "file":
        target = tmp_path / "lighthouse"
        shutil.copyfile(STUB_SOURCE, target)
        target.chmod(0o644)
    else:
        target = tmp_path / "lighthouse_dir"
        target.mkdir()
    env = _base_env(tmp_path, lighthouse_bin=str(target))
    result = _run(tmp_path, env)
    assert result.returncode != 0
    assert "LIGHTHOUSE_BIN" in result.stderr
    assert _rehearsal_dirs(tmp_path / "t") == []
