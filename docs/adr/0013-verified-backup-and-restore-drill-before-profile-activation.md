# ADR-0013 — Verified backup and isolated restore drill required before profile activation

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-02
- **Related ADRs:** ADR-0003 (exactly one active profile, controlled switch), ADR-0004 (backup
  readiness fail-closed within `/data`), ADR-0001 (Lighthouse owns the engine, Gateway is the
  control plane)
- **Supersedes:** —
- **Superseded-by:** —

## Context

ADR-0004 established that Lighthouse can prove, read-only, that authoritative state is *eligible*
for backup (resolves under the `/data` boundary). Eligibility is necessary but not sufficient: a
valid `/data` path does not prove a backup exists, a manifest does not prove files were copied, a
copied file does not prove integrity, and a verified backup does not prove it can be restored.
Because a profile switch (ADR-0003) alters how state is derived, it must be preceded by evidence
that the current state was actually captured and is actually recoverable — not merely eligible.

## Decision

- Lighthouse gains a **read-only backup-manifest system** and an **isolated restore-drill
  workflow**, implemented as deterministic, fail-closed modules (`modules/backup_manifest.py`,
  `modules/backup_create.py`, `modules/backup_verify.py`) and four CLI commands
  (`backup-manifest`, `backup-create`, `backup-verify`, `restore-drill`).
- The workflow is a strict ladder in which **no step implies the next succeeded**:
  authoritative-state inventory → boundary validation → manifest generation → backup copy to an
  explicit destination → independent verification → restore into an isolated destination →
  restored-state verification → restore-drill report.
- A versioned manifest (`lighthouse_backup_manifest.v1`) records identity, classification, path,
  type, size, and SHA-256 integrity for every included item, and **never embeds file contents,
  rows, or secrets**. Path eligibility alone is insufficient; a completed, independently verified
  backup copy and a passing isolated restore drill are all required.
- **Failure, partial completion, or uncertainty (UNKNOWN/PARTIAL/INDETERMINATE) is never treated
  as success** and blocks any future profile activation.
- SQLite databases are captured with the SQLite online-backup API (consistent snapshot), never a
  naive byte copy of a live database; the source is never mutated.
- **Production restoration is a separate, still-future capability.** A restore drill restores into
  an isolated, explicitly non-`/data` destination, never starts Lighthouse against the restored
  data, and explicitly does not prove production disaster recovery.
- **Lighthouse owns evidence about its own persistent state.** The Gateway (ADR-0001) may
  eventually require and approve backup/restore proof artifacts, but it does not create Lighthouse
  persistence evidence.
- **This packet implements no profile activation or switching.** Every report pins
  `profile_activation_authorized: false` and `production_restore_performed: false`.

## Consequences

- Activation (ADR-0003) gains a second, stronger precondition beyond readiness (ADR-0004): a
  verified backup plus a passing restore drill.
- The four commands are safe to run anytime: they are read-only toward the source, write only to
  explicit operator-chosen destinations, and fail closed on unsafe destinations (overlap, `/data`,
  non-empty) and on any integrity/traversal/symlink/special-file hazard.
- A future encryption layer can wrap the backup payload without changing manifest semantics.

## Repository evidence

- `modules/backup_manifest.py`, `modules/backup_create.py`, `modules/backup_verify.py`;
  `docs/schemas/lighthouse_backup_manifest.v1.schema.json` (reference mirror; the dataclass
  `validate()` is authoritative).
- CLI: `backup-manifest`, `backup-create`, `backup-verify`, `restore-drill` in `main.py`.
- Tests: `tests/unit/test_backup_manifest.py`, `test_backup_create.py`, `test_backup_verify.py`,
  `test_cli_backup.py`.
- `docs/BACKUP_READINESS_AND_PROFILE_SWITCHING.md`; `docs/SECURITY.md`; `docs/PROFILE_SECURITY_RISKS.md`.
