"""Invocation contract for the repository pytest configuration (LHP-CI-01).

``pyproject.toml`` registers the runtime-isolation boundary through
``addopts = ["-p", "tests.runtime_isolation", ...]``. pytest imports ``-p``
plugins inside ``Config._preparse`` -- before rootdir/conftest ``sys.path``
insertion happens -- so ``tests`` must already be importable when the
interpreter starts. The editable install exposes only ``config``, ``main``,
``modules`` and ``portal``, so the repository root reaches ``sys.path`` only
when pytest is started as ``python -m pytest`` from the repository root. The
bare ``pytest`` console script sets ``sys.path[0]`` to its own ``bin/``
directory instead, and the plugin import fails before collection with
``ModuleNotFoundError: No module named 'tests'``.

These tests pin that contract for the repository's *executable* command
sources -- the GitHub Actions workflows and the ``Makefile``. Markdown prose
is deliberately not scanned: the documentation discusses pytest by name
constantly, and a doc-wide pattern would report those mentions as
invocations.

Limitations of the scan, stated precisely:

- A command position is inferred from the surrounding characters; neither
  YAML nor shell is parsed. A non-invocation line that happens to contain a
  bare ``pytest`` token (a future ``pip install pytest``, say) would be
  reported as a violation.
- Only a literal ``python -m`` / ``python3 -m`` immediately preceding the
  token is accepted. An equivalent invocation through a variable
  (``$PYTHON -m pytest``) or an absolute interpreter path would be reported
  as a violation.
- ``pytest-cov``, ``pytest_asyncio``, ``pytest.ini_options`` and
  ``.pytest_cache`` are not invocations and are not reported.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.runtime_isolation import repo_root

REPO = repo_root()
WORKFLOW_DIR = REPO / ".github" / "workflows"
MAKEFILE = REPO / "Makefile"

# A pytest *token*: not part of a longer identifier, path, or hyphenated name.
_PYTEST_TOKEN = re.compile(r"(?<![\w./-])pytest(?![\w./-])")
# The only accepted prefix, anchored to the end of the text before the token.
_MODULE_FORM = re.compile(r"python3?\s+-m\s+$")


def find_bare_pytest_invocations(text: str, label: str) -> list[str]:
    """Return one entry per pytest invocation not written as ``python -m pytest``."""
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _PYTEST_TOKEN.finditer(line):
            if _MODULE_FORM.search(line[: match.start()]):
                continue
            violations.append(f"{label}:{lineno}: bare pytest invocation: {line.strip()}")
    return violations


def count_pytest_invocations(text: str) -> int:
    """Number of pytest invocation tokens, module-form or not."""
    return len(_PYTEST_TOKEN.findall(text))


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def test_workflow_pytest_invocations_use_module_form():
    violations: list[str] = []
    for path in _workflow_files():
        violations.extend(
            find_bare_pytest_invocations(
                path.read_text(encoding="utf-8"),
                path.relative_to(REPO).as_posix(),
            )
        )
    assert violations == []


def test_makefile_pytest_invocations_use_module_form():
    violations = find_bare_pytest_invocations(
        MAKEFILE.read_text(encoding="utf-8"),
        MAKEFILE.relative_to(REPO).as_posix(),
    )
    assert violations == []


def test_scan_actually_reaches_the_known_command_sources():
    """Coverage control: a broken glob must not pass vacuously.

    An empty violation list is only evidence once the scan is known to have
    read the files that carry the invocations.
    """
    workflows = {path.name: path for path in _workflow_files()}
    assert "ci.yml" in workflows
    assert "integration.yml" in workflows
    assert MAKEFILE.is_file()

    counts = {
        "ci.yml": count_pytest_invocations(workflows["ci.yml"].read_text(encoding="utf-8")),
        "integration.yml": count_pytest_invocations(
            workflows["integration.yml"].read_text(encoding="utf-8")
        ),
        "Makefile": count_pytest_invocations(MAKEFILE.read_text(encoding="utf-8")),
    }
    assert counts["ci.yml"] >= 1
    assert counts["integration.yml"] >= 1
    assert counts["Makefile"] >= 2
    assert sum(counts.values()) >= 4


def test_detector_reports_a_bare_invocation():
    """Positive control: a guard that has never refused anything is not evidence."""
    violations = find_bare_pytest_invocations(
        "      - run: pytest tests/unit -v --tb=short\n",
        "synthetic.yml",
    )
    assert len(violations) == 1
    assert violations[0].startswith("synthetic.yml:1:")


def test_detector_accepts_the_module_form():
    assert (
        find_bare_pytest_invocations(
            "      - run: python -m pytest tests/unit -v --tb=short\n"
            "\tpython3 -m pytest tests/unit -q\n"
            "env -u NOSTR_PRIVATE_KEY_HEX python -m pytest tests/unit -v\n",
            "synthetic.yml",
        )
        == []
    )


def test_detector_ignores_tokens_that_are_not_invocations():
    assert (
        find_bare_pytest_invocations(
            "      - run: pip install pytest-cov\n"
            "import pytest_asyncio\n"
            "[tool.pytest.ini_options]\n"
            "\tfind . -type d -name .pytest_cache -exec rm -rf {} +\n",
            "synthetic.txt",
        )
        == []
    )
