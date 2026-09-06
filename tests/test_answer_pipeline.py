from typing import Optional
from unittest.mock import patch
from src.models.search import SearchResult

from src.generation.answer import answer_question
from src.retrieval.orchestrator import RetrievalConfig
from src.providers.llm_provider import LLMProvider, LLMResponse


class MockPipelineLLM(LLMProvider):
    def __init__(self, answer_text: str = "Default answer [EVIDENCE_001].", conflicts_json: Optional[str] = None):
        self.answer_text = answer_text
        self.conflicts_json = conflicts_json or '{"conflicts": []}'

    def generate(self, prompt: str, system_prompt: str = "", model=None) -> LLMResponse:
        # Check if this is conflict prompt or answer prompt or verification prompt
        if "identify any contradictions" in prompt:
            return LLMResponse(content=self.conflicts_json, tokens_used=20, model="mock")
        if "verify each factual claim" in prompt:
            return LLMResponse(content='{"claim_checks": [{"claim": "Ser Vael founded the order", "verdict": "supported", "evidence_ids": ["EVIDENCE_001"]}], "unsupported_claims": []}', tokens_used=30, model="mock")
        return LLMResponse(content=self.answer_text, tokens_used=50, model="mock")


    def generate_structured(self, prompt: str, response_schema, system_prompt="", model=None):
        return None

    def describe_image(self, image_path: str, prompt: str, model=None) -> str:
        return ""


def test_answer_pipeline_simple_question():
    mock_results = [
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            page_start=84,
            content="Ser Vael was a founding member of the Ashen Vanguard.",
            score=0.95,
        )
    ]
    mock_llm = MockPipelineLLM(answer_text="Ser Vael founded the order [EVIDENCE_001].")

    with patch("src.generation.answer.retrieve", return_value=mock_results):
        final = answer_question(
            query="Who founded the Ashen Vanguard?",
            config=RetrievalConfig(enable_multihop=False),
            llm=mock_llm,
        )

        assert "[Royal Annals, p.84]" in final.answer_text
        assert len(final.evidence) == 1
        assert len(final.citations) == 1
        assert final.citations[0]["document_title"] == "Royal Annals"
        assert final.citations[0]["page"] == 84
        assert final.verification_result is not None
        assert final.verification_result.is_verified is True
        assert "elapsed_time_s" in final.query_trace


def test_answer_pipeline_insufficient_evidence():
    with patch("src.generation.answer.retrieve", return_value=[]):
        final = answer_question(
            query="Where is the nonexistent phantom city?",
            config=RetrievalConfig(enable_multihop=False),
            llm=MockPipelineLLM(),
        )

        assert final.evidence_status == "INSUFFICIENT"
        assert "insufficient information" in final.answer_text.lower()
        assert len(final.citations) == 0


def test_answer_pipeline_conflicting_evidence():
    mock_results = [
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Trial Record",
            content="The accused, Ser Vael, was charged with betrayal of the Ashen Vanguard.",
            score=0.9,
            source_path="Ashen_Era_Archive/ephemera/trial_transcript_concerning_vael.pdf",
        ),
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            document_title="Ballad of Vael",
            content="And Vael the True was steadfast and loyal, he never wavered.",
            score=0.88,
            source_path="Ashen_Era_Archive/ephemera/ballad_concerning_vael.docx",
        ),
    ]

    mock_llm = MockPipelineLLM(
        answer_text="While trial records charge him with betrayal [EVIDENCE_001], ballads contend however that he remained loyal [EVIDENCE_002].",
        conflicts_json='{"conflicts": [{"claim": "Dispute regarding betrayal", "supporting": ["EVIDENCE_001"], "opposing": ["EVIDENCE_002"], "type": "contradiction"}]}',
    )


    with patch("src.generation.answer.retrieve", return_value=mock_results):
        final = answer_question(
            query="Did Ser Vael betray the Ashen Vanguard?",
            config=RetrievalConfig(enable_multihop=False),
            llm=mock_llm,
        )

        assert len(final.conflicts) >= 1
        assert "[Trial Record]" in final.answer_text or "Trial" in final.answer_text
        assert final.verification_result.is_verified is True
        assert len(final.verification_result.conflict_acknowledgements) >= 1
