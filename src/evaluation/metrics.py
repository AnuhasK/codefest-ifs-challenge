from typing import List, Dict, Any, Optional

# Reference ground-truth keywords, document title tokens, and entity markers for each question in sample_questions.json
GOLDEN_BENCHMARK_TARGETS: Dict[str, List[str]] = {
    "1a_v12": ["House Morvain", "heraldry", "banner", "crossed golden keys", "keys"],
    "1a_v06": ["Ignatz Ashgrove", "Oathless", "scroll", "portrait"],
    "1a_008": ["Weeping Lurker", "threat rating", "10", "3"],
    "1a_004": ["Thrice-Bound Edge", "Thrice Bound Edge", "attunement", "shards", "20"],
    "1a_013": ["Cinder-Wrought Aegis", "Cinder Wrought Aegis", "attunement", "20"],
    "1a_v11": ["Ashen Vanguard", "banner", "heraldry", "emblem"],
    "1a_v07": ["Aldous Wrenfield", "Last Warden", "portrait"],
    "1a_009": ["Greyfell Citadel", "garrison", "3695", "3,695"],
    "1a_007": ["Thrice-Bound Lantern", "Thrice Bound Lantern", "attunement", "20"],
    "1a_v21": ["Gauntlet of Sorrowfell", "Gauntlet Of Sorrowfell", "motif", "illustration"],
    "1a_001": ["Emberdeep", "garrison", "800"],
    "1b_007": ["Ederon Fellgard", "accord", "faction"],
    "1b_006": ["War of Drowned Light", "War Of Drowned Light", "faction", "won"],
    "1b_022": ["Ravena Stormwell", "faction", "war"],
    "1b_013": ["Gravemaw Wyrm", "lair", "dominion"],
    "1b_005": ["Isolde Mournvale", "war", "organization", "faction"],
    "1b_009": ["Halvard Crowhurst", "Purge of Blackport", "Blackport"],
    "1b_003": ["Cerys Sablewood", "Sablewood", "relic", "356 AS"],
    "1c_000": ["Gloamreach", "founding", "founded", "Age of Shadows"],
    "1c_003": ["Gauntlet of Sorrowfell", "Gauntlet Of Sorrowfell", "forged", "year"],
}


def item_matches_targets(item_text: str, item_title: str, expected_targets: List[str]) -> bool:
    """Check if a retrieved item's title or text contains any of the target identifiers."""
    combined = f"{item_title} {item_text}".lower()
    for target in expected_targets:
        if target.lower() in combined:
            return True
    return False


def compute_recall_at_k(
    retrieved_items: List[Dict[str, Any]],
    expected_targets: List[str],
    k: int,
) -> float:
    """
    Compute Recall@K:
    Returns 1.0 if at least one relevant passage/target appears in the top K results; else 0.0.
    """
    if not retrieved_items or not expected_targets:
        return 0.0

    top_slice = retrieved_items[:k]
    for item in top_slice:
        txt = item.get("content", "")
        title = item.get("document_title", "")
        if item_matches_targets(txt, title, expected_targets):
            return 1.0

    return 0.0


def compute_mrr(
    retrieved_items: List[Dict[str, Any]],
    expected_targets: List[str],
) -> float:
    """
    Compute Mean Reciprocal Rank (MRR):
    Returns 1.0 / rank of the first relevant retrieved passage; returns 0.0 if not found.
    """
    if not retrieved_items or not expected_targets:
        return 0.0

    for rank, item in enumerate(retrieved_items, start=1):
        txt = item.get("content", "")
        title = item.get("document_title", "")
        if item_matches_targets(txt, title, expected_targets):
            return round(1.0 / rank, 4)

    return 0.0


def compute_retrieval_metrics(
    retrieved_items: List[Dict[str, Any]],
    qid: str,
) -> Dict[str, float]:
    """Compute complete suite of objective retrieval metrics for a question."""
    targets = GOLDEN_BENCHMARK_TARGETS.get(qid, [])
    if not targets:
        return {
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
        }

    return {
        "recall_at_1": compute_recall_at_k(retrieved_items, targets, k=1),
        "recall_at_3": compute_recall_at_k(retrieved_items, targets, k=3),
        "recall_at_5": compute_recall_at_k(retrieved_items, targets, k=5),
        "recall_at_10": compute_recall_at_k(retrieved_items, targets, k=10),
        "mrr": compute_mrr(retrieved_items, targets),
    }
