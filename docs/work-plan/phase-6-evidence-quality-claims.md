# Phase 6 — Evidence Quality + Claims

**Timeline: Days 6–7**  
**Goal:** Conflict detection, source reliability, answer verification, deterministic citations. Layer 3 claim extraction (stretch).

---

## Prerequisites

- Phase 5 complete — all acceptance criteria met
- Multi-hop retrieval working with QueryState tracking
- Evidence sufficiency scoring functional
- Relationship graph populated in Neo4j

---

## Step-by-Step Implementation

### 6.1 — Evidence Manager (`src/knowledge/evidence.py` — extend)

Create a comprehensive evidence management module:

```python
class EvidenceRecord:
    id: str  # EVIDENCE_001, EVIDENCE_002, ...
    chunk_id: str
    document_id: str
    content: str  # original chunk text
    source_category: str  # chronicles, wiki, codex, ephemera
    source_subtype: str  # ballad, trial_transcript, decree, field_report, etc.
    page: int
    section_title: str
    document_title: str
    source_file: str

class EvidenceManager:
    def __init__(self):
        self.evidence: list[EvidenceRecord] = []
        self._counter = 0
    
    def register_evidence(self, search_result: SearchResult) -> EvidenceRecord:
        """Register a search result as an evidence record with a unique ID."""
    
    def deduplicate(self) -> None:
        """Remove duplicate evidence (same chunk appearing from multiple retrieval paths)."""
    
    def group_by_entity(self, entities: list[str]) -> dict[str, list[EvidenceRecord]]:
        """Group evidence by which entity it relates to."""
    
    def group_by_document(self) -> dict[str, list[EvidenceRecord]]:
        """Group evidence by source document."""
    
    def get_source_diversity(self) -> dict:
        """Count how many unique documents, categories, and subtypes are represented."""
    
    def get_evidence_by_id(self, evidence_id: str) -> EvidenceRecord | None:
        """Look up evidence by its deterministic ID."""
```

**Test (`tests/test_evidence_manager.py`):**
- Register 10 evidence records → verify unique IDs assigned (EVIDENCE_001 through EVIDENCE_010)
- Deduplicate removes same chunk appearing twice
- Group by entity returns correct groupings
- Source diversity correctly counts unique documents/categories

---

### 6.2 — Source Classification

Classify evidence by source characteristics (NOT binary reliability):

```python
class SourceCharacteristics:
    source_type: str  # codex, chronicle, wiki, ephemera
    document_subtype: str  # determined from filename patterns
    claim_strength: str  # assertion, allegation, rumor, observation, decree
    narrative_voice: str  # official, personal, third_party, in_character
    temporal_reliability: str  # contemporary, retrospective, mythological

def classify_source(evidence: EvidenceRecord) -> SourceCharacteristics:
    """
    Classify evidence source based on document metadata and subtype.
    
    Filename patterns:
    - "ballad_concerning_*"        → ephemera, rumor, in_character
    - "trial_transcript_*"         → ephemera, allegation, official
    - "sermon_concerning_*"        → ephemera, assertion, in_character
    - "field_report_*"             → ephemera, observation, official
    - "letter_concerning_*"        → ephemera, personal, personal
    - "decree_concerning_*"        → ephemera, decree, official
    - "interrogation_record_*"     → ephemera, allegation, official
    - "petition_concerning_*"      → ephemera, assertion, personal
    - "contract_concerning_*"      → ephemera, assertion, official
    - "auction_catalogue_*"        → ephemera, observation, official
    - "muster_roll_*"             → ephemera, assertion, official
    - "quartermaster_ledger_*"    → ephemera, observation, official
    - Wiki articles               → wiki, assertion, third_party
    - Codex entries               → codex, assertion, official
    - Chronicles                  → chronicle, assertion, narrative
    """
```

**Key principle:** This classification informs the LLM about source characteristics but does NOT automatically determine truthfulness. A ballad might contain true facts; a decree might be propaganda.

**Test (`tests/test_source_classification.py`):**
- "ballad_concerning_vael.docx" → classified as ephemera, rumor, in_character
- "trial_transcript_concerning_fenspire.pdf" → classified as ephemera, allegation, official
- Wiki article → classified as wiki, assertion, third_party
- Codex entry → classified as codex, assertion, official

---

### 6.3 — Conflict Detection

Detect when evidence sources disagree:

```python
class Conflict:
    claim_summary: str  # what the conflict is about
    supporting_evidence: list[EvidenceRecord]  # evidence supporting one view
    opposing_evidence: list[EvidenceRecord]  # evidence supporting the other
    conflict_type: str  # contradiction, qualification, uncertainty

def detect_conflicts(
    evidence: list[EvidenceRecord],
    llm: LLMProvider
) -> list[Conflict]:
    """
    Analyze a set of evidence records for conflicts.
    
    Strategy:
    1. Group evidence by topic/entity
    2. Within each group, ask the LLM to identify contradictions
    3. Return structured conflict descriptions
    """
```

**LLM prompt for conflict detection:**
```
Given the following evidence passages about the same topic, identify any 
contradictions, disagreements, or qualifications between them.

Evidence:
{evidence_texts_with_ids}

For each conflict found, provide:
- claim: what the conflict is about
- supporting: list of evidence IDs that support one view
- opposing: list of evidence IDs that support the opposing view
- type: "contradiction" (direct disagreement), "qualification" (one source adds nuance), 
        or "uncertainty" (sources are ambiguous)

If there are no conflicts, return an empty list.

Respond in JSON:
{"conflicts": [...]}
```

**Test (`tests/test_conflict_detection.py`):**
- Evidence agreeing → no conflicts detected
- Evidence with clear contradiction → conflict detected with correct IDs
- Evidence with nuanced disagreement → detected as "qualification"
- Empty evidence → no conflicts

---

### 6.4 — Claim Extraction (Layer 3 — Stretch Goal) (`src/ingestion/claims.py`)

If time allows, extract structured claims:

```python
class Claim:
    id: str
    subject: str
    predicate: str  # accused_of, committed, founded, destroyed, etc.
    object: str
    temporal_context: str | None  # "during the Ashen War", "in 356 AS"
    source_evidence_id: str
    source_type: str
    claim_strength: str  # assertion, allegation, rumor

def extract_claims_from_chunk(
    chunk: Chunk,
    entities_in_chunk: list[Entity],
    llm: LLMProvider
) -> list[Claim]:
    """Extract factual claims from a chunk as structured assertions."""
```

**Critical constraint:** The predicate must preserve the **epistemological status** of the claim:
- `accused_of` ≠ `committed`
- `rumored_to_have` ≠ `did`
- `claimed_to_be` ≠ `is`

**Test:**
- Text saying "Vael was accused of betrayal" → predicate is "accused_of", NOT "committed"
- Text saying "Vael founded the order" → predicate is "founded"
- Verify claim strength matches source type

---

### 6.5 — Deterministic Citation Architecture (`src/generation/citations.py`)

Implement the citation resolution system:

```python
class CitationResolver:
    def __init__(self, evidence_manager: EvidenceManager):
        self.evidence_manager = evidence_manager
    
    def resolve_citations(self, answer_text: str) -> str:
        """
        Replace [EVIDENCE_X] references in the answer with 
        actual document/page citations.
        
        Input:  "Ser Vael was a member [EVIDENCE_001] of the Ashen Vanguard."
        Output: "Ser Vael was a member [Royal Annals, p.84] of the Ashen Vanguard."
        """
    
    def validate_citations(self, answer_text: str) -> list[CitationIssue]:
        """
        Check that all [EVIDENCE_X] references in the answer 
        correspond to actual evidence records.
        
        Returns list of issues (missing evidence, invalid IDs, etc.)
        """
    
    def get_citation_details(self, evidence_id: str) -> dict:
        """
        Return full citation details for a given evidence ID:
        - Document title
        - Page number
        - Section title
        - Source category
        - Original text snippet
        """

class CitationIssue:
    evidence_id: str
    issue_type: str  # missing, invalid, unsupported
    description: str
```

**Test (`tests/test_citations.py`):**
- "[EVIDENCE_001]" in answer → resolves to actual document name + page
- Invalid "[EVIDENCE_999]" → flagged as missing
- All citations in a sample answer are valid
- Citation details include document title, page, section, source text

---

### 6.6 — Answer Verification (`src/generation/verification.py`)

Verify generated answers against evidence:

```python
class VerificationResult:
    is_verified: bool
    claim_checks: list[ClaimCheck]
    citation_issues: list[CitationIssue]
    conflict_acknowledgements: list[str]
    unsupported_claims: list[str]

class ClaimCheck:
    claim_text: str
    has_evidence: bool
    evidence_ids: list[str]
    verdict: str  # supported, unsupported, partially_supported

def verify_answer(
    answer: str,
    evidence_manager: EvidenceManager,
    llm: LLMProvider
) -> VerificationResult:
    """
    Post-generation verification:
    1. Extract claims from the generated answer
    2. For each claim, check if supporting evidence exists
    3. Validate all citations reference real evidence
    4. Check if conflicts are acknowledged when they exist
    5. Flag any unsupported claims
    """
```

**LLM prompt for verification:**
```
Given this answer and the evidence it was based on, verify each factual 
claim in the answer.

Answer: {answer}

Evidence provided:
{evidence_texts_with_ids}

For each factual claim in the answer:
1. State the claim
2. Is it supported by the evidence? (yes/no/partially)
3. Which evidence IDs support it?
4. Are there any claims in the answer NOT supported by the evidence?

Respond in JSON format.
```

**Test (`tests/test_verification.py`):**
- Answer with all claims supported → verified
- Answer with an unsupported claim → flagged
- Answer missing conflict acknowledgement → flagged
- Answer with invalid citation → flagged

---

### 6.7 — Context Builder (Enhanced) (`src/generation/context_builder.py` — extend)

Build a structured evidence context for the LLM:

```python
def build_evidence_context(
    evidence_manager: EvidenceManager,
    conflicts: list[Conflict],
    sufficiency: SufficiencyScore
) -> str:
    """
    Build a structured context that includes:
    1. Evidence records with IDs and source metadata
    2. Source characteristic notes
    3. Detected conflicts
    4. Sufficiency status
    5. Instructions for handling conflicts
    """
```

**Context format sent to LLM:**
```
## Evidence

[EVIDENCE_001] (Source: Codex Vaeloria I, p.84 — official codex)
"Ser Vael was a founding member of the Ashen Vanguard, established in 312 AS."

[EVIDENCE_002] (Source: Trial Transcript, p.7 — allegation, official record)
"The accused, known as Ser Vael, was charged with betraying the Ashen Vanguard."

[EVIDENCE_003] (Source: Ballad concerning the Ashen Vanguard — rumor, in-character)
"And Vael the True never wavered in his oath..."

## Conflicts Detected
- EVIDENCE_002 alleges betrayal; EVIDENCE_003 claims loyalty. These sources disagree.

## Source Notes
- EVIDENCE_001 is from an official codex (high reliability)
- EVIDENCE_002 is from a trial transcript (formal allegation, not proven fact)
- EVIDENCE_003 is from a ballad (artistic, potentially romanticized)

## Evidence Status: MEDIUM (conflicting sources)
```

**Test:**
- Context includes all evidence with IDs
- Conflicts are listed in the context
- Source characteristics are noted
- Sufficiency status is included

---

### 6.8 — End-to-End Answer Pipeline

Wire everything together into the complete answer pipeline:

```python
def answer_question(query: str) -> FinalAnswer:
    """
    Complete pipeline:
    1. Analyze query
    2. Retrieve with multi-hop (Phase 5)
    3. Register evidence with EvidenceManager
    4. Classify sources
    5. Detect conflicts
    6. Assess sufficiency
    7. Build context
    8. Generate answer (LLM)
    9. Verify answer
    10. Resolve citations
    11. Return FinalAnswer
    """

class FinalAnswer:
    answer_text: str  # with resolved citations
    evidence: list[EvidenceRecord]
    citations: list[dict]  # resolved citation details
    conflicts: list[Conflict]
    evidence_status: str  # HIGH, MEDIUM, LOW, INSUFFICIENT
    verification_result: VerificationResult
    query_trace: dict  # observability trace
```

**Test (`tests/test_answer_pipeline.py`):**
- Simple question → complete FinalAnswer with citations
- Multi-hop question → FinalAnswer with multi-document evidence
- Question with conflicting evidence → conflicts acknowledged in answer
- Question with no evidence → INSUFFICIENT status, refusal to fabricate

---

## Acceptance Criteria

> **Do NOT proceed to Phase 7 unless ALL of the following are met:**

- [ ] Evidence Manager assigns unique deterministic IDs to all evidence
- [ ] Deduplication correctly removes duplicate evidence
- [ ] Source classification correctly identifies document subtypes from filenames
- [ ] Conflict detection identifies contradictions between evidence sources
- [ ] Deterministic citations resolve [EVIDENCE_X] to real document/page references
- [ ] Citation validation catches invalid/missing evidence references
- [ ] Answer verification checks claims against evidence
- [ ] Structured evidence context includes source characteristics and conflicts
- [ ] End-to-end answer pipeline runs: query → retrieval → evidence → LLM → verification → answer
- [ ] FinalAnswer includes: answer text, citations, conflicts, evidence status, verification result
- [ ] At least one Track 1B question produces an answer that correctly notes conflicting sources
- [ ] All unit tests pass
- [ ] (Stretch) Claim extraction produces structured assertions with correct predicates

### Key Metrics to Record

```
Citation accuracy:           X% of citations point to correct documents
Conflict detection rate:     Y conflicts detected across test questions
Verification pass rate:      Z% of answers pass verification
Unsupported claim rate:      W% of claims flagged as unsupported
```

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_evidence_manager.py` | ID assignment, deduplication, grouping |
| `tests/test_source_classification.py` | Filename-based classification |
| `tests/test_conflict_detection.py` | Contradiction identification |
| `tests/test_citations.py` | Citation resolution, validation |
| `tests/test_verification.py` | Answer verification, claim checking |
| `tests/test_answer_pipeline.py` | End-to-end pipeline |

Run all tests: `pytest tests/ -v`
