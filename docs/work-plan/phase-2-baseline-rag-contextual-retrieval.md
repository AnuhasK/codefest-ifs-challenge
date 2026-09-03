# Phase 2 — Baseline RAG + Contextual Retrieval

**Timeline: Days 2–3**  
**Goal:** Entity extraction via gazette + spaCy, hybrid contextual prefixes, standard and contextual embeddings, working question-answering. Establish baseline scores.

---

## Prerequisites

- Phase 1 complete — all acceptance criteria met
- All chunks stored in PostgreSQL with metadata and provenance
- Figure plate data extracted via local OCR (no API dependency for plate data)
- Docker Compose running (PostgreSQL + Neo4j)
- API keys configured: **Voyage AI** (VOYAGE_API_KEY) and **Google Gemini** (GEMINI_API_KEYS — comma-separated list for key rotation)
- spaCy installed with `en_core_web_trf` model (`python -m spacy download en_core_web_trf`)

---

## Step-by-Step Implementation

### 2.1 — Embedding Provider Abstraction (`src/providers/embeddings.py`)

Create an abstract interface for embedding generation:

```python
class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of vectors."""
    
    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Embed a single query. Some models differentiate query vs document embeddings."""
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""

# Primary implementation:
class VoyageEmbeddingProvider(EmbeddingProvider):
    """Voyage AI embedding provider using voyage-3-large (1024 dims).
    Uses input_type='document' for document embeddings and 
    input_type='query' for query embeddings (asymmetric retrieval)."""
    ...

# Optional fallbacks (implement if time allows for experimentation):
class GeminiEmbeddingProvider(EmbeddingProvider): ...
```

**Important:** Voyage AI natively distinguishes between document and query embeddings via the `input_type` parameter. Use `input_type='document'` when embedding chunks and `input_type='query'` when embedding user queries. This asymmetric embedding improves retrieval accuracy.

**Test:**
- Instantiate provider → embed a sample text → verify vector dimension matches expected
- Batch embed 10 texts → verify 10 vectors returned
- Embed same text twice → verify consistent output

---

### 2.2 — Generate Standard Embeddings

For every chunk in PostgreSQL, generate embeddings using the chosen provider:

```python
def generate_embeddings(chunks: list[Chunk], provider: EmbeddingProvider) -> None:
    """Generate standard embeddings for all chunks and store in pgvector."""
```

**Implementation details:**
- Batch embeddings (provider-dependent batch size, typically 50-100)
- Update the `embedding` column in the `chunks` table
- Track progress (this will take time — ~2,000+ chunks)
- Handle rate limits gracefully (exponential backoff)
- Log any failures

**Test:**
- All chunks have non-null `embedding` column after running
- Vector dimensions are consistent across all chunks
- Verify a sample vector is valid (correct length, reasonable values)

---

### 2.3 — Gazette + spaCy Entity Extraction (`src/ingestion/entities.py`)

Build an entity gazette from the corpus file structure and run spaCy NER on all chunks. This is done **before** contextualization because entity names are used to build contextual prefixes.

**Zero LLM calls. Fully offline.**

```python
import spacy
from spacy.pipeline import EntityRuler

class ExtractedEntity:
    name: str
    entity_type: str  # Person, Faction, Place, Event, Artifact, Organization, Creature, Title
    mentions: list[str]
    chunk_id: str
    document_id: str
    source: str  # "gazette" or "spacy_ner"

def build_gazette_from_corpus(corpus_path: str) -> dict[str, str]:
    """
    Parse wiki/codex filenames to build entity dictionary.
    
    wiki/wiki_person_ser_vael.md          → Person: "Ser Vael"
    wiki/wiki_faction_ashen_vanguard.md   → Faction: "Ashen Vanguard"
    wiki/wiki_place_red_vale.md           → Place: "Red Vale"
    wiki/wiki_creature_gravemaw_wyrm.md   → Creature: "Gravemaw Wyrm"
    wiki/wiki_artifact_thrice_bound_edge.md → Artifact: "Thrice-Bound Edge"
    
    Returns: {"Ser Vael": "Person", "Ashen Vanguard": "Faction", ...}
    """

def build_spacy_pipeline(gazette: dict[str, str]) -> spacy.Language:
    """
    Build a spaCy pipeline with:
    1. EntityRuler loaded with gazette patterns (high priority, before NER)
    2. en_core_web_trf transformer NER (catches entities not in gazette)
    """

def extract_entities_from_chunk(
    chunk: Chunk,
    nlp: spacy.Language
) -> list[ExtractedEntity]:
    """Extract named entities from a single chunk using spaCy pipeline."""

def extract_entities_from_corpus(
    chunks: list[Chunk],
    corpus_path: str
) -> tuple[list[ExtractedEntity], dict[str, list[ExtractedEntity]]]:
    """
    Extract entities from all chunks using gazette + spaCy.
    
    Returns:
        - List of all extracted entities
        - Dict mapping chunk_id → list of entities in that chunk
          (needed by the contextualization step)
    """
```

**Two-pass NER strategy:**

| Pass | Method | What it catches | Priority |
|---|---|---|---|
| 1 | spaCy EntityRuler (gazette) | All entities with wiki/codex articles (~95 entities) | High — exact match |
| 2 | spaCy `en_core_web_trf` | Entities only mentioned in novels/ephemera | Lower — candidates |

**Implementation details:**
- Parse wiki filenames: strip `wiki_` prefix, split on `_`, extract type and name
- Generate case-insensitive EntityRuler patterns for each gazette entry
- Run spaCy pipeline on all chunks (fast — ~1-2 min for full corpus on CPU)
- Tag each entity with its source (`gazette` vs `spacy_ner`) for quality tracking
- Return both the entity list AND a `chunk_id → entities` mapping (used by step 2.5)

**API cost: 0 LLM calls.**

**Test (`tests/test_entity_extraction.py`):**
- Build gazette from wiki filenames → verify expected entity count (~95)
- Extract from a chunk mentioning multiple entities → verify all are captured
- Extract from a chunk with no entities → verify empty list returned
- Verify gazette entities have `source="gazette"` and spaCy entities have `source="spacy_ner"`

---

### 2.4 — Gemini API Key Rotation (`src/providers/key_rotator.py`)

Set up round-robin API key rotation for the Gemini free tier:

```python
import itertools

class GeminiKeyRotator:
    def __init__(self, api_keys: list[str]):
        self._keys = api_keys
        self._cycle = itertools.cycle(api_keys)
    
    def next_key(self) -> str:
        return next(self._cycle)
    
    @property
    def key_count(self) -> int:
        return len(self._keys)
```

**Config (`.env`):**
```
GEMINI_API_KEYS=key1,key2,key3,key4,key5
```

---

### 2.5 — Hybrid Contextual Prefix Generation (`src/ingestion/contextualization.py`)

Generate contextual prefixes using a **two-tier** approach:

```python
def build_template_prefix(
    chunk: Chunk,
    document_title: str,
    chapter: str | None,
    section_title: str | None,
    entities_in_chunk: list[ExtractedEntity]
) -> str:
    """
    Tier 1: Build a template-based prefix from metadata + entity names.
    Zero API calls. Used for the majority of chunks.
    
    Output example:
    "From The Ashen Chronicles Vol II, Chapter 7: The War Council at Red Vale.
     Mentions: Ser Vael, Ashen Vanguard, Leaden Accord."
    """

def needs_llm_prefix(
    chunk: Chunk,
    entities_in_chunk: list[ExtractedEntity],
    nlp: spacy.Language
) -> bool:
    """
    Detect chunks that need LLM-generated prefixes.
    Returns True if the chunk has 2+ subject pronouns but ≤1 named entities.
    """

def generate_llm_prefix(
    chunk: Chunk,
    document_title: str,
    chapter: str | None,
    section_title: str | None,
    surrounding_chunks: list[str],
    llm: LLMProvider
) -> str:
    """
    Tier 2: Use LLM to generate a richer prefix for pronoun-heavy chunks.
    Resolves pronouns and summarizes what the passage is about.
    """

def contextualize_all_chunks(
    chunks: list[Chunk],
    chunk_entities: dict[str, list[ExtractedEntity]],
    documents: dict[str, Document],
    nlp: spacy.Language,
    llm: LLMProvider
) -> None:
    """
    For each chunk:
    1. Check if it needs LLM prefix (pronoun-heavy, few entities)
    2. If not: build template prefix (free)
    3. If yes: generate LLM prefix (Gemini Flash call)
    4. Store prefix + original content in contextualized_content column
    """
```

**Tier 1 template (majority of chunks, zero API calls):**
```
"From {document_title}, {chapter}: {section_title}. Mentions: {entity1}, {entity2}."
```

**Tier 2 LLM prompt (pronoun-heavy chunks only, ~500-800 API calls):**
```
You are a document analysis assistant. Given a chunk of text from a larger document, 
generate a brief contextual prefix (1-3 sentences) that:

1. Names the source document and section
2. Identifies the key entities mentioned in the chunk
3. Resolves pronouns where possible (e.g., "He" → the character's name)
4. Does NOT add information that isn't present in or directly implied by the document

Document: {document_title}
Chapter: {chapter}
Section: {section_title}
Previous chunk: {previous_chunk}
Next chunk: {next_chunk}

Chunk to contextualize:
{chunk_content}

Contextual prefix (1-3 sentences):
```

**Implementation details:**
- Use Gemini Flash with key rotation for LLM prefixes
- Store the prefix in the `contextualized_content` column: `prefix + "\n\n" + original_content`
- Track statistics: how many chunks used template vs LLM prefix
- Log any LLM failures (fall back to template prefix on failure)

**Expected split:**
- ~60-70% of chunks: template prefix (have 2+ named entities)
- ~30-40% of chunks: LLM prefix (pronoun-heavy, ≤1 entities)
- Actual LLM calls: ~500-800 (with key rotation, ~11-18 min at 3 keys)

**Test (`tests/test_contextualization.py`):**
- Template prefix for a chunk with entities → verify it lists entity names
- Template prefix mentions document title and section
- `needs_llm_prefix` returns True for pronoun-heavy chunk with no entities
- `needs_llm_prefix` returns False for entity-rich chunk
- LLM prefix for pronoun-heavy chunk → verify pronouns are resolved
- Verify original chunk content is preserved in `contextualized_content`

---

### 2.6 — Generate Contextual Embeddings

Embed the contextualized chunks (prefix + original content):

```python
def generate_contextual_embeddings(chunks: list[Chunk], provider: EmbeddingProvider) -> None:
    """Generate contextual embeddings from contextualized_content and store in pgvector."""
```

**Implementation:**
- Read `contextualized_content` from each chunk
- Generate embeddings using the same provider as standard embeddings
- Store in the `contextual_embedding` column

**Test:**
- All chunks with `contextualized_content` have non-null `contextual_embedding`
- Contextual embeddings have the same dimension as standard embeddings
- Contextual embedding ≠ standard embedding for the same chunk (they should differ)

---

### 2.7 — pgvector Index Creation

Create HNSW indexes for fast approximate nearest neighbor search:

```sql
-- Standard embedding index
CREATE INDEX chunks_embedding_idx ON chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Contextual embedding index
CREATE INDEX chunks_contextual_embedding_idx ON chunks 
USING hnsw (contextual_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Test:**
- Index creation succeeds without errors
- A similarity search query returns results in reasonable time (<100ms for this corpus size)

---

### 2.8 — Dense Search (`src/retrieval/dense_search.py`)

Implement vector similarity search:

```python
def dense_search(
    query: str,
    provider: EmbeddingProvider,
    db: PostgresConnection,
    top_k: int = 50,
    use_contextual: bool = False
) -> list[SearchResult]:
    """
    Embed query → search pgvector → return ranked results.
    
    Args:
        use_contextual: If True, search contextual_embedding column.
                        If False, search embedding column.
    """
```

**SearchResult model:**
```python
class SearchResult:
    chunk_id: str
    document_id: str
    content: str
    score: float  # cosine similarity
    page: int
    section_title: str
    source_category: str
    rank: int
```

**Test:**
- Search for "Ser Vael" → verify results contain relevant chunks
- Search with `use_contextual=True` vs `use_contextual=False` → results should differ
- Top-K returns exactly K results (or fewer if fewer chunks exist)
- Results are sorted by score descending

---

### 2.9 — LLM Provider Abstraction (`src/providers/llm_provider.py`)

```python
class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        """Generate a response from the LLM."""
    
    @abstractmethod
    def generate_structured(self, prompt: str, schema: dict) -> dict:
        """Generate a structured (JSON) response."""
    
    @abstractmethod
    def describe_image(self, image_path: str, prompt: str) -> str:
        """Describe an image using vision capabilities."""

class LLMResponse:
    content: str
    tokens_used: int
    model: str

# Primary implementation:
class GeminiLLMProvider(LLMProvider):
    """Google Gemini LLM provider.
    - gemini-2.5-flash for batch processing (entity extraction, contextualization)
    - gemini-2.5-pro for answer generation
    - Vision support via Gemini's multimodal input for image description
    """
    ...
```

Implement `GeminiLLMProvider` as the primary provider. The `describe_image` method must:
- Load the image file from disk
- Send it to Gemini with the prompt
- Return the text description

**Test:**
- Generate a simple response → verify non-empty content
- Generate structured output → verify it matches the requested schema
- Describe a sample image → verify description is relevant

---

### 2.10 — Basic Answer Generation (`src/generation/context_builder.py`, `src/generation/prompts.py`)

Build a basic RAG pipeline:

```python
def build_context(evidence: list[SearchResult]) -> str:
    """Format evidence chunks into a context string for the LLM."""

def generate_answer(question: str, context: str, llm: LLMProvider) -> str:
    """Generate an answer using the LLM with evidence-grounded prompting."""
```

**System prompt (initial version):**
```
You are an expert on the Ashen Era, a fictional world. Answer questions 
using ONLY the provided evidence. 

Rules:
1. Use only the evidence provided below. Do not use any prior knowledge.
2. If the evidence is insufficient to answer, say so explicitly.
3. Cite evidence using the format [EVIDENCE_X] where X is the evidence number.
4. If sources disagree, acknowledge the contradiction.
5. Do not invent or assume facts not present in the evidence.
```

**Test:**
- Ask a simple question with relevant evidence → verify answer uses the evidence
- Ask a question with no relevant evidence → verify the system says "insufficient evidence"
- Verify citations reference actual evidence numbers

---

### 2.11 — Baseline Evaluation

Run the full baseline pipeline on the sample questions:

```python
def evaluate_baseline(questions: list[dict], search_fn, generate_fn) -> EvaluationReport:
    """
    For each question:
    1. Dense search (standard embeddings) → top K results
    2. Build context from top results
    3. Generate answer
    4. Record answer + retrieved chunks for manual review
    """
```

**Run two experiments:**

| Experiment | Search method | Description |
|---|---|---|
| Baseline A | Standard embeddings | Dense search on raw chunk embeddings |
| Baseline B | Contextual embeddings | Dense search on contextualized chunk embeddings |

**Record for each question:**
- Retrieved chunks (top 10) and their scores
- Generated answer
- Whether the answer appears correct (manual check for sample questions)
- Retrieval time
- Total time (retrieval + generation)

**Test:**
- Evaluation runs on all 19 sample questions without errors
- Results are saved to a structured output file (JSON/CSV)
- Experiment A and B produce different retrieval results for at least some questions

---

## Acceptance Criteria

> **Do NOT proceed to Phase 3 unless ALL of the following are met:**

- [ ] Gazette built from wiki/codex filenames with correct entity count (~95)
- [ ] spaCy pipeline (EntityRuler + transformer NER) runs on all chunks without crashing
- [ ] Entity extraction completes with **zero LLM calls**
- [ ] Entity-to-chunk mapping produced (chunk_id → list of entities)
- [ ] Embedding provider abstraction works with at least one provider
- [ ] **All** chunks have standard embeddings stored in pgvector
- [ ] **All** chunks have contextual prefixes generated and stored (template or LLM)
- [ ] Contextualization statistics logged: X template prefixes, Y LLM prefixes
- [ ] **All** chunks have contextual embeddings stored in pgvector
- [ ] HNSW indexes are created on both embedding columns
- [ ] Dense search returns ranked results for any query
- [ ] Contextual search returns **different** results than standard search for the same query
- [ ] LLM provider abstraction works with at least one provider (Gemini with key rotation)
- [ ] Basic answer generation produces evidence-grounded answers
- [ ] Baseline evaluation runs on all 19 sample questions
- [ ] **Two experiment results recorded:** standard vs. contextual retrieval with comparison
- [ ] All unit tests pass
- [ ] Baseline metrics are documented (even if low — they're the baseline to improve upon)

### Key Metric to Record

```
Experiment A (standard):     Recall@10 = ?, average answer quality = ?/5
Experiment B (contextual):   Recall@10 = ?, average answer quality = ?/5
Improvement:                 Δ Recall@10 = ?
```

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_entity_extraction.py` | Gazette building, spaCy NER, entity type classification |
| `tests/test_embeddings.py` | Provider abstraction, embedding generation, dimension consistency |
| `tests/test_contextualization.py` | Template prefix, LLM prefix, pronoun detection, content preservation |
| `tests/test_dense_search.py` | Vector search, ranking, top-K correctness |
| `tests/test_llm_provider.py` | LLM generation, structured output, key rotation |
| `tests/test_baseline_rag.py` | End-to-end: question → search → generate → answer |

Run all tests: `pytest tests/ -v`
