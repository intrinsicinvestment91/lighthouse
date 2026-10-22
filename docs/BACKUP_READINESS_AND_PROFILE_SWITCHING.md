# Backup Readiness & Profile Switching

> Status: **inspection + verified backup + restore drill** (packets LHP-PROFILE-01,
> LHP-PROFILE-02). LHP-PROFILE-01 shipped the read-only backup-readiness inspector
> (`modules/backup_readiness.py`). LHP-PROFILE-02 adds a read-only backup-manifest system and an
> isolated restore-drill workflow (`modules/backup_manifest.py`, `modules/backup_create.py`,
> `modules/backup_verify.py`; CLI `backup-manifest`/`backup-create`/`backup-verify`/`restore-drill`).
> Together these can inventory, copy, verify, and restore-into-isolation a specific state set.
> They still do **not** schedule backups, perform a production restore, or implement profile
> switching. **A completed backup or restore drill does not authorize a profile switch.**

## The `/data` boundary

On Start9, all authoritative writable state must live beneath the persistent volume mounted at
`/data`, which is what StartOS backs up (`compat duplicity` over the volume). Runtime state paths
resolve through `LIGHTHOUSE_DATA_DIR` (default `data`; `/data` on Start9) — **the application data
directory itself**, never a parent under which `data/` is appended. `modules/data_paths.py` is the
canonical reader of `LIGHTHOUSE_DATA_DIR` and `DB_PATH`. The operational database used through
`modules/db.py` derives as `$LIGHTHOUSE_DATA_DIR/state.db` unless `DB_PATH` explicitly overrides
it. `AppConfig.state_db_path` and `DmsConfig.db_path` remain rooted at
`$LIGHTHOUSE_DATA_DIR/state.db` and do not follow a differing `DB_PATH`.

**Boundary check is canonical, not textual.** `is_within_boundary(path, boundary)` resolves both
paths (`Path.resolve()` — collapsing `..` and following symlinks) and returns true only if the
resolved path equals or descends from the resolved boundary. This rejects:

| Example | Verdict |
|---------|---------|
| `/data/lighthouse.db`, `/data/db/lighthouse.sqlite3`, `/data/graph/state.db`, `/data/archive/` | within (safe) |
| `/tmp/lighthouse.db`, `/root/lighthouse.db`, `/home/start9/lighthouse.db`, `/var/lib/lighthouse/lighthouse.db` | outside (rejected) |
| `/data/../tmp/lighthouse.db` (traversal) | outside (rejected) |
| a symlink under `/data` pointing outside it | outside (rejected) |
| `data/state.db` (relative) | ambiguous (rejected) |

## State classification

`modules/backup_readiness.py` classifies every state item (`STATE_CATEGORIES`):

| Category | Meaning | Examples | Gates readiness? |
|----------|---------|----------|------------------|
| `authoritative_persistent` | Irreplaceable operator/derived state | `state.db`, `entities.json`, `targets.json`, `archives/`, future `profiles/` | **Yes** |
| `reconstructable_persistent` | Re-derivable by re-running collectors | `patents/`, `arxiv/`, `flights/`, `sec/`, `adsb/`, `velocity/`, `correlations/`, `nostr/`, `narrative/` | No (informational) |
| `runtime_ephemeral` | Safe to lose; regenerated | `logs/`, `__pycache__` | No |
| `bundled_immutable` | Shipped in the image, re-supplied by reinstall | `seed/`, app code, Playwright Chromium | No |
| `external_unmanaged` | An authoritative path configured **outside** the boundary | a `DB_PATH` pointing outside `/data` | **Yes — blocks** |

## Readiness verdict (fail-closed)

`inspect_backup_readiness(boundary="/data", ...)` returns `status ∈ {READY, NOT_READY, UNKNOWN}`
plus per-check detail, `authoritative_state_complete`, `backup_boundary_valid`,
`restore_procedure_documented`, `schema_versions_known`, `blocking_reasons`, and `warnings`.

- **NOT_READY** — any authoritative path resolves outside the boundary, the boundary is not an
  absolute path, a path is ambiguous/uncanonicalizable, `DB_PATH` escapes the boundary, or a
  required schema version is unknown.
- **UNKNOWN** — the boundary is valid but authoritative state has not yet been created
  (explicit pre-runtime). **UNKNOWN is never treated as approval.**
- **READY** — all required authoritative state exists and resolves under the boundary.

### Eligibility is not proof of a completed backup

A `READY` verdict asserts only that authoritative state is **boundary-eligible for backup** — it
does **not** assert that a backup has happened. The result always carries
`backup_performed: false` and a note to that effect. Being under `/data` proves *where* state
lives, not *that a snapshot was taken*.

## The proven live-backup method (ID-P1B)

A backup is validated non-mutating when: the source `state.db` sha256 is identical **before and
after** the `sqlite3 -readonly ".backup"`, and `cmp -l` between the live file and the snapshot
shows differences confined to the **100-byte SQLite header** (the ~7 header bytes: file change
counter, schema cookie, version-valid-for, writing-library version). Any content-page difference
or a moved source hash means investigate before trusting the snapshot. Backups are scheduled
between the ~06:00Z daily runs when the DB is quiescent (no `-wal`/`-shm`).

## Verified backup manifest & isolated restore drill (LHP-PROFILE-02)

This is the ladder that turns "boundary is eligible" into "this state set can be inventoried,
copied, verified, restored into isolation, and compared" — with **no step implying the next
succeeded** and every step failing closed. See ADR-0013.

```
authoritative-state inventory → boundary validation → manifest generation
  → backup copy to an explicit destination → independent verification
  → restore into an isolated destination → restored-state verification → restore-drill report
```

### The backup manifest (`lighthouse_backup_manifest.v1`)

`generate_manifest()` deterministically inventories authoritative state (optionally the
reconstructable module dirs, clearly distinguished; `runtime_ephemeral` excluded by default;
`external_unmanaged` authoritative state **blocks**). Every entry records identity, state
category, relative payload path, type (`file`/`directory`/`sqlite_database`), size, SHA-256
digest, and — for databases — a `PRAGMA quick_check` result. **The manifest never embeds file
contents, database rows, or secrets.** The authoritative representation is the `BackupManifest`
dataclass + `validate()`; `docs/schemas/lighthouse_backup_manifest.v1.schema.json` is a
human/Gateway-facing reference mirror. Generating a manifest is read-only toward the source.

### SQLite safety

Databases are captured with the SQLite online-backup API
(`sqlite3.Connection.backup()`), which produces a transactionally-consistent copy even for
WAL-mode or actively-written databases — never a naive byte copy, and never assuming `-wal`/`-shm`
sidecars alone are a valid backup. The source is opened read-only and never mutated. A database
that cannot be snapshotted consistently fails the whole operation rather than producing an
ambiguous copy. This complements the ID-P1B live-backup method below.

### Backup layout

```
backup-root/
  manifest.json
  payload/    <authoritative files and directories, relative paths only>
  reports/verification.json
```

Manifest paths are relative to `payload/`; absolute paths, `..` traversal, and duplicate
normalized paths fail closed. Creation stages into a temporary sibling directory and atomically
renames into place, so an interrupted creation never leaves a half-finished backup mistaken for a
finalized one; a failed creation cleans up only its own staging output and never deletes unrelated
destination content.

### Commands & safety

| Command | Effect | Writes? |
|---------|--------|---------|
| `lighthouse backup-manifest --data-dir <src> --output <manifest.json>` | Inventory only | Manifest file only — **no data copied** |
| `lighthouse backup-create --data-dir <src> --destination <new-root>` | Copy + independently verify | Explicit destination only |
| `lighthouse backup-verify --backup <root>` | Independent re-verification | None (read-only) |
| `lighthouse restore-drill --backup <root> --destination <new-empty-root>` | Restore into isolation + re-verify | Explicit isolated destination only |

Source and destination must be explicit and must not overlap; a backup destination must be
new/empty; a **restore-drill destination may never be `/data` or beneath it**, must not overlap the
backup or source, and must be empty. Non-success returns a nonzero exit code.

### What the restore drill proves — and does not

A completed drill proves the manifest is valid, the payload verifies, the payload can be restored
into isolation, restored digests match, and restored databases pass integrity checks — while
leaving the backup and the original source unchanged. It explicitly **does not** prove production
disaster recovery, never restores into `/data`, never starts Lighthouse against the restored data,
and **never authorizes profile activation** (`production_restore_performed: false`,
`profile_activation_authorized: false`).

## Transition preview (`profile_transition_preview.v1`, LHP-PROFILE-03 + 14A)

A switch must never begin with "activate profile X"; it begins with "generate a complete transition
preview." The preview (`modules/profile_transition.py`, `lighthouse profile-transition-preview`) is a
deterministic, read-only **review artifact** — evidence, not a command. It never activates, switches,
writes DB state, executes a collector, or contacts the Gateway.

It records the target (and optional current) profile identity, a **declarative diff** of their
policies — all eight categories on the wire since LHP-PROFILE-14A (sources, collectors, graph,
correlation, privacy, publishing, plus the `local_only_changed`/`dms_eligibility_changed` scalar
diffs), **references** to the LHP-PROFILE-02 backup evidence (manifest id + a `backup_manifest_sha256`
content fingerprint + verification/restore statuses — status and fingerprint only, never contents),
the approvals a **future Gateway** would need to collect, and a SHA-256 `artifact_digest` over the
canonical content (excluding exactly the volatile `preview_id`/`created_at`/`evidence_timestamp` and
the digest itself, so identical transition + evidence yields an identical digest).

Since LHP-PROFILE-14A it also carries **producer-owned execution facts**, all digest-covered
([ADR-0015](adr/0015-preview-carries-producer-owned-execution-facts.md)): a typed
`target_policy_snapshot` (`local_only`, `dms_eligibility`, `data_root`,
`schema_migration_required`) derived only from a validated target profile — replacing the string-token
recovery the Gateway previously performed; a `target_profile_sha256` binding the target profile's
full content (including policy blocks the diff does not cover); and `lighthouse_version` /
`repository_commit` provenance stamps (nullable in deployments without dist metadata or git).

It fails closed. `transition_status` is `ready_for_review` **only** when both profiles are valid and
present, backup evidence is present and passing (manifest id + content fingerprint + `verified` (or
create-time `passed`) + `completed`), and the producer execution facts are present.
Otherwise: missing evidence → `incomplete`; failed/unknown evidence → `blocked`; a
malformed/unavailable profile or unsupported schema → `invalid`. Unknown never becomes ready. For
**initial** activation (no current profile), `current_profile` is empty. For an **active-to-active**
switch preview today, `current_profile` is **operator-supplied** through the
`--current-profile` CLI flag and resolved against the registry (`main.py`,
`generate_transition_preview_from_registry`) — it is **not** read from the active-profile store.
Future switch execution must derive authoritative current identity from the store and refuse any
disagreement with preview input ([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) §13).
A preview is evidence, not authority. Lighthouse generates this evidence; the Gateway remains the
future approval authority
([ADR-0014](adr/0014-transition-preview-artifacts-before-profile-activation.md), ADR-0001).

## Rollback boundary (LHP-PROFILE-14B — documented; no rollback code exists)

Before any future activation can be approved, its rollback story must be honest about what a
rollback **can** and **cannot** promise ([ADR-0016](adr/0016-activation-capability-boundary.md);
the Gateway's execution plan discloses the same limits as `STANDARD_ROLLBACK_LIMITATIONS`).

**Rollback can restore:** the files inventoried in a verified backup manifest, into an
**isolated empty destination** — this is exactly what the shipped `restore-drill` proves,
including safe SQLite online-backup snapshots.

**Rollback cannot restore:**

- anything not in the backup — observations, alerts, correlations, and graph writes made
  **after** the backup was taken;
- published Nostr events (irrevocable once broadcast) and IPFS pins;
- data collected under a newly-activated profile — a pointer-style deactivation does not purge
  it (`pointer_rollback_does_not_purge_data_collected_after_activation`);
- secrets and environment configuration living outside `/data`.

**Why production restore ≠ restore drill:** the drill refuses `/data` and everything beneath it
*by design*, never stops or starts the service, never performs cutover, and proves
recoverability **into isolation** only (`restore_drill_proves_isolated_restore_only`,
`production_restore_never_performed`). A production restore is a different, still-missing
capability (`lighthouse.production_restore`): it overwrites the live volume, requires the
service stopped and the backup independently verified first, and must be fail-closed and
never-partial. For **initial** activation, a rollback target exists once an active profile is
recorded (`active_profile_state`). For **active-to-active switching** (not implemented), rollback
semantics are defined in [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md).

## Required future switch workflow (initial RCD activation shipped; switching not implemented)

Narrow backup-gated **initial** activation of `repository_capability_discovery` v2 is shipped
(`lighthouse profile-activate`, `modules/profile_activation.py`, `modules/active_profile_store.py`).
**Active-to-active switching is not implemented** — see [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md)
for the governing design.

A future profile switch must run, fail-closed at every step (uncertainty **delays**, never
proceeds):

```
Preflight (DB quiescent, inventory complete)
  → inspect_backup_readiness() == READY   [hard gate — decision #3 / ADR-0004: backup on every switch]
  → backup-create (verified backup + manifest + payload)          [IMPLEMENTED — LHP-PROFILE-02]
  → backup-verify (independent integrity recompute)               [IMPLEMENTED — LHP-PROFILE-02]
  → restore-drill (restore into isolation + compare)              [IMPLEMENTED — LHP-PROFILE-02]
  → transition preview (what changes; evidence supporting it; approvals needed) [IMPLEMENTED — LHP-PROFILE-03]
  → operator approval (Gateway control plane)                     [future — widening switches only]
  → initial activation (no_active → profile)                      [IMPLEMENTED — RCD v2 only; LHP-V1-PROFILE-01C]
  → active-to-active switch (fenced cutover)                      [future — ADR-0024 design only]
  → post-switch behavioral validation                             [future — requires CORE-RESOLVE-01]
  → production rollback path proven (restore backup → live /data) [future — drill ≠ production restore]
```

Note the distinction: `restore-drill` proves recoverability **into isolation**; a production
rollback that restores into the live `/data` volume is a separate, still-future capability. A
passing drill is a necessary input to a future approval, never an authorization by itself.

Since LHP-V1-PROFILE-01C the **initial activation** step is shipped for RCD v2 only
(`lighthouse profile-activate` → `active_profile_state` store). The **active-to-active switch**,
post-switch behavioral validation, and production restore steps remain future work governed by
[ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md). Contracts for the switch path —
`active_profile_state.v1`, the activation-preconditions evaluator, and `activation_intent.v1`
([ADR-0016](adr/0016-activation-capability-boundary.md)) — exist; the Gateway activation endpoint
and widening-switch authorization do not.

## Controlled switch lifecycle (ADR-0024 — design only)

[ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) defines the complete fail-closed
lifecycle for an active-to-active switch. Summary:

- **Switch-authority projection** (`profile_switch_authority_projection.v1`, prerequisite
  `LHP-LIFE-AUTHPROJ-01`): complete widening guard with **presence-tagged** extraction and per-
  `dimension_id` comparison — no absent-key defaulting; the shipped 14A `target_policy_snapshot` does
  **not** contain `public_publishing`; `indeterminate` refuses unconditionally;
  `locally_activatable` enrollment is **code/release-owned, not a seed field** (currently empty) with
  digest binding and atomic cutover revalidation.
- **Transition-control record** (`profile_transition_control`) in the **same SQLite database** as the
  active-profile singleton and switch journal — authoritative fence; journal is history only.
- **Thirteen-state attempt journal** (`prepared` through `committed` / `rolled_back` /
  `recovery_required` / `refused` / `aborted`); `staging`/`staged` dormant until migration is reachable.
- **Admission generation** bumps at fence-held boundaries; `refused` from `prepared` does not bump;
  `prepared → aborted` before fence acquisition does not bump; any pre-cutover abort after fence
  acquisition bumps with atomic fence release; workers revalidate inside **every** mutating transaction.
- **Pointer-bound recovery** (`pointer_bound_state ∈ {current, target, unknown_or_corrupt}`): pre-cutover
  `recovery_required` may `aborted` only; post-cutover may `validating`, `rolling_back`, or remain fenced
  — never `aborted`; `unknown_or_corrupt` remains fenced with no authority-releasing transition (§6.1).
- **Complete mutating-surface inventory** (§8 of ADR-0024) including public-source checks, backup,
  legacy pipeline, portal, and MCP — refused until classified where outside profile governance.
- **Atomic cutover boundary:** compare-and-set pointer rewrite in one `BEGIN IMMEDIATE` transaction;
  database atomicity is not runtime convergence; power-loss durability unproven.
- **Preview is evidence, not authority** — future switch execution derives current identity from the store.
- **No deactivate-first sequence:** a normal switch never persists `no_active_profile`.
- **Normal deactivation unsupported;** `recovery_required` is the only sanctioned absence posture.

Nothing in this section is implemented. Active-to-active switching remains planned
(`LIFE-SWITCH-01`).

Rollback for *this* packet is trivial and never touches data: revert the added modules, seed
files, docs, and tests. This packet introduces no condition whose rollback would require restoring
persistent state.
