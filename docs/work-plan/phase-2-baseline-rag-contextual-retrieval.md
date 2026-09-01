# Phase 2 — Baseline RAG + Contextual Retrieval

**Timeline: Days 2–3**  
**Goal:** Working question-answering with both standard and contextual embeddings. Establish baseline scores.

---

## Prerequisites

- Phase 1 complete — all acceptance criteria met
- All chunks stored in PostgreSQL with metadata and provenance
- Docker Compose running (PostgreSQL + Neo4j)
- API keys configured: **Voyage AI** (VOYAGE_API_KEY) and **Google Gemini** (GEMINI_API_KEY)

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

### 2.3 — Contextual Prefix Generation (`src/ingestion/contextualization.py`)

For every chunk, generate a contextual prefix using the LLM:

```python
def generate_contextual_prefix(
    chunk: Chunk,
    document_title: str,
    chapter: str | None,
    section_title: str | None,
    surrounding_chunks: list[str]  # previous + next chunk content
) -> str:
    """Use LLM to generate a 1-3 sentence contextual prefix for a chunk."""
```

**Prompt template:**

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
- Use a cheaper/faster LLM for this (e.g., Gemini Flash, GPT-4o-mini) — it's a simple task
- Batch processing with rate limit handling
- Store the prefix in the `contextualized_content` column: `prefix + "\n\n" + original_content`
- Track costs and token usage

**Test:**
- Generate prefix for a sample chunk → verify it mentions the document title
- Verify the prefix does not exceed 3 sentences
- Verify the original chunk content is preserved in `contextualized_content`
- Test with a chunk containing pronouns → verify pronouns are resolved

---

### 2.4 — Generate Contextual Embeddings

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

### 2.5 — pgvector Index Creation

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

### 2.6 — Dense Search (`src/retrieval/dense_search.py`)

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

### 2.7 — LLM Provider Abstraction (`src/providers/llm_provider.py`)

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

### 2.8 — Basic Answer Generation (`src/generation/context_builder.py`, `src/generation/prompts.py`)

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

### 2.9 — Baseline Evaluation

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

- [ ] Embedding provider abstraction works with at least one provider
- [ ] **All** chunks have standard embeddings stored in pgvector
- [ ] **All** chunks have contextual prefixes generated and stored
- [ ] **All** chunks have contextual embeddings stored in pgvector
- [ ] HNSW indexes are created on both embedding columns
- [ ] Dense search returns ranked results for any query
- [ ] Contextual search returns **different** results than standard search for the same query
- [ ] LLM provider abstraction works with at least one provider
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
| `tests/test_embeddings.py` | Provider abstraction, embedding generation, dimension consistency |
| `tests/test_contextualization.py` | Prefix generation, pronoun resolution, content preservation |
| `tests/test_dense_search.py` | Vector search, ranking, top-K correctness |
| `tests/test_llm_provider.py` | LLM generation, structured output |
| `tests/test_baseline_rag.py` | End-to-end: question → search → generate → answer |

Run all tests: `pytest tests/ -v`
