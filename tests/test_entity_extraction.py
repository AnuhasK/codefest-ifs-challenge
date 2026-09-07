from pathlib import Path
from uuid import uuid4
import pytest
from src.ingestion.entities import (
    ASHEN_ERA_ONTOLOGY,
    ExtractedEntity,
    build_gazette_from_corpus,
    build_spacy_pipeline,
    extract_capitalized_candidates,
    extract_entities_from_chunk,
    extract_entities_from_corpus,
    needs_llm_entity_pass,
    classify_unknown_entities_with_llm,
    resolve_aliases,
)
from src.models.document import Chunk
from src.providers.llm_provider import LLMProvider, LLMResponse


class MockLLMForNER(LLMProvider):
    def generate(self, prompt: str, system_prompt: str = "", model=None) -> LLMResponse:
        mock_json = """{
            "entities": [
                {
                    "mention": "Lord Drovenath",
                    "canonical_name": "Drovenath",
                    "type": "PERSON",
                    "confidence": 0.95
                }
            ]
        }"""
        return LLMResponse(content=mock_json, tokens_used=50, model="mock-gemini")

    def generate_structured(self, prompt: str, response_schema, system_prompt="", model=None):
        return None

    def describe_image(self, image_path: str, prompt: str, model=None) -> str:
        return ""


def test_build_gazette_from_corpus(corpus_root: Path):
    """Test that gazette parses wiki and image files into canonical names and 15-type ontology."""
    gazette = build_gazette_from_corpus(corpus_root)
    assert len(gazette) >= 70
    
    # Check that all entity types conform to the 15-type ontology
    for entity_name, entity_type in gazette.items():
        assert entity_type in ASHEN_ERA_ONTOLOGY, f"Entity {entity_name} has invalid type {entity_type}"

    # Verify key canonical entities
    assert "Ederon Fellgard" in gazette
    assert gazette["Ederon Fellgard"] == "PERSON"
    assert "Ashen Vanguard" in gazette
    assert gazette["Ashen Vanguard"] == "FACTION"
    assert "Greyfell Citadel" in gazette
    assert gazette["Greyfell Citadel"] == "PLACE"
    assert "House Morvain" in gazette
    assert gazette["House Morvain"] == "FACTION"


def test_spacy_entity_ruler_and_gazette_extraction():
    """Test EntityRuler matches gazette entities with confidence=1.0 and source='gazette'."""
    gazette = {
        "Ederon Fellgard": "PERSON",
        "Ashen Vanguard": "FACTION",
        "Greyfell Citadel": "PLACE",
    }
    nlp = build_spacy_pipeline(gazette)
    gazette_keys_lower = {k.lower() for k in gazette.keys()}

    chunk = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        section_id=None,
        representation_id=None,
        content="In the twilight years, Ederon Fellgard rode with the Ashen Vanguard toward Greyfell Citadel.",
        contextualized_content=None,
        page_start=1,
        page_end=1,
        chapter="Chapter 1",
        section_title="The March",
        position=0,
        token_count=20,
    )

    extracted, candidates = extract_entities_from_chunk(chunk, nlp, gazette, gazette_keys_lower)
    names = {e.name for e in extracted}
    assert "Ederon Fellgard" in names
    assert "Ashen Vanguard" in names
    assert "Greyfell Citadel" in names

    for e in extracted:
        assert e.source == "gazette"
        assert e.confidence == 1.0
        assert e.entity_type in ASHEN_ERA_ONTOLOGY


def test_capitalized_candidate_rule_extraction():
    """Test Pass 2: capitalized phrase rules extract unknown candidates with title prefixes."""
    gazette = {"Ser Vael": "PERSON", "Red Vale": "PLACE"}
    nlp = build_spacy_pipeline(gazette)
    gazette_keys_lower = {k.lower() for k in gazette.keys()}

    text = "Ser Vael rode to Red Vale, seeking Lord Drovenath and the mysterious Pale Archon."
    candidates = extract_capitalized_candidates(text, nlp, gazette_keys_lower)

    # Off-gazette candidate with title prefix "Lord" should be extracted
    assert any("Drovenath" in c for c in candidates)
    # On-gazette entities should NOT be extracted as candidates
    assert not any("Ser Vael" == c for c in candidates)
    assert not any("Red Vale" == c for c in candidates)


def test_needs_llm_entity_pass_triggers():
    """Test Step 1c trigger conditions."""
    chunk_normal = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        content="The sun set over the hills.",
        metadata={"source_category": "chronicles", "source_file": "chronicles.docx"},
    )
    # Empty candidates -> False
    assert needs_llm_entity_pass(chunk_normal, []) is False

    # 4+ UNKNOWN candidates -> True
    assert needs_llm_entity_pass(chunk_normal, ["Cand One", "Cand Two", "Cand Three", "Cand Four"]) is True

    # Ephemera chunk -> True
    chunk_ephemera = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        content="Found on a scrap of parchment.",
        metadata={"source_category": "ephemera", "source_file": "ephemera/letter.txt"},
    )
    assert needs_llm_entity_pass(chunk_ephemera, ["Cand One"]) is True

    # Title prefix candidate not in gazette -> True
    assert needs_llm_entity_pass(chunk_normal, ["Lord Drovenath"]) is True


def test_classify_unknown_entities_with_llm():
    """Test Step 1c targeted Gemini entity classification with mock LLM."""
    chunk = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        content="Seeking Lord Drovenath in the shadows.",
    )
    mock_llm = MockLLMForNER()
    classified = classify_unknown_entities_with_llm(
        chunk=chunk,
        candidates=["Lord Drovenath"],
        llm=mock_llm,
    )

    assert len(classified) == 1
    assert classified[0].name == "Drovenath"
    assert classified[0].entity_type == "PERSON"
    assert classified[0].source == "gemini_ner"
    assert classified[0].confidence == 0.95
    assert "Lord Drovenath" in classified[0].mentions


def test_alias_resolution():
    """Test Step 1d alias resolution collapses variants and preserves mentions."""
    cid = str(uuid4())
    did = str(uuid4())
    entities = [
        ExtractedEntity(
            name="Ser Vael",
            entity_type="PERSON",
            mentions=["Ser Vael"],
            chunk_id=cid,
            document_id=did,
            source="gazette",
            confidence=1.0,
        ),
        ExtractedEntity(
            name="Lord Vael",
            entity_type="PERSON",
            mentions=["Lord Vael"],
            chunk_id=cid,
            document_id=did,
            source="rules",
            confidence=0.5,
        ),
        ExtractedEntity(
            name="Vael",
            entity_type="PERSON",
            mentions=["Vael"],
            chunk_id=cid,
            document_id=did,
            source="rules",
            confidence=0.5,
        ),
    ]

    alias_map = resolve_aliases(entities)
    # All 3 variants should map to the same canonical name
    canon_names = {ent.name for ent in entities}
    assert len(canon_names) == 1

    # Mentions should track surface forms
    for ent in entities:
        assert ent.name == list(canon_names)[0]


def test_strict_no_en_core_web_trf_in_codebase():
    """Architecture requirement: verify en_core_web_trf is NOT imported anywhere in src or tests."""
    root_dir = Path(__file__).resolve().parent.parent
    py_files = [
        f for f in list((root_dir / "src").rglob("*.py")) + list((root_dir / "tests").rglob("*.py"))
        if f.resolve() != Path(__file__).resolve()
    ]

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        assert "en_core_web_trf" not in content, (
            f"Found forbidden reference to 'en_core_web_trf' in {py_file}"
        )


def test_alias_resolution_propagates_to_chunk_to_entities():
    """Verify alias resolution propagates to chunk_to_entities and deduplicates within chunk."""
    cid = str(uuid4())
    did = str(uuid4())
    e1 = ExtractedEntity(
        name="Ser Vael",
        entity_type="PERSON",
        mentions=["Ser Vael"],
        chunk_id=cid,
        document_id=did,
        source="gazette",
        confidence=1.0,
    )
    e2 = ExtractedEntity(
        name="Vael",
        entity_type="UNKNOWN",
        mentions=["Vael"],
        chunk_id=cid,
        document_id=did,
        source="gemini_ner",
        confidence=0.85,
    )
    chunk_to_entities = {cid: [e1, e2]}
    all_entities = [e1, e2]
    gazette = {"Ser Vael": "PERSON"}

    alias_map = resolve_aliases(
        all_entities=all_entities,
        chunk_to_entities=chunk_to_entities,
        gazette=gazette,
    )

    # Alias map resolves "Vael" to "Ser Vael"
    assert alias_map["Vael"] == "Ser Vael"

    # chunk_to_entities must be deduplicated to exactly ONE entity for this chunk
    chunk_ents = chunk_to_entities[cid]
    assert len(chunk_ents) == 1
    assert chunk_ents[0].name == "Ser Vael"
    assert chunk_ents[0].entity_type == "PERSON"
    assert chunk_ents[0].source == "gazette"
    assert "Ser Vael" in chunk_ents[0].mentions
    assert "Vael" in chunk_ents[0].mentions

    # all_entities must also reflect the deduplicated result
    assert len(all_entities) == 1
    assert all_entities[0].name == "Ser Vael"
