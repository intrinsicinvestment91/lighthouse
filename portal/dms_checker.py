"""
Dead Man's Switch checker.

Standalone async runner that finds overdue registrations and broadcasts
their encrypted last-message to Nostr. Run on a cron schedule or via:
    python main.py --module dms-check
"""

import logging
import time

from config import dms_cfg, nostr_cfg
from modules.nostr_relay import (
    _load_private_key,
    _pubkey_hex,
    build_dms_broadcast_note,
    run_nostr_bot,
)
from modules.state_db import init_db

logger = logging.getLogger(__name__)


async def check_and_broadcast(db_path: str = dms_cfg.db_path, dry_run: bool = False) -> None:
    """Find overdue DMS registrations; broadcast a Nostr note for each."""
    conn = init_db(db_path)
    now = int(time.time())

    overdue = conn.execute(
        """
        SELECT id, display_name, encrypted_message, interval_hours, last_checkin_at, nostr_pubkey
        FROM dms_registrations
        WHERE active = 1
          AND (last_checkin_at + interval_hours * 3600) < ?
          AND id NOT IN (SELECT registration_id FROM dms_broadcasts)
        """,
        (now,),
    ).fetchall()

    if not overdue:
        logger.info("[DMS] No overdue registrations")
        return

    logger.warning("[DMS] %d overdue registration(s) — broadcasting", len(overdue))

    if not nostr_cfg.private_key_hex:
        logger.error("[DMS] NOSTR_PRIVATE_KEY_HEX not set — cannot broadcast")
        return

    priv = _load_private_key(nostr_cfg.private_key_hex)
    pubkey = _pubkey_hex(priv)

    events = [
        build_dms_broadcast_note(
            pubkey=pubkey,
            display_name=display_name,
            encrypted_message=encrypted_message,
            registration_id=reg_id,
        )
        for reg_id, display_name, encrypted_message, *_ in overdue
    ]

    if dry_run:
        logger.info("[DMS] DRY-RUN: would broadcast %d DMS events", len(events))
        return

    await run_nostr_bot(events)

    broadcast_at = int(time.time())
    for event, row in zip(events, overdue, strict=False):
        conn.execute(
            "INSERT INTO dms_broadcasts (registration_id, broadcast_at, nostr_event_id)"
            " VALUES (?, ?, ?)",
            (row[0], broadcast_at, event.id),
        )
    conn.commit()
    logger.info("[DMS] Recorded %d broadcast(s)", len(overdue))
