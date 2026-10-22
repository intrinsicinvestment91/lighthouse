# ADR-0022 — Public source retrieval is isolated to a hardened fact adapter

- **Status:** Accepted
- **Date:** 2026-07-14
- **Packet:** LHP-INTEL-04B
- **Supersedes:** —
- **Superseded-by:** —

## Context

The LHP-INTEL-04A portability review (accepted) found that Scientific Preservation is blocked not by
`pattern_candidate.v1` but by a missing **fact**: Lighthouse cannot record that it *looked* at a public
source. `observed_at` is excluded from every content digest (`repository_intake`,
`observation_normalizer`), so re-observing an unchanged source is idempotent and writes nothing; a
failed retrieval writes nothing at all; and not one of the seven legacy collectors persists a
fetched-byte digest or an HTTP status. So the absence of a new row is ambiguous across *unchanged*,
*gone*, and *never checked* — the three cases a disappearance detector must separate.

Answering *"did Lighthouse check this locator, when, and what directly happened?"* requires the network
— which every module in the fact-first pipeline is, by standing doctrine and AST-pinned tests,
forbidden to touch. This ADR records how that doctrine is refined rather than broken, and the
boundaries that keep a network fact layer from becoming a web crawler, an archive, or a detector.

## Decision

**1. One hardened network chokepoint.** `modules/public_source_intake.py` is the only module in the
fact-first pipeline permitted to import a network-capable library. It is the HTTP analogue of
`repository_intake`'s single hardened git chokepoint.

**2. Network stays forbidden everywhere else.** The doctrine becomes "no network *except* the audited
adapter". `tests/unit/test_public_source_network_boundary.py` pins the fact-first module set explicitly
(so a legacy collector's `httpx` cannot make it fail) and asserts none of them import
`socket`/`ssl`/`http.client`/`httpx`/… while the adapter — and only it — does.

**3. The host allowlist is authoritative and empty by default.** `LIGHTHOUSE_PUBLIC_SOURCE_HOSTS`
(`config.PublicSourceConfig`) is the outer trust boundary. Empty means *check nothing*, and a check
**refuses before any network** — mirroring `ObservationConfig.allowed_roots`.

**4. A targets file cannot authorize a host.** `public_source_targets.v1` is a work list. It may only
*narrow* what is checked; it has no field in which a host could be written, and unknown keys are
refused. Self-authorization is unrepresentable, not merely rejected.

**5. HTTPS-only v1.** GET, default port 443, TLS certificate + hostname verification required. HTTP,
other schemes, non-default ports, URL credentials, IP-literal hosts, and over-length URLs are refused.

**6. SSRF and DNS-rebinding are defended structurally.** The adapter resolves the host, validates every
returned address (loopback/private/link-local/multicast/reserved/CGNAT/IPv4-mapped-private/metadata all
refused; a *mixed* answer refuses the whole host), and connects only to a validated address while
presenting the real hostname to TLS. A guarded `http.client.HTTPSConnection.connect()` dials the exact
validated IP, so a name cannot be rebound to an internal host after validation; the actual peer is
re-checked as defense in depth. Every redirect hop repeats the full check; HTTPS→HTTP downgrade,
credential locators, unlisted hosts, and loops are refused. Ambient proxies, cookies, and authorization
are never sent.

**7. Checks are append-only.** Two tables (`public_source_check_runs`, `public_source_checks`) via
`modules/db.py`, insert-only (no `UPDATE`, no `DELETE`, no mutating helper). A run and all its checks
persist in one atomic transaction; the tables are created *inside* it, so a dry run or a refusal leaves
no database.

**8. Time is identity-bearing for a source check.** Unlike the observation store, `checked_at` and the
run timestamps are digest-covered, and a run carries a `run_nonce`. This is the deliberate opposite of
observation idempotency.

**9. Repeated identical outcomes are retained.** The ledger is never deduplicated — a repeated 200, a
repeated failure, a recovery are each a distinct row, because **cadence is the evidence**.

**10. Successful content uses the raw-byte SHA-256 recipe.** `content_digest = SHA-256(exact admitted
response bytes)`, the same recipe as `content_identity` (03A) — so a public artifact, a repository file,
and a local document with identical bytes share one `content:<sha256>` value.

**11. Compressed and decompressed bytes are not silently equated.** The adapter sends
`Accept-Encoding: identity` and refuses any other content encoding (`unsupported_content_encoding`, no
digest), so a digest is always over the exact bytes served — never a transparently-decompressed body.

**12. Response bodies are not stored in v1.** The ledger keeps metadata and the digest, not the body.
04B is not a web archive; a preservation-storage packet must separately address copyright, retention,
encryption, and disclosure eligibility.

**13. Outcomes remain factual.** A closed vocabulary (`available`, `not_found`, `timeout`,
`tls_failure`, `policy_refused`, …) with no member for *disappeared*, *suppressed*, *important*, or
*significant*. Confidence is epistemic (*did I determine this outcome?*), never a disappearance
probability.

**14. `not_found` is not disappearance.** It is exactly HTTP 404 or 410 — an answer, not a verdict. A
timeout, DNS, TLS, auth, rate-limit, policy, or adapter failure each proves only itself.

**15. No candidate exists in 04B.** The layer creates no `PatternCandidate`, and `pattern_candidate.py`
is untouched. Every check pins `creates_candidate`/`is_finding`/`authorizes_*`/`contacts_gateway`/
`uses_llm` to a literal `False`.

**16. 04C owns interpretation.** A later, separate `scientific_preservation`-owned rule
(`disappearing_scientific_source`) will decide whether this history satisfies one review rule. It will
require the bounded PatternCandidate generalization identified in 04A (detector-owned vocabularies).

**17. The Gateway owns consequential external release.** 04B contacts no Gateway; no
`lighthouse.publish`/`release`/`disclose` capability id exists.

**18. The dead-man's switch remains future and package-specific.** 04B implements none of it; failure or
uncertainty must delay release, never accelerate it.

## Consequences

Lighthouse can now answer *"did I check this, when, and what happened?"* deterministically, and retain
the history a disappearance rule will need — without weakening the no-network doctrine (it is now
precise rather than absolute) and without shipping a crawler, an archive, a scheduler, a score, or a
candidate. The stdlib guarded-connection choice (over `httpx`) keeps the DNS-rebinding claim truthful
and adds no dependency.

## Repository evidence

- `modules/public_source_intake.py` — `classify_address`, `_approved_addresses`,
  `_GuardedHTTPSConnection.connect`, `check_locator`, `_stream_and_hash` (bounded reads).
- `modules/public_source_check.py` — `OUTCOMES`, `canonical_locator`, `compute_run_id`,
  `PublicSourceCheck`/`PublicSourceCheckRun` `validate()`, `CHECK_AUTHORITY_PINS`.
- `modules/public_source_check_service.py` — `run_checks` (empty-allowlist refusal, dry-run, atomic
  persist, validate-on-read).
- `modules/db.py` — `_create_public_source_tables`, `store_check_run` (atomic, insert-only),
  `public_source_tables_exist`.
- `config.py` — `PublicSourceConfig`, `_parse_public_source_hosts`, `get_public_source_cfg`.
- `tests/unit/test_public_source_network_boundary.py` — the network-isolation pin.
- `docs/PUBLIC_SOURCE_CHECKS.md` — the operator/architecture guide.
