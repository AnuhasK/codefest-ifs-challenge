import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# PostgreSQL Config
POSTGRES_DB = os.getenv("POSTGRES_DB", "ashen_era")
POSTGRES_USER = os.getenv("POSTGRES_USER", "ashen")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "ashen_secret")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))

POSTGRES_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Neo4j Config
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "ashen_secret")

# Corpus Path
CORPUS_PATH = Path(os.getenv("CORPUS_PATH", str(BASE_DIR / "Ashen_Era_Archive"))).resolve()

# Auto-configure Tesseract path on Windows if present
TESSERACT_WINDOWS_PATH = r"C:\Program Files\Tesseract-OCR"
if os.path.exists(TESSERACT_WINDOWS_PATH) and TESSERACT_WINDOWS_PATH not in os.environ.get("PATH", ""):
    os.environ["PATH"] += os.pathsep + TESSERACT_WINDOWS_PATH

# AI / Provider API Keys (Supports comma-separated keys for round-robin rotation)
_raw_gemini_keys = os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", ""))
GEMINI_API_KEYS = [k.strip() for k in _raw_gemini_keys.split(",") if k.strip()]
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")

# Gemini Rate & Quota Limits (Free Tier Protection)
GEMINI_MAX_RPM_PER_KEY = int(os.getenv("GEMINI_MAX_RPM_PER_KEY", "15"))
GEMINI_MAX_DAILY_PER_KEY = int(os.getenv("GEMINI_MAX_DAILY_PER_KEY", "1000"))
GEMINI_MAX_INPUT_TOKENS = int(os.getenv("GEMINI_MAX_INPUT_TOKENS", "100000"))

# Cache Path
EMBEDDINGS_CACHE_PATH = BASE_DIR / "data" / "embeddings_cache.sqlite"

# Models
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "gemini-embedding-001" if EMBEDDING_PROVIDER == "gemini" else "voyage-3-large"
)
EMBEDDING_DIMENSION = 1024
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.8-flash")
LLM_MODEL_STRONG = os.getenv("LLM_MODEL_STRONG", "gemini-3.8-flash")


# Chunking Parameters
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "512"))
CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", "100"))
CHUNK_MIN_TOKENS = int(os.getenv("CHUNK_MIN_TOKENS", "50"))

# Image Processing
# When True, figure plates are processed with RapidOCR (local, offline, zero API calls).
# When False, falls back to Gemini Vision for all image processing.
USE_LOCAL_OCR = os.getenv("USE_LOCAL_OCR", "true").lower() in ("true", "1", "yes")

# Phase 3 — Hybrid Retrieval & Reranker Configuration
RERANKER_PROVIDER = os.getenv("RERANKER_PROVIDER", "flashrank").lower()
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "ms-marco-MiniLM-L-12-v2")
RRF_K = int(os.getenv("RRF_K", "60"))
RETRIEVAL_MAX_CHUNKS_PER_DOC = int(os.getenv("RETRIEVAL_MAX_CHUNKS_PER_DOC", "3"))
BM25_TOP_K = int(os.getenv("BM25_TOP_K", "50"))
DENSE_TOP_K = int(os.getenv("DENSE_TOP_K", "50"))
RERANKER_TOP_K = int(os.getenv("RERANKER_TOP_K", "20"))

