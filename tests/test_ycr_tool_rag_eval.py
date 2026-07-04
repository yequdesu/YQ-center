"""Golden checks for YCR capability Tool RAG ranking helpers."""

from __future__ import annotations

from yequ.ycr.capability_gateway import _rrf_fusion


def test_tool_rag_golden_rankings_meet_quality_bar() -> None:
    cases = [
        {
            "query": "disk",
            "dense": [
                ("directory.archive_artifact", 0.52),
                ("disk.detail", 0.50),
                ("eventlog.export_artifact", 0.48),
            ],
            "sparse": [("disk.detail", 8.0), ("metrics.snapshot", 2.0)],
            "expected": {"disk.detail"},
            "bad": {"directory.archive_artifact", "eventlog.export_artifact"},
        },
        {
            "query": "screen capture",
            "dense": [("screen.capture", 0.61), ("metrics.snapshot", 0.50)],
            "sparse": [("screen.capture", 9.0)],
            "expected": {"screen.capture"},
            "bad": {"metrics.snapshot"},
        },
        {
            "query": "transfer receive",
            "dense": [("transfer.croc.receive", 0.63), ("metrics.snapshot", 0.42)],
            "sparse": [("transfer.croc.receive", 7.5)],
            "expected": {"transfer.croc.receive"},
            "bad": {"metrics.snapshot"},
        },
        {
            "query": "command",
            "dense": [("transfer.croc.receive", 0.43), ("file.stat", 0.42)],
            "sparse": [],
            "expected": set(),
            "bad": {"transfer.croc.receive", "file.stat"},
        },
    ]

    metrics = _evaluate_cases(cases)

    assert metrics["top1_accuracy"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    assert metrics["bad_hit_rate"] == 0.0


def test_rrf_fusion_prefers_sparse_exact_tool_over_dense_noise() -> None:
    dense_rows = [
        ("directory.archive_artifact", 0.52),
        ("disk.detail", 0.50),
        ("eventlog.export_artifact", 0.48),
    ]
    sparse_rows = [
        ("disk.detail", 8.0),
        ("metrics.snapshot", 2.0),
    ]

    ranked = _rrf_fusion(dense_rows, sparse_rows, top_k=10)

    assert ranked[0][0] == "disk.detail"
    assert ranked[0][2] == {"dense_rank": 2, "sparse_rank": 1}
    assert "directory.archive_artifact" not in [item[0] for item in ranked]


def test_rrf_fusion_keeps_semantic_result_when_sparse_is_empty() -> None:
    ranked = _rrf_fusion(
        [("screen.capture", 0.61), ("metrics.snapshot", 0.50)],
        [],
        top_k=10,
    )

    assert [item[0] for item in ranked] == ["screen.capture"]


def test_rrf_fusion_rejects_low_confidence_dense_only_results() -> None:
    ranked = _rrf_fusion(
        [("transfer.croc.receive", 0.43), ("file.stat", 0.42)],
        [],
        top_k=10,
    )

    assert ranked == []


def _evaluate_cases(cases: list[dict[str, object]]) -> dict[str, float]:
    top1_hits = 0
    recall_hits = 0
    expected_count = 0
    bad_hits = 0
    returned_count = 0
    non_empty_cases = 0
    for case in cases:
        ranked = _rrf_fusion(
            case["dense"],  # type: ignore[arg-type]
            case["sparse"],  # type: ignore[arg-type]
            top_k=10,
        )
        returned = [item[0] for item in ranked[:5]]
        expected = case["expected"]  # type: ignore[assignment]
        bad = case["bad"]  # type: ignore[assignment]
        if expected:
            non_empty_cases += 1
            top1_hits += int(bool(returned) and returned[0] in expected)
            recall_hits += len(set(returned) & expected)
            expected_count += len(expected)
        bad_hits += len(set(returned) & bad)
        returned_count += len(returned)
    return {
        "top1_accuracy": top1_hits / max(1, non_empty_cases),
        "recall_at_5": recall_hits / max(1, expected_count),
        "bad_hit_rate": bad_hits / max(1, returned_count),
    }
