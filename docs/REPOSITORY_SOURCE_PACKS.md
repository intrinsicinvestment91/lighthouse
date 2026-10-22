# Repository Source Packs

> Status: **architecture-only.** There is **no** source-pack ingestion engine in this repository today.
> The capability is represented by a declarative profile seed
> (`seed/profiles/repository_capability_discovery.json`) and the Gateway's admission flow. This document
> fixes the architecture so future work does not drift. Decision:
> [ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md).

## What a Repository Source Pack is

A **Repository Source Pack** lets Lighthouse observe a public code repository as **one deterministic
observation source** — the same kind of input as an SEC filing or an ADSB sighting.

It enters the **fact-first pipeline**:

```
approved source-pack admission → repository intake → versioned observations → normalization
  → fact store → exact queries & cross-scope facts
  → profile-owned deterministic PatternCandidate rules → hypotheses → reviewed findings
  → optional Gateway-controlled external action or release
```

> **Corrected 2026-07-14 (LHP-ID-PRESERVE-01).** This document previously said a source pack is
> *"normalized into scored alerts and flows through the existing pipeline (observation → alert →
> correlation → graph → temporal)"*. **That path was rejected by the implementation** in `LHP-INTEL-01B`:
> `AlertRecord` is a *triage* contract (`score`, `score_label`, `--min-score`, a publish gate) and
> `GraphEdge` requires a `weight` — **neither can hold a fact without also asserting an opinion about it**,
> so a source pack routed through them would arrive already judged. The fact substrate
> (`observation_sets`, `normalized_entities`, `normalized_relationships`) exists precisely because that
> was the wrong door. The alert/correlation/graph path remains the **legacy OSINT pipeline**, untouched
> and unrelated. See [ADR-0008](adr/0008-repository-source-packs-are-a-deterministic-observation-source.md)
> (superseded in part) and [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md).

## Two source classes — do not conflate them

| | **Local operator source** | **External / third-party source pack** |
|---|---|---|
| Origin | the operator's own filesystem | a public / third-party repository |
| Trust boundary | **inside** — already on the box | **outside** — arrives from elsewhere |
| Gateway admission | **not required** ([ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md)) | **required** |
| Status today | **shipped** — `lighthouse observe` | architecture-only |
| Executed / installed / imported | **never** | **never** |
| Enters as | observations → facts | observations → facts |

Neither class bypasses the fact substrate by creating scored alerts directly, and **neither becomes a
second intelligence engine.**

## What it is *not*

- **Not a second intelligence engine.** Source packs do not re-derive correlation, graph, or scoring on
  their own. They feed the one core engine; they do not fork it.
- **Not a plugin, not executable.** Consistent with profiles
  ([ADR-0002](adr/0002-intelligence-profiles-are-declarative-not-plugins.md)), a source pack declares
  *what* to observe, never *how* to execute anything.
- **Not the invention engine.** The invention-discovery engine is a separate concern. It is **not in this
  repository, and — contrary to what the record long said — it was never in the Gateway either.** It is
  now preserved privately with a provenance manifest, pending **selective** migration
  ([ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md),
  [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)).

## The reference-only rule

External repositories named by a capability-discovery profile are **reference-only: never executed,
never installed, never imported.** Lighthouse reads *about* a repository (metadata, public structure,
declared patterns) to produce observations; it does not run foreign code. This is a standing security
guardrail (see [`SECURITY.md`](SECURITY.md), [`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md)).

## How Gateway and Lighthouse divide responsibility

Per the ownership boundary ([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md),
[`GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md`](GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md)):

| Responsibility | Owner |
|----------------|-------|
| Source-pack **admission approval** (which packs are allowed) | **Gateway** |
| Audit trail of admissions | **Gateway** |
| **Observation** produced from an admitted pack | **Lighthouse** |
| **Fact** derivation (normalization, identity, cross-scope) from those observations | **Lighthouse** (the one engine) |
| **Interpretation** of those facts (candidates → hypotheses → findings) | **Lighthouse**, via **profile-owned rules** ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)) |
| Consequential approval / external release / disclosure packages | **Gateway** |

A Gateway admission record is not authoritative state inside Lighthouse; Lighthouse stores its own
evidence.

## Current state and where it fits

- **Seed:** `seed/profiles/repository_capability_discovery.json` (declarative, `status: draft`).
- **Roadmap:** part of **Phase D — Repository Intelligence** in [`ROADMAP.md`](ROADMAP.md).
- **Blocked on:** Gateway source-pack admission (Phase G) and the live bridge
  ([ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md)).

> **Scope note — this blocker applies to *external* repositories only.** Source packs are third-party
> code arriving from outside the trust boundary, which is what makes *admission* a real decision. The
> operator's **own** repositories and documents, already present on the operator's own machine, are a
> **distinct observation class that requires no Gateway admission and is not blocked**
> ([ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md)); they are ingested by the
> observation intake layer ([`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md), `LHP-INTEL-01`). Both
> classes are observation sources, never engines, and **neither is ever executed, installed, or
> imported.**

When ingestion is built, it must reuse the existing **fact-substrate** contracts — the observation model
(`modules/observations.py`), the normalizer, and the three fact-only tables — and it must **not** introduce
a parallel storage path.

It must **not** be routed through `AlertRecord`/`GraphEdge`. That is not a parallel path being avoided; it
is the *wrong* path, for the reason recorded above: those contracts cannot carry a fact without scoring it.
