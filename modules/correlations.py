from dataclasses import dataclass

CORRELATION_SCHEMA_VERSION = 2

_VALID_VERIFICATION_STATUSES = {"unverified", "single_source", "corroborated", "multi_detector"}


@dataclass
class CorrelationRecord:
    event_id: str
    event_type: str
    entity: str
    score: float
    signals: list[str]
    evidence: dict
    observed_at: str | None = None
    schema_version: int = CORRELATION_SCHEMA_VERSION
    verification_status: str = "unverified"
    source_trust_score: float = 0.0
    is_single_source: bool = False
    witness_hash: str | None = None
    witness_signature: str | None = None

    def __post_init__(self) -> None:
        self.signals = self.signals or []
        self.evidence = self.evidence or {}
        if self.schema_version is None:
            self.schema_version = CORRELATION_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.event_id:
            raise ValueError("event_id required")
        if not self.entity:
            raise ValueError("entity required")
        if not (0 <= self.score <= 10):
            raise ValueError("score must be 0-10")
        if not isinstance(self.signals, list):
            raise ValueError("signals must be list")
        if self.verification_status not in _VALID_VERIFICATION_STATUSES:
            raise ValueError(f"verification_status must be one of {_VALID_VERIFICATION_STATUSES}")

    def to_dict(self) -> dict:
        self.validate()
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "entity": self.entity,
            "score": self.score,
            "signals": self.signals or [],
            "evidence": self.evidence or {},
            "observed_at": self.observed_at,
            "schema_version": self.schema_version,
            "verification_status": self.verification_status,
            "source_trust_score": self.source_trust_score,
            "is_single_source": self.is_single_source,
            "witness_hash": self.witness_hash,
            "witness_signature": self.witness_signature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CorrelationRecord":
        record = cls(
            event_id=data.get("event_id", ""),
            event_type=data.get("event_type", ""),
            entity=data.get("entity", ""),
            score=data.get("score", 0),
            signals=data.get("signals") or [],
            evidence=data.get("evidence") or {},
            observed_at=data.get("observed_at"),
            schema_version=data.get("schema_version", CORRELATION_SCHEMA_VERSION),
            verification_status=data.get("verification_status", "unverified"),
            source_trust_score=float(data.get("source_trust_score", 0.0)),
            is_single_source=bool(data.get("is_single_source", False)),
            witness_hash=data.get("witness_hash"),
            witness_signature=data.get("witness_signature"),
        )
        record.validate()
        return record
