# Architecture Decision Records

This directory is the canonical log of significant architectural decisions for **Lighthouse** (the
product; git repository `pattern-match-sentinel`). It answers *"What has been decided, and why?"* and
replaces the informal "decision #N" references that previously lived only in prose.

We follow lightweight [MADR](https://adr.github.io/madr/)-style records. Each ADR is immutable once
**Accepted**; we do not edit a decision to reverse it — we add a new ADR and set the old one's
`Superseded-by`.

## Format

Every ADR carries: **ID · Status · Date · Context · Decision · Consequences · Repository evidence ·
Related packets · Superseded-by**.

Statuses: `Proposed` · `Accepted` · `Accepted — implementation deferred` · `Deprecated` · `Superseded`.

## Where this fits

ADRs are one corner of the documentation system (see [`../ARCHITECTURE_INDEX.md`](../ARCHITECTURE_INDEX.md)):

| Question | Document |
|----------|----------|
| Where do I find everything? | [`../ARCHITECTURE_INDEX.md`](../ARCHITECTURE_INDEX.md) |
| Where are we going? | [`../ROADMAP.md`](../ROADMAP.md) |
| Where are we now? | [`../ROADMAP_STATUS.md`](../ROADMAP_STATUS.md) |
| How did we get here? | [`../PACKETS.md`](../PACKETS.md) |
| What might we build next? | [`../FUTURE_PLAN.md`](../FUTURE_PLAN.md) |
| **What has been decided & why?** | **this directory** |

## Index

| ID | Title | Status |
|----|-------|--------|
| [0001](0001-lighthouse-owns-engine-gateway-is-control-plane.md) | Lighthouse owns the intelligence engine; Gateway is the thin control plane | Accepted |
| [0002](0002-intelligence-profiles-are-declarative-not-plugins.md) | Intelligence Profiles are declarative policy bundles, not plugins | Accepted |
| [0003](0003-exactly-one-active-profile-controlled-switch.md) | Exactly one active profile; switching is a controlled, backup-gated transition | Accepted — implementation deferred; superseded in part by 0024 |
| [0004](0004-backup-readiness-fail-closed-data-boundary.md) | Backup readiness is fail-closed within a canonical `/data` boundary | Accepted |
| [0005](0005-invention-engine-belongs-in-lighthouse-migration-deferred.md) | The invention engine belongs in Lighthouse; migration deferred | Accepted — implementation deferred |
| [0006](0006-dms-production-blocked-pending-threat-model.md) | DMS production use is blocked pending a threat model | Accepted |
| [0007](0007-authority-gates-fail-closed-local-gate-defense-in-depth.md) | Authority gates fail closed; the local MCP gate is defense-in-depth | Accepted |
| [0008](0008-repository-source-packs-are-a-deterministic-observation-source.md) | Repository Source Packs are a deterministic observation source, not a second engine | Accepted — implementation deferred |
| [0009](0009-no-llm-in-deterministic-core.md) | No LLM in the deterministic core pipeline | Accepted |
| [0010](0010-live-gateway-bridge-blocked-pending-package-exposure.md) | The live Gateway bridge is blocked pending package-side REST exposure | Accepted |
| [0011](0011-documentation-architecture-one-source-per-topic.md) | Role-separated documentation with one canonical source per topic | Accepted |
| [0012](0012-naming-lighthouse-product-repo-identifier.md) | Naming: product is Lighthouse; `pattern-match-sentinel` is the repository identifier | Accepted |
| [0013](0013-verified-backup-and-restore-drill-before-profile-activation.md) | Verified backup manifest + isolated restore drill required before profile activation | Accepted |
| [0014](0014-transition-preview-artifacts-before-profile-activation.md) | Transition preview artifacts required before profile activation | Accepted |
| [0015](0015-preview-carries-producer-owned-execution-facts.md) | Transition preview carries producer-owned execution facts (additive, serialization v2) | Accepted |
| [0016](0016-activation-capability-boundary.md) | Activation capability boundary: contracts before capability (state model, preconditions, intent — no activation) | Accepted |
| [0017](0017-lighthouse-describes-activation-capability.md) | Lighthouse describes its activation capability; the Gateway defines and trusts it (declared `not_implemented` — no activation) | Accepted |
| [0018](0018-governance-frozen-pending-runtime-intelligence.md) | Profile governance is frozen at LHP-PROFILE-14C; the intelligence engine comes first | Accepted |
| [0019](0019-local-operator-sources-are-gateway-independent.md) | Local operator sources are a distinct, Gateway-independent observation class | Accepted |
| [0020](0020-profile-neutral-substrate-profile-owned-detectors.md) | A profile-neutral intelligence substrate with profile-owned detectors; facts before significance | Accepted |
| [0021](0021-pattern-candidates-are-review-objects-not-findings.md) | Pattern candidates are profile-owned review objects, not findings; no score, no promotion, no activation | Accepted |
| [0022](0022-public-source-retrieval-is-isolated-to-a-hardened-fact-adapter.md) | Public source retrieval is isolated to a hardened fact adapter; append-only checks, not_found is not disappearance, no body storage, no candidate | Accepted |
| [0023](0023-detector-owned-candidate-specifications.md) | Detector-owned candidate specifications preserve a profile-neutral PatternCandidate; second profile (Scientific Preservation) carried with repository candidates byte-identical, no v2 | Accepted |
| [0024](0024-controlled-profile-switch-lifecycle.md) | Controlled profile-switch lifecycle: active-to-active cutover, durable attempt journal, admission fence, migration and rollback limits (design only) | Accepted — implementation deferred |

## Adding an ADR

1. Copy the field set from any existing ADR.
2. Use the next zero-padded number; keep the filename kebab-case.
3. Cite **repository evidence** (`path:line` or module/function) so the decision stays checkable.
4. Add a row to the index above.
5. If it reverses a prior decision, set that ADR's `Superseded-by` and this one's `Supersedes`.
