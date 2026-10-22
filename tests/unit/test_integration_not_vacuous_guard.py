"""Tests for the scheduled-integration non-vacuity guard (LHP-CI-02).

The guard exists because pytest exits 0 when every test is skipped: CI run
35786919292 reported success while measuring 0 passed and 5 skipped. A guard
that has never refused anything is not evidence, so the refusal paths are
tested first and explicitly — including the exact shape of the artifact that
real run produced.
"""

from __future__ import annotations

import importlib.util

import pytest

from tests.runtime_isolation import repo_root

REPO = repo_root()
GUARD_PATH = REPO / "scripts" / "check_integration_not_vacuous.py"


def _load_guard():
    """Import the guard script by path — scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_integration_not_vacuous", GUARD_PATH)
    assert spec and spec.loader, f"cannot load guard from {GUARD_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _write(tmp_path, body: str):
    report = tmp_path / "results.xml"
    report.write_text(body)
    return report


def test_guard_script_exists():
    assert GUARD_PATH.is_file(), f"{GUARD_PATH} is missing — the workflow step would fail"


def test_refuses_the_real_all_skipped_report(tmp_path, capsys):
    """Negative control: the exact counts CI run 35786919292 produced.

    0 passed, 5 skipped, exit 0 from pytest. The guard must turn that into a
    refusal, or the packet has changed nothing.
    """
    report = _write(
        tmp_path,
        '<testsuite name="pytest" errors="0" failures="0" skipped="5" tests="5" time="0.14"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    captured = capsys.readouterr()
    assert "REFUSE" in captured.err
    assert "all-skipped run is not a successful run" in captured.err
    assert "passed=0" in captured.out


def test_accepts_a_run_where_the_credential_free_set_passed(tmp_path, capsys):
    """Positive control: 7 passed, 4 gated tests skipped — the intended daily shape."""
    report = _write(
        tmp_path,
        '<testsuite name="pytest" errors="0" failures="0" skipped="4" tests="11" time="9.0"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 0
    captured = capsys.readouterr()
    assert "OK: 7 integration test(s) actually passed" in captured.out


def test_refuses_silent_erosion_below_the_floor(tmp_path, capsys):
    """6 of 7 still running is what a floor of 1 would wave through."""
    report = _write(
        tmp_path,
        '<testsuite name="pytest" errors="0" failures="0" skipped="5" tests="11" time="9.0"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "passed=6" in capsys.readouterr().out


def test_a_failing_test_does_not_count_as_passed(tmp_path, capsys):
    """11 collected, 1 failed, 4 gated-skips -> 6 passed, which is below the floor."""
    report = _write(
        tmp_path,
        '<testsuite name="pytest" errors="0" failures="1" skipped="4" tests="11" time="9.0"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "passed=6 failed=1 errors=0 skipped=4" in capsys.readouterr().out


def test_teardown_errors_are_subtracted_with_their_extra_entries(tmp_path, capsys):
    """A teardown error adds one <testcase> entry, counted in both tests and errors.

    This is the shape pytest produced while the MCP session fixture was still an
    async generator: 11 real tests, 7 of them also erroring at teardown, so
    tests=18 and errors=7.
    """
    report = _write(
        tmp_path,
        '<testsuite name="pytest" errors="7" failures="0" skipped="4" tests="18" time="534"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 0
    assert "passed=7 failed=0 errors=7 skipped=4" in capsys.readouterr().out


def test_all_four_numbers_are_always_printed(tmp_path, capsys):
    report = _write(
        tmp_path,
        '<testsuite name="pytest" errors="1" failures="2" skipped="3" tests="14" time="9.0"/>',
    )
    guard.main([str(report), "--min-passed", "7"])
    out = capsys.readouterr().out
    assert "passed=8" in out
    assert "failed=2" in out
    assert "errors=1" in out
    assert "skipped=3" in out


def test_refuses_a_missing_report(tmp_path, capsys):
    assert guard.main([str(tmp_path / "nope.xml"), "--min-passed", "7"]) == 1
    assert "report not found" in capsys.readouterr().err


def test_refuses_malformed_xml(tmp_path, capsys):
    report = _write(tmp_path, '<testsuite tests="11"')
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "not well-formed XML" in capsys.readouterr().err


def test_refuses_a_report_with_no_testsuite(tmp_path, capsys):
    report = _write(tmp_path, "<somethingelse/>")
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "no <testsuite> element" in capsys.readouterr().err


@pytest.mark.parametrize("attr", ["tests", "failures", "errors", "skipped"])
def test_refuses_a_missing_count_attribute(tmp_path, capsys, attr):
    attrs = {"tests": "11", "failures": "0", "errors": "0", "skipped": "4"}
    del attrs[attr]
    body = "<testsuite " + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + "/>"
    assert guard.main([str(_write(tmp_path, body)), "--min-passed", "7"]) == 1
    assert f"has no '{attr}' attribute" in capsys.readouterr().err


def test_refuses_a_non_numeric_count(tmp_path, capsys):
    report = _write(
        tmp_path,
        '<testsuite tests="lots" failures="0" errors="0" skipped="4"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "is not an integer" in capsys.readouterr().err


def test_refuses_a_negative_count(tmp_path, capsys):
    report = _write(
        tmp_path,
        '<testsuite tests="11" failures="-1" errors="0" skipped="4"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "is negative" in capsys.readouterr().err


def test_refuses_a_self_inconsistent_report(tmp_path, capsys):
    report = _write(
        tmp_path,
        '<testsuite tests="2" failures="0" errors="0" skipped="5"/>',
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 1
    assert "self-inconsistent" in capsys.readouterr().err


def test_refuses_a_floor_below_one(tmp_path, capsys):
    """A floor of 0 would accept the very run this guard exists to catch."""
    report = _write(
        tmp_path,
        '<testsuite tests="5" failures="0" errors="0" skipped="5"/>',
    )
    assert guard.main([str(report), "--min-passed", "0"]) == 2
    assert "must be at least 1" in capsys.readouterr().err


def test_sums_multiple_testsuites(tmp_path, capsys):
    """pytest writes one <testsuite>, but a <testsuites> wrapper must not be miscounted."""
    report = _write(
        tmp_path,
        "<testsuites>"
        '<testsuite tests="6" failures="0" errors="0" skipped="2"/>'
        '<testsuite tests="5" failures="0" errors="0" skipped="2"/>'
        "</testsuites>",
    )
    assert guard.main([str(report), "--min-passed", "7"]) == 0
    assert "passed=7" in capsys.readouterr().out


def test_workflow_passes_the_documented_floor():
    """The workflow must call the guard with the floor the guard documents."""
    workflow = (REPO / ".github" / "workflows" / "integration.yml").read_text()
    assert "check_integration_not_vacuous.py" in workflow, "guard is not wired into the workflow"
    assert "--min-passed 7" in workflow, "workflow floor drifted from the documented set of 7"
