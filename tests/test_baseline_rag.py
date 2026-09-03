from src.models.search import SearchResult
from src.generation.context_builder import build_evidence_context
from src.generation.answer import generate_grounded_answer
from src.providers.llm_provider import LLMProvider, LLMResponse


class MockLLM(LLMProvider):
    def generate(self, prompt: str, system_prompt: str = "", model=None) -> LLMResponse:
        return LLMResponse(
            content="Ser Vael broke the accord at Red Vale [EVIDENCE_1] and fled to the marshes [EVIDENCE_2].",
            tokens_used=45,
            model="mock-llm",
        )

    def generate_structured(self, prompt: str, response_schema, system_prompt="", model=None):
        return None

    def describe_image(self, image_path: str, prompt: str, model=None) -> str:
        return "Mock image description"


def test_build_evidence_context():
    res1 = SearchResult(
        chunk_id="c1",
        document_id="d1",
        content="Ser Vael broke the accord at Red Vale.",
        score=0.92,
        page_start=14,
        chapter="Chapter 3",
        section_title="The Break",
        document_title="The Kindling Years",
        rank=1,
    )
    res2 = SearchResult(
        chunk_id="c2",
        document_id="d2",
        content="He was last seen fleeing towards the eastern marshes.",
        score=0.88,
        page_start=22,
        chapter="Chapter 5",
        section_title="Flight",
        document_title="Codex Vaeloria",
        rank=2,
    )

    context_str, evidence_map = build_evidence_context([res1, res2])
    assert "[EVIDENCE_1]" in context_str
    assert "[EVIDENCE_2]" in context_str
    assert "The Kindling Years" in context_str
    assert "Codex Vaeloria" in context_str
    assert "EVIDENCE_1" in evidence_map
    assert "EVIDENCE_2" in evidence_map


def test_generate_grounded_answer_with_citations():
    res1 = SearchResult(
        chunk_id="c1",
        document_id="d1",
        content="Ser Vael broke the accord at Red Vale.",
        score=0.92,
        page_start=14,
        document_title="The Kindling Years",
        rank=1,
    )
    res2 = SearchResult(
        chunk_id="c2",
        document_id="d2",
        content="He was last seen fleeing towards the eastern marshes.",
        score=0.88,
        page_start=22,
        document_title="Codex Vaeloria",
        rank=2,
    )

    mock_llm = MockLLM()
    answer = generate_grounded_answer(
        question="What did Ser Vael do?",
        evidence=[res1, res2],
        llm=mock_llm,
    )

    assert "[EVIDENCE_1]" in answer.answer_text
    assert len(answer.citations) == 2
    assert answer.citations[0].document_title == "The Kindling Years"
    assert answer.citations[0].page == 14
    assert answer.citations[1].document_title == "Codex Vaeloria"
    assert answer.citations[1].page == 22
    assert "EVIDENCE_1" in answer.evidence_used
    assert "EVIDENCE_2" in answer.evidence_used


def test_generate_grounded_answer_insufficient_evidence():
    answer = generate_grounded_answer(
        question="Where is the lost crown?",
        evidence=[],
    )
    assert "insufficient information" in answer.answer_text.lower()
    assert len(answer.citations) == 0
