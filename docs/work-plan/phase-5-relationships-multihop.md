# Phase 5 — Relationships + Multi-Hop Retrieval

**Timeline: Days 5–6**  
**Goal:** Cross-document reasoning via graph traversal. Relationship extraction, multi-hop queries, query decomposition.

---

## Prerequisites

- Phase 4 complete — all acceptance criteria met
- Entities stored in Neo4j with chunk/document references (extracted via gazette + spaCy, zero LLM calls)
- Entity search integrated into hybrid retrieval pipeline
- Entity resolution working (basic alias merging)
- LLM provider working (Gemini — needed for relationship classification)

---

## Step-by-Step Implementation

### 5.1 — Relationship Extraction (Layer 2) (`src/ingestion/relationships.py`)

Extract relationships between co-occurring entities using **co-occurrence pre-filtering + LLM structured output**.

Since entities were already extracted by gazette + spaCy in Phase 4, the LLM only needs to **classify relationships** between known entities — not discover them.

```python
class ExtractedRelationship:
    source_entity: str  # entity name (already known from Phase 4)
    target_entity: str  # entity name (already known from Phase 4)
    relationship_type: str  # MEMBER_OF, LED, WON, PARTICIPATED_IN, etc.
    evidence_text: str  # the text span supporting this relationship
    chunk_id: str
    document_id: str
    confidence: float  # 0.0 - 1.0

def get_entity_pairs_from_chunk(
    chunk: Chunk,
    entity_mentions: dict[str, list[str]]  # chunk_id → list of entity names
) -> list[tuple[str, str]]:
    """
    Co-occurrence pre-filter: identify entity pairs in the same chunk.
    Only chunks with 2+ entities produce pairs.
    This reduces LLM calls by ~30-40% (chunks with 0-1 entities are skipped).
    """

def extract_relationships_from_chunk(
    chunk: Chunk,
    entities_in_chunk: list[Entity],
    llm: LLMProvider
) -> list[ExtractedRelationship]:
    """
    Given a chunk and the entities that appear in it (from Phase 4 spaCy extraction),
    classify relationships between those entities using LLM with structured output.
    
    The LLM receives pre-extracted entity names (not raw text) and is constrained
    to output only relationships from the predefined type set.
    """
```

**LLM prompt for relationship classification (with structured output constraint):**

```
You are given a text passage and a list of named entities that appear in it.
These entities have already been identified. Your job is to classify the
relationships between them.

Entities found in this text: {entity_list}

Text: {chunk_content}

For each relationship between the entities, provide:
- source: the entity that is the subject
- target: the entity that is the object  
- type: MUST be one of [MEMBER_OF, LED, PARTICIPATED_IN, OCCURRED_AT, LOCATED_IN, 
         WON, LOST, CONTROLS, HOLDS, ALLIED_WITH, OPPOSED, CREATED, 
         FOUNDED, DESTROYED, RELATED_TO]
- evidence: the exact text span that supports this relationship
- confidence: your confidence in this extraction (0.0 - 1.0)

Only extract relationships that are explicitly stated or strongly implied in the text.
Do NOT infer relationships that require external knowledge.

Respond in JSON format:
{
  "relationships": [
    {"source": "...", "target": "...", "type": "...", "evidence": "...", "confidence": 0.9},
    ...
  ]
}
```

**Pydantic schema for structured output (enforced via Gemini JSON mode):**
```python
from pydantic import BaseModel, Field
from enum import Enum

class RelationshipType(str, Enum):
    MEMBER_OF = "MEMBER_OF"
    LED = "LED"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    OCCURRED_AT = "OCCURRED_AT"
    LOCATED_IN = "LOCATED_IN"
    WON = "WON"
    LOST = "LOST"
    CONTROLS = "CONTROLS"
    HOLDS = "HOLDS"
    ALLIED_WITH = "ALLIED_WITH"
    OPPOSED = "OPPOSED"
    CREATED = "CREATED"
    FOUNDED = "FOUNDED"
    DESTROYED = "DESTROYED"
    RELATED_TO = "RELATED_TO"

class RelationshipOutput(BaseModel):
    source: str
    target: str
    type: RelationshipType
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)

class RelationshipExtractionResult(BaseModel):
    relationships: list[RelationshipOutput]
```

This Pydantic schema **forces** the LLM to only emit valid relationship types, preventing hallucinated categories.

**Implementation strategy:**
1. For each chunk, get the entities already extracted in Phase 4 (from spaCy)
2. **Co-occurrence pre-filter:** Only process chunks that contain **2 or more** entities (otherwise no relationship to extract)
3. Feed pre-extracted entity names + chunk text into Gemini with structured output
4. Use Pydantic schema validation to enforce output format
5. Filter out low-confidence relationships (< 0.5)
6. Batch processing with rate limit handling (round-robin API keys)

**Optimization:** Only ~60-70% of chunks will contain 2+ entities. This reduces LLM calls to ~500-800 (vs ~2,000+ if processing all chunks).

**Test (`tests/test_relationship_extraction.py`):**
- Chunk mentioning "Ederon Fellgard" and "Ashen Vanguard" → extracts MEMBER_OF relationship
- Chunk mentioning a person and a place → extracts LOCATED_IN or OCCURRED_AT
- Chunk with only one entity → no relationships extracted (pre-filtered out)
- Relationship types are from the allowed set (enforced by Pydantic schema)
- Confidence values are between 0.0 and 1.0

---

### 5.2 — Store Relationships in Neo4j

```python
def store_relationships_in_neo4j(
    relationships: list[ExtractedRelationship],
    graph: KnowledgeGraph
) -> None:
    """
    For each relationship:
    1. Resolve source and target entity names to Neo4j entity IDs
    2. Create a typed relationship edge with evidence metadata
    """
```

**Cypher for relationship creation:**
```cypher
MATCH (source:Entity {name: $source_name})
MATCH (target:Entity {name: $target_name})
MERGE (source)-[r:MEMBER_OF]->(target)
SET r.evidence_chunk_id = $chunk_id,
    r.evidence_text = $evidence_text,
    r.source_document_id = $document_id,
    r.confidence = $confidence
```

**Note:** Relationship type in Cypher must be a string constant, so you'll need dynamic relationship creation:
```cypher
CALL apoc.create.relationship(source, $rel_type, {properties}, target) YIELD rel
```
Or use separate MERGE statements per relationship type.

**Test:**
- Relationships stored in Neo4j with correct properties
- Query `MATCH (p:Person)-[:MEMBER_OF]->(f:Faction) RETURN p, f` returns results
- Evidence metadata (chunk_id, document_id) is present on edges

---

### 5.3 — Entity Co-occurrence Graph (Complementary)

Build a lightweight co-occurrence graph alongside LLM-extracted relationships:

```python
def build_cooccurrence_graph(
    chunks: list[Chunk],
    entity_mentions: dict[str, list[str]],  # chunk_id → list of entity names
    graph: KnowledgeGraph
) -> None:
    """
    For entities that appear in the same chunk, create CO_OCCURS_WITH edges.
    Weight = number of chunks where both entities co-occur.
    """
```

**Why?** Co-occurrence is:
- Fast to compute (no LLM needed)
- Catches relationships the LLM might miss
- Useful as a fallback traversal path

**Test:**
- Two entities appearing in 5 chunks together → CO_OCCURS_WITH edge with weight 5
- Entity appearing alone → no co-occurrence edges
- Co-occurrence edges are queryable alongside typed relationships

---

### 5.4 — Multi-Hop Traversal (`src/knowledge/multihop.py`)

Implement graph-based multi-hop evidence gathering:

```python
class HopResult:
    hop_number: int
    entity: Entity
    relationship_type: str
    evidence_chunk_ids: list[str]
    path_description: str  # human-readable path description

def multi_hop_search(
    start_entities: list[str],
    graph: KnowledgeGraph,
    max_hops: int = 3,
    relationship_types: list[str] = None
) -> list[HopResult]:
    """
    Starting from given entities, traverse the knowledge graph to find 
    connected information.
    
    Returns a list of hop results, each with the entity found and 
    the evidence supporting the connection.
    """
```

**Cypher for multi-hop:**
```cypher
// 2-hop: Person → Faction → War
MATCH path = (p:Entity {name: $start_name})-[r1]->(mid)-[r2]->(target)
WHERE p.type = 'Person' AND target.type IN ['Event', 'War', 'Accord']
RETURN nodes(path) AS nodes, relationships(path) AS rels

// Variable-length: 1-3 hops
MATCH path = (p:Entity {name: $start_name})-[*1..3]->(target)
RETURN path, length(path) AS hops
ORDER BY hops ASC
LIMIT 20
```

**Important bounds:**
```
Maximum hops: 3 (configurable)
Maximum paths returned: 20
Maximum entities per hop: 10
```

**Test (`tests/test_multihop.py`):**
- 1-hop: Person → Faction (direct relationship) → verify correct faction returned
- 2-hop: Person → Faction → War → verify correct war returned
- 3-hop: verify traversal doesn't exceed max hops
- Non-existent entity → empty results
- Verify evidence chunk IDs are returned for each hop

---

### 5.5 — Query Decomposition (`src/retrieval/query_analyzer.py` — extend)

Decompose complex questions into sub-questions:

```python
def decompose_query(
    query: str,
    llm: LLMProvider
) -> list[str]:
    """
    Decompose a complex question into simpler sub-questions.
    
    Example:
    "Which war was won by the faction containing Isolde Mournvale?"
    →
    ["Which faction does Isolde Mournvale belong to?",
     "Which war did [faction from Q1] win?"]
    """
```

**LLM prompt:**
```
Decompose the following question into simpler sub-questions that can be 
answered independently. Each sub-question should be self-contained.
If the question is already simple, return it unchanged.
Maximum 5 sub-questions.

Question: {query}

Respond in JSON:
{"sub_questions": ["...", "..."]}
```

**Test (`tests/test_query_decomposition.py`):**
- Complex multi-hop question → decomposed into 2-3 sub-questions
- Simple question → returned unchanged
- Maximum 5 sub-questions

---

### 5.6 — QueryState Tracking (`src/models/query.py`)

Implement explicit state tracking for multi-hop queries:

```python
class QueryState:
    original_query: str
    query_type: str  # simple, multi_hop
    sub_questions: list[str]
    
    # Discovery tracking
    identified_entities: list[str]
    discovered_entities: list[str]  # entities found during traversal
    discovered_relationships: list[dict]
    
    # Evidence tracking
    retrieved_evidence: list[SearchResult]
    evidence_per_hop: dict[int, list[SearchResult]]
    
    # Quality tracking
    evidence_coverage: float  # 0.0 - 1.0
    missing_information: list[str]
    contradictions: list[dict]
    
    # Bounds
    iteration_count: int
    max_iterations: int  # default 3
```

**Test:**
- QueryState tracks entities discovered across hops
- Evidence per hop is correctly recorded
- Iteration count respects max_iterations

---

### 5.7 — Evidence Sufficiency Scoring (`src/knowledge/evidence.py`)

Score whether we have enough evidence to answer:

```python
class SufficiencyScore:
    level: str  # HIGH, MEDIUM, LOW, INSUFFICIENT
    coverage: float  # 0.0 - 1.0
    source_count: int
    unique_documents: int
    missing: list[str]  # what's still missing
    reasoning: str

def assess_evidence_sufficiency(
    query: str,
    query_state: QueryState,
    llm: LLMProvider
) -> SufficiencyScore:
    """
    Assess whether the gathered evidence is sufficient to answer the query.
    
    Factors:
    - Retrieval relevance (are the retrieved chunks actually relevant?)
    - Question coverage (do we have evidence for all parts of the question?)
    - Source diversity (evidence from multiple documents, not just one)
    - Contradiction presence (do sources agree or disagree?)
    """
```

**Decision tree:**
```
sufficient? ──YES──→ proceed to answer generation
      │
      NO
      │
      ▼
  iteration < max? ──YES──→ reformulate query, search again
      │
      NO
      │
      ▼
  answer with LOW/INSUFFICIENT status
  (acknowledge gaps in the answer)
```

**Test (`tests/test_evidence_sufficiency.py`):**
- Query with strong evidence from 3+ documents → HIGH
- Query with evidence from 1 document only → MEDIUM or LOW
- Query with no relevant evidence → INSUFFICIENT
- Sufficiency drives retry decision correctly

---

### 5.8 — Integrated Multi-Hop Pipeline

Update the retrieval orchestrator to support multi-hop:

```python
def retrieve_with_multihop(
    query: str,
    query_analysis: QueryAnalysis,
    config: RetrievalConfig,
    graph: KnowledgeGraph
) -> QueryState:
    """
    1. Standard hybrid retrieval (BM25 + dense + entity → RRF → reranker)
    2. If query is multi-hop:
       a. Decompose into sub-questions
       b. For each sub-question, run retrieval
       c. Use discovered entities to traverse Neo4j graph
       d. Gather evidence from each hop
       e. Assess sufficiency
       f. If insufficient and iterations remain, reformulate and search again
    3. Return QueryState with all gathered evidence
    """
```

**Test:**
- Single-hop question → standard retrieval, no graph traversal
- Multi-hop question → sub-questions generated, graph traversed, multi-document evidence gathered
- Bounded iterations → never exceeds max_iterations

---

### 5.9 — Multi-Hop Evaluation

Run evaluation focused on 1B sample questions:

| Experiment | Description |
|---|---|
| Experiment 6 (from Phase 4) | Hybrid + entity search |
| Experiment 7 | Hybrid + entity + relationships + multi-hop |

Focus on the 7 Track 1B questions from `sample_questions.json`:
- "Which accord was ultimately won by the faction of which Ederon Fellgard is a member?"
- "Which individual was a member of the faction that ultimately won the War of Drowned Light?"
- "Which war did Ravena Stormwell's own faction ultimately win?"
- "Whose dominion encompasses the lair of the Gravemaw Wyrm?"
- etc.

These are precisely the questions that require multi-hop reasoning.

---

## Acceptance Criteria

> **Do NOT proceed to Phase 6 unless ALL of the following are met:**

- [ ] Relationship extraction runs on chunks with 2+ entities
- [ ] Relationships stored in Neo4j with evidence metadata
- [ ] Co-occurrence graph built alongside typed relationships
- [ ] Multi-hop traversal returns paths up to 3 hops
- [ ] Multi-hop traversal returns evidence chunk IDs for each hop
- [ ] Query decomposition breaks complex questions into sub-questions
- [ ] QueryState correctly tracks entities, evidence, and iterations across hops
- [ ] Evidence sufficiency scoring returns meaningful levels (HIGH/MEDIUM/LOW/INSUFFICIENT)
- [ ] Bounded retry works (reformulates and searches again when evidence is insufficient)
- [ ] At least **3 of 7** Track 1B sample questions show improved results with multi-hop
- [ ] Multi-hop pipeline respects iteration bounds (never exceeds max_iterations)
- [ ] All unit tests pass
- [ ] Experiment 7 results documented with comparison to Experiment 6

### Key Metrics to Record

```
Relationships extracted:      ~X total, Y types
Multi-hop success rate:       Z/7 1B questions correctly answered
Experiment 7 vs 6:            1B answer quality Δ
Average hops per 1B question: ~N
```

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_relationship_extraction.py` | LLM relationship extraction, type validation |
| `tests/test_multihop.py` | Graph traversal, hop bounds, evidence gathering |
| `tests/test_query_decomposition.py` | Sub-question generation, bounds |
| `tests/test_evidence_sufficiency.py` | Sufficiency scoring, retry logic |
| `tests/test_query_state.py` | State tracking across hops |

Run all tests: `pytest tests/ -v`
