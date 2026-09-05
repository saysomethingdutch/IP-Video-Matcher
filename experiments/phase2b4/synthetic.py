"""Pre-registered synthetic candidate-cloud construction and frozen replay."""

from __future__ import annotations

from typing import Any

import numpy as np

from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner

from .metrics import mapping_evaluation, oracle_band_diagnostics, segment_from_dict, temporal_separability_metrics
from .trace import candidates_from_audit, trace_case, classify_first_loss


AXIS_GRID: dict[str, tuple[Any, ...]] = {
    "distributed_duplicate_frames": (0, 4, 8, 12, 16),
    "timestamp_quantization_seconds": (0.5, 1.0, 2.0, 4.0),
    "distractor_density": (0, 1, 3, 7),
    "correct_line_noise_seconds": (0.0, 0.5, 1.0, 1.5),
    "parallel_competitor_score": (0.80, 0.82, 0.90, 0.96, 0.99),
    "local_plateau_frames": (0, 2, 4, 6, 8, 12, 16),
}


def synthetic_truth() -> dict[str, Any]:
    return {
        "accused_start": 0.0,
        "accused_end": 8.0,
        "source_episode": "TRUE",
        "source_start": 100.0,
        "source_end": 108.0,
        "speed_ratio": 1.0,
    }


def _distributed_indices(count: int) -> set[int]:
    if count <= 0:
        return set()
    if count >= 16:
        return set(range(16))
    return set(np.linspace(0, 15, count, dtype=int).tolist())


def _centered_indices(count: int) -> set[int]:
    start = (16 - count) // 2
    return set(range(start, start + count))


def build_cloud(axis: str, value: Any) -> list[dict[str, Any]]:
    """Build one fixed-top-8 audit stream while changing exactly one axis."""

    if axis not in AXIS_GRID or value not in AXIS_GRID[axis]:
        raise ValueError("unregistered synthetic axis/value")
    frames: list[dict[str, Any]] = []
    noise_pattern = (-1.0, 0.0, 1.0, 0.0)
    distributed = _distributed_indices(int(value)) if axis == "distributed_duplicate_frames" else set()
    local = _centered_indices(int(value)) if axis == "local_plateau_frames" else set()
    for frame_index in range(16):
        accused_time = frame_index * 0.5
        correct_time = 100.0 + accused_time
        if axis == "timestamp_quantization_seconds":
            quantum = float(value)
            correct_time = float(np.round(correct_time / quantum) * quantum)
        elif axis == "correct_line_noise_seconds":
            correct_time += noise_pattern[frame_index % len(noise_pattern)] * float(value)
        entries: list[dict[str, Any]] = [
            {"source_id": "TRUE", "source_time": correct_time, "score": 0.96, "kind": "correct"}
        ]
        if frame_index in distributed or frame_index in local:
            entries.append({
                "source_id": "TRUE", "source_time": 120.0, "score": 0.98,
                "kind": "plateau",
            })
        if axis == "parallel_competitor_score":
            entries.append({
                "source_id": "RIVAL", "source_time": 200.0 + accused_time,
                "score": float(value), "kind": "parallel",
            })
        distractor_count = int(value) if axis == "distractor_density" else 0
        for distractor in range(distractor_count):
            # Episode identity is unique by frame so this density axis cannot
            # accidentally introduce a coherent six-frame line.
            entries.append({
                "source_id": f"NOISE_{frame_index:02d}_{distractor:02d}",
                "source_time": 300.0 + 20.0 * distractor + 0.5 * ((7 * frame_index + 3 * distractor) % 17),
                "score": 0.94,
                "kind": "incoherent_distractor",
            })
        filler_index = 0
        while len(entries) < 8:
            entries.append({
                "source_id": f"FILLER_{frame_index:02d}_{filler_index:02d}",
                "source_time": 500.0 + 10.0 * frame_index + 0.5 * filler_index,
                "score": 0.70,
                "kind": "inactive_filler",
            })
            filler_index += 1
        if len(entries) > 8:
            raise ValueError("synthetic axis exceeds fixed top-8")
        entries.sort(key=lambda item: (-float(item["score"]), str(item["source_id"]), float(item["source_time"])))
        for rank, item in enumerate(entries, 1):
            frames.append({
                "accused_time": accused_time,
                "source_time": float(item["source_time"]),
                "source_id": str(item["source_id"]),
                "rank": rank,
                "raw_cosine": float(item["score"]),
                "adapted_visual_score": float(item["score"]),
                "retained": True,
                "kind": item["kind"],
            })
    return frames


def _synthetic_row(axis: str, value: Any, audit: list[dict[str, Any]]) -> dict[str, Any]:
    truth = synthetic_truth()
    candidates = candidates_from_audit(audit)
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(candidates, index_complete=True)
    predictions = [item.to_dict() for item in outcome.segments]
    alternatives = [item.to_dict() for item in outcome.alternatives]
    complete = any(
        mapping_evaluation(segment_from_dict(item), truth)["complete"]
        for item in predictions + alternatives
    )
    status = "AMBIGUOUS" if outcome.ambiguous or outcome.contradiction else (
        "MATCH_FOUND" if predictions else "MATCH_NOT_FOUND"
    )
    return {
        "case_id": f"SYNTHETIC-{axis}-{value}",
        "backend": "SYNTHETIC",
        "positive": True,
        "truth": [truth],
        "candidate_audit": audit,
        "predictions": predictions,
        "alternatives": alternatives,
        "status": status,
        "complete_correct_mapping": complete,
        "axis": axis,
        "value": value,
    }


def run_synthetic_grid() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for axis, values in AXIS_GRID.items():
        for value in values:
            audit = build_cloud(axis, value)
            row = _synthetic_row(axis, value, audit)
            metrics = temporal_separability_metrics(audit, row["truth"][0])
            oracle = oracle_band_diagnostics(audit, row["truth"][0])
            trace = trace_case(row)
            row.update(
                metrics=metrics,
                oracle=oracle,
                trace=trace,
                first_loss_stage=classify_first_loss(trace, oracle),
                extraction_loss=(
                    trace["complete_gt_free_proposal_count"] > 0
                    and trace["complete_final_count"] == 0
                ),
            )
            rows.append(row)
    return {"axis_grid": {key: list(value) for key, value in AXIS_GRID.items()}, "rows": rows}
