from dataclasses import dataclass

GRAPH_SCHEMA_VERSION = 1


@dataclass
class GraphNode:
    node_id: str
    node_type: str
    label: str
    properties: dict | None = None
    schema_version: int = GRAPH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.properties = self.properties or {}
        if self.schema_version is None:
            self.schema_version = GRAPH_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.node_id:
            raise ValueError("node_id required")
        if not self.node_type:
            raise ValueError("node_type required")
        if not self.label:
            raise ValueError("label required")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")

    def to_dict(self) -> dict:
        self.validate()
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "properties": self.properties or {},
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphNode":
        record = cls(
            node_id=data.get("node_id", ""),
            node_type=data.get("node_type", ""),
            label=data.get("label", ""),
            properties=data.get("properties") or {},
            schema_version=data.get("schema_version", GRAPH_SCHEMA_VERSION),
        )
        record.validate()
        return record


@dataclass
class GraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    weight: float = 1.0
    properties: dict | None = None
    schema_version: int = GRAPH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.properties = self.properties or {}
        if self.schema_version is None:
            self.schema_version = GRAPH_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.edge_id:
            raise ValueError("edge_id required")
        if not self.source_id:
            raise ValueError("source_id required")
        if not self.target_id:
            raise ValueError("target_id required")
        if not self.relation:
            raise ValueError("relation required")
        if not (0.0 <= self.weight <= 1.0):
            raise ValueError("weight must be between 0.0 and 1.0")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")

    def to_dict(self) -> dict:
        self.validate()
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "weight": self.weight,
            "properties": self.properties or {},
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphEdge":
        record = cls(
            edge_id=data.get("edge_id", ""),
            source_id=data.get("source_id", ""),
            target_id=data.get("target_id", ""),
            relation=data.get("relation", ""),
            weight=data.get("weight", 1.0),
            properties=data.get("properties") or {},
            schema_version=data.get("schema_version", GRAPH_SCHEMA_VERSION),
        )
        record.validate()
        return record
