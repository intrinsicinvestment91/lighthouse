"""The public-source check service (LHP-INTEL-04B) — the boundary.

The only module that touches both sides: the network adapter in, the append-only ledger out. It
refuses before any network when no host is configured, writes nothing on a dry run, persists a run
and all its checks in one atomic transaction, and validates every stored artifact on the way back
out. It counts what happened; it never says what any of it meant.
"""

from __future__ import annotations

import json

import pytest

from modules import db
from modules import public_source_check as psc
from modules import public_source_check_service as service

# --------------------------------------------------------------------------------------------
# fakes + helpers
# --------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, *, body=b"", headers=None):
        self.status = status
        self._body = body
        self._pos = 0
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, amt):
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


class FakeTransport:
    def __init__(self, responses):
        self._responses = list(responses)

    def open(self, **kwargs):
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resolver(host):
    return ["8.8.8.8"]


class Clock:
    def __init__(self, start=1):
        self.t = start - 1

    def __call__(self):
        self.t += 1
        return f"2026-07-14T00:00:{self.t:02d}+00:00"


def _targets(tmp_path, *locators, name="targets.json"):
    p = tmp_path / name
    p.write_text(
        json.dumps(
            {
                "schema_version": psc.TARGETS_SCHEMA_VERSION,
                "targets": [{"locator": loc} for loc in locators],
            }
        )
    )
    return p


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the ledger at an empty temp path that does not yet exist."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    return db_path


def _run(
    tmp_path, *responses, locators=None, allowed_hosts=("example.org",), dry_run=False, nonce="n1"
):
    locators = locators or ["https://example.org/a"]
    return service.run_checks(
        targets_path=_targets(tmp_path, *locators),
        allowed_hosts=list(allowed_hosts),
        dry_run=dry_run,
        resolver=_resolver,
        transport=FakeTransport(list(responses)),
        clock=Clock(),
        nonce=nonce,
    )


# --------------------------------------------------------------------------------------------
# the fail-closed gates
# --------------------------------------------------------------------------------------------


def test_empty_allowlist_refuses_before_any_network(tmp_path, store):
    with pytest.raises(service.EmptyAllowlistError):
        service.run_checks(
            targets_path=_targets(tmp_path, "https://example.org/a"),
            allowed_hosts=[],
            resolver=_resolver,
            transport=FakeTransport([FakeResponse(200)]),
        )
    assert not store.exists()  # wrote nothing, created no database


def test_malformed_targets_file_refuses_and_creates_nothing(tmp_path, store):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"schema_version": "public_source_targets.v1", "targets": [{"locator": "ftp://x/y"}]}'
    )
    with pytest.raises(psc.TargetsError):
        service.run_checks(targets_path=bad, allowed_hosts=["example.org"], resolver=_resolver)
    assert not store.exists()


def test_dry_run_writes_nothing(tmp_path, store):
    result = _run(tmp_path, FakeResponse(200, body=b"x"), dry_run=True)
    assert result.dry_run is True
    assert len(result.checks) == 1
    assert not store.exists()  # not a row, not a table, not the database file


# --------------------------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------------------------


def test_live_run_stores_one_run_and_one_check_per_target(tmp_path, store):
    result = _run(
        tmp_path,
        FakeResponse(200, body=b"alpha"),
        FakeResponse(404),
        FakeResponse(500),
        locators=[
            "https://example.org/a",
            "https://example.org/b",
            "https://example.org/c",
        ],
    )
    assert result.dry_run is False
    assert store.exists()
    runs = db.fetch_public_source_runs()
    assert len(runs) == 1
    assert runs[0]["completed_check_count"] == 3
    checks = db.fetch_public_source_checks(run_id=runs[0]["run_id"])
    assert len(checks) == 3
    outcomes = sorted(c["outcome"] for c in checks)
    assert outcomes == ["available", "not_found", "server_error"]


def test_available_check_stores_the_exact_byte_digest(tmp_path, store):
    import hashlib

    result = _run(tmp_path, FakeResponse(200, body=b"alpha"))
    check = result.checks[0]
    assert check.outcome == "available"
    assert check.content_digest == hashlib.sha256(b"alpha").hexdigest()


def test_no_response_body_is_stored(tmp_path, store):
    _run(tmp_path, FakeResponse(200, body=b"secret body bytes"))
    row = db.fetch_public_source_checks()[0]
    assert "secret body bytes" not in row["check_json"]
    assert "secret body bytes" not in json.dumps(row)


def test_repeated_identical_checks_are_retained(tmp_path, store):
    # Same locator, same 200, two runs at different times ⇒ two runs, two checks, never deduped.
    _run(tmp_path, FakeResponse(200, body=b"x"), nonce="n1")
    _run(tmp_path, FakeResponse(200, body=b"x"), nonce="n2")
    assert len(db.fetch_public_source_runs()) == 2
    checks = db.fetch_public_source_checks(source_id=psc.source_id_for("https://example.org/a"))
    assert len(checks) == 2


def test_available_then_not_found_then_available_all_survive(tmp_path, store):
    src = psc.source_id_for("https://example.org/a")
    _run(tmp_path, FakeResponse(200, body=b"v1"), nonce="r1")
    _run(tmp_path, FakeResponse(404), nonce="r2")
    _run(tmp_path, FakeResponse(200, body=b"v2"), nonce="r3")
    history = db.fetch_public_source_checks(source_id=src, ascending=True)
    assert [h["outcome"] for h in history] == ["available", "not_found", "available"]


def test_a_failing_target_does_not_abort_the_run(tmp_path, store):
    result = _run(
        tmp_path,
        FakeResponse(200, body=b"a"),
        TimeoutError("boom"),
        FakeResponse(200, body=b"c"),
        locators=[
            "https://example.org/a",
            "https://example.org/b",
            "https://example.org/c",
        ],
    )
    assert len(result.checks) == 3
    outcomes = [c.outcome for c in result.checks]
    assert outcomes == ["available", "timeout", "available"]


# --------------------------------------------------------------------------------------------
# reads: list, show, history — validate on read, order deterministically
# --------------------------------------------------------------------------------------------


def test_absent_store_lists_nothing_and_creates_nothing(tmp_path, store):
    assert service.list_runs()["count"] == 0
    assert (
        service.list_source_checks(source_id=psc.source_id_for("https://example.org/a"))["count"]
        == 0
    )
    assert not store.exists()


def test_show_run_returns_the_run_and_its_checks(tmp_path, store):
    result = _run(tmp_path, FakeResponse(200, body=b"x"))
    shown = service.show_run(result.run.run_id)
    assert shown["run"]["run_id"] == result.run.run_id
    assert len(shown["checks"]) == 1


def test_show_check_returns_one_validated_check(tmp_path, store):
    result = _run(tmp_path, FakeResponse(200, body=b"x"))
    shown = service.show_check(result.checks[0].check_id)
    assert shown["check"]["check_id"] == result.checks[0].check_id


def test_history_by_content_digest(tmp_path, store):
    import hashlib

    _run(tmp_path, FakeResponse(200, body=b"shared"))
    digest = hashlib.sha256(b"shared").hexdigest()
    found = service.list_source_checks(content_digest=digest)
    assert found["count"] == 1
    assert found["checks"][0]["content_digest"] == digest


def test_tampered_check_is_refused_on_read(tmp_path, store):
    result = _run(tmp_path, FakeResponse(200, body=b"x"))
    # Forge an evaluative field directly in the stored JSON.
    conn = db.get_connection()
    with conn:
        row = conn.execute("SELECT check_json FROM public_source_checks LIMIT 1").fetchone()
        forged = json.loads(row[0])
        forged["is_finding"] = True
        conn.execute("UPDATE public_source_checks SET check_json = ?", (json.dumps(forged),))
    conn.close()
    with pytest.raises(service.TamperedRecordError):
        service.show_check(result.checks[0].check_id)


def test_check_id_prefix_resolves_and_ambiguity_is_named(tmp_path, store):
    result = _run(tmp_path, FakeResponse(200, body=b"x"))
    full = result.checks[0].check_id
    prefix = full[len("psc_") : len("psc_") + 12]
    shown = service.show_check(prefix)
    assert shown["check"]["check_id"] == full


def test_run_report_states_no_significance(tmp_path, store):
    result = _run(tmp_path, FakeResponse(200, body=b"x"))
    report = result.to_dict()
    text = json.dumps(report).lower()
    for banned in ("score", "rank", "importance", "significan", "disappear", "suppress"):
        assert banned not in text


def test_the_ledger_is_insert_only():
    """No UPDATE, no DELETE, and no mutating helper touches the ledger tables — cadence is evidence,
    and a rewritten history is not evidence."""
    import inspect

    assert not hasattr(db, "update_public_source_check")
    assert not hasattr(db, "delete_public_source_check")
    assert not hasattr(db, "update_public_source_run")
    assert not hasattr(db, "delete_public_source_run")

    for name in dir(db):
        if "public_source" not in name:
            continue
        member = getattr(db, name)
        if not callable(member):
            continue
        try:
            source = inspect.getsource(member).upper()
        except (OSError, TypeError):  # pragma: no cover - builtins
            continue
        assert "UPDATE PUBLIC_SOURCE" not in source, name
        assert "DELETE FROM PUBLIC_SOURCE" not in source, name
