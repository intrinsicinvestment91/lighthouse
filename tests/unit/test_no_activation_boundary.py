"""No-activation regression tests (LHP-PROFILE-14B, extended by 14C).

The activation capability boundary defines contracts only, and the capability descriptor
(14C) only *describes* activation. These tests prove, as a standing regression, that no
activation path exists anywhere the boundary touches: no CLI command, no module surface,
no import-time side effects, and no vocabulary widening that could smuggle an "approved
means activated" — or a "described means available" — path in later.
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import main as main_module
from modules import (
    activation_capability,
    activation_intent,
    activation_preconditions,
    active_profile_state,
    profile_registry,
    profile_transition,
    profiles,
)

_ACTIVATION_WRITER_ATTRS = frozenset(
    {"activate_active_profile_atomic", "_activate_active_profile_atomic"}
)
_DB_MODULE = "modules.db"
_MODULES_PACKAGE = "modules"

_FORBIDDEN_DB_IMPORT_SNIPPETS = (
    "from modules import db\n",
    "from modules import db as persistence\n",
    "import modules.db\n",
    "import modules.db as persistence\n",
    "from modules.db import _activate_active_profile_atomic\n",
    "from modules.db import _activate_active_profile_atomic as writer\n",
    "from modules.db import fetch_active_profile_rows\n",
    "from modules.db import active_profile_table_exists as table_exists\n",
)

_ALLOWED_NON_DB_IMPORT_SNIPPETS = (
    "from modules import active_profile_store\n",
    "from modules.active_profile_state import ActiveProfileState\n",
)

_WRITER_REFERENCE_SNIPPETS = (
    (
        "from modules import db\ndef f():\n    db._activate_active_profile_atomic('a', 'b')\n",
        "attribute call via db",
    ),
    (
        "from modules.db import _activate_active_profile_atomic\n"
        "def f():\n"
        "    _activate_active_profile_atomic('a', 'b')\n",
        "bare-name call after from-import",
    ),
    (
        "from modules.db import _activate_active_profile_atomic as writer\n"
        "def f():\n"
        "    writer('a', 'b')\n",
        "from-import alias call",
    ),
    (
        "from modules import db\ndef f():\n    getattr(db, '_activate_active_profile_atomic')\n",
        "getattr on db binding",
    ),
    (
        "from modules import db as persistence\n"
        "def f():\n"
        "    getattr(persistence, '_activate_active_profile_atomic')\n",
        "getattr on aliased db binding",
    ),
)


def _production_python_paths(repo_root: Path) -> list[Path]:
    """Production Python sources under root, modules/, portal/, and scripts/."""
    excluded_parts = frozenset(
        {"tests", "__pycache__", ".venv", "venv", "dist", "build", ".cursor", ".git"}
    )
    seen: set[Path] = set()
    for pattern in ("*.py", "modules/**/*.py", "portal/**/*.py", "scripts/**/*.py"):
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            if any(part in excluded_parts for part in path.parts):
                continue
            seen.add(path.resolve())
    return sorted(seen, key=lambda p: p.relative_to(repo_root).as_posix())


def _direct_db_import_violations(tree: ast.AST) -> list[str]:
    """Forbidden direct db import forms that could reach the activation writer."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != _DB_MODULE:
                    continue
                label = f"import {alias.name}"
                if alias.asname:
                    label += f" as {alias.asname}"
                violations.append(label)
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                continue
            module = node.module or ""
            if module == _MODULES_PACKAGE:
                for alias in node.names:
                    if alias.name != "db":
                        continue
                    label = "from modules import db"
                    if alias.asname:
                        label += f" as {alias.asname}"
                    violations.append(label)
            elif module == _DB_MODULE:
                for alias in node.names:
                    label = f"from modules.db import {alias.name}"
                    if alias.asname:
                        label += f" as {alias.asname}"
                    violations.append(label)
    return violations


def _db_binding_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _DB_MODULE:
                    names.add(alias.asname or "db")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module != _MODULES_PACKAGE:
                continue
            for alias in node.names:
                if alias.name == "db":
                    names.add(alias.asname or "db")
    return names


def _writer_local_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 0 or node.module != _DB_MODULE:
            continue
        for alias in node.names:
            if alias.name in _ACTIVATION_WRITER_ATTRS:
                names.add(alias.asname or alias.name)
    return names


def _is_getattr_writer_access(
    node: ast.Call,
    *,
    db_bindings: set[str],
) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != "getattr":
        return False
    if len(node.args) < 2:
        return False
    module_arg, attr_arg = node.args[0], node.args[1]
    if not isinstance(module_arg, ast.Name):
        return False
    if module_arg.id not in db_bindings:
        return False
    if not isinstance(attr_arg, ast.Constant) or not isinstance(attr_arg.value, str):
        return False
    return attr_arg.value in _ACTIVATION_WRITER_ATTRS


def _count_activation_writer_references(tree: ast.AST) -> int:
    db_bindings = _db_binding_names(tree)
    writer_locals = _writer_local_names(tree)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            (isinstance(func, ast.Name) and func.id in writer_locals)
            or (isinstance(func, ast.Attribute) and func.attr in _ACTIVATION_WRITER_ATTRS)
            or _is_getattr_writer_access(node, db_bindings=db_bindings)
        ):
            count += 1
    return count


def _collect_activation_writer_references(repo_root: Path) -> dict[str, int]:
    callers: dict[str, int] = {}
    for path in _production_python_paths(repo_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        count = _count_activation_writer_references(tree)
        if count:
            rel = path.relative_to(repo_root).as_posix()
            callers[rel] = count
    return callers


def _assert_db_import_guard_positive_controls() -> None:
    for snippet in _FORBIDDEN_DB_IMPORT_SNIPPETS:
        violations = _direct_db_import_violations(ast.parse(snippet))
        assert violations, f"expected db import violation for:\n{snippet}"
    for snippet in _ALLOWED_NON_DB_IMPORT_SNIPPETS:
        violations = _direct_db_import_violations(ast.parse(snippet))
        assert not violations, f"unexpected db import violation for:\n{snippet}"


def _assert_writer_reference_scanner_positive_controls() -> None:
    for snippet, label in _WRITER_REFERENCE_SNIPPETS:
        count = _count_activation_writer_references(ast.parse(snippet))
        assert count >= 1, f"scanner missed {label!r}:\n{snippet}"


EXPECTED_SUBCOMMANDS = {
    "status",
    "correlations",
    "alerts",
    "graph",
    "graph-backfill",
    "targets",
    "temporal",
    "tempo",
    "callsigns",
    "profiles",
    "backup-readiness",
    "backup-manifest",
    "backup-create",
    "backup-verify",
    "restore-drill",
    "profile-transition-preview",
    # LHP-V1-PROFILE-01C — closed initial local activation + read-only status. Deliberately
    # added; switch/deactivate/general apply remain forbidden.
    "profile-activate",
    "active-profile",
    # LHP-INTEL-01E — local, read-only observation intake. Reviewed and added deliberately; it
    # activates nothing, and the activation guard below is unchanged.
    "observe",
    # LHP-INTEL-02A — the read side of the same layer. Inspects facts; decides nothing.
    "observations",
    # LHP-INTEL-03B — the first candidate/review loop. It is the first surface that presents
    # something other than a raw fact, and the first that could therefore tell an operator what to
    # think. It does not: no score, no rank, no promotion, no publication, and — pinned by the
    # candidate contract's `activates_profile is False` — no activation. Added deliberately.
    "patterns",
    # LHP-INTEL-04B — the one surface that reaches the network. A deliberate, named command (never
    # hidden inside `--module`); it records direct facts about retrieval attempts and activates
    # nothing, creates no candidate, and contacts no Gateway. Added deliberately.
    "public-sources",
}

# `observations` is this CLI's first NESTED subcommand, so its actions are pinned separately. A
# sub-action and a top-level command are different things, and collapsing them into one set would
# leave the pin unable to tell them apart.
EXPECTED_OBSERVATION_ACTIONS = {
    "list",
    "show",
    "search",
    "stats",
    # LHP-INTEL-02B — cross-scope fact discovery. An intentional expansion, pinned here on purpose:
    # both are read-only, and neither ranks, scores, infers, or activates anything.
    "where",
    "shared",
}

# The second nested subcommand (LHP-INTEL-03B). Pinned against ITS OWN receiver rather than folded
# into a union: `list` and `show` exist under both `observations` and `patterns`, so a union would
# absorb them and stop being able to tell which command grew an action.
EXPECTED_PATTERN_ACTIONS = {
    "detect",
    "list",
    "show",
    "review",
}

# The third nested subcommand (LHP-INTEL-04B), pinned against its own receiver for the same reason:
# `show` exists under `observations`, `patterns` and here, so a union could not tell which command
# grew an action. None of these activate, publish, score, or create a candidate.
EXPECTED_PUBLIC_SOURCE_ACTIONS = {
    "check",
    "export-osr",
    "runs",
    "history",
    "show",
}

FORBIDDEN_SURFACE = (
    "activate_profile",
    "set_active_profile",
    "write_active_profile",
    "apply_profile",
    "switch_profile",
    "deactivate_profile",
    "reset_active_profile",
    "execute_transition",
    "request_activation",
    "execute_activation",
)

# Exact initial-activation/store vocabulary permitted by LHP-V1-PROFILE-01C.
ALLOWED_INITIAL_ACTIVATION_SURFACE = frozenset(
    {
        "read_active_profile_state",
        "activate_repository_capability_discovery",
        "REQUIRED_CONFIRMATION",
        "AUTHORITY_PINS_FALSE",
    }
)

BOUNDARY_MODULES = (
    active_profile_state,
    activation_preconditions,
    activation_intent,
    activation_capability,
    profiles,
    profile_registry,
    profile_transition,
)


def test_cli_subcommand_set_is_pinned_and_activation_free():
    """Adding any subcommand — especially an activation one — must be a conscious act.

    The pin buckets by receiver, because since 02A the CLI has nested subcommands and a sub-action
    must not be mistakable for a top-level command. The third assertion is the load-bearing one:
    *every* `add_parser` call in the file, on whatever receiver, has to be accounted for here — so a
    parser hung off some new subparser cannot slip in unpinned.
    """
    src = Path(main_module.__file__).read_text(encoding="utf-8")
    top_level = set(re.findall(r'\bsubparsers\.add_parser\(\s*"([a-z0-9-]+)"', src))
    observations = set(re.findall(r'\bobservation_actions\.add_parser\(\s*"([a-z0-9-]+)"', src))
    patterns = set(re.findall(r'\bpattern_actions\.add_parser\(\s*"([a-z0-9-]+)"', src))
    public_sources = set(re.findall(r'\bpublic_source_actions\.add_parser\(\s*"([a-z0-9-]+)"', src))
    every = set(re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', src))

    assert top_level == EXPECTED_SUBCOMMANDS
    assert observations == EXPECTED_OBSERVATION_ACTIONS
    assert patterns == EXPECTED_PATTERN_ACTIONS
    assert public_sources == EXPECTED_PUBLIC_SOURCE_ACTIONS
    assert every == (
        EXPECTED_SUBCOMMANDS
        | EXPECTED_OBSERVATION_ACTIONS
        | EXPECTED_PATTERN_ACTIONS
        | EXPECTED_PUBLIC_SOURCE_ACTIONS
    )
    # Exact initial-activation commands are allowed; switch/deactivate must stay absent.
    assert "profile-activate" in every
    assert "active-profile" in every
    assert not [n for n in every if "switch" in n or "deactivate" in n or n == "activate"]


def test_boundary_modules_expose_no_activation_surface():
    for module in BOUNDARY_MODULES:
        for forbidden in FORBIDDEN_SURFACE:
            assert not hasattr(module, forbidden), f"{module.__name__}.{forbidden}"
        assert not hasattr(module, "activate"), f"{module.__name__}.activate"


def test_new_modules_import_without_side_effects(tmp_path):
    """Import the boundary modules in a pristine cwd — nothing may be created."""
    repo_root = Path(main_module.__file__).resolve().parent
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import modules.active_profile_state, modules.activation_preconditions, "
            "modules.activation_intent, modules.activation_capability",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert list(tmp_path.iterdir()) == []


def test_precondition_decision_vocabulary_is_closed():
    # No "approved"/"authorized" token may ever creep into the decision set — the packet
    # rule is that "approved" and "activated" stay separate concepts.
    assert {
        "ready",
        "blocked",
        "invalid",
    } == activation_preconditions.ALLOWED_PRECONDITION_DECISIONS


def test_activation_source_vocabulary_is_closed():
    assert {
        "operator_approved_transition",
        "operator_local_cli_confirmation",
    } == active_profile_state.ALLOWED_ACTIVATION_SOURCES


def test_initial_activation_modules_expose_only_the_closed_surface():
    """LHP-V1-PROFILE-01C: store + ceremony exist, but switch/general apply stay forbidden."""
    from modules import active_profile_store, profile_activation

    for module in (active_profile_store, profile_activation):
        for forbidden in FORBIDDEN_SURFACE:
            assert not hasattr(module, forbidden), f"{module.__name__}.{forbidden}"
        assert not hasattr(module, "switch_profile")
        assert not hasattr(module, "deactivate_profile")

    assert not hasattr(active_profile_store, "persist_initial_activation")
    assert hasattr(active_profile_store, "_persist_initial_activation")
    assert hasattr(active_profile_store, "read_active_profile_state")
    assert hasattr(profile_activation, "activate_repository_capability_discovery")
    for pin in profile_activation.AUTHORITY_PINS_FALSE:
        assert pin  # non-empty names
    # Gateway/remote capability remains unavailable.
    assert activation_capability.ActivationCapabilityDescriptor().activation_available is False
    assert {"not_implemented"} == activation_capability.ALLOWED_IMPLEMENTATION_STATES


# -- LHP-V1-HARDEN-02: activation DB-writer boundary ---------------------------------------


def test_db_exposes_no_public_activation_writer():
    from modules import db

    assert not hasattr(db, "activate_active_profile_atomic")
    assert hasattr(db, "_activate_active_profile_atomic")
    # The sanctioned public surface for this table is read-only, and it is exactly these two.
    assert {n for n in dir(db) if not n.startswith("_") and "active_profile" in n} == {
        "active_profile_table_exists",
        "fetch_active_profile_rows",
    }


def test_active_profile_store_exports_no_activation_writer():
    from modules import active_profile_store

    assert set(active_profile_store.__all__) == {
        "ActiveProfileStoreError",
        "CorruptActiveProfileStateError",
        "UnsupportedProfileSwitchError",
        "read_active_profile_state",
    }


def test_only_the_private_store_reaches_the_activation_writer():
    _assert_writer_reference_scanner_positive_controls()

    repo_root = Path(__file__).resolve().parents[2]
    callers = _collect_activation_writer_references(repo_root)
    assert callers == {"modules/active_profile_store.py": 1}


def test_the_activation_ceremony_reaches_persistence_only_through_the_store():
    _assert_db_import_guard_positive_controls()

    path = Path(__file__).resolve().parents[2] / "modules" / "profile_activation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert not _direct_db_import_violations(tree)

    persist_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "_persist_initial_activation"
        and isinstance(node.value, ast.Name)
        and node.value.id == "active_profile_store"
    ]
    assert len(persist_calls) == 1


def test_activation_states_are_exactly_two():
    assert {"active", "no_active_profile"} == active_profile_state.ALLOWED_ACTIVATION_STATES


def test_implementation_state_vocabulary_cannot_claim_availability():
    # The capability descriptor (14C) has exactly one word for its own state, and that word is
    # "not_implemented". No value edit can make Lighthouse claim activation is available.
    assert {"not_implemented"} == activation_capability.ALLOWED_IMPLEMENTATION_STATES
    assert activation_capability.ActivationCapabilityDescriptor().activation_available is False


def test_describing_a_capability_is_not_providing_one():
    # The descriptor names three Gateway capabilities; all three remain unimplemented here.
    descriptor = activation_capability.describe_activation_capability()
    assert descriptor.implementation_state == "not_implemented"
    assert all(
        operation.implementation_state == "not_implemented"
        for operation in descriptor.declared_operations
    )


def test_owning_a_profile_is_not_running_under_one():
    """LHP-INTEL-03B gives a profile its first runtime *consumer* — and it still activates nothing.

    A candidate carries `profile_id` because a profile DECLARED the rule that produced it. That is
    ownership, and it is the one thing a candidate says about profiles. Reading it as "this profile
    is active" is exactly the confusion ADR-0018 froze the ladder to avoid, so the artifact pins the
    denial rather than leaving it to a docstring.
    """
    from modules import pattern_candidate, repository_content_recurrence

    assert repository_content_recurrence.OWNER_PROFILE_ID in pattern_candidate.ALLOWED_PROFILE_IDS
    assert "activates_profile" in pattern_candidate.AUTHORITY_PINS_FALSE
    assert "contacts_gateway" in pattern_candidate.AUTHORITY_PINS_FALSE


def test_the_candidate_layer_exposes_no_activation_surface():
    from modules import pattern_candidate, pattern_candidate_service, repository_content_recurrence

    for module in (pattern_candidate, pattern_candidate_service, repository_content_recurrence):
        for forbidden in FORBIDDEN_SURFACE:
            assert not hasattr(module, forbidden), f"{module.__name__}.{forbidden}"


def test_the_public_source_layer_exposes_no_activation_surface():
    # LHP-INTEL-04B adds a network fact layer. It must not grow an activation, publication, or
    # candidate-creation surface: it records facts and stops.
    from modules import public_source_check, public_source_check_service, public_source_intake

    for module in (public_source_check, public_source_check_service, public_source_intake):
        for forbidden in (*FORBIDDEN_SURFACE, "create_candidate", "publish", "activate_profile"):
            assert not hasattr(module, forbidden), f"{module.__name__}.{forbidden}"


def test_no_candidate_state_can_mean_approved_or_activated():
    from modules import pattern_candidate

    assert {"ready_for_review"} == pattern_candidate.ALLOWED_CANDIDATE_STATES
    assert {
        "accepted_for_followup",
        "dismissed",
        "needs_more_evidence",
    } == pattern_candidate.ALLOWED_DECISIONS
    every_word = pattern_candidate.ALLOWED_CANDIDATE_STATES | pattern_candidate.ALLOWED_DECISIONS
    assert not [w for w in every_word if "activ" in w or "approv" in w or "verif" in w]
