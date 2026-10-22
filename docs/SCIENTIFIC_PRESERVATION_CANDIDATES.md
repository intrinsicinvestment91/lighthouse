# Scientific preservation candidates (LHP-INTEL-04C)

`lighthouse patterns detect --detector scientific_source_disappearance_review` produces **one
conservative review object** per admitted public locator:

> A previously available admitted public source's most recent completed check returned a narrow
> 404/410 not-found, at least seven days after the last time it was seen, and not amid a broad
> outage. An operator should look.

It says no more than that. It is the second citizen of `pattern_candidate.v1` — the proof that the
candidate architecture is not repository-specific — and it is owned by the **Scientific Preservation**
profile, not the invention profile.

## Facts are one layer; a candidate is a separate interpretation

04B records **facts**: "I checked this locator at this time and this direct outcome occurred", in an
append-only ledger (see `PUBLIC_SOURCE_CHECKS.md`). 04C is the first layer to **interpret** those facts
— through one narrow deterministic rule. The two are deliberately separate packets so it is provable,
afterwards, whether a behaviour came from observation (04B) or interpretation (04C).

## Why `not_found` is not disappearance

A `not_found` is exactly HTTP 404 or 410 — an answer, not a verdict. A source may 404 because it moved,
was reconfigured, changed a path, or was briefly misconfigured. The candidate never claims the source
*disappeared*, was *removed*, was *suppressed*, or *matters scientifically*; those claims are
**unrepresentable** — there is no field for them, and the mandatory unknowns and limitations disclaim
each one explicitly.

## The conservative trigger (all required)

For one locator, from present facts only:

1. It is an admitted public source (`source_class = public_source`, `privacy_class = public`).
2. Its **most recent completed check** is `not_found` with HTTP status 404 or 410.
3. A **strictly earlier** check was `available` and carried a valid `content:<sha256>`.
4. At least two checks exist.
5. At least **seven days** separate that success and the not-found.
6. The triggering run was **not a broad outage**.
7. Every record validates on read.

### Why the *most recent* prior success

The subject is the exact bytes last served — so if a locator served `content:A` on day 1, `content:B`
on day 20, and 404'd on day 30, the candidate's subject is `content:B` (day 20 is the last time it was
seen), and the seven-day interval is measured from day 20, not day 1. Choosing the oldest success would
manufacture an interval that did not happen.

### Why seven days

A declared v1 threshold — a conservative choice to avoid flagging a source that is briefly unreachable,
not a claim that seven days means anything scientific. It is a rule parameter, adjustable with evidence.

## The broad-outage defense (deterministic, non-scoring)

If the *triggering run* checked other locators and most of them were **infrastructure failures**
(`dns_failure`, `tls_failure`, `connection_failure`, `timeout`, `adapter_failure`, `unknown`,
`run_byte_budget_exceeded`, `incomplete_response`), the 404 is not trustworthy evidence of
disappearance — it is a symptom of the outage. The rule: over the unrelated locators (the triggering one
excluded, deduped by locator), if at least three exist and at least two-thirds are infrastructure
failures (`infra*3 >= unrelated*2`, exact integers), the candidate is **blocked**. A `not_found` at
another locator is *not* an infrastructure failure — three locators all 404-ing is three more
disappearances, not an outage. Where fewer than three unrelated checks exist, the context is **limited**;
that is *disclosed* as an unknown, not hidden.

## Counterevidence — direct facts, never auto-suppression

A candidate carries the exact-byte copies Lighthouse already knows about:

- **`current_public_exact_mirror`** — another admitted locator whose latest completed check is
  `available` with the same digest. Never called independent or authoritative.
- **`historical_public_exact_copy`** — another locator served the same bytes before, but its current
  availability is not established.
- **`stored_local_exact_copy`** — the local fact store holds `content:<sha>` in a repository or document
  scope (via `observations where`). This proves only that Lighthouse *observed* an exact local copy; it
  does not prove the file still exists, and authorizes no publication.

A known copy does not suppress the candidate — it changes its meaning from *"the only known copy may be
at risk"* to *"the original locator may be unavailable, while exact copies are known elsewhere"*.
**Absence of a mirror is never rendered as "no mirror exists"** — that uncertainty stays in the unknowns.

## Recovery needs no new lifecycle

If the locator later serves an `available` check, its latest completed check is no longer `not_found`, so
the detector produces **no current candidate** — it reports `suppressed_by_recovery`. No prior candidate
version is mutated, no disposition is touched, and no "resolved" state is invented. The current detector
run *is* the currentness calculation; historical candidates remain immutable artifacts.

## `accepted_for_followup` is not verification

The three operator decisions are unchanged (`accepted_for_followup` | `dismissed` |
`needs_more_evidence`). `accepted_for_followup` means "the operator wants further investigation" — never
verified disappearance, preservation approval, publication approval, disclosure approval, Gateway
approval, or dead-man's-switch eligibility. Every authority pin on the candidate is a `False` the
contract recomputes.

## Why it belongs to `scientific_preservation`, and why no activation

The candidate carries `profile_id: scientific_preservation` because that profile *declared the rule*. The
service verifies the bundled profile exists and validates — it does not activate it, write an
active-profile record, or contact the Gateway. Profile activation remains frozen (ADR-0018). Gateway and
dead-man's-switch authority remain future, separate boundaries: failure or uncertainty must delay a
release, never accelerate it.

## This proves Lighthouse is multi-profile

| | Repository Capability Discovery | Scientific Preservation |
|---|---|---|
| detector | `repository_exact_content_recurrence` | `scientific_source_disappearance_review` |
| evidence | exact content across repository scopes | prior success + later narrow not-found history |

Both use `pattern_candidate.v1`, the immutable candidate id, a detector-owned candidate key, direct
evidence, counterevidence, unknowns, limitations, the false authority pins, and the append-only operator
disposition — through **one** neutral spine (ADR-0023). This does not mean every future detector works
automatically: each still needs a registered specification, direct facts, closed evidence, closed
unknowns and limitations, and a deterministic rule.

## Operator usage

```bash
# The scientific detector reads the append-only public-source ledger (04B). It performs NO network.
lighthouse patterns detect --detector scientific_source_disappearance_review --dry-run --json  # writes nothing
lighthouse patterns detect --detector scientific_source_disappearance_review                   # persist
lighthouse patterns list --detector-id scientific_source_disappearance_review
lighthouse patterns show --id pc_<sha256>
lighthouse patterns review --id pc_<sha256> --operator-id you \
    --decision needs_more_evidence --reason "Locator may have relocated; check the new path."
```

A dry run builds candidates in memory and writes nothing — not a row, not a table, not the database
file. An empty ledger yields no candidate and creates no database. The default detector remains
`repository_exact_content_recurrence`, so every existing `patterns detect` invocation is unchanged.
