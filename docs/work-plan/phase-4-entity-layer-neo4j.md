# Phase 4 — Entity Layer + Neo4j

**Timeline: Days 4–5**  
**Goal:** Entities extracted and stored in Neo4j, entity-aware retrieval integrated into the hybrid pipeline.

---

## Prerequisites

- Phase 3 complete — all acceptance criteria met
- Hybrid retrieval pipeline working (BM25 + dense + contextual + RRF + reranker)
- Neo4j running via Docker Compose (should be up since Phase 1)
- spaCy installed with `en_core_web_trf` model (`python -m spacy download en_core_web_trf`)
- LLM provider working (needed later for relationship extraction in Phase 5, not for entity extraction)

---

## Step-by-Step Implementation

### 4.1 — Neo4j Connection Layer (`src/database/neo4j_db.py`)

Implement a Neo4j connection manager:

```python
class Neo4jConnection:
    def __init__(self, uri: str, user: str, password: str):
        """Initialize Neo4j driver."""
    
    def execute_query(self, cypher: str, params: dict = None) -> list[dict]:
        """Execute a Cypher query and return results."""
    
    def execute_write(self, cypher: str, params: dict = None) -> None:
        """Execute a write Cypher query."""
    
    def close(self) -> None:
        """Close the driver connection."""
```

**Test:**
- Connect to Neo4j → verify connection is alive
- Run a simple query → verify results returned
- Write and read back a test node → verify data persistence

---

### 4.2 — Neo4j Graph Operations (`src/knowledge/graph.py`)

Higher-level graph operations:

```python
class KnowledgeGraph:
    def __init__(self, neo4j: Neo4jConnection):
        """Initialize with Neo4j connection."""
    
    def create_entity(self, entity: Entity) -> None:
        """Create or merge an entity node."""
    
    def create_relationship(self, source_id: str, target_id: str, 
                           rel_type: str, properties: dict = None) -> None:
        """Create a relationship between two entities."""
    
    def link_entity_to_chunk(self, entity_id: str, chunk_id: str, 
                             document_id: str) -> None:
        """Create MENTIONED_IN edge from entity to chunk/document."""
    
    def find_entity(self, name: str) -> Entity | None:
        """Find entity by name or alias."""
    
    def find_entities_by_type(self, entity_type: str) -> list[Entity]:
        """Find all entities of a given type."""
    
    def get_entity_chunks(self, entity_id: str) -> list[str]:
        """Get all chunk IDs where an entity is mentioned."""
    
    def get_related_entities(self, entity_id: str, 
                             relationship_types: list[str] = None) -> list[Entity]:
        """Get entities related to a given entity."""
```

**Test:**
- Create an entity → find it by name
- Create two entities with a relationship → verify relationship exists
- Link entity to chunk → verify chunk IDs returned by `get_entity_chunks`

---

### 4.3 — Entity Extraction (Layer 1) — Gazette + spaCy (`src/ingestion/entities.py`)

Extract named entities from every chunk using a **hybrid offline approach** — zero LLM calls:

```python
import spacy
from spacy.pipeline import EntityRuler

class ExtractedEntity:
    name: str
    entity_type: str  # Person, Faction, Place, Event, Artifact, Organization, Creature, Title
    mentions: list[str]  # text spans where this entity appears
    chunk_id: str
    document_id: str
    source: str  # "gazette" or "spacy_ner" — tracks how the entity was found

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
    1. EntityRuler loaded with gazette patterns (high priority)
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
) -> list[ExtractedEntity]:
    """
    Extract entities from all chunks using gazette + spaCy.
    No LLM calls needed — fully offline.
    
    1. Build gazette from wiki/codex filenames
    2. Build spaCy pipeline with EntityRuler + transformer NER
    3. Process all chunks through the pipeline
    4. Return extracted entities with source tracking
    """
```

**Two-pass NER strategy:**

| Pass | Method | What it catches | Priority |
|---|---|---|---|
| 1 | spaCy EntityRuler (gazette) | All entities with wiki/codex articles (~95 entities) | High — exact match |
| 2 | spaCy `en_core_web_trf` | Entities only mentioned in novels/ephemera, no wiki article | Lower — candidate entities |

**Gazette pattern example:**
```python
patterns = [
    {"label": "PERSON", "pattern": "Ser Vael"},
    {"label": "PERSON", "pattern": [{"LOWER": "ser"}, {"LOWER": "vael"}]},
    {"label": "FACTION", "pattern": "Ashen Vanguard"},
    {"label": "FACTION", "pattern": [{"LOWER": "ashen"}, {"LOWER": "vanguard"}]},
    # ... generated from wiki filenames
]
```

**Implementation details:**
- Parse wiki filenames: strip `wiki_` prefix, split on `_`, extract type and name
- Generate case-insensitive EntityRuler patterns for each gazette entry
- Run spaCy pipeline on all chunks (fast — ~1-2 min for full corpus on CPU)
- Tag each entity with its source (`gazette` vs `spacy_ner`) for quality tracking
- Gazette entities are trusted; spaCy NER entities are candidates (may need filtering)

**Expected entity counts (rough estimates):**
- ~95 entities from gazette (wiki filenames)
- ~100-200 unique Person entities total (gazette + spaCy NER)
- ~20-40 Faction/Organization entities
- ~30-50 Place entities
- ~15-30 Event entities
- ~20-40 Artifact entities
- ~10-15 Creature entities

**API cost: 0 LLM calls.**

**Test (`tests/test_entity_extraction.py`):**
- Build gazette from wiki filenames → verify expected entity count (~95)
- Extract from a wiki article about a specific character → verify the character name is extracted as Person
- Extract from a chunk mentioning multiple entities → verify all are captured
- Extract from a chunk with no entities → verify empty list returned
- Verify gazette entities have `source="gazette"` and spaCy entities have `source="spacy_ner"`
- Verify extracted entity types are from the allowed set

---

### 4.4 — Entity Deduplication & Merging

After extracting from all chunks, merge duplicate entities:

```python
def deduplicate_entities(entities: list[ExtractedEntity]) -> list[Entity]:
    """
    Merge entities that refer to the same thing.
    
    Strategy:
    1. Exact name match → merge
    2. Case-insensitive match → merge
    3. Substring match (e.g., "Ser Vael" and "Vael") → candidate for merge (manual review or LLM verification)
    """
```

**Entity model for Neo4j:**
```python
class Entity:
    id: str  # UUID
    name: str  # canonical name
    aliases: list[str]  # all known names/spellings
    entity_type: str
    source_documents: list[str]  # document_ids where this entity appears
    source_chunks: list[str]  # chunk_ids where this entity appears
    mention_count: int
```

**Test:**
- "Isolde Mournvale" appearing in 5 chunks → merged into 1 entity with 5 chunk references
- "the Ashen Vanguard" and "Ashen Vanguard" → merged (case-insensitive / article stripping)

---

### 4.5 — Store Entities in Neo4j

Persist all deduplicated entities:

```python
def store_entities_in_neo4j(entities: list[Entity], graph: KnowledgeGraph) -> None:
    """
    For each entity:
    1. Create entity node in Neo4j
    2. Create MENTIONED_IN edges to chunk/document nodes
    """
```

**Cypher for entity creation:**
```cypher
MERGE (e:Entity {id: $id})
SET e.name = $name,
    e.type = $type,
    e.aliases = $aliases,
    e.mention_count = $mention_count

// For each chunk reference:
MERGE (c:Chunk {id: $chunk_id})
MERGE (e)-[:MENTIONED_IN]->(c)

// For each document reference:
MERGE (d:Document {id: $doc_id})
MERGE (e)-[:APPEARS_IN]->(d)
```

**Test:**
- All entities stored in Neo4j
- Entity nodes have correct properties (name, type, aliases)
- MENTIONED_IN edges exist for each chunk reference
- Querying by entity type returns expected counts

---

### 4.6 — Basic Entity Resolution (`src/knowledge/entity_resolution.py`)

Handle alias resolution — different names for the same entity:

```python
def resolve_entity_aliases(
    entities: list[Entity],
    graph: KnowledgeGraph,
    llm: LLMProvider | None = None
) -> dict[str, str]:
    """
    Attempt to resolve aliases. Returns a mapping of alias → canonical entity ID.
    
    Strategy:
    1. Exact match after normalization (lowercase, strip articles/titles)
    2. Wiki article filename match (wiki article title often IS the canonical name)
    3. LLM-assisted resolution for ambiguous cases (optional, stretch)
    """
```

**Practical approach for this corpus:**
- Wiki filenames are the best source of canonical names (e.g., `ederon_fellgard.md` → "Ederon Fellgard" is canonical)
- Titles like "the Oathless" or "the Ashen" are epithets, not separate entities
- Create `SAME_AS` edges in Neo4j between alias entities and their canonical entity

**Test:**
- "The Ashen Vanguard" resolves to same entity as "Ashen Vanguard"
- Wiki article names match extracted entity names
- Resolution mapping is stored and queryable

---

### 4.7 — Entity Search (`src/retrieval/entity_search.py`)

Search based on entities found in the query:

```python
def entity_search(
    query_entities: list[str],
    graph: KnowledgeGraph,
    db: PostgresConnection,
    top_k: int = 50
) -> list[SearchResult]:
    """
    1. For each entity name in the query, find matching entity in Neo4j
    2. Get all chunk IDs where that entity is mentioned
    3. Fetch those chunks from PostgreSQL
    4. Return as SearchResults (scored by mention relevance)
    """
```

**Scoring for entity search:**
- Chunks mentioning the exact query entity → high score
- Chunks mentioning entities related to the query entity → medium score
- Score can be based on mention count, entity centrality, or a simple binary

**Test (`tests/test_entity_search.py`):**
- Query mentioning "Ederon Fellgard" → entity search returns chunks containing this name
- Query mentioning a non-existent entity → empty results
- Entity search returns chunks that BM25/dense search might miss (different ranking)

---

### 4.8 — Integrate Entity Search into Hybrid Pipeline

Update the retrieval orchestrator to include entity search:

```python
# In orchestrator.py, update retrieve():
def retrieve(query, query_analysis, config):
    results = []
    
    # Existing
    if config.enable_bm25:
        results.append(bm25_search(query, ...))
    results.append(dense_search(query, ...))  # standard
    if config.enable_contextual:
        results.append(dense_search(query, ..., use_contextual=True))
    
    # NEW: Entity search
    if config.enable_entity_search and query_analysis.entities_mentioned:
        results.append(entity_search(query_analysis.entities_mentioned, ...))
    
    # Fuse + rerank
    fused = rrf_fusion(results)
    reranked = rerank_candidates(query, fused, ...)
    return reranked
```

**Test:**
- Full pipeline with entity search enabled → verify entity results are included in fusion
- Query with entity names → verify entity search contributes results
- Query without entity names → verify entity search is skipped

---

### 4.9 — Entity Layer Evaluation

Run evaluation comparing Phase 3 hybrid vs Phase 4 hybrid + entities:

| Experiment | Description |
|---|---|
| Experiment 5 (from Phase 3) | Full hybrid (BM25 + dense + contextual + RRF + reranker) |
| Experiment 6 | Full hybrid + entity search |

Focus especially on the 1B sample questions that mention specific characters/factions.

---

## Acceptance Criteria

> **Do NOT proceed to Phase 5 unless ALL of the following are met:**

- [ ] Neo4j connection works and entities can be created/queried
- [ ] Gazette built from wiki/codex filenames with correct entity count (~95)
- [ ] spaCy pipeline (EntityRuler + transformer NER) runs on all chunks without crashing
- [ ] Entity extraction completes with **zero LLM calls**
- [ ] Entities are stored in Neo4j with correct types and chunk/document references
- [ ] Entity source tracking distinguishes gazette vs spaCy NER entities
- [ ] Entity deduplication merges obvious duplicates (exact + case-insensitive matches)
- [ ] Basic entity resolution resolves common aliases
- [ ] Entity search returns relevant chunks for queries mentioning entity names
- [ ] Entity search is integrated into the hybrid retrieval pipeline via RRF fusion
- [ ] Hybrid + entity retrieval shows improvement on entity-heavy 1B questions
- [ ] Entity count statistics are logged (total entities, per type, per source, per document)
- [ ] All unit tests pass: `pytest tests/test_entity_extraction.py tests/test_entity_search.py tests/test_neo4j.py`
- [ ] Experiment 6 results documented with comparison to Experiment 5

### Key Metrics to Record

```
Entities extracted:           ~X total, Y Person, Z Faction, ...
Entity resolution merges:     N entities merged
Experiment 6 vs 5:            Recall@10 = ?, Δ = ?
1B question improvement:      X/7 questions improved
```

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_neo4j.py` | Connection, CRUD operations, query execution |
| `tests/test_entity_extraction.py` | LLM entity extraction, type classification, edge cases |
| `tests/test_entity_resolution.py` | Deduplication, alias resolution, merge logic |
| `tests/test_entity_search.py` | Entity-based retrieval, integration with PostgreSQL |

Run all tests: `pytest tests/ -v`
