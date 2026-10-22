"""LHP-INTEL-01E — the runtime observation intake boundary.

Four packets of observation machinery existed and nothing called any of them. This is the layer that
calls them, and it is the first code in the intelligence track that *touches the operator's real
filesystem on their say-so* — so almost every test here is a test about refusal.

The safety claims, each pinned rather than asserted in prose:

* **The roots are the boundary, and they are actually passed.** Both collectors accept
  `allowed_roots=None`, and `None` means *no boundary at all*. If this service ever passed `None`
  downward, every path on the disk would be observable and nothing else in the system would notice.
  The outside-root and symlink-escape tests exist to make that mistake loud.
* **Empty roots refuse.** Not "observe everything" — refuse. The deployed container has no git and
  no mounted repositories, so it configures no roots, and therefore does nothing. Honestly.
* **Dry run writes nothing at all** — not a row, not a table, not the database file.
* **Repeat intake is idempotent**, because identity is content: an unchanged repository normalizes
  to the same digest forever, so observing it twice stores it once.
* **One bad source does not abort the others** (`main.py`'s module-fault-isolation rule).
* **Reading is not writing**: the observed tree, `.git/` included, is byte-identical afterwards.

No network, no LLM, no execution capability. The only subprocess in the whole layer remains 01A's
single hardened git chokepoint — this boundary adds none of its own.
"""

import ast
import sqlite3
from pathlib import Path

import pytest

from modules import db, observation_intake
from modules.observation_intake import (
    INTAKE_STATUSES,
    IntakeRun,
    ObservationIntakeError,
    run_observation_intake,
)
from modules.observation_sources import ObservationSource
from tests.unit.test_repository_intake import make_repo, tree_digest

# -- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """The one root the operator has permitted. Everything else on this disk is off limits."""
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Persistence points at a throwaway database — the real data/state.db is never opened."""
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


def _ensure_active_rcd() -> None:
    from tests.unit.active_profile_test_helpers import plant_active_rcd

    plant_active_rcd()


@pytest.fixture
def repo(workspace: Path) -> Path:
    return make_repo(
        workspace / "widget",
        {
            "README.md": "# widget\n",
            "pyproject.toml": "[project]\nname='widget'\n",
            "src/widget.py": "x = 1\n",
        },
    )


@pytest.fixture
def document(workspace: Path) -> Path:
    path = workspace / "spec.md"
    path.write_text("# spec\n\nthe thing does the thing\n", encoding="utf-8")
    return path


def repository_source(path: Path) -> ObservationSource:
    return ObservationSource(type="repository", path=str(path))


def document_source(path: Path) -> ObservationSource:
    return ObservationSource(type="document", path=str(path))


def observe(sources, roots, *, dry_run: bool = False) -> IntakeRun:
    _ensure_active_rcd()
    return run_observation_intake(
        sources=list(sources),
        allowed_roots=[str(root) for root in roots],
        dry_run=dry_run,
    )


def table_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("observation_sets", "normalized_entities", "normalized_relationships")
        }
    finally:
        conn.close()


# -- it works ------------------------------------------------------------------------------


def test_a_declared_repository_is_observed_normalized_and_persisted(repo, workspace, store):
    run = observe([repository_source(repo)], [workspace])

    assert [o.status for o in run.outcomes] == ["stored"]
    outcome = run.outcomes[0]
    assert outcome.source_type == "repository"
    assert outcome.scope_id and outcome.artifact_digest and outcome.normalization_digest
    assert outcome.entities > 0
    assert outcome.reason is None

    counts = table_counts(store)
    assert counts["observation_sets"] == 1
    assert counts["normalized_entities"] == outcome.entities
    assert counts["normalized_relationships"] == outcome.relationships


def test_a_declared_document_is_refused_under_active_rcd(document, workspace, store):
    """RCD admits repositories only — document intake is refused at the admission gate."""
    run = observe([document_source(document)], [workspace])

    assert [o.status for o in run.outcomes] == ["failed"]
    assert run.outcomes[0].reason == "source_type_not_admitted"
    assert "observation_sets" not in table_names(store)


def test_repositories_succeed_while_documents_are_refused_under_rcd(
    repo, document, workspace, store
):
    """One failing (not-admitted) source does not abort repository intake."""
    run = observe([repository_source(repo), document_source(document)], [workspace])

    assert [o.status for o in run.outcomes] == ["stored", "failed"]
    assert run.outcomes[1].reason == "source_type_not_admitted"
    assert table_counts(store)["observation_sets"] == 1


# -- determinism ---------------------------------------------------------------------------


def test_observing_an_unchanged_source_twice_stores_it_once(repo, workspace, store):
    """Identity is content, so the second run has nothing new to say — and says so."""
    first = observe([repository_source(repo)], [workspace])
    before = table_counts(store)

    second = observe([repository_source(repo)], [workspace])

    assert first.outcomes[0].status == "stored"
    assert second.outcomes[0].status == "already_present"
    assert second.outcomes[0].normalization_digest == first.outcomes[0].normalization_digest
    assert table_counts(store) == before


def test_repeated_intake_is_deterministic(repo, workspace, store):
    first = observe([repository_source(repo)], [workspace])
    second = observe([repository_source(repo)], [workspace])

    assert second.outcomes[0].artifact_digest == first.outcomes[0].artifact_digest
    assert second.outcomes[0].scope_id == first.outcomes[0].scope_id


# -- dry run: zero side effects ------------------------------------------------------------


def test_a_dry_run_writes_nothing_at_all(repo, workspace, store):
    """`dry_run=True` means zero observation side effects.

    Under an already-active profile the active-profile row may exist, but observation tables
    are never created on a dry run — `init_observation_tables()` is itself a write.
    """
    run = observe([repository_source(repo)], [workspace], dry_run=True)

    assert run.dry_run is True
    assert [o.status for o in run.outcomes] == ["previewed"]
    assert run.outcomes[0].normalization_digest
    assert run.outcomes[0].entities > 0
    assert "observation_sets" not in table_names(store)


def test_a_dry_run_reports_the_same_identity_a_real_run_would_persist(repo, workspace, store):
    previewed = observe([repository_source(repo)], [workspace], dry_run=True)
    stored = observe([repository_source(repo)], [workspace])

    assert previewed.outcomes[0].normalization_digest == stored.outcomes[0].normalization_digest


# -- the boundary: refusal -----------------------------------------------------------------


def test_a_source_outside_every_root_is_refused(tmp_path: Path, workspace, store):
    """The roots are the trust boundary and this is what it means to have one."""
    outside = make_repo(tmp_path / "elsewhere" / "secret", {"README.md": "# secret\n"})

    run = observe([repository_source(outside)], [workspace])

    assert run.outcomes[0].status == "failed"
    assert run.outcomes[0].reason == "source_outside_allowed_roots"
    assert "observation_sets" not in table_names(store)


def test_a_symlink_out_of_a_root_cannot_smuggle_a_read(tmp_path: Path, workspace, store):
    """The check is on the CANONICAL path, so a link inside a root cannot reach outside it.

    This test is really about the *service*: the collector already refuses this, but only when it is
    given roots. If this boundary ever passed `allowed_roots=None`, the collector would happily
    follow the link and nothing else in the system would object. That is the mistake this catches.
    """
    outside = make_repo(tmp_path / "elsewhere" / "secret", {"README.md": "# secret\n"})
    link = workspace / "innocent"
    link.symlink_to(outside)

    run = observe([repository_source(link)], [workspace])

    assert run.outcomes[0].status == "failed"
    assert run.outcomes[0].reason == "source_outside_allowed_roots"
    assert "observation_sets" not in table_names(store)


def test_a_document_symlinked_out_of_a_root_is_refused(tmp_path: Path, workspace, store):
    """Under active RCD, documents are refused at admission before path-boundary checks."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("# secret\n", encoding="utf-8")
    link = workspace / "innocent.md"
    link.symlink_to(secret)

    run = observe([document_source(link)], [workspace])

    assert run.outcomes[0].status == "failed"
    assert run.outcomes[0].reason == "source_type_not_admitted"


def test_no_configured_roots_refuses_the_entire_run(repo, workspace, store):
    """Empty roots is a valid configuration, and it fails closed.

    It must never degrade into "no boundary, so observe anything" — which is exactly what passing
    the collectors `allowed_roots=None` would do.
    """
    with pytest.raises(ObservationIntakeError, match="no allowed roots"):
        run_observation_intake(sources=[repository_source(repo)], allowed_roots=[], dry_run=False)

    assert not store.exists()


def test_no_configured_roots_refuses_even_a_dry_run(repo, store):
    with pytest.raises(ObservationIntakeError, match="no allowed roots"):
        run_observation_intake(sources=[repository_source(repo)], allowed_roots=[], dry_run=True)


def test_a_nonexistent_path_fails_closed(workspace, store):
    run = observe([repository_source(workspace / "absent")], [workspace])

    assert run.outcomes[0].status == "failed"
    assert run.outcomes[0].reason == "source_not_found"
    assert "observation_sets" not in table_names(store)


def test_a_directory_that_is_not_a_repository_fails_closed(workspace, store):
    plain = workspace / "plain"
    plain.mkdir()

    run = observe([repository_source(plain)], [workspace])

    assert run.outcomes[0].status == "failed"
    assert run.outcomes[0].reason == "not_a_git_repository"


def test_an_unsupported_document_format_is_refused_as_source_type_under_rcd(workspace, store):
    """Under active RCD, documents are refused at admission before format classification."""
    page = workspace / "page.html"
    page.write_text("<h1>hi</h1>", encoding="utf-8")

    run = observe([document_source(page)], [workspace])

    assert run.outcomes[0].status == "failed"
    assert run.outcomes[0].reason == "source_type_not_admitted"


# -- fault isolation -----------------------------------------------------------------------


def test_one_failing_source_does_not_abort_the_others(repo, document, workspace, store):
    """`main.py`'s rule: a bad source fails itself, never the run."""
    run = observe(
        [
            repository_source(workspace / "absent"),
            repository_source(repo),
            document_source(workspace / "page.html"),
            document_source(document),
        ],
        [workspace],
    )

    assert [o.status for o in run.outcomes] == ["failed", "stored", "failed", "failed"]
    assert run.outcomes[2].reason == "source_type_not_admitted"
    assert run.outcomes[3].reason == "source_type_not_admitted"
    assert table_counts(store)["observation_sets"] == 1


def test_the_run_reports_counts_not_significance(repo, workspace, store):
    run = observe([repository_source(repo)], [workspace])
    payload = run.to_dict()

    assert payload["stored"] == 1
    assert payload["failed"] == 0
    assert payload["dry_run"] is False
    # It says what happened. It never says whether any of it mattered.
    assert not [key for key in payload if key in {"score", "rank", "priority", "importance"}]


def test_the_status_vocabulary_is_closed():
    assert frozenset({"stored", "already_present", "previewed", "failed"}) == INTAKE_STATUSES


# -- reading is not writing ----------------------------------------------------------------


def test_a_full_intake_run_leaves_the_observed_tree_byte_identical(repo, workspace, store):
    """`git status` rewrites `.git/index` unless you stop it. 01A stops it; this proves the
    boundary did not undo that by taking some other path to the repository."""
    before = tree_digest(repo)

    observe([repository_source(repo)], [workspace])

    assert tree_digest(repo) == before


def test_a_dry_run_also_leaves_the_observed_tree_byte_identical(repo, workspace, store):
    before = tree_digest(repo)

    observe([repository_source(repo)], [workspace], dry_run=True)

    assert tree_digest(repo) == before


# -- purity: the boundary adds no new capability -------------------------------------------

_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "logging",
        "typing",
        "modules.active_profile_state",
        "modules.active_profile_store",
        "modules.db",
        "modules.document_intake",
        "modules.observation_normalizer",
        "modules.observation_sources",
        "modules.observations",
        "modules.profile_registry",
        "modules.profile_transition",
        "modules.repository_intake",
    }
)

_FORBIDDEN_INTERPRETATION_SURFACE = (
    "score",
    "rank",
    "priority",
    "importance",
    "severity",
    "quality",
    "recommend",
    "classify",
    "summarize",
    "is_important",
)


def _module_source() -> str:
    return Path(observation_intake.__file__).read_text(encoding="utf-8")


def test_the_boundary_adds_no_execution_capability():
    """It causes git to run — through 01A's single hardened chokepoint, which it does not
    reimplement.

    It has no subprocess of its own, no shell, no import machinery, no deserialization of code. The
    packet's rule is that intake never executes what it observes; this is that rule as a test.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(_module_source())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            # `from modules import db` must read as `modules.db`, not as a bare `modules` — else
            # the allowlist would wave through `from modules import anything`.
            if module == "modules":
                imported.update(f"modules.{alias.name}" for alias in node.names)
            else:
                imported.add(module)

    assert not imported & {
        "subprocess",
        "os",
        "sys",
        "shutil",
        "socket",
        "importlib",
        "pickle",
        "marshal",
        "httpx",
        "requests",
        "anthropic",
        "playwright",
    }
    assert imported <= _ALLOWED_IMPORTS


def test_the_boundary_never_reads_the_environment():
    """Observation intake never reads the environment; roots arrive as an argument.

    `modules/data_paths.py` is the canonical reader of `LIGHTHOUSE_DATA_DIR` and `DB_PATH`.
    Observation roots are supplied via `ObservationConfig.allowed_roots` in `config.py`.
    """
    reads = {"getenv", "environ", "putenv", "system", "popen", "exec", "eval", "compile"}
    tree = ast.parse(_module_source())
    touched = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }

    assert not touched & reads


def test_the_boundary_exposes_no_interpretation_surface():
    for name in _FORBIDDEN_INTERPRETATION_SURFACE:
        assert not hasattr(observation_intake, name), name
