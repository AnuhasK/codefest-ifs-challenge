import pytest
from unittest.mock import MagicMock
from src.database.neo4j_db import Neo4jConnection
from src.knowledge.graph import KnowledgeGraph
from src.models.entity import Entity


def test_neo4j_connection_verify():
    mock_driver = MagicMock()
    mock_driver.verify_connectivity.return_value = None

    conn = Neo4jConnection.__new__(Neo4jConnection)
    conn._driver = mock_driver

    assert conn.verify_connection() is True
    mock_driver.verify_connectivity.assert_called_once()

    # Simulate failure
    mock_driver.verify_connectivity.side_effect = Exception("Connection refused")
    assert conn.verify_connection() is False


def test_neo4j_init_schema():
    mock_session = MagicMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    conn = Neo4jConnection.__new__(Neo4jConnection)
    conn._driver = mock_driver

    conn.init_schema()
    assert mock_session.run.call_count == 4


def test_knowledge_graph_create_entity():
    mock_conn = MagicMock()
    kg = KnowledgeGraph(neo4j=mock_conn)

    ent = Entity(
        id="ent-123",
        name="Lord Vaelith",
        aliases=["Vaelith", "Lord of Mournthrone"],
        entity_type="PERSON",
        source="gazette",
        confidence=1.0,
        mention_count=5,
    )

    kg.create_entity(ent)
    mock_conn.execute_write.assert_called_once()

    call_args = mock_conn.execute_write.call_args
    query, params = call_args[0]
    assert "MERGE (e:Entity {id: $id})" in query
    assert params["id"] == "ent-123"
    assert params["name"] == "Lord Vaelith"
    assert params["entity_type"] == "PERSON"
    assert "Lord of Mournthrone" in params["aliases"]


def test_knowledge_graph_create_relationship():
    mock_conn = MagicMock()
    kg = KnowledgeGraph(neo4j=mock_conn)

    kg.create_relationship(
        source_id="ent-1",
        target_id="ent-2",
        rel_type="MEMBER_OF",
        properties={"since": "Year 402"},
    )
    mock_conn.execute_write.assert_called_once()

    call_args = mock_conn.execute_write.call_args
    query, params = call_args[0]
    assert "MERGE (s)-[r:MEMBER_OF]->(t)" in query
    assert params["source_id"] == "ent-1"
    assert params["target_id"] == "ent-2"
    assert params["props"]["since"] == "Year 402"


def test_knowledge_graph_invalid_relationship_type():
    mock_conn = MagicMock()
    kg = KnowledgeGraph(neo4j=mock_conn)

    with pytest.raises(ValueError):
        kg.create_relationship("ent-1", "ent-2", "INVALID-REL; DROP TABLE;")


def test_knowledge_graph_link_entity_to_chunk():
    mock_conn = MagicMock()
    kg = KnowledgeGraph(neo4j=mock_conn)

    kg.link_entity_to_chunk("ent-1", "chunk-99", "doc-88")
    mock_conn.execute_write.assert_called_once()

    query, params = mock_conn.execute_write.call_args[0]
    assert "MERGE (e)-[:MENTIONED_IN]->(c)" in query
    assert "MERGE (e)-[:APPEARS_IN]->(d)" in query
    assert params["chunk_id"] == "chunk-99"
    assert params["document_id"] == "doc-88"


def test_knowledge_graph_find_entity():
    mock_conn = MagicMock()
    mock_conn.execute_query.return_value = [
        {
            "id": "uuid-123",
            "name": "House Morvain",
            "entity_type": "FACTION",
            "aliases": ["Morvain", "House of Morvain"],
            "source": "gazette",
            "confidence": 1.0,
            "mention_count": 12,
        }
    ]
    kg = KnowledgeGraph(neo4j=mock_conn)

    res = kg.find_entity("morvain")
    assert res is not None
    assert res.id == "uuid-123"
    assert res.name == "House Morvain"
    assert res.entity_type == "FACTION"
    assert "Morvain" in res.aliases
