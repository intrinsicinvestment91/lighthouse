# ADR-0011 — Role-separated documentation with one canonical source per topic

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** ROADMAP-RECONCILIATION-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

Planning docs accumulated faster than they were reconciled. The repository carried five different test
counts, contradictory packet numbering across `PACKETS.md` / `ROADMAP_STATUS.md` / git, "Packet 28"
meaning two things, a stale product name, and dangling "decision #N" references with no registry. Three
documents each claimed to be "canonical". New contributors and AI assistants could not tell which
source to trust.

## Decision

Adopt a role-separated documentation system where **each document answers exactly one question and each
topic has exactly one canonical source**; everything else links rather than copies.

| Document | Answers |
|----------|---------|
| `README.md` | What is Lighthouse? |
| `docs/ARCHITECTURE_INDEX.md` | Where do I find everything? |
| `docs/ROADMAP.md` | Where are we going? (Phases A–K) |
| `docs/ROADMAP_STATUS.md` | Where are we now? (+ implementation matrix) |
| `docs/PACKETS.md` | How did we get here? (historical engineering log) |
| `docs/FUTURE_PLAN.md` | What could come next? (research/backlog) |
| `docs/adr/` | What has been decided & why? |
| `docs/SECURITY.md` | What is the security posture? (`LHP-SECURITY-01`) |

The static HTML site (`docs/site/`) is an **explanatory mirror**, secondary to Markdown; every
substantive HTML statement must trace to a canonical Markdown doc.

## Consequences

- History is preserved, not deleted: superseded planning is reframed and cross-linked, not removed.
- Contradictions (test counts, packet numbers) are resolved to a single value; the verified test count
  is 856.
- Future packets that materially change operator-facing architecture must update the canonical doc for
  that topic (and mirror to the site).

## Repository evidence

- This reconciliation (ROADMAP-RECONCILIATION-01) and the documents listed above.
- `docs/site/` (mirror; "canonical source: Markdown docs" footer).
