import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.models.search import SearchResult
from src.providers.llm_provider import LLMProvider, get_llm_provider
from src.generation.prompts import SYSTEM_PROMPT_BASELINE_RAG, QA_USER_PROMPT_TEMPLATE
from src.generation.context_builder import build_evidence_context


class Citation(BaseModel):
    """Resolved citation referencing original document and page."""
    evidence_id: str
    document_title: str
    source_path: Optional[str] = None
    page: Optional[int] = None
    excerpt: str


class GroundedAnswer(BaseModel):
    """Complete grounded answer object with resolved citations and evidence traceability."""
    question: str
    answer_text: str
    citations: List[Citation] = Field(default_factory=list)
    evidence_used: List[str] = Field(default_factory=list)
    tokens_used: int = 0
    model_used: str = ""
    evidence_count: int = 0


def generate_grounded_answer(
    question: str,
    evidence: List[SearchResult],
    llm: Optional[LLMProvider] = None,
    model: Optional[str] = None,
) -> GroundedAnswer:
    """
    Generate an evidence-grounded answer with deterministic citation resolution.
    """
    if not evidence:
        return GroundedAnswer(
            question=question,
            answer_text="Based on the provided archive evidence, there is insufficient information to answer this question.",
            citations=[],
            evidence_used=[],
            tokens_used=0,
            model_used=model or "",
            evidence_count=0,
        )

    if llm is None:
        llm = get_llm_provider()

    formatted_context, evidence_map = build_evidence_context(evidence)

    user_prompt = QA_USER_PROMPT_TEMPLATE.format(
        question=question,
        context=formatted_context,
    )

    response = llm.generate(
        prompt=user_prompt,
        system_prompt=SYSTEM_PROMPT_BASELINE_RAG,
        model=model,
    )

    answer_text = response.content.strip()

    # Extract all cited [EVIDENCE_X] or EVIDENCE_X mentions
    cited_ids = set(re.findall(r"EVIDENCE_(\d+)", answer_text))
    citations: List[Citation] = []
    used_ids: List[str] = []

    for cid in sorted(cited_ids, key=int):
        ev_key = f"EVIDENCE_{cid}"
        if ev_key in evidence_map:
            res = evidence_map[ev_key]
            citations.append(
                Citation(
                    evidence_id=ev_key,
                    document_title=res.document_title or "Ashen Era Archive Document",
                    source_path=res.source_path,
                    page=res.page_start,
                    excerpt=res.content[:200] + "..." if len(res.content) > 200 else res.content,
                )
            )
            used_ids.append(ev_key)

    return GroundedAnswer(
        question=question,
        answer_text=answer_text,
        citations=citations,
        evidence_used=used_ids,
        tokens_used=response.tokens_used,
        model_used=response.model,
        evidence_count=len(evidence),
    )
