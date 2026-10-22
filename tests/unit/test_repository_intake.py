"""LHP-INTEL-01A — deterministic repository observation intake.

Three properties carry this packet, and each is pinned here:

* An observation states a **fact** and never states why it matters (no scoring, no ranking).
* Observing a repository **never changes it** — not one byte, `.git/` included.
* The same repository state always yields the **same digest**.

Fixtures build real git repositories locally (`git init`, `git commit`). No network is used
and no database is touched: this module has no persistence surface at all.
"""

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from modules import repository_intake
from modules.observations import (
    ALLOWED_CONFIDENCE,
    CONFIDENCE_OBSERVED,
    CONFIDENCE_UNDETERMINED,
    DETERMINISTIC_SERIALIZATION_VERSION,
    ObservationValidationError,
    SourceBoundaryError,
)
from modules.repository_intake import (
    ALLOWED_SOURCES,
    DERIVATION_VERSION,
    OBSERVATION_TYPES,
    REPOSITORY_OBSERVATION_SCHEMA_VERSION,
    GitUnavailableError,
    NotAGitRepositoryError,
    RepositoryNotFoundError,
    RepositoryObservation,
    observe_repository,
    repository_observation_digest,
    validate_observation,
)

# -- fixtures ------------------------------------------------------------------------------

# Raw `<unix> <offset>` form: the one date format every git version parses without argument.
# Fixed dates make commit shas — and therefore artifact digests — reproducible.
_FIXED_DATE = "1767225600 +0000"  # 2026-01-01T00:00:00Z

_GIT_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": _FIXED_DATE,
    "GIT_COMMITTER_DATE": _FIXED_DATE,
    "LC_ALL": "C",
    "TZ": "UTC",
}


def _git(repo: Path, *args: str) -> str:
    """Run git in a fixture repository with a hermetic environment (never the developer's)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip()


def make_repo(root: Path, files: dict[str, str] | None = None, *, commit: bool = True) -> Path:
    """Create a git repository at `root`. `commit=False` leaves HEAD unborn."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet")
    # Version-proof default branch: `git init -b` needs git >= 2.28, this works everywhere.
    _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    for rel, content in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if commit and files:
        _git(root, "add", "-A")
        _git(root, "commit", "--quiet", "-m", "fixture")
    return root


def tree_digest(root: Path) -> str:
    """Digest every byte under `root`, `.git/` included — the source-immutability probe."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        if path.is_symlink():
            h.update(b"L" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            h.update(b"F" + path.read_bytes())
        else:
            h.update(b"D")
        h.update(b"\n")
    return h.hexdigest()


def facts(artifact: RepositoryObservation) -> dict[str, object]:
    """observation_type -> value, for readable assertions."""
    return {o.observation_type: o.value for o in artifact.observations}


def confidences(artifact: RepositoryObservation) -> dict[str, float]:
    return {o.observation_type: o.confidence for o in artifact.observations}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "proj", {"README.md": "# proj\n", "src/main.py": "print(1)\n"})


# -- artifact shape ------------------------------------------------------------------------


def test_observe_repository_returns_a_valid_artifact(repo):
    artifact = observe_repository(repo)

    artifact.validate()
    assert artifact.schema_version == REPOSITORY_OBSERVATION_SCHEMA_VERSION
    assert artifact.serialization_version == DETERMINISTIC_SERIALIZATION_VERSION
    assert artifact.derivation_version == DERIVATION_VERSION
    assert artifact.repository_path == str(repo.resolve())
    assert artifact.observations


def test_every_observation_carries_the_five_required_fields(repo):
    for observation in observe_repository(repo).observations:
        assert observation.observation_type in OBSERVATION_TYPES
        assert observation.source in ALLOWED_SOURCES
        assert observation.confidence in ALLOWED_CONFIDENCE
        assert observation.observed_at.endswith("+00:00")
        assert hasattr(observation, "value")


def test_observation_types_are_unique_within_a_set(repo):
    types = [o.observation_type for o in observe_repository(repo).observations]

    assert len(types) == len(set(types))


def test_every_observation_shares_the_one_clock_read(repo):
    artifact = observe_repository(repo)

    assert {o.observed_at for o in artifact.observations} == {artifact.observed_at}


def test_observation_source_is_pinned_by_the_vocabulary_not_the_producer(repo):
    for observation in observe_repository(repo).observations:
        assert observation.source == OBSERVATION_TYPES[observation.observation_type].source


def test_observation_vocabulary_is_closed_and_pinned():
    """Adding an observation type must be a conscious act, not an accident."""
    assert set(OBSERVATION_TYPES) == {
        "build.systems",
        "ci.configurations",
        "code.extension_counts",
        "code.languages",
        "containerization.files",
        "documentation.doc_directories",
        "documentation.readme_files",
        "git.branch",
        "git.commit_count",
        "git.first_commit_at",
        "git.head_commit",
        "git.head_state",
        "git.last_commit_at",
        "git.remotes",
        "git.root_commits",
        "git.submodules",
        "git.tag_count",
        "git.worktree_status",
        "license.files",
        "packaging.package_managers",
        "repository.is_git_repository",
        "repository.name",
        "repository.nested_repositories",
        "repository.top_level_entries",
        "repository.tracked_file_count",
        "repository.tracked_size_bytes",
        "tests.directories",
        # 03A — content identity. The sensor is the filesystem: git says which paths are admitted,
        # the working tree says what bytes are at them.
        "content.files",
        "content.coverage",
    }


def test_provenance_is_stamped_from_day_one(repo):
    artifact = observe_repository(repo, profile_id="scientific_preservation")

    assert artifact.profile_id == "scientific_preservation"
    assert artifact.derivation_version == DERIVATION_VERSION


def test_profile_id_is_null_when_no_profile_produced_the_observation(repo):
    # Activation is frozen (ADR-0018): there is no active profile, so null is the honest value.
    assert observe_repository(repo).profile_id is None


# -- git facts -----------------------------------------------------------------------------


def test_clean_worktree_is_reported_clean(repo):
    status = facts(observe_repository(repo))["git.worktree_status"]

    assert status == {"clean": True, "changed_entries": 0, "untracked_entries": 0}


def test_dirty_worktree_is_reported_dirty(repo):
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")

    status = facts(observe_repository(repo))["git.worktree_status"]

    assert status == {"clean": False, "changed_entries": 1, "untracked_entries": 1}


def test_attached_head_reports_branch_and_commit(repo):
    observed = facts(observe_repository(repo))

    assert observed["git.head_state"] == "attached"
    assert observed["git.branch"] == "main"
    assert len(observed["git.head_commit"]) == 40


def test_detached_head_has_no_branch_and_that_absence_is_determined(repo):
    _git(repo, "checkout", "--quiet", "--detach")

    artifact = observe_repository(repo)
    observed, confidence = facts(artifact), confidences(artifact)

    assert observed["git.head_state"] == "detached"
    assert observed["git.branch"] is None
    # Provably absent, NOT unknown — the distinction confidence exists to make.
    assert confidence["git.branch"] == CONFIDENCE_OBSERVED
    assert len(observed["git.head_commit"]) == 40


def test_unborn_head_has_no_commit_and_that_absence_is_determined(tmp_path):
    repo = make_repo(tmp_path / "fresh", commit=False)

    artifact = observe_repository(repo)
    observed, confidence = facts(artifact), confidences(artifact)

    assert observed["git.head_state"] == "unborn"
    assert observed["git.head_commit"] is None
    assert confidence["git.head_commit"] == CONFIDENCE_OBSERVED
    assert observed["git.commit_count"] == 0
    assert observed["git.root_commits"] == []
    assert observed["git.branch"] == "main"


def test_repository_with_no_remote_reports_an_empty_list(repo):
    artifact = observe_repository(repo)

    assert facts(artifact)["git.remotes"] == []
    assert confidences(artifact)["git.remotes"] == CONFIDENCE_OBSERVED


def test_multiple_remotes_are_reported_in_a_deterministic_order(repo):
    _git(repo, "remote", "add", "upstream", "https://example.invalid/upstream.git")
    _git(repo, "remote", "add", "origin", "https://example.invalid/origin.git")

    assert facts(observe_repository(repo))["git.remotes"] == [
        {"name": "origin", "url": "https://example.invalid/origin.git"},
        {"name": "upstream", "url": "https://example.invalid/upstream.git"},
    ]


def test_remote_url_credentials_are_redacted(repo):
    _git(repo, "remote", "add", "origin", "https://alice:ghp_supersecret@example.invalid/x.git")

    artifact = observe_repository(repo)

    assert facts(artifact)["git.remotes"] == [
        {"name": "origin", "url": "https://example.invalid/x.git"}
    ]
    serialized = json.dumps(artifact.to_dict())
    assert "ghp_supersecret" not in serialized
    assert "alice" not in serialized


def test_ssh_remote_is_preserved_verbatim(repo):
    # `git@` is the standard SSH user, not a credential — stripping it would destroy the fact.
    _git(repo, "remote", "add", "origin", "git@example.invalid:org/repo.git")

    assert facts(observe_repository(repo))["git.remotes"] == [
        {"name": "origin", "url": "git@example.invalid:org/repo.git"}
    ]


def test_commit_and_tag_counts_are_observed(repo):
    (repo / "second.txt").write_text("2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "second")
    _git(repo, "tag", "v1")
    _git(repo, "tag", "v2")

    observed = facts(observe_repository(repo))

    assert observed["git.commit_count"] == 2
    assert observed["git.tag_count"] == 2


def test_root_commit_and_commit_dates_are_observed(repo):
    observed = facts(observe_repository(repo))

    assert observed["git.root_commits"] == [observed["git.head_commit"]]
    assert observed["git.first_commit_at"].startswith("2026-01-01T00:00:00")
    assert observed["git.last_commit_at"].startswith("2026-01-01T00:00:00")


def test_tracked_file_counts_and_sizes_are_observed(repo):
    observed = facts(observe_repository(repo))

    assert observed["repository.tracked_file_count"] == 2
    assert observed["repository.tracked_size_bytes"] == sum(
        (repo / rel).stat().st_size for rel in ("README.md", "src/main.py")
    )
    assert observed["repository.is_git_repository"] is True
    assert observed["repository.name"] == "proj"


def test_untracked_files_do_not_inflate_tracked_counts(repo):
    # The reason enumeration goes through `git ls-files`: build artifacts never count.
    (repo / ".venv").mkdir()
    (repo / ".venv" / "junk.py").write_text("x" * 5000, encoding="utf-8")

    observed = facts(observe_repository(repo))

    assert observed["repository.tracked_file_count"] == 2


# -- language mix --------------------------------------------------------------------------


def test_language_mix_counts_extensions_and_maps_known_languages(tmp_path):
    repo = make_repo(
        tmp_path / "mixed",
        {"a.py": "1\n", "b.py": "2\n", "c.rs": "fn main(){}\n", "notes.xyz": "?\n"},
    )

    observed = facts(observe_repository(repo))

    assert observed["code.extension_counts"] == {".py": 2, ".rs": 1, ".xyz": 1}
    # An unmapped extension is still reported above — a lookup miss loses nothing.
    assert observed["code.languages"] == {"Python": 2, "Rust": 1}


# -- filesystem presence facts -------------------------------------------------------------


def test_missing_license_is_a_determined_absence(repo):
    artifact = observe_repository(repo)

    assert facts(artifact)["license.files"] == []
    assert confidences(artifact)["license.files"] == CONFIDENCE_OBSERVED


def test_license_file_is_observed_without_being_identified(tmp_path):
    repo = make_repo(tmp_path / "lic", {"LICENSE": "MIT License\n\nCopyright...\n"})

    observed = facts(observe_repository(repo))

    # The file is a fact. "This is the MIT license" would be a conclusion — out of scope.
    assert observed["license.files"] == ["LICENSE"]


def test_python_project_markers_are_observed(tmp_path):
    repo = make_repo(tmp_path / "py", {"pyproject.toml": "[project]\n", "main.py": "1\n"})

    observed = facts(observe_repository(repo))

    assert observed["build.systems"] == [{"name": "python", "marker": "pyproject.toml"}]


def test_rust_project_markers_are_observed(tmp_path):
    repo = make_repo(tmp_path / "rs", {"Cargo.toml": "[package]\n", "Cargo.lock": "\n"})

    observed = facts(observe_repository(repo))

    assert observed["build.systems"] == [{"name": "cargo", "marker": "Cargo.toml"}]
    assert observed["packaging.package_managers"] == [{"name": "cargo", "marker": "Cargo.lock"}]


def test_mixed_language_project_reports_every_build_system_sorted(tmp_path):
    repo = make_repo(
        tmp_path / "poly",
        {
            "pyproject.toml": "[project]\n",
            "Cargo.toml": "[package]\n",
            "package.json": "{}\n",
            "go.mod": "module x\n",
        },
    )

    observed = facts(observe_repository(repo))

    assert observed["build.systems"] == [
        {"name": "cargo", "marker": "Cargo.toml"},
        {"name": "go", "marker": "go.mod"},
        {"name": "node", "marker": "package.json"},
        {"name": "python", "marker": "pyproject.toml"},
    ]


def test_docker_project_is_observed(tmp_path):
    repo = make_repo(
        tmp_path / "dk", {"Dockerfile": "FROM x\n", "docker-compose.yml": "services:\n"}
    )

    observed = facts(observe_repository(repo))

    assert observed["containerization.files"] == ["Dockerfile", "docker-compose.yml"]


def test_ci_documentation_and_tests_presence_is_observed(tmp_path):
    repo = make_repo(
        tmp_path / "full",
        {
            ".github/workflows/ci.yml": "on: push\n",
            ".github/workflows/release.yaml": "on: tag\n",
            "README.md": "# x\n",
            "docs/guide.md": "g\n",
            "tests/test_x.py": "def test_x(): pass\n",
        },
    )

    observed = facts(observe_repository(repo))

    assert observed["ci.configurations"] == [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yaml",
    ]
    assert observed["documentation.readme_files"] == ["README.md"]
    assert observed["documentation.doc_directories"] == ["docs"]
    assert observed["tests.directories"] == ["tests"]


def test_top_level_entries_describe_structure_without_git_metadata(tmp_path):
    repo = make_repo(tmp_path / "st", {"README.md": "x\n", "src/main.py": "1\n"})

    entries = facts(observe_repository(repo))["repository.top_level_entries"]

    assert entries == [
        {"name": "README.md", "kind": "file"},
        {"name": "src", "kind": "directory"},
    ]


# -- nested repositories -------------------------------------------------------------------


def test_nested_repository_is_recorded_and_never_descended_into(tmp_path):
    repo = make_repo(tmp_path / "outer", {"README.md": "outer\n"})
    make_repo(repo / "vendor" / "lib", {"inner.py": "1\n"})

    observed = facts(observe_repository(repo))

    assert observed["repository.nested_repositories"] == ["vendor/lib"]
    # The outer repository's own facts are untouched by the inner one.
    assert observed["repository.tracked_file_count"] == 1
    assert observed["git.commit_count"] == 1


def test_submodule_gitlinks_are_observed(tmp_path):
    repo = make_repo(tmp_path / "outer", {"README.md": "outer\n"})
    inner = make_repo(tmp_path / "inner", {"a.txt": "a\n"})
    sha = _git(inner, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/lib")

    assert facts(observe_repository(repo))["git.submodules"] == ["vendor/lib"]


# -- bounds: no silent truncation ----------------------------------------------------------


def test_large_repository_is_observed_within_bounds(tmp_path):
    files = {f"pkg{i % 10}/mod{i}.py": f"# {i}\n" for i in range(1000)}
    repo = make_repo(tmp_path / "big", files)

    artifact = observe_repository(repo)
    observed, confidence = facts(artifact), confidences(artifact)

    assert observed["repository.tracked_file_count"] == 1000
    assert observed["code.extension_counts"] == {".py": 1000}
    assert confidence["repository.top_level_entries"] == CONFIDENCE_OBSERVED


def test_an_enumeration_that_exceeds_its_bound_is_undetermined_never_truncated(repo, monkeypatch):
    monkeypatch.setattr(repository_intake, "_MAX_LIST_ENTRIES", 1)

    artifact = observe_repository(repo)
    observed, confidence = facts(artifact), confidences(artifact)

    # Two top-level entries, bound of one: the honest answer is "I could not determine this",
    # never a quietly clipped list.
    assert observed["repository.top_level_entries"] is None
    assert confidence["repository.top_level_entries"] == CONFIDENCE_UNDETERMINED


def test_a_scan_that_exceeds_its_bound_is_undetermined(repo, monkeypatch):
    monkeypatch.setattr(repository_intake, "_MAX_SCAN_ENTRIES", 1)

    artifact = observe_repository(repo)

    assert facts(artifact)["repository.nested_repositories"] is None
    assert confidences(artifact)["repository.nested_repositories"] == CONFIDENCE_UNDETERMINED


def test_undetermined_observations_never_carry_a_value(repo, monkeypatch):
    monkeypatch.setattr(repository_intake, "_MAX_LIST_ENTRIES", 1)

    for observation in observe_repository(repo).observations:
        if observation.confidence == CONFIDENCE_UNDETERMINED:
            assert observation.value is None


# -- determinism, serialization, digest ----------------------------------------------------


def test_serialization_is_deterministic(repo):
    stamp = "2026-01-01T00:00:00+00:00"
    first = observe_repository(repo, observed_at=stamp).to_dict()
    second = observe_repository(repo, observed_at=stamp).to_dict()
    first.pop("observation_set_id")
    second.pop("observation_set_id")

    # Byte-identical, not merely equal: key order and value order are both pinned.
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_digest_is_stable_across_runs_and_clock_readings(repo):
    first = observe_repository(repo, observed_at="2026-01-01T00:00:00+00:00")
    second = observe_repository(repo, observed_at="2027-06-15T12:34:56+00:00")

    assert first.artifact_digest == second.artifact_digest
    assert first.observation_set_id != second.observation_set_id


def test_digest_changes_when_the_repository_changes(repo):
    before = observe_repository(repo).artifact_digest

    (repo / "new.py").write_text("2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "add")

    assert observe_repository(repo).artifact_digest != before


def test_digest_recipe_is_public_and_matches_the_artifact(repo):
    artifact = observe_repository(repo)

    assert artifact.artifact_digest == repository_observation_digest(artifact._content())


def test_artifact_round_trips_through_its_serialization(repo):
    artifact = observe_repository(repo)

    assert validate_observation(artifact.to_dict()) == artifact


def test_repeated_observation_of_an_unchanged_repository_is_identical(repo):
    first, second = observe_repository(repo), observe_repository(repo)

    assert facts(first) == facts(second)
    assert first.repository_id == second.repository_id


# -- fail-closed validation ----------------------------------------------------------------


@pytest.fixture
def raw(repo) -> dict:
    return observe_repository(repo).to_dict()


def test_unknown_observation_type_is_rejected(raw):
    raw["observations"][0]["observation_type"] = "repository.vibe"

    with pytest.raises(ObservationValidationError, match="unknown observation type"):
        validate_observation(raw)


def test_duplicate_observation_type_is_rejected(raw):
    raw["observations"].append(dict(raw["observations"][0]))

    with pytest.raises(ObservationValidationError, match="duplicate"):
        validate_observation(raw)


def test_wrong_source_for_an_observation_type_is_rejected(raw):
    target = next(o for o in raw["observations"] if o["observation_type"] == "git.branch")
    target["source"] = "filesystem"

    with pytest.raises(ObservationValidationError, match="source"):
        validate_observation(raw)


def test_unknown_source_is_rejected(raw):
    raw["observations"][0]["source"] = "vibes"

    with pytest.raises(ObservationValidationError, match="source"):
        validate_observation(raw)


def test_a_confidence_between_zero_and_one_is_rejected(raw):
    raw["observations"][0]["confidence"] = 0.5

    with pytest.raises(ObservationValidationError, match="confidence"):
        validate_observation(raw)


def test_a_boolean_confidence_is_rejected(raw):
    # Python: True == 1.0 — without an explicit bool guard this sails straight through.
    raw["observations"][0]["confidence"] = True

    with pytest.raises(ObservationValidationError, match="confidence"):
        validate_observation(raw)


def test_an_undetermined_observation_carrying_a_value_is_rejected(raw):
    raw["observations"][0]["confidence"] = CONFIDENCE_UNDETERMINED

    with pytest.raises(ObservationValidationError, match="undetermined"):
        validate_observation(raw)


def test_a_value_of_the_wrong_kind_is_rejected(raw):
    target = next(o for o in raw["observations"] if o["observation_type"] == "git.commit_count")
    target["value"] = "one"

    with pytest.raises(ObservationValidationError, match="value"):
        validate_observation(raw)


def test_a_boolean_where_an_integer_belongs_is_rejected(raw):
    target = next(o for o in raw["observations"] if o["observation_type"] == "git.commit_count")
    target["value"] = True

    with pytest.raises(ObservationValidationError, match="value"):
        validate_observation(raw)


def test_an_unknown_head_state_is_rejected(raw):
    target = next(o for o in raw["observations"] if o["observation_type"] == "git.head_state")
    target["value"] = "ascended"

    with pytest.raises(ObservationValidationError, match="head_state"):
        validate_observation(raw)


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_an_unsupported_schema_version_is_rejected(raw, version):
    raw["schema_version"] = version

    with pytest.raises(ObservationValidationError, match="schema_version"):
        validate_observation(raw)


def test_an_unsupported_serialization_version_is_rejected(raw):
    raw["serialization_version"] = 2

    with pytest.raises(ObservationValidationError, match="serialization_version"):
        validate_observation(raw)


def test_an_unknown_top_level_key_is_rejected(raw):
    raw["confidence_boost"] = 1.0

    with pytest.raises(ObservationValidationError, match="unexpected"):
        validate_observation(raw)


def test_a_missing_top_level_key_is_rejected(raw):
    del raw["repository_id"]

    with pytest.raises(ObservationValidationError, match="missing"):
        validate_observation(raw)


def test_an_unknown_observation_key_is_rejected(raw):
    raw["observations"][0]["why_it_matters"] = "very"

    with pytest.raises(ObservationValidationError, match="unexpected"):
        validate_observation(raw)


def test_a_tampered_value_is_caught_by_the_recomputed_digest(raw):
    target = next(o for o in raw["observations"] if o["observation_type"] == "git.commit_count")
    target["value"] = 9999

    with pytest.raises(ObservationValidationError, match="digest"):
        validate_observation(raw)


def test_a_relative_repository_path_is_rejected(raw):
    raw["repository_path"] = "proj"

    with pytest.raises(ObservationValidationError, match="repository_path"):
        validate_observation(raw)


def test_a_malformed_repository_id_is_rejected(raw):
    raw["repository_id"] = "not-a-digest"

    with pytest.raises(ObservationValidationError, match="repository_id"):
        validate_observation(raw)


def test_a_naive_timestamp_is_rejected(raw):
    raw["observed_at"] = "2026-01-01T00:00:00"

    with pytest.raises(ObservationValidationError, match="timezone|observed_at"):
        validate_observation(raw)


def test_an_empty_observation_set_is_rejected(raw):
    raw["observations"] = []

    with pytest.raises(ObservationValidationError, match="observations"):
        validate_observation(raw)


def test_a_non_object_record_is_rejected():
    with pytest.raises(ObservationValidationError):
        validate_observation(["not", "an", "object"])


# -- fail-closed inputs --------------------------------------------------------------------


def test_a_missing_repository_is_rejected(tmp_path):
    with pytest.raises(RepositoryNotFoundError):
        observe_repository(tmp_path / "does-not-exist")


def test_a_file_is_not_a_repository(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(RepositoryNotFoundError):
        observe_repository(target)


def test_a_non_git_directory_is_rejected(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "README.md").write_text("x", encoding="utf-8")

    with pytest.raises(NotAGitRepositoryError):
        observe_repository(plain)


def test_a_subdirectory_of_a_repository_is_not_a_repository(repo):
    with pytest.raises(NotAGitRepositoryError):
        observe_repository(repo / "src")


def test_a_path_outside_the_allowed_roots_is_rejected(tmp_path, repo):
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    with pytest.raises(SourceBoundaryError):
        observe_repository(repo, allowed_roots=[allowed])


def test_a_path_inside_the_allowed_roots_is_accepted(tmp_path):
    allowed = tmp_path / "allowed"
    repo = make_repo(allowed / "proj", {"README.md": "x\n"})

    observe_repository(repo, allowed_roots=[allowed]).validate()


def test_a_symlink_escaping_the_allowed_roots_is_rejected(tmp_path):
    outside = make_repo(tmp_path / "outside", {"README.md": "x\n"})
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "sneaky"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SourceBoundaryError):
        observe_repository(link, allowed_roots=[allowed])


def test_git_being_unavailable_fails_closed(repo, monkeypatch):
    monkeypatch.setattr(repository_intake.shutil, "which", lambda _name: None)

    with pytest.raises(GitUnavailableError):
        observe_repository(repo)


# -- safety: the source is never touched ---------------------------------------------------


def test_observing_a_repository_never_modifies_it(repo):
    # Stale the stat cache: this is exactly the condition under which `git status` would
    # normally REWRITE .git/index. GIT_OPTIONAL_LOCKS=0 is what prevents that write.
    os.utime(repo / "README.md", (0, 0))
    before = tree_digest(repo)

    observe_repository(repo)

    assert tree_digest(repo) == before


def test_observation_writes_nothing_anywhere(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj", {"README.md": "x\n"})
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    observe_repository(repo)

    assert list(elsewhere.iterdir()) == []


def test_observation_needs_no_lighthouse_environment(tmp_path, monkeypatch):
    """CI clean checkout: no data/, no DB, no secrets, no configuration."""
    monkeypatch.delenv("LIGHTHOUSE_DATA_DIR", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    repo = make_repo(tmp_path / "proj", {"README.md": "x\n"})

    observe_repository(repo).validate()


# -- safety: the module stays what it claims to be -----------------------------------------

_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "shutil",
        "stat",
        "subprocess",
        "typing",
        "modules.observations",
        "uuid",
        "modules.backup_manifest",
        "modules.backup_readiness",
        "modules.content_identity",
        "modules.profiles",
    }
)

_FORBIDDEN_INTERPRETATION_SURFACE = (
    "score",
    "score_repository",
    "rank",
    "rank_repositories",
    "quality",
    "recommend",
    "importance",
    "classify",
    "summarize",
    "is_important",
)


def _module_source() -> str:
    return Path(repository_intake.__file__).read_text(encoding="utf-8")


def test_module_imports_nothing_impure():
    imported: set[str] = set()
    for node in ast.walk(ast.parse(_module_source())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module or "")

    assert not imported & {"modules.db", "modules.state_db", "httpx", "anthropic", "playwright"}
    assert imported <= _ALLOWED_IMPORTS


def test_the_only_subprocess_call_is_the_single_git_chokepoint():
    source = _module_source()

    assert source.count("subprocess.run(") == 1


def test_module_exposes_no_interpretation_surface():
    """The design rule as a regression test: this layer never says why something matters."""
    for name in _FORBIDDEN_INTERPRETATION_SURFACE:
        assert not hasattr(repository_intake, name), name
