import pytest
from src.evaluation.metrics import (
    compute_recall_at_k,
    compute_joint_recall_at_k,
    compute_mrr,
    compute_retrieval_metrics,
    MULTIHOP_BENCHMARK_TARGETS,
)


def test_compute_joint_recall_both_hops_present():
    """Verify Joint Recall@K is 1.0 when both hops are in top-K."""
    items = [
        {"chunk_id": "c1", "document_title": "ederon_fellgard.md", "content": "Ederon Fellgard is a member of the Iron-Ring Cartel."},
        {"chunk_id": "c2", "document_title": "the_leaden_accord.md", "content": "The Leaden Accord declared victor was the Iron-Ring Cartel in 342 AS."},
        {"chunk_id": "c3", "document_title": "other.md", "content": "Unrelated passage."},
    ]
    hop_targets = [
        ["Ederon Fellgard", "Iron-Ring Cartel"],
        ["Leaden Accord", "victor"],
    ]

    # At k=1: only Hop 1 is present -> Joint Recall is 0.0
    assert compute_joint_recall_at_k(items, hop_targets, k=1) == 0.0
    # At k=2: both Hop 1 and Hop 2 are present -> Joint Recall is 1.0
    assert compute_joint_recall_at_k(items, hop_targets, k=2) == 1.0
    # Standard Hit@K returns 1.0 at k=1 (ceiling effect)
    assert compute_recall_at_k(items, ["Ederon Fellgard"], k=1) == 1.0


def test_compute_joint_recall_one_hop_missing():
    """Verify Joint Recall@K is 0.0 if only Hop 1 is present and Hop 2 is missing."""
    items = [
        {"chunk_id": "c1", "document_title": "ederon_fellgard.md", "content": "Ederon Fellgard is a member of the Iron-Ring Cartel."},
        {"chunk_id": "c2", "document_title": "some_novel.md", "content": "He rode into the hills."},
    ]
    hop_targets = [
        ["Ederon Fellgard", "Iron-Ring Cartel"],
        ["Leaden Accord", "victor"],
    ]
    assert compute_joint_recall_at_k(items, hop_targets, k=2) == 0.0


def test_compute_joint_recall_requires_distinct_chunks():
    """Verify that a single chunk cannot satisfy both hops when require_distinct is True."""
    # A single chunk mentioning both
    items = [
        {"chunk_id": "c1", "document_title": "summary.md", "content": "Ederon Fellgard of Iron-Ring Cartel won Leaden Accord as victor."},
    ]
    hop_targets = [
        ["Ederon Fellgard", "Iron-Ring Cartel"],
        ["Leaden Accord", "victor"],
    ]
    # Distinct chunks required: with only 1 item in top_slice, joint recall is 0.0
    assert compute_joint_recall_at_k(items, hop_targets, k=1, require_distinct=True) == 0.0
    # Without distinct required: 1.0
    assert compute_joint_recall_at_k(items, hop_targets, k=1, require_distinct=False) == 1.0


def test_compute_joint_recall_single_hop_fallback():
    """Verify that a 1-hop target behaves identically to Hit@K."""
    items = [
        {"chunk_id": "c1", "document_title": "Weeping Lurker", "content": "Rating 10."},
    ]
    hop_targets = [["Weeping Lurker", "10"]]
    assert compute_joint_recall_at_k(items, hop_targets, k=1) == 1.0


def test_multihop_benchmark_targets_structure():
    """Verify all 7 Track 1B questions have 2-hop benchmark target configurations."""
    track_1b_qids = ["1b_007", "1b_006", "1b_022", "1b_013", "1b_005", "1b_009", "1b_003"]
    for qid in track_1b_qids:
        assert qid in MULTIHOP_BENCHMARK_TARGETS
        hops = MULTIHOP_BENCHMARK_TARGETS[qid]
        assert len(hops) == 2
        assert len(hops[0]) >= 2
        assert len(hops[1]) >= 2
