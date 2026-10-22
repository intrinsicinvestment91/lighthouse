# 🛰️ Lighthouse

> **A persistent, private, local-first intelligence platform** — watches public signals (SEC filings,
> military aviation, research publications, government newsrooms), normalizes them into scored alerts,
> correlates multi-signal events, builds a queryable graph, and surfaces only high-confidence findings.
> Runs unattended. Deterministic core. Stores an integrity hash with each correlation.

<sub>The product is **Lighthouse**; `pattern-match-sentinel` is the Git repository / package identifier.</sub>

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

---

## What it does

Lighthouse watches multiple public data sources and turns scattered information into structured
intelligence:

```text
Targets → Collectors → Alerts → Correlations → Graph → Temporal/Tempo → MCP / CLI / Local Gateway / Nostr
```

1. **Observes** public signals — SEC 8-K material filings, ADSB/OpenSky military aviation around key
   facilities, arXiv researcher activity, DoD/NASA/DoE newsrooms (archived to IPFS), and (optional)
   narrative tone analysis.
2. **Normalizes** each signal deterministically into a scored, provenanced `AlertRecord`.
3. **Correlates** multi-signal events (temporal + spatial + entity) into `CorrelationRecord`s with
   deterministic weighted scoring and a SHA-256 witness hash (signed attestation is not produced in the
   production path; the BIP-340 helper is demonstration/test-only).
4. **Builds a graph** of entities, events, signals, callsigns, and facilities — queryable via CLI and a
   read-only MCP interface.
5. **Derives temporal intelligence** — callsign recurrence, operational tempo, and mission patterns.
6. **Surfaces findings** over the CLI, a private loopback **Local Gateway**, read-only **MCP** tools,
   and gated high-signal **Nostr** publishing.

The core pipeline is **deterministic** (no LLM); the narrative analyzer is an optional edge. It does not
publish everything — only events where multiple independent signals converge. Sources are public domain.

**Why it exists:** meaningful public signals are scattered across incompatible sources and only become
intelligence when connected across time and space. Lighthouse does that connection continuously,
privately, and reproducibly.

### Configurable for a mission

Deployments are shaped by **target profiles** (`data/targets.json`) and declarative **Intelligence
Profiles**. Narrow initial activation is shipped for **`repository_capability_discovery` v2 only**
(`lighthouse profile-activate`); switching, deactivation, and every other bundled profile remain
unimplemented. It runs private and local-first, packaged for **Start9**, with a clean boundary to an
external **Gateway** control plane. StartOS does not expose this V1 operator loop.

---

## Inspecting the intelligence

```bash
lighthouse status                                   # DB, schema, keys, recent activity
lighthouse alerts --min-score 0.75 --limit 20       # scored signals
lighthouse correlations --limit 10                  # high-signal multi-source events
lighthouse graph --entity "Lockheed Martin" --depth 2   # explore the graph
lighthouse temporal --callsign PAT16                # recurrence over time
lighthouse tempo --recurring                        # operational tempo
lighthouse profiles                                 # bundled Intelligence Profiles (read-only)
```

Runtime state and per-module JSON outputs are written under `LIGHTHOUSE_DATA_DIR` (default `data/`,
`/data` on Start9) — the application data directory. The operational database used through
`modules/db.py` derives as `$LIGHTHOUSE_DATA_DIR/state.db` unless `DB_PATH` explicitly overrides it.
`AppConfig.state_db_path` and `DmsConfig.db_path` remain rooted at `$LIGHTHOUSE_DATA_DIR/state.db`
and do not follow a differing `DB_PATH`. An explicit `DB_PATH` can intentionally separate those
stores; it is not one universal database variable. `data/` is local runtime output — generated
files are git-ignored; only `.gitkeep` placeholders are tracked.

---

## Quickstart

### Prerequisites

- Python 3.11+
- Git
- IPFS Kubo daemon **or** Docker (Docker Compose bundles it automatically)

### 1. Clone and install

```bash
git clone https://github.com/intrinsicinvestment91/lighthouse pattern-match-sentinel
cd pattern-match-sentinel
pip install -e ".[dev]"
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Open .env and fill in your keys (see table below)
```

| Variable | Required | Description |
|---|---|---|
| `WIPO_API_KEY` | Optional | Free at [patentscope.wipo.int](https://patentscope.wipo.int). Without it, only USPTO is queried. |
| `NOSTR_PRIVATE_KEY_HEX` | Optional | 64-char hex secp256k1 key. Without it, Module 3 is skipped. |
| `LIGHTHOUSE_REGION` | Optional | Region label added to Nostr mesh tags as `["region", "<value>"]`. |
| `IPFS_API_URL` | Optional | Defaults to `/ip4/127.0.0.1/tcp/5001`. Without a running daemon, pages save locally only. |
| `IPFS_PINNING_SERVICE` | Optional | `local` (default), `pinata`, or `web3storage`. Non-local backends currently validate credentials and keep local-only fallback. |
| `PINATA_JWT` | Optional | Required when `IPFS_PINNING_SERVICE=pinata`. |
| `WEB3STORAGE_TOKEN` | Optional | Required when `IPFS_PINNING_SERVICE=web3storage`. |

**Generate a Nostr key:**
```bash
python -c "
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256K1
from cryptography.hazmat.backends import default_backend
k = generate_private_key(SECP256K1(), default_backend())
print(k.private_bytes_raw().hex())
"
```

### 3. Run

**Option A — Docker (recommended, includes IPFS):**
```bash
docker compose up
```

**Option B — local, with a running IPFS daemon:**
```bash
ipfs init        # first time only
ipfs daemon &
lighthouse       # or: python main.py
```

**Option C — dry run (no IPFS, no Nostr publishing):**
```bash
python main.py --dry-run
```

---

## CLI reference

```
lighthouse [--since-days N] [--dry-run] [--module MODULE]

  --since-days N    Look back N days when querying APIs (default: 7)
  --dry-run         Fetch and filter; skip DB writes, IPFS pinning, and Nostr publishing
  --module NAME     patents | velocity | arxiv | sec | archive | narrative |
                    flight | adsb | nostr | targeted | all   (dms-check is standalone)
```

Read-only intelligence commands: `status`, `alerts`, `correlations`, `graph`, `callsigns`, `targets`,
`temporal`, `tempo`, `profiles`, `backup-readiness`. See the [Developer Guide](docs/site/developer.html)
and [`docs/ARCHITECTURE_INDEX.md`](docs/ARCHITECTURE_INDEX.md) for the full surface.

Run a single module standalone:
```bash
python -m modules.patent_sentinel      # patent feed only
python -m modules.wayback_archiver     # newsroom crawl only
python -m modules.nostr_relay          # publish sample events (needs NOSTR_PRIVATE_KEY_HEX)
```

Read-only intelligence-profile and backup-readiness inspection (`lighthouse profiles` writes nothing):
```bash
lighthouse profiles                    # list bundled intelligence profiles
lighthouse profiles --show <id>        # show one profile
lighthouse profiles --validate         # validate all bundled profiles
lighthouse backup-readiness            # inspect backup readiness against the /data boundary
lighthouse active-profile --json       # read-only; never creates a database
```

Narrow backup-gated initial activation (RCD v2 only — no switch) and the host-only observe → detect
→ review loop are documented in [`docs/V1_OPERATOR_WORKFLOW.md`](docs/V1_OPERATOR_WORKFLOW.md).
`--verification-report` must be output from `lighthouse backup-verify` (`status: verified`);
`backup-create`'s embedded `reports/verification.json` (`status: passed`) is not sufficient.
`--restore-report` must come from `lighthouse restore-drill` (`--output PATH`). A disposable
CLI-only rehearsal lives at `ops/v1_rcd_host_rehearsal.sh` and must never use production
`data/state.db`.

---

## Architecture

```
                         lighthouse CLI  /  read-only MCP  /  Local Gateway (loopback)
                                          │
   ┌──────────────────────────────────────▼──────────────────────────────────────┐
   │  Collectors (fault-isolated in main.py)                                       │
   │    sec · flight · adsb · arxiv · narrative · velocity · patents · archive     │
   └──────────────────────────────────────┬──────────────────────────────────────┘
                                           │  AlertRecord (scored + provenanced)
   ┌──────────────────────────────────────▼──────────────────────────────────────┐
   │  Correlator (A3: temporal + spatial + entity)  →  weighted scoring  →  witness │
   └──────────────────────────────────────┬──────────────────────────────────────┘
                                           │  CorrelationRecord
   ┌──────────────────────────────────────▼──────────────────────────────────────┐
   │  Graph (entities · events · signals · callsigns · facilities)                 │
   │  Temporal + tempo intelligence · deterministic explanation                    │
   └──────────────────────────────────────┬──────────────────────────────────────┘
                                           │  gated, evidence-stripped
                              high-signal Nostr publishing
```

The whole core is **deterministic** — no LLM ([ADR-0009](docs/adr/0009-no-llm-in-deterministic-core.md)).
State persists to SQLite under `LIGHTHOUSE_DATA_DIR`. Lighthouse is the **engine**; an external Start9
**Gateway** owns approval/policy/audit ([ADR-0001](docs/adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)).
The full module map and architecture reference are in
[`docs/ARCHITECTURE_INDEX.md`](docs/ARCHITECTURE_INDEX.md).

## Documentation

| Question | Document |
|----------|----------|
| What can it actually do? (start here) | [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) |
| Where do I find everything? | [`docs/ARCHITECTURE_INDEX.md`](docs/ARCHITECTURE_INDEX.md) |
| Where are we going? | [`docs/ROADMAP.md`](docs/ROADMAP.md) (Phases A–K) |
| Where are we now? | [`docs/ROADMAP_STATUS.md`](docs/ROADMAP_STATUS.md) |
| How did we get here? | [`docs/PACKETS.md`](docs/PACKETS.md) |
| What could come next? | [`docs/FUTURE_PLAN.md`](docs/FUTURE_PLAN.md) |
| What has been decided & why? | [`docs/adr/`](docs/adr/README.md) |
| Security posture | [`docs/SECURITY.md`](docs/SECURITY.md) |
| Operating it (Operator Guide) | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| Developing it (Developer Guide) | [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`docs/site/`](docs/site/index.html) |

---

## Configuration

All tuneable knobs live in `config.py` as typed dataclasses:

```python
# config.py — edit these or override via env vars

PatentConfig.classes              = ["C22C", "G21B", "B64G"]          # IPC codes to monitor
PatentConfig.assignees_of_interest = ["DARPA", "Lockheed", "Helion", …] # partial-match watchlist

ArchiveConfig.targets             = [{"agency": "DoD", "url": …, "keywords": […]}, …]
ArchiveConfig.crawl_delay         = 3.0   # seconds between article requests (be polite)
ArchiveConfig.max_pages_per_target = 50

NostrConfig.relays                = ["wss://relay.damus.io", "wss://nostr.wine", …]
NostrConfig.event_kind            = 1     # NIP-01 text note
```

SEC EDGAR requests need a declared User-Agent with an organization name and contact email (SEC fair-access policy). Set `SEC_USER_AGENT`, for example `SEC_USER_AGENT="Example Org admin@example.org"`. There is no default; while it is unset the SEC sentinel refuses to run and sends no requests.

---

## Development

```bash
make install       # pip install -e ".[dev]"
make lock          # regenerate pinned lock files from pyproject.toml
make sync          # sync local env to requirements-dev.lock, then install package editable
make test          # unit tests — no network required, runs in ~20 s
make test-cov      # unit tests + coverage report
make lint          # ruff check + format check
make format        # auto-format with ruff
make docker-up     # start lighthouse + IPFS via Docker Compose
```

`pyproject.toml` remains the human-edited source of truth for dependencies. Lock files
(`requirements.lock`, `requirements-dev.lock`, and `requirements-portal.lock`) are generated
artifacts for reproducible installs in local workflows, CI, and Docker.

**Integration tests** (hit live APIs — require credentials):
```bash
python -m pytest tests/integration -m integration -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development workflow and pre-commit setup.

---

## How cross-run deduplication works

`state_db.py` maintains a lightweight SQLite cache at `$LIGHTHOUSE_DATA_DIR/state.db` (via
`AppConfig.state_db_path`). Before publishing a Nostr event, the pipeline checks whether the patent
ID or archive URL has been seen before. This means you can run `lighthouse` on a cron schedule
without re-broadcasting stale records.

---

## Docker

```bash
cp .env.example .env
docker compose up
```

The Compose file starts:
- **lighthouse** — runs the pipeline once and exits (wrap in a cron job or restart policy for scheduling)
- **ipfs/kubo** — persistent IPFS node with a named volume; API is only reachable inside the Docker network

---

## Running on StartOS

- On StartOS, Lighthouse is operated through StartOS actions that run its command-line interface. It has no user-facing web portal or MCP server there.
- The package's only long-running service is a small status endpoint on internal port 9090. It is not exported as a StartOS interface, requires a Bearer token checked against a hash-only credential, accepts only GET requests on a single path, and reports only the active profile and backup readiness.
- Repositories are observed only under `/data/repos`; paths outside the allowed roots are refused.
- The StartOS package never starts Lighthouse's network collection pipeline (arXiv, SEC filings, flight/ADS-B data, Nostr, IPFS), and it sets the patent provider to `disabled`. Run outside StartOS, the pipeline contacts those services as configured. No Nostr signing key is configured by default, and correlation publishing is off unless `NOSTR_PUBLISH_CORRELATIONS` is set. IPFS defaults to a local daemon (see [Configuration](#configuration)).
- The bundled arXiv researcher roster is synthetic. The entity and target seed lists name real, publicly known companies and organizations as the default watch lists. No private profile ships.
- Package and repository URLs are placeholders until a public repository exists.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
