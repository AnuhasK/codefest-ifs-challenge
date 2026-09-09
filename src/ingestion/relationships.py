import re
import json
import logging
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set

from src.config import BASE_DIR, CORPUS_PATH, GEMINI_REL_BATCH_SIZE
from src.models.document import Chunk
from src.models.entity import (
    RelationshipType,
    ExtractedRelationship,
    RelationshipExtractionResult,
    RelationshipOutput,
)
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

CACHE_FILE = BASE_DIR / "data" / "extracted_relationships.json"

# Mapping from wiki infobox property labels to canonical RelationshipType
INFOBOX_RELATION_MAP = {
    # Membership
    "member": (RelationshipType.MEMBER_OF, True),
    "member of": (RelationshipType.MEMBER_OF, True),
    "membership": (RelationshipType.MEMBER_OF, True),
    "members": (RelationshipType.MEMBER_OF, False),  # reverse: faction -> members

    # Leadership & command
    "command": (RelationshipType.CONTROLS, True),
    "ruled by": (RelationshipType.CONTROLS, False),  # reverse: ruler -> controls place
    "seat": (RelationshipType.LOCATED_IN, True),
    "lair": (RelationshipType.LOCATED_IN, True),

    # Conflict outcomes
    "victor": (RelationshipType.WON, False),  # reverse: victor -> won -> conflict
    "victor of": (RelationshipType.WON, True),
    "belligerent in": (RelationshipType.PARTICIPATED_IN, True),
    "belligerencies": (RelationshipType.PARTICIPATED_IN, True),
    "known participant": (RelationshipType.PARTICIPATED_IN, True),
    "major fighting": (RelationshipType.OCCURRED_AT, True),
    "waged at": (RelationshipType.OCCURRED_AT, True),
    "devastated": (RelationshipType.OCCURRED_AT, True),

    # Items & Artifacts
    "wields": (RelationshipType.HOLDS, True),
    "wielded by": (RelationshipType.HOLDS, False),
    "place of housing": (RelationshipType.LOCATED_IN, True),
    "place of forging": (RelationshipType.CREATED, False),  # place -> created -> artifact

    # Personal ties
    "mentor of": (RelationshipType.ALLIED_WITH, True),
}


def clean_wiki_entity_name(raw: str) -> str:
    """Strip markdown links, bolding, and whitespace from wiki entity references."""
    s = raw.strip()
    s = re.sub(r"\[\[(.*?)\]\]", r"\1", s)
    s = s.replace("**", "").replace("*", "")
    # Remove parenthetical tags like (faction) or (character)
    s = re.sub(r"\s*\((?:faction|character|conflict|location|place)\)", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_entities_from_field(value: str) -> List[str]:
    """Parse one or more entity mentions separated by semicolons, commas, or [[...]]."""
    names: List[str] = []
    # If explicit [[...]] links exist
    wiki_links = re.findall(r"\[\[(.*?)\]\]", value)
    if wiki_links:
        for link in wiki_links:
            clean = clean_wiki_entity_name(link)
            if clean and len(clean) > 1:
                names.append(clean)
        return names

    # Split by semicolons
    parts = re.split(r"[;,]", value)
    for p in parts:
        clean = clean_wiki_entity_name(p)
        if clean and len(clean) > 1 and clean.lower() not in ("none", "unknown", "n/a"):
            names.append(clean)
    return names


def extract_infobox_relationships(corpus_path: Optional[Path] = None) -> List[ExtractedRelationship]:
    """
    Extract high-precision canonical relationships directly from wiki markdown Infobox tables.
    Runs 100% offline with zero API calls.
    """
    corpus_dir = Path(corpus_path) if corpus_path else CORPUS_PATH
    wiki_dir = corpus_dir / "wiki"
    if not wiki_dir.exists():
        logger.warning("Wiki directory not found at %s for infobox extraction.", wiki_dir)
        return []

    relationships: List[ExtractedRelationship] = []
    seen: Set[Tuple[str, str, str]] = set()

    for md_file in wiki_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read %s: %e", md_file, e)
            continue

        # Extract main page title (subject entity)
        title_match = re.search(r"^#\s+(?:\[\[)?(.*?)(?:\]\])?(?:\s*\((?:faction|character|conflict|location|place)\))?$", content, re.MULTILINE)
        if not title_match:
            continue
        page_entity = clean_wiki_entity_name(title_match.group(1))
        if not page_entity:
            continue

        # Parse markdown infobox rows: | Field | Value |
        for row_match in re.finditer(r"^\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$", content, re.MULTILINE):
            field_name = row_match.group(1).strip().lower()
            field_val = row_match.group(2).strip()

            if field_name in ("field", "---", ":---", "value"):
                continue

            rel_info = INFOBOX_RELATION_MAP.get(field_name)
            if not rel_info:
                # Check partial containment
                for k, v in INFOBOX_RELATION_MAP.items():
                    if k in field_name:
                        rel_info = v
                        break

            if not rel_info:
                continue

            rel_type, forward = rel_info
            target_entities = parse_entities_from_field(field_val)

            for target in target_entities:
                if not target or target.lower() == page_entity.lower():
                    continue

                source_name = page_entity if forward else target
                target_name = target if forward else page_entity

                key = (source_name.lower(), target_name.lower(), rel_type.value)
                if key in seen:
                    continue
                seen.add(key)

                relationships.append(
                    ExtractedRelationship(
                        source_entity=source_name,
                        target_entity=target_name,
                        relationship_type=rel_type.value,
                        evidence_text=f"Infobox {field_name}: {field_val}",
                        document_id=md_file.stem,
                        confidence=1.0,
                        source="infobox",
                    )
                )

    logger.info("Extracted %d deterministic relationships from wiki infoboxes.", len(relationships))
    return relationships


def get_entity_pairs_from_chunk(
    chunk: Chunk,
    entity_mentions: Dict[str, List[str]],
) -> List[Tuple[str, str]]:
    """
    Co-occurrence pre-filter: identify entity pairs in the same chunk.
    Only chunks with 2+ entities produce pairs.
    """
    cid_str = str(chunk.id)
    entities = entity_mentions.get(cid_str, [])
    if len(entities) < 2:
        return []

    unique_entities = sorted(list(set(e.strip() for e in entities if e and e.strip())))
    pairs = []
    for i in range(len(unique_entities)):
        for j in range(i + 1, len(unique_entities)):
            pairs.append((unique_entities[i], unique_entities[j]))
    return pairs


def extract_relationships_from_chunk(
    chunk: Chunk,
    entities_in_chunk: List[str],
    llm: Optional[LLMProvider] = None,
) -> List[ExtractedRelationship]:
    """
    Given a chunk and the entities that appear in it, classify relationships
    between those entities using LLM with structured output schema enforcement.
    """
    if len(entities_in_chunk) < 2 or llm is None:
        return []

    entity_list_str = ", ".join(entities_in_chunk)
    prompt = f"""You are given a text passage from the fictional "Ashen Era" universe and a list of identified named entities in it.
Entities: {entity_list_str}

Text:
\"\"\"{chunk.content}\"\"\"

Identify any explicit relationships between the given entities.
Rules:
- Source and target MUST be entities from the provided list.
- Type MUST be one of: MEMBER_OF, LED, PARTICIPATED_IN, OCCURRED_AT, LOCATED_IN, WON, LOST, CONTROLS, HOLDS, ALLIED_WITH, OPPOSED, CREATED, FOUNDED, DESTROYED, RELATED_TO.
- Only extract explicitly stated facts. Do not speculate.
- Provide the exact supporting evidence excerpt."""

    system_prompt = "You are an expert knowledge graph relationship extractor. Respond strictly conforming to the requested schema."

    try:
        result = llm.generate_structured(
            prompt=prompt,
            response_schema=RelationshipExtractionResult,
            system_prompt=system_prompt,
        )
        if not result or not hasattr(result, "relationships"):
            return []

        extracted = []
        for r in result.relationships:
            if r.confidence < 0.5:
                continue
            extracted.append(
                ExtractedRelationship(
                    source_entity=r.source,
                    target_entity=r.target,
                    relationship_type=r.type.value if hasattr(r.type, "value") else str(r.type),
                    evidence_text=r.evidence,
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id) if chunk.document_id else None,
                    confidence=float(r.confidence),
                    source="llm",
                )
            )
        return extracted
    except Exception as e:
        logger.debug("LLM relationship extraction failed for chunk %s: %s", chunk.id, e)
        return []


def save_relationships_cache(
    relationships: List[ExtractedRelationship],
    cache_path: Optional[Path] = None,
) -> None:
    """Save extracted relationships list to a JSON file."""
    path = cache_path or CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = [r.model_dump() for r in relationships]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)
    logger.info("Saved %d relationships to cache at %s", len(relationships), path)


def load_relationships_cache(
    cache_path: Optional[Path] = None,
) -> Optional[List[ExtractedRelationship]]:
    """Load relationships from JSON cache file if it exists."""
    path = cache_path or CACHE_FILE
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        relationships = [ExtractedRelationship.model_validate(item) for item in data]
        logger.info("Loaded %d relationships from cache at %s", len(relationships), path)
        return relationships
    except Exception as e:
        logger.warning("Failed to load relationships cache from %s: %s", path, e)
        return None


BATCH_REL_CACHE_FILE = BASE_DIR / "data" / "relationship_batch_cache.json"


def _load_batch_rel_cache() -> Dict[str, Any]:
    if BATCH_REL_CACHE_FILE.exists():
        try:
            return json.loads(BATCH_REL_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed reading relationship batch cache: %s", e)
    return {}


def _save_batch_rel_cache(cache: Dict[str, Any]) -> None:
    try:
        BATCH_REL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BATCH_REL_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed saving relationship batch cache: %s", e)


def extract_relationships_batch(
    items: List[Tuple[Chunk, List[str]]],
    llm: Optional[LLMProvider] = None,
    batch_size: int = GEMINI_REL_BATCH_SIZE,
) -> List[ExtractedRelationship]:
    """
    Extract relationships from multiple chunks in batches of `batch_size` (default: 10).
    Massively reduces LLM API calls from ~800 to ~80 calls.
    Falls back to single-chunk extraction on JSON parse error.
    """
    if not items or llm is None:
        return []

    cache = _load_batch_rel_cache()
    all_extracted: List[ExtractedRelationship] = []
    uncached_items: List[Tuple[Chunk, List[str], str]] = []

    for chunk, entities in items:
        if len(entities) < 2:
            continue
        sorted_ents = sorted(list(set(entities)))
        cache_key = hashlib.sha256(f"{chunk.content[:1000]}:{sorted_ents}".encode("utf-8")).hexdigest()
        if cache_key in cache:
            cached_rels = cache[cache_key]
            for r in cached_rels:
                all_extracted.append(
                    ExtractedRelationship(
                        source_entity=r["source_entity"],
                        target_entity=r["target_entity"],
                        relationship_type=r["relationship_type"],
                        evidence_text=r["evidence_text"],
                        chunk_id=str(chunk.id),
                        document_id=str(chunk.document_id) if chunk.document_id else None,
                        confidence=float(r.get("confidence", 0.9)),
                        source=r.get("source", "llm"),
                    )
                )
        else:
            uncached_items.append((chunk, sorted_ents, cache_key))

    if not uncached_items:
        return all_extracted

    total_batches = (len(uncached_items) + batch_size - 1) // batch_size
    print(
        f"  [Relationships] Extracting relationships for {len(uncached_items)} chunks in {total_batches} batches ({batch_size} chunks/call)...",
        flush=True,
    )

    allowed_types = {t.value for t in RelationshipType}

    for b_idx, i in enumerate(range(0, len(uncached_items), batch_size), start=1):
        batch_slice = uncached_items[i : i + batch_size]
        chunks_payload = [
            {
                "index": str(idx),
                "entities": ents,
                "text": chunk.content[:1500],
            }
            for idx, (chunk, ents, _) in enumerate(batch_slice)
        ]

        prompt = f"""You are an expert knowledge graph relationship extractor for the fictional "Ashen Era" universe.
Below are several text chunks. For each chunk, a list of confirmed named entities in that chunk is provided.

For each chunk, identify any explicit relationships between the given entities.

Allowed relationship types:
{', '.join(sorted(list(allowed_types)))}

Rules:
1. Source and target MUST be entities from the provided list for that chunk.
2. Only extract explicitly stated facts. Do not speculate or extrapolate.
3. Provide the exact supporting evidence excerpt from the text.
4. Confidence must be between 0.5 and 1.0.

Chunks:
{json.dumps(chunks_payload, indent=2)}

Return JSON in this exact structure where keys are the chunk index strings ("0", "1", ...):
{{
  "0": [
    {{
      "source": "Ser Vael",
      "target": "Ashen Vanguard",
      "type": "MEMBER_OF",
      "evidence": "Ser Vael served with the Ashen Vanguard",
      "confidence": 0.95
    }}
  ],
  "1": []
}}
"""
        batch_success = False
        try:
            response = llm.generate(
                prompt=prompt,
                system_prompt="You are an expert knowledge graph relationship extractor. Respond strictly with valid JSON conforming to the requested schema.",
            )
            raw_text = response.content.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text).strip()

            data = json.loads(raw_text)

            for idx, (chunk, ents, cache_key) in enumerate(batch_slice):
                idx_str = str(idx)
                chunk_rels: List[ExtractedRelationship] = []
                serialized_for_cache = []

                for r in data.get(idx_str, []):
                    s = str(r.get("source", "")).strip()
                    t = str(r.get("target", "")).strip()
                    rtype = str(r.get("type", "")).strip().upper()
                    ev = str(r.get("evidence", "")).strip()
                    conf = float(r.get("confidence", 0.9))

                    if not s or not t or s == t:
                        continue
                    if rtype not in allowed_types:
                        continue
                    if conf < 0.5:
                        continue

                    rel_obj = ExtractedRelationship(
                        source_entity=s,
                        target_entity=t,
                        relationship_type=rtype,
                        evidence_text=ev,
                        chunk_id=str(chunk.id),
                        document_id=str(chunk.document_id) if chunk.document_id else None,
                        confidence=conf,
                        source="llm",
                    )
                    chunk_rels.append(rel_obj)
                    serialized_for_cache.append({
                        "source_entity": s,
                        "target_entity": t,
                        "relationship_type": rtype,
                        "evidence_text": ev,
                        "confidence": conf,
                        "source": "llm",
                    })

                all_extracted.extend(chunk_rels)
                cache[cache_key] = serialized_for_cache

            _save_batch_rel_cache(cache)
            batch_success = True

        except Exception as e:
            logger.warning("Batch relationship extraction failed for batch %d: %s. Falling back to per-chunk extraction.", b_idx, e)

        if not batch_success:
            # Fallback to single chunk extraction
            for chunk, ents, cache_key in batch_slice:
                single_rels = extract_relationships_from_chunk(chunk, ents, llm)
                all_extracted.extend(single_rels)
                cache[cache_key] = [
                    {
                        "source_entity": r.source_entity,
                        "target_entity": r.target_entity,
                        "relationship_type": r.relationship_type,
                        "evidence_text": r.evidence_text,
                        "confidence": r.confidence,
                        "source": r.source,
                    }
                    for r in single_rels
                ]
            _save_batch_rel_cache(cache)

        print(
            f"  [Relationships] Completed batch {b_idx}/{total_batches} ({min(i + batch_size, len(uncached_items))}/{len(uncached_items)} chunks)...",
            flush=True,
        )

    return all_extracted


def extract_all_relationships(
    chunks: List[Chunk],
    chunk_to_entities: Dict[str, Any],
    llm: Optional[LLMProvider] = None,
    corpus_path: Optional[Path] = None,
    batch_size: int = GEMINI_REL_BATCH_SIZE,
) -> List[ExtractedRelationship]:
    """
    Complete relationship extraction pipeline:
    1. Deterministic wiki infobox extraction (zero API calls)
    2. Batched LLM relationship extraction for chunks with 2+ entities
    3. Merges, deduplicates, and caches all relationships to data/extracted_relationships.json
    """
    print("Extracting canonical relationships from wiki infoboxes...", flush=True)
    infobox_rels = extract_infobox_relationships(corpus_path)

    all_relationships: List[ExtractedRelationship] = list(infobox_rels)

    items: List[Tuple[Chunk, List[str]]] = []
    for chunk in chunks:
        cid = str(chunk.id)
        raw_ents = chunk_to_entities.get(cid, [])
        ent_names = [
            e.name if hasattr(e, "name") else str(e)
            for e in raw_ents
            if (hasattr(e, "name") and e.name) or str(e).strip()
        ]
        unique_names = sorted(list(set(ent_names)))
        if len(unique_names) >= 2:
            items.append((chunk, unique_names))

    if items and llm is not None:
        print(f"Found {len(items)} chunks with 2+ entities eligible for relationship extraction.", flush=True)
        llm_rels = extract_relationships_batch(items, llm=llm, batch_size=batch_size)
        all_relationships.extend(llm_rels)

    # Deduplicate relationships by (source.lower(), target.lower(), type)
    seen: Set[Tuple[str, str, str]] = set()
    deduped: List[ExtractedRelationship] = []
    for r in all_relationships:
        key = (r.source_entity.strip().lower(), r.target_entity.strip().lower(), r.relationship_type.strip().upper())
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    save_relationships_cache(deduped)
    return deduped
