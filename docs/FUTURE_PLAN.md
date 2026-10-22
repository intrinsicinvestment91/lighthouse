# Lighthouse — Research & Backlog

> This document answers **"What could come next?"** — deferred ideas, research directions, experimental
> concepts, and nice-to-have capabilities that are **not** committed work. It is intentionally
> speculative.
>
> It is **not** the status doc and **not** the roadmap. For *where we are now* see
> [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md); for *committed direction* (Phases A–K) see
> [`ROADMAP.md`](ROADMAP.md); for *history* see [`PACKETS.md`](PACKETS.md); for *decisions* see
> [`adr/`](adr/README.md). Day-to-day usage lives in [`OPERATIONS.md`](OPERATIONS.md) and the static
> site; security posture lives in [`SECURITY.md`](SECURITY.md).

## Strategic emphasis (near-term leverage)

Lighthouse is a persistent, graph-native, increasingly temporally-aware intelligence platform. The
strongest near-term leverage is **improving interpretation of existing signals**, not adding collectors.

| Priority | Rationale |
|----------|-----------|
| Signal quality | Real value is in ADSB+SEC and military aviation, not alert volume |
| Temporal relationships | Repetition, lead/lag, and silence beat one-off hits |
| Graph intelligence | MCP + CLI traversal is the primary analysis interface |
| Aviation pattern analysis | Callsigns are the strongest differentiator in live runs |
| Cross-source correlation | Multi-signal events remain the publishable unit |

### Explicit non-goals (near term)

- Broad collector sprawl (new APIs without an interpretation plan).
- Random feature additions unrelated to graph/temporal/callsign leverage.
- Lowering Nostr or correlation gates to increase output volume.
- LLM in the deterministic core ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)); the narrative
  module remains an optional edge.

## Backlog — re-prioritised packet scopes

These were pre-assigned to earlier packet numbers but not shipped as scoped (see the numbering note in
[`PACKETS.md`](PACKETS.md)). They remain candidate work with no committed order:

- **Time-window correlation scoring + facility baselines** — proximity-in-time bonus in
  `score_correlation()`; a `facility_baselines` table and `compute_facility_baselines()` as the
  foundation for anomaly scoring. (Temporal *analysis* shipped as Packet 24; time-aware *scoring* did
  not.)
- **Recurring-callsign promotion to `aircraft` graph nodes** — `promote_recurring_callsigns()` turning
  callsigns seen ≥N times in a window into `aircraft` nodes with `observed_at` facility edges. (Packet
  25 shipped `callsign:` nodes; the `aircraft`-node promotion did not.)
- **Signal-quality calibration** — per-facility `radius_km`, `distance_from_center_km` on alerts, and a
  proximity-decay penalty to suppress generic airspace traffic over large bounding boxes.
- **BitAgent payment gate** — see below.
- **DoE archive feed** — blocked on a verified public RSS/Atom URL.
- **USPTO ODP patent adapter** — blocked on a confirmed Open Data Portal search endpoint/schema; would
  flip `PatentConfig.provider` default from `disabled` to `odp`.

## Backlog — research directions

- **Per-researcher arXiv cadence baselines** — calibrate the silence threshold per researcher from
  actual publication cadence instead of a fixed 30-day default.
- **Probationary correlation auto-promotion** — auto-promote a probationary correlation when a second
  confirming signal arrives within N days.
- **Behavioral anomaly scoring** — cross-signal pattern scoring over rolling history windows.
- **Temporal graph edges** — `event → precedes → event`, `callsign → observed_at → facility` with time
  constraints; timeline reconstruction and lead/lag analysis (e.g. aviation activity *before* an SEC
  filing; arXiv silence *before* a patent/SEC burst).
- **Graph export / visualization** — GEXF (or similar) export for external graph tools.

## BitAgent monetization path (exploratory)

Lighthouse could become a BitAgent-compatible paid intelligence service:

```text
AI Agent → BitAgent payment gate → Lighthouse read query → graph/explanation response
```

Candidate paid methods are the existing read-only MCP tools (`query_alerts`, `query_correlations`,
`get_entity_graph`, `get_graph_neighbors`, `get_graph_edges_between`, `explain_entity_graph`). The
payment gate would be a distinct concern from the `LIGHTHOUSE_APPROVAL_SECRET` gate and default-off.
Recommended sequencing: accumulate ≥1–2 weeks of stable Start9 data and improve correlation density
first, then add the wrapper. This is deferred, not scheduled.

## Open-source strategy (exploratory)

Preferred posture: **open the engine; keep the intelligence data and tuning private at first.**

- Good candidates to open: core pipeline framework, data contracts, CLI, MCP interface, graph model,
  deterministic explanation engine.
- Keep private initially: production `.env`, private Nostr keys, logs, the local database, refined
  entity datasets, correlation tuning, and operational notes that reveal strategy.

Public release is Phase K in [`ROADMAP.md`](ROADMAP.md); it depends on threat-model completion and MCP
privacy/authentication.

## Longer-horizon direction

Lighthouse's long-term operating model is **graph-native, temporally-aware intelligence correlation**,
governed by declarative Intelligence Profiles and a clean engine/control-plane split
([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)). Explanation stays
deterministic. Anything promoted from this backlog into committed work should land as a small,
incremental packet and be reflected in [`ROADMAP.md`](ROADMAP.md) / [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md).
