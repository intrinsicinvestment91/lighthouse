from dataclasses import dataclass, field


@dataclass
class ProvenanceRecord:
    source_type: str
    source_url: str | None = None
    retrieved_at: str | None = None
    content_hash: str | None = None
    local_path: str | None = None
    ipfs_cid: str | None = None
    nostr_event_id: str | None = None
    metadata: dict | None = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "content_hash": self.content_hash,
            "local_path": self.local_path,
            "ipfs_cid": self.ipfs_cid,
            "nostr_event_id": self.nostr_event_id,
            "metadata": self.metadata or {},
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            source_type=data["source_type"],
            source_url=data.get("source_url"),
            retrieved_at=data.get("retrieved_at"),
            content_hash=data.get("content_hash"),
            local_path=data.get("local_path"),
            ipfs_cid=data.get("ipfs_cid"),
            nostr_event_id=data.get("nostr_event_id"),
            metadata=data.get("metadata") or {},
        )
