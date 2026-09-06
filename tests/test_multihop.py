from unittest.mock import MagicMock
from src.knowledge.multihop import multi_hop_search, hydrate_evidence_chunks
from src.knowledge.graph import KnowledgeGraph


def test_multihop_search_empty_entities():
    """Verify empty seed entities list returns empty results."""
    hops, discovered, cids = multi_hop_search([])
    assert hops == []
    assert discovered == []
    assert cids == []


def test_multihop_search_with_mocked_graph():
    """Verify multi-hop path extraction and entity discovery."""
    mock_kg = MagicMock(spec=KnowledgeGraph)
    mock_kg.find_multihop_paths.return_value = [
        {
            "nodes": [
                {"id": "id1", "name": "Ederon Fellgard", "type": "PERSON"},
                {"id": "id2", "name": "The Iron-Ring Cartel", "type": "FACTION"},
                {"id": "id3", "name": "The Leaden Accord", "type": "EVENT"},
            ],
            "relationships": [
                {
                    "type": "MEMBER_OF",
                    "evidence_chunk_id": "c1111111-1111-1111-1111-111111111111",
                    "evidence_text": "member",
                },
                {
                    "type": "WON",
                    "evidence_chunk_id": "c2222222-2222-2222-2222-222222222222",
                    "evidence_text": "victor",
                },
            ],
            "hops": 2,
        }
    ]

    hops, discovered, cids = multi_hop_search(
        start_entities=["Ederon Fellgard"],
        graph=mock_kg,
        max_hops=2,
    )

    assert len(hops) == 2
    assert hops[0].source_entity == "Ederon Fellgard"
    assert hops[0].target_entity == "The Iron-Ring Cartel"
    assert hops[0].relationship_type == "MEMBER_OF"
    assert hops[1].source_entity == "The Iron-Ring Cartel"
    assert hops[1].target_entity == "The Leaden Accord"
    assert hops[1].relationship_type == "WON"

    assert "The Iron-Ring Cartel" in discovered
    assert "The Leaden Accord" in discovered
    assert "c1111111-1111-1111-1111-111111111111" in cids
    assert "c2222222-2222-2222-2222-222222222222" in cids


def test_hydrate_evidence_chunks_empty():
    """Verify empty chunk_ids list returns empty list."""
    assert hydrate_evidence_chunks([]) == []
    assert hydrate_evidence_chunks(["invalid-uuid"]) == []
