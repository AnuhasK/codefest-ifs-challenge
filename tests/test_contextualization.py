from uuid import uuid4
import spacy
from src.models.document import Chunk
from src.ingestion.entities import ExtractedEntity
from src.ingestion.contextualization import (
    build_template_prefix,
    needs_llm_prefix,
    contextualize_all_chunks,
)


def test_build_template_prefix():
    chunk = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        section_id=None,
        representation_id=None,
        content="He gathered his forces and made camp.",
        contextualized_content=None,
        page_start=12,
        page_end=12,
        chapter="Chapter 4: The Outskirts",
        section_title="Muster",
        position=1,
        token_count=10,
    )

    entities = [
        ExtractedEntity(
            name="Ser Vael",
            entity_type="PERSON",
            mentions=["Ser Vael"],
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            source="gazette",
        ),
        ExtractedEntity(
            name="Ashen Vanguard",
            entity_type="FACTION",
            mentions=["Ashen Vanguard"],
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            source="gazette",
        ),
    ]

    prefix = build_template_prefix(
        chunk=chunk,
        document_title="The Kindling Years",
        chapter=chunk.chapter,
        section_title=chunk.section_title,
        entities_in_chunk=entities,
    )

    assert "The Kindling Years" in prefix
    assert "Chapter 4: The Outskirts" in prefix
    assert "Muster" in prefix
    assert "Ser Vael" in prefix
    assert "Ashen Vanguard" in prefix
    assert "Entities: Ser Vael [Person], Ashen Vanguard [Faction]." in prefix


def test_needs_llm_prefix_detection_with_spacy():
    """Verify pronoun detection using spaCy POS tagger and regex fallback."""
    nlp = spacy.load("en_core_web_sm")

    # Pronoun-heavy chunk with 0 named entities
    chunk_pronoun = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        section_id=None,
        representation_id=None,
        content="He then ordered them to hold the pass. He told her that they must not surrender.",
        contextualized_content=None,
        page_start=1,
        page_end=1,
        chapter=None,
        section_title=None,
        position=0,
        token_count=18,
    )

    # With spaCy NLP
    assert needs_llm_prefix(chunk_pronoun, [], nlp=nlp) is True
    # Without spaCy NLP (regex fallback)
    assert needs_llm_prefix(chunk_pronoun, [], nlp=None) is True

    # Entity-rich chunk (2+ entities -> False)
    entities = [
        ExtractedEntity(
            name="Ederon Fellgard",
            entity_type="PERSON",
            chunk_id=str(chunk_pronoun.id),
            document_id=str(chunk_pronoun.document_id),
            source="gazette",
        ),
        ExtractedEntity(
            name="Iron Ring",
            entity_type="FACTION",
            chunk_id=str(chunk_pronoun.id),
            document_id=str(chunk_pronoun.document_id),
            source="gazette",
        ),
    ]
    assert needs_llm_prefix(chunk_pronoun, entities, nlp=nlp) is False
    assert needs_llm_prefix(chunk_pronoun, entities, nlp=None) is False


def test_contextualize_all_chunks():
    cid = uuid4()
    did = uuid4()
    chunk = Chunk(
        id=cid,
        document_id=did,
        section_id=None,
        representation_id=None,
        content="The battle raged for three days at Red Vale.",
        contextualized_content=None,
        page_start=5,
        page_end=5,
        chapter="Chapter 2",
        section_title="The Clash",
        position=0,
        token_count=10,
    )

    stats = contextualize_all_chunks(
        chunks=[chunk],
        chunk_entities={
            str(cid): [
                ExtractedEntity(
                    name="Red Vale",
                    entity_type="PLACE",
                    chunk_id=str(cid),
                    document_id=str(did),
                    source="gazette",
                )
            ]
        },
        documents_map={str(did): "Chronicle of Drowned Light"},
        llm=None,
        use_llm_tier=False,
    )

    assert stats["total_chunks"] == 1
    assert stats["template_prefixes"] == 1
    assert stats["llm_prefixes"] == 0
    assert chunk.contextualized_content is not None
    assert "From Chronicle of Drowned Light" in chunk.contextualized_content
    assert "The battle raged for three days at Red Vale." in chunk.contextualized_content
