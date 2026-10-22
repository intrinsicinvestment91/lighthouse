"""`lighthouse patterns` — the operator's review loop (LHP-INTEL-03B).

Four verbs: detect, list, show, review. Between them they are the first surface in Lighthouse that
shows an operator something other than a raw fact — and so they are the first surface that could
accidentally tell them what to think. The tests below spend most of their weight on what the output
must NOT contain, and on the two guarantees that are easy to lose in a CLI handler: a dry run writes
nothing, and errors go to stderr so that `--json` stdout is always parseable.
"""

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import main
from modules import db
from modules.observation_normalizer import normalize_repository_observation
from modules.repository_intake import observe_repository
from tests.unit.test_repository_intake import make_repo

DOCKERFILE = "FROM scratch\n"
SHARED_CONTENT = "content:" + hashlib.sha256(DOCKERFILE.encode()).hexdigest()


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    from tests.unit.active_profile_test_helpers import plant_active_rcd

    plant_active_rcd()
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def observe(workspace: Path, *repos: Path) -> None:
    db.init_observation_tables()
    for repo in repos:
        db.store_normalized_observation(
            normalize_repository_observation(
                observe_repository(str(repo), allowed_roots=(str(workspace),))
            )
        )


@pytest.fixture
def facts(workspace: Path, store: Path) -> Path:
    alpha = make_repo(workspace / "alpha", {"Dockerfile": DOCKERFILE, "main.py": "print(1)\n"})
    beta = make_repo(workspace / "beta", {"Dockerfile": DOCKERFILE, "app.py": "print(2)\n"})
    observe(workspace, alpha, beta)
    return workspace


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    def invoke(*argv: str) -> None:
        monkeypatch.setattr("sys.argv", ["lighthouse", "patterns", *argv])
        main.cli_main()

    return invoke


def tables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        return {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()


def detect_json(run, capsys) -> dict:
    run("detect", "--json")
    return json.loads(capsys.readouterr().out)


# -- the command exists ---------------------------------------------------------------------


def test_patterns_and_its_four_actions_exist(run, capsys):
    for argv in (
        ("--help",),
        ("detect", "--help"),
        ("list", "--help"),
        ("show", "--help"),
        ("review", "--help"),
    ):
        with pytest.raises(SystemExit) as excinfo:
            run(*argv)
        assert excinfo.value.code == 0
        assert capsys.readouterr().out


def test_patterns_without_an_action_names_them(run, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    for action in ("detect", "list", "show", "review"):
        assert action in err


def test_no_module_flag_workaround_was_added():
    # `patterns` is a subcommand, not a pipeline module. Smuggling it into `--module` would put a
    # review surface inside the collector pipeline.
    parser = main._build_parser()
    module_action = next(
        a for a in parser._actions if "--module" in getattr(a, "option_strings", [])
    )
    assert "patterns" not in (module_action.choices or [])


# -- detect --------------------------------------------------------------------------------


def test_detect_finds_the_one_recurrence(facts, run, capsys):
    result = detect_json(run, capsys)

    assert result["candidate_count"] == 1
    assert result["detector_id"] == "repository_exact_content_recurrence"
    assert result["profile_id"] == "repository_capability_discovery"
    assert result["profile_activated"] is True
    assert result["stored"] == 1

    candidate = result["candidates"][0]
    assert candidate["subject"]["entity_id"] == SHARED_CONTENT
    assert candidate["distinct_repository_scope_count"] == 2
    assert candidate["candidate_state"] == "ready_for_review"


def test_detect_dry_run_writes_nothing(facts, store, run, capsys):
    before = tables(store)
    run("detect", "--dry-run", "--json")
    result = json.loads(capsys.readouterr().out)

    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["stored"] == 0
    assert result["previewed"] == 1
    assert tables(store) == before
    assert "pattern_candidates" not in tables(store)


def test_detect_against_a_missing_database_creates_nothing(tmp_path, monkeypatch, run, capsys):
    path = tmp_path / "absent.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    with pytest.raises(SystemExit) as exit_info:
        run("detect", "--dry-run", "--json")
    assert exit_info.value.code != 0
    err = capsys.readouterr().err
    assert "active profile" in err.lower()
    assert not path.exists()


def test_detect_is_idempotent(facts, run, capsys):
    detect_json(run, capsys)
    second = detect_json(run, capsys)
    assert second["stored"] == 0
    assert second["already_present"] == 1


def test_detect_human_output_states_the_limitations_and_no_significance(facts, run, capsys):
    """The limitations belong in front of the operator, not buried in `--json`.

    The word "novelty" DOES appear — inside the sentence that says recurrence does not prove it. So
    the assertion is not "these words are absent" (which would forbid the denials themselves) but
    "wherever such a word appears, it appears in a denial". A line that used one as a claim would
    have no negation in it, and would fail.
    """
    run("detect")
    out = capsys.readouterr().out.lower()

    assert "ready_for_review" in out
    assert "repository_capability_discovery" in out
    assert "active profile; detector admitted" in out
    assert "does not prove" in out
    assert "operator review is required" in out

    loaded = (
        "important",
        "invention",
        "duplicate work",
        "copied",
        "copying",
        "novelty",
        "reusable",
    )
    negations = ("does not", "do not", "not assessed", "no significance", "not activated")
    for line in out.splitlines():
        if any(word in line for word in loaded):
            assert any(negation in line for negation in negations), line

    # These have no denial form here and must simply never appear.
    for forbidden in ("score", "rank", "importance", "severity", "verified", "finding"):
        assert forbidden not in out, forbidden


def test_detect_emits_valid_json_and_nothing_else_on_stdout(facts, run, capsys):
    run("detect", "--json")
    captured = capsys.readouterr()
    json.loads(captured.out)  # raises if anything non-JSON leaked onto stdout
    assert captured.err == ""


# -- list ----------------------------------------------------------------------------------


def test_list_on_an_empty_store_exits_zero_and_creates_no_database(
    tmp_path, monkeypatch, run, capsys
):
    path = tmp_path / "absent.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    run("list", "--json")
    assert json.loads(capsys.readouterr().out) == {"count": 0, "candidates": []}
    assert not path.exists()


def test_list_shows_the_stored_candidate(facts, run, capsys):
    detect_json(run, capsys)
    run("list", "--json")
    result = json.loads(capsys.readouterr().out)

    assert result["count"] == 1
    assert result["candidates"][0]["subject"]["entity_id"] == SHARED_CONTENT
    assert result["candidates"][0]["disposition"] is None


def test_list_defaults_to_the_latest_version_and_can_show_all(facts, workspace, run, capsys):
    first = detect_json(run, capsys)
    delta = make_repo(workspace / "delta", {"Dockerfile": DOCKERFILE})
    observe(workspace, delta)
    second = detect_json(run, capsys)

    run("list", "--json")
    latest = json.loads(capsys.readouterr().out)
    assert latest["count"] == 1
    assert latest["candidates"][0]["candidate_id"] == second["candidates"][0]["candidate_id"]

    run("list", "--all-versions", "--json")
    every = json.loads(capsys.readouterr().out)
    assert every["count"] == 2
    assert {c["candidate_id"] for c in every["candidates"]} == {
        first["candidates"][0]["candidate_id"],
        second["candidates"][0]["candidate_id"],
    }


def test_list_filters_are_applied_before_the_bound(facts, run, capsys):
    detect_json(run, capsys)

    run("list", "--profile-id", "repository_capability_discovery", "--json")
    assert json.loads(capsys.readouterr().out)["count"] == 1

    run("list", "--profile-id", "scientific_preservation", "--json")
    assert json.loads(capsys.readouterr().out)["count"] == 0

    run("list", "--limit", "1", "--json")
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_list_ordering_is_deterministic_and_not_by_occurrence_count(facts, workspace, run, capsys):
    detect_json(run, capsys)
    delta = make_repo(workspace / "delta", {"Dockerfile": DOCKERFILE})
    observe(workspace, delta)
    detect_json(run, capsys)

    run("list", "--all-versions", "--json")
    once = json.loads(capsys.readouterr().out)
    run("list", "--all-versions", "--json")
    twice = json.loads(capsys.readouterr().out)
    assert once == twice

    counts = [c["distinct_repository_scope_count"] for c in once["candidates"]]
    assert counts == [2, 3]  # chronological within a key — never "most scopes first"


def test_an_unknown_disposition_filter_is_an_error_on_stderr(facts, run, capsys):
    detect_json(run, capsys)
    with pytest.raises(SystemExit) as excinfo:
        run("list", "--disposition", "approved", "--json")
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # JSON stdout is never polluted by error prose
    assert "approved" in captured.err


# -- show ----------------------------------------------------------------------------------


def test_show_returns_the_complete_candidate(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    run("show", "--id", candidate_id, "--json")
    shown = json.loads(capsys.readouterr().out)

    assert shown["candidate"]["candidate_id"] == candidate_id
    assert len(shown["candidate"]["supporting_occurrences"]) == 2
    assert shown["candidate"]["is_finding"] is False
    assert shown["candidate"]["operator_review_required"] is True
    assert shown["disposition"] is None
    assert len(shown["unknown_statements"]) == 10
    assert shown["limitation_statements"]


def test_show_accepts_an_unambiguous_prefix_and_returns_the_full_id(facts, run, capsys):
    result = detect_json(run, capsys)
    full = result["candidates"][0]["candidate_id"]
    digest = full.removeprefix("pc_")

    run("show", "--id", digest[:8], "--json")
    assert json.loads(capsys.readouterr().out)["candidate"]["candidate_id"] == full


def test_show_rejects_a_short_prefix(facts, run, capsys):
    result = detect_json(run, capsys)
    digest = result["candidates"][0]["candidate_id"].removeprefix("pc_")

    with pytest.raises(SystemExit) as excinfo:
        run("show", "--id", digest[:7], "--json")
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "8" in captured.err


def test_show_rejects_an_unknown_candidate(facts, run, capsys):
    detect_json(run, capsys)
    with pytest.raises(SystemExit) as excinfo:
        run("show", "--id", "f" * 64, "--json")
    assert excinfo.value.code == 1
    assert capsys.readouterr().out == ""


def test_show_human_output_carries_unknowns_and_limitations(facts, run, capsys):
    result = detect_json(run, capsys)
    run("show", "--id", result["candidates"][0]["candidate_id"])
    out = capsys.readouterr().out

    assert "Limitations" in out
    assert "Not assessed" in out or "Unknowns" in out
    assert "does not prove" in out.lower()
    assert "alpha" in out and "beta" in out  # the evidence, by scope
    for claim in ("score", "rank", "importance", "verified", "finding"):
        assert claim not in out.lower(), claim


# -- review --------------------------------------------------------------------------------


def test_review_records_one_disposition(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    run(
        "review",
        "--id",
        candidate_id,
        "--operator-id",
        "operator-test",
        "--decision",
        "needs_more_evidence",
        "--reason",
        "Repository lineage has not been assessed.",
        "--json",
    )
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["disposition"]["decision"] == "needs_more_evidence"
    assert recorded["disposition"]["operator_identity_authenticated"] is False
    assert recorded["disposition"]["review_method"] == "local_cli"

    run("show", "--id", candidate_id, "--json")
    shown = json.loads(capsys.readouterr().out)
    assert shown["disposition"]["decision"] == "needs_more_evidence"
    assert (
        shown["disposition"]["candidate_artifact_digest"] == shown["candidate"]["artifact_digest"]
    )


def test_review_does_not_change_the_candidate(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    run("show", "--id", candidate_id, "--json")
    before = json.loads(capsys.readouterr().out)["candidate"]

    run(
        "review",
        "--id",
        candidate_id,
        "--operator-id",
        "operator-test",
        "--decision",
        "accepted_for_followup",
        "--reason",
        "Worth a look.",
        "--json",
    )
    capsys.readouterr()

    run("show", "--id", candidate_id, "--json")
    after = json.loads(capsys.readouterr().out)["candidate"]
    assert after == before


def test_a_second_review_of_the_same_version_fails_closed(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    run(
        "review",
        "--id",
        candidate_id,
        "--operator-id",
        "operator-test",
        "--decision",
        "dismissed",
        "--reason",
        "Mine, reused.",
        "--json",
    )
    capsys.readouterr()

    with pytest.raises(SystemExit) as excinfo:
        run(
            "review",
            "--id",
            candidate_id,
            "--operator-id",
            "operator-test",
            "--decision",
            "accepted_for_followup",
            "--reason",
            "Changed my mind.",
            "--json",
        )
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "decision" in captured.err.lower()


def test_review_rejects_a_decision_outside_the_closed_enum(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    for bad in ("approved", "verified", "published"):
        with pytest.raises(SystemExit):
            run(
                "review",
                "--id",
                candidate_id,
                "--operator-id",
                "operator-test",
                "--decision",
                bad,
                "--reason",
                "Because.",
                "--json",
            )
        capsys.readouterr()


def test_review_requires_an_operator_and_a_reason(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    with pytest.raises(SystemExit):
        run(
            "review",
            "--id",
            candidate_id,
            "--operator-id",
            "  ",
            "--decision",
            "dismissed",
            "--reason",
            "Because.",
            "--json",
        )
    capsys.readouterr()

    with pytest.raises(SystemExit):
        run(
            "review",
            "--id",
            candidate_id,
            "--operator-id",
            "operator-test",
            "--decision",
            "dismissed",
            "--reason",
            "   ",
            "--json",
        )
    capsys.readouterr()


def test_review_of_an_unknown_candidate_is_an_error(facts, run, capsys):
    detect_json(run, capsys)
    with pytest.raises(SystemExit):
        run(
            "review",
            "--id",
            "f" * 64,
            "--operator-id",
            "operator-test",
            "--decision",
            "dismissed",
            "--reason",
            "Because.",
            "--json",
        )
    assert capsys.readouterr().out == ""


def test_review_creates_no_finding_and_contacts_nothing(facts, run, capsys):
    result = detect_json(run, capsys)
    candidate_id = result["candidates"][0]["candidate_id"]

    run(
        "review",
        "--id",
        candidate_id,
        "--operator-id",
        "operator-test",
        "--decision",
        "accepted_for_followup",
        "--reason",
        "Worth a look.",
        "--json",
    )
    body = capsys.readouterr().out.lower()
    for absent in ("verified", "finding", "gateway", "publish", "authoriz", "activat"):
        assert absent not in body, absent


def test_a_new_version_may_be_reviewed_separately(facts, workspace, run, capsys):
    first = detect_json(run, capsys)
    run(
        "review",
        "--id",
        first["candidates"][0]["candidate_id"],
        "--operator-id",
        "operator-test",
        "--decision",
        "dismissed",
        "--reason",
        "Only two.",
        "--json",
    )
    capsys.readouterr()

    delta = make_repo(workspace / "delta", {"Dockerfile": DOCKERFILE})
    observe(workspace, delta)
    second = detect_json(run, capsys)

    run(
        "review",
        "--id",
        second["candidates"][0]["candidate_id"],
        "--operator-id",
        "operator-test",
        "--decision",
        "needs_more_evidence",
        "--reason",
        "A third repository changes things.",
        "--json",
    )
    assert json.loads(capsys.readouterr().out)["disposition"]["decision"] == "needs_more_evidence"

    # The earlier decision is exactly where it was left.
    run("show", "--id", first["candidates"][0]["candidate_id"], "--json")
    assert json.loads(capsys.readouterr().out)["disposition"]["decision"] == "dismissed"


# -- what the surface must never say --------------------------------------------------------


FORBIDDEN_KEYS = frozenset(
    {
        "score",
        "score_label",
        "importance",
        "priority",
        "severity",
        "rank",
        "ranking",
        "novelty",
        "significance",
        "similarity",
        "recommendation",
    }
)


def _every_key(node, keys: set[str]) -> set[str]:
    """Recursively — the one place an evaluative field could hide is nested inside the evidence."""
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            _every_key(value, keys)
    elif isinstance(node, list):
        for item in node:
            _every_key(item, keys)
    return keys


def test_no_command_emits_a_score_a_rank_or_a_verdict(facts, run, capsys):
    detect_json(run, capsys)
    run("list", "--json")
    listed = capsys.readouterr().out

    run("detect", "--json")
    detected = capsys.readouterr().out

    result = json.loads(detected)
    run("show", "--id", result["candidates"][0]["candidate_id"], "--json")
    shown = capsys.readouterr().out

    for body in (listed, detected, shown):
        keys = _every_key(json.loads(body), set())
        assert not (keys & FORBIDDEN_KEYS), keys & FORBIDDEN_KEYS


# -- adversarial: a tampered FACT must fail closed, not crash --------------------------------


def test_a_tampered_fact_fails_closed_rather_than_raising_a_traceback(facts, store, run, capsys):
    """The detector reads through the fact boundary, which refuses a tampered artifact — and that
    refusal has to arrive as an error, not as an unhandled exception.

    Found in adversarial review: `TamperedObservationError` belongs to the fact layer, so it sailed
    straight past a handler that caught only the candidate layer's errors. A stack trace is not a
    fail-closed answer: it dumps raw internals and corrupts `--json` stdout.
    """
    conn = sqlite3.connect(store)
    row = conn.execute(
        "SELECT normalization_digest, artifact_json FROM observation_sets"
    ).fetchone()
    artifact = json.loads(row[1])
    artifact["observations"].append(
        {
            "observation_type": "content.coverage",
            "source": "filesystem",
            "value": {"complete": True},
            "confidence": 1.0,
            "observed_at": "2026-07-14T00:00:00+00:00",
        }
    )
    conn.execute(
        "UPDATE observation_sets SET artifact_json = ? WHERE normalization_digest = ?",
        (json.dumps(artifact), row[0]),
    )
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit) as excinfo:
        run("detect", "--json")
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    assert captured.out == ""  # no half-written JSON
    assert captured.err  # a message, not a traceback
    assert "Traceback" not in captured.err


# -- the scientific detector (LHP-INTEL-04C) -------------------------------------------------

import datetime  # noqa: E402

from modules import public_source_check as psc  # noqa: E402

SCIENTIFIC = "scientific_source_disappearance_review"
_DIGEST_A = hashlib.sha256(b"dataset-A").hexdigest()
_DIGEST_B = hashlib.sha256(b"dataset-B").hexdigest()
_POLICY = hashlib.sha256(b"policy").hexdigest()
_L = "https://a.example.org/dataset.csv"


def _seed_disappearance() -> None:
    def at(day):
        return datetime.datetime.fromtimestamp(
            1_800_000_000 + day * 86400, tz=datetime.UTC
        ).isoformat()

    def store(day, targets, nonce):
        canon = [t[0] for t in targets]
        run_id = psc.compute_run_id(
            started_at=at(day),
            run_nonce=nonce,
            adapter_id="public_source_intake",
            adapter_version="public_source_intake/1",
            policy_digest=_POLICY,
            target_count=len(canon),
            target_set_digest=psc.target_set_digest(canon),
        )
        checks = [
            psc.build_check(
                run_id=run_id,
                canonical_locator=c,
                final_locator=c,
                redirected=False,
                redirect_chain=(),
                outcome=o,
                http_status=s,
                content_digest=d,
                body_size_bytes=(512 if d else None),
                content_type=("text/csv" if d else None),
                checked_at=at(day),
                adapter_id="public_source_intake",
                adapter_version="public_source_intake/1",
                policy_digest=_POLICY,
            )
            for (c, o, s, d) in targets
        ]
        run = psc.build_run(
            started_at=at(day),
            completed_at=at(day),
            run_nonce=nonce,
            adapter_id="public_source_intake",
            adapter_version="public_source_intake/1",
            policy_digest=_POLICY,
            targets_ordered=tuple(canon),
            checks=tuple(checks),
        )
        db.store_check_run(run=run.to_dict(), checks=[c.to_dict() for c in checks])

    store(1, [(_L, "available", 200, _DIGEST_A)], "n1")
    store(
        10,
        [(_L, "not_found", 404, None)]
        + [(f"https://o{i}.example.org/x", "available", 200, _DIGEST_B) for i in range(3)],
        "n2",
    )


def test_detect_has_a_closed_detector_choice(run, capsys):
    with pytest.raises(SystemExit):
        run("detect", "--detector", "not_a_real_detector")
    err = capsys.readouterr().err
    assert "invalid choice" in err.lower() or "not_a_real_detector" in err


def test_scientific_detect_refuses_under_active_rcd(store, run, capsys):
    _seed_disappearance()
    with pytest.raises(SystemExit) as exit_info:
        run("detect", "--detector", SCIENTIFIC, "--json")
    assert exit_info.value.code != 0
    err = capsys.readouterr().err
    assert "not admitted" in err.lower() or "ownership" in err.lower() or "active" in err.lower()


def test_scientific_detect_dry_run_refuses_without_matching_active_profile(
    tmp_path, monkeypatch, run, capsys
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty.db")
    with pytest.raises(SystemExit) as exit_info:
        run("detect", "--detector", SCIENTIFIC, "--dry-run", "--json")
    assert exit_info.value.code != 0
    assert not (tmp_path / "empty.db").exists()


def test_scientific_detect_human_output_is_safe(store, run, capsys):
    """Under active RCD, scientific detect refuses — and must not print a candidate."""
    _seed_disappearance()
    with pytest.raises(SystemExit):
        run("detect", "--detector", SCIENTIFIC)
    out = capsys.readouterr()
    body = (out.out + out.err).lower()
    assert "preservation review candidate" not in body
    for banned in (
        "permanently gone",
        "only known copy",
        "verified disappearance",
        "important science",
        "critical research",
        "censored",
        "maliciously removed",
        "publication ready",
    ):
        assert banned not in body


def test_scientific_show_of_historical_candidate_remains_available(store, run, capsys):
    """A scientific candidate planted outside service detection remains readable."""
    from modules import pattern_candidate_service as service
    from modules import scientific_source_disappearance as ssd

    _seed_disappearance()
    data, _truncated = service._collect_public_source_input("2026-08-30T00:00:00+00:00")
    result = ssd.detect_disappearing_sources(data)
    candidate = service._assemble_scientific_candidate(
        result.candidates[0], "2026-08-30T00:00:00+00:00"
    )
    db.init_pattern_candidate_tables()
    db.insert_pattern_candidate(candidate)

    run("show", "--id", candidate.candidate_id)
    out = capsys.readouterr().out
    assert "Preservation review candidate" in out
    assert "HTTP 404" in out
    assert "not a finding" in out.lower()
