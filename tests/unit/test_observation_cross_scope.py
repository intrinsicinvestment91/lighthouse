"""LHP-INTEL-02B — cross-scope fact discovery.

02A let an operator see observation *sets*. It could not answer the question the normalized model
was *built* to answer: **"where else have I built this?"** 01B gave `technology:docker` a global
identity precisely so two repositories could meet on it; 01C gave a document an identity derived
from its **bytes** precisely so the same file in two places would be one entity observed twice.
Both joins have been sitting in the store, unreachable, ever since.

This is the layer that reaches them — and, just as deliberately, refuses to say what they mean.

The claims, each pinned rather than asserted in prose:

* **An occurrence is an entity row, not a relationship.** `normalized_entities.scope_id` is a stored
  column, so "directly observed in this scope" is a fact the store already holds. It has to be:
  a document scope has *zero* relationships (01C), and a repository entity is only ever a
  relationship *subject* — so a relationship-only model would answer "the same document lives in
  two places" with silence.
* **Evidence is verbatim, and it lives where it is stored.** No aggregate confidence (an entity is
  built only from *determined* observations, so a scalar would be a derived constant `1.0` forever —
  a number in exactly the slot where significance likes to hide). No scalar relationship (an
  occurrence carries zero, one, or several). And no top-level `value` or `digest`: both **vary per
  occurrence**, because an entity's digest covers the artifact it came from, and the same document
  bytes can be filed under two different names.
* **`shared` is exact set intersection, never union**, and never similarity. An entity missing from
  one requested scope is not shared.
* **Nothing is ranked.** Shared entities sort by identity. By count would be "the most common
  technology" — the first ranking this system ever built, arriving by accident.
* **Entity ids are exact; scope ids may be abbreviated.** Scope ids are uniformly SHA-256. Entity
  ids are not (`technology:docker`, `language:Python`, `remote:<url>`), so a prefix rule for only
  the digest-tailed types would be a second identifier system, and `technology:doc` matching
  `technology:docker` is fuzzy matching wearing a hat.
* **An unknown scope fails closed.** Returning an empty intersection would claim "these two share
  nothing" about a scope that was never observed.
"""

import ast
import dataclasses
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from modules import db, observation_query
from modules.document_intake import observe_document
from modules.observation_normalizer import (
    normalize_document_observation,
    normalize_repository_observation,
)
from modules.observation_query import (
    MAX_LIMIT,
    MAX_SCOPE_IDS,
    MIN_ID_PREFIX,
    SENSORS,
    AmbiguousScopeId,
    ObservationQueryError,
    ScopeNotFound,
    entity_occurrences,
    resolve_scope_id,
    shared_entities,
)
from modules.repository_intake import observe_repository
from tests.unit.test_observation_query import _FORBIDDEN, file_digest
from tests.unit.test_repository_intake import _GIT_ENV, make_repo

# -- the fixture, and why every scope in it is there ------------------------------------------
#
#   alpha   Dockerfile + pyproject.toml + main.py + README.md   the rich one
#   beta    Dockerfile + main.py + LICENSE                      shares docker + Python with alpha
#   delta   Dockerfile + main.py                                makes a THREE-scope intersection
#   gamma   notes.txt                                           shares NOTHING — the honest zero
#   outer   README.md, with `inner` nested inside it            the repository↔repository join
#   inner   main.py                                             observed directly AND as a nestee
#   spec.md, and copies/renamed.md with the SAME BYTES          the document↔document join
#
# alpha is then CHANGED and re-observed, so one scope holds two artifacts — which is how a duplicate
# provenance row gets into the store, and it must not be collapsed away.
#
# The copy is deliberately RENAMED. Same bytes, so it is the same entity; different name, so its
# stored `value` differs between the two occurrences. That is the sharpest available proof that
# `value` belongs on the occurrence and not on the entity.


SHARED_REMOTE = "https://example.invalid/shared.git"


def git(root: Path, *args: str) -> None:
    # Hermetic identity (never the developer's ambient git config) — the same `_GIT_ENV`
    # make_repo commits through, so a clean CI runner with no user.name/user.email does not
    # fail these commits with exit 128.
    subprocess.run(["git", "-C", str(root), *args], env=_GIT_ENV, check=True, capture_output=True)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "store.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture
def scopes(tmp_path: Path, store: Path) -> dict:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    alpha = make_repo(
        workspace / "alpha",
        {
            "Dockerfile": "FROM scratch\n",
            "pyproject.toml": "[project]\nname = 'alpha'\n",
            "main.py": "print(1)\n",
            "README.md": "# alpha\n",
        },
    )
    beta = make_repo(
        workspace / "beta",
        {"Dockerfile": "FROM scratch\n", "main.py": "print(2)\n", "LICENSE": "MIT\n"},
    )
    # A remote that beta has from the start and alpha only acquires later. It is the one shared
    # entity whose occurrence count DIFFERS from the others' — without it, every shared entity would
    # have exactly three occurrences and the ordering test could not tell identity order apart from
    # count order. A fixture that cannot distinguish them cannot pin them (the 01C lesson).
    git(beta, "remote", "add", "origin", SHARED_REMOTE)
    delta = make_repo(
        workspace / "delta", {"Dockerfile": "FROM scratch\n", "main.py": "print(3)\n"}
    )
    gamma = make_repo(workspace / "gamma", {"notes.txt": "nothing shared\n"})
    outer = make_repo(workspace / "outer", {"README.md": "# outer\n"})
    inner = make_repo(workspace / "outer" / "inner", {"main.py": "print(4)\n"})

    spec = workspace / "spec.md"
    spec.write_text("# spec\n\nthe body of the document\n", encoding="utf-8")
    (workspace / "copies").mkdir()
    renamed = workspace / "copies" / "renamed.md"
    shutil.copy2(spec, renamed)

    roots = (str(workspace),)
    sets = {
        name: normalize_repository_observation(observe_repository(str(path), allowed_roots=roots))
        for name, path in (
            ("alpha", alpha),
            ("beta", beta),
            ("delta", delta),
            ("gamma", gamma),
            ("outer", outer),
            ("inner", inner),
        )
    }
    for name, path in (("spec", spec), ("renamed", renamed)):
        sets[name] = normalize_document_observation(
            observe_document(str(path), allowed_roots=roots)
        )

    db.init_observation_tables()
    for result in sets.values():
        assert db.store_normalized_observation(result) is True

    # The same scope, observed a second time after it changed: one scope_id, two artifacts.
    (alpha / "extra.py").write_text("print(5)\n", encoding="utf-8")
    git(alpha, "add", "-A")
    git(alpha, "commit", "--quiet", "-m", "again")
    git(alpha, "remote", "add", "origin", SHARED_REMOTE)
    sets["alpha_again"] = normalize_repository_observation(
        observe_repository(str(alpha), allowed_roots=roots)
    )
    assert db.store_normalized_observation(sets["alpha_again"]) is True
    assert sets["alpha_again"].scope_id == sets["alpha"].scope_id
    assert sets["alpha_again"].source_artifact_digest != sets["alpha"].source_artifact_digest

    scope_ids = {name: result.scope_id for name, result in sets.items()}

    def only(result, entity_type: str) -> str:
        """The one entity of this type. Named explicitly rather than taken off the front of a set:
        a document set used to hold exactly one entity, so `next(iter(...))` happened to be stable.
        03A gave it a second (the neutral `content:` identity) and the arbitrary pick became a coin
        toss — a fixture that silently changes what it is testing."""
        found = [e.entity_id for e in result.entities if e.entity_type == entity_type]
        assert len(found) == 1, f"expected exactly one {entity_type} entity, found {len(found)}"
        return found[0]

    fixture = {
        "workspace": workspace,
        "sets": sets,
        "scope": scope_ids,
        "docker": "technology:docker",
        "python": "language:Python",
        "document": only(sets["spec"], "document"),
        "document_content": only(sets["spec"], "content"),
        "inner_repository": f"repository:{scope_ids['inner']}",
        "alpha_repository": f"repository:{scope_ids['alpha']}",
    }

    # A fixture is only worth what it can distinguish (the 01C lesson: a fixture that cannot fail is
    # a test that cannot pass for the right reason). Assert the joins are real BEFORE using them.
    assert fixture["docker"] in entity_ids(sets["alpha"]) & entity_ids(sets["beta"])
    assert fixture["docker"] in entity_ids(sets["delta"])
    assert fixture["python"] in entity_ids(sets["alpha"]) & entity_ids(sets["beta"])
    assert not entity_ids(sets["alpha"]) & entity_ids(sets["gamma"])
    assert fixture["inner_repository"] in entity_ids(sets["outer"]) & entity_ids(sets["inner"])
    assert entity_ids(sets["spec"]) == entity_ids(sets["renamed"])
    assert scope_ids["spec"] != scope_ids["renamed"]
    return fixture


def entity_ids(result) -> set[str]:
    return {entity.entity_id for entity in result.entities}


def commit_entity_id(result) -> str:
    """The commit of a single-commit repository is BOTH its root commit and its head commit — so one
    entity carries two supporting observations and two relationships, with no fixture gymnastics."""
    return next(e.entity_id for e in result.entities if e.entity_type == "commit")


def stored_observations(result) -> dict[str, dict]:
    return {item["observation_type"]: item for item in result.source_artifact["observations"]}


def forge_scope_row(source_scope_id: str, forged_scope_id: str) -> None:
    """Copy an observation_sets row under a different scope_id — the only way to make two scope ids
    share an 8-character prefix, since real SHA-256 digests will not collide there."""
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(
            conn.execute(
                "SELECT * FROM observation_sets WHERE scope_id = ? LIMIT 1", (source_scope_id,)
            ).fetchone()
        )
        row["scope_id"] = forged_scope_id
        row["normalization_digest"] = "f" * 64
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO observation_sets ({columns}) VALUES ({placeholders})", tuple(row.values())
        )


def keys_of(payload) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(key)
            found |= keys_of(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= keys_of(item)
    return found


# -- where: every scope in which an exact entity was directly observed -------------------------


def test_a_global_entity_observed_in_one_scope(scopes: dict):
    result = entity_occurrences("technology:python")

    assert result["entity"] == {"entity_id": "technology:python", "entity_type": "technology"}
    assert result["occurrence_count"] == 2  # alpha alone — but alpha was observed twice
    assert {o["scope_id"] for o in result["occurrences"]} == {scopes["scope"]["alpha"]}
    assert {o["value"] for o in result["occurrences"]} == {"python"}


def test_a_global_entity_observed_in_three_scopes(scopes: dict):
    result = entity_occurrences(scopes["docker"])

    assert {o["scope_id"] for o in result["occurrences"]} == {
        scopes["scope"]["alpha"],
        scopes["scope"]["beta"],
        scopes["scope"]["delta"],
    }
    # alpha contributes TWO occurrences — it was observed twice — so the count exceeds the scopes.
    assert result["occurrence_count"] == 4


def test_a_global_entity_spans_every_scope_that_uses_it(scopes: dict):
    result = entity_occurrences(scopes["python"])

    assert {o["scope_id"] for o in result["occurrences"]} == {
        scopes["scope"]["alpha"],
        scopes["scope"]["beta"],
        scopes["scope"]["delta"],
        scopes["scope"]["inner"],
    }


def test_a_repository_observed_directly_and_as_a_nested_one_is_a_single_entity(scopes: dict):
    """The join 01B built global repository identity for: a nested clone and the repository itself
    resolve to one entity with two provenances, because a repository's id is the digest of its own
    canonical path — not of whichever repository happened to be looking at it."""
    result = entity_occurrences(scopes["inner_repository"])

    assert {o["scope_id"] for o in result["occurrences"]} == {
        scopes["scope"]["outer"],
        scopes["scope"]["inner"],
    }
    within_outer = next(
        o for o in result["occurrences"] if o["scope_id"] == scopes["scope"]["outer"]
    )
    assert [r["relationship_type"] for r in within_outer["relationships"]] == [
        "contains_repository"
    ]


def test_the_same_document_at_two_paths_is_one_entity_in_two_scopes(scopes: dict):
    """The join documents exist for. Identity is the digest of the BYTES, so a copy is not a second
    document — it is the same document, observed somewhere else."""
    result = entity_occurrences(scopes["document"])

    assert result["occurrence_count"] == 2
    assert {o["scope_id"] for o in result["occurrences"]} == {
        scopes["scope"]["spec"],
        scopes["scope"]["renamed"],
    }
    assert {o["scope_type"] for o in result["occurrences"]} == {"document"}
    assert {o["scope_path"] for o in result["occurrences"]} == {
        str(scopes["workspace"] / "spec.md"),
        str(scopes["workspace"] / "copies" / "renamed.md"),
    }


def test_one_entity_can_carry_a_different_value_in_each_occurrence(scopes: dict):
    """The same bytes, filed under two names. `value` is a stored fact ABOUT AN OCCURRENCE —
    hoisting it onto the entity would force a choice between two true answers."""
    result = entity_occurrences(scopes["document"])

    assert "value" not in result["entity"]
    assert {o["value"] for o in result["occurrences"]} == {"spec.md", "renamed.md"}
    assert {o["scope_label"] for o in result["occurrences"]} == {"spec.md", "renamed.md"}


def test_one_entity_can_carry_a_different_digest_in_each_occurrence(scopes: dict):
    """An entity's digest covers the artifact it was derived from, so the same entity observed in
    two artifacts has two digests. There is no such thing as "the" digest of an entity."""
    result = entity_occurrences(scopes["docker"])

    assert "digest" not in result["entity"]
    digests = {o["entity_digest"] for o in result["occurrences"]}
    assert len(digests) == result["occurrence_count"]
    assert all(len(digest) == 64 for digest in digests)


def test_a_document_occurrence_carries_no_relationships(scopes: dict):
    """`relationships: []` is a fact, not a gap. 01C emits none, deliberately — and a contract that
    forced a scalar here would have to invent a null and call it evidence."""
    result = entity_occurrences(scopes["document"])

    for occurrence in result["occurrences"]:
        assert occurrence["relationships"] == []


def test_a_file_entity_can_never_appear_in_a_second_scope(scopes: dict):
    """`file:<scope_id>:<name>` embeds its scope, so two repositories that both contain a Dockerfile
    do NOT meet on the file — they meet on `technology:docker`. An operator who does not know that
    will think `shared` is broken, so it is pinned here rather than left to be discovered."""
    alpha_dockerfile = f"file:{scopes['scope']['alpha']}:Dockerfile"
    beta_dockerfile = f"file:{scopes['scope']['beta']}:Dockerfile"
    assert alpha_dockerfile != beta_dockerfile

    result = entity_occurrences(alpha_dockerfile)
    assert {o["scope_id"] for o in result["occurrences"]} == {scopes["scope"]["alpha"]}


def test_an_unknown_but_well_formed_entity_id_is_an_empty_result_not_an_error(scopes: dict):
    """ "That is not a word I know" and "Lighthouse knows nothing about that" are different claims.
    `technology:rust` is a perfectly well-formed id; the store simply has no occurrence of it."""
    result = entity_occurrences("technology:rust")

    assert result["occurrence_count"] == 0
    assert result["occurrences"] == []
    assert result["entity"]["entity_id"] == "technology:rust"


@pytest.mark.parametrize(
    "value",
    ["docker", "", "   ", "technology:", ":docker", "bogus:docker", "TECHNOLOGY:docker", None, 7],
)
def test_a_malformed_entity_id_is_rejected(scopes: dict, value):
    with pytest.raises(ObservationQueryError):
        entity_occurrences(value)


def test_an_entity_id_may_contain_colons_in_its_value(scopes: dict):
    """A remote's id is `remote:<url>`, and a URL has colons in it. Splitting on anything but the
    FIRST colon would make every git remote unaddressable."""
    result = entity_occurrences("remote:https://example.invalid/x.git")

    assert result["entity"]["entity_type"] == "remote"
    assert result["occurrence_count"] == 0


def test_no_prefix_of_an_entity_id_ever_resolves(scopes: dict):
    """Exact, always. Not for value-based ids (`technology:dock` is fuzzy matching wearing a hat),
    and not for digest-tailed ones either (a type-conditional rule is a second identifier system).
    """
    assert entity_occurrences("technology:dock")["occurrence_count"] == 0
    assert entity_occurrences(scopes["inner_repository"][:30])["occurrence_count"] == 0
    assert entity_occurrences(scopes["document"][:30])["occurrence_count"] == 0


def test_the_canonical_full_entity_id_is_always_emitted(scopes: dict):
    result = entity_occurrences(scopes["inner_repository"])

    assert result["entity"]["entity_id"] == scopes["inner_repository"]
    assert len(result["entity"]["entity_id"].split(":", 1)[1]) == 64


def test_occurrences_are_ordered_deterministically_and_never_by_count(scopes: dict):
    result = entity_occurrences(scopes["docker"])

    keys = [
        (o["scope_id"], o["source_artifact_digest"], o["derivation_version"])
        for o in result["occurrences"]
    ]
    assert keys == sorted(keys)
    assert result == entity_occurrences(scopes["docker"])


def test_duplicate_provenance_rows_are_preserved_not_collapsed(scopes: dict):
    """One scope, two artifacts, one entity. The unique index says those are two rows; collapsing
    them into one occurrence would silently destroy a provenance the operator cannot get back."""
    result = entity_occurrences(scopes["docker"])

    within_alpha = [o for o in result["occurrences"] if o["scope_id"] == scopes["scope"]["alpha"]]
    assert len(within_alpha) == 2
    assert {o["source_artifact_digest"] for o in within_alpha} == {
        scopes["sets"]["alpha"].source_artifact_digest,
        scopes["sets"]["alpha_again"].source_artifact_digest,
    }


def test_every_occurrence_preserves_full_provenance(scopes: dict):
    result = entity_occurrences(scopes["docker"])

    for occurrence in result["occurrences"]:
        for field in (
            "scope_id",
            "scope_type",
            "scope_label",
            "scope_path",
            "value",
            "entity_digest",
            "source_artifact_digest",
            "derivation_version",
            "observed_at",
            "sensors",
            "supporting_observations",
        ):
            assert field in occurrence, field
            assert occurrence[field] not in (None, "", []), field
        assert "relationships" in occurrence
        assert len(occurrence["source_artifact_digest"]) == 64
        assert len(occurrence["entity_digest"]) == 64
        assert occurrence["derivation_version"] == "observation_normalizer/2"


def test_supporting_observations_are_read_verbatim_from_the_stored_artifact(scopes: dict):
    """Several supporting observations on one occurrence, each with its own provenance. The commit
    of a single-commit repository is BOTH its root commit and its head commit — two observations,
    two facts, and no reason to pick one of them."""
    commit = commit_entity_id(scopes["sets"]["beta"])
    occurrence = next(
        o
        for o in entity_occurrences(commit)["occurrences"]
        if o["scope_id"] == scopes["scope"]["beta"]
    )

    assert [s["observation_type"] for s in occurrence["supporting_observations"]] == [
        "git.head_commit",
        "git.root_commits",
    ]
    assert occurrence["sensors"] == ["git_metadata"]

    stored = stored_observations(scopes["sets"]["beta"])
    for supporting in occurrence["supporting_observations"]:
        original = stored[supporting["observation_type"]]
        assert supporting["sensor"] == original["source"]
        assert supporting["confidence"] == original["confidence"]
        assert supporting["observed_at"] == original["observed_at"]


def test_individual_confidences_are_preserved_and_never_aggregated(scopes: dict):
    commit = commit_entity_id(scopes["sets"]["beta"])
    occurrence = entity_occurrences(commit)["occurrences"][0]

    assert len(occurrence["supporting_observations"]) == 2
    assert [s["confidence"] for s in occurrence["supporting_observations"]] == [1.0, 1.0]
    assert "confidence" not in occurrence


def test_an_occurrence_can_carry_several_relationships(scopes: dict):
    """One entity, two predicates. A scalar `relationship_type` would have had to choose one."""
    commit = commit_entity_id(scopes["sets"]["beta"])
    occurrence = entity_occurrences(commit)["occurrences"][0]

    assert sorted(r["relationship_type"] for r in occurrence["relationships"]) == [
        "has_head_commit",
        "has_root_commit",
    ]
    for relationship in occurrence["relationships"]:
        assert len(relationship["relationship_digest"]) == 64
        assert relationship["relationship_id"].startswith("rel:")
        assert relationship["source_artifact_digest"] == occurrence["source_artifact_digest"]
        assert relationship["derivation_version"] == occurrence["derivation_version"]


def test_relationships_are_deterministically_ordered(scopes: dict):
    commit = commit_entity_id(scopes["sets"]["beta"])
    occurrence = entity_occurrences(commit)["occurrences"][0]

    keys = [
        (r["relationship_id"], r["source_artifact_digest"]) for r in occurrence["relationships"]
    ]
    assert keys == sorted(keys)


def test_a_relationship_is_never_attributed_to_the_wrong_artifact(scopes: dict):
    """alpha was observed twice, so `technology:docker` has two occurrences in one scope — and each
    must carry the relationships of ITS OWN artifact, never of the other's."""
    result = entity_occurrences(scopes["docker"])

    for occurrence in result["occurrences"]:
        for relationship in occurrence["relationships"]:
            assert relationship["source_artifact_digest"] == occurrence["source_artifact_digest"]


def test_sensors_are_real_and_never_a_collector_name(scopes: dict):
    """A repository is the SUBJECT of an observation, not a sensor. `repository_intake` is a module.
    The store holds the real thing, and it is one of exactly three."""
    for entity_id in (scopes["docker"], scopes["document"], scopes["inner_repository"]):
        for occurrence in entity_occurrences(entity_id)["occurrences"]:
            assert set(occurrence["sensors"]) <= SENSORS
            assert occurrence["sensors"] == sorted(occurrence["sensors"])
            assert "repository_intake" not in occurrence["sensors"]
            assert "document_intake" not in occurrence["sensors"]
            assert "source" not in occurrence


def test_the_subject_entity_carries_both_sensors(scopes: dict):
    """The repository entity is derived from every determined observation, so its provenance spans
    the filesystem AND git — which is exactly why a sensor is per-occurrence, not per-scope."""
    occurrence = entity_occurrences(scopes["alpha_repository"])["occurrences"][0]

    assert occurrence["sensors"] == ["filesystem", "git_metadata"]


def test_no_scalar_confidence_or_relationship_field_appears_anywhere(scopes: dict):
    for entity_id in (scopes["docker"], scopes["document"], scopes["inner_repository"]):
        for occurrence in entity_occurrences(entity_id)["occurrences"]:
            assert "confidence" not in occurrence
            assert "relationship_type" not in occurrence
            assert "relationship_digest" not in occurrence
            assert isinstance(occurrence["relationships"], list)


def test_the_limit_bounds_the_occurrences(scopes: dict):
    """The 02A limit contract, unchanged: an over-large limit is CLAMPED to the ceiling (the caller
    cannot talk the surface into an unbounded read), and a nonsensical one is refused."""
    assert len(entity_occurrences(scopes["docker"], limit=1)["occurrences"]) == 1
    assert entity_occurrences(scopes["docker"], limit=MAX_LIMIT + 1) == entity_occurrences(
        scopes["docker"], limit=MAX_LIMIT
    )
    for refused in (0, -1, "two", 1.5, True):
        with pytest.raises(ObservationQueryError):
            entity_occurrences(scopes["docker"], limit=refused)


# -- shared: the exact entities directly observed in EVERY requested scope ----------------------


def test_two_scopes_with_several_shared_entities(scopes: dict):
    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])

    ids = {entity["entity_id"] for entity in result["shared_entities"]}
    assert scopes["docker"] in ids
    assert scopes["python"] in ids
    assert result["shared_entity_count"] == len(result["shared_entities"])


def test_two_scopes_with_no_shared_entities(scopes: dict):
    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["gamma"]])

    assert result["shared_entity_count"] == 0
    assert result["shared_entities"] == []
    assert {s["scope_id"] for s in result["scopes"]} == {
        scopes["scope"]["alpha"],
        scopes["scope"]["gamma"],
    }


def test_an_entity_in_only_one_scope_is_excluded(scopes: dict):
    """alpha has a pyproject.toml, so it alone carries `technology:python`. Intersection = ALL."""
    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])

    ids = {entity["entity_id"] for entity in result["shared_entities"]}
    assert "technology:python" in entity_ids(scopes["sets"]["alpha"])
    assert "technology:python" not in entity_ids(scopes["sets"]["beta"])
    assert "technology:python" not in ids


def test_intersection_means_all_scopes_never_union(scopes: dict):
    """The mutation this exists for: an implementation that unioned would return alpha's entire
    entity set here, because gamma shares nothing with it. Exactly zero is the right answer."""
    alpha_only = entity_ids(scopes["sets"]["alpha"])
    gamma_only = entity_ids(scopes["sets"]["gamma"])
    assert len(alpha_only | gamma_only) > 10  # a union would be very obviously non-empty

    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["gamma"]])
    assert result["shared_entity_count"] == 0


def test_a_three_scope_intersection_means_all_three(scopes: dict):
    three = shared_entities(
        [scopes["scope"]["alpha"], scopes["scope"]["beta"], scopes["scope"]["delta"]]
    )
    two = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])

    ids_three = {entity["entity_id"] for entity in three["shared_entities"]}
    ids_two = {entity["entity_id"] for entity in two["shared_entities"]}
    assert scopes["docker"] in ids_three
    assert ids_three <= ids_two  # adding a scope can only ever narrow an intersection
    assert len(three["scopes"]) == 3


def test_adding_a_scope_that_shares_nothing_empties_the_intersection(scopes: dict):
    result = shared_entities(
        [scopes["scope"]["alpha"], scopes["scope"]["beta"], scopes["scope"]["gamma"]]
    )

    assert result["shared_entity_count"] == 0


def test_the_nested_repository_is_shared_between_outer_and_inner(scopes: dict):
    result = shared_entities([scopes["scope"]["outer"], scopes["scope"]["inner"]])

    assert [entity["entity_id"] for entity in result["shared_entities"]] == [
        scopes["inner_repository"]
    ]


def test_every_shared_entity_carries_evidence_for_every_requested_scope(scopes: dict):
    requested = [scopes["scope"]["alpha"], scopes["scope"]["beta"], scopes["scope"]["delta"]]
    result = shared_entities(requested)

    assert result["shared_entities"]
    for entity in result["shared_entities"]:
        covered = {occurrence["scope_id"] for occurrence in entity["occurrences"]}
        assert covered == set(requested), entity["entity_id"]


def test_shared_occurrences_preserve_duplicate_provenance(scopes: dict):
    """alpha holds two artifacts, so a shared entity has two occurrences there. Both are real."""
    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])

    docker = next(e for e in result["shared_entities"] if e["entity_id"] == scopes["docker"])
    within_alpha = [o for o in docker["occurrences"] if o["scope_id"] == scopes["scope"]["alpha"]]
    assert len(within_alpha) == 2


def test_shared_entities_are_ordered_by_identity_never_by_count(scopes: dict):
    """Ordering these by occurrence count would be "the most common technology" — a ranking, and the
    first this system ever built. They sort by (entity_type, entity_id), which is total."""
    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])

    keys = [(e["entity_type"], e["entity_id"]) for e in result["shared_entities"]]
    assert keys == sorted(keys)

    counts = [len(e["occurrences"]) for e in result["shared_entities"]]
    assert counts != sorted(counts, reverse=True), (
        "the fixture must be able to tell identity order apart from count order"
    )


def test_the_intersection_is_computed_before_the_limit(scopes: dict):
    """The mutation this exists for: limiting each scope's entities BEFORE intersecting them would
    return nothing here, because alpha's first entity and beta's first entity are different."""
    full = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])
    assert full["shared_entity_count"] > 1

    limited = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]], limit=1)
    assert limited["shared_entity_count"] == 1
    assert limited["shared_entities"][0] == full["shared_entities"][0]


def test_a_single_scope_is_not_an_intersection(scopes: dict):
    with pytest.raises(ObservationQueryError, match="two"):
        shared_entities([scopes["scope"]["alpha"]])
    with pytest.raises(ObservationQueryError):
        shared_entities([])


def test_a_duplicate_scope_is_rejected(scopes: dict):
    with pytest.raises(ObservationQueryError, match="twice"):
        shared_entities([scopes["scope"]["alpha"], scopes["scope"]["alpha"]])


def test_a_prefix_that_resolves_to_an_already_requested_scope_is_rejected(scopes: dict):
    with pytest.raises(ObservationQueryError, match="twice"):
        shared_entities([scopes["scope"]["alpha"], scopes["scope"]["alpha"][:12]])


def test_too_many_scopes_are_rejected(scopes: dict):
    with pytest.raises(ObservationQueryError, match="at most"):
        shared_entities([f"{i:064x}" for i in range(MAX_SCOPE_IDS + 1)])


def test_shared_results_are_deterministic_and_order_independent(scopes: dict):
    pair = [scopes["scope"]["alpha"], scopes["scope"]["beta"]]

    assert shared_entities(pair) == shared_entities(pair)
    assert shared_entities(pair) == shared_entities(list(reversed(pair)))


# -- scope identity: digest-based, so 02A's abbreviation rule applies ---------------------------


def test_a_scope_prefix_resolves(scopes: dict):
    full = scopes["scope"]["alpha"]

    assert resolve_scope_id(full[:MIN_ID_PREFIX]) == full
    assert resolve_scope_id(full) == full


def test_a_short_scope_prefix_is_rejected(scopes: dict):
    with pytest.raises(ObservationQueryError, match=str(MIN_ID_PREFIX)):
        resolve_scope_id(scopes["scope"]["alpha"][: MIN_ID_PREFIX - 1])


def test_a_non_hex_scope_id_is_rejected(scopes: dict):
    with pytest.raises(ObservationQueryError):
        resolve_scope_id("/home/you/Projects/alpha")


def test_an_unknown_scope_fails_closed_rather_than_returning_an_empty_intersection(scopes: dict):
    """The most dangerous answer this surface could give: "these two repositories share nothing",
    about a scope that was never observed. An intersection with a scope that does not exist is not
    empty — it is unanswerable, and it has to say so."""
    unknown = "0" * 64

    with pytest.raises(ScopeNotFound):
        resolve_scope_id(unknown)
    with pytest.raises(ScopeNotFound):
        shared_entities([scopes["scope"]["alpha"], unknown])


def test_an_ambiguous_scope_prefix_fails_closed_and_names_every_candidate(scopes: dict):
    alpha = scopes["scope"]["alpha"]
    twin = alpha[:12] + ("0" if alpha[12] != "0" else "1") * (64 - 12)
    forge_scope_row(alpha, twin)

    with pytest.raises(AmbiguousScopeId) as raised:
        resolve_scope_id(alpha[:12])

    assert set(raised.value.candidates) == {alpha, twin}
    assert raised.value.prefix == alpha[:12]
    with pytest.raises(AmbiguousScopeId):
        shared_entities([alpha[:12], scopes["scope"]["beta"]])


def test_the_canonical_scope_identity_is_always_emitted(scopes: dict):
    result = shared_entities([scopes["scope"]["alpha"][:12], scopes["scope"]["beta"][:12]])

    for scope in result["scopes"]:
        assert len(scope["scope_id"]) == 64
        assert scope["scope_type"] in {"repository", "document"}
        assert scope["scope_label"] and scope["scope_path"]
    for entity in result["shared_entities"]:
        for occurrence in entity["occurrences"]:
            assert len(occurrence["scope_id"]) == 64


def test_a_scope_label_is_a_stored_fact_and_never_a_basename(scopes: dict):
    """`scope_label` is the subject entity's observed `value` — `repository.name` / `document.name`.
    Deriving it from the path would be inventing identity out of a display string."""
    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])

    labels = {scope["scope_id"]: scope["scope_label"] for scope in result["scopes"]}
    assert labels[scopes["scope"]["alpha"]] == "alpha"
    assert labels[scopes["scope"]["beta"]] == "beta"


# -- read-only, an empty store, and a store that does not exist ---------------------------------


def test_cross_scope_reads_against_no_database_create_no_database(store: Path):
    assert not store.exists()

    assert entity_occurrences("technology:docker")["occurrence_count"] == 0
    with pytest.raises(ScopeNotFound):
        shared_entities(["a" * 64, "b" * 64])

    assert not store.exists(), "a read created a database"


def test_cross_scope_reads_against_an_empty_store_answer_zero(store: Path):
    db.init_observation_tables()

    assert entity_occurrences("technology:docker") == {
        "entity": {"entity_id": "technology:docker", "entity_type": "technology"},
        "occurrence_count": 0,
        "occurrences": [],
        "scan_truncated": False,
    }


def test_cross_scope_reads_leave_the_database_byte_identical(scopes: dict):
    before = file_digest(Path(db.DB_PATH))

    entity_occurrences(scopes["docker"])
    entity_occurrences(scopes["document"])
    shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])
    resolve_scope_id(scopes["scope"]["alpha"][:12])

    assert file_digest(Path(db.DB_PATH)) == before


def test_the_cross_scope_reads_use_a_connection_that_refuses_to_write(scopes: dict):
    conn = db.get_readonly_connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM normalized_entities")
    finally:
        conn.close()


# -- adversarial: a tampered store fails closed, loudly ----------------------------------------


def test_an_injected_score_is_rejected_on_the_way_out_of_where(scopes: dict):
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_entities SET attributes_json = ? WHERE entity_id = ?",
            (json.dumps({"score": 0.99}), scopes["alpha_repository"]),
        )

    with pytest.raises(ObservationQueryError):
        entity_occurrences(scopes["alpha_repository"])


def test_a_restamped_forged_ranking_is_rejected_by_the_closed_vocabulary(scopes: dict):
    """The forger does the arithmetic too: content edited, digest recomputed, internally consistent.
    So the digest recompute cannot be what fires here — the closed attribute vocabulary is the gate,
    and it has no word for `importance`."""
    subject = next(
        e for e in scopes["sets"]["alpha"].entities if e.entity_id == scopes["alpha_repository"]
    )
    forged = dataclasses.replace(subject, attributes={"importance": 0.99})
    forged = dataclasses.replace(forged, digest=forged.compute_digest())
    assert forged.digest == forged.compute_digest(), "the forgery is internally consistent"

    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_entities SET attributes_json = ?, digest = ? "
            "WHERE entity_id = ? AND source_artifact_digest = ?",
            (
                json.dumps(forged.attributes),
                forged.digest,
                forged.entity_id,
                forged.source_artifact_digest,
            ),
        )

    with pytest.raises(ObservationQueryError, match="importance|attribute"):
        entity_occurrences(scopes["alpha_repository"])


def test_an_entity_moved_into_another_scope_is_rejected(scopes: dict):
    """Provenance substitution: claim alpha's docker was seen in beta. `scope_id` is digest-covered,
    so the record no longer matches its own digest."""
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_entities SET scope_id = ? WHERE entity_id = ? AND scope_id = ?",
            (scopes["scope"]["beta"], scopes["docker"], scopes["scope"]["alpha"]),
        )

    with pytest.raises(ObservationQueryError, match="digest"):
        entity_occurrences(scopes["docker"])


def test_a_forged_relationship_is_rejected(scopes: dict):
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_relationships SET relationship_type = ? WHERE object_id = ?",
            ("uses_technology", scopes["inner_repository"]),
        )

    with pytest.raises(ObservationQueryError):
        entity_occurrences(scopes["inner_repository"])


def test_a_forged_shared_entity_is_rejected(scopes: dict):
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE normalized_entities SET value = ? WHERE entity_id = ?",
            ("podman", scopes["docker"]),
        )

    with pytest.raises(ObservationQueryError):
        shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])


def test_a_tampered_source_artifact_is_rejected(scopes: dict):
    """Supporting observations are read out of the stored artifact — so the artifact is validated
    before a single one of its facts is believed."""
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE observation_sets SET artifact_json = ? WHERE scope_id = ?",
            (json.dumps({"schema_version": 1, "observations": []}), scopes["scope"]["beta"]),
        )

    with pytest.raises(ObservationQueryError):
        entity_occurrences(scopes["docker"])


# -- the vocabulary: this layer still has no word for significance ------------------------------


def test_no_significance_vocabulary_appears_in_any_cross_scope_payload(scopes: dict):
    payloads = [
        entity_occurrences(scopes["docker"]),
        entity_occurrences(scopes["document"]),
        entity_occurrences(scopes["inner_repository"]),
        shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]]),
    ]

    for payload in payloads:
        emitted = keys_of(payload)
        assert not emitted & set(_FORBIDDEN), emitted & set(_FORBIDDEN)


def test_the_cross_scope_functions_are_named_without_judgement():
    """A helper called `_rank_scopes` or `_most_common` would be the significance layer arriving by
    accident, one convenience function at a time.

    This guards **defined names**, not prose. A naive substring ban over the whole source would fire
    on a docstring that says "not similarity" — rejecting the very sentence that promises the thing
    it is checking for, which is how a guard becomes a nuisance and then gets deleted.
    """
    tree = ast.parse(Path(observation_query.__file__).read_text(encoding="utf-8"))
    defined = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }
    defined |= {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "entity_occurrences" in defined and "shared_entities" in defined

    for name in defined:
        for word in ("similar", "recommend", "rank", "novelty", "most_common", "best", "top_"):
            assert word not in name.lower(), name


# -- bounds are honest: when a scan hits its cap, it says so ------------------------------------


def test_a_bounded_scope_scan_says_so_rather_than_lying(
    scopes: dict, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(observation_query, "_SCOPE_ENTITY_ID_LIMIT", 1)

    result = shared_entities([scopes["scope"]["alpha"], scopes["scope"]["beta"]])
    assert result["scan_truncated"] is True


def test_a_bounded_relationship_scan_says_so_rather_than_lying(
    scopes: dict, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(observation_query, "_RELATIONSHIP_SCAN_LIMIT", 1)

    result = entity_occurrences(scopes["docker"])
    assert result["scan_truncated"] is True


# -- LHP-INTEL-03A: the content join, through the surface an operator actually uses ----------


def test_a_document_and_a_repository_file_with_the_same_bytes_meet_on_one_content_id(
    tmp_path: Path, store: Path
):
    """The question 03A exists to make answerable: *which scopes contain these exact bytes?*

    Before 03A this returned nothing at all — `repository_intake` recorded file paths and never
    file content, so a repository file and a standalone document had nothing to join on. Note what
    it still does NOT say: not that either was copied, not which came first, not that it matters.
    """
    import hashlib

    from modules.observation_query import entity_occurrences

    payload = "# Specification\n\nexactly these bytes\n"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roots = [str(workspace)]

    repo = make_repo(workspace / "widget", {"docs/spec.md": payload})
    loose = workspace / "loose" / "spec-copy.md"
    loose.parent.mkdir(parents=True)
    loose.write_text(payload, encoding="utf-8")

    db.init_observation_tables()
    repo_set = normalize_repository_observation(observe_repository(str(repo), allowed_roots=roots))
    doc_set = normalize_document_observation(observe_document(str(loose), allowed_roots=roots))
    assert db.store_normalized_observation(repo_set) is True
    assert db.store_normalized_observation(doc_set) is True

    content_id = f"content:{hashlib.sha256(payload.encode()).hexdigest()}"
    result = entity_occurrences(content_id)

    assert result["entity"] == {"entity_id": content_id, "entity_type": "content"}
    assert result["occurrence_count"] == 2
    assert {o["scope_type"] for o in result["occurrences"]} == {"repository", "document"}

    # The repository occurrence names the path — as relationship evidence, because a `content:`
    # entity is deduplicated per artifact and could not carry two paths in one `value`.
    repository = next(o for o in result["occurrences"] if o["scope_type"] == "repository")
    assert [
        (link["relationship_type"], link["subject_id"].endswith(":docs/spec.md"))
        for link in repository["relationships"]
    ] == [("file_has_content", True)]

    # The sensor is the filesystem in BOTH scopes: the same bytes, read the same way. Nothing here
    # claims the file was copied, or that the recurrence means anything at all.
    assert all(o["sensors"] == ["filesystem"] for o in result["occurrences"])


def test_shared_reports_the_content_identity_two_repositories_have_in_common(
    tmp_path: Path, store: Path
):
    import hashlib

    from modules.observation_query import shared_entities

    payload = "FROM scratch\nCOPY . /app\n"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roots = [str(workspace)]

    left = make_repo(workspace / "left", {"Dockerfile": payload, "a.py": "print(1)\n"})
    right = make_repo(workspace / "right", {"deploy/Dockerfile": payload, "b.py": "print(2)\n"})

    db.init_observation_tables()
    sets = {
        name: normalize_repository_observation(observe_repository(str(path), allowed_roots=roots))
        for name, path in (("left", left), ("right", right))
    }
    for result in sets.values():
        assert db.store_normalized_observation(result) is True

    shared = shared_entities([sets["left"].scope_id, sets["right"].scope_id])
    content_id = f"content:{hashlib.sha256(payload.encode()).hexdigest()}"
    ids = [entity["entity_id"] for entity in shared["shared_entities"]]

    assert content_id in ids
    # Sorted by identity, never by count. "The most shared file" would be a ranking.
    assert ids == sorted(ids)
    # The two `.py` files differ, so their content identities are NOT shared. Exact intersection.
    assert sum(1 for entity_id in ids if entity_id.startswith("content:")) == 1


def test_content_identity_survives_a_rename_and_moves_on_an_edit(tmp_path: Path, store: Path):
    """A rename changes where something is; an edit changes what it is. The store keeps both facts,
    and the old one is not rewritten."""
    import hashlib

    from modules.observation_query import entity_occurrences

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roots = [str(workspace)]
    repo = make_repo(workspace / "widget", {"a.txt": "original\n"})

    db.init_observation_tables()
    first = normalize_repository_observation(observe_repository(str(repo), allowed_roots=roots))
    assert db.store_normalized_observation(first) is True

    git(repo, "mv", "a.txt", "b.txt")
    git(repo, "commit", "--quiet", "-m", "rename")
    renamed = normalize_repository_observation(observe_repository(str(repo), allowed_roots=roots))
    assert db.store_normalized_observation(renamed) is True

    original = "content:" + hashlib.sha256(b"original\n").hexdigest()
    assert entity_occurrences(original)["occurrence_count"] == 2  # same bytes, two artifacts

    (repo / "b.txt").write_text("edited\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "edit")
    edited = normalize_repository_observation(observe_repository(str(repo), allowed_roots=roots))
    assert db.store_normalized_observation(edited) is True

    # A new identity for the new bytes; the old identity is untouched, not rewritten.
    assert entity_occurrences(original)["occurrence_count"] == 2
    new_id = "content:" + hashlib.sha256(b"edited\n").hexdigest()
    assert entity_occurrences(new_id)["occurrence_count"] == 1
