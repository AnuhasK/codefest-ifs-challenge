import re
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional
from pydantic import BaseModel, Field
import spacy
from spacy.pipeline import EntityRuler

from src.models.document import Chunk


class ExtractedEntity(BaseModel):
    """Structured entity extracted from a chunk via gazette or NER."""
    name: str
    entity_type: str  # Person, Faction, Place, Event, Artifact, Organization, Creature, Title
    mentions: List[str] = Field(default_factory=list)
    chunk_id: str
    document_id: str
    source: str  # 'gazette' or 'spacy_ner'


TYPE_MAPPING = {
    "person": "Person",
    "character": "Person",
    "faction": "Faction",
    "house": "Faction",
    "place": "Place",
    "location": "Place",
    "fortress": "Place",
    "citadel": "Place",
    "event": "Event",
    "conflict": "Event",
    "war": "Event",
    "accord": "Event",
    "purge": "Event",
    "artifact": "Artifact",
    "relic": "Artifact",
    "creature": "Creature",
    "monster": "Creature",
    "organization": "Organization",
    "cartel": "Organization",
    "choir": "Organization",
    "title": "Title",
    # spaCy default NER labels mapping
    "PERSON": "Person",
    "ORG": "Organization",
    "GPE": "Place",
    "LOC": "Place",
    "EVENT": "Event",
    "FAC": "Place",
    "NORP": "Faction",
}


def _format_entity_name_from_stem(stem: str) -> str:
    """Convert filename token like 'ederon_fellgard' to 'Ederon Fellgard'."""
    words = stem.split("_")
    return " ".join(w.capitalize() for w in words if w)


def build_gazette_from_corpus(corpus_path: Path | str) -> Dict[str, str]:
    """
    Parse wiki and archive filenames to build a canonical entity dictionary.
    Uses first headings from wiki files, image prefixes, and stem variations.
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
                ("atmo_portrait_character_", "Person"),
                ("atmo_heraldry_faction_", "Faction"),
                ("atmo_landscape_location_", "Place"),
                ("atmo_battle_painting_conflict_", "Event"),
                ("atmo_creature_creature_", "Creature"),
                ("atmo_relic_artifact_", "Artifact"),
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
            name_from_stem = _format_entity_name_from_stem(stem)

            # Read first header and opening text
            try:
                content = file.read_text(encoding="utf-8")
                lines = [l.strip() for l in content.split("\n") if l.strip()]
                first_header = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else name_from_stem
                first_para = lines[1].lower() if len(lines) > 1 else ""

                # Infer type
                inferred_type = "Person"
                if any(w in first_para for w in ("faction", "house", "vanguard", "cartel", "choir", "order", "military")):
                    inferred_type = "Faction"
                elif any(w in first_para for w in ("place", "location", "fortress", "citadel", "keep", "marsh", "vale", "city", "abbey", "hold")):
                    inferred_type = "Place"
                elif any(w in first_para for w in ("war", "battle", "accord", "conflict", "purge", "reckoning", "treaty")):
                    inferred_type = "Event"
                elif any(w in first_para for w in ("artifact", "relic", "weapon", "blade", "sword", "crown", "aegis", "sceptre", "lantern", "gauntlet", "psalter")):
                    inferred_type = "Artifact"
                elif any(w in first_para for w in ("creature", "beast", "monster", "wyrm", "leviathan", "lurker", "shrike", "stag", "wraith", "colossus")):
                    inferred_type = "Creature"

                # If image already classified it, keep that; else use inferred
                chosen_type = gazette.get(first_header, gazette.get(name_from_stem, inferred_type))
                gazette[first_header] = chosen_type
                gazette[name_from_stem] = chosen_type
                if first_header.lower().startswith("the "):
                    gazette[first_header[4:]] = chosen_type
                if name_from_stem.lower().startswith("The "):
                    gazette[name_from_stem[4:]] = chosen_type
            except Exception:
                gazette[name_from_stem] = "Person"

    return gazette


def build_spacy_pipeline(gazette: Dict[str, str]) -> spacy.Language:
    """
    Construct spaCy pipeline with EntityRuler for gazette matching + standard NER.
    """
    try:
        nlp = spacy.load("en_core_web_trf")
    except Exception:
        nlp = spacy.load("en_core_web_sm")

    # Add EntityRuler before standard NER
    ruler = nlp.add_pipe("entity_ruler", before="ner" if "ner" in nlp.pipe_names else None)

    patterns = []
    for name, entity_type in gazette.items():
        if not name or len(name) < 2:
            continue
        # Exact string match with ID
        patterns.append({
            "label": entity_type.upper(),
            "pattern": name,
            "id": name,
        })
        # Case-insensitive token pattern
        tokens = name.split()
        patterns.append({
            "label": entity_type.upper(),
            "pattern": [{"LOWER": t.lower()} for t in tokens],
            "id": name,
        })

    ruler.add_patterns(patterns)
    return nlp


def extract_entities_from_chunk(
    chunk: Chunk,
    nlp: spacy.Language,
    gazette_keys_lower: Set[str],
) -> List[ExtractedEntity]:
    """Extract entities from a single chunk using the configured spaCy pipeline."""
    if not chunk.content or not chunk.content.strip():
        return []

    doc = nlp(chunk.content)
    seen_entities: Dict[str, ExtractedEntity] = {}

    for ent in doc.ents:
        raw_text = ent.text.strip()
        if len(raw_text) < 2 or raw_text.isnumeric():
            continue

        raw_lower = raw_text.lower()
        is_gazette = bool(ent.ent_id_) or (raw_lower in gazette_keys_lower)
        if ent.ent_id_ and ent.ent_id_.lower() in gazette_keys_lower:
            is_gazette = True

        source = "gazette" if is_gazette else "spacy_ner"
        
        # Map label
        raw_label = ent.label_.upper()
        entity_type = TYPE_MAPPING.get(raw_label, TYPE_MAPPING.get(ent.label_, "Person"))

        canonical_name = ent.ent_id_ if ent.ent_id_ else raw_text

        if canonical_name in seen_entities:
            seen_entities[canonical_name].mentions.append(raw_text)
        else:
            seen_entities[canonical_name] = ExtractedEntity(
                name=canonical_name,
                entity_type=entity_type,
                mentions=[raw_text],
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                source=source,
            )

    return list(seen_entities.values())


def extract_entities_from_corpus(
    chunks: List[Chunk],
    corpus_path: Path | str,
    nlp: Optional[spacy.Language] = None,
) -> Tuple[List[ExtractedEntity], Dict[str, List[ExtractedEntity]]]:
    """
    Extract entities across all corpus chunks without any LLM calls.
    Returns:
        - List of all ExtractedEntity records
        - Mapping of chunk_id -> list of ExtractedEntity in that chunk
    """
    gazette = build_gazette_from_corpus(corpus_path)
    gazette_keys_lower = {k.lower() for k in gazette.keys()}

    if nlp is None:
        nlp = build_spacy_pipeline(gazette)

    all_entities: List[ExtractedEntity] = []
    chunk_to_entities: Dict[str, List[ExtractedEntity]] = {}

    for chunk in chunks:
        cid = str(chunk.id)
        entities = extract_entities_from_chunk(chunk, nlp, gazette_keys_lower)
        chunk_to_entities[cid] = entities
        all_entities.extend(entities)

    return all_entities, chunk_to_entities
