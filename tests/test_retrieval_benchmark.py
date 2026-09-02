from __future__ import annotations

import json
import random

import numpy as np

from ipvm.retrieval_benchmark import (
    SourceIndex,
    _balanced_phase_assignments,
    _phase_bucket,
    _sample_off_grid_start,
    aggregate_observations,
    anonymized_aggregate_report,
    characterize_query_frame,
    markdown_summary,
    write_report,
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


def test_phase_stratified_sampler_is_random_off_grid_and_covers_all_buckets() -> None:
    randomizer = random.Random(7)
    starts = [
        _sample_off_grid_start(randomizer, 100.0 + index * 10, 109.0 + index * 10, index % 10)
        for index in range(20)
    ]
    assert all(min(start % 0.5, 0.5 - (start % 0.5)) >= 0.02 for start in starts)
    assert {_phase_bucket(start % 0.5) for start in starts} == {
        f"B{index + 1:02d}_{index * 0.05:.3f}-{(index + 1) * 0.05:.3f}s"
        for index in range(10)
    }


def test_phase_assignments_are_balanced_and_decoupled_from_time_order() -> None:
    assignments = _balanced_phase_assignments(random.Random(7), 20)
    assert assignments != list(range(10)) * 2
    assert {bucket: assignments.count(bucket) for bucket in range(10)} == {
        bucket: 2 for bucket in range(10)
    }


def test_anonymized_aggregate_report_removes_frame_level_observations() -> None:
    report = {
        "aggregate": aggregate_observations([]),
        "by_episode": {},
        "observations": [{"expected_source_timestamp": 12.345}],
        "decision": None,
    }
    aggregate = anonymized_aggregate_report(report)
    assert "observations" not in aggregate
    assert "expected_source_timestamp" not in json.dumps(aggregate)


def test_write_report_emits_separate_anonymous_aggregate(tmp_path) -> None:
    observation = {
        "correct_source_rank": 1,
        "score_margin": 0.25,
        "hits": {"1": True, "8": True, "32": True, "128": True},
        "expected_source_timestamp": 12.345,
    }
    report = {
        "episode_count": 1,
        "sampled_interval_count": 1,
        "aggregate": aggregate_observations([observation]),
        "observations": [observation],
        "decision": None,
    }
    full_path = tmp_path / "full.json"
    markdown_path = tmp_path / "aggregate.md"
    aggregate_path = tmp_path / "aggregate.json"
    write_report(report, full_path, markdown_path, aggregate_path)
    assert json.loads(full_path.read_text())["observations"]
    aggregate = json.loads(aggregate_path.read_text())
    assert "observations" not in aggregate
    assert "expected_source_timestamp" not in aggregate_path.read_text()
