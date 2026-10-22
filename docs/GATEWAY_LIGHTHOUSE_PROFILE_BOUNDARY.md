# Gateway / Lighthouse Profile Boundary

> Status: **contract documentation only** (packet LHP-PROFILE-01). No live bridge is
> implemented. The deployed Start9 package exposes only MCP-SSE on port 8001 — there is no
> REST / `/local/*` surface for a Gateway bridge yet.

Per the approved analysis and operator decision #1 ([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)), Lighthouse is the **home of the intelligence
engine** and the external operator gateway (a separate application, not bundled with Lighthouse) is a
**thin control, approval, policy, and audit plane**.
This document fixes who owns what, so future packets do not blur the split.

## Lighthouse owns (the engine)

- Profile definitions, validation, and (future) resolution + activation state.
- Intake validation and normalization; observation creation.
- Graph construction; temporal and tempo derivation; correlation.
- (Future) candidate invention findings and disclosure-package *preparation*.
- **Backup-readiness evidence about its own state.**

Findings and profile state are produced here and exposed **read-only** (the loopback,
evidence-stripped `modules/local_gateway.py` pattern).

## Gateway owns (the thin control plane)

- Operator review/approval surfaces (the already-shipped fixture-backed preview routes and
  dashboards).
- Profile-switch authorization and the audit trail.
- Source-pack admission approval.
- (Future) DMS release policy.

**Gateway never executes Lighthouse work and never writes Lighthouse data.** Gateway approval
records are **not** authoritative approval state inside Lighthouse — Lighthouse stores its own
evidence; it does not treat a Gateway record as the source of truth for its internal state.

## The read-only contract Lighthouse will expose (future)

A bounded, read-only, evidence-stripped method set (extending the `/local/*` gateway):

| Method | Returns |
|--------|---------|
| `profiles.list` | installed/bundled profiles + validation status |
| `profiles.active` | the currently active profile (once activation exists) |
| `profiles.preview` | a proposed transition preview |
| `backup.readiness` | the backup-readiness evidence (from `inspect_backup_readiness`) |
| `findings.recent` | recent candidate findings (evidence-stripped) |
| `disclosure.package.summary` | a disclosure-package summary (never raw evidence) |

## Hard prerequisite: package-side exposure

The live bridge is **blocked** until a separate, approved packet exposes a private REST/`/local/*`
surface from the Start9 package (currently only MCP-SSE:8001 is published). Until then the split
stays fixture-backed on the Gateway side. Introducing a live bridge before that exposure is
designed and approved is out of scope (governing decision #10, recorded as [ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md)).
