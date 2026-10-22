# Lighthouse — Architecture Index

> **Start here.** This is the navigation hub for the Lighthouse Intelligence Platform (git repository
> `pattern-match-sentinel`). It links every major document; it does not duplicate their content.

Lighthouse is a persistent, graph-based intelligence platform. It watches public signals (SEC filings,
military aviation / ADSB, research publications, government newsrooms), normalizes them into scored
alerts, correlates multi-signal events, builds a queryable graph, derives temporal and tempo
intelligence, and surfaces only high-confidence findings over CLI, a read-only MCP interface, and a
loopback local gateway. For the one-page overview, read [`../README.md`](../README.md).

## The documentation map — one question per document

| If you want to know… | Read | Role |
|----------------------|------|------|
| **What is Lighthouse?** | [`../README.md`](../README.md) | Overview + quick start |
| **What can it actually do?** | [`CAPABILITIES.md`](CAPABILITIES.md) | Plain-English capability guide (easiest entry point) |
| **Where do I find everything?** | this file | Navigation hub |
| **Where are we going?** | [`ROADMAP.md`](ROADMAP.md) | Canonical roadmap — Phases A–K |
| **What must ship for the full platform?** | [`FULL_CAPABILITY_ROADMAP.md`](FULL_CAPABILITY_ROADMAP.md) | Capability ledger + release trains (intent; v1.0.0 → V2) |
| **Where are we now?** | [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) | Living status + implementation matrix (shipped truth) |
| **How did we get here?** | [`PACKETS.md`](PACKETS.md) | Historical engineering log |
| **What could come next?** | [`FUTURE_PLAN.md`](FUTURE_PLAN.md) | Research & backlog |
| **What has been decided & why?** | [`adr/`](adr/README.md) | Architecture Decision Records |
| **How would an active-to-active switch work?** | [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) | Controlled switch lifecycle design (not implemented) |
| **What is the security posture?** | [`SECURITY.md`](SECURITY.md) | `LHP-SECURITY-01` overview |

## Architecture at a glance

```text
Targets → Collectors → Alerts → Correlations → Graph → Temporal/Tempo → MCP / CLI / Local Gateway / Nostr
```

Deterministic core (no LLM — [ADR-0009](adr/0009-no-llm-in-deterministic-core.md)); per-module fault
isolation in `main.py`; SQLite persistence under `LIGHTHOUSE_DATA_DIR` (`/data` on Start9). Module-level
detail is in [`CAPABILITIES.md`](CAPABILITIES.md); the human-browsable mirror is the static site at
[`site/architecture.html`](site/architecture.html).

## Key subsystems and where they are documented

| Subsystem | Canonical doc | Code |
|-----------|---------------|------|
| Signals / collectors | [`CAPABILITIES.md`](CAPABILITIES.md) | `modules/*_sentinel.py`, `flight_tracker.py`, `velocity_tracker.py` |
| Alert & correlation contracts | [`CAPABILITIES.md`](CAPABILITIES.md) | `modules/alerts.py`, `modules/correlations.py`, `modules/correlator.py` |
| Witness attestation | [`CAPABILITIES.md`](CAPABILITIES.md) | `modules/witness.py` |
| Graph intelligence | [`site/graph.html`](site/graph.html) | `modules/graph*.py` |
| Temporal / tempo | [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) | `modules/temporal_analysis.py`, `modules/tempo_analysis.py` |
| Intelligence Profiles | [`INTELLIGENCE_PROFILE_ARCHITECTURE.md`](INTELLIGENCE_PROFILE_ARCHITECTURE.md) · [`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md) | `modules/profiles.py`, `modules/profile_registry.py`, `modules/profile_activation.py`, `modules/active_profile_store.py`; `lighthouse profile-activate`, `lighthouse active-profile` (RCD v2 only; no switch) |
| Backup readiness, verified backup & restore drill | [`BACKUP_READINESS_AND_PROFILE_SWITCHING.md`](BACKUP_READINESS_AND_PROFILE_SWITCHING.md) · [`schemas/lighthouse_backup_manifest.v1.schema.json`](schemas/lighthouse_backup_manifest.v1.schema.json) | `modules/backup_readiness.py`, `modules/backup_manifest.py`, `modules/backup_create.py`, `modules/backup_verify.py` |
| Profile transition preview (review artifact; no activation) | [`BACKUP_READINESS_AND_PROFILE_SWITCHING.md`](BACKUP_READINESS_AND_PROFILE_SWITCHING.md) · [`schemas/profile_transition_preview.v1.schema.json`](schemas/profile_transition_preview.v1.schema.json) · [ADR-0014](adr/0014-transition-preview-artifacts-before-profile-activation.md) | `modules/profile_transition.py` |
| Initial profile activation (RCD v2 only; backup-gated) | [`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md) · [`schemas/active_profile_state.v1.schema.json`](schemas/active_profile_state.v1.schema.json) · [ADR-0016](adr/0016-activation-capability-boundary.md) | **Shipped:** `modules/profile_activation.py`, `modules/active_profile_store.py`, `lighthouse profile-activate`, `lighthouse active-profile`; observe/detect admission for active RCD. **No switch, no deactivate, no Gateway authority** |
| Activation contracts (evaluate only; do not activate) | [ADR-0016](adr/0016-activation-capability-boundary.md) · [`schemas/activation_intent.v1.schema.json`](schemas/activation_intent.v1.schema.json) | `modules/active_profile_state.py`, `modules/activation_preconditions.py`, `modules/activation_intent.py` |
| Activation capability descriptor (self-description; Gateway unavailable — `not_implemented`) | [ADR-0017](adr/0017-lighthouse-describes-activation-capability.md) · [`schemas/lighthouse_activation_capability.v1.schema.json`](schemas/lighthouse_activation_capability.v1.schema.json) | `modules/activation_capability.py` |
| **Profile governance** (broader activation deferred; narrow RCD initial activation shipped) | [ADR-0018](adr/0018-governance-frozen-pending-runtime-intelligence.md) | Narrow `no_active_profile → repository_capability_discovery` v2 shipped; profile switching, non-RCD targets, and Gateway bridge **deferred** |
| **Profile switch lifecycle** (active-to-active; design only) | [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) | **Not implemented** — authority projection, transition-control record, attempt journal, default-deny local enrollment (empty) |
| Local Gateway (read-only dispatch) | [`site/local-gateway.html`](site/local-gateway.html) | `modules/local_gateway.py`, `portal/app.py` |
| Gateway/Lighthouse boundary | [`GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md`](GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md) | — (the separate Gateway application) |
| **Observation intake** (local repos + documents; the active track) | [`OBSERVATION_INTAKE.md`](OBSERVATION_INTAKE.md) · [ADR-0019](adr/0019-local-operator-sources-are-gateway-independent.md) | shipped — `modules/repository_intake.py`, `document_intake.py`, `observations.py`, `observation_normalizer.py`, `observation_intake.py`, `observation_query.py`, `content_identity.py` (03A — the one raw-byte SHA-256 recipe) |
| **Pattern candidates** (the candidate/review loop) | [`PATTERN_CANDIDATES.md`](PATTERN_CANDIDATES.md) · [ADR-0021](adr/0021-pattern-candidates-are-review-objects-not-findings.md) | shipped — `modules/pattern_candidate.py` (the contract), `repository_content_recurrence.py` (the first profile-owned rule), `pattern_candidate_service.py` (the boundary); `lighthouse patterns`. A candidate is **not** a hypothesis, a finding, or authority to do anything |
| **Public source checks** (the first network fact layer) | [`PUBLIC_SOURCE_CHECKS.md`](PUBLIC_SOURCE_CHECKS.md) · [ADR-0022](adr/0022-public-source-retrieval-is-isolated-to-a-hardened-fact-adapter.md) | shipped — `modules/public_source_intake.py` (the one hardened HTTPS chokepoint), `public_source_check.py` (the contracts), `public_source_check_service.py` (the boundary); `lighthouse public-sources`, an append-only check ledger. A `not_found` is HTTP 404/410, **not** a claim that a source is gone; no candidate, no body storage |
| **Scientific preservation candidates** (the second detector) | [`SCIENTIFIC_PRESERVATION_CANDIDATES.md`](SCIENTIFIC_PRESERVATION_CANDIDATES.md) · [ADR-0023](adr/0023-detector-owned-candidate-specifications.md) | shipped — `modules/scientific_source_disappearance.py` (the pure rule), `pattern_candidate_specs.py` (the closed detector-spec registry); `patterns detect --detector scientific_source_disappearance_review`. Carries a second profile through `pattern_candidate.v1` with repository candidates byte-identical; `not_found` is never disappearance |
| **The significance boundary** — where facts end and interpretation begins; profile-neutral substrate, **profile-owned detector rules**; facts first, interpretation second, findings only after evidence and review | [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md) | shipped candidate/review loop (LHP-INTEL-03B/04C); **no hypothesis or finding layer**. Portability proof complete (LHP-INTEL-04A–04C); selective invention migration is next |
| **Invention-discovery prototype** (external; preserved privately, **not adopted**) | [ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) (location premise corrected) · [ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md) | out of tree — preserved privately; no engine code in this repository |
| Repository Source Packs (external; Gateway-admitted) | [`REPOSITORY_SOURCE_PACKS.md`](REPOSITORY_SOURCE_PACKS.md) | architecture-only (seed profile) |
| MCP interface | [`CAPABILITIES.md`](CAPABILITIES.md) | `portal/mcp_server.py` |
| Security & threat model | [`SECURITY.md`](SECURITY.md), [`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md) | — |
| Operations / Start9 | [`OPERATIONS.md`](OPERATIONS.md) | `scripts/` |

## The Gateway/Lighthouse boundary (read this before touching either)

Lighthouse owns the **intelligence engine** (observations, graph, temporal, correlation, profiles,
backup evidence). The **external operator gateway (a separate application, not bundled with
Lighthouse)** is a **thin control/approval/policy/audit plane**. The two are separate applications;
the live bridge between them is **blocked** until a package-side private surface is exposed. See
[ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)
and [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md).

> Note: "**Local Gateway**" (`modules/local_gateway.py`) is an *in-repo* read-only dispatch layer — a
> different thing from the external "**Gateway**" control plane. Don't conflate them.

## Contracts you must not bypass

`AlertRecord` · `CorrelationRecord` · `GraphNode`/`GraphEdge` · `IntelligenceProfile`. Their invariants
and the standing guardrails are recorded in the ADRs ([`adr/README.md`](adr/README.md)).

## For new contributors

Read in this order: [`../README.md`](../README.md) → [`CAPABILITIES.md`](CAPABILITIES.md) → this index
→ [`ROADMAP.md`](ROADMAP.md) → [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) → [`adr/`](adr/README.md).
Developer setup is in
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) and [`site/developer.html`](site/developer.html); operators
start at [`OPERATIONS.md`](OPERATIONS.md).
