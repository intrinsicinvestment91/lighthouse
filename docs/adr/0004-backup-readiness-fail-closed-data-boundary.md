# ADR-0004 — Backup readiness is fail-closed within a canonical `/data` boundary

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

Lighthouse accumulates irreplaceable intelligence (`state.db`, callsign history, graph, entities/targets
seeds). On Start9 the persistent backup volume is `/data`. An authoritative path configured outside
`/data` (e.g. via `DB_PATH`) silently escapes StartOS backup expectations. Because a profile switch
alters derivation, it must be preceded by a real backup (originally "decision #3: backup on every
switch").

## Decision

- Backup readiness is inspected by a **read-only, fail-closed** inspector over a canonical boundary
  `DATA_BOUNDARY = "/data"`. Path checks use resolved paths, rejecting `..` traversal and symlink
  escape.
- Verdicts: `NOT_READY` (any blocking check, non-absolute boundary, or unknown schema versions);
  `UNKNOWN` (boundary-valid but authoritative state not yet created); `READY` otherwise.
- **`READY` asserts boundary *eligibility* only — never that a backup was performed** (`backup_performed`
  is always `False`). `UNKNOWN` is never treated as approval.
- A profile's declared `backup_policy.data_root` must canonically resolve under `/data` or `validate()`
  rejects it. Future profile activation is blocked while readiness is not `READY`; configuration must
  never silently fall back to an external path.

## Consequences

- Activation (ADR-0003) has a hard, checkable precondition.
- The inspector never mutates state or triggers a backup — actual backups remain a separate operator/
  StartOS action (see `docs/OPERATIONS.md`, ID-P1B live-backup method).

## Repository evidence

- `modules/backup_readiness.py`: `DATA_BOUNDARY = "/data"`; `is_within_boundary()`/`resolve_path()`;
  `inspect_backup_readiness()` returning `READY`/`NOT_READY`/`UNKNOWN` with `backup_performed: False`.
- `docs/BACKUP_READINESS_AND_PROFILE_SWITCHING.md`; `docs/PROFILE_SECURITY_RISKS.md` risk 3
  (`DB_PATH` backup escape).
