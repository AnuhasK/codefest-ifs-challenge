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

# Granular multi-hop targets for Track 1B questions.
# Each entry specifies the sequential hop requirements:
# [ [hop1_keywords/entities], [hop2_keywords/entities] ]
MULTIHOP_BENCHMARK_TARGETS: Dict[str, List[List[str]]] = {
    "1b_007": [
        ["Ederon Fellgard", "ederon_fellgard"],
        ["Leaden Accord", "the_leaden_accord", "Iron-Ring Cartel was a victor", "victor of The Leaden Accord"],
    ],
    "1b_006": [
        ["War of Drowned Light", "the_war_of_drowned_light"],
        ["the_silent_choir", "Ignatz Fellgard", "Brannoc Palefroth", "Thessaly Coldwater", "Lucan Hollowmere", "Tamsin Greyfen", "Ossric Ashgrove", "Ravena Stormwell", "Isolde Mournvale"],
    ],
    "1b_022": [
        ["Ravena Stormwell", "ravena_stormwell"],
        ["War of Drowned Light", "the_war_of_drowned_light", "Winter Reckoning", "the_winter_reckoning"],
    ],
    "1b_013": [
        ["Gravemaw Wyrm", "gravemaw_wyrm"],
        ["Marrowwell Abbey", "marrowwell_abbey", "Gareth Ironmere", "Bleeding Crown"],
    ],
    "1b_005": [
        ["Isolde Mournvale", "isolde_mournvale"],
        ["War of Drowned Light", "the_war_of_drowned_light", "Winter Reckoning", "the_winter_reckoning"],
    ],
    "1b_009": [
        ["Purge of Blackport", "the_purge_of_blackport"],
        ["Halvard Crowhurst", "halvard_crowhurst"],
    ],
    "1b_003": [
        ["Cerys Sablewood", "cerys_sablewood_the_ashen"],
        ["the_cinder_wrought_aegis", "Cinder-Wrought Aegis", "Gloamreach"],
    ],
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
    Compute standard Hit@K (backward compatible with Phase 1-4).
    Returns 1.0 if ANY expected target appears in the top-K items; otherwise 0.0.
    """
    if not retrieved_items or not expected_targets or k <= 0:
        return 0.0

    top_slice = retrieved_items[:k]
    for item in top_slice:
        txt = item.get("content", "")
        title = item.get("document_title", "")
        if item_matches_targets(txt, title, expected_targets):
            return 1.0

    return 0.0


def compute_joint_recall_at_k(
    retrieved_items: List[Dict[str, Any]],
    hop_targets: List[List[str]],
    k: int,
    require_distinct: bool = True,
) -> float:
    """
    Compute Joint Multi-Target Recall@K for multi-hop cross-document retrieval.
    Requires at least one chunk for Hop 1 AND at least one chunk for Hop 2 in the top-K,
    with distinct documents supporting each hop when require_distinct=True.
    """
    if not retrieved_items or not hop_targets or k <= 0:
        return 0.0

    top_slice = retrieved_items[:k]

    # Single-hop fallback: behaves identically to standard Hit@K
    if len(hop_targets) == 1:
        return compute_recall_at_k(retrieved_items, hop_targets[0], k)

    # For multi-hop (e.g. 2 hops):
    # Find which items in top_slice match which hops
    hop_matching_item_indices: List[set] = []
    for hop_keywords in hop_targets:
        matching_indices = set()
        for idx, item in enumerate(top_slice):
            txt = item.get("content", "")
            title = item.get("document_title", "")
            if item_matches_targets(txt, title, hop_keywords):
                matching_indices.add(idx)
        if not matching_indices:
            return 0.0
        hop_matching_item_indices.append(matching_indices)

    if not require_distinct:
        return 1.0

    # Helper to resolve document identity
    def get_doc_id(idx: int) -> str:
        item = top_slice[idx]
        return str(item.get("document_id") or item.get("document_title") or idx)

    # Verify distinct document assignment for 2 hops:
    if len(hop_targets) == 2:
        h1_matches = hop_matching_item_indices[0]
        h2_matches = hop_matching_item_indices[1]
        for i in h1_matches:
            for j in h2_matches:
                if get_doc_id(i) != get_doc_id(j):
                    return 1.0
        return 0.0

    # General greedy distinct document check for N >= 3 hops
    used_docs = set()
    for matching_indices in hop_matching_item_indices:
        matching_docs = {get_doc_id(idx) for idx in matching_indices}
        available = matching_docs - used_docs
        if not available:
            return 0.0
        used_docs.add(next(iter(available)))

    return 1.0


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
    hop_targets = MULTIHOP_BENCHMARK_TARGETS.get(qid)

    metrics = {
        "recall_at_1": compute_recall_at_k(retrieved_items, targets, k=1),
        "recall_at_3": compute_recall_at_k(retrieved_items, targets, k=3),
        "recall_at_5": compute_recall_at_k(retrieved_items, targets, k=5),
        "recall_at_10": compute_recall_at_k(retrieved_items, targets, k=10),
        "mrr": compute_mrr(retrieved_items, targets),
    }

    if hop_targets:
        metrics["joint_recall_at_1"] = compute_joint_recall_at_k(retrieved_items, hop_targets, k=1)
        metrics["joint_recall_at_3"] = compute_joint_recall_at_k(retrieved_items, hop_targets, k=3)
        metrics["joint_recall_at_5"] = compute_joint_recall_at_k(retrieved_items, hop_targets, k=5)
        metrics["joint_recall_at_10"] = compute_joint_recall_at_k(retrieved_items, hop_targets, k=10)
    else:
        # Single-hop question: joint recall is identical to standard recall
        metrics["joint_recall_at_1"] = metrics["recall_at_1"]
        metrics["joint_recall_at_3"] = metrics["recall_at_3"]
        metrics["joint_recall_at_5"] = metrics["recall_at_5"]
        metrics["joint_recall_at_10"] = metrics["recall_at_10"]

    return metrics
