"""Deterministic repository observation intake — `repository_observation.v1` (LHP-INTEL-01A).

The first local observation source: the operator's **own** git repositories, which are already
inside the trust boundary and need no Gateway admission (ADR-0019). This is Class A only; external
third-party source packs remain a separate, Gateway-admitted class (`REPOSITORY_SOURCE_PACKS.md`).

**The design rule this module exists to obey:**

    This layer MUST NOT know *why* an observation matters. It only knows: "I observed this."
    Future layers (LHP-INTEL-01B, normalization) answer: "Is it important?"

So there is no scoring here, no ranking, no summary, no "primary language", no project type, no
quality — those are conclusions. A repository has a `LICENSE` file: fact. It is "the MIT license":
conclusion, and not this module's business.

What it does: read a repository, emit facts, hand back a validated, digest-bearing artifact.

* **Pure.** No database, no network, no LLM, no writes — not to `data/`, not anywhere. It returns
  an object; persistence belongs to whatever consumes it.
* **Read-only toward the source, and that is a tested property.** Every git call runs through one
  chokepoint with a from-scratch environment. `GIT_OPTIONAL_LOCKS=0` is load-bearing: without it
  `git status` refreshes and **rewrites `.git/index`**, which would mutate the very repository we
  promised only to look at. `core.fsmonitor=false` is load-bearing too — a repository-local
  fsmonitor hook is code execution, and nothing observed here is ever executed.
* **Deterministic.** One clock read per run; every list sorted; every dict key-sorted. The digest
  covers content but not time, so an unchanged repository yields the same `artifact_digest`
  forever — that stability is what makes content-identity dedup possible downstream.

Confidence is deterministic, never a guess, and has exactly two values:

* ``1.0`` — **determined.** The fact is known, *including when it is provably absent* (`[]`,
  `false`, `null`). "This repository has no remotes" is a fact.
* ``0.0`` — **undetermined.** The fact could not be established within bounds; the value is then
  always ``None``. An enumeration that exceeds its cap becomes undetermined rather than being
  silently truncated — a clipped list presented as complete is a lie, an honest "I don't know"
  is not.

Provenance is stamped from day one: every artifact carries `profile_id` + `derivation_version`
(`INTELLIGENCE_PROFILE_ARCHITECTURE.md`), so a future mission switch can never silently
reinterpret past intelligence. `profile_id` is `None` today because activation is frozen and no
profile is active (ADR-0018) — the field is the seam, honestly empty.

No content is ever read out of the repository: not file bodies, not commit messages, not diffs.
Only metadata. Repository content identity is already provided by git itself (`git.head_commit`),
so nothing here needs to hash a byte of the operator's source.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.backup_manifest import detect_lighthouse_version
from modules.backup_readiness import is_within_boundary, resolve_path
from modules.content_identity import (
    DIGEST_ALGORITHM,
    ContentChangedDuringReadError,
    ContentIdentityError,
    ContentMissingError,
    ContentSpecialFileError,
    ContentSymlinkError,
    ContentTooLargeError,
    identify_regular_file,
)
from modules.observations import (
    _DERIVATION_RE,
    _SHA256_HEX,
    CONFIDENCE_OBSERVED,
    CONFIDENCE_UNDETERMINED,
    DETERMINISTIC_SERIALIZATION_VERSION,
    Observation,
    ObservationError,
    ObservationSpec,
    ObservationValidationError,
    SourceBoundaryError,
    _require_exact_int,
    _require_utc_timestamp,
    canonical_digest,
    observation_from_dict,
    validate_observations,
)
from modules.profiles import PROFILE_ID_RE

REPOSITORY_OBSERVATION_SCHEMA_VERSION = 1
DERIVATION_VERSION = "repository_intake/2"

SOURCE_FILESYSTEM = "filesystem"
SOURCE_GIT = "git_metadata"
ALLOWED_SOURCES = frozenset({SOURCE_FILESYSTEM, SOURCE_GIT})

# -- content identity (LHP-INTEL-03A) -------------------------------------------------------
#
# What happened to every tracked path, in a closed vocabulary. Exactly one of these says the bytes
# were established; the other nine say, specifically, why they were not — because "I could not read
# it", "it is a link to somewhere else" and "I ran out of budget before reaching it" are three
# different facts, and collapsing them into one silence is how an incomplete inventory starts
# reading like a complete one.
#
# There is no word here for duplicate, copied, reused, common or suspicious. Those are conclusions,
# and this layer does not draw them.
CONTENT_HASHED = "hashed"
CONTENT_MISSING = "missing"
CONTENT_UNREADABLE = "unreadable"
CONTENT_TOO_LARGE = "too_large"
CONTENT_AGGREGATE_LIMIT_EXCEEDED = "aggregate_limit_exceeded"
CONTENT_SYMLINK_UNSUPPORTED = "symlink_unsupported"
CONTENT_GITLINK_UNSUPPORTED = "gitlink_unsupported"
CONTENT_SPECIAL_FILE_REJECTED = "special_file_rejected"
CONTENT_CHANGED_DURING_READ = "changed_during_read"
CONTENT_INVALID_PATH = "invalid_path"

CONTENT_STATUSES = frozenset(
    {
        CONTENT_HASHED,
        CONTENT_MISSING,
        CONTENT_UNREADABLE,
        CONTENT_TOO_LARGE,
        CONTENT_AGGREGATE_LIMIT_EXCEEDED,
        CONTENT_SYMLINK_UNSUPPORTED,
        CONTENT_GITLINK_UNSUPPORTED,
        CONTENT_SPECIAL_FILE_REJECTED,
        CONTENT_CHANGED_DURING_READ,
        CONTENT_INVALID_PATH,
    }
)

# Bounds, chosen against the repositories this actually runs on: the four largest local source
# repositories track 75–733 files, so 5,000 covers an ordinary source repository with room to
# spare while keeping one artifact's record count comfortably inside the normalizer's 25,000.
# 64 MiB per file is `document_intake`'s existing bound, reused rather than reinvented — "too big
# to identify" should mean the same thing to both collectors. 1 GiB in aggregate keeps the hashing
# pass on a pathological binary repository to a couple of seconds instead of unbounded minutes.
MAX_CONTENT_FILE_BYTES = 64 * 1024 * 1024
MAX_CONTENT_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_CONTENT_FILES = 5_000

# The two shapes a per-file record may have, and there is no third. A digest exists exactly when the
# bytes were read; there is no key here in which a significance could be written, so one cannot be.
_CONTENT_HASHED_KEYS = frozenset(
    {"path", "status", "content_digest", "digest_algorithm", "size_bytes"}
)
_CONTENT_BARE_KEYS = frozenset({"path", "status"})

# git's index modes. A tracked entry's *declared* type; the filesystem is asked separately, because
# a tracked regular file can have been replaced on disk by a link since it was committed.
_GIT_MODE_GITLINK = "160000"
_GIT_MODE_SYMLINK = "120000"

HEAD_STATE_ATTACHED = "attached"
HEAD_STATE_DETACHED = "detached"
HEAD_STATE_UNBORN = "unborn"
ALLOWED_HEAD_STATES = frozenset({HEAD_STATE_ATTACHED, HEAD_STATE_DETACHED, HEAD_STATE_UNBORN})

# Userinfo is the only place a credential can hide in a remote URL. Scp-style (`git@host:path`)
# has no scheme and is left verbatim — `git` there is the SSH user, not a secret.
_URL_USERINFO_RE = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@]*@(?P<rest>.*)$")

# Bounds. Exceeding one makes the observation undetermined — never a silently truncated answer.
_MAX_LIST_ENTRIES = 500
_MAX_SCAN_ENTRIES = 20_000
_GIT_TIMEOUT = 60

# Command-line `-c` overrides repository-local config, so a hostile `.git/config` cannot re-enable
# a hook-spawning fsmonitor or background gc on a repository we only meant to read.
_GIT_SAFE_FLAGS = ("-c", "core.fsmonitor=false", "-c", "gc.auto=0")


class RepositoryIntakeError(ObservationError):
    """Base class for every fail-closed *repository* intake condition.

    It inherits from `ObservationError` (the substrate's base), so one `except ObservationError`
    catches any intake failure — a repository one, a document one, or a violation of the shared
    observation contract. `SourceBoundaryError` and `ObservationValidationError` are NOT repository
    errors and no longer pretend to be: they belong to every source, and live in `observations`.
    """


class RepositoryNotFoundError(RepositoryIntakeError):
    """The path does not exist, or is not a directory."""


class NotAGitRepositoryError(RepositoryIntakeError):
    """The path exists but is not the root of a git repository."""


class GitUnavailableError(RepositoryIntakeError):
    """Git is not usable, so git facts cannot be established at all — fail, never half-observe."""


# -- the closed observation vocabulary -----------------------------------------------------

# What a repository can be asked. The model these entries are written in — `ObservationSpec`, the
# five-field `Observation`, the confidence contract — belongs to `modules.observations`; only the
# vocabulary is a repository's own business.

OBSERVATION_TYPES: dict[str, ObservationSpec] = {
    # Presence facts, observed at the repository root: a Dockerfile is a fact whether or not it
    # happens to be committed.
    "repository.name": ObservationSpec(SOURCE_FILESYSTEM, (str,)),
    "repository.top_level_entries": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "repository.nested_repositories": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "license.files": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "documentation.readme_files": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "documentation.doc_directories": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "build.systems": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "packaging.package_managers": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "ci.configurations": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "containerization.files": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "tests.directories": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    # Content facts, computed over git's tracked-file set: build artifacts, virtualenvs and
    # `node_modules` are ignored by construction because git already ignores them.
    "repository.is_git_repository": ObservationSpec(SOURCE_GIT, (bool,)),
    "repository.tracked_file_count": ObservationSpec(SOURCE_GIT, (int,)),
    "repository.tracked_size_bytes": ObservationSpec(SOURCE_GIT, (int,)),
    "code.extension_counts": ObservationSpec(SOURCE_GIT, (dict,), nullable=True),
    "code.languages": ObservationSpec(SOURCE_GIT, (dict,), nullable=True),
    # Git's own answers.
    "git.head_state": ObservationSpec(SOURCE_GIT, (str,)),
    "git.branch": ObservationSpec(SOURCE_GIT, (str,), nullable=True),
    "git.head_commit": ObservationSpec(SOURCE_GIT, (str,), nullable=True),
    "git.root_commits": ObservationSpec(SOURCE_GIT, (list,), nullable=True),
    "git.commit_count": ObservationSpec(SOURCE_GIT, (int,)),
    "git.tag_count": ObservationSpec(SOURCE_GIT, (int,)),
    "git.remotes": ObservationSpec(SOURCE_GIT, (list,), nullable=True),
    "git.submodules": ObservationSpec(SOURCE_GIT, (list,), nullable=True),
    "git.worktree_status": ObservationSpec(SOURCE_GIT, (dict,)),
    "git.first_commit_at": ObservationSpec(SOURCE_GIT, (str,), nullable=True),
    "git.last_commit_at": ObservationSpec(SOURCE_GIT, (str,), nullable=True),
    # Content identity (03A). The sensor is the FILESYSTEM, not git: git says which paths are
    # admitted, but the bytes are read from the working tree — the same sensor, and the same
    # recipe, that establishes a standalone document's `document.content_digest`. That is what
    # lets the two join. `content.coverage` is separate on purpose: when the inventory itself is
    # withheld, the *fact that it is incomplete* must still survive.
    "content.files": ObservationSpec(SOURCE_FILESYSTEM, (list,), nullable=True),
    "content.coverage": ObservationSpec(SOURCE_FILESYSTEM, (dict,)),
}

_LICENSE_STEMS = frozenset({"license", "licence", "copying", "unlicense"})
_DOC_DIRECTORIES = frozenset({"docs", "doc", "documentation"})
_TEST_DIRECTORIES = frozenset({"tests", "test", "spec", "__tests__"})

_BUILD_MARKERS = {
    "BUILD.bazel": "bazel",
    "CMakeLists.txt": "cmake",
    "Cargo.toml": "cargo",
    "Gemfile": "bundler",
    "Makefile": "make",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "composer.json": "composer",
    "go.mod": "go",
    "meson.build": "meson",
    "mix.exs": "mix",
    "package.json": "node",
    "pom.xml": "maven",
    "pyproject.toml": "python",
    "setup.cfg": "python",
    "setup.py": "python",
}

_PACKAGE_MARKERS = {
    "Cargo.lock": "cargo",
    "Gemfile.lock": "bundler",
    "Pipfile.lock": "pipenv",
    "composer.lock": "composer",
    "go.sum": "go",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "poetry.lock": "poetry",
    "requirements.txt": "pip",
    "uv.lock": "uv",
    "yarn.lock": "yarn",
}

_CONTAINER_FILES = frozenset(
    {
        ".dockerignore",
        "Containerfile",
        "Dockerfile",
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
    }
)

_CI_FILES = frozenset(
    {".drone.yml", ".gitlab-ci.yml", ".travis.yml", "Jenkinsfile", "azure-pipelines.yml"}
)

# A deterministic lookup table, not a judgement: an unmapped extension is still reported in
# `code.extension_counts`, so a lookup miss loses nothing.
_EXTENSION_LANGUAGE = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".go": "Go",
    ".h": "C",
    ".hpp": "C++",
    ".html": "HTML",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".lua": "Lua",
    ".md": "Markdown",
    ".php": "PHP",
    ".proto": "Protocol Buffers",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".sh": "Shell",
    ".sql": "SQL",
    ".swift": "Swift",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".yaml": "YAML",
    ".yml": "YAML",
}

_EXPECTED_KEYS = frozenset(
    {
        "schema_version",
        "serialization_version",
        "observation_set_id",
        "repository_id",
        "repository_path",
        "observed_at",
        "profile_id",
        "derivation_version",
        "lighthouse_version",
        "observations",
        "artifact_digest",
    }
)


# -- the artifact --------------------------------------------------------------------------


@dataclass
class RepositoryObservation:
    """One `repository_observation.v1` artifact — every fact observed about one repository."""

    observation_set_id: str
    repository_id: str
    repository_path: str
    observed_at: str
    observations: tuple[Observation, ...]
    profile_id: str | None = None
    derivation_version: str = DERIVATION_VERSION
    lighthouse_version: str | None = None
    schema_version: int = REPOSITORY_OBSERVATION_SCHEMA_VERSION
    serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    artifact_digest: str = ""

    def _sorted(self) -> list[Observation]:
        return sorted(self.observations, key=lambda o: o.observation_type)

    def _content(self) -> dict[str, Any]:
        """Digest-covered content: every fact, plus the producer's identity.

        Excludes exactly `observation_set_id`, both levels of `observed_at`, and the digest
        itself — so re-observing an unchanged repository reproduces the same digest, which is
        what makes the digest a content identity rather than a timestamp.
        """
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "repository_id": self.repository_id,
            "repository_path": self.repository_path,
            "profile_id": self.profile_id,
            "derivation_version": self.derivation_version,
            "lighthouse_version": self.lighthouse_version,
            "observations": [
                {
                    "observation_type": observation.observation_type,
                    "source": observation.source,
                    "confidence": observation.confidence,
                    "value": observation.value,
                }
                for observation in self._sorted()
            ],
        }

    def compute_digest(self) -> str:
        return repository_observation_digest(self._content())

    def validate(self) -> None:
        """Fail closed. Recomputes the digest: this artifact is Lighthouse-owned end to end, so
        an inconsistent record is never merely trusted."""
        _require_exact_int(
            "schema_version", self.schema_version, REPOSITORY_OBSERVATION_SCHEMA_VERSION
        )
        _require_exact_int(
            "serialization_version",
            self.serialization_version,
            DETERMINISTIC_SERIALIZATION_VERSION,
        )
        if not isinstance(self.observation_set_id, str) or not self.observation_set_id:
            raise ObservationValidationError("observation_set_id must be a non-empty string")
        if not isinstance(self.repository_id, str) or not _SHA256_HEX.match(self.repository_id):
            raise ObservationValidationError(
                "repository_id must be a 64-char lowercase hex SHA-256"
            )
        if not isinstance(self.repository_path, str) or not os.path.isabs(self.repository_path):
            raise ObservationValidationError("repository_path must be an absolute path")
        _require_utc_timestamp("observed_at", self.observed_at)
        if not isinstance(self.derivation_version, str) or not _DERIVATION_RE.match(
            self.derivation_version
        ):
            raise ObservationValidationError(
                f"derivation_version must look like 'name/1', got {self.derivation_version!r}"
            )
        if self.profile_id is not None and (
            not isinstance(self.profile_id, str) or not PROFILE_ID_RE.match(self.profile_id)
        ):
            raise ObservationValidationError("profile_id must be a valid profile id or None")
        if self.lighthouse_version is not None and (
            not isinstance(self.lighthouse_version, str) or not self.lighthouse_version
        ):
            raise ObservationValidationError(
                "lighthouse_version must be a non-empty string or None"
            )

        self._validate_observations()

        # A closed value set belonging to *this* vocabulary. The substrate enforces the observation
        # model; it does not know what a git head is, and teaching it would be the same ownership
        # mistake this packet removed, pointed the other way. `DocumentObservation.validate` checks
        # its own `text.extraction_status` in exactly this place, for exactly this reason.
        head_state = next(
            (o.value for o in self.observations if o.observation_type == "git.head_state"),
            None,
        )
        if head_state not in ALLOWED_HEAD_STATES:
            raise ObservationValidationError(
                f"git.head_state must be one of {sorted(ALLOWED_HEAD_STATES)}, got {head_state!r}"
            )

        self._validate_content_files()

        if not isinstance(self.artifact_digest, str) or not _SHA256_HEX.match(self.artifact_digest):
            raise ObservationValidationError(
                "artifact_digest must be a 64-char lowercase hex SHA-256"
            )
        if self.artifact_digest != self.compute_digest():
            raise ObservationValidationError(
                "artifact_digest mismatch — the content does not match its digest"
            )

    def _validate_observations(self) -> None:
        validate_observations(self.observations, OBSERVATION_TYPES)

    def _validate_content_files(self) -> None:
        """The per-file records, checked against their own closed shape.

        The substrate validates the observation *model* — a `content.files` value is "a list", and
        that is all it can say. The records inside it belong to this vocabulary, so this is where
        they are enforced, exactly as `git.head_state` is.

        Without this the closed status vocabulary would be a declaration rather than a gate: a
        record saying `{"status": "important"}` or, far worse, a *fabricated digest on a file we
        admitted we could not read*, would pass. The digest cannot be the gate, because anyone able
        to write a record can re-stamp it.
        """
        records = next(
            (
                observation.value
                for observation in self.observations
                if observation.observation_type == "content.files"
            ),
            None,
        )
        if records is None:  # undetermined — the inventory was withheld, and says so
            return

        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise ObservationValidationError("content.files entries must be objects")

            path = record.get("path")
            if not isinstance(path, str) or not path:
                raise ObservationValidationError("content.files entry needs a non-empty path")
            if (
                path.startswith("/")
                or "\0" in path
                or any(part in {"..", ""} for part in path.split("/"))
            ):
                raise ObservationValidationError(
                    f"content.files path must be repository-relative and traversal-free: {path!r}"
                )
            if path in seen:
                raise ObservationValidationError(f"duplicate content.files path: {path!r}")
            seen.add(path)

            status = record.get("status")
            if status not in CONTENT_STATUSES:
                raise ObservationValidationError(
                    f"content.files status must be one of {sorted(CONTENT_STATUSES)}, "
                    f"got {status!r}"
                )

            expected = _CONTENT_HASHED_KEYS if status == CONTENT_HASHED else _CONTENT_BARE_KEYS
            if set(record) != expected:
                raise ObservationValidationError(
                    f"content.files entry for {path!r} carries {sorted(set(record))}, "
                    f"expected exactly {sorted(expected)}"
                )
            if status != CONTENT_HASHED:
                continue  # an outcome with no digest: nothing further to check, by design

            digest = record["content_digest"]
            if not isinstance(digest, str) or not _SHA256_HEX.match(digest):
                raise ObservationValidationError(
                    f"content_digest must be a 64-char lowercase hex SHA-256: {path!r}"
                )
            if record["digest_algorithm"] != DIGEST_ALGORITHM:
                raise ObservationValidationError(
                    f"digest_algorithm must be {DIGEST_ALGORITHM!r} — a git blob SHA-1 is a "
                    f"different identity and could never join to a document: {path!r}"
                )
            size = record["size_bytes"]
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ObservationValidationError(f"size_bytes must be a non-negative int: {path!r}")

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Validates first — an invalid artifact never serializes."""
        self.validate()
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "observation_set_id": self.observation_set_id,
            "repository_id": self.repository_id,
            "repository_path": self.repository_path,
            "observed_at": self.observed_at,
            "profile_id": self.profile_id,
            "derivation_version": self.derivation_version,
            "lighthouse_version": self.lighthouse_version,
            "observations": [
                {
                    "observation_type": observation.observation_type,
                    "value": observation.value,
                    "source": observation.source,
                    "observed_at": observation.observed_at,
                    "confidence": observation.confidence,
                }
                for observation in self._sorted()
            ],
            "artifact_digest": self.artifact_digest,
        }

    @classmethod
    def from_dict(cls, data: Any) -> RepositoryObservation:
        """Strict, fail-closed deserialization: missing and unexpected keys both raise."""
        if not isinstance(data, dict):
            raise ObservationValidationError("repository observation must be an object")
        keys = set(data)
        missing = _EXPECTED_KEYS - keys
        if missing:
            raise ObservationValidationError(
                f"repository observation is missing required keys: {sorted(missing)}"
            )
        unexpected = keys - _EXPECTED_KEYS
        if unexpected:
            raise ObservationValidationError(
                f"repository observation carries unexpected keys: {sorted(unexpected)}"
            )
        raw_observations = data["observations"]
        if not isinstance(raw_observations, list):
            raise ObservationValidationError("observations must be a list")
        artifact = cls(
            observation_set_id=data["observation_set_id"],
            repository_id=data["repository_id"],
            repository_path=data["repository_path"],
            observed_at=data["observed_at"],
            observations=tuple(observation_from_dict(item) for item in raw_observations),
            profile_id=data["profile_id"],
            derivation_version=data["derivation_version"],
            lighthouse_version=data["lighthouse_version"],
            schema_version=data["schema_version"],
            serialization_version=data["serialization_version"],
            artifact_digest=data["artifact_digest"],
        )
        artifact.validate()
        return artifact


def repository_observation_digest(content: dict[str, Any]) -> str:
    """The repository artifact's digest is the shared recipe — named here only so callers of this
    contract do not have to reach into the substrate to spell it."""
    return canonical_digest(content)


def validate_observation(raw: Any) -> RepositoryObservation:
    """Parse and fully validate a serialized artifact. Fail-closed; never returns a partial."""
    return RepositoryObservation.from_dict(raw)


# -- observation construction --------------------------------------------------------------


def _fact(observation_type: str, value: Any, observed_at: str) -> Observation:
    return Observation(
        observation_type=observation_type,
        value=value,
        source=OBSERVATION_TYPES[observation_type].source,
        observed_at=observed_at,
        confidence=CONFIDENCE_OBSERVED,
    )


def _undetermined(observation_type: str, observed_at: str) -> Observation:
    return Observation(
        observation_type=observation_type,
        value=None,
        source=OBSERVATION_TYPES[observation_type].source,
        observed_at=observed_at,
        confidence=CONFIDENCE_UNDETERMINED,
    )


def _bounded(observation_type: str, value: Any, observed_at: str) -> Observation:
    """An enumeration that exceeds its bound is undetermined, never silently truncated."""
    if value is None or len(value) > _MAX_LIST_ENTRIES:
        return _undetermined(observation_type, observed_at)
    return _fact(observation_type, value, observed_at)


# -- git access: one hardened, read-only chokepoint -----------------------------------------


def _git_env() -> dict[str, str]:
    """A from-scratch environment — an ambient `GIT_DIR`/`GIT_WORK_TREE` can never redirect us,
    and the operator's own `~/.gitconfig` can never alter what is observed."""
    return {
        "PATH": os.environ.get("PATH", ""),
        # Without this, `git status` refreshes and REWRITES `.git/index` — a write to the very
        # repository this module promises never to touch.
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
        "TZ": "UTC",
    }


def _require_git() -> None:
    if shutil.which("git") is None:
        raise GitUnavailableError("git executable not found on PATH")


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """The only place this module launches a process, and it only ever launches read-only git
    plumbing. Never `shell=True`; never a command derived from repository content."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *_GIT_SAFE_FLAGS, *args],
            env=_git_env(),
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError("git executable not found") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitUnavailableError(f"git could not be invoked: {exc}") from exc


def _git_text(root: Path, *args: str) -> str | None:
    result = _run_git(root, *args)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _git_lines(root: Path, *args: str) -> list[str]:
    output = _git_text(root, *args)
    return [line for line in (output or "").splitlines() if line]


def _git_zlines(root: Path, *args: str) -> list[str]:
    result = _run_git(root, *args)
    if result.returncode != 0:
        return []
    raw = result.stdout.decode("utf-8", errors="replace")
    return [chunk for chunk in raw.split("\0") if chunk]


# -- filesystem observations ---------------------------------------------------------------


def _entry_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _top_level_entries(root: Path) -> list[dict[str, str]]:
    """The repository's shape. `.git` is excluded: it is the repository's own bookkeeping, and
    `repository.is_git_repository` already states that fact."""
    return [
        {"name": child.name, "kind": _entry_kind(child)}
        for child in sorted(root.iterdir(), key=lambda path: path.name)
        if child.name != ".git"
    ]


def _scan_nested_repositories(root: Path) -> list[str] | None:
    """Repositories checked out inside this one. Never descends into them, never follows a
    symlink. Returns None (undetermined) if the scan exceeds its bound."""
    found: list[str] = []
    walk = enumerate(os.walk(root, followlinks=False), start=1)
    for visited, (dirpath, dirnames, filenames) in walk:
        if visited > _MAX_SCAN_ENTRIES:
            return None
        here = Path(dirpath)
        # A submodule working tree carries `.git` as a *file*, not a directory.
        if ".git" in dirnames or ".git" in filenames:
            if ".git" in dirnames:
                dirnames.remove(".git")
            if here != root:
                found.append(here.relative_to(root).as_posix())
        dirnames.sort()
    return sorted(found)


def _ci_configurations(root: Path, top_level_files: set[str]) -> list[str]:
    found = [name for name in top_level_files if name in _CI_FILES]
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        found.extend(
            child.relative_to(root).as_posix()
            for child in workflows.iterdir()
            if child.is_file() and child.suffix.lower() in {".yml", ".yaml"}
        )
    circle = root / ".circleci" / "config.yml"
    if circle.is_file():
        found.append(circle.relative_to(root).as_posix())
    return sorted(found)


def _markers(names: set[str], table: dict[str, str]) -> list[dict[str, str]]:
    found = [{"name": table[name], "marker": name} for name in names if name in table]
    return sorted(found, key=lambda entry: (entry["name"], entry["marker"]))


def _filesystem_observations(root: Path, observed_at: str) -> list[Observation]:
    entries = _top_level_entries(root)
    files = {entry["name"] for entry in entries if entry["kind"] == "file"}
    directories = {entry["name"] for entry in entries if entry["kind"] == "directory"}

    return [
        _fact("repository.name", root.name, observed_at),
        _bounded("repository.top_level_entries", entries, observed_at),
        _bounded("repository.nested_repositories", _scan_nested_repositories(root), observed_at),
        _bounded(
            "license.files",
            sorted(name for name in files if Path(name).stem.lower() in _LICENSE_STEMS),
            observed_at,
        ),
        _bounded(
            "documentation.readme_files",
            sorted(name for name in files if Path(name).stem.lower() == "readme"),
            observed_at,
        ),
        _bounded(
            "documentation.doc_directories",
            sorted(name for name in directories if name.lower() in _DOC_DIRECTORIES),
            observed_at,
        ),
        _bounded("build.systems", _markers(files, _BUILD_MARKERS), observed_at),
        _bounded("packaging.package_managers", _markers(files, _PACKAGE_MARKERS), observed_at),
        _bounded("ci.configurations", _ci_configurations(root, files), observed_at),
        _bounded(
            "containerization.files",
            sorted(name for name in files if name in _CONTAINER_FILES),
            observed_at,
        ),
        _bounded(
            "tests.directories",
            sorted(name for name in directories if name.lower() in _TEST_DIRECTORIES),
            observed_at,
        ),
    ]


# -- git observations ----------------------------------------------------------------------


def _redact_remote_url(url: str) -> str:
    return _URL_USERINFO_RE.sub(r"\g<scheme>\g<rest>", url)


def _remotes(root: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for line in _git_lines(root, "config", "--get-regexp", r"^remote\..*\.url$"):
        key, _, url = line.partition(" ")
        if not key.startswith("remote.") or not key.endswith(".url") or not url:
            continue
        name = key[len("remote.") : -len(".url")]
        found.append({"name": name, "url": _redact_remote_url(url)})
    return sorted(found, key=lambda entry: (entry["name"], entry["url"]))


def _submodules(root: Path) -> list[str]:
    found: list[str] = []
    for entry in _git_zlines(root, "ls-files", "--stage", "-z"):
        metadata, _, path = entry.partition("\t")
        if metadata.startswith("160000 ") and path:
            found.append(path)
    return sorted(found)


def _worktree_status(root: Path) -> dict[str, Any]:
    chunks = _git_zlines(root, "status", "--porcelain=v1", "-z")
    changed = untracked = 0
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if len(chunk) < 3:
            continue
        code = chunk[:2]
        if code == "??":
            untracked += 1
            continue
        changed += 1
        if code[0] in {"R", "C"}:
            index += 1  # a rename/copy carries its original path as the next NUL-separated chunk
    return {
        "clean": changed == 0 and untracked == 0,
        "changed_entries": changed,
        "untracked_entries": untracked,
    }


def _tracked_size_bytes(root: Path, tracked: list[str]) -> int:
    total = 0
    for relative in tracked:
        try:
            info = os.lstat(root / relative)
        except OSError:
            continue  # tracked but deleted from the worktree — visible in git.worktree_status
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def _language_mix(tracked: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    extensions: dict[str, int] = {}
    for relative in tracked:
        suffix = Path(relative).suffix.lower()
        extensions[suffix] = extensions.get(suffix, 0) + 1
    languages: dict[str, int] = {}
    for suffix, count in extensions.items():
        language = _EXTENSION_LANGUAGE.get(suffix)
        if language:
            languages[language] = languages.get(language, 0) + count
    return dict(sorted(extensions.items())), dict(sorted(languages.items()))


def _git_observations(root: Path, observed_at: str) -> list[Observation]:
    tracked = _git_zlines(root, "ls-files", "-z")
    head_commit = _git_text(root, "rev-parse", "--verify", "--quiet", "HEAD") or None
    branch = _git_text(root, "symbolic-ref", "--quiet", "--short", "HEAD")

    if head_commit is None:
        head_state = HEAD_STATE_UNBORN
    elif branch is None:
        head_state = HEAD_STATE_DETACHED
    else:
        head_state = HEAD_STATE_ATTACHED

    if head_commit:
        root_commits = sorted(_git_lines(root, "rev-list", "--max-parents=0", "HEAD"))
        count_text = _git_text(root, "rev-list", "--count", "HEAD") or ""
        commit_count = int(count_text) if count_text.isdigit() else 0
        last_commit_at = _git_text(root, "log", "-1", "--format=%cI")
        root_dates = _git_lines(root, "log", "--max-parents=0", "--format=%cI")
        first_commit_at = min(root_dates) if root_dates else None
    else:
        root_commits, commit_count = [], 0
        last_commit_at = first_commit_at = None

    extensions, languages = _language_mix(tracked)

    return [
        _fact("repository.is_git_repository", True, observed_at),
        _fact("repository.tracked_file_count", len(tracked), observed_at),
        _fact("repository.tracked_size_bytes", _tracked_size_bytes(root, tracked), observed_at),
        _bounded("code.extension_counts", extensions, observed_at),
        _bounded("code.languages", languages, observed_at),
        _fact("git.head_state", head_state, observed_at),
        _fact("git.branch", branch, observed_at),
        _fact("git.head_commit", head_commit, observed_at),
        _bounded("git.root_commits", root_commits, observed_at),
        _fact("git.commit_count", commit_count, observed_at),
        _fact(
            "git.tag_count",
            len(_git_lines(root, "for-each-ref", "--format=%(refname)", "refs/tags")),
            observed_at,
        ),
        _bounded("git.remotes", _remotes(root), observed_at),
        _bounded("git.submodules", _submodules(root), observed_at),
        _fact("git.worktree_status", _worktree_status(root), observed_at),
        _fact("git.first_commit_at", first_commit_at, observed_at),
        _fact("git.last_commit_at", last_commit_at, observed_at),
    ]


# -- content identity ----------------------------------------------------------------------


def _tracked_entries(root: Path) -> dict[str, str]:
    """Every tracked path and the type git believes it has, keyed by path.

    NUL-delimited, because a newline is a legal character in a filename and a line-delimited parse
    would hand an attacker a way to invent a path. Keying by path also makes a duplicate
    structurally impossible: a conflicted index lists the same path at several stages, and the
    working tree still has exactly one file there.
    """
    entries: dict[str, str] = {}
    for entry in _git_zlines(root, "ls-files", "--stage", "-z"):
        metadata, _, path = entry.partition("\t")
        mode = metadata.split(" ", 1)[0]
        if path and path not in entries:
            entries[path] = mode
    return entries


def _safe_relative(root: Path, relative: str) -> Path | None:
    """The tracked path, resolved and proven to still be inside the repository — or None.

    git's index cannot express an absolute path or a `..`, and it cannot track *through* a symlink
    (it stores the link itself). But a directory that was a directory at commit time can be a link
    by the time we look, and then a repository-relative name would read a file somewhere else
    entirely. So the resolved path is checked against the boundary rather than trusted.
    """
    if not relative or relative.startswith("/") or "\0" in relative:
        return None
    if any(part in {"..", ""} for part in relative.split("/")):
        return None
    candidate = root / relative
    try:
        if not is_within_boundary(resolve_path(candidate), root):
            return None
    except (OSError, ValueError):
        return None
    return candidate


def _content_record(
    root: Path,
    relative: str,
    mode: str,
    *,
    max_file_bytes: int,
    remaining_bytes: int,
) -> tuple[dict[str, Any], int]:
    """One tracked path's outcome, and how many bytes it consumed of the aggregate budget.

    Every branch below produces a record. There is no path out of this function that omits one,
    which is what makes "every tracked path is accounted for" a property rather than a promise.
    """
    if mode == _GIT_MODE_GITLINK:
        # A nested repository is its own subject. Recursing into it here would silently observe a
        # source nobody admitted, and would attribute its files to the wrong scope.
        return {"path": relative, "status": CONTENT_GITLINK_UNSUPPORTED}, 0
    if mode == _GIT_MODE_SYMLINK:
        return {"path": relative, "status": CONTENT_SYMLINK_UNSUPPORTED}, 0

    target = _safe_relative(root, relative)
    if target is None:
        return {"path": relative, "status": CONTENT_INVALID_PATH}, 0

    try:
        size = os.lstat(target).st_size
    except FileNotFoundError:
        return {"path": relative, "status": CONTENT_MISSING}, 0
    except OSError:
        return {"path": relative, "status": CONTENT_UNREADABLE}, 0

    if size > remaining_bytes:
        return {"path": relative, "status": CONTENT_AGGREGATE_LIMIT_EXCEEDED}, 0

    try:
        identity = identify_regular_file(target, max_bytes=max_file_bytes)
    except ContentTooLargeError:
        return {"path": relative, "status": CONTENT_TOO_LARGE}, 0
    except ContentSymlinkError:
        return {"path": relative, "status": CONTENT_SYMLINK_UNSUPPORTED}, 0
    except ContentSpecialFileError:
        return {"path": relative, "status": CONTENT_SPECIAL_FILE_REJECTED}, 0
    except ContentChangedDuringReadError:
        return {"path": relative, "status": CONTENT_CHANGED_DURING_READ}, 0
    except ContentMissingError:
        return {"path": relative, "status": CONTENT_MISSING}, 0
    except ContentIdentityError:
        # The message never carries bytes — only the path, the status and the declared bound.
        return {"path": relative, "status": CONTENT_UNREADABLE}, 0

    return (
        {
            "path": relative,
            "status": CONTENT_HASHED,
            "content_digest": identity.content_digest,
            "digest_algorithm": identity.digest_algorithm,
            "size_bytes": identity.size_bytes,
        },
        identity.size_bytes,
    )


def _content_observations(
    root: Path,
    observed_at: str,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_files: int,
) -> list[Observation]:
    """The bytes behind the tracked paths — and an honest account of the ones we did not read.

    Bounds behave differently on purpose, and the difference is the whole point:

    * The **file-count** bound withholds the *inventory itself* (undetermined, value `None`) — the
      substrate's existing idiom for "too many to enumerate". A truncated list would be read as a
      complete one, and nothing in the record would say otherwise.
    * The **byte** bounds keep the inventory and mark the individual entries (`too_large`,
      `aggregate_limit_exceeded`). We *did* enumerate those paths; we just did not read them.

    Either way `content.coverage` survives to say `complete: false`, so an incomplete inventory can
    never be mistaken for a complete one by a consumer that only looks at one of the two.
    """
    entries = _tracked_entries(root)
    tracked = len(entries)

    if tracked > max_files:
        coverage = {
            "tracked_files": tracked,
            "accounted_files": 0,
            "hashed_files": 0,
            "hashed_bytes": 0,
            "complete": False,
        }
        return [
            _undetermined("content.files", observed_at),
            _fact("content.coverage", coverage, observed_at),
        ]

    records: list[dict[str, Any]] = []
    hashed_files = hashed_bytes = 0
    # Sorted, so that which files fall past the aggregate bound is a fact about the repository and
    # not about the order the filesystem happened to hand them back.
    for relative in sorted(entries):
        record, consumed = _content_record(
            root,
            relative,
            entries[relative],
            max_file_bytes=max_file_bytes,
            remaining_bytes=max_total_bytes - hashed_bytes,
        )
        records.append(record)
        if record["status"] == CONTENT_HASHED:
            hashed_files += 1
            hashed_bytes += consumed

    coverage = {
        "tracked_files": tracked,
        "accounted_files": len(records),
        "hashed_files": hashed_files,
        "hashed_bytes": hashed_bytes,
        "complete": hashed_files == tracked,
    }
    return [
        _fact("content.files", records, observed_at),
        _fact("content.coverage", coverage, observed_at),
    ]


# -- entry point ---------------------------------------------------------------------------


def _repository_id(root: Path) -> str:
    """Host-local identity. Never published: a path digest is still a statement about this box.
    Location-independent identity is available to consumers via `git.root_commits`."""
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _default_set_id() -> str:
    return f"lhrepo-{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_repository_root(path: str | Path, allowed_roots: Sequence[str | Path] | None) -> Path:
    """Canonicalize and fail closed. The boundary is checked *first*: a path outside every
    allowed root is never even stat'd."""
    root = resolve_path(path)
    if allowed_roots is not None and not any(
        is_within_boundary(root, boundary) for boundary in allowed_roots
    ):
        raise SourceBoundaryError(f"path resolves outside every allowed root: {root}")
    if not root.exists():
        raise RepositoryNotFoundError(f"repository path does not exist: {root}")
    if not root.is_dir():
        raise RepositoryNotFoundError(f"repository path is not a directory: {root}")

    _require_git()
    toplevel = _git_text(root, "rev-parse", "--show-toplevel")
    if not toplevel:
        raise NotAGitRepositoryError(f"not a git repository: {root}")
    if resolve_path(toplevel) != root:
        raise NotAGitRepositoryError(
            f"not a repository root: {root} belongs to the repository at {toplevel}"
        )
    return root


def observe_repository(
    path: str | Path,
    *,
    allowed_roots: Sequence[str | Path] | None = None,
    profile_id: str | None = None,
    observed_at: str | None = None,
    max_content_file_bytes: int = MAX_CONTENT_FILE_BYTES,
    max_content_total_bytes: int = MAX_CONTENT_TOTAL_BYTES,
    max_content_files: int = MAX_CONTENT_FILES,
) -> RepositoryObservation:
    """Observe one git repository and return a validated `repository_observation.v1` artifact.

    Reads. Never writes, never executes anything it observes, never reaches the network, and
    never returns a partially-observed artifact — every failure raises.

    `allowed_roots`, when supplied, is the ADR-0019 trust boundary: the repository must resolve
    inside one of them (symlink escape included). `observed_at` is injectable so a caller can
    pin the clock; it never affects the digest. The three `max_content_*` bounds are injectable
    for the same reason — a bound that can only be exercised by writing a gigabyte is a bound
    nobody tests.
    """
    root = _resolve_repository_root(path, allowed_roots)
    stamp = observed_at or _now_iso()  # one clock read per run — no intra-artifact skew

    observations = [
        *_filesystem_observations(root, stamp),
        *_git_observations(root, stamp),
        *_content_observations(
            root,
            stamp,
            max_file_bytes=max_content_file_bytes,
            max_total_bytes=max_content_total_bytes,
            max_files=max_content_files,
        ),
    ]
    artifact = RepositoryObservation(
        observation_set_id=_default_set_id(),
        repository_id=_repository_id(root),
        repository_path=str(root),
        observed_at=stamp,
        observations=tuple(sorted(observations, key=lambda o: o.observation_type)),
        profile_id=profile_id,
        lighthouse_version=detect_lighthouse_version(),
    )
    artifact.artifact_digest = artifact.compute_digest()
    artifact.validate()
    return artifact
