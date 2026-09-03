import re
import spacy
from typing import Dict, List, Optional, Any
from src.models.document import Chunk, LogicalDocument
from src.ingestion.entities import ExtractedEntity
from src.providers.llm_provider import LLMProvider


PRONOUN_PATTERN = re.compile(r"\b(he|she|they|it|his|her|their|him|them)\b", re.IGNORECASE)


def build_template_prefix(
    chunk: Chunk,
    document_title: str,
    chapter: Optional[str] = None,
    section_title: Optional[str] = None,
    entities_in_chunk: Optional[List[ExtractedEntity]] = None,
) -> str:
    """
    Tier 1 Template Contextual Prefix: Zero API calls. Fast & deterministic.
    Combines document title, structural location, and extracted entities.
    """
    parts = [f"From {document_title}"]

    ch = chapter or chunk.chapter
    sec = section_title or chunk.section_title

    if ch and ch.strip():
        parts.append(f", Chapter: {ch.strip()}")
    if sec and sec.strip():
        parts.append(f", Section: {sec.strip()}")

    parts.append(".")

    if entities_in_chunk:
        unique_names = list(dict.fromkeys([e.name for e in entities_in_chunk if e.name]))
        if unique_names:
            parts.append(f" Mentions: {', '.join(unique_names)}.")

    return "".join(parts)


def needs_llm_prefix(
    chunk: Chunk,
    entities_in_chunk: Optional[List[ExtractedEntity]],
) -> bool:
    """
    Fast regex check for pronoun-heavy chunks without named entities.
    Avoids expensive CPU NLP pipeline in the hot loop.
    """
    if not chunk.content or len(chunk.content.strip()) < 40:
        return False

    entity_count = len(entities_in_chunk) if entities_in_chunk else 0
    if entity_count >= 2:
        return False

    # Count subject/possessive pronouns using fast regex
    pronoun_matches = PRONOUN_PATTERN.findall(chunk.content)
    return len(pronoun_matches) >= 4 and entity_count == 0


def generate_llm_prefix(
    chunk: Chunk,
    document_title: str,
    chapter: Optional[str] = None,
    section_title: Optional[str] = None,
    previous_chunk: str = "",
    next_chunk: str = "",
    llm: Optional[LLMProvider] = None,
) -> str:
    """
    Tier 2 LLM Contextual Prefix: Generates a 1-3 sentence situating summary using Gemini Flash.
    """
    if llm is None:
        return build_template_prefix(chunk, document_title, chapter, section_title)

    prompt = f"""You are a document analysis assistant. Given a chunk of text from a larger document, generate a brief contextual prefix (1-3 sentences) that:
1. Names the source document and section.
2. Identifies the key entities mentioned in the chunk.
3. Resolves pronouns where possible (e.g., "He" -> the character's name).
4. Does NOT add information that isn't present in or directly implied by the document.

Document: {document_title}
Chapter: {chapter or "N/A"}
Section: {section_title or "N/A"}
Previous context: {previous_chunk[:200] if previous_chunk else "None"}
Next context: {next_chunk[:200] if next_chunk else "None"}

Chunk to contextualize:
{chunk.content[:600]}

Contextual prefix (1-3 sentences only, concise and factual):"""

    try:
        response = llm.generate(prompt=prompt, system_prompt="Generate concise factual contextual prefixes.")
        prefix = response.content.strip()
        if not prefix or len(prefix) > 400:
            return build_template_prefix(chunk, document_title, chapter, section_title)
        return prefix
    except Exception:
        return build_template_prefix(chunk, document_title, chapter, section_title)


def contextualize_all_chunks(
    chunks: List[Chunk],
    chunk_entities: Dict[str, List[ExtractedEntity]],
    documents_map: Dict[str, Any],
    nlp: Optional[spacy.Language] = None,
    llm: Optional[LLMProvider] = None,
    use_llm_tier: bool = True,
    max_llm_calls: int = 150,
) -> Dict[str, Any]:
    """
    Orchestrate hybrid contextual prefix generation for all chunks with live progress reporting.
    """
    template_count = 0
    llm_count = 0
    total = len(chunks)

    print(f"  [Contextualization] Starting for {total} chunks (LLM tier enabled: {use_llm_tier and llm is not None})...", flush=True)

    for i, chunk in enumerate(chunks, start=1):
        cid = str(chunk.id)
        did = str(chunk.document_id)
        
        doc_obj = documents_map.get(did)
        if isinstance(doc_obj, str):
            doc_title = doc_obj
        elif hasattr(doc_obj, "title"):
            doc_title = doc_obj.title
        else:
            doc_title = chunk.metadata.get("source_file", "Ashen Era Document")

        entities = chunk_entities.get(cid, [])
        prev_content = chunks[i - 2].content if i > 1 and str(chunks[i - 2].document_id) == did else ""
        next_content = chunks[i].content if i < total and str(chunks[i].document_id) == did else ""

        is_pronoun_heavy = needs_llm_prefix(chunk, entities)

        if use_llm_tier and is_pronoun_heavy and llm is not None and llm_count < max_llm_calls:
            prefix = generate_llm_prefix(
                chunk=chunk,
                document_title=doc_title,
                chapter=chunk.chapter,
                section_title=chunk.section_title,
                previous_chunk=prev_content,
                next_chunk=next_content,
                llm=llm,
            )
            llm_count += 1
        else:
            prefix = build_template_prefix(
                chunk=chunk,
                document_title=doc_title,
                chapter=chunk.chapter,
                section_title=chunk.section_title,
                entities_in_chunk=entities,
            )
            template_count += 1

        chunk.contextualized_content = f"{prefix}\n\n{chunk.content}"

        if i % 250 == 0 or i == total:
            print(f"  [Contextualization] {i}/{total} chunks processed ({template_count} template, {llm_count} LLM)...", flush=True)

    return {
        "total_chunks": total,
        "template_prefixes": template_count,
        "llm_prefixes": llm_count,
    }
