import pytest
from unittest.mock import MagicMock, call
from uuid import uuid4

from src.ingestion.entities import ExtractedEntity
from src.ingestion.graph_storage import (
    generate_entity_id,
    store_entities_in_neo4j,
    SOURCE_PRIORITY,
)


def test_generate_entity_id_deterministic():
    id1 = generate_entity_id("Lord Vaelith", "PERSON")
    id2 = generate_entity_id("lord vaelith", "PERSON")
    id3 = generate_entity_id("  Lord Vaelith  ", "PERSON")
    id4 = generate_entity_id("House Morvain", "FACTION")

    assert id1 == id2 == id3
    assert id1 != id4
    assert len(id1) == 36  # Valid UUID string format


def test_store_entities_in_neo4j_empty():
    mock_conn = MagicMock()
    result = store_entities_in_neo4j([], neo4j_conn=mock_conn)

    assert result["status"] == "empty"
    assert result["entities_saved"] == 0
    mock_conn.execute_write.assert_not_called()


def test_store_entities_in_neo4j_connection_failure():
    mock_conn = MagicMock()
    mock_conn.init_schema.side_effect = Exception("Neo4j connection refused")

    ent = ExtractedEntity(
        name="Vaelith",
        entity_type="PERSON",
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        source="gazette",
        confidence=1.0,
    )
    result = store_entities_in_neo4j([ent], neo4j_conn=mock_conn)

    assert result["status"] == "connection_failed"
    assert "refused" in result.get("error", "")
    mock_conn.execute_write.assert_not_called()


def test_store_entities_in_neo4j_aggregation_and_execution():
    mock_conn = MagicMock()
    mock_conn.init_schema.return_value = None

    chunk1_id = str(uuid4())
    chunk2_id = str(uuid4())
    doc1_id = str(uuid4())

    entities = [
        ExtractedEntity(
            name="House Morvain",
            entity_type="FACTION",
            mentions=["Morvain", "House of Morvain"],
            chunk_id=chunk1_id,
            document_id=doc1_id,
            source="rules",
            confidence=0.5,
        ),
        ExtractedEntity(
            name="House Morvain",
            entity_type="FACTION",
            mentions=["the Morvain banner"],
            chunk_id=chunk2_id,
            document_id=doc1_id,
            source="gazette",
            confidence=1.0,
        ),
        ExtractedEntity(
            name="Ignatz Ashgrove",
            entity_type="PERSON",
            mentions=["Ignatz the Oathless"],
            chunk_id=chunk1_id,
            document_id=doc1_id,
            source="gemini_ner",
            confidence=0.9,
        ),
    ]

    result = store_entities_in_neo4j(entities, neo4j_conn=mock_conn, batch_size=10)

    assert result["status"] == "success"
    assert result["entities_saved"] == 2
    assert result["chunk_links"] == 3  # (Morvain -> c1), (Morvain -> c2), (Ignatz -> c1)
    assert result["doc_links"] == 2    # (Morvain -> d1), (Ignatz -> d1)

    # Verify write transactions were executed (nodes, chunk edges, doc edges)
    assert mock_conn.execute_write.call_count == 3

    # Inspect the entity records written in call 1
    entity_call_args = mock_conn.execute_write.call_args_list[0]
    entity_batch = entity_call_args[0][1]["batch"]
    assert len(entity_batch) == 2

    # Find Morvain in the batch
    morvain_rec = next(r for r in entity_batch if r["name"] == "House Morvain")
    assert morvain_rec["type"] == "FACTION"
    assert morvain_rec["source"] == "gazette"  # gazette priority > rules
    assert morvain_rec["confidence"] == 1.0     # max confidence
    assert morvain_rec["mention_count"] == 2
    assert "Morvain" in morvain_rec["aliases"]
    assert "the Morvain banner" in morvain_rec["aliases"]
