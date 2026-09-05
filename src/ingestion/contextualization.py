import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
import spacy

from src.config import BASE_DIR
from src.models.document import Chunk, LogicalDocument
from src.ingestion.entities import ExtractedEntity
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)
PREFIX_CACHE_FILE = BASE_DIR / "data" / "contextual_prefix_cache.json"


def _load_prefix_cache() -> Dict[str, str]:
    """Load persistent contextual prefix cache from disk."""
    if PREFIX_CACHE_FILE.exists():
        try:
            return json.loads(PREFIX_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed reading prefix cache: %s", e)
    return {}


def _save_prefix_cache(cache: Dict[str, str]) -> None:
    """Save persistent contextual prefix cache to disk."""
    try:
        PREFIX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFIX_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed writing prefix cache: %s", e)


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
    entities_in_chunk: Optional[List[ExtractedEntity]] = None,
    nlp: Optional[spacy.Language] = None,
) -> bool:
    """
    Returns True if chunk has 2+ subject pronouns AND <= 1 named entities.
    Uses spaCy POS tagger for pronoun detection (legitimate use, not NER).
    Falls back to regex when nlp is None.
    """
    if not chunk.content or len(chunk.content.strip()) < 30:
        return False

    entity_count = len(entities_in_chunk) if entities_in_chunk else 0
    if entity_count > 1:
        return False

    if nlp is not None:
        try:
            doc = nlp(chunk.content)
            subject_pronouns = [
                t for t in doc
                if t.pos_ == "PRON" and (t.dep_ in ("nsubj", "nsubjpass") or t.lower_ in ("he", "she", "they", "it"))
            ]
            return len(subject_pronouns) >= 2 and entity_count <= 1
        except Exception:
            pass

    # Fast regex fallback
    pronoun_matches = PRONOUN_PATTERN.findall(chunk.content)
    return len(pronoun_matches) >= 2 and entity_count <= 1


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
    Checks persistent disk cache first (data/contextual_prefix_cache.json).
    """
    if llm is None:
        return build_template_prefix(chunk, document_title, chapter, section_title)

    # Check cache first
    cache_key = hashlib.sha256(
        f"{document_title}:{chapter}:{section_title}:{chunk.content[:400]}".encode("utf-8")
    ).hexdigest()
    cache = _load_prefix_cache()
    if cache_key in cache and cache[cache_key].strip():
        return cache[cache_key].strip()

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
            prefix = build_template_prefix(chunk, document_title, chapter, section_title)
        else:
            cache[cache_key] = prefix
            _save_prefix_cache(cache)
        return prefix
    except Exception:
        return build_template_prefix(chunk, document_title, chapter, section_title)


def generate_llm_prefix_batch(
    items: List[Dict[str, Any]],
    llm: LLMProvider,
) -> Dict[str, str]:
    """
    Generate contextual prefixes for a batch of chunks (10 chunks per call).
    Massively reduces API calls from ~150 to ~15, fully utilizing the 250k TPM window.
    Returns: mapping of chunk_id -> prefix string
    """
    if not items or not llm:
        return {}

    cache = _load_prefix_cache()
    results: Dict[str, str] = {}
    batch_payload = []

    for idx, item in enumerate(items):
        batch_payload.append({
            "index": str(idx),
            "document": item["document_title"],
            "chapter": item["chapter"] or "N/A",
            "section": item["section_title"] or "N/A",
            "text": item["chunk"].content[:500],
        })

    prompt = f"""You are a document analysis assistant. Given chunks from fictional documents, generate a brief contextual prefix (1-2 sentences) for each chunk that:
1. Names the source document and section.
2. Identifies the key characters, factions, or places mentioned.
3. Resolves pronouns (e.g. "He" -> character's name) based on context.
4. Does NOT hallucinate details outside the provided information.

Chunks:
{json.dumps(batch_payload, indent=2)}

Return a JSON object where keys are the index strings ("0", "1", ...), mapping to the concise prefix string:
{{
  "0": "In Chapter 3 of Mournthrone, Lord Drovenath discusses...",
  "1": "..."
}}
"""

    try:
        response = llm.generate(prompt=prompt, system_prompt="Generate concise factual contextual prefixes.")
        raw_text = response.content.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        data = json.loads(raw_text)
        for idx, item in enumerate(items):
            cid = str(item["chunk"].id)
            idx_str = str(idx)
            cache_key = item["cache_key"]
            prefix = str(data.get(idx_str, "")).strip()

            if prefix and len(prefix) <= 400:
                cache[cache_key] = prefix
                results[cid] = prefix
            else:
                results[cid] = build_template_prefix(
                    item["chunk"], item["document_title"], item["chapter"], item["section_title"]
                )

        _save_prefix_cache(cache)

    except Exception:
        for item in items:
            cid = str(item["chunk"].id)
            results[cid] = build_template_prefix(
                item["chunk"], item["document_title"], item["chapter"], item["section_title"]
            )

    return results


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
    Orchestrate hybrid contextual prefix generation with multi-chunk batching.
    """
    template_count = 0
    llm_count = 0
    total = len(chunks)
    cache = _load_prefix_cache()

    print(f"  [Contextualization] Starting for {total} chunks (LLM tier enabled: {use_llm_tier and llm is not None})...", flush=True)

    pending_llm_items: List[Dict[str, Any]] = []

    # Pass 1: Resolve cached & template prefixes
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

        is_pronoun_heavy = needs_llm_prefix(chunk, entities, nlp=nlp)
        cache_key = hashlib.sha256(
            f"{doc_title}:{chunk.chapter}:{chunk.section_title}:{chunk.content[:400]}".encode("utf-8")
        ).hexdigest()

        meta = {
            "chunk": chunk,
            "document_title": doc_title,
            "chapter": chunk.chapter,
            "section_title": chunk.section_title,
            "previous_chunk": prev_content,
            "next_chunk": next_content,
            "cache_key": cache_key,
            "entities": entities,
        }

        if cache_key in cache and cache[cache_key].strip():
            prefix = cache[cache_key].strip()
            chunk.contextualized_content = f"{prefix}\n\n{chunk.content}"
            llm_count += 1
        elif use_llm_tier and is_pronoun_heavy and llm is not None and (llm_count + len(pending_llm_items)) < max_llm_calls:
            pending_llm_items.append(meta)
        else:
            prefix = build_template_prefix(chunk, doc_title, chunk.chapter, chunk.section_title, entities)
            chunk.contextualized_content = f"{prefix}\n\n{chunk.content}"
            template_count += 1

    # Pass 2: Batch process any uncached pronoun-heavy chunks via Gemini (10 chunks/call)
    if pending_llm_items and llm is not None:
        batch_size = 10
        total_batches = (len(pending_llm_items) + batch_size - 1) // batch_size
        print(
            f"  [Contextualization LLM] Generating {len(pending_llm_items)} prefixes in {total_batches} batches (10 chunks/call)...",
            flush=True,
        )

        for b_idx, i in enumerate(range(0, len(pending_llm_items), batch_size), start=1):
            batch_slice = pending_llm_items[i : i + batch_size]
            batch_prefixes = generate_llm_prefix_batch(batch_slice, llm)

            for item in batch_slice:
                c = item["chunk"]
                cid = str(c.id)
                prefix = batch_prefixes.get(cid) or build_template_prefix(
                    c, item["document_title"], item["chapter"], item["section_title"], item["entities"]
                )
                c.contextualized_content = f"{prefix}\n\n{c.content}"
                llm_count += 1

            print(
                f"  [Contextualization LLM] Completed batch {b_idx}/{total_batches} "
                f"({min(i + batch_size, len(pending_llm_items))}/{len(pending_llm_items)})...",
                flush=True,
            )

    print(f"  [Contextualization] Done: {template_count} template prefixes, {llm_count} LLM prefixes.", flush=True)

    return {
        "total_chunks": total,
        "template_prefixes": template_count,
        "llm_prefixes": llm_count,
    }
