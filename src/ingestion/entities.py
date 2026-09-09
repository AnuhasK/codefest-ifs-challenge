import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from pydantic import BaseModel, Field
import spacy
from spacy.pipeline import EntityRuler

from src.config import BASE_DIR, GEMINI_NER_BATCH_SIZE
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
    # Core Ontology and Standard Synonyms
    "person": "PERSON",
    "individual": "PERSON",
    "character": "PERSON",
    "human": "PERSON",
    "figure": "PERSON",

    "faction": "FACTION",
    "political_faction": "FACTION",

    "place": "PLACE",
    "location": "PLACE",
    "geographic_location": "PLACE",
    "settlement": "PLACE",
    "region": "PLACE",

    "event": "EVENT",
    "historical_event": "EVENT",
    "conflict": "EVENT",
    "battle": "EVENT",

    "artifact": "ARTIFACT",
    "relic": "ARTIFACT",
    "object": "ARTIFACT",
    "item": "ARTIFACT",
    "weapon": "ARTIFACT",

    "creature": "CREATURE",
    "monster": "CREATURE",
    "beast": "CREATURE",
    "fauna": "CREATURE",

    "organization": "ORGANIZATION",
    "institution": "ORGANIZATION",
    "group": "ORGANIZATION",
    "order": "ORGANIZATION",
    "guild": "ORGANIZATION",

    "title": "TITLE",
    "honorific": "TITLE",
    "rank": "TITLE",

    "dynasty": "DYNASTY",
    "lineage": "DYNASTY",
    "house": "DYNASTY",

    "deity": "DEITY",
    "god": "DEITY",
    "divinity": "DEITY",

    "concept": "CONCEPT",
    "philosophy": "CONCEPT",
    "phenomenon": "CONCEPT",

    "document": "DOCUMENT",
    "text": "DOCUMENT",
    "record": "DOCUMENT",
    "manuscript": "DOCUMENT",

    "building": "BUILDING",
    "structure": "BUILDING",
    "monument": "BUILDING",

    "military_unit": "MILITARY_UNIT",
    "military": "MILITARY_UNIT",
    "unit": "MILITARY_UNIT",

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

TITLE_PREFIXES = ("ser ", "lord ", "lady ", "high ", "the ")

# Generalized linguistic regexes for epithets and genitive place/house attachments
RE_EPITHET_SUFFIX = re.compile(
    r"\s+the\s+[A-Za-z]+(?:-[A-Za-z]+)?$",
    re.IGNORECASE,
)
RE_GENITIVE_SUFFIX = re.compile(
    r"\s+of(?:\s+the)?\s+[A-Za-z]+(?:\s+[A-Za-z]+)?$",
    re.IGNORECASE,
)


def _format_entity_name_from_stem(stem: str) -> str:
    """Convert filename token like 'ederon_fellgard' to 'Ederon Fellgard'."""
    words = stem.split("_")
    return " ".join(w.capitalize() for w in words if w)


def build_gazette_from_corpus(corpus_path: Path | str) -> Dict[str, str]:
    """
    Step 1a: Parse wiki, codex, and archive filenames and infobox tables to build
    a canonical entity dictionary. Zero API calls. Fully offline.
    Derives entities strictly from structural document metadata rather than keyword heuristics.
    """
    corpus_root = Path(corpus_path)
    gazette: Dict[str, str] = {}

    # 1. Inspect image prefixes and figure plates (definitive visual entity categories)
    image_dirs = [corpus_root / "images", corpus_root / "wiki" / "images"]
    for img_dir in image_dirs:
        if not img_dir.exists():
            continue
        for file in img_dir.glob("*.png"):
            stem = file.stem
            # Atmo illustrations
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

            # Figure plates: plate_<num>_<category>_<entity_slug>
            if stem.startswith("plate_"):
                parts = stem.split("_")
                if len(parts) >= 4:
                    cat = parts[2].lower()
                    slug = "_".join(parts[3:])
                    mapped_type = TYPE_MAPPING.get(cat, "UNKNOWN")
                    name = _format_entity_name_from_stem(slug)
                    gazette[name] = mapped_type
                    if name.lower().startswith("the "):
                        gazette[name[4:]] = mapped_type

    # 2. Inspect wiki articles
    wiki_dir = corpus_root / "wiki"
    if wiki_dir.exists():
        for file in wiki_dir.glob("*.md"):
            stem = file.stem
            clean_stem = stem
            detected_type = None

            for pfx, m_type in [
                ("wiki_person_", "PERSON"),
                ("wiki_faction_", "FACTION"),
                ("wiki_place_", "PLACE"),
                ("wiki_creature_", "CREATURE"),
                ("wiki_artifact_", "ARTIFACT"),
                ("wiki_event_", "EVENT"),
                ("wiki_organization_", "ORGANIZATION"),
            ]:
                if stem.startswith(pfx):
                    detected_type = m_type
                    clean_stem = stem[len(pfx):]
                    break

            name_from_stem = _format_entity_name_from_stem(clean_stem)

            # Read document title and structural infobox fields
            try:
                content = file.read_text(encoding="utf-8")
                lines = [l.strip() for l in content.split("\n") if l.strip()]
                first_header = name_from_stem
                if lines:
                    h_line = lines[0].lstrip("# ").strip()
                    # If first line is image link e.g. ![Name](path)
                    img_match = re.match(r"!\[(.*?)\]", h_line)
                    if img_match:
                        first_header = img_match.group(1).strip()
                    elif lines[0].startswith("#"):
                        first_header = h_line
                    elif len(lines) > 1 and lines[1].startswith("#"):
                        first_header = lines[1].lstrip("# ").strip()

                # Clean header parenthetical tags like "(faction)"
                first_header = re.sub(
                    r"\s*\((?:faction|character|conflict|location|place)\)",
                    "",
                    first_header,
                    flags=re.IGNORECASE,
                ).strip()

                # Infer type from structured infobox schema fields if available
                if not detected_type:
                    for line in lines:
                        if not line.startswith("|"):
                            continue
                        parts = [p.strip().lower() for p in line.split("|") if p.strip()]
                        if not parts:
                            continue
                        field = parts[0]
                        if field in ("role", "born", "died", "physical traits", "demeanor"):
                            detected_type = "PERSON"
                            break
                        elif field in ("region", "founded", "status", "elevation"):
                            detected_type = "PLACE"
                            break
                        elif field in ("threat rating", "lair", "diet"):
                            detected_type = "CREATURE"
                            break
                        elif field in ("attunement cost", "forged at", "place of housing", "wielder"):
                            detected_type = "ARTIFACT"
                            break
                        elif field in ("belligerents", "date", "outcome", "major fighting"):
                            detected_type = "EVENT"
                            break
                        elif field in ("organization kind", "organization type", "membership", "leader", "headquarters", "members", "command"):
                            detected_type = "FACTION"
                            break

                chosen_type = gazette.get(first_header, gazette.get(name_from_stem, detected_type or "UNKNOWN"))
                gazette[first_header] = chosen_type
                gazette[name_from_stem] = chosen_type
                if first_header.lower().startswith("the "):
                    gazette[first_header[4:]] = chosen_type
                if name_from_stem.lower().startswith("the "):
                    gazette[name_from_stem[4:]] = chosen_type
            except Exception:
                chosen_type = detected_type or "UNKNOWN"
                gazette[name_from_stem] = chosen_type

    # 3. Inspect codex files
    codex_dir = corpus_root / "codex"
    if codex_dir.exists():
        for file in codex_dir.glob("*.pdf"):
            stem = file.stem
            name = _format_entity_name_from_stem(stem)
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


class GeminiNERExtractor:
    """
    Step 1c: Semantic Entity Extraction provider using Gemini 3.8 Flash.
    Uses full chunk text + gazette ground truth context to extract
    and classify fictional entities into the 15-type Ashen Era ontology.
    """

    def __init__(
        self,
        llm: LLMProvider,
        gazette: Dict[str, str],
        batch_size: int = GEMINI_NER_BATCH_SIZE,
    ):
        self.llm = llm
        self.gazette = gazette
        self.batch_size = batch_size

    def _build_gazette_summary(self) -> str:
        unique_gazette: Dict[str, str] = {}
        for k, v in self.gazette.items():
            core = k[4:] if k.lower().startswith("the ") else k
            if core not in unique_gazette:
                unique_gazette[core] = TYPE_MAPPING.get(v, "UNKNOWN")

        return "\n".join(f"- {name} [{etype}]" for name, etype in sorted(unique_gazette.items()))

    def extract_batch(
        self,
        batch: List[Tuple[Chunk, str]],
        entity_cache: Dict[str, Any],
    ) -> Dict[str, List[ExtractedEntity]]:
        """
        Execute Gemini NER for a batch of uncached chunks.
        Returns mapping of chunk_id -> list of ExtractedEntity.
        """
        if not batch or not self.llm:
            return {}

        gazette_summary = self._build_gazette_summary()
        ontology_str = ", ".join(sorted(list(ASHEN_ERA_ONTOLOGY)))

        chunks_data = [
            {"index": str(idx), "text": chunk.content[:2000]}
            for idx, (chunk, _) in enumerate(batch)
        ]

        prompt = f"""You are an expert entity extractor for the "Ashen Era" fictional universe.

The following entities are already confirmed from the corpus index (treat as ground truth):
{gazette_summary}

For each chunk below, identify ALL named entities in the text — including those already in
the confirmed list above AND any new fictional entities not yet known (characters, factions,
locations, historical events, artifacts, orders, creatures, titles, dynasties, deities, codices).

Allowed entity types:
{ontology_str}

For each entity return:
- "mention": exact surface form from the text
- "canonical_name": standardized name (use confirmed names where possible)
- "type": one of the allowed types above
- "confidence": 0.0–1.0

Rules:
1. Only extract entities explicitly named in the text. Do NOT invent.
2. If a name matches a confirmed gazette entity, use that canonical name and type.
3. If unsure of type, use UNKNOWN.

Chunks (process each independently):
{json.dumps(chunks_data, indent=2)}

Return JSON in this exact structure:
{{
  "0": [{{"mention": "...", "canonical_name": "...", "type": "...", "confidence": 0.95}}],
  "1": [...]
}}
"""

        results: Dict[str, List[ExtractedEntity]] = {}
        try:
            response = self.llm.generate(
                prompt=prompt,
                system_prompt="You are an expert entity extractor for the Ashen Era fictional universe.",
            )
            raw_text = response.content.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text).strip()

            data = json.loads(raw_text)

            for idx, (chunk, cache_key) in enumerate(batch):
                cid = str(chunk.id)
                idx_str = str(idx)
                chunk_ents: List[ExtractedEntity] = []
                serialized_for_cache = []

                for item in data.get(idx_str, []):
                    mention = str(item.get("mention", "")).strip()
                    canonical = str(item.get("canonical_name", mention)).strip()
                    if not canonical or len(canonical) < 2 or canonical.isnumeric():
                        continue
                    raw_type = str(item.get("type", "UNKNOWN")).strip().upper()
                    etype = TYPE_MAPPING.get(raw_type, "UNKNOWN")
                    conf = float(item.get("confidence", 0.85))

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

                entity_cache[cache_key] = serialized_for_cache
                results[cid] = chunk_ents

            _save_entity_cache(entity_cache)

        except Exception as e:
            logger.warning("Gemini NER batch extraction failed: %s", e)
            for chunk, _ in batch:
                results[str(chunk.id)] = []

        return results


def classify_unknown_entities_batch_with_llm(
    items: List[Tuple[Chunk, List[str], str]],
    llm: LLMProvider,
) -> Dict[str, List[ExtractedEntity]]:
    """
    Deprecated: Kept for backwards compatibility. Uses GeminiNERExtractor where possible.
    """
    if not items or not llm:
        return {}

    batch_tuples = [(chunk, cache_key) for chunk, _, cache_key in items]
    cache = _load_entity_cache()
    extractor = GeminiNERExtractor(llm=llm, gazette={})
    return extractor.extract_batch(batch_tuples, cache)


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
    """Strip common titles, epithets, and genitive suffixes to isolate the core entity name."""
    core = name.strip()
    core_lower = core.lower()
    for prefix in TITLE_PREFIXES:
        if core_lower.startswith(prefix):
            core = core[len(prefix):].strip()
            core_lower = core.lower()
            break

    # Strip epithets e.g. " the Pale", " the Red-Handed", " the Oathless"
    core = RE_EPITHET_SUFFIX.sub("", core).strip()
    # Strip genitive suffixes e.g. " of Mournthrone", " of Red Vale"
    core = RE_GENITIVE_SUFFIX.sub("", core).strip()
    return core


def resolve_aliases(
    all_entities: List[ExtractedEntity],
    chunk_to_entities: Optional[Dict[str, List[ExtractedEntity]]] = None,
    embeddings_cache: Optional[Any] = None,
    gazette: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Step 1d: Alias Resolution (0 extra API calls).
    Collapses surface form variants across documents into canonical names.
    Updates all_entities and chunk_to_entities in-place and removes duplicate
    entity entries within chunks.

    Returns: mapping of surface_form -> canonical_name
    """
    alias_map: Dict[str, str] = {}
    if not all_entities and not chunk_to_entities:
        return alias_map

    # Seed canonical candidates with gazette if available (gazette names are ground truth)
    canonical_list: List[str] = []
    seen_canon_lower: Set[str] = set()

    canonical_to_type: Dict[str, str] = {}
    canonical_to_source: Dict[str, str] = {}
    canonical_to_conf: Dict[str, float] = {}

    if gazette:
        for g_name, g_type in sorted(gazette.items(), key=lambda x: (x[0].lower().startswith("the "), len(x[0]))):
            norm = g_name.strip()
            norm_lower = norm.lower()
            if norm_lower not in seen_canon_lower:
                seen_canon_lower.add(norm_lower)
                canonical_list.append(norm)
                canonical_to_type[norm] = TYPE_MAPPING.get(g_type, "UNKNOWN")
                canonical_to_source[norm] = "gazette"
                canonical_to_conf[norm] = 1.0

    # Collect unique entity names from extracted entities
    entities_to_scan = list(all_entities)
    if chunk_to_entities:
        for ents in chunk_to_entities.values():
            entities_to_scan.extend(ents)

    unique_names: List[str] = list({e.name.strip() for e in entities_to_scan if e.name and e.name.strip()})
    source_priority = {"gazette": 3, "gemini_ner": 2, "rules": 1}
    name_to_best_ent: Dict[str, ExtractedEntity] = {}
    for ent in entities_to_scan:
        n = ent.name.strip()
        if n not in name_to_best_ent or source_priority.get(ent.source, 0) > source_priority.get(name_to_best_ent[n].source, 0):
            name_to_best_ent[n] = ent

    # Sort names so higher-priority sources appear first
    unique_names.sort(key=lambda n: (-source_priority.get(name_to_best_ent.get(n, ent).source, 0), len(n)))

    for name in unique_names:
        core_name = _strip_name_affixes(name)
        matched_canonical = None

        # 1. Check if matches any existing canonical core
        for canon in canonical_list:
            canon_core = _strip_name_affixes(canon)
            if name.lower() == canon.lower() or (core_name and core_name.lower() == canon_core.lower()):
                matched_canonical = canon
                break
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
            seen_canon_lower.add(name.lower())
            alias_map[name] = name
            if core_name and core_name != name:
                alias_map[core_name] = name

    # Resolve types and sources for all canonical names
    for ent in entities_to_scan:
        canon = alias_map.get(ent.name.strip(), ent.name.strip())
        curr_src = canonical_to_source.get(canon, "")
        curr_type = canonical_to_type.get(canon, "UNKNOWN")

        if curr_src == "gazette":
            pass
        elif ent.source == "gazette":
            canonical_to_type[canon] = ent.entity_type
            canonical_to_source[canon] = "gazette"
            canonical_to_conf[canon] = 1.0
        elif ent.source == "gemini_ner" and (curr_src != "gemini_ner" or curr_type == "UNKNOWN"):
            canonical_to_type[canon] = ent.entity_type
            canonical_to_source[canon] = "gemini_ner"
            canonical_to_conf[canon] = ent.confidence
        elif canon not in canonical_to_type or curr_type == "UNKNOWN":
            canonical_to_type[canon] = ent.entity_type
            canonical_to_source[canon] = ent.source
            canonical_to_conf[canon] = ent.confidence

    # If chunk_to_entities is provided, update and deduplicate inside each chunk
    if chunk_to_entities is not None:
        for cid, ents in chunk_to_entities.items():
            deduped_chunk_ents: Dict[str, ExtractedEntity] = {}
            for ent in ents:
                canon = alias_map.get(ent.name.strip(), ent.name.strip())
                if ent.name not in ent.mentions:
                    ent.mentions.append(ent.name)
                ent.name = canon
                if canon in canonical_to_type:
                    ent.entity_type = canonical_to_type[canon]

                if canon in deduped_chunk_ents:
                    target = deduped_chunk_ents[canon]
                    for m in ent.mentions:
                        if m not in target.mentions:
                            target.mentions.append(m)
                    target.confidence = max(target.confidence, ent.confidence)
                    if target.source != "gazette" and ent.source == "gazette":
                        target.source = "gazette"
                        target.confidence = 1.0
                        target.entity_type = ent.entity_type
                else:
                    deduped_chunk_ents[canon] = ent
            chunk_to_entities[cid] = list(deduped_chunk_ents.values())

        # Re-sync all_entities from the deduplicated chunk_to_entities
        all_entities.clear()
        for ents in chunk_to_entities.values():
            all_entities.extend(ents)
    else:
        for ent in all_entities:
            canon = alias_map.get(ent.name.strip(), ent.name.strip())
            if ent.name not in ent.mentions:
                ent.mentions.append(ent.name)
            ent.name = canon
            if canon in canonical_to_type:
                ent.entity_type = canonical_to_type[canon]

    return alias_map


def extract_entities_from_chunk(
    chunk: Chunk,
    nlp: spacy.Language,
    gazette: Dict[str, str],
    gazette_keys_lower: Set[str],
) -> Tuple[List[ExtractedEntity], List[str]]:
    """
    Extract entities from a single chunk:
    Pass 1: spaCy EntityRuler (gazette) -> typed entities, confidence=1.0, source='gazette'.
    Returns (entities, []).
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

    return list(seen_entities.values()), []


def extract_entities_from_corpus(
    chunks: List[Chunk],
    corpus_path: Path | str,
    llm: Optional[LLMProvider] = None,
    nlp: Optional[spacy.Language] = None,
) -> Tuple[List[ExtractedEntity], Dict[str, List[ExtractedEntity]]]:
    """
    Complete 4-step entity extraction pipeline:
    - Step 1a: Build gazette from corpus (~126 entities, 0 API calls)
    - Step 1b: spaCy EntityRuler for gazette matching (0 API calls)
    - Step 1c: Gemini structured NER across all chunks in batches of GEMINI_NER_BATCH_SIZE (50)
    - Step 1d: Alias resolution with propagation fix to chunk_to_entities (0 API calls)

    Returns:
    - All extracted entities (deduplicated, aliases resolved)
    - chunk_id -> list of entities mapping (deduplicated, canonical names)
    """
    gazette = build_gazette_from_corpus(corpus_path)
    gazette_keys_lower = {k.lower() for k in gazette.keys()}

    if nlp is None:
        nlp = build_spacy_pipeline(gazette)

    chunk_to_entities: Dict[str, List[ExtractedEntity]] = {}
    entity_cache = _load_entity_cache()
    uncached_items: List[Tuple[Chunk, str]] = []

    # Step 1b: Match gazette entities on all chunks
    for chunk in chunks:
        cid = str(chunk.id)
        pass1_entities, _ = extract_entities_from_chunk(
            chunk=chunk,
            nlp=nlp,
            gazette=gazette,
            gazette_keys_lower=gazette_keys_lower,
        )
        chunk_to_entities[cid] = list(pass1_entities)

        # Check Gemini NER cache
        cache_key = hashlib.sha256(f"gemini_ner:{chunk.content}".encode("utf-8")).hexdigest()
        if cache_key in entity_cache:
            cached_items = entity_cache[cache_key]
            for item in cached_items:
                chunk_to_entities[cid].append(
                    ExtractedEntity(
                        name=item["name"],
                        entity_type=item["entity_type"],
                        mentions=item.get("mentions", [item["name"]]),
                        chunk_id=cid,
                        document_id=str(chunk.document_id),
                        source="gemini_ner",
                        confidence=item.get("confidence", 0.9),
                    )
                )
        elif llm is not None:
            uncached_items.append((chunk, cache_key))

    # Step 1c: Gemini NER on uncached chunks in batches
    if uncached_items and llm is not None:
        batch_size = GEMINI_NER_BATCH_SIZE
        total_batches = (len(uncached_items) + batch_size - 1) // batch_size
        print(
            f"  [Entity Pass 1c] {len(chunks) - len(uncached_items)} chunks resolved from gazette/cache. "
            f"Running Gemini NER on remaining {len(uncached_items)} chunks in {total_batches} batches ({batch_size} chunks/call)...",
            flush=True,
        )

        extractor = GeminiNERExtractor(
            llm=llm,
            gazette=gazette,
            batch_size=batch_size,
        )

        for b_idx, i in enumerate(range(0, len(uncached_items), batch_size), start=1):
            batch_slice = uncached_items[i : i + batch_size]
            batch_results = extractor.extract_batch(batch_slice, entity_cache)
            for chunk, _ in batch_slice:
                cid = str(chunk.id)
                ents = batch_results.get(cid, [])
                chunk_to_entities[cid].extend(ents)

            print(
                f"  [Entity Pass 1c] Completed batch {b_idx}/{total_batches} "
                f"({min(i + batch_size, len(uncached_items))}/{len(uncached_items)} chunks)...",
                flush=True,
            )

    # Collect all entities across chunks
    all_entities: List[ExtractedEntity] = []
    for ents in chunk_to_entities.values():
        all_entities.extend(ents)

    # Step 1d: Alias Resolution with propagation fix to chunk_to_entities
    print("  [Entity Pass 1d] Resolving entity aliases and propagating canonical forms...", flush=True)
    resolve_aliases(
        all_entities=all_entities,
        chunk_to_entities=chunk_to_entities,
        gazette=gazette,
    )

    return all_entities, chunk_to_entities


# Re-export graph persistence function for Phase 4 compatibility
def store_entities_in_neo4j(*args, **kwargs):
    from src.ingestion.graph_storage import store_entities_in_neo4j as _store
    return _store(*args, **kwargs)


def clear_entity_graph(*args, **kwargs):
    from src.ingestion.graph_storage import clear_entity_graph as _clear
    return _clear(*args, **kwargs)

