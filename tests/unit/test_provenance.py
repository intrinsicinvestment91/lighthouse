from modules.provenance import ProvenanceRecord


def test_constructs_minimal_record():
    record = ProvenanceRecord(source_type="sec")
    assert record.source_type == "sec"
    assert record.metadata == {}


def test_to_dict_serializes():
    record = ProvenanceRecord(
        source_type="patent",
        source_url="https://example.com/patent",
        metadata={"key": "value"},
    )
    result = record.to_dict()
    assert result["source_type"] == "patent"
    assert result["source_url"] == "https://example.com/patent"
    assert result["metadata"] == {"key": "value"}


def test_from_dict_deserializes():
    data = {
        "source_type": "archive",
        "source_url": "https://example.com/archive",
        "retrieved_at": "2026-04-26T00:00:00Z",
        "metadata": {"agency": "NASA"},
    }
    record = ProvenanceRecord.from_dict(data)
    assert record.source_type == "archive"
    assert record.source_url == "https://example.com/archive"
    assert record.retrieved_at == "2026-04-26T00:00:00Z"
    assert record.metadata == {"agency": "NASA"}


def test_from_dict_missing_metadata_defaults_to_empty_dict():
    record = ProvenanceRecord.from_dict({"source_type": "flight"})
    assert record.metadata == {}
