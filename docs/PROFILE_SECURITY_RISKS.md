# Profile Platform — Security & Privacy Risk Register

> Status: **findings recorded** (packet LHP-PROFILE-01). These are blocking dependencies for
> future profile activation, disclosure, and DMS work. This packet does not remediate them; it
> records them and enforces the fail-closed dispositions it can from the Lighthouse side.
>
> **Canonical security overview:** [`SECURITY.md`](SECURITY.md) (`LHP-SECURITY-01`) — this file is the
> detailed risk register it links to. The decisions behind each disposition are in [`adr/`](adr/README.md).

## 1. Dead-man's-switch timer publication (HIGHEST) — see [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md)

**Finding.** The existing DMS path (`portal/dms_checker.py` + `--module dms-check`) can broadcast
the stored ciphertext to Nostr once a deadline lapses **with no score, approval, or witness gate**
— unlike the 4-condition correlation publish gate.

**Disposition (enforced/adopted here):**
- DMS **production use is blocked.**
- Profiles may only declare DMS *eligibility* (`dead_mans_switch_policy.eligible`), which is
  boolean and defaults `false`. The contract **rejects** any enrollment/activation declaration
  (`enrolled/activated/active/enabled/armed/scheduled`).
- No profile can enroll itself; no timed-release behavior is implemented in this packet.
- A dedicated future threat-model + release state-machine initiative is **mandatory** before any
  DMS behavior ships. Failure or uncertainty must **delay** release, never accelerate it.

## 2. `_is_human_approved` fail-open (Lighthouse RESOLVED · Gateway BLOCKING, cross-repo) — see [ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md)

**Finding.** The Gateway's `_is_human_approved` approval check can **fail open** (approve) when its
secret is unset. The Lighthouse `LIGHTHOUSE_APPROVAL_SECRET` MCP gate previously shared this defect.

**Lighthouse remediation — shipped (`LHP-SECURITY-01`).** Lighthouse's `_is_human_approved`
(`portal/mcp_server.py`, guarding `run_module`/`run_pipeline`/`publish_note`) now **fails closed**:
it denies on missing/empty/whitespace secret, missing/empty/malformed token, unreadable configuration,
or any exception during validation. Comparison is timing-safe (`hmac.compare_digest`); secrets and
tokens are never logged. A regression test reproduces the original fail-open behavior and confirms it
is closed (`tests/unit/test_mcp_server.py`).

**Disposition:**
- **Lighthouse side: RESOLVED** by `LHP-SECURITY-01`.
- **Gateway side: still BLOCKING (cross-repo).** The Gateway repository is **not** modified in this
  packet; a dedicated Gateway security patch is recommended before any live profile switch or
  source-pack bridge.
- Required contract (both sides): **missing, invalid, or unverifiable approval configuration must fail
  closed** (deny), never open.
- Any Lighthouse-side activation/disclosure gate that reuses an approval token must fail closed
  independently.

## 3. `DB_PATH` backup escape (BLOCKING for activation) — see [ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md)

**Finding.** An authoritative database path configured outside `/data` (via `DB_PATH`) silently
escapes StartOS backup expectations. A per-profile data root outside `/data` would do the same.

**Disposition (enforced here):**
- `inspect_backup_readiness()` **fails closed** (`NOT_READY`) when `DB_PATH` or any authoritative
  path resolves outside the boundary, and records an `external_unmanaged` check.
- A profile's declared `backup_policy.data_root` must resolve under `/data` or `validate()`
  rejects it.
- Future profile activation must be **blocked** while readiness is not `READY`; configuration must
  not silently fall back to an external path.
- Path checks are canonical (`Path.resolve()`), covering direct external paths, `..` traversal,
  relative/ambiguous paths, and symlink escape.

## Standing guardrails reaffirmed

- Do not publish raw alerts or full evidence; keep the correlation publish gate and
  `NOSTR_PUBLISH_CORRELATIONS=false` default.
- Private profiles: local-only, no external model calls, no raw-content export, no public
  publishing — all fail-closed defaults in the contract.
- External repositories referenced by capability-discovery profiles are reference-only: never
  executed, never installed.
