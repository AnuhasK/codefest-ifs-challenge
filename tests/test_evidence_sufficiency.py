from src.models.query import QueryState, HopResult
from src.models.search import SearchResult
from src.knowledge.evidence import assess_evidence_sufficiency


def test_sufficiency_empty_evidence():
    """Verify empty evidence yields INSUFFICIENT."""
    state = QueryState(original_query="Test query", query_type="simple")
    score = assess_evidence_sufficiency("Test query", state)
    assert score.level == "INSUFFICIENT"
    assert score.coverage == 0.0


def test_sufficiency_multihop_strong_evidence():
    """Verify multi-hop query with >= 2 distinct docs and multiple hops yields HIGH."""
    state = QueryState(
        original_query="Which accord was won by the faction of Ederon Fellgard?",
        query_type="multi_hop",
        identified_entities=["Ederon Fellgard"],
    )
    chunk1 = SearchResult(chunk_id="c1", document_id="doc1", document_title="ederon_fellgard.md", content="Ederon Fellgard is a member of Iron-Ring Cartel.", score=0.9)
    chunk2 = SearchResult(chunk_id="c2", document_id="doc2", document_title="the_leaden_accord.md", content="The Leaden Accord was won by Iron-Ring Cartel.", score=0.85)

    state.retrieved_evidence = [chunk1, chunk2]
    state.evidence_per_hop[1] = [chunk1]
    state.evidence_per_hop[2] = [chunk2]
    state.traversal_hops = [
        HopResult(
            hop_number=1,
            source_entity="Ederon Fellgard",
            target_entity="Iron-Ring Cartel",
            relationship_type="MEMBER_OF",
        )
    ]

    score = assess_evidence_sufficiency("Which accord was won by the faction of Ederon Fellgard?", state)
    assert score.level == "HIGH"
    assert score.unique_documents >= 2


def test_sufficiency_multihop_single_document_low():
    """Verify multi-hop query confined to a single document yields LOW."""
    state = QueryState(
        original_query="Which accord was won by the faction of Ederon Fellgard?",
        query_type="multi_hop",
        identified_entities=["Ederon Fellgard"],
    )
    chunk1 = SearchResult(chunk_id="c1", document_id="doc1", document_title="ederon_fellgard.md", content="Ederon Fellgard is a sapper.", score=0.9)
    state.retrieved_evidence = [chunk1]

    score = assess_evidence_sufficiency("Which accord was won by the faction of Ederon Fellgard?", state)
    assert score.level in ("LOW", "INSUFFICIENT")
