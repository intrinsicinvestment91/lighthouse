"""Shared pytest fixtures for Project Lighthouse tests."""

from tests.runtime_isolation import ensure_runtime_isolation

ensure_runtime_isolation()

import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from modules.state_db import init_db  # noqa: E402


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_state.db")


@pytest.fixture
def tmp_db(tmp_db_path: str) -> sqlite3.Connection:
    return init_db(tmp_db_path)


@pytest.fixture
def sample_uspto_raw() -> dict:
    return {
        "patent_id": "US123456",
        "patent_title": "Advanced Plasma Confinement Device",
        "patent_date": "2026-01-15",
        "patent_abstract": "A novel approach to inertial confinement fusion.",
        "assignee_organization": "MIT",
        "assignee_type": "3",
        "ipc_section_id": "G",
        "ipc_class_id": "G21",
        "ipc_subclass_id": "G21B",
    }


@pytest.fixture
def sample_wipo_raw() -> dict:
    return {
        "PN": "WO2026123456",
        "TI": "High-Entropy Alloy for Aerospace Applications",
        "PA": "Lockheed Martin Corp",
        "IPC": "C22C 30/00",
        "PD": "20260115",
    }


@pytest.fixture
def nostr_test_key_hex() -> str:
    # A known valid secp256k1 scalar (not for real use — deterministic test key)
    return "0101010101010101010101010101010101010101010101010101010101010101"
