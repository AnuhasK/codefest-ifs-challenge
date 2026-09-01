from pathlib import Path
from typing import List, Dict
import re
from uuid import uuid4

from src.models.document import DiscoveredFile, LogicalDocument, DocumentRepresentation


IGNORE_FILES = {"README.txt", "sample_questions.json", ".DS_Store"}


def discover_corpus(corpus_path: Path | str) -> List[DiscoveredFile]:
    """Recursively scan corpus directory and classify all files."""
    corpus_root = Path(corpus_path).resolve()
    discovered: List[DiscoveredFile] = []

    if not corpus_root.exists():
        raise FileNotFoundError(f"Corpus root path not found: {corpus_root}")

    for file_path in corpus_root.rglob("*"):
        if not file_path.is_file():
            continue

        if file_path.name in IGNORE_FILES or file_path.name.startswith("."):
            continue

        # Determine relative category
        rel_path = file_path.relative_to(corpus_root)
        parts = rel_path.parts

        ext = file_path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            category = "images"
        else:
            category = parts[0].lower() if len(parts) > 1 else "root"
            # Validate allowed categories
            if category not in {"chronicles", "wiki", "codex", "ephemera"}:
                category = "ephemera"  # fallback for unclassified items

        is_scan = ".scan." in file_path.name.lower() or file_path.name.lower().endswith(".scan.pdf")
        
        # Calculate clean stem (removing .scan and extension)
        stem = file_path.stem
        if stem.endswith(".scan"):
            stem = stem[:-5]

        discovered.append(
            DiscoveredFile(
                path=str(file_path.resolve()),
                filename=file_path.name,
                stem=stem,
                extension=file_path.suffix.lower(),
                category=category,
                is_scan=is_scan,
                file_size_bytes=file_path.stat().st_size,
            )
        )

    return discovered


def bundle_documents(files: List[DiscoveredFile]) -> List[LogicalDocument]:
    """
    Group files by canonical logical document.
    PDF, DOCX, and scan variants with the same stem belong to one LogicalDocument.
    Wiki markdown files are each their own LogicalDocument.
    Standalone images are bundled under an image collection document.
    """
    # Group by (category, stem)
    grouped: Dict[tuple, List[DiscoveredFile]] = {}

    for f in files:
        if f.extension not in {".pdf", ".docx", ".md", ".txt"}:
            continue
        key = (f.category, f.stem)
        grouped.setdefault(key, []).append(f)

    logical_docs: List[LogicalDocument] = []

    for (category, stem), representations in grouped.items():
        doc_id = uuid4()
        
        # Format human-readable title from stem
        title = re.sub(r"[_\-]+", " ", stem).strip().title()

        # Choose primary source path (prefer docx over pdf for text richness if available)
        primary_file = representations[0]
        for rep in representations:
            if rep.extension == ".docx" or rep.extension == ".md":
                primary_file = rep
                break

        doc_reps = [
            DocumentRepresentation(
                id=uuid4(),
                document_id=doc_id,
                file_path=r.path,
                format="scan_pdf" if r.is_scan else r.extension.lstrip("."),
                file_size_bytes=r.file_size_bytes,
            )
            for r in representations
        ]

        logical_docs.append(
            LogicalDocument(
                id=doc_id,
                title=title,
                source_category=category,
                source_path=primary_file.path,
                representations=doc_reps,
                metadata={
                    "stem": stem,
                    "representation_count": len(doc_reps),
                    "formats": [r.format for r in doc_reps],
                },
            )
        )

    return logical_docs
