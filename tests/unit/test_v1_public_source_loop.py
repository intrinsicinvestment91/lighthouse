"""Scientific-preservation path — public-source facts → detector → OSR export.

Not part of the V1 claim (RCD-only activation ceremony). Proves the 04B ledger + 04C detector +
04D-R1 OSR export under a planted active scientific_preservation profile.
"""

from __future__ import annotations

import datetime
import hashlib

import pytest

from modules import db
from modules import pattern_candidate_service as service
from modules import public_source_check as psc
from modules.observed_source_exporter import export_observed_source_reference
from modules.observed_source_reference import CONSUMER_ARTICLE_TO_SKILLS
from modules.pattern_candidate_service import run_detection, show_candidate
from tests.unit.active_profile_test_helpers import plant_active_sp

DAY = 24 * 3600
DIGEST = hashlib.sha256(b"dataset-A").hexdigest()
POLICY_DIGEST = hashlib.sha256(b"policy").hexdigest()
ADAPTER_ID = "public_source_intake"
ADAPTER_VERSION = "public_source_intake/1"
LOCATOR = "https://archive.example.org/dataset-a.csv"
SCIENTIFIC = "scientific_source_disappearance_review"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    plant_active_sp()
    return tmp_path / "state.db"


def _iso(day: int) -> str:
    return datetime.datetime.fromtimestamp(1_800_000_000 + day * DAY, tz=datetime.UTC).isoformat()


def _store_run(*, day: int, targets: list[tuple], nonce: str) -> psc.PublicSourceCheck:
    at = _iso(day)
    canonicals = [t[0] for t in targets]
    run_id = psc.compute_run_id(
        started_at=at,
        run_nonce=nonce,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
        target_count=len(canonicals),
        target_set_digest=psc.target_set_digest(canonicals),
    )
    checks = [
        psc.build_check(
            run_id=run_id,
            canonical_locator=canonical,
            final_locator=canonical,
            redirected=False,
            redirect_chain=(),
            outcome=outcome,
            http_status=status,
            content_digest=digest,
            body_size_bytes=(512 if digest else None),
            content_type=("text/csv" if digest else None),
            checked_at=at,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            policy_digest=POLICY_DIGEST,
        )
        for (canonical, outcome, status, digest) in targets
    ]
    run = psc.build_run(
        started_at=at,
        completed_at=at,
        run_nonce=nonce,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
        targets_ordered=tuple(canonicals),
        checks=tuple(checks),
    )
    db.store_check_run(run=run.to_dict(), checks=[check.to_dict() for check in checks])
    return checks[0]


def test_scientific_preservation_ledger_detect_and_osr_export(ledger):
    available = _store_run(
        day=1,
        nonce="avail",
        targets=[(LOCATOR, psc.AVAILABLE, 200, DIGEST)],
    )
    _store_run(
        day=10,
        nonce="gone",
        targets=[
            (LOCATOR, psc.NOT_FOUND, 404, None),
            *[
                (
                    f"https://o{i}.example.org/x",
                    psc.AVAILABLE,
                    200,
                    hashlib.sha256(f"x{i}".encode()).hexdigest(),
                )
                for i in range(3)
            ],
        ],
    )

    preview = run_detection(detector=SCIENTIFIC, dry_run=True, now=_iso(999))
    assert preview.to_dict()["candidate_count"] == 1

    live = run_detection(detector=SCIENTIFIC, dry_run=False, now=_iso(999))
    assert live.stored == 1
    candidate_id = service.list_candidates(detector_id=SCIENTIFIC)["candidates"][0]["candidate_id"]
    shown = show_candidate(candidate_id)
    assert shown["candidate"]["candidate_type"] == "disappearing_scientific_source"

    exported = export_observed_source_reference(
        available.check_id,
        intended_consumer=CONSUMER_ARTICLE_TO_SKILLS,
        producer_commit="a" * 40,
    )
    ref = exported["reference"]
    assert ref["outcome"] == psc.AVAILABLE
    assert ref["content_digest"] == DIGEST
    assert ref["check_id"] == available.check_id
