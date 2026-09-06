from src.retrieval.query_analyzer import (
    analyze_query,
    extract_entities_heuristic,
    classify_query_type,
    build_bm25_query,
)


def test_extract_entities_heuristic():
    q1 = "What is the central emblem on the banner of House Morvain?"
    ents1 = extract_entities_heuristic(q1)
    assert any("House Morvain" in e for e in ents1)

    q2 = "According to the official plate, what rating is assigned to the creature Weeping Lurker?"
    ents2 = extract_entities_heuristic(q2)
    assert any("Weeping Lurker" in e for e in ents2)

    q3 = 'On the figure plate depicting "The Cinder-Wrought Aegis", what cost is listed?'
    ents3 = extract_entities_heuristic(q3)
    assert any("The Cinder-Wrought Aegis" in e or "Cinder-Wrought Aegis" in e for e in ents3)


def test_classify_query_type():
    assert classify_query_type("Compare House Morvain and Ashen Vanguard", ["House Morvain", "Ashen Vanguard"]) == "comparison"
    assert classify_query_type("Which war did the faction containing Isolde Mournvale win?", ["Isolde Mournvale"]) == "multi_hop"
    assert classify_query_type("Whose dominion encompasses the lair of the Gravemaw Wyrm?", ["Gravemaw Wyrm"]) == "multi_hop"
    assert classify_query_type("What is the central emblem on the banner of House Morvain?", ["House Morvain"]) == "simple"


def test_build_bm25_query():
    q = "What is the central emblem on the banner of House Morvain?"
    ents = ["House Morvain"]
    bm25_q = build_bm25_query(q, ents)
    assert "House Morvain" in bm25_q
    assert "emblem" in bm25_q
    assert "what" not in bm25_q.lower().split()
    assert "the" not in bm25_q.lower().split()


def test_analyze_query_full():
    res = analyze_query("Which war was won by the organization that included Isolde Mournvale as one of its members?")
    assert res.query_type == "multi_hop"
    assert any("Isolde Mournvale" in e for e in res.entities_mentioned)
    assert len(res.expanded_queries) >= 1
    assert "Isolde Mournvale" in res.bm25_query
