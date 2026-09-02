from __future__ import annotations

import json

from ipvm.benchmarking import evaluate_segments
from ipvm.models import SegmentMatch


def test_benchmark_metrics_measure_recall_episode_and_boundaries() -> None:
    truth = [{
        "accused_start": 0.0,
        "accused_end": 4.0,
        "source_episode": "EP02",
        "source_start": 10.0,
        "source_end": 14.0,
    }]
    predicted = [SegmentMatch(
        accused_start=0.0,
        accused_end=4.0,
        source_episode="EP02",
        source_start=10.5,
        source_end=14.5,
        speed_ratio=1.0,
        visual_score=0.98,
        audio_score=None,
        temporal_score=1.0,
        match_score=0.98,
    )]
    metrics = evaluate_segments(truth, predicted)
    assert metrics["source_aware_recall_at_1s"] == 1.0
    assert metrics["episode_recall"] == 1.0
    assert metrics["source_boundary_mae_seconds"] == 0.5


def test_benchmark_wrong_episode_is_not_recalled() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 4.0, "source_episode": "EP02",
        "source_start": 10.0, "source_end": 14.0,
    }]
    predicted = [SegmentMatch(
        0.0, 4.0, "EP01", 10.0, 14.0, 1.0, 0.98, None, 1.0, 0.98
    )]
    metrics = evaluate_segments(truth, predicted)
    assert metrics["source_aware_recall_at_1s"] == 0.0
    assert metrics["episode_recall"] == 0.0
    assert metrics["segment_precision_at_1s"] == 0.0
    assert metrics["episode_correctness"] == 0.0


def test_false_positive_segments_and_seconds_reduce_precision() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 4.0, "source_episode": "EP02",
        "source_start": 10.0, "source_end": 14.0, "speed_ratio": 1.0,
    }]
    predictions = [
        SegmentMatch(0.0, 4.0, "EP02", 10.0, 14.0, 1.0, 0.99, None, 1.0, 0.99),
        SegmentMatch(6.0, 10.0, "EP01", 20.0, 24.0, 1.0, 0.99, None, 1.0, 0.99),
    ]
    metrics = evaluate_segments(truth, predictions)
    assert metrics["source_aware_recall_at_1s"] == 1.0
    assert metrics["segment_precision_at_1s"] == 0.5
    assert metrics["false_positive_segments"] == 1
    assert metrics["false_positive_seconds"] == 4.0


def test_missed_truth_has_null_mae_and_standard_json() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 4.0, "source_episode": "EP02",
        "source_start": 10.0, "source_end": 14.0, "speed_ratio": 1.0,
    }]
    metrics = evaluate_segments(truth, [])
    assert metrics["source_boundary_mae_seconds"] is None
    assert metrics["boundary_missed_segments"] == 1
    json.dumps(metrics, allow_nan=False)


def test_duplicate_prediction_is_false_positive_under_one_to_one_assignment() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 4.0, "source_episode": "EP02",
        "source_start": 10.0, "source_end": 14.0, "speed_ratio": 1.0,
    }]
    prediction = SegmentMatch(
        0.0, 4.0, "EP02", 10.0, 14.0, 1.0, 0.99, None, 1.0, 0.99,
        candidate_tier="GREEN",
    )
    metrics = evaluate_segments(truth, [prediction, prediction])
    assert metrics["source_aware_recall_at_1s"] == 1.0
    assert metrics["segment_precision_at_1s"] == 0.5
    assert metrics["false_positive_segments"] == 1
    assert metrics["green_precision"] == 0.5


def test_duration_and_slope_inconsistency_is_rejected() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 60.0, "source_episode": "EP02",
        "source_start": 10.0, "source_end": 15.0, "speed_ratio": 1.0,
    }]
    impossible = SegmentMatch(
        0.0, 60.0, "EP02", 10.0, 15.0, 1.0, 0.99, None, 1.0, 0.99,
    )
    metrics = evaluate_segments(truth, [impossible])
    assert metrics["source_aware_recall_at_1s"] == 0.0
    assert metrics["segment_precision_at_1s"] == 0.0
    assert metrics["physical_inconsistent_predictions"] == 1
