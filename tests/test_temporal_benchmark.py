from __future__ import annotations

import json

from ipvm.temporal_benchmark import IdealTemporalBenchmark, write_temporal_report


def test_ideal_temporal_benchmark_covers_speed_jitter_and_piecewise(tmp_path) -> None:
    report = IdealTemporalBenchmark().run()
    assert report["passed"] is True
    assert report["speed_sweep"] == [0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.5]
    assert report["aggregate"]["source_aware_segment_recall_at_1s"] == 1.0
    assert report["aggregate"]["segment_precision_at_1s"] == 1.0
    assert report["aggregate"]["false_positive_segment_count"] == 0
    piecewise = next(item for item in report["cases"] if item["case_id"] == "IDEAL_PIECEWISE_5")
    assert piecewise["metrics"]["predicted_segments"] == 5
    assert sum(item["source_reuse"] for item in piecewise["predictions"]) == 2
    jitter = next(item for item in report["cases"] if item["case_id"] == "IDEAL_GRID_JITTER")
    assert jitter["metrics"]["source_aware_recall_at_1s"] == 1.0

    path = tmp_path / "ideal.json"
    write_temporal_report(report, path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["visual_retrieval_involved"] is False
    assert parsed["configuration"]["candidate_timestamp_uncertainty_grid_steps"] == 1
