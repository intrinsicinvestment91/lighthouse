# ADR-0007 — Authority gates fail closed; the local MCP gate is defense-in-depth

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

Execution authority belongs to the Gateway (ADR-0001). Today, however, Lighthouse contains the live
approval gate: the MCP server requires an `approval_token` for non-dry-run `run_module`/`run_pipeline`
and for live `publish_note`. Both the Gateway's `_is_human_approved` and Lighthouse's
`LIGHTHOUSE_APPROVAL_SECRET` gate currently **fail open** when their secret is unset — approving by
default. That is a blocking, cross-repository defect.

## Decision

- **Missing, invalid, or unverifiable approval configuration must fail closed (deny), never open.**
- Lighthouse **retains a local approval gate as defense-in-depth** until the Gateway implements
  equivalent authority; even after that, Lighthouse's gate stays as an independent fail-closed layer.
- Any Lighthouse-side activation/disclosure gate that reuses an approval token must fail closed on its
  own, independent of the Gateway.
- The Gateway repository is not modified by this decision; a dedicated Gateway security patch is
  recommended before any live profile switch or source-pack bridge.

## Consequences

- The current fail-open behavior is recorded as a defect to remediate (a follow-up code packet), not a
  design choice. Docs must describe the *intended* fail-closed contract and flag the current gap.
- Phase H (Execution Authority) depends on this ADR.

## Implementation status

- **Lighthouse side — implemented (`LHP-SECURITY-01`).** `_is_human_approved` now fails closed: it
  denies on missing/empty/whitespace secret, missing/empty/malformed token, unreadable configuration,
  or any validation exception, and compares tokens with `hmac.compare_digest`. A regression test
  reproduces the original fail-open behavior and confirms it is closed
  (`tests/unit/test_mcp_server.py`).
- **Gateway side — still open.** The Gateway's own `_is_human_approved` fail-open remains a blocking
  cross-repo defect; that repository is not modified here.

## Repository evidence

- `portal/mcp_server.py`: `_expected_approval_token()` (returns `None` when secret unset — the gate
  now treats that as **deny**), `_is_human_approved()` (fail-closed, timing-safe), enforcement on
  `run_module`/`run_pipeline`/`publish_note`.
- `docs/PROFILE_SECURITY_RISKS.md` risk 2 (Lighthouse resolved; Gateway `_is_human_approved` fail-open
  remains cross-repo).
