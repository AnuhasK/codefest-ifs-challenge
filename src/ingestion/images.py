from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import uuid4
import re
import json
import logging
from PIL import Image

from src.models.document import Asset
from src.config import (
    GEMINI_API_KEYS,
    GEMINI_API_KEY,
    LLM_MODEL,
    USE_LOCAL_OCR,
)
from src.providers.key_rotator import get_shared_gemini_rotator

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Local OCR for figure plates (rapidocr-onnxruntime)
# ---------------------------------------------------------------------------

def extract_plate_text_ocr(image_path: str) -> str:
    """
    Extract raw text from a figure plate using RapidOCR (local, offline).

    Returns all detected text lines joined by newline.
    Raises ImportError if rapidocr-onnxruntime is not installed.
    """
    from rapidocr_onnxruntime import RapidOCR

    ocr_engine = RapidOCR()
    result, _ = ocr_engine(image_path)

    if not result:
        return ""

    # result is a list of (bbox, text, confidence) tuples
    # Sort by vertical position (top of bounding box) to preserve reading order
    sorted_results = sorted(result, key=lambda r: r[0][0][1])
    lines = [text for _, text, _ in sorted_results]
    return "\n".join(lines)


def parse_plate_structured_data(ocr_text: str, entity_name: str) -> Dict[str, Any]:
    """
    Parse OCR text from a figure plate into structured fields.

    Attempts to extract:
    - entity_name: name/title shown on the plate
    - metric_type: what is being measured (Threat Rating, Garrison Strength, etc.)
    - numerical_value: the number shown
    - scale_or_unit: the scale or unit description
    - provenance_note: any attribution text (e.g., "As entered into the Codex Vaeloria")
    """
    data: Dict[str, Any] = {"entity_name": entity_name}

    lines = [line.strip() for line in ocr_text.split("\n") if line.strip()]

    if not lines:
        return data

    metric_patterns = [
        r"[Tt]hreat\s*[Rr]ating",
        r"[Gg]arrison\s*[Ss]trength",
        r"[Aa]ttunement\s*[Cc]ost",
        r"[Dd]efensive\s*[Rr]ating",
        r"[Pp]opulation",
        r"[Ss]trategic\s*[Vv]alue",
    ]

    for line in lines:
        for pattern in metric_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                data["metric_type"] = line.strip()
                break

        numeric_match = re.match(r"^[\s]*([\d,]+(?:\.\d+)?)\s*$", line)
        if numeric_match and "numerical_value" not in data:
            raw_val = numeric_match.group(1).replace(",", "")
            try:
                data["numerical_value"] = (
                    int(raw_val) if "." not in raw_val else float(raw_val)
                )
            except ValueError:
                data["numerical_value"] = numeric_match.group(1)

        if re.search(r"(per the|out of|of \d+|scale)", line, re.IGNORECASE):
            data["scale_or_unit"] = line.strip()

        if re.search(
            r"(entered into|codex|verified|recorded by|as per)", line, re.IGNORECASE
        ):
            data["provenance_note"] = line.strip()

    return data


# ---------------------------------------------------------------------------
# Gemini Vision processing with Key Rotator & Rate Limiter
# ---------------------------------------------------------------------------

def _process_plate_with_gemini(
    image_path: str, entity_name: str
) -> tuple[str, Dict[str, Any]]:
    """
    Fallback: Extract structured data from a figure plate using Gemini Vision.
    Uses GeminiKeyRotator to respect 5 RPM and 20 RPD limits across keys.
    """
    from google import genai

    rotator = get_shared_gemini_rotator()
    prompt = (
        "You are an archival intelligence system analyzing a fantasy codex figure plate. "
        "Extract all data visible in this image. Return valid JSON only with keys: "
        "entity_name (string), metric_type (e.g. Threat Rating, Garrison Strength, Attunement Cost), "
        "numerical_value (number or string), scale_or_unit (string, e.g. of 10, per the Vanguard scale), "
        "provenance_note (string if any text is at the bottom). "
        "Do not include markdown code fences, return pure JSON."
    )
    prompt = rotator.enforce_token_limit(prompt)
    img = Image.open(image_path)

    max_retries = max(3, rotator.available_count)
    for _ in range(max_retries):
        key = rotator.next_key()
        if not key:
            print(
                f"  [Gemini Vision] Daily quota reached across all keys. Fallback metadata for plate '{entity_name}'.",
                flush=True,
            )
            return (
                f"Figure plate illustration representing {entity_name}.",
                {"entity_name": entity_name, "asset_type": "figure_plate", "fallback": True},
            )

        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=[img, prompt],
            )
            text_response = (response.text or "").strip()

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
            print(f"  [Gemini Vision] Extracted plate '{entity_name}' via {key[:6]}...", flush=True)
            return (description, extracted_data)

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                rotator.mark_exhausted(key, reason="429 Resource Exhausted")
                print(f"  [Gemini Vision] Key {key[:6]}... hit quota. Rotating to next key...", flush=True)
                continue
            logger.error("Gemini plate extraction error for '%s': %s", entity_name, e)
            break

    return (
        f"Figure plate illustration representing {entity_name}.",
        {"entity_name": entity_name, "asset_type": "figure_plate", "fallback": True},
    )


def _process_atmospheric_with_gemini(
    image_path: str, entity_name: str, asset_type: str
) -> tuple[str, Dict[str, Any]]:
    """
    Generate a detailed visual description of atmospheric art using Gemini Vision.
    Rotates through configured keys, respecting 5 RPM and 20 requests/day per key.
    """
    from google import genai

    rotator = get_shared_gemini_rotator()
    prompt = (
        f"Describe this fantasy artwork depicting '{entity_name}'. "
        "Focus on visual details: what the character or entity is holding, "
        "armor, colors, emblem symbols, materials, landscape features, or architectural details. "
        "Be factual and concise (2-4 sentences)."
    )
    prompt = rotator.enforce_token_limit(prompt)
    img = Image.open(image_path)

    max_retries = max(3, rotator.available_count)
    for _ in range(max_retries):
        key = rotator.next_key()
        if not key:
            print(
                f"  [Gemini Vision] Daily quota reached across all keys. Using metadata fallback for '{entity_name}'.",
                flush=True,
            )
            return (
                f"Visual asset ({asset_type}) depicting {entity_name}.",
                {"entity_name": entity_name, "asset_type": asset_type, "quota_exhausted": True},
            )

        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=[img, prompt],
            )
            description = (response.text or "").strip()
            print(f"  [Gemini Vision] Described '{entity_name}' via {key[:6]}...", flush=True)
            return (description, {"entity_name": entity_name, "asset_type": asset_type})

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                rotator.mark_exhausted(key, reason="429 Resource Exhausted")
                print(f"  [Gemini Vision] Key {key[:6]}... hit quota. Rotating to next key...", flush=True)
                continue
            logger.error("Gemini Vision failed for '%s': %s", entity_name, e)
            break

    return (
        f"Visual asset ({asset_type}) depicting {entity_name}.",
        {"entity_name": entity_name, "asset_type": asset_type, "fallback": True},
    )


# ---------------------------------------------------------------------------
# Unified image processing
# ---------------------------------------------------------------------------

def process_image(
    image_path: str, asset_type: str, entity_name: str
) -> tuple[str, Dict[str, Any]]:
    """
    Process an image using the appropriate strategy:
    - Figure plates: RapidOCR (local, offline, zero API calls) with Gemini Vision fallback
    - Atmospheric art: Gemini Vision with key rotation (5 RPM, 20 RPD) & fallback
    """
    # ── Figure plates: try local OCR first ──
    if asset_type == "figure_plate" and USE_LOCAL_OCR:
        try:
            ocr_text = extract_plate_text_ocr(image_path)
            if ocr_text.strip():
                extracted_data = parse_plate_structured_data(ocr_text, entity_name)
                val = extracted_data.get("numerical_value", "")
                metric = extracted_data.get("metric_type", "Metric")
                unit = extracted_data.get("scale_or_unit", "")
                prov = extracted_data.get("provenance_note", "")
                description = (
                    f"Figure plate for {entity_name}: {metric} is {val} ({unit}). "
                    f"{prov}".strip()
                )
                extracted_data["ocr_source"] = "rapidocr"
                print(
                    f"  [RapidOCR] Extracted figure plate '{entity_name}': {metric} = {val} ({unit}) [0 API calls]",
                    flush=True,
                )
                return (description, extracted_data)
            else:
                print(
                    f"  [RapidOCR] Empty text for plate '{entity_name}', falling back to Gemini Vision.",
                    flush=True,
                )
        except ImportError:
            print(
                f"  [RapidOCR] Library not installed, falling back to Gemini Vision for '{entity_name}'.",
                flush=True,
            )
        except Exception as e:
            print(
                f"  [RapidOCR] Extraction issue for '{entity_name}' ({e}), falling back to Gemini Vision.",
                flush=True,
            )

    # ── Gemini Vision path (atmospheric art, or figure plate fallback) ──
    rotator = get_shared_gemini_rotator()
    if rotator.key_count == 0:
        return (
            f"{asset_type.replace('_', ' ').title()} illustration representing {entity_name}.",
            {"entity_name": entity_name, "asset_type": asset_type},
        )

    if asset_type == "figure_plate":
        return _process_plate_with_gemini(image_path, entity_name)
    else:
        return _process_atmospheric_with_gemini(image_path, entity_name, asset_type)


def process_corpus_images(
    corpus_path: Path | str, use_vision: bool = True
) -> List[Asset]:
    """
    Scan all image directories in the corpus, deduplicate identical figure plates,
    and generate Asset models with vision descriptions & extracted data.

    Figure plates are processed via local OCR (zero API calls) when USE_LOCAL_OCR is True.
    Atmospheric art is processed via Gemini Vision with key rotation and rate limits.
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

        # Deduplicate identical filenames across directories
        if img_path.name in seen_filenames:
            continue
        seen_filenames.add(img_path.name)

        asset_type, entity_name = classify_image_type(img_path.name)

        if use_vision:
            description, extracted_data = process_image(
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
