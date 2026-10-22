# ADR-0010 — The live Gateway bridge is blocked pending package-side REST exposure

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

The Gateway/Lighthouse split (ADR-0001) anticipates a live, read-only bridge: the Gateway reads
profile state, backup readiness, and evidence-stripped findings from Lighthouse over a bounded
`/local/*` surface. But the deployed Start9 package currently publishes **only MCP-SSE on port 8001** —
there is no private REST / `/local/*` surface exposed from the package. Standing this bridge up before
that exposure is designed and approved would create an unreviewed network surface (originally "governing
decision #10").

## Decision

- The live Gateway bridge is **blocked** until a separate, approved packet exposes a private
  REST/`/local/*` surface from the Start9 package.
- Until then the split stays **fixture-backed on the Gateway side**; Lighthouse's `/local/*` routes
  remain loopback-only (`127.0.0.1`/`::1`).
- The future read-only contract (`profiles.list`, `profiles.active`, `profiles.preview`,
  `backup.readiness`, `findings.recent`, `disclosure.package.summary`) is documented but not yet wired.

## Consequences

- Phase G (Gateway Integration) is blocked on this ADR plus a package-exposure packet.
- Docs must not claim a live bridge exists.

## Repository evidence

- `docs/GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md` ("The live bridge is blocked … currently only
  MCP-SSE:8001 is published"; future read-only method table).
- `portal/app.py` (`_require_local_client` restricts `/local/*` to `127.0.0.1`/`::1`).
