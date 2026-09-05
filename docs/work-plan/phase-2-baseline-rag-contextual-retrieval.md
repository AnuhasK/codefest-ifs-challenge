# Phase 2 — Baseline RAG + Contextual Retrieval

**Timeline: Days 2–3**  
**Goal:** Corpus-aware entity extraction (gazette + rules + targeted Gemini), hybrid contextual prefixes, standard and contextual embeddings, working question-answering. Establish baseline scores.

> **Status Note (as of current work):** Embeddings (Steps 2.2 + 2.6), contextualization (Step 2.5), key rotation (Step 2.4), and the entity extraction scaffold (Step 2.3) are substantially built. The pipeline runs end-to-end. Phase 2 is currently in progress.

---

## Prerequisites

- Phase 1 complete — all acceptance criteria met
- All chunks stored in PostgreSQL with metadata and provenance
- Docker Compose running (PostgreSQL + Neo4j)
- API keys configured: **Google Gemini** (GEMINI_API_KEYS — comma-separated list for key rotation)
- spaCy installed with `en_core_web_sm` model (`python -m spacy download en_core_web_sm`)
- **Note:** `en_core_web_trf` is NOT used — see architecture-v1.md §10 for rationale

---

## Entity Extraction Strategy Overview

The Ashen Era is a **closed fictional corpus**. Generic pretrained NER models (`en_core_web_trf`, `en_core_web_sm` NER) are trained on real-world text (news, Wikipedia) and produce unreliable or wrong labels for fictional vocabulary.

**The gazette is the primary NER layer.** spaCy is a helper for POS tagging, pronoun detection, and noun chunk extraction only.

### Entity Ontology (Fictional-World-Appropriate)

```
PERSON, FACTION, PLACE, EVENT, ARTIFACT, ORGANIZATION, CREATURE,
TITLE, DYNASTY, DEITY, CONCEPT, DOCUMENT, BUILDING, MILITARY_UNIT, UNKNOWN
```

---

## Step-by-Step Implementation

### 2.1 — Embedding Provider Abstraction (`src/providers/embeddings.py`)

```python
class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str], batch_size: int = 16) -> list[list[float]]: ...
    @abstractmethod
    def embed_query(self, query: str) -> list[float]: ...
    @property
    @abstractmethod
    def dimension(self) -> int: ...

class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google Gemini embedding provider (gemini-embedding-001, 1024 dims).
    Uses EmbeddingDiskCache (SQLite at data/embeddings_cache.sqlite) to persist
    vectors and avoid redundant API calls on restarts.
    
    Rate limit handling:
    - batch_size=5 (~1,750 tokens/request, well under 30k TPM limit)
    - 1.0s pause between batches
    - 429 = temporary per-minute limit: sleep 15s + retry. Do NOT mark key exhausted.
    - Key rotation: next_key() round-robins across all configured API keys
    """
    ...

class VoyageEmbeddingProvider(EmbeddingProvider):
    """Voyage AI provider (voyage-3-large, 1024 dims). Keep for future use."""
    ...
```

**Disk Cache (critical for rate-limited free tier):**
Every batch committed to `data/embeddings_cache.sqlite` immediately. On any restart, already-computed embeddings load from disk — zero API calls.

**Test:**
- Embed a sample text ? verify vector dimension is 1024
- Batch embed 10 texts ? 10 vectors returned
- Embed same text twice ? second call loads from disk cache (0 API calls)

---

### 2.2 — Generate Standard Embeddings

```python
def generate_embeddings(chunks: list[Chunk], provider: EmbeddingProvider) -> None:
    """Generate standard embeddings for all chunks and store in pgvector."""
```

- Adaptive batch size: 5 for Gemini, 16 for Voyage (auto-detected)
- Update `embedding` column in `chunks` table
- Track progress every 25 chunks
- Disk cache means restarts resume without re-embedding processed chunks

**Test:** All chunks have non-null 1024-dim `embedding` after running.

---

### 2.3 — Corpus-Aware Entity Extraction (`src/ingestion/entities.py`)

#### Step 1a: Build Gazette from Corpus Structure (0 API calls)

```python
def build_gazette_from_corpus(corpus_path: str) -> dict[str, str]:
    """
    Parse wiki/codex filenames to build entity dictionary.
    wiki/wiki_person_ser_vael.md         ? PERSON: "Ser Vael"
    wiki/wiki_faction_ashen_vanguard.md  ? FACTION: "Ashen Vanguard"
    wiki/wiki_place_red_vale.md          ? PLACE: "Red Vale"
    wiki/wiki_creature_gravemaw_wyrm.md  ? CREATURE: "Gravemaw Wyrm"
    Returns: {"Ser Vael": "PERSON", "Ashen Vanguard": "FACTION", ...}
    """
```

Expected gazette: ~95 entities from wiki filenames + codex entries.

#### Step 1b: spaCy EntityRuler + Rule-Based Candidates (0 API calls)

```python
def build_spacy_pipeline(gazette: dict[str, str]) -> spacy.Language:
    """
    spaCy en_core_web_sm with EntityRuler (gazette patterns, high priority).
    IMPORTANT: The en_core_web_sm NER classifier is NOT used for entity labels.
    Only tokenizer, POS tagger, and noun chunk extractor are used.
    """

def extract_capitalized_candidates(chunk_text: str, nlp, gazette_entities: set[str]) -> list[str]:
    """
    Extract capitalized 1-3 word noun chunks NOT in gazette.
    Apply title prefix patterns: "Ser ", "Lord ", "Lady ", "High ", "the ".
    Tag as UNKNOWN (candidate for Step 1c).
    """
```

**Two-pass pipeline:**

| Pass | Method | Output |
|---|---|---|
| 1 | spaCy EntityRuler (gazette) | Typed entity, confidence=1.0, source="gazette" |
| 2 | Capitalized phrase rules | UNKNOWN candidates, source="rules" |

Example:
```
Input: "Ser Vael of the Ashen Vanguard rode to Red Vale, seeking Lord Drovenath."
Pass 1: PERSON:"Ser Vael", FACTION:"Ashen Vanguard", PLACE:"Red Vale" (gazette)
Pass 2: UNKNOWN:"Lord Drovenath" (title prefix "Lord" detected)
```

#### Step 1c: Targeted Gemini Entity Pass (~80–150 calls)

Triggered only for specific chunks:

```python
def needs_llm_entity_pass(chunk: Chunk, unknown_candidates: list[str]) -> bool:
    """
    Returns True if:
    - chunk has 4+ UNKNOWN candidates, OR
    - chunk is from an ephemera document, OR
    - chunk has title-prefix candidates (Ser/Lord/Lady) not in gazette
    """
```

**Gemini prompt (structured JSON output):**
```
This is a chunk from a fictional fantasy corpus called "Ashen Era".
Candidate names detected: {candidates}

Classify each using ONLY:
PERSON, FACTION, PLACE, EVENT, ARTIFACT, ORGANIZATION, CREATURE, 
TITLE, DYNASTY, DEITY, CONCEPT, DOCUMENT, BUILDING, MILITARY_UNIT, UNKNOWN

Also identify any other named entities missed. Do not invent entities.

Return JSON: {"entities": [{"mention":"...", "canonical_name":"...", "type":"...", "confidence":0.0}]}

Chunk text: {chunk_text}
```

- Entities from this step: `source="gemini_ner"`, include `confidence` score

#### Step 1d: Alias Resolution (0 API calls)

After all extraction passes, resolve name variants before writing to Neo4j:

```python
def resolve_aliases(
    all_entities: list[ExtractedEntity],
    embeddings_cache: EmbeddingDiskCache
) -> dict[str, str]:  # surface_form -> canonical_entity_id
    """
    1. Exact match        -> same entity
    2. Prefix/suffix strip ("Lord ", " the Oathless") -> compare core name
    3. Levenshtein distance <= 2 -> likely same (typo/variant)
    4. Embedding cosine similarity >= 0.92 -> possible alias
       (uses already-computed chunk embeddings, zero extra API calls)
    5. Remaining ambiguous pairs -> flagged UNKNOWN_ALIAS
    """
```

Output example:
```
"Lord Vaelith"             -> uuid-vaelith (canonical: "Vaelith")
"Lord V."                  -> uuid-vaelith
"the Lord of Mournthrone"  -> uuid-vaelith
```

Neo4j entity nodes carry `aliases` property listing all surface forms.

#### Entity Data Model

```python
class ExtractedEntity:
    name: str           # canonical name
    entity_type: str    # from the 15-type ontology
    mentions: list[str] # all surface forms seen
    chunk_id: str
    document_id: str
    source: str         # "gazette", "rules", or "gemini_ner"
    confidence: float   # 1.0 for gazette, 0.0-1.0 for Gemini

def extract_entities_from_corpus(
    chunks: list[Chunk],
    corpus_path: str,
    llm: LLMProvider
) -> tuple[list[ExtractedEntity], dict[str, list[ExtractedEntity]]]:
    """
    Returns:
    - All extracted entities (deduplicated, aliases resolved)
    - chunk_id -> list of entities mapping (used by contextualization)
    """
```

**API cost summary:**
- Step 1a: 0 calls | Step 1b: 0 calls | Step 1c: ~80-150 calls | Step 1d: 0 calls

**Test (`tests/test_entity_extraction.py`):**
- Gazette builds ~95 entities with correct types
- "Ashen Vanguard" in text ? FACTION, confidence=1.0, source="gazette"
- "Lord Drovenath" (not in gazette) ? UNKNOWN candidate from rules
- `needs_llm_entity_pass` True for ephemera chunks; False for entity-rich chunks
- Alias resolution: "Lord Vael" and "Ser Vael" ? same entity ID (if appropriate)
- `en_core_web_trf` is NOT imported anywhere in the codebase

---

### 2.4 — Gemini API Key Rotation (`src/providers/key_rotator.py`)

```python
class GeminiKeyRotator:
    def __init__(self, api_keys: list[str], max_rpm: int = 8, max_daily: int = 1000):
        ...
    def next_key(self) -> str:
        """Return next available key, respecting rate limits."""
    def mark_exhausted(self, key: str, reason: str = "") -> None:
        """Mark key as exhausted. Use ONLY for genuine daily quota errors, NOT 429s."""
```

Config: `GEMINI_API_KEYS=key1,key2,key3,key4`

**429 handling rule:** A `429 RESOURCE_EXHAUSTED` for per-minute TPM is temporary. Sleep 15s + retry the same key. Do NOT call `mark_exhausted()`.

---

### 2.5 — Hybrid Contextual Prefix Generation (`src/ingestion/contextualization.py`)

```python
def build_template_prefix(
    chunk: Chunk,
    document_title: str,
    chapter: str | None,
    section_title: str | None,
    entities_in_chunk: list[ExtractedEntity]
) -> str:
    """
    Tier 1: Template prefix from metadata + gazette entity names.
    Zero API calls. Used for the vast majority of chunks.
    Output: "From The Ashen Chronicles Vol II, Chapter 7: The War Council at Red Vale.
             Mentions: Ser Vael, Ashen Vanguard, Leaden Accord."
    """

def needs_llm_prefix(
    chunk: Chunk,
    entities_in_chunk: list[ExtractedEntity],
    nlp: spacy.Language
) -> bool:
    """
    Returns True if chunk has 2+ subject pronouns AND <=1 named entities.
    Uses spaCy POS tagger for pronoun detection (legitimate use, not NER).
    """
```

**Expected split (actual measured result from this corpus):**
- ~99.9% template prefix (gazette coverage is high — most chunks have entities)
- ~0.1% LLM prefix (~3 calls)
- Original estimate of ~500-800 LLM calls was for a corpus without a gazette

**Test:** Template prefix lists entity names; `needs_llm_prefix` True only for genuinely pronoun-heavy chunks.

---

### 2.6 — Generate Contextual Embeddings

```python
def generate_contextual_embeddings(chunks: list[Chunk], provider: EmbeddingProvider) -> None:
    """Embed contextualized_content (prefix + original) and store in contextual_embedding."""
```

Disk cache handles deduplication — unchanged chunks won't re-embed.

**Test:** All chunks have 1024-dim `contextual_embedding`; contextual != standard embedding.

---

### 2.7 — pgvector Index Creation

```sql
CREATE INDEX chunks_embedding_idx ON chunks 
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX chunks_contextual_embedding_idx ON chunks 
  USING hnsw (contextual_embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX assets_embedding_idx ON assets
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
```

---

### 2.8 — Dense Search (`src/retrieval/dense_search.py`)

```python
def dense_search(
    query: str,
    provider: EmbeddingProvider,
    db: PostgresConnection,
    top_k: int = 50,
    use_contextual: bool = False
) -> list[SearchResult]: ...
```

---

### 2.9 — LLM Provider Abstraction (`src/providers/llm_provider.py`)

```python
class GeminiLLMProvider(LLMProvider):
    """
    - gemini-2.5-flash for batch processing (entity extraction, contextualization)
    - gemini-2.5-pro for answer generation
    - Vision support for image description
    """
```

---

### 2.10 — Basic Answer Generation

System prompt enforcing evidence-only answers with citations:
```
You are an expert on the Ashen Era. Answer using ONLY the provided evidence.
Do not use prior knowledge. Cite as [EVIDENCE_X]. If insufficient, say so.
```

---

### 2.11 — Baseline Evaluation

| Experiment | Search | Description |
|---|---|---|
| Baseline A | Standard dense | Raw chunk embeddings |
| Baseline B | Contextual dense | Contextualized chunk embeddings |

---

## Acceptance Criteria

> **Do NOT proceed to Phase 3 unless ALL of the following are met:**

- [ ] Gazette built from wiki/codex filenames (~95 entities, correct types)
- [ ] `en_core_web_sm` EntityRuler gazette pass runs on all chunks
- [ ] Capitalized phrase rules produce UNKNOWN candidate lists for off-gazette mentions
- [ ] `needs_llm_entity_pass` correctly identifies ephemera + unknown-heavy chunks
- [ ] Targeted Gemini entity pass classifies entities with the 15-type Ashen Era ontology
- [ ] Alias resolution produces surface_form ? canonical entity mapping
- [ ] Entity-to-chunk mapping produced (chunk_id ? list of entities)
- [ ] **`en_core_web_trf` is NOT installed or imported anywhere in the codebase**
- [ ] Gemini embedding provider works (1024 dims)
- [ ] Disk cache (`data/embeddings_cache.sqlite`) created and persists embeddings across runs
- [ ] 429 errors handled as temporary rate limits (15s pause + retry), NOT key exhaustion
- [ ] All chunks have standard embeddings (1024 dims) in pgvector
- [ ] All chunks have contextual prefixes (template or LLM) in `contextualized_content`
- [ ] Contextualization stats logged: X template prefixes, Y LLM prefixes
- [ ] All chunks have contextual embeddings in pgvector
- [ ] HNSW indexes on chunks.embedding, chunks.contextual_embedding, assets.embedding
- [ ] Dense search (standard + contextual) returns ranked results
- [ ] LLM provider works with key rotation
- [ ] Baseline evaluation runs on all sample questions
- [ ] Two experiment results (standard vs contextual) documented with comparison
- [ ] All unit tests pass

### Expected LLM Call Counts (Phase 2)

| Task | Calls |
|---|---|
| Gazette + rules entity extraction | **0** |
| Targeted Gemini entity pass | **~80–150** |
| Alias resolution | **0** |
| Contextual prefixes (template) | **0** |
| Contextual prefixes (LLM) | **~3** |
| Standard embeddings (2,117 chunks) | **~424** |
| Contextual embeddings (2,117 chunks) | **~424** |
| Image asset embeddings (70 assets) | **~14** |
| **Phase 2 total** | **~945–1,015** |

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_entity_extraction.py` | Gazette building, EntityRuler, rule candidates, targeted Gemini pass, alias resolution |
| `tests/test_embeddings.py` | Provider abstraction, embedding generation, disk cache |
| `tests/test_contextualization.py` | Template prefix, LLM prefix, pronoun detection |
| `tests/test_dense_search.py` | Vector search, ranking, top-K |
| `tests/test_llm_provider.py` | LLM generation, structured output, key rotation |
| `tests/test_baseline_rag.py` | End-to-end: question ? search ? generate ? answer |

Run all tests: `pytest tests/ -v`
