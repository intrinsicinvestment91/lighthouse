# ADR-0012 — Naming: the product is Lighthouse; `pattern-match-sentinel` is the repository identifier

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** ROADMAP-RECONCILIATION-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

The project began as "Pattern Match Sentinel" and the git repository, Python package, and Docker image
retain that identifier. The product, CLI (`lighthouse`), and Start9 application are named "Lighthouse".
Documentation drifted: `README.md` still titled the product "Pattern Match Sentinel" and
`CONTRIBUTING.md` said "Project Lighthouse", confusing readers about what the thing is called.

## Decision

Use these names consistently:

| Concept | Name |
|---------|------|
| Product | **Lighthouse** |
| Start9 application | **Lighthouse** |
| Platform | **Lighthouse Intelligence Platform** |
| Git repository / package / image / paths | **`pattern-match-sentinel`** (identifier only) |

- The repository name appears **only** when referring to Git identity, package name, or literal paths.
- "Pattern Match Sentinel" is **not** used as a product name anywhere.
- Repository paths are **not** renamed.

## Consequences

- One product name across all docs and the static site.
- The lowercase `pattern-match-sentinel` remains valid wherever it denotes repo/package identity (e.g.
  clone URLs, site footers).

## Repository evidence

- Prior drift: `README.md:1` ("Pattern Match Sentinel"), `CONTRIBUTING.md:1` ("Project Lighthouse").
- Consistent product usage across the internal agent guide, `docs/ROADMAP_STATUS.md`, `docs/site/*` and
  the `lighthouse` CLI entry point in `pyproject.toml`.
