# ADR-0002 — Intelligence Profiles are declarative policy bundles, not plugins

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** LHP-PROFILE-01
- **Supersedes:** —
- **Superseded-by:** —

## Context

An **Intelligence Profile** describes which mission a Lighthouse deployment is configured for (public
scientific, private invention discovery, aerospace/defense monitoring, …). A profile could have been
implemented as a plugin that declares executable modules — but that would make every profile a code
path to audit, with arbitrary-execution blast radius.

## Decision

A profile is a **versioned, declarative policy bundle** that declares *what* is allowed (source
categories, collectors, observation tiers, graph/correlation/temporal participation, privacy/publishing
policy, backup requirements, interface eligibility, DMS eligibility, compatibility) — never *how* to
execute anything. It is data, not code.

`IntelligenceProfile.validate()` fails closed. It structurally **rejects executable-intent keys**
(`module(s)`, `command`, `cmd`, `shell`, `exec`, `entrypoint`, `eval`, `script`, `run`, `callable`,
`import`, `subprocess`) at any depth in any policy block; rejects unknown/future `schema_version`;
rejects a private profile that would publish publicly; and rejects DMS enrollment declarations
(see ADR-0006). The dataclass validator is authoritative; the JSON Schema is advisory (no `jsonschema`
runtime dependency).

## Consequences

- A bad profile's blast radius is a validation error, never arbitrary execution.
- Profiles are reviewable and diffable as data.
- Any future capability a profile needs must be expressed declaratively and implemented in the engine,
  not smuggled in as profile-declared code.

## Repository evidence

- `modules/profiles.py`: `INTELLIGENCE_PROFILE_SCHEMA_VERSION = 1`; `_FORBIDDEN_POLICY_KEYS`;
  `validate()`; module docstring ("does not activate, resolve, or apply any profile").
- `docs/INTELLIGENCE_PROFILE_ARCHITECTURE.md` (contract invariants).
- `docs/schemas/intelligence_profile.v1.schema.json` (advisory schema).
- `seed/profiles/*.json` (six declarative seeds).
