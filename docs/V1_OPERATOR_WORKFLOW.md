# Lighthouse V1 — Repository Capability Discovery Operator Loop

> **LHP-V1-RCD-01A / LHP-V1-TRUTH-01.** Documents the **honest V1 claim** for Lighthouse on the
> finish-state branch: one closed operator loop for **`repository_capability_discovery`** via
> `repository_exact_content_recurrence`, plus backup-gated initial activation and OSR producer export.

## V1 definition of done

Lighthouse V1 means an operator can, on an allowlisted host, with unit-tested CLI handlers:

1. Run the **RCD loop** — observe → query → detect → review → idempotent re-detect (isolated store).
2. Perform **backup-gated initial activation** — `no_active_profile → repository_capability_discovery`
   v2 via `lighthouse profile-activate` (typed confirmation + verified backup/restore evidence).
3. Rely on **active-profile admission** — `observe` and `patterns detect` (RCD detector) refuse
   without a matching active profile; historical reads/reviews stay ungated.
4. Export one **OSR** (`observed_source_reference.v0`) from a validated `available` public-source
   check (`lighthouse public-sources export-osr`).

### Explicitly not V1

- Profile **switching** or activating any profile other than RCD v2
- Scientific Preservation **activation** or SciPres detect under ceremony (detector exists; activation
  path deferred — see `tests/unit/test_v1_public_source_loop.py` for ledger+OSR+detect with planted SP)
- Live host activation against production `data/state.db` (use disposable rehearsal — `ops/v1_rcd_host_rehearsal.sh`)
- Hypothesis/finding promotion, Gateway approval, invention migration, `uv` packaging
- Claiming production deployment or StartOS completeness

Separate surfaces: Scientific Preservation public-source ledger + disappearance detector
(`lighthouse public-sources`, `scientific_source_disappearance_review`) and OSR export are
**implemented** but only the OSR export is in the V1 claim above. See
`docs/SCIENTIFIC_PRESERVATION_CANDIDATES.md` and `docs/PUBLIC_SOURCE_CHECKS.md`.

## What the RCD slice proves

Approved repository inputs → dry-run observation → persisted observation → query → exact-content
recurrence detection → candidate list/show/review → idempotent re-detection — on an isolated
temporary configuration and database, **after** RCD activation (or planted active state in tests).

## Prerequisites

1. **Host-only.** The deployed container has no `git` and configures no observation roots; it
   observes nothing by design. This loop runs on a development host with `git` available.
2. A Python environment with the `lighthouse` CLI installed (`pip install -e ".[dev]"`).
3. Two or more local git repositories under an operator-approved root, with **identical tracked
   file bytes** in at least one path (the detector's trigger).
4. **Active RCD profile.** `lighthouse observe` and `lighthouse patterns detect` require
   `repository_capability_discovery` v2 to be active. Use the activation ceremony below or the
   disposable rehearsal script.

## Isolated configuration and data store

Do **not** use the host `data/state.db`. Point both the data directory and the database at a
disposable directory:

```bash
WORKDIR=$(mktemp -d /tmp/lighthouse-v1-rcd-XXXXXX)
export LIGHTHOUSE_DATA_DIR="$WORKDIR/data"
export DB_PATH="$WORKDIR/data/state.db"
mkdir -p "$LIGHTHOUSE_DATA_DIR" "$WORKDIR/repos"

# Trust boundary (empty => observe REFUSES)
export LIGHTHOUSE_OBSERVATION_ROOTS="$WORKDIR/repos"

# Work list — copy the example, then replace placeholder paths with real repos
# under LIGHTHOUSE_OBSERVATION_ROOTS only. The work list may narrow the roots; it
# cannot widen them (no root field exists in the schema).
cp examples/observation_sources.v1.json "$LIGHTHOUSE_DATA_DIR/observation_sources.json"
# edit paths in that file to point at repositories under $WORKDIR/repos
```

Optional: `profile_id: "repository_capability_discovery"` on a source names the **owning profile of
the rule**; with an active RCD profile, null `profile_id` is stamped from the active profile.

## Backup-gated initial activation (RCD v2 only)

One safe transition: `no_active_profile → repository_capability_discovery` v2. Requires verified
backup + restore drill + transition preview evidence. **No switch, no Gateway, no other targets.**

`--verification-report` **must** be typed output from `lighthouse backup-verify`
(`report_schema: lighthouse_backup_verification.v1`, `status: verified`) bound to the
supplied manifest's file-byte digest. `backup-create` writes `reports/verification.json`
with `status: passed`; that embed is a different, untyped contract and **cannot** make
`profile-transition-preview` `ready_for_review` or satisfy `profile-activate`.
`--restore-report` **must** be typed output from `lighthouse restore-drill` (`--output PATH`,
`report_schema: lighthouse_restore_drill.v1`, `status: completed`).

```bash
BACKUP="$WORKDIR/backup"
RESTORE="$WORKDIR/restore"
lighthouse backup-create \
  --data-dir "$LIGHTHOUSE_DATA_DIR" \
  --destination "$BACKUP" \
  --boundary "$LIGHTHOUSE_DATA_DIR" \
  --db-path "$DB_PATH"

lighthouse backup-verify --backup "$BACKUP" --json > "$WORKDIR/verification.json"

lighthouse restore-drill \
  --backup "$BACKUP" \
  --destination "$RESTORE" \
  --source-data-root "$LIGHTHOUSE_DATA_DIR" \
  --output "$WORKDIR/restore.json"

lighthouse profile-transition-preview \
  --target-profile repository_capability_discovery \
  --backup-manifest "$BACKUP/manifest.json" \
  --verification-report "$WORKDIR/verification.json" \
  --restore-report "$WORKDIR/restore.json" \
  --output "$WORKDIR/preview.json"

lighthouse profile-activate \
  --target-profile repository_capability_discovery \
  --target-version 2 \
  --confirm "ACTIVATE repository_capability_discovery v2" \
  --transition-preview "$WORKDIR/preview.json" \
  --backup-root "$BACKUP" \
  --backup-manifest "$BACKUP/manifest.json" \
  --verification-report "$WORKDIR/verification.json" \
  --restore-report "$WORKDIR/restore.json" \
  --data-dir "$LIGHTHOUSE_DATA_DIR" \
  --json

lighthouse active-profile --json
```

A full disposable CLI-only rehearsal (throwaway `LIGHTHOUSE_DATA_DIR` / `DB_PATH` / observation
roots; never production `data/state.db`) is `ops/v1_rcd_host_rehearsal.sh`. It runs the ceremony
above, then persist `observe` → query → `patterns detect` → list/show/review → detect again.

## Dry-run observation

```bash
lighthouse observe --dry-run --json
```

Writes **nothing** — not a row, not a table, not the database file. Exit 0 with
`previewed` counts only when every declared source is observable **and** RCD is active.

## Persist observations

```bash
lighthouse observe --json
```

Insert-only, idempotent by content identity. An unchanged repository re-observed later reports
`already_present`, not a duplicate row.

## Query stored facts

```bash
lighthouse observations list --limit 5 --json
lighthouse observations where --entity-id content:<sha256-of-exact-raw-bytes> --json
```

Read-only (`SQLite mode=ro`). Counts and lists; never ranks. `content:<sha256>` is
`SHA-256(exact raw bytes)` of a tracked file — not the path, not git's blob SHA-1.

## Exact-content recurrence detection

```bash
lighthouse patterns detect --dry-run --json   # writes nothing
lighthouse patterns detect --json             # persist candidates (insert-only)
```

Default detector: `repository_exact_content_recurrence`, owned by
`repository_capability_discovery`. Trigger: one `content:<sha256>` entity directly observed in
**≥2 distinct repository scopes** (a set of scope ids, never a row count).

## Candidate list, show, and review

```bash
lighthouse patterns list --json
lighthouse patterns show --id <pc_… or unambiguous ≥8-hex prefix> --json

lighthouse patterns review --id <candidate-id-or-prefix> \
  --operator-id you \
  --decision needs_more_evidence \
  --reason "Repository lineage has not been assessed."
```

Decisions (closed): `accepted_for_followup` | `dismissed` | `needs_more_evidence`.
A disposition is a **local note**, not authority — one per candidate **version**, enforced by a
`UNIQUE` index. `operator_identity_authenticated` is always `false` (a CLI flag is not an identity).

## Repeat detection and idempotency

```bash
lighthouse patterns detect --json
```

Identical facts reproduce the same `candidate_id` (`pc_` + artifact digest). A second detect with
unchanged evidence reports `stored: 0` / `already_present` and does **not** duplicate the candidate.

## OSR export (V1 producer)

```bash
lighthouse public-sources export-osr --check-id psc_<sha256> \
  --consumer article-to-skills --json
```

Only `outcome=available` checks qualify. Not active-profile gated. Cross-repo consumer interop is
**trial only** — see `docs/PUBLIC_SOURCE_CHECKS.md`.

## Cleanup and recovery expectations

- Delete `$WORKDIR` to discard the disposable store and sources file.
- Dry-run observe/detect leave **no** database file behind when the store did not already exist.
- Recovery on disposable paths is "re-run the isolated loop"; production recovery uses verified backups.

## Automated proof

```bash
python -m pytest tests/unit/test_v1_operator_loop.py tests/unit/test_v1_profile_activation.py -q
```

The operator-loop test plants post-activation RCD state (ceremony covered by profile-activation tests).
It drives real CLI handlers against temporary git repositories and a temporary database. No host
datastore and no network.

## Truthful limitations — V1 does **not** claim

- Profile switching or SciPres activation via CLI
- Long-running scheduling or continuous monitoring
- Hypothesis or finding promotion
- Gateway approval or remote/fleet activation
- External action or publication authority
- Production deployment completeness on StartOS
- That every bundled intelligence profile is activatable

A candidate says *"these direct facts satisfy one declared review rule; look at this"* and must
never say more (ADR-0021; `docs/PATTERN_CANDIDATES.md`).
