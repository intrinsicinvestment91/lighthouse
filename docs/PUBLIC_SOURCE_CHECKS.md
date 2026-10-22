# Public source checks (LHP-INTEL-04B)

`lighthouse public-sources` records **one neutral temporal fact**: *"Lighthouse checked this admitted
public locator at this time, and this direct outcome occurred."* It does not decide what that history
means — that is a later, separate layer's job (LHP-INTEL-04C).

## Why this layer exists

The intelligence engine can observe repositories and documents, normalize them into facts, identify
exact bytes, and run one declared rule into a reviewable candidate. It **cannot** currently record that
it *looked* at something and what happened. Three reasons, all deliberate:

- `observed_at` is excluded from every content digest, so re-observing an unchanged source is a silent
  no-op — the store is idempotent by content identity.
- A failed retrieval writes nothing at all.
- No collector persists a digest of fetched bytes or an HTTP status.

So the absence of a new row is ambiguous across three cases a disappearance detector must tell apart:

| The store says | It could mean |
|---|---|
| no new row | the source is **unchanged** |
| no new row | the source is **unavailable / gone** |
| no new row | the source was **never checked** |

The append-only check ledger separates them: every completed attempt — success, failure, repeat,
recovery — is retained as a distinct, timestamped row.

## Why one hardened chokepoint, and why network stays forbidden elsewhere

Every module in the fact-first pipeline is, by standing doctrine and AST-pinned tests, forbidden to
touch the network. Rather than break that, 04B refines it to **"no network except the one audited
adapter"**. `modules/public_source_intake.py` is the only module permitted a network-capable import;
`tests/unit/test_public_source_network_boundary.py` proves it, and would fail if `import httpx` (or
`socket`, or `http.client`) appeared in any other fact-first module. This is the HTTP analogue of
`repository_intake`'s single hardened git chokepoint.

## How host authorization works (and why a targets file cannot grant it)

The trust boundary is `LIGHTHOUSE_PUBLIC_SOURCE_HOSTS` (via `config.PublicSourceConfig.allowed_hosts`) —
a comma-separated list of **exact** hostnames. It is **empty by default, and empty fails closed**: with
no hosts configured, `check` refuses before any network. Matching is exact — a subdomain of an allowed
host is *not* an allowed host, and `evil-example.org` does not match `example.org`.

`data/public_source_targets.json` is a **work list, not a boundary.** It may only narrow what is
checked. It has no field in which a host could be written, and unknown keys are refused — so a targets
file cannot authorize itself, the way `{"allowed_hosts": ["evil"], "targets": [...]}` would if the
schema allowed it.

```json
{
  "schema_version": "public_source_targets.v1",
  "targets": [
    { "locator": "https://archive.example.org/dataset/2024.csv" }
  ]
}
```

A locator carries no credentials, cookies, headers, method, or importance — those fields do not exist.
Duplicate canonical locators fail closed.

## Identity: source vs locator vs content

Three separate identities, all kept:

- **`public-source:<sha256(canonical_locator)>`** — the source. In v1 one admitted locator is one source.
- **`public-locator:<sha256(canonical_locator)>`** — the locator. Same digest, distinct semantic field.
- **`content:<sha256(exact response bytes)>`** — the bytes. The **same** recipe as repository/document
  content identity (03A), so a public artifact, a repository file, and a local document with identical
  bytes share one `content:` value. This is a *fact join*, not publication authorization.

A check's own identity (`psc_<sha256>`) covers `checked_at` — **time is part of the fact**, the
deliberate opposite of the observation store. A run (`psr_<sha256>`) carries a `run_nonce`, so two runs
of an identical target set at the same instant remain distinct.

## Exactly what bytes are hashed

The adapter sends `Accept-Encoding: identity` and accepts only an absent or `identity` `Content-Encoding`
(anything else → `unsupported_content_encoding`, no digest). The digest is `SHA-256` of the **exact
entity-body bytes** delivered — no transparent decompression, no text decode, no line-ending or
HTML/JSON normalization. LF and CRLF are genuinely different bytes and therefore different identities. A
204 (or any empty body) hashes the empty string with `body_size_bytes: 0`.

## Why repeated identical checks are retained

The ledger is **never deduplicated**. A repeated 200, a repeated timeout, a recovery from 404 back to
200 — each is its own row. Cadence *is* the evidence: "checked daily for a month, then 404" is a
different fact from "checked once and it 404'd", and only an append-only history can tell them apart.

## Why `not_found` is not disappearance (and neither is anything else)

Every outcome names one direct fact and nothing more:

| Outcome | Means | Does **not** mean |
|---|---|---|
| `not_found` | HTTP 404 or 410 | the source is gone / removed / suppressed |
| `timeout` | a read/connect deadline was reached | the source is unavailable |
| `dns_failure` | resolution failed | the source disappeared |
| `tls_failure` | verified TLS failed | the source was taken down |
| `authentication_required` | 401 / 403 | the source is gone |
| `rate_limited` | 429 | the source is gone |
| `server_error` | 5xx | the source is gone |
| `policy_refused` | our own allowlist/SSRF policy refused | anything about the source at all |
| `unknown` | could not be classified (confidence 0.0) | anything |

`confidence` is **epistemic** — *did I determine this outcome?* — never a probability that a source
disappeared.

## Why no response body is stored

04B keeps the digest, the byte count, the status, the locator, redirect facts, provenance, and
timestamps — **not the body.** This is a ledger, not a web archive. A future preservation-storage packet
must separately decide copyright, terms of service, retention, storage bounds, encryption, publication
eligibility, and disclosure packages. A successful content digest may be *compared* to locally observed
content identities (a fact join); that comparison is not permission to publish.

## How 04C uses these facts (shipped)

**LHP-INTEL-04C** adds a `scientific_preservation`-owned rule, `disappearing_scientific_source`, over
`content:<sha>`. Its conservative trigger, from present facts only: a locator was admitted, at least
one earlier check recorded a successful `content:<sha>`, the most recent completed check is the narrow
404/410 not-found, at least two checks exist, at least seven days separate the success and the
not-found, the run was not part of a broad outage, and every record validates. It **never** triggers
from timeout, DNS, TLS, auth, rate-limit, policy, oversized, 5xx, redirect-alone, or a source never
successfully observed. A later recovery yields no current candidate — the prior version and its
disposition stay byte-identical. Run with `lighthouse patterns detect --detector
scientific_source_disappearance_review` (reads the ledger only; **no network**).

## Observed-source reference export (LHP-INTEL-04D-R1 / 04D-R2)

Lighthouse can export one immutable `observed_source_reference.v0` from a single validated
`available` check. The export carries locator metadata, content identity, and check provenance — **not
the body** and **no authority** (every authorization pin is const `false`; `operator_handoff_required`
is const `true`).

**What an OSR proves:** a validated reference to one `available` public-source observation (locator,
content identity, and check/run provenance), addressed to one closed consumer profile.

**What an OSR does not prove:** authenticity, scientific correctness, legal retention or redistribution
rights, retention authorization, skill derivation, candidate approval, registry or external publication,
installation, execution, disclosure, or Gateway approval.

**Authoritative schema (vendored byte-identical mirror; not yet production-canonical):**

| Pin | Value |
|---|---|
| Source path | `offline-sovereign-stack/services/retained_source/schemas/observed_source_reference.v0.schema.json` |
| Authoritative commit | `53b9761` |
| SHA-256 | `ca9211fd65f79cb7ba19fd3cfbe829ad1382c4afd03ed1351f4242ecc209cc0e` |
| Local mirror | `docs/schemas/observed_source_reference.v0.schema.json` |

**Identity exclude set** (must match OSS/ACL consumer validators): `reference_id`, `artifact_digest`,
`producer_repository`, `producer_commit`. `artifact_digest = sha256(canonical_json(identity-covered
content))`; `reference_id = 'osr_' + artifact_digest`. Producer repository/commit stamps are best-effort
provenance only — changing them must not change identity.

**Status:** producer-side export and local consumer-compatibility checks are implemented in this repo.
Cross-repository interoperability with live ACL/OSS consumers remains a trial — do not treat this as a
shipped production interop surface.

Two closed consumer profiles are supported:

| `intended_consumer` | `intended_use` |
|---|---|
| `article-to-skills` | `exact_source_byte_preflight_before_skill_derivation` |
| `offline-sovereign-stack` | `exact_source_byte_preflight_for_local_retention` |

```bash
lighthouse public-sources export-osr --check-id psc_<sha256> \
  --consumer article-to-skills --json
lighthouse public-sources export-osr --check-id psc_<sha256> \
  --consumer offline-sovereign-stack --json
```

Only `outcome=available` checks qualify. Downstream validators (ACL Article-to-Skills, OSS retained
storage) each refuse the other profile before any side effect; that consumer-side refusal is outside
this producer packet.

## Gateway and dead-man's switch stay separate

04B contacts no Gateway and implements no dead-man's-switch behavior. External release, disclosure
approval, and DMS enrollment remain future authority boundaries the Gateway owns. Failure or uncertainty
must always delay release, never accelerate it.

## Operator usage

```bash
export LIGHTHOUSE_PUBLIC_SOURCE_HOSTS=archive.example.org,data.example.edu   # empty => refuse
lighthouse public-sources check --targets-file /path/to/public_source_targets.json --dry-run  # writes nothing
lighthouse public-sources check --targets-file /path/to/public_source_targets.json            # persist
lighthouse public-sources runs                          # completed runs, newest first
lighthouse public-sources runs --id psr_<...>           # one run + its checks
lighthouse public-sources history --source-id public-source:<sha256>       # a source's timeline
lighthouse public-sources history --locator https://archive.example.org/x  # by locator
lighthouse public-sources history --content-digest <sha256>                # every check that saw these bytes
lighthouse public-sources show --id psc_<...>           # one check in full
```

A dry run performs the checks (network *does* happen) but writes nothing — not a row, not a table, not
the database file. An empty allowlist, or a malformed targets file, refuses before any network and
creates no database. Reads on an empty store answer `{"count": 0}`, exit 0, and create nothing.
