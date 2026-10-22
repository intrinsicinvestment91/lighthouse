"""Pydantic request/response models for the Dead Man's Switch portal."""

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=200)
    encrypted_message: str = Field(..., min_length=1)
    interval_hours: int = Field(..., ge=1)
    nostr_pubkey: str | None = None


class RegisterResponse(BaseModel):
    id: str
    display_name: str
    interval_hours: int
    next_deadline: int


class CheckinRequest(BaseModel):
    id: str


class RevokeRequest(BaseModel):
    id: str


class StatusResponse(BaseModel):
    id: str
    display_name: str
    active: bool
    last_checkin_at: int
    next_deadline: int


class GatewayRequest(BaseModel):
    method: str = Field(..., min_length=1, max_length=100)
    params: dict = Field(default_factory=dict)
