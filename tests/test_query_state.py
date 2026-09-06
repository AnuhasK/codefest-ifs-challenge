from src.models.query import QueryState, HopResult, SufficiencyScore
from src.models.search import SearchResult


def test_query_state_initialization():
    """Verify QueryState default attributes and iteration tracking."""
    state = QueryState(
        original_query="Which accord was won by the faction Ederon Fellgard belongs to?",
        query_type="multi_hop",
        sub_questions=["Which faction is Ederon Fellgard a member of?"],
        identified_entities=["Ederon Fellgard"],
    )
    assert state.iteration_count == 0
    assert state.max_iterations == 3
    assert state.discovered_entities == []
    assert state.evidence_per_hop == {}


def test_query_state_hop_tracking():
    """Verify recording evidence per hop."""
    state = QueryState(original_query="Test query")

    chunk1 = SearchResult(chunk_id="c1", document_id="d1", content="Hop 1 evidence", score=0.9)
    chunk2 = SearchResult(chunk_id="c2", document_id="d2", content="Hop 2 evidence", score=0.8)

    state.evidence_per_hop[1] = [chunk1]
    state.evidence_per_hop[2] = [chunk2]

    state.discovered_entities.append("The Iron-Ring Cartel")
    state.traversal_hops.append(
        HopResult(
            hop_number=1,
            source_entity="Ederon Fellgard",
            target_entity="The Iron-Ring Cartel",
            relationship_type="MEMBER_OF",
            evidence_chunk_ids=["c1"],
            path_description="Ederon Fellgard -[MEMBER_OF]- The Iron-Ring Cartel",
        )
    )

    assert len(state.evidence_per_hop[1]) == 1
    assert len(state.evidence_per_hop[2]) == 1
    assert len(state.traversal_hops) == 1
    assert state.traversal_hops[0].relationship_type == "MEMBER_OF"
