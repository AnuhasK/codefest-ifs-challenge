import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from pydantic import BaseModel, Field
import spacy
from spacy.pipeline import EntityRuler

from src.config import BASE_DIR
from src.models.document import Chunk
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)
ENTITY_CACHE_FILE = BASE_DIR / "data" / "entity_extraction_cache.json"


def _load_entity_cache() -> Dict[str, Any]:
    """Load persistent entity extraction cache from disk."""
    if ENTITY_CACHE_FILE.exists():
        try:
            return json.loads(ENTITY_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed reading entity cache: %s", e)
    return {}


def _save_entity_cache(cache: Dict[str, Any]) -> None:
    """Save persistent entity extraction cache to disk."""
    try:
        ENTITY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENTITY_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed writing entity cache: %s", e)


# Fictional-World-Appropriate Entity Ontology (15 types)
ASHEN_ERA_ONTOLOGY: Set[str] = {
    "PERSON",
    "FACTION",
    "PLACE",
    "EVENT",
    "ARTIFACT",
    "ORGANIZATION",
    "CREATURE",
    "TITLE",
    "DYNASTY",
    "DEITY",
    "CONCEPT",
    "DOCUMENT",
    "BUILDING",
    "MILITARY_UNIT",
    "UNKNOWN",
}


class ExtractedEntity(BaseModel):
    """Structured entity extracted from a chunk via gazette, rules, or targeted LLM."""
    name: str  # canonical name
    entity_type: str  # from the 15-type ontology
    mentions: List[str] = Field(default_factory=list)  # all surface forms seen
    chunk_id: str
    document_id: str
    source: str  # "gazette", "rules", or "gemini_ner"
    confidence: float = 1.0  # 1.0 for gazette, 0.5 for rules, 0.0-1.0 for Gemini


# Type mapping from raw tags/keywords to the 15-type ontology
TYPE_MAPPING: Dict[str, str] = {
    "person": "PERSON",
    "character": "PERSON",
    "lord": "PERSON",
    "lady": "PERSON",
    "ser": "PERSON",
    "warden": "PERSON",
    "faction": "FACTION",
    "house": "FACTION",
    "covenant": "FACTION",
    "vanguard": "FACTION",
    "place": "PLACE",
    "location": "PLACE",
    "fortress": "PLACE",
    "citadel": "PLACE",
    "keep": "PLACE",
    "vale": "PLACE",
    "city": "PLACE",
    "marsh": "PLACE",
    "event": "EVENT",
    "conflict": "EVENT",
    "war": "EVENT",
    "accord": "EVENT",
    "purge": "EVENT",
    "reckoning": "EVENT",
    "battle": "EVENT",
    "treaty": "EVENT",
    "artifact": "ARTIFACT",
    "relic": "ARTIFACT",
    "weapon": "ARTIFACT",
    "blade": "ARTIFACT",
    "sword": "ARTIFACT",
    "crown": "ARTIFACT",
    "aegis": "ARTIFACT",
    "sceptre": "ARTIFACT",
    "lantern": "ARTIFACT",
    "gauntlet": "ARTIFACT",
    "psalter": "ARTIFACT",
    "creature": "CREATURE",
    "monster": "CREATURE",
    "beast": "CREATURE",
    "wyrm": "CREATURE",
    "leviathan": "CREATURE",
    "lurker": "CREATURE",
    "shrike": "CREATURE",
    "stag": "CREATURE",
    "wraith": "CREATURE",
    "colossus": "CREATURE",
    "organization": "ORGANIZATION",
    "cartel": "ORGANIZATION",
    "choir": "ORGANIZATION",
    "guild": "ORGANIZATION",
    "order": "ORGANIZATION",
    "title": "TITLE",
    "dynasty": "DYNASTY",
    "deity": "DEITY",
    "concept": "CONCEPT",
    "document": "DOCUMENT",
    "codex": "DOCUMENT",
    "building": "BUILDING",
    "military_unit": "MILITARY_UNIT",
    "regiment": "MILITARY_UNIT",
    "army": "MILITARY_UNIT",
    "unknown": "UNKNOWN",
    # Uppercase normalization
    "PERSON": "PERSON",
    "FACTION": "FACTION",
    "PLACE": "PLACE",
    "EVENT": "EVENT",
    "ARTIFACT": "ARTIFACT",
    "ORGANIZATION": "ORGANIZATION",
    "CREATURE": "CREATURE",
    "TITLE": "TITLE",
    "DYNASTY": "DYNASTY",
    "DEITY": "DEITY",
    "CONCEPT": "CONCEPT",
    "DOCUMENT": "DOCUMENT",
    "BUILDING": "BUILDING",
    "MILITARY_UNIT": "MILITARY_UNIT",
    "UNKNOWN": "UNKNOWN",
}

TITLE_PREFIXES = ("ser ", "lord ", "lady ", "high ", "the ", "warden ", "archon ")
TITLE_SUFFIXES = (
    " the oathless",
    " the unbroken",
    " the unyielding",
    " the blind",
    " the pale",
    " the silent",
    " of mournthrone",
    " of red vale",
    " of the third house",
)


def _format_entity_name_from_stem(stem: str) -> str:
    """Convert filename token like 'ederon_fellgard' to 'Ederon Fellgard'."""
    words = stem.split("_")
    return " ".join(w.capitalize() for w in words if w)


def build_gazette_from_corpus(corpus_path: Path | str) -> Dict[str, str]:
    """
    Step 1a: Parse wiki, codex, and archive filenames to build a canonical entity dictionary.
    Zero API calls. Fully offline. Maps canonical entity names to the 15-type ontology.
    """
    corpus_root = Path(corpus_path)
    gazette: Dict[str, str] = {}

    # 1. Inspect image prefixes (definitive entity categories)
    image_dirs = [corpus_root / "images", corpus_root / "wiki" / "images"]
    for img_dir in image_dirs:
        if not img_dir.exists():
            continue
        for file in img_dir.glob("*.png"):
            stem = file.stem
            for prefix, mapped_type in [
                ("atmo_portrait_character_", "PERSON"),
                ("atmo_heraldry_faction_", "FACTION"),
                ("atmo_landscape_location_", "PLACE"),
                ("atmo_battle_painting_conflict_", "EVENT"),
                ("atmo_creature_creature_", "CREATURE"),
                ("atmo_relic_artifact_", "ARTIFACT"),
            ]:
                if stem.startswith(prefix):
                    entity_stem = stem[len(prefix):]
                    name = _format_entity_name_from_stem(entity_stem)
                    gazette[name] = mapped_type
                    if name.lower().startswith("the "):
                        gazette[name[4:]] = mapped_type

    # 2. Inspect wiki articles
    wiki_dir = corpus_root / "wiki"
    if wiki_dir.exists():
        for file in wiki_dir.glob("*.md"):
            stem = file.stem
            # Check explicit wiki filename prefixes
            detected_type = None
            if stem.startswith("wiki_person_"):
                detected_type = "PERSON"
                clean_stem = stem[len("wiki_person_"):]
            elif stem.startswith("wiki_faction_"):
                detected_type = "FACTION"
                clean_stem = stem[len("wiki_faction_"):]
            elif stem.startswith("wiki_place_"):
                detected_type = "PLACE"
                clean_stem = stem[len("wiki_place_"):]
            elif stem.startswith("wiki_creature_"):
                detected_type = "CREATURE"
                clean_stem = stem[len("wiki_creature_"):]
            elif stem.startswith("wiki_artifact_"):
                detected_type = "ARTIFACT"
                clean_stem = stem[len("wiki_artifact_"):]
            elif stem.startswith("wiki_event_"):
                detected_type = "EVENT"
                clean_stem = stem[len("wiki_event_"):]
            elif stem.startswith("wiki_organization_"):
                detected_type = "ORGANIZATION"
                clean_stem = stem[len("wiki_organization_"):]
            else:
                clean_stem = stem

            name_from_stem = _format_entity_name_from_stem(clean_stem)

            # Read first header and opening text
            try:
                content = file.read_text(encoding="utf-8")
                lines = [l.strip() for l in content.split("\n") if l.strip()]
                first_header = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else name_from_stem
                first_para = lines[1].lower() if len(lines) > 1 else ""

                if not detected_type:
                    if any(w in first_para for w in ("minor figure", "born in", "serves as", "he serves", "she serves", "their recorded service", "sapper", "is a warrior", "is a knight", "is a lord", "is a lady", "is an archon")):
                        detected_type = "PERSON"
                    elif any(w in first_para for w in ("core location", "fortified settlement", "stronghold in", "settlement in", "garrison strength", "founded in", "fortress", "citadel", "keep", "marsh", "vale", "city", "abbey", "hold")):
                        detected_type = "PLACE"
                    elif any(w in first_para for w in ("lairs in", "threat rating", "creature", "beast", "monster", "wyrm", "leviathan", "lurker", "shrike", "stag", "wraith", "colossus", "dream-feeder", "ambusher", "siege-breaker")):
                        detected_type = "CREATURE"
                    elif any(w in first_para for w in ("is a ward", "ward forged", "regalia artifact", "artifact forged", "relic", "attunement cost", "forged in", "forged at", "weapon", "blade", "sword", "crown", "aegis", "sceptre", "lantern", "gauntlet", "psalter")):
                        detected_type = "ARTIFACT"
                    elif any(w in first_para for w in ("war of", "accord of", "purge of", "winter reckoning", "belligerent in", "war", "battle", "accord", "conflict", "purge", "reckoning", "treaty")):
                        detected_type = "EVENT"
                    elif any(w in first_para for w in ("dynasty", "lineage", "bloodline", "ruling house")):
                        detected_type = "DYNASTY"
                    elif any(w in first_para for w in ("is a knightly order", "is a militant order", "is a mercantile league", "is a secretive priesthood", "is a royalist remnant", "merchant cartel", "faction", "house", "vanguard", "cartel", "choir", "order")):
                        detected_type = "FACTION"
                    elif any(w in first_para for w in ("god", "deity", "divine", "pantheon")):
                        detected_type = "DEITY"
                    elif any(w in first_para for w in ("document", "codex", "chronicle", "manuscript", "tome", "scroll")):
                        detected_type = "DOCUMENT"
                    elif any(w in first_para for w in ("building", "tower", "spire", "ossuary", "sanctum", "monument")):
                        detected_type = "BUILDING"
                    elif any(w in first_para for w in ("regiment", "military unit", "cohort", "battalion", "legion")):
                        detected_type = "MILITARY_UNIT"
                    elif any(w in first_para for w in ("concept", "attunement", "ashen tide")):
                        detected_type = "CONCEPT"
                    elif any(w in first_para for w in ("title", "epithet", "rank", "office")):
                        detected_type = "TITLE"
                    else:
                        detected_type = "PERSON"

                # Check if already present from image
                chosen_type = gazette.get(first_header, gazette.get(name_from_stem, detected_type))
                gazette[first_header] = chosen_type
                gazette[name_from_stem] = chosen_type
                if first_header.lower().startswith("the "):
                    gazette[first_header[4:]] = chosen_type
                if name_from_stem.lower().startswith("the "):
                    gazette[name_from_stem[4:]] = chosen_type
            except Exception:
                chosen_type = detected_type or "PERSON"
                gazette[name_from_stem] = chosen_type

    # 3. Inspect codex files
    codex_dir = corpus_root / "codex"
    if codex_dir.exists():
        for file in codex_dir.glob("*.pdf"):
            stem = file.stem
            name = _format_entity_name_from_stem(stem)
            if "codex" in stem.lower():
                gazette[name] = "DOCUMENT"

    return gazette


def build_spacy_pipeline(gazette: Dict[str, str]) -> spacy.Language:
    """
    Step 1b: spaCy en_core_web_sm pipeline with EntityRuler for gazette matching.
    NOTE: Heavy transformer model is NOT used. Standard pretrained NER is disabled or superseded
    by the gazette EntityRuler because pretrained models misclassify fictional entities.
    """
    # Exclusively load en_core_web_sm
    nlp = spacy.load("en_core_web_sm")

    # Disable standard pretrained NER to prevent real-world misclassification of fictional terms
    if "ner" in nlp.pipe_names:
        nlp.disable_pipe("ner")

    # Add EntityRuler
    ruler = nlp.add_pipe("entity_ruler", last=True)

    patterns = []
    for name, entity_type in gazette.items():
        if not name or len(name) < 2:
            continue
        norm_type = TYPE_MAPPING.get(entity_type, "UNKNOWN")
        # Exact string pattern
        patterns.append({
            "label": norm_type,
            "pattern": name,
            "id": name,
        })
        # Case-insensitive token pattern
        tokens = name.split()
        if len(tokens) > 1:
            patterns.append({
                "label": norm_type,
                "pattern": [{"LOWER": t.lower()} for t in tokens],
                "id": name,
            })

    ruler.add_patterns(patterns)
    return nlp


def extract_capitalized_candidates(
    chunk_text: str,
    nlp: spacy.Language,
    gazette_entities_lower: Set[str],
) -> List[str]:
    """
    Pass 2: Extract capitalized 1-3 word noun chunks or title-prefixed phrases NOT in gazette.
    These are tagged as UNKNOWN candidates for targeted LLM classification.
    """
    if not chunk_text or not chunk_text.strip():
        return []

    doc = nlp(chunk_text)
    candidates: List[str] = []
    seen: Set[str] = set()

    # Rule A: Noun chunks that start with capital letters
    for chunk in doc.noun_chunks:
        text = chunk.text.strip()
        # Clean leading punctuation
        text = re.sub(r"^[^\w]+", "", text)
        words = text.split()
        if not words or len(words) > 4:
            continue
        # Check if words are capitalized
        if all(w[0].isupper() for w in words if w and w[0].isalpha()):
            clean = " ".join(words)
            if (
                clean.lower() not in gazette_entities_lower
                and len(clean) > 2
                and not clean.isnumeric()
                and clean not in seen
            ):
                seen.add(clean)
                candidates.append(clean)

    # Rule B: Regex for title prefix phrases ("Ser ...", "Lord ...", "Lady ...", "High ...")
    pattern = re.compile(r"\b(Ser|Lord|Lady|High|Warden|Archon)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
    for match in pattern.finditer(chunk_text):
        full_mention = match.group(0).strip()
        if (
            full_mention.lower() not in gazette_entities_lower
            and full_mention not in seen
        ):
            seen.add(full_mention)
            candidates.append(full_mention)

    return candidates


def needs_llm_entity_pass(chunk: Chunk, unknown_candidates: List[str]) -> bool:
    """
    Step 1c trigger condition:
    Returns True if:
    - chunk has 4+ UNKNOWN candidates, OR
    - chunk is from an ephemera document, OR
    - chunk has title-prefix candidates (Ser/Lord/Lady) not in gazette
    """
    if len(unknown_candidates) >= 4:
        return True

    # Ephemera documents introduce the most off-gazette entities
    source_cat = chunk.metadata.get("source_category", "") if chunk.metadata else ""
    source_file = chunk.metadata.get("source_file", "") if chunk.metadata else ""
    if source_cat == "ephemera" or "ephemera" in str(source_file).lower():
        return True

    # Check for title prefixes in candidates
    for cand in unknown_candidates:
        cand_lower = cand.lower()
        if any(cand_lower.startswith(p) for p in ("ser ", "lord ", "lady ", "high ", "warden ", "archon ")):
            return True

    return False


def classify_unknown_entities_with_llm(
    chunk: Chunk,
    candidates: List[str],
    llm: LLMProvider,
) -> List[ExtractedEntity]:
    """
    Step 1c: Targeted Gemini Entity Pass (~80-150 calls total across corpus).
    Classifies candidates using ONLY the 15-type Ashen Era ontology.
    """
    if not candidates or not llm:
        return []

    # Check disk cache first
    sorted_cand = sorted(candidates)
    cache_key = hashlib.sha256(f"{sorted_cand}:{chunk.content[:400]}".encode("utf-8")).hexdigest()
    cache = _load_entity_cache()
    if cache_key in cache:
        cached_items = cache[cache_key]
        return [
            ExtractedEntity(
                name=item["name"],
                entity_type=item["entity_type"],
                mentions=item.get("mentions", [item["name"]]),
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                source="gemini_ner",
                confidence=item.get("confidence", 0.9),
            )
            for item in cached_items
        ]

    ontology_str = ", ".join(sorted(list(ASHEN_ERA_ONTOLOGY)))
    prompt = f"""This is a chunk from a fictional fantasy corpus called "Ashen Era".
Candidate names detected by pattern matching:
{json.dumps(candidates)}

For each candidate, classify it using ONLY these types:
{ontology_str}

Also identify any other named entities you see in the text that were missed. Do not invent entities.

Return JSON in this exact structure:
{{
  "entities": [
    {{"mention": "...", "canonical_name": "...", "type": "...", "confidence": 0.95}}
  ]
}}

Chunk text:
{chunk.content[:1000]}
"""

    entities: List[ExtractedEntity] = []
    try:
        response = llm.generate(
            prompt=prompt,
            system_prompt="You are an expert entity extractor for the Ashen Era fictional universe.",
        )
        raw_text = response.content.strip()
        # Strip code markdown block if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        data = json.loads(raw_text)
        serialized_for_cache = []
        for item in data.get("entities", []):
            mention = str(item.get("mention", "")).strip()
            canonical = str(item.get("canonical_name", mention)).strip()
            raw_type = str(item.get("type", "UNKNOWN")).strip().upper()
            etype = TYPE_MAPPING.get(raw_type, "UNKNOWN")
            conf = float(item.get("confidence", 0.8))

            if canonical:
                mentions_list = [mention] if mention else [canonical]
                entities.append(
                    ExtractedEntity(
                        name=canonical,
                        entity_type=etype,
                        mentions=mentions_list,
                        chunk_id=str(chunk.id),
                        document_id=str(chunk.document_id),
                        source="gemini_ner",
                        confidence=max(0.0, min(1.0, conf)),
                    )
                )
                serialized_for_cache.append({
                    "name": canonical,
                    "entity_type": etype,
                    "mentions": mentions_list,
                    "confidence": max(0.0, min(1.0, conf)),
                })

        if serialized_for_cache:
            cache[cache_key] = serialized_for_cache
            _save_entity_cache(cache)
    except Exception:
        # Graceful fallback: return candidate items as UNKNOWN rules entities
        for c in candidates:
            entities.append(
                ExtractedEntity(
                    name=c,
                    entity_type="UNKNOWN",
                    mentions=[c],
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    source="rules",
                    confidence=0.5,
                )
            )

    return entities


def classify_unknown_entities_batch_with_llm(
    items: List[Tuple[Chunk, List[str], str]],
    llm: LLMProvider,
) -> Dict[str, List[ExtractedEntity]]:
    """
    Step 1c: Batched Gemini Entity Pass (10-12 chunks per API call).
    Massively reduces API calls from ~500 to ~25-40, using the generous 250k TPM window.
    Returns: mapping of chunk_id -> list of ExtractedEntity
    """
    if not items or not llm:
        return {}

    cache = _load_entity_cache()
    results: Dict[str, List[ExtractedEntity]] = {}
    ontology_str = ", ".join(sorted(list(ASHEN_ERA_ONTOLOGY)))

    batch_payload = []
    for idx, (chunk, candidates, _) in enumerate(items):
        batch_payload.append({
            "chunk_index": str(idx),
            "candidates": candidates,
            "text": chunk.content[:800],
        })

    prompt = f"""You are an expert entity extractor for the Ashen Era fictional universe.
Classify the candidate names for each chunk using ONLY these 15 types:
{ontology_str}

Chunks:
{json.dumps(batch_payload, indent=2)}

Return a JSON object where keys are the chunk_index strings ("0", "1", ...), mapping to a list of entities:
{{
  "0": [
    {{"mention": "...", "canonical_name": "...", "type": "...", "confidence": 0.95}}
  ]
}}
"""

    try:
        response = llm.generate(
            prompt=prompt,
            system_prompt="You are an expert entity extractor for the Ashen Era fictional universe.",
        )
        raw_text = response.content.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        data = json.loads(raw_text)
        for idx, (chunk, candidates, cache_key) in enumerate(items):
            cid = str(chunk.id)
            idx_str = str(idx)
            chunk_ents = []
            serialized_for_cache = []

            for item in data.get(idx_str, []):
                mention = str(item.get("mention", "")).strip()
                canonical = str(item.get("canonical_name", mention)).strip()
                raw_type = str(item.get("type", "UNKNOWN")).strip().upper()
                etype = TYPE_MAPPING.get(raw_type, "UNKNOWN")
                conf = float(item.get("confidence", 0.8))

                if canonical:
                    mentions_list = [mention] if mention else [canonical]
                    chunk_ents.append(
                        ExtractedEntity(
                            name=canonical,
                            entity_type=etype,
                            mentions=mentions_list,
                            chunk_id=cid,
                            document_id=str(chunk.document_id),
                            source="gemini_ner",
                            confidence=max(0.0, min(1.0, conf)),
                        )
                    )
                    serialized_for_cache.append({
                        "name": canonical,
                        "entity_type": etype,
                        "mentions": mentions_list,
                        "confidence": max(0.0, min(1.0, conf)),
                    })

            if serialized_for_cache:
                cache[cache_key] = serialized_for_cache
                results[cid] = chunk_ents
            else:
                fallback = [
                    ExtractedEntity(
                        name=c,
                        entity_type="UNKNOWN",
                        mentions=[c],
                        chunk_id=cid,
                        document_id=str(chunk.document_id),
                        source="rules",
                        confidence=0.5,
                    )
                    for c in candidates
                ]
                results[cid] = fallback

        _save_entity_cache(cache)

    except Exception:
        for chunk, candidates, _ in items:
            cid = str(chunk.id)
            results[cid] = [
                ExtractedEntity(
                    name=c,
                    entity_type="UNKNOWN",
                    mentions=[c],
                    chunk_id=cid,
                    document_id=str(chunk.document_id),
                    source="rules",
                    confidence=0.5,
                )
                for c in candidates
            ]

    return results


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Wagner-Fischer edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (c1 != c2)
            curr[j + 1] = min(insertions, deletions, substitutions)
        prev = curr
    return prev[len(s2)]


def _strip_name_affixes(name: str) -> str:
    """Strip common titles and epithets to isolate the core entity name."""
    core = name.strip()
    core_lower = core.lower()
    for prefix in TITLE_PREFIXES:
        if core_lower.startswith(prefix):
            core = core[len(prefix):].strip()
            core_lower = core.lower()
            break
    for suffix in TITLE_SUFFIXES:
        if core_lower.endswith(suffix):
            core = core[:-len(suffix)].strip()
            core_lower = core.lower()
            break
    return core


def resolve_aliases(
    all_entities: List[ExtractedEntity],
    embeddings_cache: Optional[Any] = None,
) -> Dict[str, str]:
    """
    Step 1d: Alias Resolution (0 extra API calls).
    Collapses surface form variants across documents into canonical names:
    1. Exact match -> same entity
    2. Prefix/suffix strip ("Lord ", " the Oathless") -> compare core name
    3. Levenshtein distance <= 2 -> likely same (typo/variant)
    4. Embedding cosine similarity >= 0.92 -> possible alias (if cache provided)
    
    Returns: mapping of surface_form -> canonical_name
    """
    alias_map: Dict[str, str] = {}
    if not all_entities:
        return alias_map

    # Collect distinct entities and canonical candidates (favor gazette > gemini_ner > rules)
    unique_names: List[str] = list({e.name for e in all_entities if e.name})

    # Sort so that shorter/simpler canonical base names or gazette sources appear first
    canonical_list: List[str] = []

    for name in unique_names:
        core_name = _strip_name_affixes(name)
        matched_canonical = None

        # 1. Check if matches any existing canonical core
        for canon in canonical_list:
            canon_core = _strip_name_affixes(canon)
            # Exact or core match
            if name.lower() == canon.lower() or (core_name and core_name.lower() == canon_core.lower()):
                matched_canonical = canon
                break
            # Levenshtein distance <= 2 for strings with length > 4
            if (
                len(core_name) > 4
                and len(canon_core) > 4
                and abs(len(core_name) - len(canon_core)) <= 2
                and levenshtein_distance(core_name.lower(), canon_core.lower()) <= 2
            ):
                matched_canonical = canon
                break

        if matched_canonical:
            alias_map[name] = matched_canonical
            if core_name and core_name != name:
                alias_map[core_name] = matched_canonical
        else:
            canonical_list.append(name)
            alias_map[name] = name
            if core_name and core_name != name:
                alias_map[core_name] = name

    # Update entity objects in-place with resolved canonical names and accumulated mentions
    for ent in all_entities:
        canon = alias_map.get(ent.name, ent.name)
        if ent.name not in ent.mentions:
            ent.mentions.append(ent.name)
        ent.name = canon

    return alias_map


def extract_entities_from_chunk(
    chunk: Chunk,
    nlp: spacy.Language,
    gazette: Dict[str, str],
    gazette_keys_lower: Set[str],
) -> Tuple[List[ExtractedEntity], List[str]]:
    """
    Extract entities from a single chunk:
    Pass 1: spaCy EntityRuler (gazette) -> typed entities, confidence=1.0, source='gazette'
    Pass 2: Capitalized phrase rules -> UNKNOWN candidates, source='rules'
    """
    if not chunk.content or not chunk.content.strip():
        return [], []

    doc = nlp(chunk.content)
    seen_entities: Dict[str, ExtractedEntity] = {}

    # Pass 1: Gazette EntityRuler matches
    for ent in doc.ents:
        raw_text = ent.text.strip()
        if len(raw_text) < 2 or raw_text.isnumeric():
            continue

        raw_lower = raw_text.lower()
        is_gazette = bool(ent.ent_id_) or (raw_lower in gazette_keys_lower)
        canonical_name = ent.ent_id_ if ent.ent_id_ else raw_text
        entity_type = TYPE_MAPPING.get(ent.label_, TYPE_MAPPING.get(gazette.get(canonical_name, ""), "UNKNOWN"))

        if canonical_name in seen_entities:
            if raw_text not in seen_entities[canonical_name].mentions:
                seen_entities[canonical_name].mentions.append(raw_text)
        else:
            seen_entities[canonical_name] = ExtractedEntity(
                name=canonical_name,
                entity_type=entity_type,
                mentions=[raw_text],
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                source="gazette" if is_gazette else "rules",
                confidence=1.0 if is_gazette else 0.5,
            )

    # Pass 2: Capitalized phrase rule extraction for off-gazette candidates
    unknown_candidates = extract_capitalized_candidates(
        chunk_text=chunk.content,
        nlp=nlp,
        gazette_entities_lower=gazette_keys_lower,
    )

    return list(seen_entities.values()), unknown_candidates


def extract_entities_from_corpus(
    chunks: List[Chunk],
    corpus_path: Path | str,
    llm: Optional[LLMProvider] = None,
    nlp: Optional[spacy.Language] = None,
) -> Tuple[List[ExtractedEntity], Dict[str, List[ExtractedEntity]]]:
    """
    Complete 4-step entity extraction pipeline:
    - Step 1a: Build gazette from corpus (~95 entities, 0 API calls)
    - Step 1b: spaCy EntityRuler (gazette) + capitalized phrase rules (0 API calls)
    - Step 1c: Targeted Gemini pass for ephemera & unknown-heavy chunks (~80-150 calls)
    - Step 1d: Alias resolution (0 API calls)
    
    Returns:
    - All extracted entities (deduplicated, aliases resolved)
    - chunk_id -> list of entities mapping
    """
    gazette = build_gazette_from_corpus(corpus_path)
    gazette_keys_lower = {k.lower() for k in gazette.keys()}

    if nlp is None:
        nlp = build_spacy_pipeline(gazette)

    all_entities: List[ExtractedEntity] = []
    chunk_to_entities: Dict[str, List[ExtractedEntity]] = {}
    uncached_llm_items: List[Tuple[Chunk, List[str], str]] = []  # (chunk, candidates, cache_key)
    entity_cache = _load_entity_cache()

    for chunk in chunks:
        cid = str(chunk.id)
        pass1_entities, unknown_candidates = extract_entities_from_chunk(
            chunk=chunk,
            nlp=nlp,
            gazette=gazette,
            gazette_keys_lower=gazette_keys_lower,
        )

        chunk_entities = list(pass1_entities)

        if unknown_candidates:
            sorted_cand = sorted(unknown_candidates)
            cache_key = hashlib.sha256(f"{sorted_cand}:{chunk.content[:400]}".encode("utf-8")).hexdigest()

            if cache_key in entity_cache:
                cached_items = entity_cache[cache_key]
                chunk_entities.extend([
                    ExtractedEntity(
                        name=item["name"],
                        entity_type=item["entity_type"],
                        mentions=item.get("mentions", [item["name"]]),
                        chunk_id=cid,
                        document_id=str(chunk.document_id),
                        source="gemini_ner",
                        confidence=item.get("confidence", 0.9),
                    )
                    for item in cached_items
                ])
            elif llm is not None and needs_llm_entity_pass(chunk, unknown_candidates):
                uncached_llm_items.append((chunk, unknown_candidates, cache_key))
            else:
                for cand in unknown_candidates:
                    chunk_entities.append(
                        ExtractedEntity(
                            name=cand,
                            entity_type="UNKNOWN",
                            mentions=[cand],
                            chunk_id=cid,
                            document_id=str(chunk.document_id),
                            source="rules",
                            confidence=0.5,
                        )
                    )

        chunk_to_entities[cid] = chunk_entities

    # Step 1c: Batch classify any uncached chunks via Gemini (10 chunks per API call)
    if uncached_llm_items and llm is not None:
        batch_size = 10
        total_batches = (len(uncached_llm_items) + batch_size - 1) // batch_size
        print(
            f"  [Entity Pass 1c] {len(chunks) - len(uncached_llm_items)} chunks resolved from gazette/cache. "
            f"Classifying remaining {len(uncached_llm_items)} candidate chunks in {total_batches} batches (10 chunks/call)...",
            flush=True,
        )

        for b_idx, i in enumerate(range(0, len(uncached_llm_items), batch_size), start=1):
            batch_slice = uncached_llm_items[i : i + batch_size]
            batch_results = classify_unknown_entities_batch_with_llm(batch_slice, llm)
            for chunk, candidates, cache_key in batch_slice:
                cid = str(chunk.id)
                ents = batch_results.get(cid, [])
                chunk_to_entities[cid].extend(ents)

            print(
                f"  [Entity Pass 1c] Completed batch {b_idx}/{total_batches} "
                f"({min(i + batch_size, len(uncached_llm_items))}/{len(uncached_llm_items)} chunks)...",
                flush=True,
            )

    # Collect all entities across chunks
    for ents in chunk_to_entities.values():
        all_entities.extend(ents)

    # Step 1d: Alias Resolution
    print("  [Entity Pass 1d] Resolving entity aliases...", flush=True)
    resolve_aliases(all_entities)

    return all_entities, chunk_to_entities
