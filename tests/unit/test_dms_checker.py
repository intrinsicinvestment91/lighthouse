"""Unit tests for portal/dms_checker.py"""

import time
from unittest.mock import AsyncMock, patch

import config
from portal.dms_checker import check_and_broadcast


def _insert_registration(conn, reg_id, interval_hours, last_checkin_at, active=1):
    conn.execute(
        """
        INSERT INTO dms_registrations
            (id, display_name, encrypted_message, interval_hours, last_checkin_at, active)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (reg_id, "Dr. Test", "encrypted-payload", interval_hours, last_checkin_at, active),
    )
    conn.commit()


async def test_no_overdue_does_nothing(tmp_db_path):
    await check_and_broadcast(db_path=tmp_db_path, dry_run=True)
    # No errors raised — nothing to broadcast


async def test_overdue_dry_run_does_not_broadcast(tmp_db, tmp_db_path, monkeypatch):
    monkeypatch.setattr(config.nostr_cfg, "private_key_hex", "01" * 32)
    now = int(time.time())
    _insert_registration(tmp_db, "reg-001", interval_hours=1, last_checkin_at=now - 7200)

    with patch("portal.dms_checker.run_nostr_bot", new_callable=AsyncMock) as mock_bot:
        await check_and_broadcast(db_path=tmp_db_path, dry_run=True)
        mock_bot.assert_not_called()


async def test_overdue_broadcasts_and_records(tmp_db, tmp_db_path, monkeypatch):
    monkeypatch.setattr(config.nostr_cfg, "private_key_hex", "01" * 32)
    now = int(time.time())
    _insert_registration(tmp_db, "reg-002", interval_hours=1, last_checkin_at=now - 7200)

    with patch("portal.dms_checker.run_nostr_bot", new_callable=AsyncMock) as mock_bot:
        await check_and_broadcast(db_path=tmp_db_path, dry_run=False)
        mock_bot.assert_called_once()

    row = tmp_db.execute(
        "SELECT registration_id FROM dms_broadcasts WHERE registration_id = 'reg-002'"
    ).fetchone()
    assert row is not None


async def test_already_broadcast_not_re_sent(tmp_db, tmp_db_path, monkeypatch):
    monkeypatch.setattr(config.nostr_cfg, "private_key_hex", "01" * 32)
    now = int(time.time())
    _insert_registration(tmp_db, "reg-003", interval_hours=1, last_checkin_at=now - 7200)
    tmp_db.execute(
        "INSERT INTO dms_broadcasts (registration_id, broadcast_at) VALUES ('reg-003', ?)",
        (now - 100,),
    )
    tmp_db.commit()

    with patch("portal.dms_checker.run_nostr_bot", new_callable=AsyncMock) as mock_bot:
        await check_and_broadcast(db_path=tmp_db_path, dry_run=False)
        mock_bot.assert_not_called()


async def test_inactive_registration_skipped(tmp_db, tmp_db_path, monkeypatch):
    monkeypatch.setattr(config.nostr_cfg, "private_key_hex", "01" * 32)
    now = int(time.time())
    _insert_registration(tmp_db, "reg-004", interval_hours=1, last_checkin_at=now - 7200, active=0)

    with patch("portal.dms_checker.run_nostr_bot", new_callable=AsyncMock) as mock_bot:
        await check_and_broadcast(db_path=tmp_db_path, dry_run=False)
        mock_bot.assert_not_called()


async def test_no_nostr_key_does_not_raise(tmp_db, tmp_db_path, monkeypatch):
    monkeypatch.setattr(config.nostr_cfg, "private_key_hex", "")
    now = int(time.time())
    _insert_registration(tmp_db, "reg-005", interval_hours=1, last_checkin_at=now - 7200)
    # Should log error but not raise
    await check_and_broadcast(db_path=tmp_db_path, dry_run=False)
