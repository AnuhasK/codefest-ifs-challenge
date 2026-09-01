import pytest
from pathlib import Path
import sys

# Ensure src is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import CORPUS_PATH


@pytest.fixture
def corpus_root() -> Path:
    return CORPUS_PATH
