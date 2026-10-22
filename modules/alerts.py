from dataclasses import dataclass

ALERT_SCHEMA_VERSION = 1


@dataclass
class AlertRecord:
    source: str
    alert_type: str
    title: str | None = None
    entity: str | None = None
    score: float | None = None
    score_label: str | None = None
    provenance: dict | None = None
    payload: dict | None = None
    schema_version: int = ALERT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.payload = self.payload or {}
        self.provenance = self.provenance or {}
        if self.schema_version is None:
            self.schema_version = ALERT_SCHEMA_VERSION

    def validate(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")

        if not isinstance(self.alert_type, str) or not self.alert_type.strip():
            raise ValueError("alert_type must be a non-empty string")

        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")

        if self.score is not None and not (0.0 <= self.score <= 1.0):
            raise ValueError("score must be None or between 0.0 and 1.0")

        if self.score_label is not None and self.score_label not in {"low", "medium", "high"}:
            raise ValueError("score_label must be one of: low, medium, high")

    def to_dict(self) -> dict:
        self.validate()
        return {
            "source": self.source,
            "alert_type": self.alert_type,
            "title": self.title,
            "entity": self.entity,
            "score": self.score,
            "score_label": self.score_label,
            "provenance": self.provenance or {},
            "payload": self.payload or {},
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlertRecord":
        record = cls(
            source=data.get("source", ""),
            alert_type=data.get("alert_type", ""),
            title=data.get("title"),
            entity=data.get("entity"),
            score=data.get("score"),
            score_label=data.get("score_label"),
            provenance=data.get("provenance") or {},
            payload=data.get("payload") or {},
            schema_version=data.get("schema_version", ALERT_SCHEMA_VERSION),
        )
        record.validate()
        return record
