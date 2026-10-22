"""Network isolation (LHP-INTEL-04B).

The doctrine is no longer "the fact-first pipeline makes no network calls"; it is now the precise
"network access is forbidden everywhere except the one explicitly audited public-source intake
adapter". This test pins the fact-first module set explicitly (so an unrelated legacy collector's
`httpx` cannot make it fail), proves none of them import a network-capable library, and proves that
`public_source_intake` — and only it — does.
"""

from __future__ import annotations

import ast
from pathlib import Path

from modules import (
    content_identity,
    db,
    document_intake,
    observation_intake,
    observation_normalizer,
    observation_query,
    observation_sources,
    observations,
    observed_source_exporter,
    observed_source_reference,
    pattern_candidate,
    pattern_candidate_service,
    pattern_candidate_specs,
    public_source_check,
    public_source_check_service,
    public_source_intake,
    repository_content_recurrence,
    repository_intake,
    scientific_source_disappearance,
)

# Pinned explicitly. A module is here because it is part of the deterministic fact-first pipeline;
# legacy collectors (patent/arxiv/sec/flight/adsb/narrative/wayback/nostr) are NOT here,
# because they legitimately use the network and are not part of this boundary.
FACT_FIRST_MODULES = (
    observations,
    repository_intake,
    document_intake,
    content_identity,
    observation_normalizer,
    observation_query,
    observation_sources,
    observation_intake,
    observed_source_exporter,
    observed_source_reference,
    pattern_candidate,
    pattern_candidate_specs,
    repository_content_recurrence,
    scientific_source_disappearance,
    pattern_candidate_service,
    public_source_check,
    public_source_check_service,
    db,
)

# Top-level modules that can open a socket. `urllib.parse` (pure string work) is absent:
# it is not `urllib.request`, and the contract module uses it to canonicalize a locator.
_FORBIDDEN_TOPLEVEL = {"socket", "ssl", "http", "httpx", "requests", "urllib3", "aiohttp", "ftplib"}
_FORBIDDEN_EXACT = {"urllib.request", "http.client"}


def _imported_names(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _network_imports(names: set[str]) -> set[str]:
    hits: set[str] = set()
    for name in names:
        if name in _FORBIDDEN_EXACT or name.split(".")[0] in _FORBIDDEN_TOPLEVEL:
            hits.add(name)
    return hits


def test_no_fact_first_module_imports_a_network_library():
    for module in FACT_FIRST_MODULES:
        hits = _network_imports(_imported_names(module))
        assert not hits, f"{module.__name__} imports network libraries: {sorted(hits)}"


def test_the_adapter_is_the_sole_network_capable_module():
    # If this ever passes with an empty set, the adapter stopped being able to reach the network at
    # all — which would mean the isolation guard above is proving nothing.
    hits = _network_imports(_imported_names(public_source_intake))
    assert {"socket", "ssl"} <= hits
    assert "http.client" in hits


def test_the_guard_would_catch_a_regression():
    # A live sanity check on the matcher itself: were any fact-first module to grow `import httpx`,
    # `_network_imports` must flag it.
    assert _network_imports({"httpx"}) == {"httpx"}
    assert _network_imports({"socket"}) == {"socket"}
    assert _network_imports({"urllib.parse"}) == set()  # pure, allowed
    assert _network_imports({"json", "hashlib", "sqlite3"}) == set()
