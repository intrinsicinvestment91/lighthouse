# Lighthouse Production Operations SOP

This SOP defines repeatable, safe production operations for Lighthouse.

## Production Readiness Gates

1. Entity seed validation passes
2. Unit suite passes
3. Dry-run exits 0
4. Live local run exits 0
5. Nostr heartbeat verified
6. High-signal publish gate verified

## Required Commands

```bash
python scripts/validate_entities.py data/entities.json
env -u NOSTR_PRIVATE_KEY_HEX -u NOSTR_PUBLISH_CORRELATIONS python -m pytest tests/unit -v --tb=short
lighthouse --module all --dry-run --since-days 7
lighthouse --module all --since-days 7
```

## Environment Variables

Never commit runtime secrets or local values for these variables.

- `NOSTR_PRIVATE_KEY_HEX` — required for heartbeat and Nostr publishing.
- `NOSTR_RELAYS` — required for heartbeat and Nostr publishing.
- `LIGHTHOUSE_REGION` — required for region tagging on heartbeat and Nostr publishing.
- `NOSTR_PUBLISH_CORRELATIONS` — required (must be true) for correlation publishing.
- `ANTHROPIC_API_KEY` — optional; improves narrative signal coverage.
- `WIPO_API_KEY` — optional; improves patent signal coverage.
- `IPFS_PINNING_SERVICE` — optional; required only when using non-local IPFS pinning.
- `PINATA_JWT` — optional; required only if `IPFS_PINNING_SERVICE=pinata`.
- `WEB3STORAGE_TOKEN` — optional; required only if `IPFS_PINNING_SERVICE=web3storage`.
- `PROXY_LIST` — optional; can improve resilience in constrained networks.

## Safe Publishing Rules

- Raw alerts are not published.
- Only correlation events may publish.
- `NOSTR_PUBLISH_CORRELATIONS` must be `true`.
- Correlation score must be `>= 7.0`.
- At least two unique signals are required.
- Evidence payloads are not published.

## Backup / Restore (Local)

```bash
mkdir -p backups
cp data/state.db backups/state-before-run-$(date +%Y%m%d-%H%M%S).db
cp backups/<file>.db data/state.db
```

## Log Handling

```bash
mkdir -p logs
lighthouse --module all --since-days 7 > logs/live-$(date +%Y%m%d-%H%M%S).log 2>&1
tail -200 logs/live-*.log
```

## Query Commands

```bash
lighthouse status
lighthouse alerts --limit 20 --json
lighthouse correlations --limit 20 --json
lighthouse graph --entity "Lockheed Martin" --depth 2 --json
```

## Declaring Public Sources (LHP-INTEL-04B)

`lighthouse public-sources check` fetches admitted public HTTPS locators and records an append-only history of the outcomes. Two declarations are required and are **not** interchangeable:

1. `LIGHTHOUSE_PUBLIC_SOURCE_HOSTS` — the trust boundary (comma-separated exact hostnames). **Empty (default) => `check` REFUSES.**
2. `data/public_source_targets.json` — the work list (`public_source_targets.v1`; `targets[]` with `locator`). It may only narrow the hosts, never widen them.

```bash
export LIGHTHOUSE_PUBLIC_SOURCE_HOSTS=archive.example.org,data.example.edu
lighthouse public-sources check --targets-file data/public_source_targets.json --dry-run  # writes nothing
lighthouse public-sources check --targets-file data/public_source_targets.json            # persist
lighthouse public-sources runs
lighthouse public-sources history --content-digest <sha256>
```

The ledger stores metadata and the exact-byte digest, **never the response body**. A `not_found` is HTTP 404/410, not a claim that a source is gone. No candidate, no scheduler, no Gateway (ADR-0022).

## Declaring Observation Sources (LHP-INTEL-01E)

`lighthouse observe` reads local repositories and documents you have explicitly declared, records what
exists, and stops. It never scores, ranks, alerts, or decides that anything it found is interesting. It is
**host-only** — the container has no `git` and nothing of yours is mounted into it.

Two declarations are required, and they are deliberately **not** the same thing:

**1. The trust boundary** (`LIGHTHOUSE_OBSERVATION_ROOTS`) — the directories Lighthouse is permitted to
look inside at all. `os.pathsep`-separated. **Empty is the default, and empty refuses**: nothing on your
disk is observable until you say so. There is no default path and no home-directory scan.

```bash
export LIGHTHOUSE_OBSERVATION_ROOTS=/home/you/Projects:/home/you/Documents
```

**2. The work list** (`data/observation_sources.json`) — what, inside those roots, to actually observe. It
may only *narrow* the boundary; it can never widen it, and there is no key in which a root could be
declared.

```json
{
  "schema_version": 1,
  "sources": [
    {"type": "repository", "path": "/home/you/Projects/widget"},
    {"type": "document",   "path": "/home/you/Documents/spec.pdf"},
    {"type": "repository", "path": "/home/you/Projects/old", "enabled": false}
  ]
}
```

```bash
lighthouse observe --dry-run      # observe + normalize; writes NOTHING (not even the tables)
lighthouse observe                # persist — insert-only, idempotent by content identity
lighthouse observe --json         # machine-readable; errors go to stderr, so stdout stays parseable
lighthouse observe --source /home/you/Projects/widget   # one DECLARED source (not a new path)
```

Re-running against an unchanged source reports `already_present` and writes nothing: identity is content, so
observing something a hundred times stores it once. A source that fails (missing, outside the roots, a
symlink escaping them, an unsupported format, not a git repository) fails **itself** — the other sources
still run — and the command exits non-zero.

Reading is not writing: the observed tree, `.git/` included, is byte-identical afterwards.

## Known Operational Issues

- Patent module may fail due to DNS/network/API availability.
- arXiv may return 429 rate limits.
- IPFS daemon may be unreachable if not configured.
- Zero publish events is acceptable if no correlation passes the gate.

## Rollback

```bash
NOSTR_PUBLISH_CORRELATIONS=false
cp backups/<file>.db data/state.db
```

---

## Start9 Backup Strategy

Start9 (StartOS) has **built-in backup support** configured in `lighthouse-startos/manifest.yaml`. The package uses `duplicity` to back up the entire `/data` volume — a *wholesale volume* snapshot that incidentally captures everything on the volume, including logs.

> **Canonical state-classification policy (see `modules/backup_readiness.py` and ADR-0013).** Two backup mechanisms coexist and must not be conflated:
> - The **StartOS volume backup** copies the whole `/data` volume, so `logs/` are captured *incidentally* — not because they are authoritative.
> - The **Lighthouse verified-backup manifest** (`lighthouse backup-create`) is *curated*: it includes `authoritative_persistent` state (`state.db`, `entities.json`, `targets.json`, `archives/`), optionally the `reconstructable_persistent` module dirs, and **excludes `runtime_ephemeral` state — including `logs/` — by default.**
>
> Canonically, **`logs/` are `runtime_ephemeral`**: re-generated at runtime, not required to restore intelligence, and not part of the authoritative manifest. Their presence in a StartOS volume backup is a convenience, not a durability guarantee. (This is a documentation reconciliation only; it does not change runtime logging behavior.)

**Using Start9's native backup:**

Use the StartOS web UI → Lighthouse → Actions → Create Backup. This snapshots the full `/data` volume using the configured `BACKUP` destination. Restore is also available from the UI: Lighthouse → Actions → Restore Backup.

**Manual extraction via SSH (if Start9 backup target is unavailable):**

```bash
# Copy state.db off the node before any maintenance
scp start9-node.local:/path/to/lighthouse/data/state.db ./backups/state-$(date +%Y%m%d-%H%M%S).db
```

**What the `/data` volume contains:**

| Path | Contents |
|------|---------|
| `/data/state.db` | Full SQLite DB: alerts, correlations, graph nodes/edges, callsign history, all 13 tables |
| `/data/targets.json` | Operator target profiles (seeded from image, operator-customizable) |
| `/data/entities.json` | Entity seed (seeded from image, operator-customizable) |
| `/data/logs/` | Rotating run logs (last 14 kept) |
| `/data/archives/` | Archived HTML pages and manifest JSONs |
| `/data/patents/`, `/data/flights/`, etc. | Module output JSONs |

All intelligence history, graph state, callsign observations, and correlation records live in the
`modules/db.py` database (default `$LIGHTHOUSE_DATA_DIR/state.db`, overridable via `DB_PATH`).
Per-module deduplication caches in `state_db.py` use `AppConfig.state_db_path` at
`$LIGHTHOUSE_DATA_DIR/state.db` and do not follow a differing `DB_PATH`. **Losing the operational
database means losing all historical intelligence.** Keep regular backups before updates.

---

## Start9 Restore Strategy

**Before any package update or reinstall:**

1. Create a StartOS backup via the web UI (Lighthouse → Backup).
2. Alternatively: SSH into the node and copy `/data/state.db` off to a safe location.

**After a restore:**

If state.db is restored from backup, run graph-backfill to ensure graph tables are consistent with correlation history:

```bash
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db \
    lighthouse graph-backfill --dry-run --limit 100
# Review output, then:
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db \
    lighthouse graph-backfill --limit 100
```

**Start9 volume reset behavior:**

StartOS performs a **volume reset on uninstall** by default. A same-version reinstall (stop → uninstall → install → start) **will wipe `/data`** unless a backup is restored afterward. Use the StartOS backup/restore feature to preserve operational history across package updates.

To avoid data loss during routine updates: create a backup *before* uninstall, then restore *after* install, before starting the service.

---

## Preserving Historical Intelligence State

The following files should be protected before any Start9 package update:

- `/data/state.db` — all intelligence history (graph, correlations, callsigns, alerts)
- `/data/targets.json` — operator-customized target profiles (if modified from defaults)
- `/data/entities.json` — operator-validated entity seed (if modified from defaults)

After restoring from backup:

1. Verify DB integrity: `lighthouse status`
2. Re-run graph-backfill if needed (see section below)
3. Run dry-run to confirm modules operate correctly: `lighthouse --module all --dry-run --since-days 7`

---

## Graph Backfill SOP

`lighthouse graph-backfill` is an idempotent command that repopulates `graph_nodes` and `graph_edges` from existing `correlation_events`. It is safe to re-run multiple times — repeated runs do not duplicate data.

**When to run:**

- After restoring `state.db` from backup
- After a volume reset (reinstall)
- After migrating DB from another environment
- Anytime graph queries return sparse or empty results despite correlations existing

**Procedure (always dry-run first):**

```bash
# Step 1: dry-run to see what would be backfilled
lighthouse graph-backfill --dry-run --limit 100

# Step 2: run live
lighthouse graph-backfill --limit 100

# Step 3: repeat with higher --limit until correlations_seen < limit
lighthouse graph-backfill --limit 500
```

**On Start9 (via podman exec):**

```bash
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db \
    lighthouse graph-backfill --dry-run --limit 100

podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db \
    lighthouse graph-backfill --limit 100
```

**Expected behavior:**

- First run backfills all historical correlations not yet in graph tables
- Subsequent runs produce 0 new nodes/edges (idempotent)
- Graph growth is roughly 2–4 nodes and 3–6 edges per correlation

**Caution:** Pre-Packet-12 correlations (including any false-positive events) will become graph-visible after backfill. Review correlation quality before running on a production volume with long history.

---

## Operational Continuity Verification

Run these commands after any package update, DB restore, or container restart to verify operational continuity:

```bash
# System health
lighthouse status

# Recent callsigns (verify callsign_observations table is intact)
lighthouse callsigns --limit 10

# Recent correlations
lighthouse correlations --limit 10

# Graph query for key entity
lighthouse graph --entity "DARPA" --depth 2 --json

# Targeted mode dry-run
lighthouse --module targeted --dry-run --since-days 1

# Verify target profiles and routes
lighthouse targets --show-routes
```

**On Start9 (via podman exec — must pass env vars explicitly):**

```bash
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db lighthouse status
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db lighthouse callsigns --limit 10
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db lighthouse correlations --limit 10
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db lighthouse graph --entity "DARPA" --depth 2
podman exec lighthouse.embassy env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db lighthouse targets --show-routes
```

Note: `podman exec` does not inherit the env vars exported by the entrypoint script. Pass
`env LIGHTHOUSE_DATA_DIR=/data DB_PATH=/data/state.db` explicitly for one-off commands (both match
the entrypoint contract when kept aligned; `DB_PATH` derives from `LIGHTHOUSE_DATA_DIR` inside
`modules/data_paths.py` when unset, but `podman exec` still needs the data-dir vars in its
environment).

---

## Start9 Runtime Env Consistency

| Variable | Set by | Value on Start9 | Notes |
|----------|--------|-----------------|-------|
| `LIGHTHOUSE_DATA_DIR` | manifest.yaml + entrypoint | `/data` | Application data directory; `modules/data_paths.py` is the canonical reader |
| `DB_PATH` | entrypoint (recommended) / derived in-app | `/data/state.db` | Explicit override of the `modules/db.py` SQLite file. When unset, `resolve_db_path()` derives `$LIGHTHOUSE_DATA_DIR/state.db` |
| `LIGHTHOUSE_DB_PATH` | removed (was dead) | — | Was in manifest but no module read it; removed in Packet 23 |

`AppConfig.state_db_path` and `DmsConfig.db_path` always resolve to `$LIGHTHOUSE_DATA_DIR/state.db`
via `config._data_path()` — they do not follow a differing `DB_PATH`. An explicit `DB_PATH` can
intentionally separate the alert/graph store from the per-module cache store; do not describe
`DB_PATH` as controlling every SQLite consumer.

All seed files are seeded to `/data/` on first run by the entrypoint:
- `/data/entities.json` ← `/app/seed/entities.json`
- `/data/targets.json` ← `/app/seed/targets.json`

Researchers are loaded directly from `/app/seed/researchers.json` by `config.py` (`_load_seed_researchers()`); they are not copied to `/data`.
