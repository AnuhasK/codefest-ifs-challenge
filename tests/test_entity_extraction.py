from pathlib import Path
from uuid import uuid4
import spacy
from src.ingestion.entities import (
    build_gazette_from_corpus,
    build_spacy_pipeline,
    extract_entities_from_chunk,
    extract_entities_from_corpus,
)
from src.models.document import Chunk


def test_build_gazette_from_corpus(corpus_root: Path):
    """Test that gazette parses wiki and image files into canonical names and types."""
    gazette = build_gazette_from_corpus(corpus_root)
    assert len(gazette) > 50
    # Check key expected entities from Ashen Era Archive
    assert any("Ederon Fellgard" in k for k in gazette)
    assert any("Ashen Vanguard" in k for k in gazette)
    assert any("Greyfell Citadel" in k for k in gazette)
    assert any("House Morvain" in k for k in gazette)


def test_spacy_entity_ruler_and_extraction(corpus_root: Path):
    """Test entity extraction from a synthetic text chunk using spaCy EntityRuler."""
    gazette = {"Ederon Fellgard": "Person", "Ashen Vanguard": "Faction", "Greyfell Citadel": "Place"}
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

    extracted = extract_entities_from_chunk(chunk, nlp, gazette_keys_lower)
    names = {e.name for e in extracted}
    assert "Ederon Fellgard" in names
    assert "Ashen Vanguard" in names
    assert "Greyfell Citadel" in names

    # Check that gazette-matched entities are correctly labeled with source='gazette'
    gazette_matches = [e for e in extracted if e.name in gazette]
    assert len(gazette_matches) == 3
    for e in gazette_matches:
        assert e.source == "gazette"
        assert e.entity_type in ("Person", "Faction", "Place")


def test_extract_empty_chunk(corpus_root: Path):
    """Test extracting from a chunk with no named entities."""
    gazette = {"Ederon Fellgard": "Person"}
    nlp = build_spacy_pipeline(gazette)
    gazette_keys_lower = {k.lower() for k in gazette.keys()}

    chunk = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        section_id=None,
        representation_id=None,
        content="The wind blew cold across the empty plains.",
        contextualized_content=None,
        page_start=1,
        page_end=1,
        chapter=None,
        section_title=None,
        position=0,
        token_count=9,
    )

    extracted = extract_entities_from_chunk(chunk, nlp, gazette_keys_lower)
    assert len(extracted) == 0
