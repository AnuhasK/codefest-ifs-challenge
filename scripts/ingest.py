import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import CORPUS_PATH
from src.ingestion.pipeline import run_ingestion


def main():
    parser = argparse.ArgumentParser(description="Ingest the Ashen Era Archive with Dual Embeddings and Contextual Retrieval.")
    parser.add_argument(
        "--corpus-path",
        type=str,
        default=str(CORPUS_PATH),
        help="Path to the Ashen_Era_Archive root folder.",
    )
    parser.add_argument(
        "--no-vision",
        action="store_true",
        help="Skip Gemini Vision API calls for image descriptions (fallback to filename metadata).",
    )
    parser.add_argument(
        "--no-contextual-llm",
        action="store_true",
        help="Use Tier 1 template prefix for all chunks (skip Tier 2 LLM contextual prefixes).",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip generating Voyage AI vector embeddings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and validate corpus without writing to the PostgreSQL database.",
    )

    args = parser.parse_args()

    report = run_ingestion(
        corpus_path=Path(args.corpus_path),
        use_vision=not args.no_vision,
        generate_embeddings_flag=not args.skip_embeddings,
        use_contextual_llm=not args.no_contextual_llm,
        persist_db=not args.dry_run,
    )

    print("\n================ INGESTION SUMMARY REPORT ================")
    print(f"Total Files Discovered:       {report.documents_discovered}")
    print(f"Logical Documents Created:    {report.logical_documents_created}")
    print(f"Documents Extracted:          {report.files_extracted}")
    print(f"Scanned PDFs OCR'd:           {report.ocr_processed_files}")
    print(f"Image Assets Ingested:        {report.images_processed} ({report.figure_plates_extracted} plates, {report.atmospheric_art_described} art)")
    print(f"Total Chunks Stored:          {report.total_chunks_created}")
    print(f"Corpus Validation Status:     {'PASSED' if report.validation.is_valid else 'FAILED'}")
    if report.validation.warnings:
        print("Warnings:")
        for w in report.validation.warnings:
            print(f"  - {w}")
    print("==========================================================\n")


if __name__ == "__main__":
    main()
