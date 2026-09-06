from src.retrieval.query_analyzer import decompose_query, analyze_query


def test_decompose_accord_won_by_faction_member():
    """Verify decomposition of 1b_007 style query."""
    q = "Which accord was ultimately won by the faction of which Ederon Fellgard is a member?"
    sub_qs = decompose_query(q)
    assert len(sub_qs) == 2
    assert "Ederon Fellgard" in sub_qs[0]
    assert "accord" in sub_qs[1].lower()


def test_decompose_war_won_by_faction():
    """Verify decomposition of 1b_022 style query."""
    q = "Which war did Ravena Stormwell's own faction ultimately win?"
    sub_qs = decompose_query(q)
    assert len(sub_qs) == 2
    assert "Ravena Stormwell" in sub_qs[0]
    assert "war" in sub_qs[1].lower()


def test_decompose_dominion_lair():
    """Verify decomposition of 1b_013 style query."""
    q = "Whose dominion encompasses the lair of the Gravemaw Wyrm?"
    sub_qs = decompose_query(q)
    assert len(sub_qs) == 2
    assert "Gravemaw Wyrm" in sub_qs[0]
    assert "dominion" in sub_qs[1].lower()


def test_decompose_individual_victor_member():
    """Verify decomposition of 1b_006 style query."""
    q = "Which individual was a member of the faction that ultimately won the War of Drowned Light?"
    sub_qs = decompose_query(q)
    assert len(sub_qs) == 2
    assert "War of Drowned Light" in sub_qs[0]
    assert "members" in sub_qs[1].lower()


def test_decompose_simple_query():
    """Verify that a simple question is returned unchanged."""
    q = "What is the central emblem on the banner of House Morvain?"
    sub_qs = decompose_query(q)
    assert len(sub_qs) == 1
    assert sub_qs[0] == q


def test_query_analyzer_integrates_sub_questions():
    """Verify analyze_query populates sub_questions for multi-hop queries."""
    q = "Which accord was ultimately won by the faction of which Ederon Fellgard is a member?"
    analysis = analyze_query(q)
    assert analysis.query_type == "multi_hop"
    assert len(analysis.sub_questions) == 2
