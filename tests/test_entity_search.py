import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.retrieval.entity_search import entity_search
from src.retrieval.orchestrator import retrieve, RetrievalConfig
from src.retrieval.query_analyzer import QueryAnalysis
from src.models.entity import Entity
from src.models.search import SearchResult


def test_entity_search_empty_query():
    results = entity_search("")
    assert results == []


def test_entity_search_no_matching_entity():
    mock_kg = MagicMock()
    mock_kg.find_entity.return_value = None

    results = entity_search("Completely Unknown Query Entity Name XYZ", graph=mock_kg)
    assert results == []


def test_entity_search_matching_and_hydration():
    mock_kg = MagicMock()
    ent_id = str(uuid4())
    chunk1_id = str(uuid4())
    chunk2_id = str(uuid4())

    mock_kg.find_entity.side_effect = lambda name: Entity(
        id=ent_id,
        name="House Morvain",
        aliases=["Morvain"],
        entity_type="FACTION",
        source="gazette",
        confidence=1.0,
        mention_count=10,
    ) if "morvain" in name.lower() else None

    mock_kg.get_entity_chunks.return_value = [chunk1_id, chunk2_id]

    # Mock PostgreSQL hydration
    fake_rows = [
        {
            "id": chunk1_id,
            "document_id": str(uuid4()),
            "content": "The banner of House Morvain features golden keys.",
            "contextualized_content": "Chronicles of House Morvain: The banner features golden keys.",
            "section_title": "Heraldry",
            "chapter": "Chapter 1",
            "page_start": 12,
            "page_end": 13,
            "source_category": "wiki",
            "source_path": "wiki/house_morvain.md",
            "document_title": "House Morvain",
            "metadata": {"type": "wiki"},
        },
        {
            "id": chunk2_id,
            "document_id": str(uuid4()),
            "content": "A second chunk mentioning House Morvain.",
            "contextualized_content": None,
            "section_title": "History",
            "chapter": "Chapter 2",
            "page_start": 20,
            "page_end": 21,
            "source_category": "wiki",
            "source_path": "wiki/house_morvain.md",
            "document_title": "House Morvain",
            "metadata": {},
        },
    ]

    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = fake_rows
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur

    with patch("src.retrieval.entity_search.get_db_connection", return_value=mock_conn):
        qa = QueryAnalysis(
            original_query="Tell me about House Morvain",
            query_type="simple",
            entities_mentioned=["House Morvain"],
            bm25_query="House Morvain",
        )
        results = entity_search(
            query="Tell me about House Morvain",
            query_analysis=qa,
            graph=mock_kg,
            top_k=5,
        )

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].chunk_id in (chunk1_id, chunk2_id)
    assert results[0].metadata["retrieval_stream"] == "entity_search"
    assert "House Morvain" in results[0].metadata["matched_entities"]
    assert results[0].score == 1.0  # normalized top score
    assert results[0].rank == 1
    assert results[1].rank == 2


def test_orchestrator_4_stream_rrf_integration():
    # Test that orchestrator retrieves with enable_entity_search=True
    cfg = RetrievalConfig(
        enable_bm25=True,
        enable_dense=True,
        enable_contextual=True,
        enable_entity_search=True,
        enable_reranker=True,
        bm25_top_k=10,
        dense_top_k=10,
        contextual_top_k=10,
        entity_top_k=10,
        rrf_top_n=10,
        reranker_top_k=5,
    )
    assert cfg.enable_entity_search is True

    # Run on an existing real query against live PostgreSQL and Neo4j
    results = retrieve("House Morvain banner", config=cfg)
    assert isinstance(results, list)
    assert len(results) > 0
    assert all(isinstance(r, SearchResult) for r in results)
