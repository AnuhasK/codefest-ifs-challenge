import json
import logging
from pathlib import Path, PureWindowsPath
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document
import pymupdf

from src.database.postgres import get_db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def backfill_chunk_locations(corpus_dir_path: str = "Ashen_Era_Archive"):
    corpus_dir = Path(corpus_dir_path).resolve()
    if not corpus_dir.exists():
        corpus_dir = Path("/app/Ashen_Era_Archive").resolve()

    logger.info("Scanning corpus files from %s...", corpus_dir)
    file_map = {}
    if corpus_dir.exists():
        for p in corpus_dir.rglob("*"):
            if p.is_file():
                file_map[p.name.lower()] = p

    logger.info("Indexed %d files from corpus directory.", len(file_map))

    # Pre-parse cache: {fpath: {"type": ..., "data": ...}}
    parsed_cache = {}

    def get_file_data(fpath: Path):
        if fpath in parsed_cache:
            return parsed_cache[fpath]
        ext = fpath.suffix.lower().lstrip(".")
        data = None
        try:
            if ext == "pdf":
                pdf = pymupdf.open(fpath)
                pages = []
                for pno, page in enumerate(pdf, 1):
                    pages.append((pno, page.get_text().lower()))
                data = {"type": "pdf", "pages": pages}
            elif ext in ("md", "txt"):
                raw_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                lines = [l.lower() for l in raw_lines]
                data = {"type": "lines", "lines": lines, "total_lines": len(lines)}
            elif ext == "docx":
                doc = Document(fpath)
                paragraphs = []
                cum_words = []
                running_words = 0
                for idx, p in enumerate(doc.paragraphs, 1):
                    t = p.text.strip()
                    paragraphs.append(t.lower())
                    running_words += len(t.split())
                    cum_words.append(running_words)
                data = {"type": "docx", "paragraphs": paragraphs, "cum_words": cum_words}
        except Exception as e:
            logger.warning("Could not pre-parse %s: %s", fpath.name, e)
            data = None
        parsed_cache[fpath] = data
        return data

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    c.id, 
                    c.content, 
                    c.position, 
                    c.section_title, 
                    c.metadata, 
                    d.id AS doc_id, 
                    d.source_path, 
                    d.source_category
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id;
            """)
            chunks = cur.fetchall()

        logger.info("Found %d chunks to inspect and update.", len(chunks))

        updates = []
        for r in chunks:
            cid = r["id"]
            content = r["content"] or ""
            source_path = r["source_path"]
            meta = dict(r["metadata"] or {})
            pos = r["position"] or 0

            # Visual asset / figure plate
            if not source_path or meta.get("is_asset_chunk"):
                p_start = 1
                p_end = 1
                meta["page"] = 1
                meta["line_start"] = "Plate"
                meta["reference_location"] = "Plate / Visual Record"
                updates.append((p_start, p_end, json.dumps(meta), cid))
                continue

            fname = PureWindowsPath(source_path).name.lower()
            fpath = file_map.get(fname)

            first_snippet = ""
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and len(stripped) >= 15:
                    first_snippet = stripped[:45].lower()
                    break
            if not first_snippet:
                first_snippet = content.strip()[:40].lower()

            p_start = None
            p_end = None

            if fpath:
                fdata = get_file_data(fpath)
                if fdata:
                    ftype = fdata["type"]
                    if ftype == "pdf":
                        for pno, ptext in fdata["pages"]:
                            if first_snippet in ptext:
                                p_start = pno
                                p_end = pno
                                meta["reference_location"] = f"p. {pno}"
                                break
                    elif ftype == "lines":
                        for lno, line in enumerate(fdata["lines"], 1):
                            if first_snippet in line:
                                # Standard book page ~35 lines
                                p_start = max(1, (lno // 35) + 1)
                                p_end = p_start
                                chunk_line_count = len(content.splitlines())
                                meta["line_start"] = lno
                                meta["line_end"] = lno + max(1, chunk_line_count)
                                meta["reference_location"] = f"p. {p_start} (Lines {meta['line_start']}-{meta['line_end']})"
                                break
                    elif ftype == "docx":
                        for pno, ptext in enumerate(fdata["paragraphs"]):
                            if first_snippet in ptext:
                                p_idx = pno + 1
                                cum_w = fdata["cum_words"][pno] if pno < len(fdata["cum_words"]) else 0
                                p_start = max(1, (cum_w // 300) + 1)
                                p_end = p_start
                                meta["paragraph_start"] = p_idx
                                meta["line_start"] = f"para {p_idx}"
                                meta["reference_location"] = f"p. {p_start} (Para {p_idx})"
                                break

            # Fallback if text search did not pinpoint exact offset
            if not p_start:
                p_start = max(1, (pos // 2) + 1)
                p_end = p_start
                est_line_start = (pos * 25) + 1
                est_line_end = est_line_start + len(content.splitlines())
                meta["line_start"] = est_line_start
                meta["line_end"] = est_line_end
                meta["reference_location"] = f"p. {p_start} (Lines {est_line_start}-{est_line_end})"

            meta["page"] = p_start
            updates.append((p_start, p_end, json.dumps(meta), cid))

        logger.info("Executing batch update for %d chunks...", len(updates))
        with conn.cursor() as cur:
            cur.executemany("""
                UPDATE chunks
                SET page_start = %s,
                    page_end = %s,
                    metadata = %s::jsonb
                WHERE id = %s;
            """, updates)
        conn.commit()

        logger.info("Successfully updated all %d chunks with page and line reference metadata.", len(updates))


if __name__ == "__main__":
    backfill_chunk_locations()
