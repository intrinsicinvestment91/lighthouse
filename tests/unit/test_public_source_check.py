"""The public-source check contract (LHP-INTEL-04B) — pure, fail-closed, no network.

`public_source_check.py` owns three versioned contracts (`public_source_targets.v1`,
`public_source_check.v1`, `public_source_check_run.v1`), the closed outcome vocabulary, the
identity recipes, and the static locator/host policy. It states a direct factual outcome and has
**no field in which it could say what that outcome means** — no score, no significance, and (unlike
the observation store) time is part of a check's identity.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from modules import public_source_check as psc

# --------------------------------------------------------------------------------------------
# vocabulary and constants
# --------------------------------------------------------------------------------------------


def test_outcome_vocabulary_is_closed_and_names_no_disappearance():
    # A failure proves only itself. The vocabulary must have no word for a judgement.
    assert psc.AVAILABLE in psc.OUTCOMES
    assert psc.NOT_FOUND in psc.OUTCOMES
    assert psc.UNKNOWN in psc.OUTCOMES
    for banned in ("disappeared", "suppressed", "removed", "important", "censored", "gone"):
        assert banned not in psc.OUTCOMES


def test_determined_outcomes_are_confident_and_unknown_is_not():
    assert psc.confidence_for(psc.AVAILABLE) == 1.0
    assert psc.confidence_for(psc.NOT_FOUND) == 1.0
    assert psc.confidence_for(psc.TIMEOUT) == 1.0
    assert psc.confidence_for(psc.UNKNOWN) == 0.0


# --------------------------------------------------------------------------------------------
# canonical locator + static locator policy (HTTPS-only v1)
# --------------------------------------------------------------------------------------------


def test_https_locator_canonicalizes():
    assert psc.canonical_locator("HTTPS://Example.ORG/a/B?q=1") == "https://example.org/a/B?q=1"


def test_canonicalization_lowercases_host_not_path_or_query():
    out = psc.canonical_locator("https://Example.ORG/Path/CASE?Q=Mixed")
    assert out == "https://example.org/Path/CASE?Q=Mixed"


def test_canonicalization_drops_default_port_and_fragment():
    assert psc.canonical_locator("https://example.org:443/x#frag") == "https://example.org/x"


def test_empty_path_normalizes_to_slash():
    assert psc.canonical_locator("https://example.org") == "https://example.org/"


def test_query_order_is_preserved():
    out = psc.canonical_locator("https://example.org/?b=2&a=1")
    assert out == "https://example.org/?b=2&a=1"


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.org/x",  # not https
        "ftp://example.org/x",
        "file:///etc/passwd",
        "data:text/plain,hi",
        "ws://example.org/x",
        "https://user:pass@example.org/x",  # userinfo / credentials
        "https://example.org:8443/x",  # non-default port
        "https://127.0.0.1/x",  # IP literal
        "https://[::1]/x",  # IPv6 literal
        "https:///x",  # hostless
        "https://",  # hostless
        "not a url",
        "https://exa mple.org/x",  # malformed host
    ],
)
def test_disallowed_locators_are_refused(raw):
    with pytest.raises(psc.LocatorError):
        psc.canonical_locator(raw)


def test_overlong_locator_is_refused():
    long = "https://example.org/" + ("a" * (psc.MAX_URL_LENGTH + 10))
    with pytest.raises(psc.LocatorError):
        psc.canonical_locator(long)


# --------------------------------------------------------------------------------------------
# host allowlist policy — normalize, exact match, no suffix trust, no self-authorization
# --------------------------------------------------------------------------------------------


def test_host_normalization_lowercases_and_idna():
    assert psc.normalize_host("Example.ORG") == "example.org"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "*.example.org",  # wildcard
        "https://example.org",  # scheme inside host entry
        "example.org/path",  # path inside host entry
        "user@example.org",  # userinfo inside host entry
        "example.org:443",  # port inside host entry
    ],
)
def test_malformed_host_entries_are_refused(bad):
    with pytest.raises(psc.HostPolicyError):
        psc.normalize_host(bad)


def test_allowlist_dedupes_and_matches_exactly():
    allow = psc.normalize_host_allowlist(["Example.ORG", "example.org", "archive.example.edu"])
    assert allow == frozenset({"example.org", "archive.example.edu"})
    assert psc.host_allowed("example.org", allow) is True
    # exact only: a subdomain and a lookalike are NOT the same host
    assert psc.host_allowed("evil-example.org", allow) is False
    assert psc.host_allowed("sub.example.org", allow) is False


def test_empty_allowlist_matches_nothing():
    allow = psc.normalize_host_allowlist([])
    assert allow == frozenset()
    assert psc.host_allowed("example.org", allow) is False


# --------------------------------------------------------------------------------------------
# identity recipes
# --------------------------------------------------------------------------------------------


def test_source_and_locator_ids_share_a_digest_but_differ_in_prefix():
    loc = "https://example.org/a"
    sid = psc.source_id_for(loc)
    lid = psc.locator_id_for(loc)
    assert sid.startswith(psc.SOURCE_ID_PREFIX)
    assert lid.startswith(psc.LOCATOR_ID_PREFIX)
    assert sid.split(":", 1)[1] == lid.split(":", 1)[1]  # same sha256(canonical_locator)


def test_ids_are_a_function_of_the_canonical_locator_only():
    # Two encodings of the same locator canonicalize to one identity.
    a = psc.source_id_for(psc.canonical_locator("https://Example.ORG:443/a#x"))
    b = psc.source_id_for(psc.canonical_locator("https://example.org/a"))
    assert a == b


# --------------------------------------------------------------------------------------------
# check artifact
# --------------------------------------------------------------------------------------------

_ALPHA_DIGEST = "9f" * 32  # a 64-char lowercase hex placeholder


def _available_check(**overrides):
    kwargs = dict(
        run_id="psr_" + "0" * 64,
        canonical_locator="https://example.org/a",
        final_locator="https://example.org/a",
        redirected=False,
        redirect_chain=(),
        outcome=psc.AVAILABLE,
        http_status=200,
        content_digest=_ALPHA_DIGEST,
        body_size_bytes=5,
        content_type="text/plain",
        checked_at="2026-07-14T00:00:00+00:00",
        adapter_id="public_source_intake",
        adapter_version="public_source_intake/1",
        policy_digest="ab" * 32,
    )
    kwargs.update(overrides)
    return psc.build_check(**kwargs)


def test_available_check_validates_and_binds_its_identity():
    check = _available_check()
    check.validate()
    assert check.check_id.startswith(psc.CHECK_ID_PREFIX)
    assert check.check_id == psc.CHECK_ID_PREFIX + check.artifact_digest
    assert check.confidence == 1.0
    assert check.source_class == "public_source"
    assert check.privacy_class == "public"


def test_not_found_check_carries_status_and_no_digest():
    check = _available_check(
        outcome=psc.NOT_FOUND, http_status=404, content_digest=None, body_size_bytes=None
    )
    check.validate()
    assert check.content_digest is None
    assert check.http_status == 404


def test_no_response_outcomes_carry_no_status_and_no_digest():
    check = _available_check(
        outcome=psc.TIMEOUT, http_status=None, content_digest=None, body_size_bytes=None
    )
    check.validate()
    assert check.http_status is None
    assert check.content_digest is None


def test_available_requires_a_digest_a_size_and_a_status():
    with pytest.raises(psc.PublicSourceCheckError):
        _available_check(content_digest=None)
    with pytest.raises(psc.PublicSourceCheckError):
        _available_check(body_size_bytes=None)
    with pytest.raises(psc.PublicSourceCheckError):
        _available_check(http_status=None)


def test_non_available_forbids_a_content_digest():
    with pytest.raises(psc.PublicSourceCheckError):
        _available_check(outcome=psc.NOT_FOUND, http_status=404, content_digest=_ALPHA_DIGEST)


def test_unknown_requires_zero_confidence():
    check = _available_check(
        outcome=psc.UNKNOWN, http_status=None, content_digest=None, body_size_bytes=None
    )
    check.validate()
    assert check.confidence == 0.0


def test_artifact_digest_is_recomputed_on_read():
    check = _available_check()
    raw = check.to_dict()
    raw["artifact_digest"] = "0" * 64  # forge the digest
    with pytest.raises(psc.PublicSourceCheckError):
        psc.PublicSourceCheck.from_dict(raw).validate()


def test_checked_at_is_covered_by_identity():
    a = _available_check(checked_at="2026-07-14T00:00:00+00:00")
    b = _available_check(checked_at="2026-07-14T01:00:00+00:00")
    # Same locator + outcome + bytes, different time ⇒ different identity. Time is a fact here.
    assert a.check_id != b.check_id


def test_authority_pins_must_be_literal_false():
    check = _available_check()
    raw = check.to_dict()
    raw["is_finding"] = True
    # re-stamp the digest so only the pin check can catch it
    content = {k: v for k, v in raw.items() if k not in ("check_id", "artifact_digest")}
    raw["artifact_digest"] = psc._artifact_digest(content)
    raw["check_id"] = psc.CHECK_ID_PREFIX + raw["artifact_digest"]
    with pytest.raises(psc.PublicSourceCheckError):
        psc.PublicSourceCheck.from_dict(raw).validate()


def test_classification_pins_are_not_overridable():
    check = _available_check()
    raw = check.to_dict()
    raw["publication_authorized"] = True
    content = {k: v for k, v in raw.items() if k not in ("check_id", "artifact_digest")}
    raw["artifact_digest"] = psc._artifact_digest(content)
    raw["check_id"] = psc.CHECK_ID_PREFIX + raw["artifact_digest"]
    with pytest.raises(psc.PublicSourceCheckError):
        psc.PublicSourceCheck.from_dict(raw).validate()


def test_unknown_field_is_refused_in():
    check = _available_check()
    raw = check.to_dict()
    raw["scientific_value"] = 9  # a significance field must be unrepresentable
    with pytest.raises(psc.PublicSourceCheckError):
        psc.PublicSourceCheck.from_dict(raw)


def test_unsupported_outcome_is_refused():
    with pytest.raises(psc.PublicSourceCheckError):
        _available_check(outcome="looks_gone")


# --------------------------------------------------------------------------------------------
# run artifact
# --------------------------------------------------------------------------------------------


def _run(checks, **overrides):
    kwargs = dict(
        started_at="2026-07-14T00:00:00+00:00",
        completed_at="2026-07-14T00:00:05+00:00",
        run_nonce="nonce-0",
        adapter_id="public_source_intake",
        adapter_version="public_source_intake/1",
        policy_digest="ab" * 32,
        targets_ordered=("https://example.org/a",),
        checks=checks,
    )
    kwargs.update(overrides)
    return psc.build_run(**kwargs)


def test_run_validates_and_summarizes_its_checks():
    check = _available_check()
    run = _run([check])
    run.validate()
    assert run.run_id.startswith(psc.RUN_ID_PREFIX)
    assert run.run_status == "completed"
    assert run.completed_check_count == 1
    assert run.check_ids == (check.check_id,)
    assert run.outcome_counts.get(psc.AVAILABLE) == 1


def test_run_nonce_makes_identical_target_sets_distinct():
    check = _available_check()
    a = _run([check], run_nonce="nonce-a")
    b = _run([check], run_nonce="nonce-b")
    assert a.run_id != b.run_id  # the ledger is an event log, never idempotent


def test_run_artifact_digest_is_recomputed_on_read():
    run = _run([_available_check()])
    raw = run.to_dict()
    raw["artifact_digest"] = "0" * 64
    with pytest.raises(psc.PublicSourceCheckError):
        psc.PublicSourceCheckRun.from_dict(raw).validate()


def test_run_authority_pins_are_false():
    run = _run([_available_check()])
    for pin, value in psc.RUN_AUTHORITY_PINS.items():
        assert run.to_dict()[pin] is False, pin
        assert value is False


# --------------------------------------------------------------------------------------------
# targets file
# --------------------------------------------------------------------------------------------


def test_targets_file_loads(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": psc.TARGETS_SCHEMA_VERSION,
                "targets": [{"locator": "https://example.org/a"}],
            }
        )
    )
    targets = psc.load_targets(p)
    targets.validate()
    assert targets.canonical_locators() == ("https://example.org/a",)


def test_targets_file_rejects_unknown_keys(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": psc.TARGETS_SCHEMA_VERSION,
                "allowed_hosts": ["evil.example"],  # self-authorization is unrepresentable
                "targets": [{"locator": "https://example.org/a"}],
            }
        )
    )
    with pytest.raises(psc.TargetsError):
        psc.load_targets(p)


def test_targets_file_rejects_credentials_in_a_locator(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": psc.TARGETS_SCHEMA_VERSION,
                "targets": [{"locator": "https://user:pw@example.org/a"}],
            }
        )
    )
    with pytest.raises(psc.TargetsError):
        psc.load_targets(p)


def test_targets_file_rejects_duplicate_canonical_locators(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": psc.TARGETS_SCHEMA_VERSION,
                "targets": [
                    {"locator": "https://example.org/a"},
                    {"locator": "https://Example.ORG:443/a#frag"},  # same canonical locator
                ],
            }
        )
    )
    with pytest.raises(psc.TargetsError):
        psc.load_targets(p)


def test_targets_file_rejects_unknown_schema(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(json.dumps({"schema_version": "nope", "targets": []}))
    with pytest.raises(psc.TargetsError):
        psc.load_targets(p)


# --------------------------------------------------------------------------------------------
# structural guards: pure, network-free, no word for significance
# --------------------------------------------------------------------------------------------

_MODULE = Path(psc.__file__)
_FORBIDDEN_IMPORTS = {
    "socket",
    "ssl",
    "http",
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "aiohttp",
    "ftplib",
    "websockets",
    "sqlite3",
}

# Stems that would let a factual record grow a judgement. `confidence` is deliberately allowed.
_FORBIDDEN_STEMS = (
    "score",
    "rank",
    "importance",
    "priority",
    "severity",
    "relevance",
    "significan",
    "suspicious",
    "censor",
    "suppress",
    "disappear",
    "preservation_required",
    "scientific_value",
    "patentab",
    "probability",
    "novelty",
    "recommend",
)


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def test_module_imports_no_network_or_database():
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in _FORBIDDEN_IMPORTS:
        assert forbidden not in imported, forbidden


def test_module_has_no_word_for_significance():
    names = set()
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())
        elif isinstance(node, ast.Dict):
            # A significance concept smuggled in as a field name is a dict KEY, not an identifier —
            # so scan dict keys too. 03B's lesson: forbid the concept, not just an exact name.
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.add(key.value.lower())
    for stem in _FORBIDDEN_STEMS:
        assert not any(stem in name for name in names), stem


# --------------------------------------------------------------------------------------------
# schema mirrors are DOCUMENTATION; the drift test keeps them from becoming a confident lie
# --------------------------------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "schemas"


def _mirror(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_check_mirror_pins_the_contract():
    mirror = _mirror("public_source_check.v1.schema.json")
    assert set(mirror["required"]) == set(psc._CHECK_KEYS)
    assert set(mirror["properties"]) == set(psc._CHECK_KEYS)
    assert mirror["additionalProperties"] is False
    props = mirror["properties"]
    assert set(props["outcome"]["enum"]) == set(psc.OUTCOMES)
    assert props["schema_version"]["const"] == psc.CHECK_SCHEMA_VERSION
    assert props["source_class"]["const"] == psc.SOURCE_CLASS
    assert props["privacy_class"]["const"] == psc.PRIVACY_CLASS
    for pin in (*psc.CHECK_AUTHORITY_PINS, "publication_authorized", "disclosure_authorized"):
        assert props[pin]["const"] is False, pin


def test_run_mirror_pins_the_contract():
    mirror = _mirror("public_source_check_run.v1.schema.json")
    assert set(mirror["required"]) == set(psc._RUN_KEYS)
    assert set(mirror["properties"]) == set(psc._RUN_KEYS)
    assert mirror["additionalProperties"] is False
    props = mirror["properties"]
    assert props["run_status"]["const"] == "completed"
    assert props["schema_version"]["const"] == psc.RUN_SCHEMA_VERSION
    for pin in psc.RUN_AUTHORITY_PINS:
        assert props[pin]["const"] is False, pin


def test_targets_mirror_pins_the_contract():
    mirror = _mirror("public_source_targets.v1.schema.json")
    assert set(mirror["required"]) == {"schema_version", "targets"}
    assert mirror["additionalProperties"] is False
    assert mirror["properties"]["schema_version"]["const"] == psc.TARGETS_SCHEMA_VERSION
    item = mirror["properties"]["targets"]["items"]
    assert set(item["required"]) == {"locator"}
    assert item["additionalProperties"] is False
