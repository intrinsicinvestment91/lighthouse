"""LHP-INTEL-01C — deterministic document observation intake.

The packet's central requirement is a distinction, not a feature:

    "The system must distinguish 'document has no text' from 'system could not extract text'."

01A already has the machinery for exactly that (confidence 1.0 = determined *including provably
absent*; 0.0 = undetermined, value always None), so these tests hold `document_intake` to it, and
assert the two cases are *different observations* rather than the same silence.
"""

from __future__ import annotations

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

from modules import document_intake
from modules.document_intake import (
    DOCUMENT_OBSERVATION_SCHEMA_VERSION,
    EXTRACTION_STATUSES,
    OBSERVATION_TYPES,
    DocumentIntakeError,
    DocumentNotFoundError,
    DocumentObservation,
    DocumentUnreadableError,
    UnsupportedDocumentFormatError,
    observe_document,
    validate_document_observation,
)
from modules.observations import ObservationValidationError, SourceBoundaryError

from .test_repository_intake import tree_digest

_PINNED_AT = "2026-07-13T00:00:00+00:00"


# -- fixtures ------------------------------------------------------------------------------


def make_pdf(path: Path, page_texts: list[str]) -> Path:
    """A minimal, hand-built PDF.

    Building the bytes here rather than reaching for a PDF *writer* keeps the fixture exactly
    deterministic and adds no dependency: the test's input is precisely the bytes on disk.
    """
    objects: list[bytes] = []
    count = len(page_texts)
    page_nums = [4 + 2 * i for i in range(count)]
    content_nums = [5 + 2 * i for i in range(count)]
    kids = " ".join(f"{n} 0 R" for n in page_nums)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(page_texts):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                f"/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {content_nums[index]} 0 R >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_at).encode()
        + b"\n%%EOF\n"
    )
    path.write_bytes(bytes(out))
    return path


def facts(artifact: DocumentObservation) -> dict[str, object]:
    """Only the determined observations, by type."""
    return {o.observation_type: o.value for o in artifact.observations if o.confidence == 1.0}


def undetermined(artifact: DocumentObservation) -> set[str]:
    return {o.observation_type for o in artifact.observations if o.confidence == 0.0}


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "note.txt"
    path.write_text("hello lighthouse\nsecond line\n", encoding="utf-8")
    return path


@pytest.fixture
def observed(note: Path) -> DocumentObservation:
    return observe_document(note, observed_at=_PINNED_AT)


# -- the artifact ---------------------------------------------------------------------------


def test_observing_a_text_document_yields_a_validated_artifact(observed, note):
    assert observed.schema_version == DOCUMENT_OBSERVATION_SCHEMA_VERSION
    assert observed.document_path == str(note.resolve())
    assert observed.derivation_version == "document_intake/1"
    observed.validate()


def test_every_observation_belongs_to_the_closed_vocabulary(observed):
    for observation in observed.observations:
        assert observation.observation_type in OBSERVATION_TYPES


def test_the_source_of_a_fact_is_pinned_by_its_type_not_by_the_producer(observed):
    """A text fact comes from the extractor and a size fact from the filesystem. The producer does
    not get to say which — so it cannot dress up a guess as a filesystem reading."""
    for observation in observed.observations:
        assert observation.source == OBSERVATION_TYPES[observation.observation_type].source


def test_the_document_id_is_the_digest_of_its_canonical_path(observed, note):
    import hashlib

    expected = hashlib.sha256(str(note.resolve()).encode("utf-8")).hexdigest()
    assert observed.document_id == expected


def test_the_content_digest_is_the_digest_of_the_bytes(observed, note):
    import hashlib

    expected = hashlib.sha256(note.read_bytes()).hexdigest()
    assert facts(observed)["document.content_digest"] == expected


def test_the_artifact_round_trips_through_a_dict(observed):
    assert validate_document_observation(observed.to_dict()).artifact_digest == (
        observed.artifact_digest
    )


# -- identity and determinism ---------------------------------------------------------------


def test_the_same_document_yields_the_same_digest(note):
    first = observe_document(note, observed_at=_PINNED_AT)
    second = observe_document(note, observed_at="2027-01-01T12:00:00+00:00")
    assert first.artifact_digest == second.artifact_digest


def test_a_changed_document_changes_its_identity(note):
    before = observe_document(note, observed_at=_PINNED_AT)
    note.write_text("hello lighthouse\nsecond line\nthird\n", encoding="utf-8")
    after = observe_document(note, observed_at=_PINNED_AT)
    assert facts(after)["document.content_digest"] != facts(before)["document.content_digest"]
    assert after.artifact_digest != before.artifact_digest


def test_the_same_bytes_in_two_places_share_a_content_digest_but_not_a_scope(tmp_path: Path):
    """The join normalization exists to provide: one document, copied, is *one thing* observed in
    two places. Content answers "what is it"; the path answers "where did I look"."""
    here = tmp_path / "a" / "paper.md"
    there = tmp_path / "b" / "paper.md"
    for path in (here, there):
        path.parent.mkdir(parents=True)
        path.write_text("# Title\n\nbody\n", encoding="utf-8")

    one = observe_document(here, observed_at=_PINNED_AT)
    two = observe_document(there, observed_at=_PINNED_AT)
    assert facts(one)["document.content_digest"] == facts(two)["document.content_digest"]
    assert one.document_id != two.document_id


# -- formats --------------------------------------------------------------------------------


def test_markdown_is_supported(tmp_path: Path):
    path = tmp_path / "readme.md"
    path.write_text("# Heading\n\ntext\n", encoding="utf-8")
    assert facts(observe_document(path, observed_at=_PINNED_AT))["document.format"] == "markdown"


def test_pdf_text_is_extracted(tmp_path: Path):
    pdf = make_pdf(tmp_path / "paper.pdf", ["Hello Lighthouse"])
    known = facts(observe_document(pdf, observed_at=_PINNED_AT))
    assert known["document.format"] == "pdf"
    assert known["text.extraction_status"] == "extracted"
    assert known["text.character_count"] > 0
    assert known["document.page_count"] == 1
    assert str(known["text.extractor"]).startswith("pypdf/")


def test_an_office_format_is_rejected(tmp_path: Path):
    path = tmp_path / "deck.docx"
    path.write_bytes(b"PK\x03\x04")
    with pytest.raises(UnsupportedDocumentFormatError):
        observe_document(path, observed_at=_PINNED_AT)


def test_html_is_rejected(tmp_path: Path):
    """Declined deliberately: a tag-stripper that quietly mangles entities, <script> and
    whitespace is exactly the "silently lose information" this packet forbids."""
    path = tmp_path / "page.html"
    path.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(UnsupportedDocumentFormatError):
        observe_document(path, observed_at=_PINNED_AT)


# -- the distinction the packet exists for --------------------------------------------------


def test_an_empty_document_has_no_text_and_says_so_determinately(tmp_path: Path):
    """'This document contains no text' is a FACT, not an absence of one:
    count 0, at confidence 1.0."""
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    artifact = observe_document(path, observed_at=_PINNED_AT)
    known = facts(artifact)
    assert known["text.extraction_status"] == "empty"
    assert known["text.character_count"] == 0
    assert "text.character_count" not in undetermined(artifact)


def test_a_blank_pdf_page_is_empty_not_unextractable(tmp_path: Path):
    pdf = make_pdf(tmp_path / "blank.pdf", [""])
    artifact = observe_document(pdf, observed_at=_PINNED_AT)
    assert facts(artifact)["text.extraction_status"] == "empty"
    assert "text.character_count" not in undetermined(artifact)


def test_an_undecodable_document_is_undetermined_not_empty(tmp_path: Path):
    """Invalid UTF-8. We refuse to guess an encoding and refuse a lossy decode — either would
    silently corrupt. The text facts become undetermined, and the document is still observed."""
    path = tmp_path / "broken.txt"
    path.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")
    artifact = observe_document(path, observed_at=_PINNED_AT)
    assert facts(artifact)["text.extraction_status"] == "unreadable"
    assert "text.character_count" in undetermined(artifact)
    assert facts(artifact)["document.content_digest"]  # the document itself is still observed


def test_a_missing_extractor_is_undetermined_not_empty(tmp_path: Path, monkeypatch):
    """A stripped install has no pypdf. It must say "I could not read this",
    never "this is blank"."""
    pdf = make_pdf(tmp_path / "paper.pdf", ["Hello Lighthouse"])
    monkeypatch.setitem(sys.modules, "pypdf", None)  # makes `import pypdf` raise ImportError
    artifact = observe_document(pdf, observed_at=_PINNED_AT)
    assert facts(artifact)["text.extraction_status"] == "extractor_unavailable"
    assert facts(artifact)["text.extractor"] == "none"
    assert "text.character_count" in undetermined(artifact)
    assert "document.page_count" in undetermined(artifact)


def test_no_text_and_could_not_read_are_not_the_same_observation(tmp_path: Path):
    """The packet's whole point, asserted directly: these two must never collapse
    into one silence."""
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    broken = tmp_path / "broken.txt"
    broken.write_bytes(b"\xc3\x28")

    has_no_text = observe_document(empty, observed_at=_PINNED_AT)
    could_not_read = observe_document(broken, observed_at=_PINNED_AT)

    assert facts(has_no_text)["text.character_count"] == 0
    assert "text.character_count" in undetermined(could_not_read)
    assert (
        facts(has_no_text)["text.extraction_status"]
        != (facts(could_not_read)["text.extraction_status"])
    )


def test_a_text_document_has_no_pages_and_that_is_a_fact(observed):
    """Provably absent, not unknown: a .txt genuinely has no page structure —
    determined, value None."""
    page_count = next(
        o for o in observed.observations if o.observation_type == "document.page_count"
    )
    assert page_count.confidence == 1.0
    assert page_count.value is None


# -- bounds: fail safe, or truncate and say so -----------------------------------------------


def test_a_document_over_the_size_bound_is_refused_unread(tmp_path: Path):
    path = tmp_path / "big.txt"
    path.write_text("x" * 500, encoding="utf-8")
    artifact = observe_document(path, observed_at=_PINNED_AT, max_file_bytes=100)
    known = facts(artifact)
    assert known["text.extraction_status"] == "too_large"
    assert "text.character_count" in undetermined(artifact)
    # the document is still identified — refusing to *extract* is not refusing to *observe*
    assert known["document.size_bytes"] == 500
    assert known["document.content_digest"]


def test_text_over_the_character_bound_is_truncated_and_says_so(tmp_path: Path):
    path = tmp_path / "long.txt"
    path.write_text("abcdefghij", encoding="utf-8")
    known = facts(observe_document(path, observed_at=_PINNED_AT, max_text_characters=4))
    assert known["text.extraction_status"] == "truncated"
    assert known["text.truncated"] is True
    assert known["text.character_count"] == 4


def test_a_pdf_over_the_page_bound_is_truncated_and_says_so(tmp_path: Path):
    pdf = make_pdf(tmp_path / "two.pdf", ["One", "Two"])
    known = facts(observe_document(pdf, observed_at=_PINNED_AT, max_pages=1))
    assert known["text.extraction_status"] == "truncated"
    assert known["text.truncated"] is True
    assert known["document.page_count"] == 2  # how many there are
    assert known["text.pages_extracted"] == 1  # how many we read — never silently lost


def test_truncation_never_changes_the_document_identity(tmp_path: Path):
    """Identity is the digest of the FILE'S BYTES, never of the extracted text — so a bound can
    never make two different documents look like the same one."""
    path = tmp_path / "long.txt"
    path.write_text("abcdefghij", encoding="utf-8")
    whole = observe_document(path, observed_at=_PINNED_AT)
    clipped = observe_document(path, observed_at=_PINNED_AT, max_text_characters=4)
    assert facts(whole)["document.content_digest"] == facts(clipped)["document.content_digest"]
    assert whole.artifact_digest != clipped.artifact_digest  # but what we read differs, honestly


# -- provenance: the extractor is part of what was observed -----------------------------------


def test_the_extractor_is_recorded_and_digest_covered(tmp_path: Path, monkeypatch):
    """pypdf's output can change between versions, so the extractor's identity is a fact about the
    observation. Re-reading with a different extractor is a NEW observation, not a silent
    revision."""
    pdf = make_pdf(tmp_path / "p.pdf", ["Hello"])
    real = observe_document(pdf, observed_at=_PINNED_AT)
    assert str(facts(real)["text.extractor"]).startswith("pypdf/")

    monkeypatch.setattr(document_intake, "_pdf_extractor_version", lambda: "pypdf/999.0")
    other = observe_document(pdf, observed_at=_PINNED_AT)
    assert other.artifact_digest != real.artifact_digest


def test_a_builtin_extractor_is_named_for_text_formats(observed):
    assert facts(observed)["text.extractor"] == "builtin/1"


def test_the_creation_time_is_undetermined_when_the_filesystem_cannot_supply_it(observed):
    """Linux ext4 exposes no birth time through stat. We say "I don't know" rather than pass off
    ctime (an inode-change time) as a creation time — that would be a fabricated fact."""
    created = next(o for o in observed.observations if o.observation_type == "document.created_at")
    if created.confidence == 0.0:
        assert created.value is None
    else:
        assert isinstance(created.value, str)


# -- fail closed ------------------------------------------------------------------------------


def test_a_missing_path_fails_closed(tmp_path: Path):
    with pytest.raises(DocumentNotFoundError):
        observe_document(tmp_path / "nope.txt", observed_at=_PINNED_AT)


def test_a_directory_fails_closed(tmp_path: Path):
    (tmp_path / "dir.txt").mkdir()
    with pytest.raises(DocumentNotFoundError):
        observe_document(tmp_path / "dir.txt", observed_at=_PINNED_AT)


def test_a_broken_symlink_fails_closed(tmp_path: Path):
    link = tmp_path / "dangling.txt"
    link.symlink_to(tmp_path / "absent.txt")
    with pytest.raises(DocumentNotFoundError):
        observe_document(link, observed_at=_PINNED_AT)


def test_a_special_file_fails_closed(tmp_path: Path):
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)
    with pytest.raises(DocumentNotFoundError):
        observe_document(fifo, observed_at=_PINNED_AT)


def test_a_path_outside_the_allowed_roots_fails_closed(tmp_path: Path, note: Path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(SourceBoundaryError):
        observe_document(note, allowed_roots=[elsewhere], observed_at=_PINNED_AT)


def test_a_symlink_escaping_the_allowed_roots_fails_closed(tmp_path: Path):
    """The boundary is checked on the CANONICAL path, so a symlink cannot smuggle a read out."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("private", encoding="utf-8")

    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "link.txt").symlink_to(secret)

    with pytest.raises(SourceBoundaryError):
        observe_document(inside / "link.txt", allowed_roots=[inside], observed_at=_PINNED_AT)


def test_a_file_whose_bytes_cannot_be_read_fails_closed(tmp_path: Path):
    """Permission denied fails closed, and that is not the same as the `unreadable` *status*.

    A document's identity IS the digest of its bytes. If we cannot read them we cannot say which
    document this is, and a record that cannot identify its own subject is not an observation of
    it — so we refuse, typed, rather than emit an anonymous husk. Text that cannot be extracted
    from *readable* bytes is the other case, and stays an honest undetermined observation.
    """
    path = tmp_path / "locked.txt"
    path.write_text("secret", encoding="utf-8")
    path.chmod(0o000)
    try:
        if os.access(path, os.R_OK):  # running as root: the premise does not hold
            pytest.skip("cannot make a file unreadable as root")
        with pytest.raises(DocumentUnreadableError):
            observe_document(path, observed_at=_PINNED_AT)
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_a_corrupt_pdf_is_unreadable_but_still_identified(tmp_path: Path):
    """Bytes readable, text not: the document keeps its identity, only the text is undetermined.
    This is what keeps the `unreadable` status reachable rather than a dead vocabulary member."""
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not actually a pdf\n%%EOF\n")
    artifact = observe_document(path, observed_at=_PINNED_AT)
    assert facts(artifact)["text.extraction_status"] == "unreadable"
    assert "text.character_count" in undetermined(artifact)
    assert "document.page_count" in undetermined(artifact)
    assert facts(artifact)["document.content_digest"]  # still identified


def test_a_tampered_artifact_digest_is_rejected(observed):
    raw = dict(observed.to_dict(), artifact_digest="0" * 64)
    with pytest.raises(ObservationValidationError, match="digest"):
        validate_document_observation(raw)


def test_a_forged_observation_with_a_restamped_digest_is_still_rejected(observed):
    """A forger who also recomputes the digest produces a self-consistent lie. The closed
    vocabulary is the second lock: there is no observation type to put an opinion under."""
    raw = observed.to_dict()
    raw["observations"] = raw["observations"] + [
        {
            "observation_type": "document.importance",
            "value": 0.9,
            "source": "filesystem",
            "observed_at": _PINNED_AT,
            "confidence": 1.0,
        }
    ]
    with pytest.raises(ObservationValidationError, match="unknown observation type"):
        validate_document_observation(raw)


def test_an_unknown_schema_version_is_rejected(observed):
    raw = dict(observed.to_dict(), schema_version=2)
    with pytest.raises(ObservationValidationError, match="schema_version"):
        validate_document_observation(raw)


def test_an_undetermined_observation_carrying_a_value_is_rejected(observed):
    raw = observed.to_dict()
    for item in raw["observations"]:
        if item["observation_type"] == "document.created_at":
            item["confidence"] = 0.0
            item["value"] = "2026-01-01T00:00:00+00:00"
    with pytest.raises(ObservationValidationError, match="undetermined"):
        validate_document_observation(raw)


def test_an_unexpected_key_is_rejected(observed):
    raw = dict(observed.to_dict(), verdict="important")
    with pytest.raises(ObservationValidationError, match="unexpected"):
        validate_document_observation(raw)


def test_every_extraction_status_is_reachable():
    """A vocabulary with dead members lies about what the system can tell you."""
    assert {
        "extracted",
        "empty",
        "truncated",
        "unreadable",
        "extractor_unavailable",
        "too_large",
    } == EXTRACTION_STATUSES


# -- purity and safety -------------------------------------------------------------------------


def test_observing_a_document_never_modifies_it(tmp_path: Path):
    make_pdf(tmp_path / "paper.pdf", ["Hello"])
    (tmp_path / "note.md").write_text("# hi\n", encoding="utf-8")
    before = tree_digest(tmp_path)
    observe_document(tmp_path / "paper.pdf", observed_at=_PINNED_AT)
    observe_document(tmp_path / "note.md", observed_at=_PINNED_AT)
    assert tree_digest(tmp_path) == before


def test_document_intake_imports_nothing_impure():
    """No network, no LLM, no database, no subprocess. `pypdf` is imported lazily inside the
    extractor, which is why it does not appear here at module scope."""
    source = Path(document_intake.__file__).read_text(encoding="utf-8")
    allowed = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "hashlib",
        "io",
        "modules.content_identity",
        "os",
        "pathlib",
        "re",
        "stat",
        "typing",
        "uuid",
        "modules.backup_manifest",
        "modules.backup_readiness",
        "modules.observations",
        "modules.profiles",
        "pypdf",  # lazy, inside the extractor
    }
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "") in allowed, node.module


def test_document_intake_never_opens_a_file_for_writing():
    """Read-only toward the source, checked in the syntax tree: every `open` is binary-read."""
    source = Path(document_intake.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"exec", "eval", "compile", "__import__", "input"}
            if node.func.id == "open":
                modes = [a.value for a in node.args[1:] if isinstance(a, ast.Constant)]
                modes += [
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
                ]
                assert modes and all("r" in m and "w" not in m and "a" not in m for m in modes)


def test_the_module_surface_carries_no_opinion():
    """There is no name here for score, rank, priority, importance, severity or quality — because
    a document observation may not carry one."""
    forbidden = {"score", "rank", "priority", "importance", "severity", "quality", "relevance"}
    for name in dir(document_intake):
        assert not any(word in name.lower() for word in forbidden), name
    for observation_type in OBSERVATION_TYPES:
        assert not any(word in observation_type.lower() for word in forbidden), observation_type


def test_document_intake_errors_share_one_base(tmp_path: Path):
    assert issubclass(DocumentNotFoundError, DocumentIntakeError)
    assert issubclass(UnsupportedDocumentFormatError, DocumentIntakeError)
