from pathlib import Path
import pytest

from src.ingestion.images import classify_image_type, process_corpus_images
from src.ingestion.chunking import create_image_chunk


def test_classify_image_types():
    plate_type, plate_entity = classify_image_type("plate_08_creature_weeping_lurker.png")
    assert plate_type == "figure_plate"
    assert plate_entity == "Weeping Lurker"

    portrait_type, portrait_entity = classify_image_type(
        "atmo_portrait_character_aldous_wrenfield_the_last_warden.png"
    )
    assert portrait_type == "portrait"
    assert "Aldous Wrenfield" in portrait_entity

    heraldry_type, heraldry_entity = classify_image_type(
        "atmo_heraldry_faction_house_morvain.png"
    )
    assert heraldry_type == "heraldry"
    assert heraldry_entity == "House Morvain"


def test_process_corpus_images_offline(corpus_root: Path):
    # Test image discovery and metadata extraction without external vision calls
    assets = process_corpus_images(corpus_root, use_vision=False)

    assert len(assets) >= 70, f"Expected ~70 unique visual assets, found {len(assets)}"

    figure_plates = [a for a in assets if a.asset_type == "figure_plate"]
    assert len(figure_plates) == 15, f"Expected 15 unique figure plates, found {len(figure_plates)}"

    # Test synthetic chunk generation
    for a in figure_plates[:3]:
        chunk = create_image_chunk(a)
        assert chunk.content, "Synthetic image chunk content must not be empty"
        assert chunk.metadata.get("is_asset_chunk") is True
