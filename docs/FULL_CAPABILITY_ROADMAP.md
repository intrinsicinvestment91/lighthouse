# Lighthouse — Full-Capability Roadmap

> **Authoritative intended destination and ordered work** from released **v1.0.0** through the
> complete sovereign, profile-driven intelligence platform. For *shipped and verified state* see
> [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) — that document wins whenever this roadmap could be read as
> claiming something is already available. For *historical phase direction* (A–K) see
> [`ROADMAP.md`](ROADMAP.md). For *execution history* see [`PACKETS.md`](PACKETS.md).

## Purpose and audience

This roadmap directs all future Lighthouse engineering from the **v1.0.0** baseline to **full-capability
V2**. It covers every bundled intelligence profile and every shared capability required to operate them
safely. It uses stable **capability IDs** so work packets, release trains, and status updates stay
traceable without renumbering.

**Audience:** implementers, reviewers, operators, and future agents. **Not** a substitute for ADRs,
profile contracts, or the implementation matrix.

## v1.0.0 baseline (unchanged)

Release **v1.0.0** (`3419c769`, tag at HEAD) claims a **local RCD operator foundation**, not full
multi-profile or production capability. See [`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md).

**In v1.0.0:** backup-gated initial activation of `repository_capability_discovery` v2; observe/detect
admission; RCD loop; OSR producer export; public-source ledger + SciPres detector (detector only — not
SciPres activation ceremony).

**Not in v1.0.0:** profile switching; activating any profile other than RCD v2; hypothesis or finding
layers; Gateway authority; invention migration; sovereign production deployment completeness.

## Authority hierarchy

When documents disagree, resolve in this order:

| Rank | Source | Role |
|------|--------|------|
| 1 | [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) | Authoritative **shipped and verified** state |
| 2 | **This document** | Authoritative **intended destination** and ordered work |
| 3 | [`adr/`](adr/README.md) | Accepted architectural decisions and frozen boundaries |
| 4 | [`seed/profiles/`](../seed/profiles/) | Profile-owned source, detector, evidence, privacy, output policies |
| 5 | [`PACKETS.md`](PACKETS.md) | Small execution slices tied to capability IDs |
| 6 | Invention-discovery prototype (external; private) | Noncanonical design and historical evidence only |

**Calibration rule:** A capability marked `shipped` here must match [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md)
or cite tests/rehearsals explicitly. This roadmap **never silently upgrades** planned work into shipped
status. Prototype artifacts are **never** evidence of runtime capability.

## State vocabulary (closed)

Each capability entry uses exactly one state:

| State | Meaning |
|-------|---------|
| `shipped` | Implemented and recorded in ROADMAP_STATUS as ✅ |
| `proven_nonproduction` | Verified under controlled tests or disposable rehearsal; not a production claim |
| `partial` | Foundation exists; known gaps or defects documented |
| `planned` | Accepted intent; no implementation evidence yet |
| `blocked` | Gated by an accepted ADR or external dependency |
| `deferred` | Explicitly out of scope until named resume conditions |

## Target platform definition

Full capability means a platform where:

1. Exactly one validated profile is active at a time.
2. Every bundled profile has a closed, versioned, tested contract.
3. Profiles own their source policies, detectors, evidence thresholds, privacy rules, outputs, and
   authority boundaries.
4. Shared infrastructure remains profile-neutral.
5. Profile activation, switching, migration, rollback, and recovery are fail-closed and backup-gated.
6. Observations are durable, provenance-bound, queryable, and idempotent.
7. Candidate, hypothesis, finding, and publication states are distinct.
8. No automated process can silently promote evidence into a finding.
9. Public-source evidence can be checked, preserved, reviewed, and exported.
10. Private material remains local and protected by the owning profile.
11. Gateway approval is required wherever external action or publication authority is needed.
12. Scheduling and automation operate only within active-profile policy.
13. Operator actions are auditable.
14. Backup, restore, upgrade, and disaster recovery are rehearsed.
15. CLI, API, and operator interface expose the same contracts.
16. Supported production deployment is reproducible, observable, and recoverable.
17. Every claim has executable or rehearsed evidence.

---

## Intelligence lifecycle gates

Detectors produce **candidates** only. Promotion requires explicit human or Gateway-controlled
transitions ([ADR-0021](adr/0021-pattern-candidates-are-review-objects-not-findings.md)).

```text
observation
  → candidate                    [DET + REV-CAND-01 — partial/shipped]
  → reviewed_candidate             [REV-DISP-01 — shipped for disposition]
  → hypothesis                     [REV-HYP-01 — planned]
  → validated_finding              [REV-FIND-01 — planned]
  → governed_output                [REV-GOV-01 — planned]
```

| Gate | Capability ID | State | Transition authority |
|------|---------------|-------|----------------------|
| Observation | OBS-* | shipped (substrate) | Collectors + intake boundaries |
| Candidate | REV-CAND-01, DET-* | shipped (RCD + SciPres detectors) | Deterministic rules only |
| Reviewed candidate | REV-DISP-01 | shipped | Operator disposition CLI |
| Hypothesis | REV-HYP-01 | planned | Human / Gateway (future) |
| Validated finding | REV-FIND-01 | planned | Human / Gateway (future) |
| Governed output | REV-GOV-01, AUTH-PUB-01 | planned | Gateway approval required |

---

## Stable capability ledger

Each entry: **ID** · intended capability · state · dependencies · completion gate · verification
evidence · claims prohibited until completion.

### SEC — Security and fail-closed authority

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| SEC-FAILCLOSED-01 | Lighthouse authority gates deny on uncertainty | shipped | ADR-0007 | Local gates fail-closed; missing/malformed evidence denies | SECURITY.md, ADR-0007, test_mcp_server.py | "all authority gates safe" (Gateway separate) |
| SEC-MCP-01 | Lighthouse MCP live-action approval gate | shipped | SEC-FAILCLOSED-01 | Missing/empty secret denies; malformed/invalid token denies; evaluator exceptions deny; `hmac.compare_digest`; regression tests | portal/mcp_server.py; SECURITY.md LHP-SECURITY-01; PROFILE_SECURITY_RISKS risk 2 Lighthouse RESOLVED | "MCP safe without secret"; Gateway gate implied |
| SEC-RESTORE-01 | Restore-report positive Boolean fields strict | shipped | LIFE-RESTORE-01 | `validate_supplied_restore_drill_report()` requires JSON literal `true` for `manifest_valid`, `payload_verified`, `restored_digests_verified`, `sqlite_integrity_verified`, `source_unchanged`; Python/JSON truthy substitutes refused; `files_restored` must be a non-Boolean integer > 0 | backup_verify.py; test_v1_profile_activation.py (257 focused + 2359 full unit — verification evidence, not maturity proof) | "evidence validated" with truthy strings; "all evidence report fields typed"; "evidence ladder complete" |
| SEC-THREAT-01 | Threat model ↔ implementation reconciliation | planned | SEC-FAILCLOSED-01 | Lighthouse risks closed or accepted; Gateway cross-repo items tracked separately | SECURITY.md; PROFILE_SECURITY_RISKS | "security posture complete" |
| SEC-PATH-01 | Path traversal and symlink escape refusal | shipped | — | Backup/restore/activation refuse escape | backup_manifest.py, backup_readiness.py | — |
| SEC-NET-01 | Single hardened network chokepoint | shipped | — | Only public_source_intake may fetch | ADR-0022; AST boundary test | "network isolated" without host allowlist |
| SEC-AUDIT-01 | Security/system audit event integrity and retention | planned | OPS-LOG-01 | Append-only security-relevant audit substrate; retention policy enforced | — | "security events are audited" |

### CORE — Profile-neutral engine and storage

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| CORE-OBS-01 | Observation substrate contract | shipped | — | validate_observations; closed vocabularies | modules/observations.py; ROADMAP_STATUS | — |
| CORE-NORM-01 | Fact-only normalization storage | shipped | CORE-OBS-01 | No score/rank columns; validate on write | observation_normalizer.py; db.py | — |
| CORE-QUERY-01 | Read boundary validate-on-read | shipped | CORE-NORM-01 | mode=ro; tampered rows refused | observation_query.py | — |
| CORE-CAND-01 | Pattern candidate contract spine | shipped | CORE-NORM-01 | Closed field set; authority pins `is False` | pattern_candidate.py; ADR-0021 | "finding" |
| CORE-DETREG-01 | Detector-owned spec registry | shipped | CORE-CAND-01 | Closed REGISTRY; byte-compat RCD | pattern_candidate_specs.py; ADR-0023 | — |
| CORE-RESOLVE-01 | Profile → runtime policy resolver | planned | LIFE-ACTIVATE-01 | Collectors/privacy/publish read profile | ADR-0018 resume; ADR-0024 §9 | "profile controls runtime" |
| CORE-HYP-01 | Hypothesis contract layer | planned | REV-HYP-01 | Distinct from candidate; no auto-promotion | ADR-0020 | "hypothesis layer exists" |
| CORE-FIND-01 | Finding contract layer | planned | REV-FIND-01 | Distinct from hypothesis; Gateway gate | ADR-0021 | "finding layer exists" |
| CORE-PROV-01 | Provenance and digest identity | shipped | — | Canonical digests; idempotent store | observations.py; content_identity.py | — |
| CORE-ALERT-01 | Scored alert contract and persistence | shipped | — | AlertRecord contract; db insert/query | modules/alerts.py; ROADMAP_STATUS | — |
| CORE-CORR-01 | Multi-signal correlation and scoring | shipped | — | CorrelationRecord; weighted scoring; A3 correlator | correlator.py; correlation_scoring.py | — |
| CORE-GRAPH-01 | Graph model, persistence, and query | shipped | — | GraphNode/Edge; backfill; traversal CLI | modules/graph*.py; ROADMAP_STATUS | — |
| CORE-WITNESS-01 | Witness attestation on correlations | shipped | — | SHA-256 witness hash on correlation events (signature empty in production path; BIP-340 helper demo/test-only) | modules/witness.py | "tamper-proof", "tamper-evident", "authenticated" (integrity hash only) |
| CORE-TEMPO-01 | Temporal and operational tempo analysis | shipped | — | Informational tempo/temporal CLI; no scoring change | temporal_analysis.py; tempo_analysis.py | — |

### LIFE — Profile lifecycle and switching

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| LIFE-BACKUP-01 | Verified backup manifest | shipped | SEC-PATH-01 | Deterministic manifest; overlap refused | backup_manifest.py, backup_create.py | "backup performed" from manifest-only |
| LIFE-RESTORE-01 | Isolated restore drill | shipped | LIFE-BACKUP-01 | Never restores to /data; report pins false authority | backup_verify.py; ADR-0013 | "disaster recovery proven" |
| LIFE-PREVIEW-01 | Transition preview artifact | shipped | LIFE-BACKUP-01 | ready_for_review only with evidence | profile_transition.py; ADR-0014 | "ready to activate" without evidence |
| LIFE-PRECOND-01 | Activation preconditions evaluator | shipped | LIFE-PREVIEW-01 | unknown never becomes ready | activation_preconditions.py | — |
| LIFE-ACTIVATE-01 | Initial activation (no_active → profile) | partial | LIFE-PRECOND-01 | RCD v2 only; backup-gated; typed confirm | profile_activation.py; V1_OPERATOR_WORKFLOW | "any profile activatable" |
| LIFE-ACTIVATE-02 | Atomic activation DB writer | partial | LIFE-ACTIVATE-01 | Crash/durability and cross-process concurrency evidenced | LHP-V1-HARDEN-02 (`4f5f8c4`, `active_profile_store.py`): public activation DB writer removed from sanctioned surface; exactly one production caller via private store boundary (sanctioned ref map `{"modules/active_profile_store.py": 1}`); AST regression guards on current production Python surfaces; transaction body unchanged. Limitations: underscore privacy conventional; power-loss/process-kill crash safety unproven; cross-process concurrency unproven; switching unimplemented. ADR-0024 §7 cutover boundary (design) | "activation is atomic/crash-safe" without durability evidence |
| LIFE-SWITCH-01 | Active-to-active profile switching | planned | LIFE-ACTIVATE-02, CORE-RESOLVE-01 | Exactly one active after success; ADR-0024 | ADR-0024; ADR-0003 (invariants) | "profile switching shipped" |
| LIFE-MIGRATE-01 | Schema/data migration on switch | planned | LIFE-SWITCH-01 | Migration required disclosed; rollback path | ADR-0024 §10 | "migration proven"; "migration reversible" |
| LIFE-ROLLBACK-01 | Rollback after failed transition | planned | LIFE-SWITCH-01 | Failed switch leaves prior profile or safe state | ADR-0016 rollback boundary; ADR-0024 §11 | "rollback is safe"; "proven disaster recovery" |
| LIFE-EVID-01 | Evidence report strict validation | shipped | SEC-RESTORE-01 | All report fields typed; forged/mismatched reports refused | LHP-LIFE-EVID-01: typed `lighthouse_backup_verification.v1` / `lighthouse_restore_drill.v1` with full field value-type checks and status/detail consistency; `manifest_sha256` file-byte bind; `artifact_digest` uses `active_profile_state` recipe; `load_evidence` + activate refuse untyped/`passed`/cross-backup substitution; preview digest recomputed locally at activate (closed). Residual: restore is not re-run at activate | "evidence ladder complete" |
| LIFE-DEACT-01 | Profile deactivation | deferred | LIFE-SWITCH-01 | Explicit policy or documented unsupported | ADR-0024 §15 (unsupported) | "deactivate profile" |

### OBS — Observation intake and evidence ledger

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| OBS-REPO-01 | Repository observation intake | shipped | — | Pure collector; git read-only | repository_intake.py | — |
| OBS-DOC-01 | Document observation intake | shipped | — | Metadata-first; bytes identity | document_intake.py | — |
| OBS-RUN-01 | Runtime observation boundary | shipped | OBS-REPO-01, OBS-DOC-01 | Empty roots refuse; dry run writes nothing | observation_intake.py | — |
| OBS-CONTENT-01 | Raw-byte content identity | shipped | — | content:sha256; tracked files only | content_identity.py; ADR via 03A | "content preserved" (identity only) |
| OBS-XSCOPE-01 | Cross-scope fact discovery | shipped | CORE-QUERY-01 | Exact lookup/intersection; no rank | observation_query.py 02B | "similarity search" |
| OBS-PUBSRC-01 | Public source check ledger | shipped | SEC-NET-01 | Append-only; not_found ≠ disappearance | public_source_check_service.py | — |
| OBS-RETAIN-01 | Retention policy enforcement | planned | CORE-RESOLVE-01 | Profile-owned retention; enforced at store | — | "retention governed" |
| OBS-REDACT-01 | Redaction and privacy classification | planned | CORE-RESOLVE-01 | Classification on facts; export respects | — | — |
| OBS-ADAPT-01 | Profile-owned source adapters | planned | CORE-RESOLVE-01 | Adapters behind profile policy | ADR-0008 external packs | — |
| OBS-RSP-01 | Repository Source Packs (Gateway-admitted) | partial | AUTH-BRIDGE-01 | External repo observation per ADR-0008; Gateway admission | REPOSITORY_SOURCE_PACKS.md; ADR-0008 | "source packs operational" |

### DET — Detection and candidate production

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| DET-RCD-01 | Repository exact content recurrence | shipped | OBS-CONTENT-01 | ≥2 distinct repo scopes; no independence claim | repository_content_recurrence.py | "independent invention" |
| DET-SCPRES-01 | Scientific source disappearance | shipped | OBS-PUBSRC-01 | Latest not_found + outage defense | scientific_source_disappearance.py | "source is gone" from HTTP 404 alone |
| DET-REG-01 | Closed detector spec registry | shipped | CORE-DETREG-01 | Unknown detector refused | pattern_candidate_specs.py | — |
| DET-PORT-01 | Multi-profile detector portability | shipped | DET-REG-01 | RCD byte-identical; SciPres second branch | LHP-INTEL-04A/04C | — |
| DET-FAMILY-01 | Additional profile-owned detectors | planned | DET-REG-01 | One detector family per packet | ADR-0020 | "all profile detectors exist" |
| DET-EVID-01 | Incomplete evidence refusal | shipped | CORE-QUERY-01 | Truncated scan ⇒ no candidate | pattern_candidate_service.py | — |

### REV — Review, hypothesis, and finding lifecycle

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| REV-CAND-01 | Candidate production surface | shipped | DET-* | detect/list/show; no rank | pattern_candidate_service.py | — |
| REV-DISP-01 | Operator disposition | shipped | REV-CAND-01 | One per version; no approved/verified | pattern_candidate.py | "approved finding" |
| REV-REVIEWED-01 | Reviewed candidate state | partial | REV-DISP-01 | Disposition recorded; no downstream branch | AST-pinned no branch on decision | "workflow complete" |
| REV-HYP-01 | Hypothesis workflow | planned | REV-REVIEWED-01 | Distinct contract; human/Gateway gate | ADR-0020 | "hypothesis exists" |
| REV-FIND-01 | Validated finding lifecycle | planned | REV-HYP-01 | Distinct contract; no auto-promotion | ADR-0021 | "finding exists" |
| REV-GOV-01 | Governed output gate | planned | REV-FIND-01, AUTH-PUB-01 | Publication requires approval | — | "authorized release" |
| REV-NOPROMO-01 | No silent promotion | shipped | CORE-CAND-01 | Detectors cannot emit findings | ADR-0021; AST tests | — |

### PUB — Public-source intelligence

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| PUB-LEDGER-01 | Append-only check ledger | shipped | OBS-PUBSRC-01 | Insert-only; validate on read | db.py public_source tables | — |
| PUB-CHOKE-01 | Hardened HTTPS chokepoint | shipped | SEC-NET-01 | SSRF refused; bounded body | public_source_intake.py | — |
| PUB-SCHED-01 | Scheduled public checks | planned | PUB-LEDGER-01, OPS-SCHED-01 | Profile-owned schedule | — | "automated monitoring" |
| PUB-RATE-01 | Respectful rate limits | planned | PUB-CHOKE-01 | Backoff; host budgets | — | — |
| PUB-EXPORT-01 | Governed public export | planned | REV-GOV-01 | Export approval gate | — | "public export enabled" |

### PRES — Preservation and fixity

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| PRES-DETECT-01 | Disappearance detection | shipped | DET-SCPRES-01 | Counterevidence disclosed | scientific_source_disappearance.py | — |
| PRES-ACTIV-01 | Scientific Preservation activation | planned | LIFE-ACTIVATE-01, PROFILE-SCPRES-01 | Profile activate + admission | seed maturity_note | "SciPres is active" |
| PRES-FIXITY-01 | Fixity manifests | planned | OBS-CONTENT-01 | Manifest over stored bytes | — | "material preserved" from digest alone |
| PRES-RETAIN-01 | Content retention policy | planned | OBS-RETAIN-01 | Profile-owned retention | — | — |
| PRES-QUEUE-01 | Preservation review queues | planned | REV-CAND-01, PRES-DETECT-01 | Operator review surface | — | — |
| PRES-DRILL-01 | Restore and audit drills | planned | LIFE-RESTORE-01 | Rehearsed preservation restore | — | "production restore proven" |

### OSR — Interoperability and evidence export

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| OSR-EXPORT-01 | Observed source reference export | shipped | PUB-LEDGER-01 | One available check → OSR; authority false | observed_source_exporter.py | "consumer interop shipped" |
| OSR-CONSUMER-01 | Cross-consumer interop | proven_nonproduction | OSR-EXPORT-01 | Trial with article-to-skills profile | LHP-INTEL-04D-R1 | "interop production-ready" |
| OSR-AUTH-01 | OSR authority pins | shipped | OSR-EXPORT-01 | All authority const false | observed_source_reference.py | "export grants permission" |

### AUTH — Gateway approval and external authority

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| AUTH-CAP-01 | Activation capability descriptor | shipped | — | activation_available false; not_implemented | activation_capability.py; ADR-0017 | "activation available" |
| AUTH-BRIDGE-01 | Live Gateway bridge | blocked | ADR-0010 | Package exposes private surface | ADR-0010 | "Gateway connected" |
| AUTH-INTENT-01 | Typed action intents | partial | AUTH-CAP-01 | activation_intent.v1; authorizes false | activation_intent.py | "intent authorizes" |
| AUTH-IDENT-01 | Identity-bound authorization | planned | AUTH-BRIDGE-01 | Operator identity authenticated | — | "authenticated operator" from CLI flag |
| AUTH-REPLAY-01 | Replay protection | planned | AUTH-BRIDGE-01 | Nonce/expiry on approvals | — | — |
| AUTH-PUB-01 | Publication approval gate | planned | AUTH-BRIDGE-01, REV-GOV-01 | Nostr/export require Gateway | ADR-0001 | "safe publishing" without gate |
| AUTH-AUDIT-01 | Authorization decision audit trail | planned | SEC-AUDIT-01, AUTH-BRIDGE-01 | Intents, decisions, approvals, refusals, expiration, revocation, replay evidence on audit substrate | — | "authorization is audited" without Gateway bridge |

### OPS — Scheduling, monitoring, audit, and recovery

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| OPS-SCHED-01 | Profile-owned schedules | planned | CORE-RESOLVE-01 | Jobs run under active profile only | — | "scheduled intelligence" |
| OPS-BUDGET-01 | Resource budgets | planned | OPS-SCHED-01 | CPU/network/fs bounds | — | — |
| OPS-HEALTH-01 | Health checks | partial | — | MCP /health; portal liveness | mcp_server.py; portal | "production monitored" |
| OPS-LOG-01 | Structured logs | partial | — | main.py logging setup | main.py | — |
| OPS-METRIC-01 | Metrics | planned | OPS-HEALTH-01 | Observable counters | — | — |
| OPS-AUDIT-01 | Operational audit history | planned | OPS-SCHED-01 | Operator actions recorded | — | — |
| OPS-BACKUP-01 | Backup cadence | partial | LIFE-BACKUP-01 | Documented SOP | OPERATIONS.md | "automated backup" |
| OPS-RECOVER-01 | Crash recovery | planned | LIFE-ACTIVATE-02 | Idempotent jobs; safe restart | ADR-0024 §12 | — |
| OPS-DMS-01 | Dead Man's Switch production path | blocked | ADR-0006 | Threat model + state machine approved; mechanism present | portal/dms_checker.py; ADR-0006 | "DMS production-ready" |

### UX — CLI, API, and operator interface

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| UX-CLI-01 | Complete CLI for contracts | partial | CORE-* | observe/patterns/profiles/backup | main.py CLI | "full CLI" |
| UX-API-01 | Stable local API | partial | UX-CLI-01 | Same contracts as CLI | local_gateway.py; portal | "API complete" |
| UX-PORTAL-01 | Operator dashboard | partial | UX-API-01 | Portal surfaces read paths | portal/app.py | "dashboard complete" |
| UX-PROFILE-01 | Active profile visibility | partial | LIFE-ACTIVATE-01 | active-profile command | main.py | — |
| UX-REVIEW-01 | Evidence/candidate review UI | partial | REV-CAND-01 | CLI review; portal partial | patterns review CLI | "review UI complete" |
| UX-REFUSE-01 | Refusal explanations | planned | UX-CLI-01 | Actionable refusal tokens | — | — |
| UX-EXPORT-01 | Export workflows | partial | OSR-EXPORT-01 | export-osr CLI | main.py | "export workflows complete" |

### DEPLOY — Packaging, upgrades, and production deployment

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| DEPLOY-LOCK-01 | Canonical dependency locks | partial | — | requirements*.lock; make lock | pyproject.toml | "deps locked" without sync |
| DEPLOY-BUILD-01 | Reproducible builds | partial | DEPLOY-LOCK-01 | Docker build smoke | CI docker job | "reproducible" without clean clone |
| DEPLOY-CLONE-01 | Clean-clone tests | partial | REL-UNIT-01 | CI clean checkout | ci.yml | — |
| DEPLOY-MIGRATE-01 | Migration safety | partial | CORE-NORM-01 | Lazy table create; column ensure | db.py | — |
| DEPLOY-S9-01 | StartOS/sovereign packaging | partial | DEPLOY-BUILD-01 | Start9 deploy notes | OPERATIONS.md; the StartOS test server | "StartOS production-ready" |
| DEPLOY-SBOM-01 | SBOM and dependency review | planned | DEPLOY-LOCK-01 | SBOM artifact per release | — | — |
| DEPLOY-CONFIG-01 | `LIGHTHOUSE_DATA_DIR` and path semantics | partial | — | `modules/data_paths.py`; `DB_PATH` derives when unset; empty env refused; divergence check; 03D corrections | config.py; data_paths.py; OPERATIONS.md | StartOS manifest/entrypoint wiring remains external to this repo |

### PROFILE — Profile-specific completion

| ID | Profile | State | Completion gate | Required rehearsal |
|----|---------|-------|-----------------|-------------------|
| PROFILE-RCD-01 | repository_capability_discovery v2 | partial | V1 operator loop + activation ceremony | ops/v1_rcd_host_rehearsal.sh; V1_OPERATOR_WORKFLOW |
| PROFILE-SCPRES-01 | scientific_preservation v1 | planned | Activation + scheduled checks + review queue + OSR | Disposable host; no production claim |
| PROFILE-PSL-01 | public_scientific_lighthouse v1 | planned | Collectors routed by profile; gated publish rehearsal | Profile resolver + targeted collectors |
| PROFILE-AERO-01 | aerospace_defense_monitoring v1 | planned | ADSB+flight+SEC under profile admission | Targeted mode under active profile |
| PROFILE-FUSION-01 | fusion_research_monitoring v1 | planned | arXiv+patent+narrative under profile | Graceful patent degradation documented |

### REL — Verification and release engineering

| ID | Intended capability | State | Dependencies | Completion gate | Verification evidence | Prohibited claims |
|----|---------------------|-------|--------------|-----------------|----------------------|-------------------|
| REL-UNIT-01 | Unit test suite | shipped | — | pytest tests/unit green on clean checkout | ROADMAP_STATUS; v1.0.0 | Test count as maturity proof |
| REL-CLONE-01 | Clean-clone CI gate | shipped | REL-UNIT-01 | CI without populated data/ | ci.yml | — |
| REL-RC-01 | Release candidate gates | planned | REL-CLONE-01 | Checklist before tag | — | "release ready" without review |
| REL-REVIEW-01 | Independent release review | planned | REL-RC-01 | External reviewer sign-off | — | — |
| REL-V2-01 | V2 completion declaration | planned | All PROFILE-* | Full DoD below + RELEASE READY | — | "V2 shipped" |

**Ledger count:** 109 capability IDs across 15 groups (SEC 7, CORE 14, LIFE 11, OBS 10, DET 6, REV 7,
PUB 5, PRES 6, OSR 3, AUTH 7, OPS 9, UX 7, DEPLOY 7, PROFILE 5, REL 5).

**Gateway cross-repo note:** Lighthouse-local MCP approval (`SEC-MCP-01`) is **shipped** and fails closed.
The separate **Gateway** `_is_human_approved` fail-open defect remains a **blocking cross-repo** item
([`PROFILE_SECURITY_RISKS.md`](PROFILE_SECURITY_RISKS.md) risk 2; ADR-0007 Gateway side). This roadmap does
not imply Gateway availability or remediation.

---

## Bundled profile completion table

Derived from [`seed/profiles/`](../seed/profiles/) — every bundled profile listed.

| profile_id | version | seed status | owned sources | owned collectors | owned detectors | evidence / privacy | activation readiness | missing implementation | completion gate | required rehearsal |
|------------|---------|---------------|---------------|------------------|-----------------|------------------|----------------------|------------------------|-----------------|-------------------|
| `repository_capability_discovery` | 2 | ready_for_validation | repository | repository_intake | repository_exact_content_recurrence | metadata_only; local_only; no raw export | partial — RCD v2 only via profile-activate | Profile resolver; switching; production host claim | Observe→detect→review idempotent; backup-gated activation | V1_OPERATOR_WORKFLOW; v1_rcd_host_rehearsal.sh |
| `scientific_preservation` | 1 | draft | press_archives, scientific_publications, operator_document_pack | archive (+ public check path) | scientific_source_disappearance_review | public tier; local_only; disclosure disabled | planned — detector only | Profile activation CLI; scheduled checks; fixity; disclosure packaging; DMS production | Activation + scheduled public checks + review queue + OSR handoff | Disposable ledger + detect; not production |
| `public_scientific_lighthouse` | 1 | ready_for_validation | scientific_publications, press_archives, regulatory_filings, patents, aviation_signals, institutional_pages | sec, arxiv, flight, adsb, narrative, archive, patent | none profile-owned yet | public tier; gated_correlations_only | planned | Profile activation; collector routing from profile; not env/targets.json alone | Collectors + correlation publish gate under profile policy | Gated Nostr rehearsal only |
| `aerospace_defense_monitoring` | 1 | draft | aviation_signals, regulatory_filings | flight, adsb, sec | none | public; min_publish_score 7.0 | planned | Profile activation; narrow proven signal under profile | ADSB+flight+SEC correlations under active profile | Targeted aviation rehearsal |
| `fusion_research_monitoring` | 1 | draft | scientific_publications, patents, press_archives | arxiv, patent, narrative | none | public; patent graceful degradation | planned | Profile activation; patent provider often disabled | arXiv+narrative under profile; patent degradation honest | Seed researchers + narrative optional |

**Patent and Research Tracking:** Named as a co-equal mission in [`ROADMAP.md`](ROADMAP.md) but **no
sixth bundled seed** exists. Patent/research capabilities are partially covered by
`public_scientific_lighthouse`, `fusion_research_monitoring`, and legacy `patent_sentinel` /
`arxiv_sentinel` modules. A dedicated bundled profile or vertical completion is **planned** under Train 5
(workstream §7).

---

## Required workstreams

### 1. V1.0.x hardening (Train 1)

| Capability IDs | Work |
|----------------|------|
| LIFE-EVID-01 | **Shipped** (LHP-LIFE-EVID-01) — typed verify/restore reports bound to manifest file bytes; full field value-type checks and status/detail consistency; substitution refused; preview `artifact_digest` recomputed locally at activate (closed). Residual: restore not re-run at activate |
| LIFE-ACTIVATE-02 | Crash/durability and cross-process concurrency evidence (writer boundary shipped LHP-V1-HARDEN-02) |
| DEPLOY-LOCK-01, DEPLOY-CONFIG-01 | Dependency lock validation; `LIGHTHOUSE_DATA_DIR` / `_data_path` semantics documented |
| SEC-THREAT-01 | Threat-model and security-boundary reconciliation (Lighthouse + tracked Gateway items) |
| SEC-*, LIFE-* | Adversarial activation, backup, restore, path-handling tests |

**Regression gates (shipped — must remain green):** `REL-UNIT-01`, `REL-CLONE-01`, `SEC-MCP-01`
(Lighthouse MCP gate; see Gateway cross-repo note above).

### 2. Complete profile lifecycle (Train 2)

| Capability IDs | Work |
|----------------|------|
| LIFE-* | Validation from bundled/installed contracts |
| LIFE-ACTIVATE-01 | Activation from no-active state (extend beyond RCD when gates met) |
| LIFE-PREVIEW-01 | Active-profile transition preview |
| LIFE-BACKUP-01, LIFE-RESTORE-01 | Backup and restore gates |
| LIFE-SWITCH-01 | Active-to-active switching |
| LIFE-MIGRATE-01 | Schema/data migration |
| LIFE-ROLLBACK-01 | Rollback after failed transition |
| LIFE-ACTIVATE-02, OPS-RECOVER-01 | Crash and concurrency recovery; idempotency |
| LIFE-DEACT-01 | Maintenance/recovery state; deactivation unsupported or explicit (ADR-0024 §15) |
| — | Unsupported downgrade refusal; immutable transition audit history (ADR-0024) |

### 3. Observation and evidence foundation (Trains 2–3)

| Capability IDs | Work |
|----------------|------|
| OBS-REPO-01 through OBS-PUBSRC-01 | Shipped base |
| OBS-RETAIN-01, OBS-REDACT-01 | Retention and privacy classification |
| OBS-ADAPT-01 | Profile-owned source adapters |
| CORE-PROV-01 | Append-only observation history (partial → enforced retention) |
| OBS-CONTENT-01 | Content identity and provenance (shipped) |
| OBS-PUBSRC-01 | Source disappearance/change history via ledger |
| CORE-NORM-01 | Deduplication and idempotency (shipped) |
| DEPLOY-MIGRATE-01 | Schema migration |
| CORE-QUERY-01, OSR-EXPORT-01 | Query and export contracts |

### 4. Intelligence lifecycle (Trains 4–6)

| Gate | Capability IDs |
|------|----------------|
| Candidate | REV-CAND-01, DET-* (shipped) |
| Reviewed candidate | REV-DISP-01 (shipped) |
| Hypothesis | REV-HYP-01, CORE-HYP-01 (planned) |
| Validated finding | REV-FIND-01, CORE-FIND-01 (planned) |
| Governed output | REV-GOV-01, AUTH-PUB-01 (planned) |

Human or Gateway-controlled transitions required above reviewed candidate. **REV-NOPROMO-01** enforced.

### 5. Scientific Preservation (Train 3)

| Capability IDs | Work |
|----------------|------|
| PROFILE-SCPRES-01, PRES-ACTIV-01 | Supported activation |
| OBS-PUBSRC-01, PUB-SCHED-01 | Approved target intake; scheduled checks |
| PUB-RATE-01 | Network policy and rate limits |
| PRES-RETAIN-01 | Content retention |
| PRES-FIXITY-01 | Fixity manifests |
| PRES-DETECT-01 | Source-change and disappearance (detector shipped) |
| PRES-QUEUE-01 | Preservation review queues |
| PRES-DRILL-01 | Restore and audit drills |
| OSR-EXPORT-01 | OSR handoff |
| PRES-RETAIN-01, AUTH-PUB-01 | Authenticity and legal limitations documented; governed publication paths |

### 6. Private Invention Discovery (Train 4)

| Capability IDs | Work |
|----------------|------|
| — | Private local source intake |
| SEC-NET-01 | Strict no-network defaults |
| OBS-REDACT-01 | Encrypted or access-controlled storage; privacy-preserving provenance |
| DET-FAMILY-01 | Recurrence and relationship detection (selective migration) |
| REV-CAND-01, REV-DISP-01 | Candidate review |
| REV-HYP-01 | Hypothesis workflow |
| REV-GOV-01 | Invention-card generation (no prototype composite scores); human-controlled export |
| REV-NOPROMO-01 | Prior-art and novelty boundaries; no automatic patentability, ownership, or legal conclusions |

Prototype: design evidence only ([ADR-0005](adr/0005-invention-engine-belongs-in-lighthouse-migration-deferred.md)).

### 7. Patent and research tracking (Train 5)

| Capability IDs | Work |
|----------------|------|
| CORE-ALERT-01 | Patent/research alert surfaces via legacy sentinels |
| PROFILE-PSL-01, PROFILE-FUSION-01 | Profile-owned collector routing when activation ships |
| DET-FAMILY-01 | Duplicate/related-work candidate detectors (future) |
| REV-CAND-01, REV-DISP-01 | Review queues for patent/research candidates |
| OBS-RETAIN-01 | Retention and licensing policy |

No bundled seed — workstream also covers: approved patent/research sources; versioned queries and
watchlists; citation and provenance; change detection; claim-level evidence;
**no unreviewed novelty or infringement conclusions**.

Maps to: `patent_sentinel`, `arxiv_sentinel`, and the profile IDs above.

### 8. Every bundled profile

See [Bundled profile completion table](#bundled-profile-completion-table) above. No profile omitted.

### 9. Gateway and governed outputs (Train 6)

| Capability IDs | Work |
|----------------|------|
| AUTH-CAP-01 | Truthful capability discovery (descriptor shipped) |
| AUTH-INTENT-01 | Typed action intents |
| AUTH-BRIDGE-01 | Gateway bridge when ADR-0010 unblocked |
| AUTH-IDENT-01, AUTH-REPLAY-01 | Identity, expiration, revocation, replay protection |
| AUTH-PUB-01 | Publication/export approval |
| SEC-AUDIT-01, AUTH-AUDIT-01 | Audit substrate then authorization audit trail |
| — | Refusal when Gateway absent; separation of evidence truth vs permission to act |

### 10. Scheduling and operations (Train 7)

| Capability IDs | Work |
|----------------|------|
| OPS-SCHED-01, OPS-BUDGET-01 | Profile-owned schedules and budgets |
| OPS-RECOVER-01 | Retries, backoff, crash recovery |
| SEC-PATH-01, SEC-NET-01 | Filesystem and network boundaries |
| OPS-HEALTH-01, OPS-LOG-01, OPS-METRIC-01 | Health, logs, metrics |
| OPS-AUDIT-01 | Audit history |
| OPS-BACKUP-01, PRES-DRILL-01 | Backup cadence and restore rehearsal |
| OPS-HEALTH-01, OPS-RECOVER-01 | Operator alerts; offline operation |

### 11. Operator experience (Train 7)

| Capability IDs | Work |
|----------------|------|
| UX-CLI-01 through UX-EXPORT-01 | Complete CLI, API, portal |
| UX-PROFILE-01 | Active-profile visibility |
| UX-REVIEW-01 | Evidence and candidate review |
| LIFE-PREVIEW-01 | Transition previews in UI |
| LIFE-BACKUP-01 | Backup/restore status |
| OPS-SCHED-01 | Schedule management |
| UX-REFUSE-01 | Authority and refusal explanations |

Interface must not gain authority absent from underlying contracts.

### 12. Deployment and release engineering (Train 8)

| Capability IDs | Work |
|----------------|------|
| DEPLOY-LOCK-01 through DEPLOY-SBOM-01 | Locks, builds, clean-clone, migrations, SBOM |
| DEPLOY-S9-01 | StartOS / sovereign-host packaging |
| LIFE-ROLLBACK-01 | Upgrade and rollback rehearsals |
| LIFE-BACKUP-01 | Persistent-data protection |
| REL-RC-01, REL-REVIEW-01 | Release-candidate and production-readiness review |

---

## Ordered release trains

Version labels after v1.0.x are **provisional**. **No calendar dates.**

### Train 1 — V1.0.x Foundation hardening

| Field | Content |
|-------|---------|
| Entry | v1.0.0 tagged at `3419c769` |
| Capability IDs | SEC-RESTORE-01, SEC-THREAT-01, LIFE-ACTIVATE-02, LIFE-EVID-01, DEPLOY-LOCK-01, DEPLOY-CONFIG-01 |
| Regression gates | `REL-UNIT-01`, `REL-CLONE-01`, `SEC-MCP-01` (shipped; must stay green) |
| Exclusions | Profile switching; Gateway bridge remediation; SciPres activation; invention migration |
| Exit gate | Hardening packets merged; adversarial path/activation tests green; SECURITY.md and ROADMAP_STATUS calibrated |
| Required tests | Unit suite on clean checkout (`REL-CLONE-01`); restore-report Boolean tests; MCP gate regression (`SEC-MCP-01`) |
| Required rehearsals | None production — disposable backup/activation only |
| Documentation | ROADMAP_STATUS security rows; SECURITY.md MCP status |
| Rollback | Revert hardening commits; v1.0.0 tag remains valid baseline |

### Train 2 — Profile lifecycle foundation

| Field | Content |
|-------|---------|
| Entry | Train 1 exit |
| Capability IDs | LIFE-SWITCH-01, LIFE-MIGRATE-01, LIFE-ROLLBACK-01, CORE-RESOLVE-01, LIFE-DEACT-01 (document) |
| Exclusions | Full SciPres vertical; invention migration; Gateway authority |
| Exit gate | Safe `no_active → active → active` switch rehearsed on disposable store; exactly one active profile after success |
| Required tests | Switch idempotency; crash recovery; downgrade refusal |
| Required rehearsals | Backup-gated switch with restore drill evidence |
| Documentation | BACKUP_READINESS_AND_PROFILE_SWITCHING.md; [ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md) |
| Rollback | Failed switch restores prior active profile or blocks with honest error |

### Train 3 — Scientific Preservation complete vertical

| Field | Content |
|-------|---------|
| Entry | Train 2 exit; OBS-PUBSRC-01 and DET-SCPRES-01 shipped |
| Capability IDs | PROFILE-SCPRES-01, PRES-ACTIV-01, PRES-FIXITY-01, PRES-RETAIN-01, PRES-QUEUE-01, PRES-DRILL-01, PUB-SCHED-01, PUB-RATE-01 |
| Exclusions | Private invention migration; Gateway-governed publication |
| Exit gate | SciPres activation + scheduled public checks + disappearance review queue + OSR handoff on clean host |
| Required tests | SciPres activation path; scheduled check dry-run; detector under active profile |
| Required rehearsals | Disposable host ledger + detect + review; not production data |
| Documentation | SCIENTIFIC_PRESERVATION_CANDIDATES.md; profile seed maturity_note |
| Rollback | Deactivate SciPres; ledger append-only preserved |

### Train 4 — Private Invention Discovery complete vertical

| Field | Content |
|-------|---------|
| Entry | Train 2 exit; OBS-CONTENT-01, REV-CAND-01 shipped |
| Capability IDs | DET-FAMILY-01 (one family), REV-HYP-01 (optional partial), OBS-REDACT-01 |
| Exclusions | Bulk prototype copy; composite scores; automatic legal conclusions |
| Exit gate | One detector family migrated; no-network default proven; human export only |
| Required tests | Network boundary AST; candidate ≠ finding; no prototype composite scores in substrate |
| Required rehearsals | Local-only observe/detect/review on private fixtures |
| Documentation | ADR-0005 migration notes; PATTERN_CANDIDATES.md |
| Rollback | Remove migrated detector; profile remains inactive |

### Train 5 — Patent/research tracking and remaining bundled profiles

| Field | Content |
|-------|---------|
| Entry | Trains 2–4 patterns established |
| Capability IDs | PROFILE-PSL-01, PROFILE-AERO-01, PROFILE-FUSION-01; patent/research workstream |
| Exclusions | Gateway authority; DMS production |
| Exit gate | Each remaining seed profile meets its completion gate in profile table |
| Required tests | Per-profile acceptance trials |
| Required rehearsals | Per-profile disposable rehearsals |
| Documentation | INTELLIGENCE_PROFILE_ARCHITECTURE.md; per-profile notes |
| Rollback | Per-profile deactivation or switch back |

### Train 6 — Gateway-governed outputs and external actions

| Field | Content |
|-------|---------|
| Entry | ADR-0010 unblocked; Trains 3–5 materially complete |
| Capability IDs | AUTH-BRIDGE-01, AUTH-IDENT-01, AUTH-REPLAY-01, AUTH-PUB-01, AUTH-AUDIT-01, REV-GOV-01, PUB-EXPORT-01 |
| Exclusions | Silent publication; autonomous external actions |
| Exit gate | Gateway-authorized operations fail closed when Gateway absent; append-only audit |
| Required tests | Fail-closed without Gateway; approval replay tests |
| Required rehearsals | Gateway integration staging |
| Documentation | GATEWAY_LIGHTHOUSE_PROFILE_BOUNDARY.md |
| Rollback | Disable bridge; local-only operation |

### Train 7 — Scheduling, API, and operator interface

| Field | Content |
|-------|---------|
| Entry | Trains 3–6 core paths available |
| Capability IDs | OPS-SCHED-01 through OPS-RECOVER-01; UX-* |
| Exclusions | DMS production (ADR-0006) |
| Exit gate | Profile-owned schedules; API contract parity with CLI; refusal explanations |
| Required tests | Schedule idempotency; API contract tests |
| Required rehearsals | Operator workflow end-to-end on disposable host |
| Documentation | OPERATIONS.md; site operator pages |
| Rollback | Disable scheduler; CLI remains authoritative |

### Train 8 — Sovereign production deployment

| Field | Content |
|-------|---------|
| Entry | Trains 1–7 complete |
| Capability IDs | DEPLOY-S9-01, DEPLOY-SBOM-01, OPS-BACKUP-01, PRES-DRILL-01 |
| Exclusions | Aspirational packaging without rehearsal evidence |
| Exit gate | Supported host passes install, upgrade, backup, restore, rollback rehearsals |
| Required tests | Clean-clone; docker smoke; Start9 update drill |
| Required rehearsals | Full backup/restore/rollback on supported sovereign host |
| Documentation | OPERATIONS.md; Start9 manifest |
| Rollback | Documented rollback to prior package + data restore |

### Train 9 — Full-capability V2 release

| Field | Content |
|-------|---------|
| Entry | All trains 1–8 exit gates met |
| Capability IDs | All groups; REL-V2-01 |
| Exclusions | None — this is the completion train |
| Exit gate | [V2 Definition of Done](#full-capability-v2-definition-of-done) + independent `RELEASE READY` |
| Required tests | Full clean-clone suite; profile-specific acceptance |
| Required rehearsals | Production-readiness review |
| Documentation | ROADMAP_STATUS reflects shipped truth; this doc updated for any deferred items |
| Rollback | Major version rollback procedure documented |

---

## Full-capability V2 definition of done

V2 may be declared complete (**REL-V2-01**) only when:

| Criterion | Capability / evidence |
|-----------|----------------------|
| Every bundled profile meets its completion gate | PROFILE-* rows |
| Safe profile switching shipped | LIFE-SWITCH-01 rehearsed |
| Observation → governed-output lifecycle complete | OBS-* through REV-GOV-01 |
| Scientific Preservation passes clean-host rehearsal | PROFILE-SCPRES-01, Train 3 exit |
| Private Invention Discovery passes clean-host rehearsal | Train 4 exit |
| Gateway-authorized operations fail closed | AUTH-BRIDGE-01, SEC-FAILCLOSED-01 |
| Scheduling and recovery production-capable | OPS-SCHED-01, OPS-RECOVER-01 |
| Supported deployment passes install/upgrade/backup/restore/rollback | Train 8 exit |
| Security findings at or above release threshold closed | SEC-THREAT-01, REL-RC-01 |
| Clean-clone suites and profile acceptance trials pass | REL-CLONE-01, PROFILE-* |
| Documentation and static roadmap match shipped state | ROADMAP_STATUS, site/roadmap.html |
| No aspirational capability represented as available | Claim-calibration audit |
| Independent release review returns `RELEASE READY` | REL-REVIEW-01 |

---

## Future packet discipline

Every future implementation packet must:

1. Cite one or more **roadmap capability IDs** from this document.
2. State the exact **current-to-target status change** (e.g. `partial → shipped`).
3. Identify the **owning profile** or **profile-neutral** layer.
4. List **exact paths** to be created or modified.
5. Define **tests and rehearsal gates** before implementation.
6. Preserve **authority and privacy boundaries** (ADRs, profile policies).
7. Update [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) **only after evidence exists**.
8. **Stop before commit, activation, release, or push** unless explicitly authorized.

**Forbidden without gate satisfaction:** "complete," "production-ready," "finding," "authorized,"
"verified disaster recovery," "tamper-proof," "autonomous," and similar claims that exceed evidence.

See also [`PACKETS.md`](PACKETS.md) future packet requirements.

---

## Immediate packet queue

Separate reviewable packets — do not combine:

| # | Proposed packet | Capability IDs | Scope |
|---|-----------------|----------------|-------|
| 1 | LHP-SCPRES-GAP-01 | PROFILE-SCPRES-01, PRES-ACTIV-01 | Scientific Preservation activation-readiness gap analysis (parallel; SciPres activation remains blocked) |
| 2 | LHP-LIFE-RESOLVE-01 | CORE-RESOLVE-01 | Runtime policy resolver + resolved-policy digest (first lifecycle implementation packet; ADR-0024) |

**Completed (docs only):** `LHP-LIFE-DESIGN-01` — controlled profile-switch lifecycle design
([ADR-0024](adr/0024-controlled-profile-switch-lifecycle.md); no switch implementation).
`LHP-LIFE-DESIGN-01-C2` — adversarial lifecycle corrections (ADR-0024 + reconciliation; no
implementation).

**Completed (CLI truth only):** `LHP-LIFE-TRUTH-01` — commit `8399764`; three stale profile-lifecycle
CLI claims corrected; initial activation accurately limited to RCD v2 through `profile-activate`;
switching and deactivation remain unimplemented; transition-preview current-profile input is
operator-supplied and not store-derived; two new behavioral tests; clean-checkout **2422 passed,
0 failed, 0 skipped**. Does **not** change `UX-CLI-01` / `CORE-RESOLVE-01` capability state; no
lifecycle resolution, switching, fencing, migration, rollback, deactivation, or Gateway
authorization was added.

**Ordered lifecycle implementation sequence** (`LHP-SCPRES-GAP-01` may run in parallel as analysis
only):

1. `LHP-LIFE-RESOLVE-01` — runtime policy resolver (`CORE-RESOLVE-01`)
2. `LHP-LIFE-AUTHPROJ-01` — `profile_switch_authority_projection.v1` + planned switch `activation_source` values
3. `LHP-LIFE-JOURNAL-01` — switch-attempt journal + `profile_transition_control` singleton (same DB)
4. `LHP-LIFE-FENCE-01` — complete admission inventory; generation bump at every boundary; per-transaction revalidation
5. `LHP-LIFE-MIGPLAN-01` — migration-plan contract (`LIFE-MIGRATE-01`) — **dormant** until reachable
6. `LHP-LIFE-VALIDATE-01` — profile-owned behavioral validator + negative probes
7. `LHP-LIFE-ROLLBACK-01` — rollback and `recovery_required` primitives (`LIFE-ROLLBACK-01`, `OPS-RECOVER-01`)
8. `LHP-LIFE-CUTOVER-01` — cutover orchestrator composing validation + rollback (no earlier packet may expose cutover)
9. `LHP-LIFE-CLI-01` — operator CLI/API (`UX-CLI-01`, `UX-REFUSE-01`)
10. `LHP-LIFE-GATEWAY-01` — Gateway bridge for **widening-only** switches (`AUTH-BRIDGE-01`; blocked on ADR-0010; never authorizes `indeterminate`)

Internal components remain unreachable from production/operator surfaces until the safe orchestrator
and applicable authority path exist.

---

## Related documents

- Shipped state: [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md)
- Phase model (A–K): [`ROADMAP.md`](ROADMAP.md)
- V1 claim: [`V1_OPERATOR_WORKFLOW.md`](V1_OPERATOR_WORKFLOW.md)
- Security: [`SECURITY.md`](SECURITY.md)
- Navigation: [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md)
