#!/usr/bin/env python3
"""Refuse a scheduled integration run that proved nothing.

pytest exits 0 when every test is skipped. On 2026-09-22 the scheduled
Integration Tests workflow did exactly that — run 35786919292 reported
``conclusion: success`` while measuring ``0 passed, 0 failed, 5 skipped``.
A green tick is a summary, not the evidence; this guard reads the evidence.

It parses the JUnit XML pytest wrote and refuses unless at least
``--min-passed`` tests actually passed. It never parses terminal output, and
it fails closed: a missing, unreadable, malformed or nonsensical report is a
refusal, not a fallback, because a broken instrument is not a measurement.

WHY THE FLOOR IS 7 (the value the workflow passes today)
--------------------------------------------------------
``tests/integration`` holds 11 tests. Exactly 7 of them are credential-free
and therefore expected to run on every scheduled build — the MCP server tests
in ``tests/integration/test_mcp_server_integration.py``. The other 4 are
gated on credentials or an explicit opt-in flag and are *expected* to skip:

    test_patent_api.py::test_fetch_uspto_patents_live
        RUN_LIVE_PATENT_API_TESTS + PATENT_PROVIDER
    test_patent_api.py::test_fetch_wipo_patents_live
    test_patent_api.py::test_fetch_wipo_patents_paginates_correctly
        RUN_LIVE_PATENT_API_TESTS + WIPO_API_KEY
    test_nostr_live.py::test_publish_to_relay_live
        NOSTR_PRIVATE_KEY_HEX

So the floor is the size of the credential-free set, not a token "more than
zero". A floor of 1 would stay green while 6 of the 7 quietly stopped running.

The floor lives at the call site in ``.github/workflows/integration.yml`` so
that changing it is a visible, deliberate edit. **When you add or remove a
credential-free integration test, change ``--min-passed`` in the same commit**,
and update the list above.

Usage:
    python scripts/check_integration_not_vacuous.py --min-passed 7 integration-results.xml
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_COUNT_ATTRS = ("tests", "failures", "errors", "skipped")


class VacuousResultError(Exception):
    """The report cannot be trusted, or it shows nothing was proven."""


def _int_attr(element: ET.Element, name: str) -> int:
    """Read one non-negative integer attribute, refusing anything else."""
    raw = element.get(name)
    if raw is None:
        raise VacuousResultError(f"<{element.tag}> has no '{name}' attribute")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise VacuousResultError(f"<{element.tag}> '{name}' is not an integer: {raw!r}") from exc
    if value < 0:
        raise VacuousResultError(f"<{element.tag}> '{name}' is negative: {value}")
    return value


def summarise(report_path: Path) -> dict[str, int]:
    """Return {passed, failed, errors, skipped} from a JUnit XML report.

    Raises VacuousResultError for anything that makes the report untrustworthy.
    """
    if not report_path.exists():
        raise VacuousResultError(f"report not found: {report_path}")
    try:
        root = ET.parse(report_path).getroot()
    except ET.ParseError as exc:
        raise VacuousResultError(f"report is not well-formed XML: {exc}") from exc
    except OSError as exc:
        raise VacuousResultError(f"report is unreadable: {exc}") from exc

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise VacuousResultError("report contains no <testsuite> element")

    totals = dict.fromkeys(_COUNT_ATTRS, 0)
    for suite in suites:
        for attr in _COUNT_ATTRS:
            totals[attr] += _int_attr(suite, attr)

    # A test that fails or errors in teardown contributes an extra <testcase>
    # entry, counted in both 'tests' and 'errors'/'failures', so subtracting
    # all three from 'tests' leaves exactly the tests that passed outright.
    passed = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    if passed < 0:
        raise VacuousResultError(
            f"report is self-inconsistent: tests={totals['tests']} "
            f"failures={totals['failures']} errors={totals['errors']} "
            f"skipped={totals['skipped']} implies {passed} passed"
        )
    return {
        "passed": passed,
        "failed": totals["failures"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="JUnit XML report written by pytest")
    parser.add_argument(
        "--min-passed",
        type=int,
        required=True,
        help="minimum number of tests that must actually pass (see module docstring)",
    )
    args = parser.parse_args(argv)

    if args.min_passed < 1:
        print(
            "REFUSE: --min-passed must be at least 1; a floor of 0 would accept a run "
            "that proved nothing",
            file=sys.stderr,
        )
        return 2

    try:
        counts = summarise(args.report)
    except VacuousResultError as exc:
        print(f"REFUSE: {exc}", file=sys.stderr)
        return 1

    print(
        "integration results: "
        f"passed={counts['passed']} failed={counts['failed']} "
        f"errors={counts['errors']} skipped={counts['skipped']}"
    )

    if counts["passed"] < args.min_passed:
        why = (
            "nothing ran: an all-skipped run is not a successful run"
            if counts["passed"] == 0
            else "fewer tests ran than the credential-free set should produce"
        )
        print(
            f"REFUSE: {counts['passed']} test(s) passed, but at least {args.min_passed} "
            f"must pass for this run to mean anything — {why}.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {counts['passed']} integration test(s) actually passed (floor {args.min_passed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
