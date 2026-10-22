# ADR-0005 — The invention-discovery engine belongs in Lighthouse; migration deferred

- **Status:** Accepted — implementation deferred
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

An invention-discovery engine (card types, composite scoring, pattern detectors) already exists,
fixture-backed and fail-closed, outside this repository. Lighthouse is the home of the intelligence
engine (ADR-0001), so invention discovery conceptually belongs here. But moving it now — before the
profile contract, backup readiness, and the live-interface boundary are independently proven — would
couple unproven surfaces together.

## Decision

- The invention-discovery engine **ultimately belongs inside Lighthouse.**
- Its current implementation **stays outside this repository** for now; migration is **intentionally
  deferred** until the profile contract (ADR-0002/0003), backup readiness (ADR-0004), and the live bridge
  (ADR-0010) are separately proven.
- When it moves, the **vocabulary is reused** and the engine is **not duplicated** — no parallel second
  engine.
- Until then, Lighthouse ships a private invention-discovery profile as a **declarative seed that
  references** the gateway vocabulary and copies **no** engine code.

## Consequences

- No invention engine code lives in this repository today; docs must not imply otherwise.
- Repository Source Packs are treated as an observation source, not a second engine (ADR-0008).

## Repository evidence

- `docs/INTELLIGENCE_PROFILE_ARCHITECTURE.md` ("Why the invention engine is not migrated here").
- A private invention-discovery profile seed (declarative reference; `status: experimental`).
- Audit confirmed: no `invention`/`source pack` engine code under `modules/` or `portal/`.

> **Public release note (2026-10-21):** the private invention-discovery profile seed is not bundled in
> this public release; five profiles ship in `seed/profiles/`.

## Implementation status (2026-07-14, LHP-ID-PRESERVE-01) — the location premise was wrong

**The decision stands. Its premise did not.**

This ADR states that the engine *"already exists … in the separate Gateway application"* and that its
implementation *"stays in the Gateway for now."* A forensic inventory proved otherwise: **the engine was
in no git repository at all.** The Gateway held only a consumer/validator and one derived output of
the untracked engine — **the output was under version control while the engine that produced it was
not**, with nothing binding one to the other.

The error was consequential: because the architectural record said the engine was safely somewhere, nobody
looked. It has now been preserved privately under version control with a provenance manifest.

**Unchanged:** no invention engine code lives in *this* repository, migration remains deferred, and the
vocabulary is to be reused rather than duplicated when it eventually moves.

**Amended by [ADR-0020](0020-profile-neutral-substrate-profile-owned-detectors.md):** migration is now
gated on the accepted significance boundary and is **selective, one detector family at a time** — never a
bulk copy. The prototype's composite scoring is **quarantined**, and the prototype card contract is
**preserved, not adopted** as the shared platform contract.
