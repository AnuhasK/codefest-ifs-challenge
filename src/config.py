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
GEMINI_MAX_RPM_PER_KEY = int(os.getenv("GEMINI_MAX_RPM_PER_KEY", "5"))
GEMINI_MAX_DAILY_PER_KEY = int(os.getenv("GEMINI_MAX_DAILY_PER_KEY", "20"))
GEMINI_MAX_INPUT_TOKENS = int(os.getenv("GEMINI_MAX_INPUT_TOKENS", "100000"))

# Models
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "voyage-3-large")
EMBEDDING_DIMENSION = 1024
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
LLM_MODEL_STRONG = os.getenv("LLM_MODEL_STRONG", "gemini-3.6-flash")

# Chunking Parameters
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "512"))
CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", "100"))
CHUNK_MIN_TOKENS = int(os.getenv("CHUNK_MIN_TOKENS", "50"))

# Image Processing
# When True, figure plates are processed with RapidOCR (local, offline, zero API calls).
# When False, falls back to Gemini Vision for all image processing.
USE_LOCAL_OCR = os.getenv("USE_LOCAL_OCR", "true").lower() in ("true", "1", "yes")
