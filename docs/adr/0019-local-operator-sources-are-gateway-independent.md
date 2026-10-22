# ADR-0019 — Local operator sources are a distinct, Gateway-independent observation class

- **Status:** Accepted — implementation deferred
- **Date:** 2026-07-12
- **Packet:** LHP-DIRECTION-02 (charters `LHP-INTEL-01`)

## Context

[ADR-0008](0008-repository-source-packs-are-a-deterministic-observation-source.md) established that a
**Repository Source Pack** is one deterministic observation source, and that the **Gateway owns
source-pack admission approval**. [`REPOSITORY_SOURCE_PACKS.md`](../REPOSITORY_SOURCE_PACKS.md) records
the consequence: repository ingestion is *"blocked on Gateway source-pack admission (Phase G) and the
live bridge ([ADR-0010](0010-live-gateway-bridge-blocked-pending-package-exposure.md))"*.

Read naively, that blocks `LHP-INTEL-01` — the observation intake layer — before it starts, and blocks it
behind a phase that is itself blocked on a decision (StartOS package exposure) unrelated to intelligence.

But those decisions were written about **external, third-party, untrusted** repositories: code the
operator does not control, arriving from the public internet, whose *admission* is a trust decision.
Admission exists to answer *"should this deployment be allowed to observe that foreign thing?"*

`LHP-INTEL-01` ingests something categorically different: **the operator's own repositories and documents,
already present on the operator's own machine, inside the same trust boundary as the database Lighthouse
already writes.** There is no network fetch, no third party, and no trust decision for the Gateway to
make. Requiring Gateway admission for a file the operator already owns would be governance theatre — and
it would block the intelligence engine on a bridge that does not exist.

## Decision

**Local operator sources are a distinct observation class, and require no Gateway admission.**

| | External Repository Source Pack | **Local operator source** |
|---|---|---|
| Origin | public / third-party repository | the operator's own filesystem |
| Trust boundary | outside — arrives from elsewhere | **inside** — already on the box |
| Admission | **Gateway-approved** ([ADR-0008](0008-repository-source-packs-are-a-deterministic-observation-source.md)) | **none required** |
| Blocked on | Phase G + [ADR-0010](0010-live-gateway-bridge-blocked-pending-package-exposure.md) | **nothing** |
| Executed / installed / imported | **never** | **never** |
| Feeds | the one engine, as ordinary observations | the one engine, as ordinary observations |

Both classes are **observation sources, not engines** — ADR-0008's central rule is preserved unchanged
and extended, not weakened. Neither class may ever be executed, installed, or imported; the
reference-only rule is absolute and applies to local sources exactly as it applies to foreign ones.

Two constraints keep the local class from becoming a hole:

1. **Allowlisted roots only.** A local source must be reachable from an explicitly configured root. There
   is no arbitrary filesystem walk, and no path traversal outside a declared root — the same fail-closed
   posture as the `/data` backup boundary ([ADR-0004](0004-backup-readiness-fail-closed-data-boundary.md)).
2. **Read-only, metadata-first.** Sources are never modified. Observations default to
   `content_tier: metadata_only` with `raw_content: false`, matching what the bundled profiles already
   declare.

## Consequences

- `LHP-INTEL-01` (the observation intake layer) is **not blocked** by Phase G or ADR-0010, and can
  proceed immediately. This is the decision that unblocks Phase D.
- The Gateway keeps full authority over the class where admission actually means something: external
  packs. **No Gateway change is made or required.**
- Two source classes must stay visibly distinct in code and docs, or the local class will silently become
  a way to smuggle foreign repositories past admission. A local source is identified by residing under a
  configured local root — not by a claim in the observation.
- If a *future* packet wants Lighthouse to fetch an external repository directly, that is the
  ADR-0008/Phase-G path and this ADR does not authorise it.

## Repository evidence

- `docs/adr/0008-repository-source-packs-are-a-deterministic-observation-source.md` — the external-pack
  decision this ADR refines (not reverses).
- `docs/REPOSITORY_SOURCE_PACKS.md` — the "blocked on Gateway admission" statement that applies to
  external packs only.
- `seed/profiles/repository_capability_discovery.json` — declares `"requires_gateway_approval": true`,
  `"external_repo_execution": false`, `"external_repo_install": false`, and `content_tier:
  metadata_only`: the external class, with the reference-only rule already encoded.
- `docs/OBSERVATION_INTAKE.md` — the `LHP-INTEL-01` charter this ADR unblocks.

## Related ADRs

- [ADR-0008](0008-repository-source-packs-are-a-deterministic-observation-source.md) — refined here;
  source packs remain one observation source, never a second engine.
- [ADR-0005](0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) — the invention engine
  still lives in the Gateway; this ADR moves *observation*, not that engine.
- [ADR-0010](0010-live-gateway-bridge-blocked-pending-package-exposure.md) — remains blocked, and is now
  explicitly **not** on the intelligence engine's critical path.
- [ADR-0018](0018-governance-frozen-pending-runtime-intelligence.md) — the freeze that makes this the
  next work.
- [ADR-0004](0004-backup-readiness-fail-closed-data-boundary.md) — the canonical-boundary posture the
  allowlisted-roots rule mirrors.
