"""Raw-byte content identity (LHP-INTEL-03A).

`SHA-256(exact raw bytes)` — the one recipe, defined once, so that two collectors cannot quietly
disagree about what the same bytes hash to. Two copies of this already existed and happened to
agree; a third would have been the packet where they stopped agreeing.

**What the identity depends on: the bytes. Nothing else.** Not the path, not the name, not the
mode, not the timestamps, not a decoded string, not a normalized line ending, not git's blob
SHA-1 — which hashes a `blob <len>\\0` header *before* the bytes, and so could never be the same
identity a loose file on disk would get. A rename changes where something is; it does not change
what it is. One flipped byte changes what it is.

This module owns **mechanics only**: streaming, bounding, refusing what must not be read, and
noticing when the ground moved under a read. It knows nothing about what the bytes are *for* and
has no word for whether they matter. Callers classify; this layer measures.

Pure: no network, no subprocess, no database, no clock, no LLM.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

DIGEST_ALGORITHM = "sha256"

# Streamed, never slurped: identity must not depend on being able to hold a file in memory, and a
# bounded reader cannot be talked into allocating a gigabyte by a hostile size.
HASH_CHUNK_BYTES = 1024 * 1024


class ContentIdentityError(Exception):
    """Base class for every fail-closed content-identity condition."""


class ContentMissingError(ContentIdentityError):
    """The path is not there. Absence is a fact; a fabricated digest would not be."""


class ContentUnreadableError(ContentIdentityError):
    """The bytes could not be read. Without bytes there is no identity — never guess one."""


class ContentSymlinkError(ContentIdentityError):
    """The path is a symbolic link. Its target is somewhere else, and following it would silently
    carry a read outside the boundary the caller established."""


class ContentSpecialFileError(ContentIdentityError):
    """Not a regular file — a FIFO, a socket, a device, a directory. Reading one can block forever
    or have side effects, and neither is a thing an observer may do."""


class ContentTooLargeError(ContentIdentityError):
    """Larger than the caller's bound, and therefore refused *unread*."""


class ContentChangedDuringReadError(ContentIdentityError):
    """It changed while it was being read, so the bytes we hashed were never all true at once.

    There is no retry here on purpose. A retry loop eventually catches *some* consistent state and
    presents it as the state that was observed. The honest answer is that we do not know.
    """


@dataclass(frozen=True)
class FileContentIdentity:
    """What was found: the digest of the exact bytes, the algorithm that produced it, and how many
    bytes there were. No name, no location — those belong to whoever was looking."""

    content_digest: str
    digest_algorithm: str
    size_bytes: int


def open_binary(path: str | Path):
    """Open for reading without following a final-component symlink.

    `O_NOFOLLOW` closes the race that a plain `open()` leaves open: a path checked and *then*
    reopened can be swapped for a link in between, and the reader would follow it out of bounds
    without ever knowing. Here the kernel refuses instead.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    return os.fdopen(os.open(path, flags), "rb")


def sha256_stream(path: str | Path, *, chunk_bytes: int = HASH_CHUNK_BYTES) -> tuple[str, int]:
    """SHA-256 of a file's exact bytes, streamed. Returns `(digest, size_bytes)`.

    The unbounded primitive: it hashes what it is given and counts what it read. Callers that need
    a bound, a type check or a change-detection guard want `identify_regular_file`.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(chunk_bytes):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ContentUnreadableError(f"cannot read bytes: {path} ({exc.strerror})") from exc
    return digest.hexdigest(), size


def identify_regular_file(path: str | Path, *, max_bytes: int) -> FileContentIdentity:
    """Identify one regular file by its exact bytes, within a bound, fail-closed throughout.

    The sequence matters, and each step closes a specific hole:

    1. `lstat` — never `stat`: a symlink must be *seen*, not resolved.
    2. refuse anything that is not a regular file, and refuse an oversized one **before opening it**
       (a bound that is enforced after the read is not a bound).
    3. open with `O_NOFOLLOW` so the thing opened is the thing that was inspected.
    4. `fstat` the open descriptor and compare identity against step 1 — this is what makes the
       check and the read the same object rather than the same *name*.
    5. stream, counting bytes.
    6. `fstat` again: if size, mtime or inode moved, the bytes were never all true at once.
    """
    resolved = Path(path)
    try:
        before = os.lstat(resolved)
    except FileNotFoundError as exc:
        raise ContentMissingError(f"no such path: {resolved}") from exc
    except OSError as exc:
        raise ContentUnreadableError(f"cannot inspect: {resolved} ({exc.strerror})") from exc

    if stat.S_ISLNK(before.st_mode):
        raise ContentSymlinkError(f"path is a symbolic link: {resolved}")
    if not stat.S_ISREG(before.st_mode):
        raise ContentSpecialFileError(f"path is not a regular file: {resolved}")
    if before.st_size > max_bytes:
        raise ContentTooLargeError(
            f"file exceeds the {max_bytes}-byte bound and was not read: {resolved}"
        )

    digest = hashlib.sha256()
    size = 0
    try:
        with open_binary(resolved) as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ContentSpecialFileError(f"path is not a regular file: {resolved}")
            if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
                raise ContentChangedDuringReadError(
                    f"file was replaced before the read: {resolved}"
                )
            while chunk := handle.read(HASH_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise ContentTooLargeError(
                        f"file grew past the {max_bytes}-byte bound while being read: {resolved}"
                    )
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except (ContentIdentityError, OSError) as exc:
        if isinstance(exc, ContentIdentityError):
            raise
        if isinstance(exc, FileNotFoundError):
            raise ContentMissingError(f"no such path: {resolved}") from exc
        # ELOOP: O_NOFOLLOW refused a symlink that appeared between the lstat and the open.
        if getattr(exc, "errno", None) == 40:  # ELOOP
            raise ContentSymlinkError(f"path became a symbolic link: {resolved}") from exc
        raise ContentUnreadableError(f"cannot read bytes: {resolved} ({exc.strerror})") from exc

    if (after.st_size, after.st_mtime_ns, after.st_ino) != (
        before.st_size,
        before.st_mtime_ns,
        before.st_ino,
    ) or size != before.st_size:
        raise ContentChangedDuringReadError(f"file changed while it was being read: {resolved}")

    return FileContentIdentity(
        content_digest=digest.hexdigest(),
        digest_algorithm=DIGEST_ALGORITHM,
        size_bytes=size,
    )
