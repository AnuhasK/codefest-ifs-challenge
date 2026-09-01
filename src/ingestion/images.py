from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import uuid4
import re
import json
from PIL import Image

from src.models.document import Asset
from src.config import GEMINI_API_KEY, LLM_MODEL


def classify_image_type(filename: str) -> tuple[str, str]:
    """
    Classify asset type and extract the entity name from filename pattern.
    Returns: (asset_type, entity_name)
    """
    name = Path(filename).stem.lower()

    if name.startswith("plate_"):
        # Format: plate_00_location_marrowwatch or plate_08_creature_weeping_lurker
        parts = name.split("_")
        if len(parts) >= 4:
            entity_slug = "_".join(parts[3:])
        else:
            entity_slug = "_".join(parts[2:]) if len(parts) >= 3 else parts[-1]
        entity_name = entity_slug.replace("_", " ").title()
        return ("figure_plate", entity_name)

    elif name.startswith("atmo_portrait_character_"):
        entity_slug = name.replace("atmo_portrait_character_", "")
        return ("portrait", entity_slug.replace("_", " ").title())

    elif name.startswith("atmo_heraldry_faction_"):
        entity_slug = name.replace("atmo_heraldry_faction_", "")
        return ("heraldry", entity_slug.replace("_", " ").title())

    elif name.startswith("atmo_landscape_location_"):
        entity_slug = name.replace("atmo_landscape_location_", "")
        return ("landscape", entity_slug.replace("_", " ").title())

    elif name.startswith("atmo_battle_painting_conflict_"):
        entity_slug = name.replace("atmo_battle_painting_conflict_", "")
        return ("battle_painting", entity_slug.replace("_", " ").title())

    elif name.startswith("atmo_creature_creature_"):
        entity_slug = name.replace("atmo_creature_creature_", "")
        return ("creature", entity_slug.replace("_", " ").title())

    elif name.startswith("atmo_relic_artifact_"):
        entity_slug = name.replace("atmo_relic_artifact_", "")
        return ("relic", entity_slug.replace("_", " ").title())

    else:
        return ("illustration", name.replace("_", " ").title())


def process_image_with_gemini(
    image_path: str, asset_type: str, entity_name: str
) -> tuple[str, Dict[str, Any]]:
    """
    Use Gemini Vision (via google-genai) to extract structured data or visual descriptions.
    Falls back to heuristic descriptions if API call is unconfigured or fails.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("your_"):
        # Fallback when offline or in test environments
        return (
            f"{asset_type.replace('_', ' ').title()} illustration representing {entity_name}.",
            {"entity_name": entity_name, "asset_type": asset_type},
        )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        img = Image.open(image_path)

        if asset_type == "figure_plate":
            prompt = (
                "You are an archival intelligence system analyzing a fantasy codex figure plate. "
                "Extract all data visible in this image. Return valid JSON only with keys: "
                "entity_name (string), metric_type (e.g. Threat Rating, Garrison Strength, Attunement Cost), "
                "numerical_value (number or string), scale_or_unit (string, e.g. of 10, per the Vanguard scale), "
                "provenance_note (string if any text is at the bottom). "
                "Do not include markdown code fences, return pure JSON."
            )
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=[img, prompt],
            )
            text_response = response.text.strip()
            
            # Clean possible markdown fences
            if text_response.startswith("```"):
                text_response = re.sub(r"^```(?:json)?\s*", "", text_response)
                text_response = re.sub(r"\s*```$", "", text_response)

            try:
                extracted_data = json.loads(text_response)
            except Exception:
                extracted_data = {"raw_vision_output": text_response}

            val = extracted_data.get("numerical_value", "")
            metric = extracted_data.get("metric_type", "Metric")
            unit = extracted_data.get("scale_or_unit", "")
            description = (
                f"Figure plate for {entity_name}: {metric} is {val} ({unit}). "
                f"{extracted_data.get('provenance_note', '')}".strip()
            )
            return (description, extracted_data)

        else:
            prompt = (
                f"Describe this fantasy artwork depicting '{entity_name}'. "
                "Focus on visual details: what the character or entity is holding, "
                "armor, colors, emblem symbols, materials, landscape features, or architectural details. "
                "Be factual and concise (2-4 sentences)."
            )
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=[img, prompt],
            )
            description = response.text.strip()
            return (description, {"entity_name": entity_name, "asset_type": asset_type})

    except Exception as e:
        # Graceful fallback on API error
        return (
            f"Visual asset ({asset_type}) depicting {entity_name}.",
            {"entity_name": entity_name, "asset_type": asset_type, "error": str(e)},
        )


def process_corpus_images(
    corpus_path: Path | str, use_vision: bool = True
) -> List[Asset]:
    """
    Scan all image directories in the corpus, deduplicate identical figure plates,
    and generate Asset models with vision descriptions & extracted data.
    """
    corpus_root = Path(corpus_path).resolve()
    assets: List[Asset] = []
    seen_filenames = set()

    # Search for all image files
    image_paths = sorted(
        list(corpus_root.glob("images/*.png"))
        + list(corpus_root.glob("wiki/images/*.png"))
        + list(corpus_root.glob("codex/images/*.png"))
    )

    for img_path in image_paths:
        if not img_path.is_file():
            continue

        # Deduplicate identical filenames across directories (e.g. images/plate_X vs codex/images/plate_X)
        if img_path.name in seen_filenames:
            continue
        seen_filenames.add(img_path.name)

        asset_type, entity_name = classify_image_type(img_path.name)

        if use_vision:
            description, extracted_data = process_image_with_gemini(
                str(img_path), asset_type, entity_name
            )
        else:
            description = f"Visual asset ({asset_type}) representing {entity_name}."
            extracted_data = {"entity_name": entity_name, "asset_type": asset_type}

        asset = Asset(
            id=uuid4(),
            file_path=str(img_path),
            asset_type=asset_type,
            entity_name=entity_name,
            description=description,
            extracted_data=extracted_data,
            metadata={
                "filename": img_path.name,
                "category": img_path.parent.name,
            },
        )
        assets.append(asset)

    return assets
