"""The public-source check service (LHP-INTEL-04B) — the boundary.

This is the only module that touches both sides: the hardened adapter in, the append-only ledger
out. It orchestrates, but holds no network code of its own — a repository-wide test proves the only
network-capable module is ``public_source_intake``.

Three promises the layers below cannot keep alone:

**It refuses before it reaches the network when nothing is configured.** An empty host allowlist is
not "check anything"; it is "check nothing", and the run stops before a socket, a table or a
database file exists.

**A dry run writes nothing at all** — not a row, not a table, not the database file. The tables are
created inside the persisting transaction, so a preview and a refusal leave the store exactly as
they found it.

**Every completed attempt is retained, and read back through its own contract.** The ledger is an
event log: a repeated identical outcome, a failure, a recovery — each is a distinct row, never
deduplicated, because cadence is the evidence. On the way out, a stored artifact is re-validated
(digest recomputed, closed vocabulary enforced), so a field forged by raw SQL is refused.

It counts what happened. It does not decide what any of it means — that is 04C's to do, later.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules import db, public_source_intake
from modules import public_source_check as psc
from modules.observation_query import MIN_ID_PREFIX
from modules.public_source_check import (
    PublicSourceCheck,
    PublicSourceCheckError,
    PublicSourceCheckRun,
)
from modules.public_source_intake import ADAPTER_ID, ADAPTER_VERSION

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500


class PublicSourceServiceError(Exception):
    """The check service refused: bad input, an unusable policy, or an unavailable store."""


class EmptyAllowlistError(PublicSourceServiceError):
    """No public-source host is configured. Empty fails closed — the run refuses rather than
    trusting the network."""


class RunNotFound(PublicSourceServiceError):
    pass


class CheckNotFound(PublicSourceServiceError):
    pass


class AmbiguousId(PublicSourceServiceError):
    """An abbreviated id matches more than one record. Names them all; never guesses."""

    def __init__(self, prefix: str, matches: list[str]) -> None:
        listed = "\n  ".join(matches)
        super().__init__(
            f"{prefix!r} matches {len(matches)} records — use more characters:\n  {listed}"
        )
        self.prefix = prefix
        self.matches = matches


class TamperedRecordError(PublicSourceServiceError):
    """A stored run or check does not satisfy its own contract."""


@dataclass(frozen=True)
class RunResult:
    """What one check run did. Counts and facts, never a judgement about what it found."""

    dry_run: bool
    run: PublicSourceCheckRun
    checks: tuple[PublicSourceCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "run_id": self.run.run_id,
            "started_at": self.run.started_at,
            "completed_at": self.run.completed_at,
            "adapter_id": self.run.adapter_id,
            "adapter_version": self.run.adapter_version,
            "policy_version": self.run.policy_version,
            "target_count": self.run.target_count,
            "completed_check_count": self.run.completed_check_count,
            "outcome_counts": dict(self.run.outcome_counts),
            "checks": [
                {
                    "check_id": check.check_id,
                    "canonical_locator": check.canonical_locator,
                    "final_locator": check.final_locator,
                    "outcome": check.outcome,
                    "http_status": check.http_status,
                    "content_digest": check.content_digest,
                    "body_size_bytes": check.body_size_bytes,
                    "redirected": check.redirected,
                }
                for check in self.checks
            ],
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# -- running checks --------------------------------------------------------------------------


def run_checks(
    *,
    targets_path: str,
    allowed_hosts: list[str],
    dry_run: bool = False,
    resolver: Callable[[str], list[str]] | None = None,
    transport: Any | None = None,
    clock: Callable[[], str] | None = None,
    nonce: str | None = None,
) -> RunResult:
    """Check every declared target once, and — unless this is a dry run — persist the completed run.

    ``allowed_hosts`` is the authoritative outer boundary (``main.py`` reads it from config). It is
    normalized here; an empty result refuses the run before any network. A per-target failure is
    isolated into its own factual outcome and never aborts the run.
    """
    try:
        allowlist = psc.normalize_host_allowlist(allowed_hosts)
    except psc.HostPolicyError as exc:
        raise PublicSourceServiceError(f"the configured host allowlist is invalid: {exc}") from exc
    if not allowlist:
        raise EmptyAllowlistError(
            "no public-source hosts are configured; refusing to check anything "
            "(set LIGHTHOUSE_PUBLIC_SOURCE_HOSTS)"
        )

    targets = psc.load_targets(targets_path)  # raises TargetsError on a bad work list
    canonicals = targets.canonical_locators()
    if len(canonicals) > public_source_intake.MAX_TARGETS:
        raise PublicSourceServiceError(
            f"a run may check at most {public_source_intake.MAX_TARGETS} targets"
        )

    tick = clock or _utc_now
    run_nonce = nonce or uuid.uuid4().hex
    policy_digest = public_source_intake.policy_digest()
    started_at = tick()

    run_id = psc.compute_run_id(
        started_at=started_at,
        run_nonce=run_nonce,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=policy_digest,
        target_count=len(canonicals),
        target_set_digest=psc.target_set_digest(canonicals),
    )

    budget = public_source_intake.MAX_AGGREGATE_BYTES
    checks: list[PublicSourceCheck] = []
    for canonical in canonicals:
        checked_at = tick()
        check, consumed = _check_one(
            canonical,
            run_id=run_id,
            allowlist=allowlist,
            resolver=resolver,
            transport=transport,
            aggregate_remaining=budget,
            checked_at=checked_at,
            policy_digest=policy_digest,
        )
        budget = max(0, budget - consumed)
        checks.append(check)

    completed_at = tick()
    run = psc.build_run(
        started_at=started_at,
        completed_at=completed_at,
        run_nonce=run_nonce,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=policy_digest,
        targets_ordered=canonicals,
        checks=tuple(checks),
    )

    if not dry_run:
        db.store_check_run(run=run.to_dict(), checks=[check.to_dict() for check in checks])

    return RunResult(dry_run=dry_run, run=run, checks=tuple(checks))


def _check_one(
    canonical: str,
    *,
    run_id: str,
    allowlist: frozenset[str],
    resolver,
    transport,
    aggregate_remaining: int,
    checked_at: str,
    policy_digest: str,
) -> tuple[PublicSourceCheck, int]:
    """One target → one validated check. A failure is isolated into ``adapter_failure``, never a
    traceback and never a claim of absence."""
    try:
        result = public_source_intake.check_locator(
            canonical,
            allowlist=allowlist,
            resolver=resolver,
            transport=transport,
            aggregate_remaining=aggregate_remaining,
        )
        check = psc.build_check(
            run_id=run_id,
            canonical_locator=canonical,
            final_locator=result.final_locator,
            redirected=result.redirected,
            redirect_chain=result.redirect_chain,
            outcome=result.outcome,
            http_status=result.http_status,
            content_digest=result.content_digest,
            body_size_bytes=result.body_size_bytes,
            content_type=result.content_type,
            checked_at=checked_at,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            policy_digest=policy_digest,
        )
        return check, result.bytes_consumed
    except Exception:
        check = psc.build_check(
            run_id=run_id,
            canonical_locator=canonical,
            final_locator=canonical,
            redirected=False,
            redirect_chain=(),
            outcome=psc.ADAPTER_FAILURE,
            http_status=None,
            content_digest=None,
            body_size_bytes=None,
            content_type=None,
            checked_at=checked_at,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            policy_digest=policy_digest,
        )
        return check, 0


# -- reading the ledger back -----------------------------------------------------------------


def list_runs(*, limit: int | None = None) -> dict[str, Any]:
    bound = _bounded_limit(limit)
    if not db.public_source_tables_exist():
        return {"count": 0, "runs": []}
    rows = db.fetch_public_source_runs(limit=MAX_LIST_LIMIT)
    runs = [_run_summary(_validated_run(row)) for row in rows[:bound]]
    return {"count": len(runs), "runs": runs}


def show_run(run_id: str) -> dict[str, Any]:
    resolved = resolve_run_id(run_id)
    row = db.fetch_public_source_run(resolved)
    if row is None:  # pragma: no cover - resolve proved it exists
        raise RunNotFound(f"no run matches {run_id!r}")
    run = _validated_run(row)
    checks = [
        _validated_check(check_row).to_dict()
        for check_row in db.fetch_public_source_checks(run_id=resolved)
    ]
    return {"run": run.to_dict(), "checks": checks}


def list_source_checks(
    *,
    source_id: str | None = None,
    locator: str | None = None,
    locator_id: str | None = None,
    content_digest: str | None = None,
    ascending: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Chronological facts for one source, locator, or exact content digest. Never a ranking."""
    selectors = [x for x in (source_id, locator, locator_id, content_digest) if x is not None]
    if len(selectors) != 1:
        raise PublicSourceServiceError(
            "provide exactly one of source_id, locator, locator_id, or content_digest"
        )

    resolved_source = source_id
    resolved_locator_id = locator_id
    if locator is not None:
        try:
            canonical = psc.canonical_locator(locator)
        except psc.LocatorError as exc:
            raise PublicSourceServiceError(str(exc)) from exc
        resolved_locator_id = psc.locator_id_for(canonical)

    bound = _bounded_limit(limit)
    if not db.public_source_tables_exist():
        return {"count": 0, "checks": []}
    rows = db.fetch_public_source_checks(
        source_id=resolved_source,
        locator_id=resolved_locator_id,
        content_digest=content_digest,
        ascending=ascending,
        limit=MAX_LIST_LIMIT,
    )
    checks = [_validated_check(row).to_dict() for row in rows[:bound]]
    return {"count": len(checks), "checks": checks}


def show_check(check_id: str) -> dict[str, Any]:
    resolved = resolve_check_id(check_id)
    row = db.fetch_public_source_check(resolved)
    if row is None:  # pragma: no cover - resolve proved it exists
        raise CheckNotFound(f"no check matches {check_id!r}")
    check = _validated_check(row)
    run_row = db.fetch_public_source_run(check.run_id)
    return {
        "check": check.to_dict(),
        "run": _run_summary(_validated_run(run_row)) if run_row is not None else None,
    }


# -- id resolution ---------------------------------------------------------------------------


def resolve_run_id(value: str) -> str:
    return _resolve(
        value,
        prefix=psc.RUN_ID_PREFIX,
        fetch=db.fetch_public_source_run,
        fetch_prefix=db.fetch_public_source_run_ids_by_prefix,
        not_found=RunNotFound,
    )


def resolve_check_id(value: str) -> str:
    return _resolve(
        value,
        prefix=psc.CHECK_ID_PREFIX,
        fetch=db.fetch_public_source_check,
        fetch_prefix=db.fetch_public_source_check_ids_by_prefix,
        not_found=CheckNotFound,
    )


def _resolve(value, *, prefix, fetch, fetch_prefix, not_found):
    if not isinstance(value, str):
        raise PublicSourceServiceError("an id must be a string")
    body = value.strip()
    if body.startswith(prefix):
        body = body[len(prefix) :]
    if not body or not all(char in "0123456789abcdef" for char in body):
        raise PublicSourceServiceError(
            f"an id is lowercase hexadecimal, optionally prefixed {prefix!r} — not {value!r}"
        )
    if len(body) < MIN_ID_PREFIX:
        raise PublicSourceServiceError(
            f"an id needs at least {MIN_ID_PREFIX} characters to be unambiguous"
        )
    if len(body) > 64:
        raise PublicSourceServiceError(f"an id is at most 64 characters, not {len(body)}")

    if not db.public_source_tables_exist():
        raise not_found(f"no record matches {value!r}")
    if len(body) == 64:
        full = prefix + body
        if fetch(full) is None:
            raise not_found(f"no record matches {value!r}")
        return full
    matches = fetch_prefix(body)
    if not matches:
        raise not_found(f"no record matches {value!r}")
    if len(matches) > 1:
        raise AmbiguousId(body, matches)
    return matches[0]


# -- validate on read ------------------------------------------------------------------------


def _validated_check(row: dict[str, Any]) -> PublicSourceCheck:
    try:
        check = PublicSourceCheck.from_dict(json.loads(row["check_json"]))
        check.validate()
    except (PublicSourceCheckError, KeyError, TypeError, ValueError) as exc:
        raise TamperedRecordError(
            f"the stored check {row.get('check_id')!r} does not satisfy its own contract: {exc}"
        ) from exc
    if check.check_id != row["check_id"]:
        raise TamperedRecordError(
            f"stored check {row['check_id']!r} carries an artifact with a different identity"
        )
    return check


def _validated_run(row: dict[str, Any]) -> PublicSourceCheckRun:
    try:
        run = PublicSourceCheckRun.from_dict(json.loads(row["run_json"]))
        run.validate()
    except (PublicSourceCheckError, KeyError, TypeError, ValueError) as exc:
        raise TamperedRecordError(
            f"the stored run {row.get('run_id')!r} does not satisfy its own contract: {exc}"
        ) from exc
    if run.run_id != row["run_id"]:
        raise TamperedRecordError(
            f"stored run {row['run_id']!r} carries an artifact with a different identity"
        )
    return run


def _run_summary(run: PublicSourceCheckRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "adapter_version": run.adapter_version,
        "policy_version": run.policy_version,
        "target_count": run.target_count,
        "completed_check_count": run.completed_check_count,
        "outcome_counts": dict(run.outcome_counts),
    }


def _bounded_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIST_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise PublicSourceServiceError(f"limit must be an integer, not {limit!r}") from exc
    if value < 1:
        raise PublicSourceServiceError("limit must be at least 1")
    return min(value, MAX_LIST_LIMIT)
