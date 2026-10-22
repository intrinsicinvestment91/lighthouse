"""
Dead Man's Switch — REST API portal.

Researchers register an encrypted last-message and a check-in interval.
If a check-in deadline is missed, dms_checker.py broadcasts the message to Nostr.

Start with:  uvicorn portal.app:app --host 0.0.0.0 --port 8080
Browse API:  http://localhost:8080/docs
"""

import hashlib
import os
import time
import uuid

from fastapi import Depends, FastAPI, Form, HTTPException, Request

from config import dms_cfg
from modules import db as _alerts_db
from modules.local_gateway import ALLOWED_METHODS, dispatch
from modules.state_db import init_db, insert_zk_intake_submission
from portal.models import (
    CheckinRequest,
    GatewayRequest,
    RegisterRequest,
    RegisterResponse,
    RevokeRequest,
    StatusResponse,
)

app = FastAPI(title="Dead Man's Switch Portal", version="1.0.0")

_db = init_db(dms_cfg.db_path)
_alerts_db.init_alerts_table()


@app.post("/register", response_model=RegisterResponse)
async def register(req: RegisterRequest) -> RegisterResponse:
    if req.interval_hours > dms_cfg.max_interval_hours:
        raise HTTPException(
            status_code=400,
            detail=f"interval_hours exceeds maximum ({dms_cfg.max_interval_hours})",
        )
    reg_id = str(uuid.uuid4())
    now = int(time.time())
    _db.execute(
        """
        INSERT INTO dms_registrations
            (id, display_name, encrypted_message, interval_hours, last_checkin_at, nostr_pubkey)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            reg_id,
            req.display_name,
            req.encrypted_message,
            req.interval_hours,
            now,
            req.nostr_pubkey,
        ),
    )
    _db.commit()
    return RegisterResponse(
        id=reg_id,
        display_name=req.display_name,
        interval_hours=req.interval_hours,
        next_deadline=now + req.interval_hours * 3600,
    )


@app.post("/checkin")
async def checkin(req: CheckinRequest) -> dict:
    now = int(time.time())
    row = _db.execute(
        "SELECT interval_hours, active FROM dms_registrations WHERE id = ?",
        (req.id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Registration not found")
    if not row[1]:
        raise HTTPException(status_code=410, detail="Registration has been revoked")
    _db.execute(
        "UPDATE dms_registrations SET last_checkin_at = ? WHERE id = ?",
        (now, req.id),
    )
    _db.commit()
    return {"status": "ok", "next_deadline": now + row[0] * 3600}


@app.delete("/revoke")
async def revoke(req: RevokeRequest) -> dict:
    row = _db.execute("SELECT 1 FROM dms_registrations WHERE id = ?", (req.id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Registration not found")
    _db.execute("UPDATE dms_registrations SET active = 0 WHERE id = ?", (req.id,))
    _db.commit()
    return {"status": "revoked"}


@app.get("/alerts")
async def get_alerts(
    source: str | None = None,
    min_score: float | None = None,
    label: str | None = None,
    entity: str | None = None,
    limit: int = 10,
    since_days: int | None = None,
) -> list[dict]:
    """Query stored alerts with optional filtering."""
    if entity:
        rows = _alerts_db.find_related_alerts(entity, limit=limit, since_days=since_days)
    elif source:
        rows = _alerts_db.fetch_alerts_by_source(source, limit=limit, since_days=since_days)
    elif min_score is not None:
        rows = _alerts_db.fetch_alerts_min_score(min_score, limit=limit, since_days=since_days)
    elif label:
        rows = _alerts_db.fetch_alerts_by_label(label, limit=limit, since_days=since_days)
    else:
        rows = _alerts_db.fetch_alerts(limit=limit, since_days=since_days)
    return [row["alert"] for row in rows]


@app.get("/approval-token")
async def approval_token() -> dict:
    """Return current human-approval token when gate secret is configured."""
    secret = os.environ.get("LIGHTHOUSE_APPROVAL_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="LIGHTHOUSE_APPROVAL_SECRET not configured")
    token = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:24]
    return {"token": token}


@app.post("/intake")
async def intake(
    submission_type: str = Form(...),
    content: str = Form(...),
) -> dict:
    """Store zero-knowledge intake metadata only (no raw content)."""
    submission_type = submission_type.strip().lower()
    if submission_type not in {"text", "json", "file"}:
        raise HTTPException(
            status_code=400,
            detail="submission_type must be one of: text, json, file",
        )
    if not content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    submission_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    insert_zk_intake_submission(
        _db,
        submission_id=submission_id,
        submission_type=submission_type,
        content_hash=content_hash,
        ipfs_cid=None,
    )
    return {
        "id": submission_id,
        "submission_type": submission_type,
        "content_hash": content_hash,
        "ipfs_cid": None,
    }


@app.get("/status/{reg_id}", response_model=StatusResponse)
async def status(reg_id: str) -> StatusResponse:
    row = _db.execute(
        """
        SELECT display_name, active, last_checkin_at, interval_hours
        FROM dms_registrations WHERE id = ?
        """,
        (reg_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Registration not found")
    display_name, active, last_checkin_at, interval_hours = row
    return StatusResponse(
        id=reg_id,
        display_name=display_name,
        active=bool(active),
        last_checkin_at=last_checkin_at,
        next_deadline=last_checkin_at + interval_hours * 3600,
    )


async def _require_local_client(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Local access only")


@app.get("/local/health")
async def local_health(dep: None = Depends(_require_local_client)) -> dict:
    return {"status": "ok", "service": "lighthouse-local-gateway"}


@app.get("/local/methods")
async def local_methods(dep: None = Depends(_require_local_client)) -> dict:
    return {"methods": sorted(ALLOWED_METHODS)}


@app.post("/local/request")
async def local_request(
    req: GatewayRequest,
    dep: None = Depends(_require_local_client),
) -> dict:
    return dispatch(req.method, req.params)
