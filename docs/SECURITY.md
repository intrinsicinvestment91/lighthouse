# Lighthouse — Security Posture (`LHP-SECURITY-01`)

> **Canonical security overview.** This document answers *"What is the security posture of Lighthouse?"*
> It consolidates the platform's security architecture and status. The detailed, itemised risk register
> lives in [`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md); the decisions behind these
> controls are in [`adr/`](adr/README.md).

`LHP-SECURITY-01` is the identifier for this consolidated posture. It is not a claim that the platform
is fully hardened — several items below are **blocking** for future work and are recorded as such.

## Design tenets

- **Fail closed.** Missing, invalid, or unverifiable safety configuration must **deny**, never allow.
- **Deterministic core.** No LLM in correlation/scoring/witness/explanation
  ([ADR-0009](adr/0009-no-llm-in-deterministic-core.md)) — output is reproducible and attestable.
- **Least exposure.** MCP stays private; `/local/*` is loopback-only; only high-signal, evidence-stripped
  correlations may publish.
- **Preserve intelligence.** Authoritative state stays inside the `/data` backup boundary
  ([ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md)).

## Gateway approval architecture (and current defect)

Execution/approval authority belongs to the external Gateway
([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)); today Lighthouse holds the
live MCP approval gate as an interim exception. **Required contract:** any authority gate must **fail
closed** — deny when its secret is unset, invalid, or unverifiable.

**Lighthouse gate — remediated (`LHP-SECURITY-01`).** Lighthouse's `LIGHTHOUSE_APPROVAL_SECRET` MCP
gate (`_is_human_approved`, guarding `run_module`/`run_pipeline`/`publish_note`) now **fails closed**:
it denies when the secret is missing, empty, or whitespace; when the token is missing, empty, or
malformed; when configuration is unreadable; or when validation raises. Token comparison is timing-safe
(`hmac.compare_digest`); secrets and tokens are never logged. Approval is granted only when a non-empty
secret is configured *and* a matching token is supplied.

**Remaining defect (blocking, cross-repo):** the Gateway's own `_is_human_approved` still **fails open**
(approve) when its secret is unset. That is a separate repository and is **not** modified here; it is
tracked as a blocking Gateway-side defect. This is also why Lighthouse retains an independent
fail-closed local gate as **defense-in-depth**
([ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md)). See
[`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md) risk 2.

## Backup boundary & `/data` enforcement

Authoritative state (`state.db`, `entities.json`, `targets.json`, archives) must resolve under the
canonical `/data` boundary. `inspect_backup_readiness()` fails closed (`NOT_READY`) when `DB_PATH` or a
profile `data_root` escapes `/data`, including `..` traversal and symlink escape. `READY` means
*eligible for backup*, never *backed up*. Future profile activation is blocked while readiness is not
`READY`. See [ADR-0004](adr/0004-backup-readiness-fail-closed-data-boundary.md) and
[`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md) risk 3.

## Verified backup manifests & restore drills (LHP-PROFILE-02)

The backup manifest, verification report, and restore-drill report are **sensitive operational
metadata**. They may reveal filenames, directory structure, state categories, sizes, timestamps,
software/schema versions, the repository commit, and storage layout — **filenames alone can be
sensitive**. They deliberately embed **no** file contents, database rows, secrets, private keys,
approval tokens, environment values, or Nostr keys, and error messages avoid unsafe disclosure
while retaining local operator diagnostics. SQLite state is captured with the online-backup API
(consistent snapshot, source never mutated), digests are SHA-256, and every hazard — symlink
escape, `..` traversal, special files, external-unmanaged authoritative state, destination overlap,
a `/data` restore target — fails closed. Encryption, key management, and remote upload are
explicitly deferred; the manifest is designed so a later encryption layer can wrap the payload
without changing manifest semantics. A completed backup or restore drill **never authorizes a
profile switch** and is **not** a production restore. See
[ADR-0013](adr/0013-verified-backup-and-restore-drill-before-profile-activation.md).

## Defense-in-depth

Even once the Gateway owns authority, Lighthouse keeps its own fail-closed layers: the local approval
gate, the loopback-only `/local/*` restriction (`127.0.0.1`/`::1`), the four-condition Nostr publish
gate, and evidence-stripping on all outward responses. No single external record is treated as
Lighthouse's authoritative internal state.

## Publishing controls

Only correlation events may publish, and only when **all four** hold: `NOSTR_PUBLISH_CORRELATIONS=true`,
`not dry_run`, `score >= 7.0`, and ≥2 unique signals. Only `entity`, `event_type`, `score`, `signals`,
`summary` may appear in a note — never raw alert payloads, full evidence, secrets, or keys.

## The intelligence/authority boundary (ADR-0020)

**Lighthouse determines:** *"What intelligence can be extracted from the admitted evidence?"*
**Gateway determines:** *"May a consequential action or external release occur?"*

Consequently, and permanently:

- A **finding is not permission.** Reaching the top of Lighthouse's evidence chain — observation →
  normalized fact → direct relationship → cross-scope fact → pattern candidate → hypothesis → reviewed
  finding — authorizes **nothing**. No score, at any layer, may promote a candidate, authorize publication,
  authorize execution, override missing evidence, or suppress counterevidence.
- The **Gateway** retains consequential approvals, external publication authorization, disclosure-package
  approval, operator identity for release actions, trusted-contact policy, countdown policy, DMS
  enrollment/cancellation/simulation/release, and the release audit history.
- **Local candidate dismissal is a Lighthouse concern** and does not route through the Gateway. Reviewing
  your own evidence is not a consequential action.
- **Privacy and publishing eligibility are profile-owned** and declarative; they are policy a profile
  *declares*, never capability a profile *grants*.

## Public-source retrieval — one hardened chokepoint (LHP-INTEL-04B, ADR-0022)

The fact-first pipeline is network-free by doctrine. `lighthouse public-sources` adds the single exception: `modules/public_source_intake.py` is the **only** fact-first module permitted a network-capable import, pinned by `tests/unit/test_public_source_network_boundary.py` (which would fail if `socket`/`ssl`/`http.client`/`httpx` appeared in any other fact-first module). The LHP-INTEL-04C scientific detector (`modules/scientific_source_disappearance.py`) and the detector-spec registry (`modules/pattern_candidate_specs.py`) are pinned into that same network-free set — the disappearance rule reads only the *stored* check facts a prior fetch recorded and never reaches the network itself.

- **Empty-default allowlist.** `LIGHTHOUSE_PUBLIC_SOURCE_HOSTS` is the authoritative outer boundary; empty means *check nothing*, and a check refuses before any network. Exact-host match only — no suffix trust. A targets file may only narrow it and cannot authorize a host.
- **SSRF + DNS-rebinding.** Every resolved address is validated before connecting (loopback/private/link-local/multicast/reserved/CGNAT/IPv4-mapped-private/metadata refused; a mixed answer refuses the whole host). A guarded `HTTPSConnection.connect()` dials the validated IP while TLS verifies the real hostname (SNI + cert), so a name cannot be rebound; the peer is re-checked. Redirects are manual, bounded, and revalidated each hop; HTTPS→HTTP is refused.
- **HTTPS-only, no ambient state.** TLS verification required; URL credentials, cookies, authorization, and ambient proxies are refused. Bodies are streamed within a bound; a body over the bound gets no digest.
- **Facts, not verdicts.** A closed outcome vocabulary with no word for *disappeared*/*suppressed*/*important*; a `not_found` is HTTP 404/410, never disappearance. The ledger stores the digest and metadata, **not the response body** (it is not a web archive). No candidate, no score, no scheduler, no LLM, and no Gateway contact.

## Dead-Man's Switch — blocked

A DMS mechanism exists but **production use is blocked** pending a dedicated threat model and release
state machine. Profiles may declare DMS *eligibility* only; enrollment/activation declarations are
rejected by the contract. See [ADR-0006](adr/0006-dms-production-blocked-pending-threat-model.md) and
[`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md) risk 1 (highest severity).

When (and only when) it is ever built, it remains **Gateway-controlled** and bounded by these properties
([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)) — recorded here as the
authority boundary, **not** as an implementation:

- disabled by default · explicitly enrolled · package-specific · simulation-first · fail-safe;
- **unable** to release arbitrary database contents, raw private repositories or notes, or **unreviewed
  findings**;
- limited to **operator-approved disclosure packages**; blocked by package-digest mismatch; blocked by
  policy uncertainty;
- **failure or uncertainty must delay release, never accelerate it.**

## Threat-model status

| Area | Status |
|------|--------|
| Deterministic core / no-LLM | ✅ Enforced |
| Backup boundary (`/data`) | ✅ Enforced (fail-closed) |
| MCP privacy / loopback `/local/*` | ✅ Enforced |
| Publishing gate | ✅ Enforced |
| Authority gate fail-closed | ✅ **Enforced** — fixed in `0cbbfcd`; `_is_human_approved` denies on missing/empty/whitespace secret, malformed/invalid token, unreadable config, or **any** exception. Uncertainty denies ([ADR-0007](adr/0007-authority-gates-fail-closed-local-gate-defense-in-depth.md)) |
| Fact substrate cannot score | ✅ Enforced — no `score`/`weight`/`priority`/`importance`/`severity`/`rank` column in any fact table (pinned by a `PRAGMA table_info` test); closed attribute vocabulary; AST name guards ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)) |
| Significance layer | 🚫 Not built — boundary decided ([ADR-0020](adr/0020-profile-neutral-substrate-profile-owned-detectors.md)); a finding authorizes nothing |
| DMS release safety | ⛔ **Blocked** — no production behavior until threat model (ADR-0006) |
| Full platform threat model | 🔭 Pending (prerequisite for Phase K public release) |

## Cross-repository dependencies

Lighthouse's security posture depends on the separate Gateway application for approval/policy/audit
([ADR-0001](adr/0001-lighthouse-owns-engine-gateway-is-control-plane.md)). The Gateway's fail-open
approval defect is a **cross-application** blocker for any live profile switch or source-pack bridge;
the Gateway is not modified from here. The live bridge itself is blocked until package-side
exposure is designed and approved
([ADR-0010](adr/0010-live-gateway-bridge-blocked-pending-package-exposure.md)).

## Reporting

This is a private, local-first platform under active development. Security-relevant findings should be
recorded in [`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md) with a fail-closed disposition and,
where they set precedent, promoted to an [ADR](adr/README.md).
