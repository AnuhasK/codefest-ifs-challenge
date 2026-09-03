from typing import List, Tuple, Dict, Any
from src.models.search import SearchResult


def build_evidence_context(
    results: List[SearchResult],
    max_tokens: int = 3500,
) -> Tuple[str, Dict[str, SearchResult]]:
    """
    Format search results into structured evidence blocks with sequential IDs [EVIDENCE_1], [EVIDENCE_2], etc.
    
    Returns:
        - formatted_context_string: String passed to LLM prompt
        - evidence_map: Dict mapping '[EVIDENCE_X]' to its SearchResult
    """
    context_blocks = []
    evidence_map: Dict[str, SearchResult] = {}

    current_approx_tokens = 0

    for i, res in enumerate(results, start=1):
        ev_id = f"EVIDENCE_{i}"
        evidence_map[ev_id] = res

        doc_title = res.document_title or res.metadata.get("source_file", "Unknown Document")
        location_parts = []
        if res.page_start:
            if res.page_end and res.page_end != res.page_start:
                location_parts.append(f"pp. {res.page_start}-{res.page_end}")
            else:
                location_parts.append(f"p. {res.page_start}")
        if res.chapter:
            location_parts.append(f"Chapter: {res.chapter}")
        if res.section_title:
            location_parts.append(f"Section: {res.section_title}")

        loc_str = f" ({', '.join(location_parts)})" if location_parts else ""

        block = f"[{ev_id}] Source: {doc_title}{loc_str}\n{res.content.strip()}\n"
        
        # Word-to-token heuristic (~1.3 tokens/word)
        approx_tokens = len(block.split()) * 1.3
        if current_approx_tokens + approx_tokens > max_tokens and context_blocks:
            break

        context_blocks.append(block)
        current_approx_tokens += approx_tokens

    formatted_context = "\n".join(context_blocks)
    return formatted_context, evidence_map
