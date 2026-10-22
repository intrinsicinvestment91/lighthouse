"""The disappearing scientific source detector (LHP-INTEL-04C) — the pure rule engine.

It reads validated public-source check facts and answers exactly one conservative question per
admitted
locator: *did a previously available source's most recent completed check return a narrow 404/410
not-found, at least seven days after the last time it was seen, and not as part of a broad outage?*

It says no more than that. It builds no candidate object, touches no database, opens no socket, and
reads no clock — `created_at` is injected. Every test below is a pure function of the facts it was
handed.
"""

from __future__ import annotations

import hashlib

import pytest

from modules import scientific_source_disappearance as ssd
from modules.scientific_source_disappearance import (
    LocatorHistory,
    ScientificDetectionInput,
    detect_disappearing_sources,
)

DAY = 24 * 3600
DIGEST_A = hashlib.sha256(b"dataset-A").hexdigest()
DIGEST_B = hashlib.sha256(b"dataset-B").hexdigest()


def _iso(day: int) -> str:
    base = 1_800_000_000  # fixed epoch seconds; value is irrelevant, only differences matter
    secs = base + day * DAY
    import datetime

    return datetime.datetime.fromtimestamp(secs, tz=datetime.UTC).isoformat()


def chk(
    *,
    outcome,
    day,
    http_status=None,
    content_digest=None,
    run_id="run-1",
    locator_id="loc-A",
    source_id="src-A",
    canonical="https://a.example.org/dataset.csv",
    seq=0,
):
    algo = "sha256" if content_digest else None
    ident = hashlib.sha256(f"{locator_id}{day}{outcome}{seq}".encode()).hexdigest()
    return {
        "check_id": "psc_" + ident,
        "run_id": run_id,
        "source_id": source_id,
        "locator_id": locator_id,
        "canonical_locator": canonical,
        "outcome": outcome,
        "http_status": http_status,
        "content_digest": content_digest,
        "content_digest_algorithm": algo,
        "body_size_bytes": 1024 if content_digest else None,
        "content_type": "text/csv" if content_digest else None,
        "checked_at": _iso(day),
        "adapter_id": "public_source_intake",
        "adapter_version": "public_source_intake/1",
        "policy_digest": "p" * 64,
        "artifact_digest": ident,
        "source_class": "public_source",
        "privacy_class": "public",
    }


def loc(
    checks, *, locator_id="loc-A", source_id="src-A", canonical="https://a.example.org/dataset.csv"
):
    return LocatorHistory(
        source_id=source_id,
        locator_id=locator_id,
        canonical_locator=canonical,
        checks=tuple(checks),
    )


def run(locators, *, local=None, run_digests=None, created_at=None):
    return detect_disappearing_sources(
        ScientificDetectionInput(
            locators=tuple(locators),
            local_occurrences=dict(local or {}),
            run_digests=dict(run_digests or {}),
            created_at=created_at or _iso(999),
        )
    )


def available(day, digest=DIGEST_A, **kw):
    return chk(outcome="available", day=day, http_status=200, content_digest=digest, **kw)


def not_found(day, status=404, **kw):
    return chk(outcome="not_found", day=day, http_status=status, **kw)


# -- basic trigger ---------------------------------------------------------------------------


def test_no_history_produces_no_candidate():
    result = run([loc([])])
    assert result.candidates == ()


def test_one_not_found_produces_no_candidate():
    result = run([loc([not_found(30)])])
    assert result.candidates == ()


def test_one_available_produces_no_candidate():
    result = run([loc([available(1)])])
    assert result.candidates == ()


def test_source_never_available_produces_no_candidate():
    result = run([loc([not_found(1), not_found(30)])])
    assert not result.candidates
    assert result.non_candidates == (("loc-A", ssd.REASON_NO_PRIOR_SUCCESS),)


def test_prior_available_then_404_after_seven_days_is_one_candidate():
    result = run([loc([available(1), not_found(10)])])
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.subject_content_entity_id == "content:" + DIGEST_A
    assert candidate.evidence["triggering_not_found"]["http_status"] == 404
    assert candidate.evidence["temporal_rule"]["temporal_rule_satisfied"] is True


def test_prior_available_then_410_triggers():
    result = run([loc([available(1), not_found(10, status=410)])])
    assert len(result.candidates) == 1


def test_prior_available_then_404_within_seven_days_is_no_candidate():
    result = run([loc([available(1), not_found(5)])])
    assert not result.candidates
    assert result.non_candidates == (("loc-A", ssd.REASON_INSUFFICIENT_ELAPSED),)


def test_exactly_seven_days_satisfies_the_threshold():
    result = run([loc([available(1), not_found(8)])])  # day 1 -> day 8 == 7 days exactly
    assert len(result.candidates) == 1


def test_subject_is_the_most_recent_prior_successful_digest():
    result = run(
        [loc([available(1, digest=DIGEST_A), available(20, digest=DIGEST_B), not_found(30)])]
    )
    assert result.candidates[0].subject_content_entity_id == "content:" + DIGEST_B


# -- latest-check selection ------------------------------------------------------------------


def test_recovery_after_not_found_produces_no_current_candidate():
    result = run([loc([available(1), not_found(10), available(11)])])
    assert not result.candidates
    assert result.non_candidates == (("loc-A", ssd.REASON_SUPPRESSED_BY_RECOVERY),)


def test_later_timeout_after_not_found_is_not_a_trigger():
    result = run([loc([available(1), not_found(10), chk(outcome="timeout", day=12)])])
    assert not result.candidates
    assert result.non_candidates == (("loc-A", ssd.REASON_LATEST_NOT_TRIGGERING),)


def test_older_not_found_with_a_later_success_never_triggers():
    # available, not_found, available: latest is available -> recovery, never the old not_found.
    result = run([loc([available(1), not_found(10), available(40)])])
    assert not result.candidates


def test_two_not_founds_trigger_from_the_latest_using_the_most_recent_success():
    result = run([loc([available(1), not_found(10), not_found(30)])])
    assert len(result.candidates) == 1
    # the prior success is day 1; elapsed is measured to the latest (day 30) not-found
    assert result.candidates[0].evidence["temporal_rule"]["prior_success_checked_at"] == _iso(1)
    assert result.candidates[0].evidence["triggering_not_found"]["checked_at"] == _iso(30)


# -- narrow outcome --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,status",
    [
        ("timeout", None),
        ("dns_failure", None),
        ("tls_failure", None),
        ("connection_failure", None),
        ("authentication_required", 401),
        ("rate_limited", 429),
        ("policy_refused", None),
        ("server_error", 503),
        ("client_error", 400),
        ("adapter_failure", None),
        ("unknown", None),
        ("redirected_failure", None),
    ],
)
def test_non_not_found_latest_never_triggers(outcome, status):
    result = run([loc([available(1), chk(outcome=outcome, day=30, http_status=status)])])
    assert not result.candidates


def test_a_404_body_is_a_fact_not_a_verdict_only_404_or_410():
    # A client_error that is not the narrow 404/410 mapping must not trigger, even at 403/451.
    for status in (400, 403, 451):
        result = run([loc([available(1), chk(outcome="client_error", day=30, http_status=status)])])
        assert not result.candidates


# -- outage defense --------------------------------------------------------------------------


def _unrelated(n_checks, n_infra, run_id="run-1"):
    """`n_checks` unrelated locators in the triggering run, `n_infra` of them infrastructure
    failures."""
    out = []
    for i in range(n_checks):
        outcome = "dns_failure" if i < n_infra else "available"
        digest = None if i < n_infra else DIGEST_B
        out.append(
            loc(
                [
                    chk(
                        outcome=outcome,
                        day=30,
                        http_status=(None if i < n_infra else 200),
                        content_digest=digest,
                        run_id=run_id,
                        locator_id=f"other-{i}",
                        source_id=f"other-src-{i}",
                        canonical=f"https://o{i}.example.org/x",
                    )
                ],
                locator_id=f"other-{i}",
                source_id=f"other-src-{i}",
                canonical=f"https://o{i}.example.org/x",
            )
        )
    return out


def test_fewer_than_three_unrelated_checks_is_limited_context_but_allowed():
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    result = run([subject, *_unrelated(2, 0)])
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.evidence["outage_context"]["outage_context_status"] == ssd.OUTAGE_LIMITED
    assert ssd.CONDITIONAL_UNKNOWN_OUTAGE in candidate.unknowns
    assert ssd.CONDITIONAL_LIMITATION_OUTAGE in candidate.limitations


def test_three_unrelated_zero_infrastructure_failures_passes():
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    result = run([subject, *_unrelated(3, 0)])
    assert len(result.candidates) == 1
    assert (
        result.candidates[0].evidence["outage_context"]["outage_context_status"]
        == ssd.OUTAGE_PASSED
    )
    assert ssd.CONDITIONAL_UNKNOWN_OUTAGE not in result.candidates[0].unknowns


def test_three_unrelated_one_infrastructure_failure_passes():
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    result = run([subject, *_unrelated(3, 1)])
    assert len(result.candidates) == 1


def test_three_unrelated_two_infrastructure_failures_blocks():
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    result = run([subject, *_unrelated(3, 2)])
    assert not result.candidates
    assert ("loc-A", ssd.REASON_BLOCKED_OUTAGE) in result.non_candidates


def test_six_unrelated_four_infrastructure_failures_blocks():
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    result = run([subject, *_unrelated(6, 4)])
    assert not result.candidates


def test_not_found_in_the_run_is_not_an_infrastructure_failure():
    # Three unrelated locators that all 404 in the triggering run: that is not a broad OUTAGE, it is
    # three more disappearances. It must not block, and it must not be counted as infrastructure.
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    others = [
        loc(
            [
                not_found(
                    30,
                    run_id="run-1",
                    locator_id=f"o-{i}",
                    source_id=f"s-{i}",
                    canonical=f"https://o{i}.example.org/x",
                )
            ],
            locator_id=f"o-{i}",
            source_id=f"s-{i}",
            canonical=f"https://o{i}.example.org/x",
        )
        for i in range(3)
    ]
    result = run([subject, *others])
    subject_candidate = [c for c in result.candidates if c.locator_id == "loc-A"]
    assert subject_candidate
    assert subject_candidate[0].evidence["outage_context"]["infrastructure_failure_count"] == 0


def test_the_triggering_locator_is_excluded_from_its_own_outage_context():
    subject = loc([available(1, run_id="run-0"), not_found(30, run_id="run-1")])
    result = run([subject, *_unrelated(3, 0)])
    ctx = result.candidates[0].evidence["outage_context"]
    assert ctx["unrelated_check_count"] == 3  # the subject's own check is not counted


# -- counterevidence -------------------------------------------------------------------------


def test_a_current_public_mirror_becomes_counterevidence():
    subject = loc([available(1), not_found(30)])
    mirror = loc(
        [
            available(
                29,
                digest=DIGEST_A,
                locator_id="loc-M",
                source_id="src-M",
                canonical="https://mirror.example.org/d.csv",
                run_id="run-2",
            )
        ],
        locator_id="loc-M",
        source_id="src-M",
        canonical="https://mirror.example.org/d.csv",
    )
    result = run([subject, mirror])
    candidate = [c for c in result.candidates if c.locator_id == "loc-A"][0]
    kinds = {ce["type"] for ce in candidate.counterevidence}
    assert ssd.CE_CURRENT_PUBLIC_MIRROR in kinds


def test_a_stored_local_copy_becomes_counterevidence_and_adds_the_conditional_unknown():
    subject = loc([available(1), not_found(30)])
    occ = {
        "scope_id": hashlib.sha256(b"/repos/x").hexdigest(),
        "scope_type": "repository",
        "scope_label": "x",
        "source_artifact_digest": hashlib.sha256(b"artifact").hexdigest(),
        "derivation_version": "observation_normalizer/2",
    }
    result = run([subject], local={"content:" + DIGEST_A: (occ,)})
    candidate = result.candidates[0]
    kinds = {ce["type"] for ce in candidate.counterevidence}
    assert ssd.CE_STORED_LOCAL_COPY in kinds
    assert ssd.CONDITIONAL_UNKNOWN_LOCAL in candidate.unknowns


def test_a_mirror_is_never_called_independent_or_authoritative():
    subject = loc([available(1), not_found(30)])
    mirror = loc(
        [
            available(
                29,
                digest=DIGEST_A,
                locator_id="loc-M",
                source_id="src-M",
                canonical="https://mirror.example.org/d.csv",
                run_id="run-2",
            )
        ],
        locator_id="loc-M",
        source_id="src-M",
        canonical="https://mirror.example.org/d.csv",
    )
    result = run([subject, mirror])
    for ce in result.candidates[0].counterevidence:
        assert "independent" not in " ".join(map(str, ce.keys())).lower()
        assert "authoritative" not in " ".join(map(str, ce.keys())).lower()


def test_absence_of_a_mirror_is_not_rendered_as_no_mirror_exists():
    result = run([loc([available(1), not_found(30)])])
    assert result.candidates[0].counterevidence == ()
    # the uncertainty lives in unknowns/limitations, not as a false "no copy exists" claim
    assert "mirror_search_coverage_incomplete" in result.candidates[0].unknowns


# -- required vocabulary ---------------------------------------------------------------------


def test_every_candidate_carries_the_mandatory_unknowns_and_limitations():
    candidate = run([loc([available(1), not_found(30)])]).candidates[0]
    assert set(ssd.REQUIRED_UNKNOWNS) <= set(candidate.unknowns)
    assert set(ssd.REQUIRED_LIMITATIONS) <= set(candidate.limitations)
    assert "permanent_disappearance_not_established" in candidate.unknowns


def test_candidates_are_ordered_by_locator_then_subject_never_by_count():
    a = loc(
        [available(1), not_found(30)],
        locator_id="loc-A",
        source_id="s-A",
        canonical="https://a.example.org/d",
    )
    z = loc(
        [available(1), not_found(30)],
        locator_id="loc-Z",
        source_id="s-Z",
        canonical="https://z.example.org/d",
    )
    forward = run([a, z]).candidates
    backward = run([z, a]).candidates
    assert [c.locator_id for c in forward] == [c.locator_id for c in backward] == ["loc-A", "loc-Z"]
