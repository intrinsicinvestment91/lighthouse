# ADR-0001 — Lighthouse owns the intelligence engine; Gateway is the thin control plane

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

Lighthouse produces intelligence (observations, graph, correlation, temporal/tempo, profiles, backup
evidence). A separate application, the **Gateway**, provides operator review, approval, policy, and
audit surfaces. Without a fixed ownership split, future packets could duplicate the engine in the
Gateway, or let Gateway approvals silently become Lighthouse's authoritative state. This decision
records the operator-approved split (originally "operator decision #1").

## Decision

Lighthouse is the **home of the intelligence engine**. The Gateway is a **thin control, approval,
policy, and audit plane**.

- **Lighthouse owns:** profile definitions/validation/(future) resolution + activation state; intake
  validation and normalization; observation creation; graph construction; temporal and tempo
  derivation; correlation; (future) candidate invention findings and disclosure-package *preparation*;
  backup-readiness evidence about its own state. Findings and profile state are exposed **read-only**
  (the loopback, evidence-stripped `modules/local_gateway.py` pattern).
- **Gateway owns:** operator review/approval surfaces; profile-switch authorization + audit trail;
  source-pack admission approval; (future) DMS release policy.
- **Invariant:** the Gateway never executes Lighthouse work and never writes Lighthouse data. A Gateway
  approval record is *not* authoritative approval state inside Lighthouse.

## Consequences

- Clear seam: the engine can evolve without the Gateway, and vice versa.
- "Gateway" as a control plane is distinct from the in-repo **Local Gateway** (`modules/local_gateway.py`),
  which is a bounded read-only *dispatch* layer, not the control plane. See ADR-0007, ADR-0010.
- The live bridge between the two is separately gated (ADR-0010).

## Repository evidence

- `docs/GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md` (ownership table; "Gateway never executes Lighthouse
  work and never writes Lighthouse data").
- `modules/local_gateway.py` (read-only loopback dispatch; 5 methods; `MAX_DEPTH=3`, `MAX_LIMIT=50`).
- No Gateway application code exists in this repository (reference only).

## Reconciled by ADR-0024 (2026-09-02, LHP-LIFE-DESIGN-01-C2)

[ADR-0024](0024-controlled-profile-switch-lifecycle.md) narrows Gateway ownership of profile-switch
authorization **only** for explicitly eligible, cryptographically bound, proven non-widening local
switches between enrolled `locally_activatable` profile pairs (enrollment is currently the empty set).
Gateway remains **mandatory** for **widening** switches — including when Gateway is unavailable or
`not_implemented`. **`indeterminate` projection dimensions refuse unconditionally** (ADR-0024 §2.3,
§13) — Gateway cannot authorize unknown policy. This ADR's Decision body is unchanged; the carve-out is
documented here and in ADR-0024 §13.
