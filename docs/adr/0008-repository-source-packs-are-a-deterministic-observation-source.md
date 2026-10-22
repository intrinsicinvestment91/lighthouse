# ADR-0008 — Repository Source Packs are a deterministic observation source, not a second engine

- **Status:** Accepted — implementation deferred
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

Lighthouse will eventually observe public code repositories (via the `repository_capability_discovery`
profile and the Gateway's source-pack admission flow). There is a risk of treating source packs as a
*second* intelligence engine that re-derives correlation/graph independently, duplicating the core and
creating a parallel, unreviewed execution surface.

## Decision

- A **Repository Source Pack is one deterministic observation source** feeding the same pipeline as any
  other signal — **not** a second intelligence engine and not a plugin.
- Responsibility split (per ADR-0001): the **Gateway owns source-pack admission approval**; **Lighthouse
  owns the observation** it produces once admitted.
- External repositories referenced by capability-discovery profiles are **reference-only: never executed,
  never installed, never imported.**

**Deferred:** no source-pack ingestion engine exists in this repository yet; the capability is
architecture-only, represented by a declarative profile seed.

## Consequences

- Source-pack work reuses the existing observation → alert → correlation → graph path; it does not fork
  it. — **⚠ Superseded in part (2026-07-14); the original wording is preserved above. See
  ["Superseded in part"](#superseded-in-part-2026-07-14-lhp-id-preserve-01) below: source packs enter the
  fact-first pipeline, not the alert/correlation/graph path.**
- Ties to ADR-0005 (invention engine): source packs stay observation-tier, not an engine.

## Repository evidence

- `seed/profiles/repository_capability_discovery.json` (declarative seed).
- `docs/PROFILE_SECURITY_RISKS.md` ("external repositories … reference-only: never executed, never
  installed").
- `docs/GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md` ("Source-pack admission approval" — Gateway-owned).

## Superseded in part (2026-07-14, LHP-ID-PRESERVE-01)

**The central rule stands. The stated pipeline does not.**

**Unchanged and reaffirmed:** a source pack is *one deterministic observation source, never a second
intelligence engine*; external packs require Gateway admission; external repository code is never executed,
installed, or imported.

**Superseded:** the Consequences line above — *"Source-pack work reuses the existing observation → alert →
correlation → graph path"*. That path was **rejected by the implementation** in `LHP-INTEL-01B`, and the
reasoning is recorded in `docs/OBSERVATION_INTAKE.md`: `AlertRecord` is a *triage* contract (`score`,
`score_label`, a `--min-score` filter, a publish gate) and `GraphEdge` requires a `weight` — **neither can
hold a fact without also asserting an opinion about it.** A source pack routed through scored alerts would
arrive already judged.

Source packs enter the **fact-first** pipeline (see [ADR-0020](0020-profile-neutral-substrate-profile-owned-detectors.md)):

```
approved source-pack admission → repository intake → versioned observations → normalization
  → fact store → exact queries & cross-scope facts
  → profile-owned deterministic PatternCandidate rules → hypotheses → reviewed findings
  → optional Gateway-controlled external action or release
```

The alert/correlation/graph path remains the **legacy OSINT pipeline** and is untouched. It is not the
contract for the fact substrate, and source packs do not bypass that substrate by creating scored alerts
directly.
