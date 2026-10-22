"""Observed-source reference export boundary (LHP-INTEL-04D-R1).

The only module that reads a validated public-source check and emits an
``observed_source_reference.v0``. Read-only toward the ledger; writes nothing.
"""

from __future__ import annotations

from typing import Any

from modules import db
from modules.backup_manifest import detect_repository_commit
from modules.observed_source_reference import (
    ObservedSourceReferenceError,
    build_observed_source_reference,
)
from modules.public_source_check_service import (
    CheckNotFound,
    PublicSourceServiceError,
    TamperedRecordError,
    _validated_check,
    _validated_run,
    resolve_check_id,
)


class ExportError(Exception):
    """The export refused."""


def export_observed_source_reference(
    check_id: str,
    *,
    intended_consumer: str,
    producer_repository: str | None = "pattern-match-sentinel",
    producer_commit: str | None = None,
) -> dict[str, Any]:
    """Export one OSR from a stored check. Refuses unavailable or tampered records."""
    try:
        resolved = resolve_check_id(check_id)
    except PublicSourceServiceError as exc:
        raise ExportError(str(exc)) from exc

    if not db.public_source_tables_exist():
        raise ExportError("no public-source check history exists")

    row = db.fetch_public_source_check(resolved)
    if row is None:
        raise CheckNotFound(f"no check matches {check_id!r}")

    try:
        check = _validated_check(row)
    except TamperedRecordError as exc:
        raise ExportError(str(exc)) from exc

    run_row = db.fetch_public_source_run(check.run_id)
    run_digest: str | None = None
    if run_row is not None:
        try:
            run = _validated_run(run_row)
            run_digest = run.artifact_digest
        except TamperedRecordError as exc:
            raise ExportError(str(exc)) from exc

    commit = producer_commit if producer_commit is not None else detect_repository_commit()
    try:
        reference = build_observed_source_reference(
            check,
            intended_consumer=intended_consumer,
            run_artifact_digest=run_digest,
            producer_repository=producer_repository,
            producer_commit=commit,
        )
    except ObservedSourceReferenceError as exc:
        raise ExportError(str(exc)) from exc

    reference.validate()
    return {"reference": reference.to_dict()}
