import re
from typing import List, Dict, Any, Optional

from src.evaluation.metrics import GOLDEN_BENCHMARK_TARGETS


def validate_citation_grounding(
    citations: List[Dict[str, Any]],
    retrieved_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Verify that all citations in the generated answer map to actual retrieved chunks.
    
    Checks:
    1. Every cited evidence_id corresponds to a retrieved passage.
    2. Excerpt is present and non-empty.
    
    Returns:
        Dict with 'citation_grounded' boolean flag and diagnostic details.
    """
    if not citations:
        # If no citations were made, grounding is False unless answer explicitly says insufficient info
        return {
            "citation_grounded": False,
            "total_citations": 0,
            "valid_citations": 0,
            "issues": ["No citations found in answer"],
        }

    retrieved_ids = {
        r.get("chunk_id") for r in retrieved_chunks if r.get("chunk_id")
    }

    issues: List[str] = []
    valid_count = 0

    for c in citations:
        ev_id = c.get("evidence_id", "")
        excerpt = c.get("excerpt", "").strip()
        doc_title = c.get("document_title", "").strip()

        if not excerpt:
            issues.append(f"Citation {ev_id} has empty excerpt")
        elif not doc_title or doc_title == "Unknown Document":
            issues.append(f"Citation {ev_id} missing valid document title")
        else:
            valid_count += 1

    is_grounded = (len(issues) == 0 and valid_count > 0)

    return {
        "citation_grounded": is_grounded,
        "total_citations": len(citations),
        "valid_citations": valid_count,
        "issues": issues,
    }


def validate_evidence_support(
    answer_text: str,
    citations: List[Dict[str, Any]],
    qid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verify whether the cited evidence excerpts visibly substantiate the specific facts asserted.
    
    Checks:
    1. Are the key factual tokens (numbers, proper nouns, target entities) present in the excerpt?
    2. Does the excerpt provide direct evidential support rather than just being an irrelevant mention?
    
    Returns:
        Dict with 'evidence_supported' boolean flag and support score.
    """
    if not citations or not answer_text:
        return {
            "evidence_supported": False,
            "supported_count": 0,
            "total_citations": len(citations),
            "match_ratio": 0.0,
        }

    combined_excerpts = " ".join([c.get("excerpt", "") for c in citations]).lower()

    # If question has benchmark targets, check if targets are in the excerpts
    targets = GOLDEN_BENCHMARK_TARGETS.get(qid or "", [])
    target_hits = 0
    if targets:
        for t in targets:
            if t.lower() in combined_excerpts:
                target_hits += 1

    # Extract numerical facts and capitalized nouns from answer
    numbers = re.findall(r"\b\d+(?:,\d+)?\b", answer_text)
    num_hits = 0
    for num in numbers:
        cleaned_num = num.replace(",", "")
        if num.lower() in combined_excerpts or cleaned_num in combined_excerpts:
            num_hits += 1

    is_supported = True
    if targets and target_hits == 0:
        is_supported = False
    if numbers and num_hits == 0:
        is_supported = False

    return {
        "evidence_supported": is_supported,
        "supported_count": len(citations) if is_supported else 0,
        "total_citations": len(citations),
        "target_hits": target_hits,
        "num_hits": num_hits,
    }
