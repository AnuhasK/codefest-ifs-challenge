from src.evaluation.metrics import (
    compute_recall_at_k,
    compute_mrr,
    compute_retrieval_metrics,
)
from src.evaluation.validation import (
    validate_citation_grounding,
    validate_evidence_support,
)


def test_compute_recall_and_mrr():
    items = [
        {"chunk_id": "c1", "document_title": "Chronicles", "content": "Some generic text."},
        {"chunk_id": "c2", "document_title": "House Morvain", "content": "Golden keys banner."},
        {"chunk_id": "c3", "document_title": "Weeping Lurker", "content": "Rating 10."},
    ]
    targets = ["House Morvain", "banner"]

    # Target is at index 1 (rank 2)
    assert compute_recall_at_k(items, targets, k=1) == 0.0
    assert compute_recall_at_k(items, targets, k=2) == 1.0
    assert compute_recall_at_k(items, targets, k=5) == 1.0
    assert compute_mrr(items, targets) == 0.5  # 1/2


def test_compute_retrieval_metrics_for_known_qid():
    items = [
        {"chunk_id": "c1", "document_title": "House Morvain", "content": "Crossed golden keys on banner."},
    ]
    metrics = compute_retrieval_metrics(items, qid="1a_v12")
    assert metrics["recall_at_1"] == 1.0
    assert metrics["recall_at_10"] == 1.0
    assert metrics["mrr"] == 1.0


def test_validate_citation_grounding():
    retrieved = [
        {"chunk_id": "c1", "document_title": "House Morvain", "content": "Banner description"},
    ]
    citations = [
        {
            "evidence_id": "EVIDENCE_1",
            "document_title": "House Morvain",
            "excerpt": "A banner with golden keys",
        }
    ]
    res = validate_citation_grounding(citations, retrieved)
    assert res["citation_grounded"] is True
    assert res["valid_citations"] == 1

    # Missing excerpt
    bad_cit = [
        {
            "evidence_id": "EVIDENCE_1",
            "document_title": "House Morvain",
            "excerpt": "",
        }
    ]
    bad_res = validate_citation_grounding(bad_cit, retrieved)
    assert bad_res["citation_grounded"] is False


def test_validate_evidence_support():
    citations = [
        {
            "evidence_id": "EVIDENCE_1",
            "document_title": "House Morvain",
            "excerpt": "The banner of House Morvain features two crossed golden keys.",
        }
    ]
    ans = "The banner of House Morvain features crossed golden keys [EVIDENCE_1]."
    support = validate_evidence_support(ans, citations, qid="1a_v12")
    assert support["evidence_supported"] is True

    # Answer asserts a number not in the excerpt
    num_ans = "The recorded strength is 99999 souls [EVIDENCE_1]."
    num_support = validate_evidence_support(num_ans, citations, qid="1a_009")
    assert num_support["evidence_supported"] is False
