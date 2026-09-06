from src.models.evidence import EvidenceRecord, SourceCharacteristics
from src.knowledge.conflict import detect_conflicts
from src.providers.llm_provider import LLMProvider, LLMResponse


class MockConflictLLM(LLMProvider):
    def __init__(self, json_response: str):
        self.json_response = json_response

    def generate(self, prompt: str, system_prompt: str = "", model=None) -> LLMResponse:
        return LLMResponse(content=self.json_response, tokens_used=50, model="mock")

    def generate_structured(self, prompt: str, response_schema, system_prompt="", model=None):
        return None

    def describe_image(self, image_path: str, prompt: str, model=None) -> str:
        return ""


def test_conflict_detection_empty_or_single():
    assert detect_conflicts([]) == []

    rec = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c1",
        document_id="d1",
        content="Ser Vael was a loyal knight.",
        source_category="chronicle",
        source_subtype="chronicle_entry",
        document_title="Annals",
    )
    assert detect_conflicts([rec]) == []


def test_conflict_detection_agreeing_heuristic():
    rec1 = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c1",
        document_id="d1",
        content="Ser Vael founded the Ashen Vanguard in 312 AS.",
        source_category="codex",
        source_subtype="codex_entry",
        document_title="Codex Vaeloria",
    )
    rec2 = EvidenceRecord(
        id="EVIDENCE_002",
        chunk_id="c2",
        document_id="d2",
        content="In 312 AS, Ser Vael established the Ashen Vanguard.",
        source_category="chronicle",
        source_subtype="chronicle_entry",
        document_title="Kindling Years",
    )
    conflicts = detect_conflicts([rec1, rec2])
    assert len(conflicts) == 0


def test_conflict_detection_contradiction_heuristic():
    rec1 = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c1",
        document_id="d1",
        content="The accused, Ser Vael, was charged with betrayal and treason against the Vanguard.",
        source_category="ephemera",
        source_subtype="trial_transcript",
        document_title="Trial Transcript",
        source_characteristics=SourceCharacteristics(
            source_type="ephemera",
            document_subtype="trial_transcript",
            claim_strength="allegation",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    )
    rec2 = EvidenceRecord(
        id="EVIDENCE_002",
        chunk_id="c2",
        document_id="d2",
        content="And Vael the True remained steadfast and loyal, never wavered in his solemn oath.",
        source_category="ephemera",
        source_subtype="ballad",
        document_title="Ballad of Vael",
        source_characteristics=SourceCharacteristics(
            source_type="ephemera",
            document_subtype="ballad",
            claim_strength="rumor",
            narrative_voice="in_character",
            temporal_reliability="mythological",
        ),
    )

    conflicts = detect_conflicts([rec1, rec2])
    assert len(conflicts) >= 1
    assert conflicts[0].conflict_type == "contradiction"


def test_conflict_detection_with_mock_llm():
    rec1 = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c1",
        document_id="d1",
        content="The battle occurred in 312 AS.",
        source_category="chronicle",
        source_subtype="chronicle_entry",
        document_title="Royal Annals",
    )
    rec2 = EvidenceRecord(
        id="EVIDENCE_002",
        chunk_id="c2",
        document_id="d2",
        content="Some regional reports suggest the skirmish began later, around 315 AS.",
        source_category="ephemera",
        source_subtype="field_report",
        document_title="Field Report",
    )

    json_payload = """
    {
      "conflicts": [
        {
          "claim": "The exact year the battle commenced",
          "supporting": ["EVIDENCE_001"],
          "opposing": ["EVIDENCE_002"],
          "type": "qualification"
        }
      ]
    }
    """
    mock_llm = MockConflictLLM(json_payload)
    conflicts = detect_conflicts([rec1, rec2], llm=mock_llm)

    assert len(conflicts) == 1
    assert conflicts[0].claim_summary == "The exact year the battle commenced"
    assert conflicts[0].conflict_type == "qualification"
    assert len(conflicts[0].supporting_evidence) == 1
    assert conflicts[0].supporting_evidence[0].id == "EVIDENCE_001"
    assert len(conflicts[0].opposing_evidence) == 1
    assert conflicts[0].opposing_evidence[0].id == "EVIDENCE_002"
