from src.models.search import SearchResult
from src.models.evidence import Conflict, EvidenceRecord
from src.knowledge.evidence import EvidenceManager
from src.generation.verification import verify_answer
from src.providers.llm_provider import LLMProvider, LLMResponse


class MockVerifyLLM(LLMProvider):
    def __init__(self, json_payload: str):
        self.json_payload = json_payload

    def generate(self, prompt: str, system_prompt: str = "", model=None) -> LLMResponse:
        return LLMResponse(content=self.json_payload, tokens_used=60, model="mock")

    def generate_structured(self, prompt: str, response_schema, system_prompt="", model=None):
        return None

    def describe_image(self, image_path: str, prompt: str, model=None) -> str:
        return ""


def test_verify_supported_answer_heuristic():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            content="Ser Vael founded the Ashen Vanguard in 312 AS.",
            score=0.9,
        )
    )

    answer = "Ser Vael founded the Ashen Vanguard in 312 AS [EVIDENCE_001]."
    result = verify_answer(answer, manager)

    assert result.is_verified is True
    assert len(result.citation_issues) == 0
    assert len(result.unsupported_claims) == 0


def test_verify_unsupported_claim():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            content="Ser Vael founded the Ashen Vanguard.",
            score=0.9,
        )
    )

    # Introduces completely fabricated proper nouns and facts
    answer = "Lord Zephyros destroyed the Iron Fortress of Valdor [EVIDENCE_001]."
    result = verify_answer(answer, manager)

    assert result.is_verified is False
    assert len(result.unsupported_claims) > 0


def test_verify_invalid_citation_flagged():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            content="Ser Vael founded the Ashen Vanguard.",
            score=0.9,
        )
    )

    answer = "Ser Vael was a founder [EVIDENCE_999]."
    result = verify_answer(answer, manager)

    assert result.is_verified is False
    assert len(result.citation_issues) == 1
    assert result.citation_issues[0].evidence_id == "EVIDENCE_999"


def test_verify_missing_conflict_acknowledgement():
    manager = EvidenceManager()
    r1 = manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Trial Record",
            content="The accused betrayed the pact.",
            score=0.9,
        )
    )
    r2 = manager.register_evidence(
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            document_title="Ballad",
            content="He remained loyal throughout.",
            score=0.85,
        )
    )
    conflict = Conflict(
        claim_summary="Whether Vael betrayed or remained loyal",
        supporting_evidence=[r1],
        opposing_evidence=[r2],
        conflict_type="contradiction",
    )

    # One-sided answer with NO conflict acknowledgement keywords
    unacknowledged_answer = "Ser Vael betrayed the pact completely [EVIDENCE_001]."
    res1 = verify_answer(unacknowledged_answer, manager, conflicts=[conflict])
    assert res1.is_verified is False
    assert any("not acknowledged" in msg for msg in res1.conflict_acknowledgements)

    # Answer that acknowledges the dispute
    acknowledged_answer = (
        "While trial records allege that Ser Vael betrayed the pact [EVIDENCE_001], "
        "ballads contend however that he remained loyal [EVIDENCE_002]."
    )
    res2 = verify_answer(acknowledged_answer, manager, conflicts=[conflict])
    assert res2.is_verified is True
    assert any("Acknowledged dispute" in msg for msg in res2.conflict_acknowledgements)
