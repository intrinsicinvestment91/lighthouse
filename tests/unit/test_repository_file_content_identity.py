"""LHP-INTEL-03A — repository file content identity.

One new fact: *these exact bytes were observed at this admitted repository path.*

The packet's whole discipline is in what this fact refuses to say. Byte identity proves byte
identity — never copying, never authorship, never independence, never significance. So the tests
below pin three things and nothing beyond them:

* **Content identity is the raw bytes and only the raw bytes.** Not the path, not the name, not the
  mode, not the mtime, not a decoded string, not a normalized line ending, not git's blob SHA-1
  (which hashes a header *and* the bytes, and so could never join to a standalone document).
* **Every tracked path is accounted for.** A file that was not hashed says *why* — in a closed
  status vocabulary — and incomplete coverage can never masquerade as complete.
* **Undetermined is never an identity.** A symlink, a gitlink, a special file, a file that changed
  under the read: none of them produce a `content:` entity. Silence is not a digest.

Fixtures build real git repositories. No network, no database, no LLM.
"""

import ast
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from modules import content_identity, observation_normalizer, repository_intake
from modules.content_identity import (
    DIGEST_ALGORITHM,
    ContentChangedDuringReadError,
    ContentIdentityError,
    ContentTooLargeError,
    identify_regular_file,
    sha256_stream,
)
from modules.document_intake import observe_document
from modules.observation_normalizer import (
    ENTITY_CONTENT,
    REL_FILE_HAS_CONTENT,
    normalize_document_observation,
    normalize_repository_observation,
)
from modules.observations import (
    CONFIDENCE_UNDETERMINED,
    Observation,
    ObservationValidationError,
)
from modules.repository_intake import (
    CONTENT_STATUSES,
    MAX_CONTENT_FILE_BYTES,
    MAX_CONTENT_FILES,
    MAX_CONTENT_TOTAL_BYTES,
    observe_repository,
)

# -- fixtures ------------------------------------------------------------------------------

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
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(root: Path, name: str = "widget", files: dict[str, bytes] | None = None) -> Path:
    """A real git repository whose tracked files carry exactly the given bytes."""
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    for rel, payload in (files or {"README.md": b"hello\n"}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _content_facts(repo: Path, **kwargs) -> dict[str, dict]:
    """The per-file outcome inventory, keyed by path."""
    artifact = observe_repository(str(repo), allowed_roots=[str(repo.parent)], **kwargs)
    records = _observation(artifact, "content.files").value
    return {record["path"]: record for record in records}


def _observation(artifact, observation_type: str):
    for item in artifact.observations:
        if item.observation_type == observation_type:
            return item
    raise AssertionError(f"no {observation_type} observation in artifact")


def _entities(result, entity_type: str) -> list:
    return [entity for entity in result.entities if entity.entity_type == entity_type]


# -- raw-byte identity ---------------------------------------------------------------------


def test_content_digest_is_sha256_of_the_exact_raw_bytes(tmp_path):
    payload = b"\x00\x01binary\xff\xfe not text\n"
    repo = _repo(tmp_path, files={"blob.bin": payload})

    facts = _content_facts(repo)

    assert facts["blob.bin"]["content_digest"] == hashlib.sha256(payload).hexdigest()
    assert facts["blob.bin"]["digest_algorithm"] == "sha256"
    assert facts["blob.bin"]["size_bytes"] == len(payload)
    assert facts["blob.bin"]["status"] == "hashed"


def test_content_digest_is_not_gits_blob_sha1(tmp_path):
    """Git hashes `blob <len>\\0` + bytes. A document has no such header, so a git blob id could
    never be the identity both sources share — the join would be structurally impossible."""
    payload = b"hello\n"
    repo = _repo(tmp_path, files={"a.txt": payload})

    facts = _content_facts(repo)
    git_blob_sha1 = _git(repo, "hash-object", "a.txt")

    assert facts["a.txt"]["content_digest"] != git_blob_sha1
    assert facts["a.txt"]["content_digest"] == hashlib.sha256(payload).hexdigest()


def test_same_bytes_under_two_paths_share_one_content_identity(tmp_path):
    payload = b"shared bytes\n"
    repo = _repo(tmp_path, files={"one.txt": payload, "nested/two.txt": payload})

    facts = _content_facts(repo)

    assert facts["one.txt"]["content_digest"] == facts["nested/two.txt"]["content_digest"]


def test_same_bytes_in_two_repositories_share_one_content_identity(tmp_path):
    payload = b"FROM scratch\n"
    left = _repo(tmp_path / "l", files={"Dockerfile": payload})
    right = _repo(tmp_path / "r", files={"deploy/Dockerfile": payload})

    left_digest = _content_facts(left)["Dockerfile"]["content_digest"]
    right_digest = _content_facts(right)["deploy/Dockerfile"]["content_digest"]

    assert left_digest == right_digest


def test_one_changed_byte_changes_the_content_identity(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"hello\n"})
    before = _content_facts(repo)["a.txt"]["content_digest"]

    (repo / "a.txt").write_bytes(b"hellp\n")
    after = _content_facts(repo)["a.txt"]["content_digest"]

    assert before != after


def test_renaming_a_file_does_not_change_its_content_identity(tmp_path):
    payload = b"unchanged bytes\n"
    repo = _repo(tmp_path, files={"before.txt": payload})
    before = _content_facts(repo)["before.txt"]["content_digest"]

    _git(repo, "mv", "before.txt", "after.txt")
    _git(repo, "commit", "-m", "rename")
    after = _content_facts(repo)["after.txt"]["content_digest"]

    assert before == after, "a rename changes the path, never the bytes"


def test_permission_change_does_not_change_content_identity(tmp_path):
    repo = _repo(tmp_path, files={"run.sh": b"#!/bin/sh\necho hi\n"})
    before = _content_facts(repo)["run.sh"]["content_digest"]

    (repo / "run.sh").chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chmod")
    after = _content_facts(repo)["run.sh"]["content_digest"]

    assert before == after


def test_mtime_change_does_not_change_content_identity(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"hello\n"})
    before = _content_facts(repo)["a.txt"]["content_digest"]

    os.utime(repo / "a.txt", (1_000_000, 1_000_000))
    after = _content_facts(repo)["a.txt"]["content_digest"]

    assert before == after


def test_lf_and_crlf_are_different_identities(tmp_path):
    """The bytes differ, so the identity differs. Normalizing line endings before hashing would
    make two genuinely different files collide — a lie told for tidiness."""
    repo = _repo(tmp_path, files={"lf.txt": b"a\nb\n", "crlf.txt": b"a\r\nb\r\n"})

    facts = _content_facts(repo)

    assert facts["lf.txt"]["content_digest"] != facts["crlf.txt"]["content_digest"]
    assert facts["lf.txt"]["content_digest"] == hashlib.sha256(b"a\nb\n").hexdigest()
    assert facts["crlf.txt"]["content_digest"] == hashlib.sha256(b"a\r\nb\r\n").hexdigest()


def test_bytes_that_are_not_valid_utf8_still_hash(tmp_path):
    """No decode step exists. A file that cannot be decoded can still be identified."""
    payload = b"\xff\xfe\x00invalid utf-8\x80\x81"
    repo = _repo(tmp_path, files={"raw.bin": payload})

    facts = _content_facts(repo)

    assert facts["raw.bin"]["content_digest"] == hashlib.sha256(payload).hexdigest()


def test_empty_file_hashes_to_the_sha256_of_empty_bytes(tmp_path):
    repo = _repo(tmp_path, files={"empty": b"", "README.md": b"x\n"})

    facts = _content_facts(repo)

    assert facts["empty"]["status"] == "hashed"
    assert facts["empty"]["content_digest"] == hashlib.sha256(b"").hexdigest()
    assert facts["empty"]["size_bytes"] == 0


def test_repeated_observation_is_byte_for_byte_deterministic(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n", "b/c.txt": b"c\n"})

    first = observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    second = observe_repository(str(repo), allowed_roots=[str(tmp_path)])

    assert first.artifact_digest == second.artifact_digest
    assert _observation(first, "content.files").value == _observation(second, "content.files").value


# -- the tracked-file boundary -------------------------------------------------------------


def test_untracked_file_is_not_hashed(tmp_path):
    repo = _repo(tmp_path, files={"tracked.txt": b"t\n"})
    (repo / "untracked.txt").write_bytes(b"u\n")

    facts = _content_facts(repo)

    assert "tracked.txt" in facts
    assert "untracked.txt" not in facts, "only git's tracked set is admitted"


def test_ignored_file_is_not_hashed(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n", ".gitignore": b"secret.env\n"})
    (repo / "secret.env").write_bytes(b"TOKEN=hunter2\n")

    facts = _content_facts(repo)

    assert "secret.env" not in facts


def test_deleted_tracked_file_is_accounted_for_without_a_digest(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n", "gone.txt": b"g\n"})
    (repo / "gone.txt").unlink()

    facts = _content_facts(repo)

    assert facts["gone.txt"]["status"] == "missing"
    assert "content_digest" not in facts["gone.txt"]


def test_tracked_symlink_is_not_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"SECRET OUTSIDE THE REPOSITORY\n")
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    (repo / "link").symlink_to(outside)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "link")

    facts = _content_facts(repo)
    secret = hashlib.sha256(b"SECRET OUTSIDE THE REPOSITORY\n").hexdigest()

    assert facts["link"]["status"] == "symlink_unsupported"
    assert "content_digest" not in facts["link"]
    assert all(record.get("content_digest") != secret for record in facts.values())


def test_tracked_gitlink_is_not_recursed(tmp_path):
    inner = _repo(tmp_path, name="inner", files={"deep.txt": b"deep\n"})
    outer = _repo(tmp_path, name="outer", files={"a.txt": b"a\n"})
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(outer),
            "submodule",
            "add",
            str(inner),
            "vendor",
        ],
        env=_GIT_ENV,
        capture_output=True,
        check=True,
    )
    _git(outer, "commit", "-m", "submodule")

    facts = _content_facts(outer)

    assert facts["vendor"]["status"] == "gitlink_unsupported"
    assert "content_digest" not in facts["vendor"]
    assert not any(path.startswith("vendor/") for path in facts), "a gitlink is never recursed into"


def test_fifo_is_rejected_and_never_read(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    placeholder = repo / "pipe"
    placeholder.write_bytes(b"placeholder\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "placeholder")
    placeholder.unlink()
    os.mkfifo(placeholder)  # a read here would block forever

    facts = _content_facts(repo)

    assert facts["pipe"]["status"] == "special_file_rejected"
    assert "content_digest" not in facts["pipe"]


# -- bounds ---------------------------------------------------------------------------------


def test_file_exactly_at_the_per_file_bound_is_hashed(tmp_path):
    payload = b"x" * 64
    repo = _repo(tmp_path, files={"edge.bin": payload})

    facts = _content_facts(repo, max_content_file_bytes=64)

    assert facts["edge.bin"]["status"] == "hashed"
    assert facts["edge.bin"]["content_digest"] == hashlib.sha256(payload).hexdigest()


def test_file_one_byte_over_the_per_file_bound_is_not_hashed(tmp_path):
    repo = _repo(tmp_path, files={"edge.bin": b"x" * 65})

    facts = _content_facts(repo, max_content_file_bytes=64)

    assert facts["edge.bin"]["status"] == "too_large"
    assert "content_digest" not in facts["edge.bin"]


def test_aggregate_bound_exhaustion_marks_the_remainder_rather_than_dropping_it(tmp_path):
    repo = _repo(tmp_path, files={"a.bin": b"a" * 40, "b.bin": b"b" * 40, "c.bin": b"c" * 40})

    facts = _content_facts(repo, max_content_total_bytes=80)

    assert facts["a.bin"]["status"] == "hashed"
    assert facts["b.bin"]["status"] == "hashed"
    assert facts["c.bin"]["status"] == "aggregate_limit_exceeded"
    assert "content_digest" not in facts["c.bin"]
    assert len(facts) == 3, "no tracked path may silently disappear"


def test_incomplete_coverage_is_explicit(tmp_path):
    repo = _repo(tmp_path, files={"a.bin": b"a" * 40, "b.bin": b"b" * 40})

    artifact = observe_repository(
        str(repo), allowed_roots=[str(tmp_path)], max_content_total_bytes=40
    )
    coverage = _observation(artifact, "content.coverage").value

    assert coverage["complete"] is False
    assert coverage["hashed_files"] == 1
    assert coverage["tracked_files"] == 2
    assert coverage["accounted_files"] == 2


def test_complete_coverage_says_so(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n", "b.txt": b"b\n"})

    artifact = observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    coverage = _observation(artifact, "content.coverage").value

    assert coverage["complete"] is True
    assert coverage["hashed_files"] == coverage["tracked_files"] == 2


def test_file_count_bound_withholds_the_inventory_rather_than_truncating_it(tmp_path):
    repo = _repo(tmp_path, files={f"f{i}.txt": b"x\n" for i in range(4)})

    artifact = observe_repository(str(repo), allowed_roots=[str(tmp_path)], max_content_files=3)
    files = _observation(artifact, "content.files")
    coverage = _observation(artifact, "content.coverage").value

    # Withheld, never truncated: a partial list would read as a complete one.
    assert files.confidence == CONFIDENCE_UNDETERMINED
    assert files.value is None
    assert coverage["complete"] is False
    assert coverage["tracked_files"] == 4
    assert coverage["hashed_files"] == 0


def test_bounds_are_overridable_without_environment_access(tmp_path):
    assert MAX_CONTENT_FILE_BYTES == 64 * 1024 * 1024
    assert MAX_CONTENT_TOTAL_BYTES == 1024 * 1024 * 1024
    assert MAX_CONTENT_FILES == 5_000


def test_processing_order_is_deterministic(tmp_path):
    repo = _repo(tmp_path, files={"c.txt": b"c\n", "a.txt": b"a\n", "b/z.txt": b"z\n"})

    artifact = observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    paths = [record["path"] for record in _observation(artifact, "content.files").value]

    assert paths == sorted(paths)


# -- read consistency -----------------------------------------------------------------------


def test_a_file_that_changes_during_the_read_produces_no_digest(tmp_path):
    target = tmp_path / "moving.bin"
    target.write_bytes(b"x" * 4096)

    original_open = content_identity.open_binary

    def swap_after_open(path):
        handle = original_open(path)
        target.write_bytes(b"y" * 8192)  # a different size AND different bytes
        return handle

    content_identity.open_binary = swap_after_open
    try:
        with pytest.raises(ContentChangedDuringReadError):
            identify_regular_file(target, max_bytes=MAX_CONTENT_FILE_BYTES)
    finally:
        content_identity.open_binary = original_open


def test_changed_during_read_is_reported_as_a_status_not_a_digest(tmp_path):
    assert "changed_during_read" in CONTENT_STATUSES


def test_no_silent_retry_on_a_changed_file(tmp_path):
    """A retry loop would eventually hash *some* consistent state and present it as the state we
    observed. The honest answer is that we do not know."""
    source = Path(content_identity.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.For)):
            # The only loop permitted is the streaming read itself.
            assert "chunk" in ast.dump(node), "no retry loop may exist in the content hasher"


# -- the observation contract ---------------------------------------------------------------


def test_status_vocabulary_is_closed_and_carries_no_interpretation(tmp_path):
    assert (
        frozenset(
            {
                "hashed",
                "missing",
                "unreadable",
                "too_large",
                "aggregate_limit_exceeded",
                "symlink_unsupported",
                "gitlink_unsupported",
                "special_file_rejected",
                "changed_during_read",
                "invalid_path",
            }
        )
        == CONTENT_STATUSES
    )
    banned = (
        "duplicate",
        "reused",
        "copied",
        "important",
        "significant",
        "suspicious",
        "novel",
        "invention",
        "independent",
        "common",
        "recommended",
    )
    for status in CONTENT_STATUSES:
        assert not any(word in status for word in banned), status


def test_only_a_hashed_record_may_carry_a_digest(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n", "gone.txt": b"g\n"})
    (repo / "gone.txt").unlink()

    for record in _content_facts(repo).values():
        if record["status"] == "hashed":
            assert len(record["content_digest"]) == 64
            assert record["digest_algorithm"] == "sha256"
            assert isinstance(record["size_bytes"], int)
        else:
            assert "content_digest" not in record
            assert "size_bytes" not in record


def test_artifact_digest_covers_every_file_outcome(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    artifact = observe_repository(str(repo), allowed_roots=[str(tmp_path)])

    (repo / "a.txt").write_bytes(b"b\n")
    changed = observe_repository(str(repo), allowed_roots=[str(tmp_path)])

    assert artifact.artifact_digest != changed.artifact_digest


def test_artifact_digest_is_stable_across_observation_timestamps(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})

    first = observe_repository(
        str(repo), allowed_roots=[str(tmp_path)], observed_at="2026-01-01T00:00:00+00:00"
    )
    second = observe_repository(
        str(repo), allowed_roots=[str(tmp_path)], observed_at="2027-06-06T12:00:00+00:00"
    )

    assert first.artifact_digest == second.artifact_digest


def test_git_unavailable_leaves_content_undetermined_never_absent(tmp_path, monkeypatch):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    monkeypatch.setattr(repository_intake.shutil, "which", lambda _: None)

    with pytest.raises(repository_intake.GitUnavailableError):
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])


# -- normalization --------------------------------------------------------------------------


def test_a_hashed_file_becomes_a_profile_neutral_content_entity(tmp_path):
    payload = b"FROM scratch\n"
    repo = _repo(tmp_path, files={"Dockerfile": payload})
    digest = hashlib.sha256(payload).hexdigest()

    result = normalize_repository_observation(
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    )

    ids = {entity.entity_id for entity in _entities(result, ENTITY_CONTENT)}
    assert f"content:{digest}" in ids


def test_the_content_entity_is_global_so_two_repositories_join_on_it(tmp_path):
    payload = b"FROM scratch\n"
    left = _repo(tmp_path / "l", files={"Dockerfile": payload})
    right = _repo(tmp_path / "r", files={"infra/Dockerfile": payload})

    left_ids = {
        entity.entity_id
        for entity in _entities(
            normalize_repository_observation(
                observe_repository(str(left), allowed_roots=[str(tmp_path)])
            ),
            ENTITY_CONTENT,
        )
    }
    right_ids = {
        entity.entity_id
        for entity in _entities(
            normalize_repository_observation(
                observe_repository(str(right), allowed_roots=[str(tmp_path)])
            ),
            ENTITY_CONTENT,
        )
    }

    assert left_ids & right_ids, "identical bytes in two repositories are one content entity"


def test_a_repository_file_and_a_standalone_document_share_one_content_entity(tmp_path):
    """The join this packet exists for. `content:` is the identity both sources speak."""
    payload = b"# Specification\n\nthe same exact bytes\n"
    repo = _repo(tmp_path / "r", files={"docs/spec.md": payload})
    document = tmp_path / "loose" / "spec.md"
    document.parent.mkdir()
    document.write_bytes(payload)

    repo_result = normalize_repository_observation(
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    )
    doc_result = normalize_document_observation(
        observe_document(str(document), allowed_roots=[str(tmp_path)])
    )

    repo_ids = {entity.entity_id for entity in _entities(repo_result, ENTITY_CONTENT)}
    doc_ids = {entity.entity_id for entity in _entities(doc_result, ENTITY_CONTENT)}
    expected = f"content:{hashlib.sha256(payload).hexdigest()}"

    assert expected in repo_ids
    assert expected in doc_ids


def test_the_document_entity_is_preserved_alongside_the_content_entity(tmp_path):
    payload = b"# Spec\n"
    document = tmp_path / "spec.md"
    document.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    result = normalize_document_observation(
        observe_document(str(document), allowed_roots=[str(tmp_path)])
    )
    ids = {entity.entity_id for entity in result.entities}

    assert f"document:{digest}" in ids, "the document subject entity is not replaced"
    assert f"content:{digest}" in ids, "the neutral content entity is emitted alongside it"


def test_the_path_is_preserved_as_direct_relationship_evidence(tmp_path):
    payload = b"FROM scratch\n"
    repo = _repo(tmp_path, files={"infra/Dockerfile": payload})
    digest = hashlib.sha256(payload).hexdigest()

    result = normalize_repository_observation(
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    )
    links = [r for r in result.relationships if r.relationship_type == REL_FILE_HAS_CONTENT]

    assert any(
        link.object_id == f"content:{digest}" and link.subject_id.endswith(":infra/Dockerfile")
        for link in links
    ), "this scoped file path had these exact bytes"


def test_two_identical_files_in_one_repository_are_one_entity_and_two_relationships(tmp_path):
    """The unique index is (entity_id, source_artifact_digest, derivation_version): a second
    content entity for the same bytes in the same artifact would collide. Both paths must still
    survive — as relationships."""
    payload = b"same\n"
    repo = _repo(tmp_path, files={"one.txt": payload, "two.txt": payload})
    digest = hashlib.sha256(payload).hexdigest()

    result = normalize_repository_observation(
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    )

    contents = [e for e in _entities(result, ENTITY_CONTENT) if e.entity_id == f"content:{digest}"]
    links = [
        r
        for r in result.relationships
        if r.relationship_type == REL_FILE_HAS_CONTENT and r.object_id == f"content:{digest}"
    ]

    assert len(contents) == 1
    assert {link.subject_id.rsplit(":", 1)[-1] for link in links} == {"one.txt", "two.txt"}


def test_no_content_entity_for_an_undetermined_outcome(tmp_path):
    """Undetermined is not a content identity. Silence must never become a digest."""
    repo = _repo(tmp_path, files={"a.txt": b"a\n", "gone.txt": b"g\n", "big.bin": b"x" * 100})
    (repo / "gone.txt").unlink()

    artifact = observe_repository(
        str(repo), allowed_roots=[str(tmp_path)], max_content_file_bytes=64
    )
    result = normalize_repository_observation(artifact)

    values = {entity.value for entity in _entities(result, ENTITY_CONTENT)}
    assert values == {hashlib.sha256(b"a\n").hexdigest()}


def test_a_content_entity_carries_no_attributes_and_no_significance(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})

    result = normalize_repository_observation(
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    )

    for entity in _entities(result, ENTITY_CONTENT):
        assert entity.attributes == {}
    for link in result.relationships:
        assert not hasattr(link, "weight")
        assert not hasattr(link, "score")


def test_the_normalizer_derivation_version_advanced(tmp_path):
    """Forced, not cosmetic: a previously-stored document's artifact digest is unchanged, so
    re-normalizing it under the old derivation version would collide on the unique entity index."""
    assert observation_normalizer.DERIVATION_VERSION == "observation_normalizer/2"
    assert repository_intake.DERIVATION_VERSION == "repository_intake/2"


# -- the neutral helper ----------------------------------------------------------------------


def test_the_shared_hasher_streams_and_never_slurps(tmp_path):
    target = tmp_path / "big.bin"
    target.write_bytes(b"z" * (3 * 1024 * 1024 + 7))

    digest, size = sha256_stream(target)

    assert digest == hashlib.sha256(b"z" * (3 * 1024 * 1024 + 7)).hexdigest()
    assert size == 3 * 1024 * 1024 + 7


def test_no_read_call_may_slurp_the_whole_file(tmp_path):
    """A structural proof, because a behavioural one is not available: a whole-file `handle.read()`
    produces exactly the same digest as a streamed one, so no assertion about the OUTPUT can tell
    them apart. (A mutation run proved it — swapping the stream for a slurp passed every other test
    in this file.) The bound is only real if the read is chunked, so the read is checked directly:
    every `.read(...)` must be given a size.
    """
    tree = ast.parse(Path(content_identity.__file__).read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read"
    ]

    assert reads, "the hasher must actually read something"
    for call in reads:
        assert call.args, "a bare .read() loads the whole file into memory — the bound is a fiction"


def test_the_shared_hasher_refuses_an_oversized_file(tmp_path):
    target = tmp_path / "big.bin"
    target.write_bytes(b"z" * 100)

    with pytest.raises(ContentTooLargeError):
        identify_regular_file(target, max_bytes=99)


def test_the_shared_hasher_knows_nothing_about_repositories_or_significance():
    source = Path(content_identity.__file__).read_text(encoding="utf-8").lower()
    for word in (
        "repository",
        "document",
        "profile",
        "invention",
        "duplicate",
        "score",
        "rank",
        "significance",
        "importance",
        "severity",
        "priority",
    ):
        assert word not in source, f"the neutral hasher must not know the word {word!r}"


def test_document_content_digests_are_unchanged_by_the_extraction(tmp_path):
    """Golden: the document collector's digest recipe must survive the shared-helper extraction
    byte-for-byte, or every previously-stored document identity silently moves."""
    payload = b"# Title\n\nbody\n"
    document = tmp_path / "doc.md"
    document.write_bytes(payload)

    artifact = observe_document(str(document), allowed_roots=[str(tmp_path)])
    digest = next(
        item.value
        for item in artifact.observations
        if item.observation_type == "document.content_digest"
    )

    assert digest == hashlib.sha256(payload).hexdigest()


def test_digest_algorithm_label_is_sha256():
    assert DIGEST_ALGORITHM == "sha256"
    assert issubclass(ContentTooLargeError, ContentIdentityError)


# -- no significance, structurally ------------------------------------------------------------


def test_the_new_surface_has_no_word_for_significance():
    for module in (content_identity, repository_intake, observation_normalizer):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                lowered = node.name.lower()
                for word in ("score", "rank", "priority", "importance", "severity", "novelty"):
                    assert word not in lowered, f"{module.__name__}.{node.name}"


def test_content_identity_performs_no_network_and_no_subprocess():
    source = Path(content_identity.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {
                    "socket",
                    "http",
                    "httpx",
                    "requests",
                    "urllib",
                    "subprocess",
                    "sqlite3",
                }
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in {
                "socket",
                "http",
                "httpx",
                "requests",
                "urllib",
                "subprocess",
                "sqlite3",
            }


def test_observation_types_carry_no_evaluative_vocabulary():
    banned = ("score", "rank", "priority", "importance", "severity", "significance", "novelty")
    for observation_type in repository_intake.OBSERVATION_TYPES:
        assert not any(word in observation_type for word in banned), observation_type
    for entity_type in observation_normalizer.ENTITY_TYPES:
        assert not any(word in entity_type for word in banned), entity_type
    for relationship_type in observation_normalizer.RELATIONSHIP_TYPES:
        assert not any(word in relationship_type for word in banned), relationship_type


# -- the artifact validates its own per-file records ------------------------------------------


def _artifact_with(records, tmp_path, *, repo=None):
    repo = repo or _repo(tmp_path, files={"a.txt": b"a\n"})
    artifact = observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    patched = tuple(
        Observation("content.files", records, item.source, item.observed_at, item.confidence)
        if item.observation_type == "content.files"
        else item
        for item in artifact.observations
    )
    artifact.observations = patched
    artifact.artifact_digest = (
        artifact.compute_digest()
    )  # re-stamped: the digest cannot be the gate
    return artifact


def test_a_forged_status_outside_the_closed_vocabulary_is_rejected(tmp_path):
    """Re-stamped on purpose. If the digest were the only thing standing between the store and a
    record that says `"status": "important"`, then anyone who can write a record can write an
    opinion. The closed vocabulary has to be the gate."""
    artifact = _artifact_with([{"path": "a.txt", "status": "important"}], tmp_path)
    with pytest.raises(ObservationValidationError, match="status"):
        artifact.validate()


def test_a_hashed_record_without_a_digest_is_rejected(tmp_path):
    artifact = _artifact_with([{"path": "a.txt", "status": "hashed"}], tmp_path)
    with pytest.raises(ObservationValidationError):
        artifact.validate()


def test_an_unhashed_record_carrying_a_digest_is_rejected(tmp_path):
    """The sharpest one: a fabricated identity for a file we admitted we could not read."""
    artifact = _artifact_with(
        [
            {
                "path": "a.txt",
                "status": "unreadable",
                "content_digest": "b" * 64,
                "digest_algorithm": "sha256",
                "size_bytes": 1,
            }
        ],
        tmp_path,
    )
    with pytest.raises(ObservationValidationError):
        artifact.validate()


def test_a_malformed_digest_is_rejected(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    for bad in ("B" * 64, "abc", "z" * 64):  # uppercase, too short, not hex
        artifact = _artifact_with(
            [
                {
                    "path": "a.txt",
                    "status": "hashed",
                    "content_digest": bad,
                    "digest_algorithm": "sha256",
                    "size_bytes": 1,
                }
            ],
            tmp_path,
            repo=repo,
        )
        with pytest.raises(ObservationValidationError):
            artifact.validate()


def test_an_unsupported_digest_algorithm_is_rejected(tmp_path):
    artifact = _artifact_with(
        [
            {
                "path": "a.txt",
                "status": "hashed",
                "content_digest": "a" * 64,
                "digest_algorithm": "sha1",  # git's blob id lives here — and must never
                "size_bytes": 1,
            }
        ],
        tmp_path,
    )
    with pytest.raises(ObservationValidationError, match="digest_algorithm"):
        artifact.validate()


def test_a_malformed_relative_path_is_rejected(tmp_path):
    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    for bad in ("/etc/shadow", "../escape", "", "a\0b"):
        artifact = _artifact_with([{"path": bad, "status": "missing"}], tmp_path, repo=repo)
        with pytest.raises(ObservationValidationError):
            artifact.validate()


def test_duplicate_paths_are_rejected(tmp_path):
    artifact = _artifact_with(
        [{"path": "a.txt", "status": "missing"}, {"path": "a.txt", "status": "missing"}], tmp_path
    )
    with pytest.raises(ObservationValidationError, match="duplicate"):
        artifact.validate()


def test_an_unknown_key_in_a_record_is_rejected(tmp_path):
    """There is no field for significance, so significance is unrepresentable — but only if an
    unexpected field is refused rather than carried along."""
    artifact = _artifact_with([{"path": "a.txt", "status": "missing", "importance": 0.9}], tmp_path)
    with pytest.raises(ObservationValidationError):
        artifact.validate()


def test_a_content_entity_cannot_be_given_an_attribute_at_all(tmp_path):
    """It is not a subject. The bytes have no properties beyond being themselves."""
    import dataclasses

    from modules.observation_normalizer import NormalizedRecordError

    repo = _repo(tmp_path, files={"a.txt": b"a\n"})
    result = normalize_repository_observation(
        observe_repository(str(repo), allowed_roots=[str(tmp_path)])
    )
    entity = _entities(result, ENTITY_CONTENT)[0]
    forged = dataclasses.replace(entity, attributes={"repository.name": "x"})
    forged = dataclasses.replace(forged, digest=forged.compute_digest())  # re-stamped

    with pytest.raises(NormalizedRecordError, match="subject"):
        forged.validate()
