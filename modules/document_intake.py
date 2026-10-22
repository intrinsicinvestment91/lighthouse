"""Deterministic document observation intake — `document_observation.v1` (LHP-INTEL-01C).

The second local observation source, and the first that reads a file's *contents*. It answers one
question — *"what information exists?"* — and refuses the next three: not *"what is important"*,
not *"what should the operator do"*, not *"what is novel"*. Those belong to a layer that does not
exist yet, and this module must not grow it by accident.

**The distinction this module exists to preserve.** Extraction can fail, and a failure that reads
as an answer is worse than no answer at all:

    "this document contains no text"   is a FACT        -> character_count 0, confidence 1.0
    "I could not extract its text"     is an ABSENCE    -> value None,       confidence 0.0

`repository_intake` already encodes exactly that (1.0 = determined *including provably absent*;
0.0 = undetermined, and then the value is *always* None), so this module reuses that model rather
than inventing a second, subtly different one. `text.extraction_status` names which case occurred,
and it is itself always determined — what happened when we read the file is a fact either way.

**Bounds, on two different axes, and never silent.** A file larger than `max_file_bytes` is refused
*unread* (`too_large`) — we do not load four gigabytes into memory to be helpful. Text longer than
`max_text_characters`, or a PDF deeper than `max_pages`, is **truncated with explicit metadata**
(`text.truncated`, `text.pages_extracted` vs `document.page_count`). What was clipped is always
recoverable from the record; a clipped answer presented as complete would be a lie.

**Identity is the bytes, never the text.** `document.content_digest` is the SHA-256 of the file, so
the same document copied to a second path is *one thing observed in two places*, and no extraction
bound can ever make two different documents look like the same one. The artifact's `document_id`
is a different digest answering a different question — SHA-256 of the canonical absolute path, the
same rule `repository_intake` uses, i.e. *where I looked* rather than *what I found*.

**The extractor is part of the observation.** pypdf's output can change between releases, so
`text.extractor` is recorded and digest-covered. The honest promise is therefore: *an unchanged
document, read by the same extractor, yields the same digest forever.* A pypdf upgrade genuinely
changes what we were able to read, and the insert-only store records a **new** observation instead
of silently revising the old claim. pypdf is imported lazily, so an install without it degrades to
an honest `extractor_unavailable` rather than crashing.

**What is read, and what is kept.** Text is extracted in order to establish facts *about* it —
whether it exists, how much there is, what it hashes to. The text itself is **not** carried in the
artifact and never reaches the database: this is metadata-first intake, and `text.digest` keeps a
later re-extraction verifiable. Putting the operator's private document contents into SQLite is a
decision with its own consequences, and it is not this packet's to make.

Read-only toward the source (a tested property), no network, no LLM, no database, no writes.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.backup_manifest import detect_lighthouse_version
from modules.backup_readiness import is_within_boundary, resolve_path
from modules.content_identity import ContentUnreadableError, sha256_stream
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

DOCUMENT_OBSERVATION_SCHEMA_VERSION = 1
DERIVATION_VERSION = "document_intake/1"

SOURCE_FILESYSTEM = "filesystem"
SOURCE_EXTRACTOR = "text_extractor"

FORMAT_PDF = "pdf"
FORMAT_MARKDOWN = "markdown"
FORMAT_TEXT = "text"
DOCUMENT_FORMATS = frozenset({FORMAT_MARKDOWN, FORMAT_PDF, FORMAT_TEXT})

# A closed extension map, not a sniffing heuristic. HTML and the office formats are deliberately
# absent: a tag-stripper that quietly mangles entities, <script> and whitespace is exactly the
# "silently lose information" this packet forbids, so we decline rather than half-support them.
_EXTENSION_FORMAT: dict[str, str] = {
    ".markdown": FORMAT_MARKDOWN,
    ".md": FORMAT_MARKDOWN,
    ".pdf": FORMAT_PDF,
    ".text": FORMAT_TEXT,
    ".txt": FORMAT_TEXT,
}

# What happened when we tried to read the text. Every member is reachable: a vocabulary with dead
# entries lies about what the system can actually tell you.
EXTRACTION_EXTRACTED = "extracted"
EXTRACTION_EMPTY = "empty"
EXTRACTION_TRUNCATED = "truncated"
EXTRACTION_UNREADABLE = "unreadable"
EXTRACTION_EXTRACTOR_UNAVAILABLE = "extractor_unavailable"
EXTRACTION_TOO_LARGE = "too_large"

EXTRACTION_STATUSES = frozenset(
    {
        EXTRACTION_EMPTY,
        EXTRACTION_EXTRACTED,
        EXTRACTION_EXTRACTOR_UNAVAILABLE,
        EXTRACTION_TOO_LARGE,
        EXTRACTION_TRUNCATED,
        EXTRACTION_UNREADABLE,
    }
)

# The three statuses that mean "no text was established". They make the text facts UNDETERMINED —
# never zero, because zero is an answer and this is the absence of one.
_TEXT_NOT_ESTABLISHED = frozenset(
    {EXTRACTION_EXTRACTOR_UNAVAILABLE, EXTRACTION_TOO_LARGE, EXTRACTION_UNREADABLE}
)

BUILTIN_EXTRACTOR = "builtin/1"
NO_EXTRACTOR = "none"

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARACTERS = 5_000_000
MAX_PAGES = 5_000
_HASH_CHUNK_BYTES = 1024 * 1024


class DocumentIntakeError(ObservationError):
    """Base class for every fail-closed *document* intake condition.

    It inherits from `ObservationError` (the substrate's base), so one `except ObservationError`
    catches any intake failure. Before LHP-INTEL-01D-A this class stood alone while document
    validation raised `ObservationValidationError` — which was, by inheritance, a *repository*
    error. A malformed document therefore reported itself as a repository failure and slipped past
    the normalizer's document wrapper entirely. One base, and that whole class of confusion is gone.
    """


class DocumentNotFoundError(DocumentIntakeError):
    """The path does not exist, or is not a regular file."""


class UnsupportedDocumentFormatError(DocumentIntakeError):
    """The format is outside the closed supported set. Declined, never half-read."""


class DocumentUnreadableError(DocumentIntakeError):
    """The file's bytes cannot be read at all — permission denied, or an I/O error.

    This fails closed rather than degrading to an observation, and the distinction is deliberate.
    A document's identity IS the digest of its bytes; if we cannot read them we cannot say *which*
    document this is, and a record that cannot identify its subject is not an observation of it.
    Text that cannot be *extracted* from readable bytes is a different matter entirely — that is an
    honest undetermined observation, and the document is still fully identified.
    """


# -- the closed observation vocabulary -----------------------------------------------------

OBSERVATION_TYPES: dict[str, ObservationSpec] = {
    # What the file is. Established from the filesystem and from the bytes themselves.
    "document.name": ObservationSpec(SOURCE_FILESYSTEM, (str,)),
    "document.format": ObservationSpec(SOURCE_FILESYSTEM, (str,)),
    "document.size_bytes": ObservationSpec(SOURCE_FILESYSTEM, (int,)),
    "document.content_digest": ObservationSpec(SOURCE_FILESYSTEM, (str,)),
    "document.modified_at": ObservationSpec(SOURCE_FILESYSTEM, (str,), nullable=True),
    # Most Linux filesystems expose no birth time through stat(). We report that we do not know,
    # rather than passing off ctime (an inode-change time) as a creation time: that would be a
    # fabricated fact, and a fabricated fact is worse than a missing one.
    "document.created_at": ObservationSpec(SOURCE_FILESYSTEM, (str,), nullable=True),
    # Pages are a PDF idea. A .txt does not have "unknown" pages — it provably has none, which is
    # determined-and-null, not undetermined.
    "document.page_count": ObservationSpec(SOURCE_EXTRACTOR, (int,), nullable=True),
    # What reading it established. `extraction_status` and `extractor` are always determined: what
    # happened when we tried is a fact whether or not any text came back.
    "text.extraction_status": ObservationSpec(SOURCE_EXTRACTOR, (str,)),
    "text.extractor": ObservationSpec(SOURCE_EXTRACTOR, (str,)),
    "text.character_count": ObservationSpec(SOURCE_EXTRACTOR, (int,)),
    "text.line_count": ObservationSpec(SOURCE_EXTRACTOR, (int,)),
    "text.digest": ObservationSpec(SOURCE_EXTRACTOR, (str,)),
    "text.truncated": ObservationSpec(SOURCE_EXTRACTOR, (bool,)),
    "text.pages_extracted": ObservationSpec(SOURCE_EXTRACTOR, (int,), nullable=True),
}

_EXPECTED_KEYS = frozenset(
    {
        "schema_version",
        "serialization_version",
        "observation_set_id",
        "document_id",
        "document_path",
        "observed_at",
        "profile_id",
        "derivation_version",
        "lighthouse_version",
        "observations",
        "artifact_digest",
    }
)


# -- the artifact ---------------------------------------------------------------------------


@dataclass
class DocumentObservation:
    """One `document_observation.v1` artifact — every fact observed about one document."""

    observation_set_id: str
    document_id: str
    document_path: str
    observed_at: str
    observations: tuple[Observation, ...]
    profile_id: str | None = None
    derivation_version: str = DERIVATION_VERSION
    lighthouse_version: str | None = None
    schema_version: int = DOCUMENT_OBSERVATION_SCHEMA_VERSION
    serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    artifact_digest: str = ""

    def _sorted(self) -> list[Observation]:
        return sorted(self.observations, key=lambda o: o.observation_type)

    def _content(self) -> dict[str, Any]:
        """Digest-covered content: every fact, plus the producer's identity.

        Excludes exactly `observation_set_id`, both levels of `observed_at`, and the digest itself
        — so re-observing an unchanged document reproduces the same digest, which is what makes it
        a content identity rather than a timestamp.
        """
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "document_id": self.document_id,
            "document_path": self.document_path,
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
        return canonical_digest(self._content())

    def validate(self) -> None:
        """Fail closed, and recompute the digest: this artifact is Lighthouse-owned end to end, so
        an inconsistent record is never merely trusted."""
        _require_exact_int(
            "schema_version", self.schema_version, DOCUMENT_OBSERVATION_SCHEMA_VERSION
        )
        _require_exact_int(
            "serialization_version",
            self.serialization_version,
            DETERMINISTIC_SERIALIZATION_VERSION,
        )
        if not isinstance(self.observation_set_id, str) or not self.observation_set_id:
            raise ObservationValidationError("observation_set_id must be a non-empty string")
        if not isinstance(self.document_id, str) or not _SHA256_HEX.match(self.document_id):
            raise ObservationValidationError("document_id must be a 64-char lowercase hex SHA-256")
        if not isinstance(self.document_path, str) or not os.path.isabs(self.document_path):
            raise ObservationValidationError("document_path must be an absolute path")
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

        # One shared implementation of the confidence contract, imported rather than copied.
        validate_observations(self.observations, OBSERVATION_TYPES)

        status = next(
            (o.value for o in self.observations if o.observation_type == "text.extraction_status"),
            None,
        )
        if status not in EXTRACTION_STATUSES:
            raise ObservationValidationError(f"unknown extraction status: {status!r}")

        if not isinstance(self.artifact_digest, str) or not _SHA256_HEX.match(self.artifact_digest):
            raise ObservationValidationError(
                "artifact_digest must be a 64-char lowercase hex SHA-256"
            )
        if self.artifact_digest != self.compute_digest():
            raise ObservationValidationError(
                "artifact_digest mismatch — the content does not match its digest"
            )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Validates first — an invalid artifact never serializes."""
        self.validate()
        return {
            "schema_version": self.schema_version,
            "serialization_version": self.serialization_version,
            "observation_set_id": self.observation_set_id,
            "document_id": self.document_id,
            "document_path": self.document_path,
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
    def from_dict(cls, data: Any) -> DocumentObservation:
        """Strict, fail-closed deserialization: missing and unexpected keys both raise."""
        if not isinstance(data, dict):
            raise ObservationValidationError("document observation must be an object")
        keys = set(data)
        missing = _EXPECTED_KEYS - keys
        if missing:
            raise ObservationValidationError(
                f"document observation is missing required keys: {sorted(missing)}"
            )
        unexpected = keys - _EXPECTED_KEYS
        if unexpected:
            raise ObservationValidationError(
                f"document observation carries unexpected keys: {sorted(unexpected)}"
            )
        raw_observations = data["observations"]
        if not isinstance(raw_observations, list):
            raise ObservationValidationError("observations must be a list")
        artifact = cls(
            observation_set_id=data["observation_set_id"],
            document_id=data["document_id"],
            document_path=data["document_path"],
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


def validate_document_observation(raw: Any) -> DocumentObservation:
    """Parse and fully validate a serialized artifact. Fail-closed; never returns a partial."""
    return DocumentObservation.from_dict(raw)


# -- the observer ---------------------------------------------------------------------------


def observe_document(
    path: str | Path,
    *,
    allowed_roots: Sequence[str | Path] | None = None,
    profile_id: str | None = None,
    observed_at: str | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_text_characters: int = MAX_TEXT_CHARACTERS,
    max_pages: int = MAX_PAGES,
) -> DocumentObservation:
    """Observe one document and return a validated `document_observation.v1` artifact.

    `allowed_roots`, when supplied, is the ADR-0019 trust boundary: the document must resolve
    inside one of them, checked on the CANONICAL path so a symlink cannot smuggle a read out.
    """
    resolved = _resolve_document(path, allowed_roots)
    stamp = observed_at or datetime.now(UTC).isoformat()  # one clock read per run

    document_format = _EXTENSION_FORMAT[resolved.suffix.lower()]
    info = resolved.stat()
    size_bytes = info.st_size

    content_digest = _digest_file(resolved)
    reading = _extract_text(
        resolved,
        document_format,
        size_bytes=size_bytes,
        max_file_bytes=max_file_bytes,
        max_text_characters=max_text_characters,
        max_pages=max_pages,
    )

    facts: dict[str, Any] = {
        "document.name": resolved.name,
        "document.format": document_format,
        "document.size_bytes": size_bytes,
        "document.content_digest": content_digest,
        "document.modified_at": _utc(info.st_mtime),
        "document.created_at": _birth_time(info),
        "document.page_count": reading.page_count,
        "text.extraction_status": reading.status,
        "text.extractor": reading.extractor,
        "text.character_count": reading.character_count,
        "text.line_count": reading.line_count,
        "text.digest": reading.digest,
        "text.truncated": reading.truncated,
        "text.pages_extracted": reading.pages_extracted,
    }
    undetermined = _undetermined_types(reading, info)

    observations = tuple(
        Observation(
            observation_type=name,
            value=None if name in undetermined else facts[name],
            source=OBSERVATION_TYPES[name].source,
            observed_at=stamp,
            confidence=(CONFIDENCE_UNDETERMINED if name in undetermined else CONFIDENCE_OBSERVED),
        )
        for name in sorted(OBSERVATION_TYPES)
    )

    artifact = DocumentObservation(
        observation_set_id=str(uuid.uuid4()),
        document_id=hashlib.sha256(str(resolved).encode("utf-8")).hexdigest(),
        document_path=str(resolved),
        observed_at=stamp,
        observations=observations,
        profile_id=profile_id,
        lighthouse_version=detect_lighthouse_version(),
    )
    artifact.artifact_digest = artifact.compute_digest()
    artifact.validate()
    return artifact


@dataclass(frozen=True)
class _Reading:
    """What one attempt to read the document's text established."""

    status: str
    extractor: str
    character_count: int | None = None
    line_count: int | None = None
    digest: str | None = None
    truncated: bool | None = None
    page_count: int | None = None
    pages_extracted: int | None = None
    # A PDF whose pages we could not enumerate has an UNKNOWN page count; a .txt provably has none.
    page_count_determined: bool = True


def _undetermined_types(reading: _Reading, info: os.stat_result) -> frozenset[str]:
    """Exactly which facts we failed to establish. Everything else is determined — including the
    determined-and-null ones ("a text file provably has no pages")."""
    unknown: set[str] = set()
    if reading.status in _TEXT_NOT_ESTABLISHED:
        unknown |= {
            "text.character_count",
            "text.line_count",
            "text.digest",
            "text.truncated",
            "text.pages_extracted",
        }
    if not reading.page_count_determined:
        unknown.add("document.page_count")
    if not hasattr(info, "st_birthtime"):
        unknown.add("document.created_at")
    return frozenset(unknown)


def _extract_text(
    path: Path,
    document_format: str,
    *,
    size_bytes: int,
    max_file_bytes: int,
    max_text_characters: int,
    max_pages: int,
) -> _Reading:
    """Read the text, or fail honestly. Never raises on a content problem: an unreadable document
    is an observation, not an error — the document itself was still observed."""
    if size_bytes > max_file_bytes:
        # Refused unread. We do not load four gigabytes into memory to be helpful.
        return _Reading(
            status=EXTRACTION_TOO_LARGE,
            extractor=NO_EXTRACTOR,
            page_count_determined=False,
        )

    if document_format == FORMAT_PDF:
        return _read_pdf(path, max_text_characters=max_text_characters, max_pages=max_pages)
    return _read_plain(path, max_text_characters=max_text_characters)


def _read_plain(path: Path, *, max_text_characters: int) -> _Reading:
    try:
        raw = path.read_bytes()
    except OSError:
        return _Reading(status=EXTRACTION_UNREADABLE, extractor=BUILTIN_EXTRACTOR)
    try:
        # Strict: no `errors="replace"`, no encoding guess. A lossy decode silently corrupts, and a
        # guessed encoding is a fabricated fact. "I could not read this" is the honest answer.
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _Reading(status=EXTRACTION_UNREADABLE, extractor=BUILTIN_EXTRACTOR)
    return _measure(text, BUILTIN_EXTRACTOR, max_text_characters=max_text_characters)


def _read_pdf(path: Path, *, max_text_characters: int, max_pages: int) -> _Reading:
    reader_class = _pdf_reader()
    if reader_class is None:
        # A stripped install must say "I could not read this", never "this document is blank".
        return _Reading(
            status=EXTRACTION_EXTRACTOR_UNAVAILABLE,
            extractor=NO_EXTRACTOR,
            page_count_determined=False,
        )
    extractor = _pdf_extractor_version()
    try:
        raw = path.read_bytes()
        reader = reader_class(io.BytesIO(raw))
        pages = list(reader.pages)
    except Exception:  # a corrupt PDF is an observation, not a crash
        return _Reading(
            status=EXTRACTION_UNREADABLE,
            extractor=extractor,
            page_count_determined=False,
        )

    page_count = len(pages)
    read_pages = pages[:max_pages]
    chunks: list[str] = []
    for page in read_pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # a page that will not render is not a reason to lose the document
            return _Reading(
                status=EXTRACTION_UNREADABLE,
                extractor=extractor,
                page_count=page_count,
            )

    reading = _measure("\n".join(chunks), extractor, max_text_characters=max_text_characters)
    pages_truncated = len(read_pages) < page_count
    status = EXTRACTION_TRUNCATED if pages_truncated else reading.status
    return _Reading(
        status=status,
        extractor=extractor,
        character_count=reading.character_count,
        line_count=reading.line_count,
        digest=reading.digest,
        truncated=bool(reading.truncated) or pages_truncated,
        page_count=page_count,
        pages_extracted=len(read_pages),
    )


def _measure(text: str, extractor: str, *, max_text_characters: int) -> _Reading:
    """Turn extracted text into facts about it. The text itself is measured, then dropped."""
    truncated = len(text) > max_text_characters
    kept = text[:max_text_characters] if truncated else text
    if truncated:
        status = EXTRACTION_TRUNCATED
    elif kept.strip():
        status = EXTRACTION_EXTRACTED
    else:
        # Determined: this document contains no text. Zero is an answer.
        status = EXTRACTION_EMPTY
    return _Reading(
        status=status,
        extractor=extractor,
        character_count=len(kept),
        line_count=len(kept.splitlines()),
        digest=hashlib.sha256(kept.encode("utf-8")).hexdigest(),
        truncated=truncated,
        pages_extracted=None,
    )


def _pdf_reader() -> Any:
    """Lazy, so an install without pypdf degrades to an honest `extractor_unavailable`."""
    try:
        from pypdf import PdfReader
    except Exception:  # absent, broken, or shadowed: all of them mean "no extractor"
        return None
    return PdfReader


def _pdf_extractor_version() -> str:
    try:
        import pypdf

        return f"pypdf/{pypdf.__version__}"
    except Exception:
        return NO_EXTRACTOR


# -- filesystem helpers ---------------------------------------------------------------------


def _resolve_document(path: str | Path, allowed_roots: Sequence[str | Path] | None) -> Path:
    """Canonicalize and fail closed. The boundary is checked *first*: a path outside every allowed
    root is never even stat'd, and it is the CANONICAL path that is checked, so a symlink into a
    forbidden directory resolves to the forbidden directory and is refused."""
    resolved = resolve_path(path)
    if allowed_roots is not None and not any(
        is_within_boundary(resolved, boundary) for boundary in allowed_roots
    ):
        raise SourceBoundaryError(f"path resolves outside every allowed root: {resolved}")
    if not resolved.exists():
        raise DocumentNotFoundError(f"document does not exist: {resolved}")
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode):
        raise DocumentNotFoundError(f"document is not a regular file: {resolved}")
    suffix = resolved.suffix.lower()
    if suffix not in _EXTENSION_FORMAT:
        raise UnsupportedDocumentFormatError(
            f"unsupported document format {suffix!r}: supported formats are "
            f"{sorted(_EXTENSION_FORMAT)}"
        )
    return resolved


def _digest_file(path: Path) -> str:
    """SHA-256 of the file's bytes, streamed in chunks.

    Streamed rather than slurped so that identity never depends on being able to hold the document
    in memory: a file too large to *extract* is still one we can *identify*. If the bytes cannot be
    read at all we fail closed — see `DocumentUnreadableError`.

    Since 03A the recipe itself lives in `modules.content_identity` — the same function object a
    repository file is hashed with, so the two cannot drift into disagreeing about what the same
    bytes hash to. The digest is byte-for-byte what it always was; only the definition moved.
    """
    try:
        digest, _ = sha256_stream(path, chunk_bytes=_HASH_CHUNK_BYTES)
    except ContentUnreadableError as exc:
        raise DocumentUnreadableError(f"cannot read document bytes: {path} ({exc})") from exc
    return digest


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _birth_time(info: os.stat_result) -> str | None:
    birth = getattr(info, "st_birthtime", None)
    return _utc(birth) if birth is not None else None
