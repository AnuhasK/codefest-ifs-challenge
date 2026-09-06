import pytest
from unittest.mock import MagicMock

from src.knowledge.graph import KnowledgeGraph
from src.knowledge.alias_resolution import verify_and_apply_alias_table


def test_verify_and_apply_alias_table_empty():
    mock_conn = MagicMock()
    kg = KnowledgeGraph(neo4j=mock_conn)

    stats = verify_and_apply_alias_table({}, kg)
    assert stats["resolved"] == 0
    assert stats["flagged"] == 0
    mock_conn.execute_write.assert_not_called()


def test_verify_and_apply_alias_table_resolution_and_flagging():
    mock_conn = MagicMock()
    kg = KnowledgeGraph(neo4j=mock_conn)

    alias_table = {
        "Lord Vaelith": "Vaelith",
        "Lord V.": "Vaelith",
        "the Lord of Mournthrone": "Vaelith",
        "Ashen Vanguard": "Ashen Vanguard",
        "the Vanguard": "Ashen Vanguard",
        "Suspicious Unknown Figure": "UNKNOWN_ALIAS",
        "Ambiguous Entity": "UNKNOWN_ALIAS",
    }

    stats = verify_and_apply_alias_table(alias_table, kg, batch_size=10)

    assert stats["resolved"] == 5
    assert stats["flagged"] == 2
    assert stats["canonical_count"] == 2

    # Verify write was called
    assert mock_conn.execute_write.call_count == 1
    call_args = mock_conn.execute_write.call_args[0]
    query = call_args[0]
    params = call_args[1]

    assert "MATCH (e:Entity)" in query
    assert "SET e.aliases = reduce" in query
    batch = params["batch"]
    assert len(batch) == 2

    vaelith_item = next(b for b in batch if b["canonical_name"] == "Vaelith")
    assert "Lord Vaelith" in vaelith_item["new_aliases"]
    assert "the Lord of Mournthrone" in vaelith_item["new_aliases"]
    assert "Vaelith" in vaelith_item["new_aliases"]
