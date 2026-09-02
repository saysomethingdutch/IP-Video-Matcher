from __future__ import annotations

import json

import numpy as np

from ipvm.retrieval_benchmark import (
    SourceIndex,
    aggregate_observations,
    characterize_query_frame,
    markdown_summary,
)


def test_retrieval_characterization_reports_rank_recall_and_margin_without_identity() -> None:
    indexes = [
        SourceIndex(
            "E001",
            np.asarray([0.0, 0.5], dtype=np.float32),
            np.asarray([[1.0, 0.0], [0.8, 0.2]], dtype=np.float32),
        ),
        SourceIndex(
            "E002",
            np.asarray([0.0], dtype=np.float32),
            np.asarray([[0.9, 0.1]], dtype=np.float32),
        ),
    ]
    observation = characterize_query_frame(
        np.asarray([1.0, 0.0], dtype=np.float32), indexes, "E001", 0.0, 0.26
    )
    assert observation["correct_source_rank"] == 1
    assert observation["hits"] == {"1": True, "8": True, "32": True, "128": True}
    assert observation["score_margin"] > 0
    aggregate = aggregate_observations([observation])
    assert aggregate["retrieval_recall_at_k"]["8"] == 1.0
    assert aggregate["median_rank"] == 1


def test_markdown_summary_accepts_standard_anonymized_report() -> None:
    observation = {
        "correct_source_rank": None,
        "score_margin": None,
        "hits": {"1": False, "8": False, "32": False, "128": False},
    }
    report = {
        "episode_count": 2,
        "sampled_interval_count": 4,
        "aggregate": aggregate_observations([observation]),
    }
    text = markdown_summary(report)
    assert "recall@128" in text
    assert "filename" in text
    json.dumps(report, allow_nan=False)
