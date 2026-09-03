from __future__ import annotations

import json
import random
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .benchmarking import (
    _encoder_available,
    _ffmpeg,
    _make_clip,
    _make_platform_transcode_clip,
    evaluate_segments,
    resource_snapshot,
)
from .config import AppConfig, MatchingConfig
from .database import Database
from .matching import TemporalAligner, VideoMatcher
from .media import extract_grayscale_frames
from .models import CandidateMatch, MatchStatus, SegmentMatch
from .retrieval_benchmark import (
    PHASE_BUCKET_COUNT,
    TRANSCODE_PROFILES,
    _balanced_phase_assignments,
    _sample_off_grid_start,
)


IDEAL_SPEEDS = (0.75, 0.80, 0.90, 1.00, 1.10, 1.25, 1.40, 1.50)
REAL_PROFILES = ("direct", "p1", "p2", "p3")


def _line(
    episode: str,
    accused_start: float,
    source_start: float,
    speed: float,
    count: int,
    score: float = 0.98,
    jitter: tuple[float, ...] = (0.0,),
) -> list[CandidateMatch]:
    return [
        CandidateMatch(
            accused_start + index * 0.5,
            episode,
            source_start + speed * index * 0.5 + jitter[index % len(jitter)],
            score,
        )
        for index in range(count)
    ]


def _truth(
    episode: str,
    accused_start: float,
    source_start: float,
    speed: float,
    duration: float,
) -> dict[str, Any]:
    return {
        "accused_start": accused_start,
        "accused_end": accused_start + duration,
        "source_episode": episode,
        "source_start": source_start,
        "source_end": source_start + speed * duration,
        "speed_ratio": speed,
    }


def _metric_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [item["metrics"] for item in cases]
    truth_count = sum(int(item["ground_truth_segments"]) for item in metrics)
    prediction_count = sum(int(item["predicted_segments"]) for item in metrics)
    source_recalled = sum(int(item["source_aware_recalled_segments"]) for item in metrics)
    episode_recalled = sum(int(item["episode_recalled_segments"]) for item in metrics)
    false_positive_segments = sum(int(item["false_positive_segments"]) for item in metrics)
    accused_errors = [
        float(value) for item in metrics for value in item.get("raw_accused_boundary_errors", [])
    ]
    source_errors = [
        float(value) for item in metrics for value in item.get("raw_source_boundary_errors", [])
    ]
    speed_errors = [
        float(value) for item in metrics for value in item.get("raw_speed_ratio_errors", [])
    ]
    predictions = [
        prediction for item in cases for prediction in item.get("predictions", [])
    ]
    green_count = sum(int(item.get("candidate_green_predictions", 0)) for item in metrics)
    correct_green = sum(
        int(item.get("correct_candidate_green_predictions", 0)) for item in metrics
    )
    overlapping = sum(int(item.get("overlapping_predictions", 0)) for item in metrics)
    correct_episode = sum(int(item.get("correct_episode_predictions", 0)) for item in metrics)
    segment_count_errors = [
        abs(int(item["predicted_segments"]) - int(item["ground_truth_segments"]))
        for item in metrics
    ]
    return {
        "case_count": len(cases),
        "ground_truth_segments": truth_count,
        "predicted_segments": prediction_count,
        "source_aware_segment_recall_at_1s": (
            source_recalled / truth_count if truth_count else None
        ),
        "segment_precision_at_1s": (
            (prediction_count - false_positive_segments) / prediction_count
            if prediction_count else (1.0 if not truth_count else 0.0)
        ),
        "episode_recall": episode_recalled / truth_count if truth_count else None,
        "episode_correctness": correct_episode / overlapping if overlapping else None,
        "source_boundary_mae_seconds": (
            sum(source_errors) / len(source_errors) if source_errors else None
        ),
        "accused_boundary_mae_seconds": (
            sum(accused_errors) / len(accused_errors) if accused_errors else None
        ),
        "false_positive_segment_count": false_positive_segments,
        "false_positive_seconds": sum(float(item["false_positive_seconds"]) for item in metrics),
        "ambiguity_rate": (
            sum(item["status"] == MatchStatus.AMBIGUOUS.value for item in cases) / len(cases)
            if cases else None
        ),
        "mean_absolute_segment_count_error": (
            sum(segment_count_errors) / len(segment_count_errors)
            if segment_count_errors else None
        ),
        "speed_ratio_mae": sum(speed_errors) / len(speed_errors) if speed_errors else None,
        "mean_residual_rms_seconds": (
            sum(float(item["residual_rms_seconds"]) for item in predictions) / len(predictions)
            if predictions else None
        ),
        "mean_inlier_ratio": (
            sum(float(item["inlier_ratio"]) for item in predictions) / len(predictions)
            if predictions else None
        ),
        "candidate_green_precision_diagnostic": (
            correct_green / green_count if green_count else None
        ),
        "candidate_green_count": green_count,
        "runtime_seconds": sum(float(item["runtime_seconds"]) for item in cases),
        "failure_attribution": {
            "RETRIEVAL_LIMIT": sum(
                item.get("failure_attribution") == "RETRIEVAL_LIMIT" for item in cases
            ),
            "ALIGNMENT_LIMIT": sum(
                item.get("failure_attribution") == "ALIGNMENT_LIMIT" for item in cases
            ),
        },
    }


def _group_cases(cases: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[str(case[key])].append(case)
    return {name: _metric_summary(items) for name, items in sorted(grouped.items())}


class IdealTemporalBenchmark:
    """Deterministic candidate-level benchmark, independent of visual retrieval."""

    def __init__(self, config: MatchingConfig | None = None):
        self.config = config or MatchingConfig()
        self.aligner = TemporalAligner(self.config)

    def _run_case(
        self,
        case_id: str,
        transformation: str,
        truth: list[dict[str, Any]],
        candidates: list[CandidateMatch],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        outcome = self.aligner.align_with_diagnostics(candidates, index_complete=True)
        elapsed = time.perf_counter() - started
        status = (
            MatchStatus.AMBIGUOUS.value
            if outcome.ambiguous or outcome.contradiction
            else MatchStatus.MATCH_FOUND.value if outcome.segments
            else MatchStatus.MATCH_NOT_FOUND.value
        )
        metrics = evaluate_segments(
            truth, outcome.segments, status, outcome.alternatives
        )
        return {
            "case_id": case_id,
            "transformation": transformation,
            "status": status,
            "metrics": metrics,
            "runtime_seconds": elapsed,
            "predictions": [item.to_dict() for item in outcome.segments],
            "alternative_candidates": [item.to_dict() for item in outcome.alternatives],
        }

    def run(self) -> dict[str, Any]:
        cases: list[dict[str, Any]] = []
        for speed in IDEAL_SPEEDS:
            accused_start, source_start, duration = 2.0, 40.0, 10.0
            candidates = _line(
                "EP07", accused_start, source_start, speed, 20, score=0.97
            )
            cases.append(self._run_case(
                f"IDEAL_SPEED_{speed:.2f}", "ideal_speed_sweep",
                [_truth("EP07", accused_start, source_start, speed, duration)],
                candidates,
            ))

        jitter_candidates: list[CandidateMatch] = []
        jitter = (-0.5, 0.0, 0.5, 0.0)
        for index in range(24):
            accused = index * 0.5
            jitter_candidates.append(CandidateMatch(
                accused, "EP01", 25.0 + 1.25 * accused + jitter[index % 4], 0.96
            ))
            jitter_candidates.append(CandidateMatch(
                accused, "EP01", 80.0 + ((index * 7) % 19) * 0.5, 1.0
            ))
            jitter_candidates.append(CandidateMatch(
                accused, "EP99", 140.0 + ((index * 11) % 23) * 0.5, 0.999
            ))
        cases.append(self._run_case(
            "IDEAL_GRID_JITTER", "grid_jitter_outlier_wrong_episode",
            [_truth("EP01", 0.0, 25.0, 1.25, 12.0)], jitter_candidates,
        ))

        specifications = [
            ("EP01", 0.0, 10.0, 1.0),
            ("EP02", 8.0, 30.0, 1.25),
            ("EP01", 14.0, 50.0, 0.8),
            ("EP03", 22.0, 70.0, 1.1),
            ("EP01", 30.0, 10.0, 1.0),
        ]
        piecewise_candidates: list[CandidateMatch] = []
        piecewise_truth: list[dict[str, Any]] = []
        for episode, accused_start, source_start, speed in specifications:
            piecewise_candidates.extend(_line(
                episode, accused_start, source_start, speed, 12, score=0.98
            ))
            for index in range(12):
                accused = accused_start + index * 0.5
                piecewise_candidates.append(CandidateMatch(
                    accused, episode,
                    150.0 + ((index * 7 + int(accused_start)) % 29) * 0.5,
                    0.995,
                ))
            piecewise_truth.append(_truth(
                episode, accused_start, source_start, speed, 6.0
            ))
        cases.append(self._run_case(
            "IDEAL_PIECEWISE_5", "piecewise_five_segments",
            piecewise_truth, piecewise_candidates,
        ))
        aggregate = _metric_summary(cases)
        return {
            "schema_version": "temporal-alignment-characterization-v0.1",
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "ideal_candidate_temporal_alignment",
            "visual_retrieval_involved": False,
            "configuration": asdict(self.config),
            "speed_sweep": list(IDEAL_SPEEDS),
            "aggregate": aggregate,
            "by_transformation": _group_cases(cases, "transformation"),
            "passed": (
                aggregate["source_aware_segment_recall_at_1s"] == 1.0
                and aggregate["segment_precision_at_1s"] == 1.0
            ),
            "cases": cases,
        }


def _make_lossless_direct_clip(
    source: Path, target: Path, start: float, duration: float
) -> None:
    _ffmpeg([
        "-ss", f"{start:.6f}", "-i", str(source),
        "-vf", f"trim=duration={duration:.6f},setpts=PTS-STARTPTS",
        "-an", "-c:v", "ffv1", str(target),
    ])


class RealSegmentBenchmark:
    """Real-media segment benchmark; reports aliases and normalized positions only."""

    def __init__(self, config: AppConfig, database: Database):
        if config.matching.coarse_fps != 2.0 or config.matching.top_k != 8:
            raise ValueError("Phase 2A real segment baseline requires 2 FPS and top-k=8")
        self.config = config
        self.database = database

    def _retrieval_attribution(
        self,
        matcher: VideoMatcher,
        query_path: Path,
        episode: str,
        source_start: float,
        speed: float,
    ) -> tuple[str | None, int, int]:
        frames = extract_grayscale_frames(
            query_path,
            fps=self.config.matching.coarse_fps,
            width=self.config.matching.frame_width,
            height=self.config.matching.frame_height,
        )
        features = matcher.encoder.encode_images(frame for _, frame in frames)
        candidates = matcher.retriever.retrieve(
            np.asarray([item[0] for item in frames], dtype=np.float32),
            features,
        )
        hits: set[int] = set()
        tolerance = 1.0 / self.config.matching.coarse_fps
        for candidate in candidates:
            expected = source_start + speed * candidate.accused_time
            if (
                candidate.source_episode == episode
                and abs(candidate.source_time - expected) <= tolerance + 1e-6
            ):
                hits.add(round(candidate.accused_time * self.config.matching.coarse_fps))
        minimum = int(
            self.config.matching.minimum_segment_seconds * self.config.matching.coarse_fps
        )
        attribution = None if len(hits) >= minimum else "RETRIEVAL_LIMIT"
        return attribution, len(hits), len(frames)

    def run(
        self,
        samples_per_episode: int = 10,
        source_duration_seconds: float = 6.0,
        modes: tuple[str, ...] = REAL_PROFILES,
        include_speed_profiles: bool = False,
        seed: int = 20260903,
    ) -> dict[str, Any]:
        allowed = set(REAL_PROFILES) | {"speed_0.8x", "speed_1.25x"}
        requested_modes = list(modes)
        if include_speed_profiles:
            requested_modes.extend(["speed_0.8x", "speed_1.25x"])
        if set(requested_modes) - allowed:
            raise ValueError("Unsupported real temporal benchmark profile")
        project_id = self.database.initialize(self.config.paths.root)
        health = self.database.index_health(project_id)
        if health.get("status") != "COMPLETE":
            raise ValueError("A COMPLETE 2 FPS source index is required")
        rows = self.database.list_original_indexes(project_id)
        aliases = {
            str(row["episode"]): f"E{index:03d}" for index, row in enumerate(rows, start=1)
        }
        randomizer = random.Random(seed)
        cases: list[dict[str, Any]] = []
        profile_status: dict[str, dict[str, str]] = {}
        active_modes: list[str] = []
        for mode in requested_modes:
            if mode == "p3" and not _encoder_available("libx265"):
                profile_status[mode] = {
                    "status": "SKIPPED",
                    "reason": "libx265 HEVC encoder unavailable in local FFmpeg",
                }
            else:
                profile_status[mode] = {"status": "COMPLETED"}
                active_modes.append(mode)
        matcher = VideoMatcher(self.config, self.database)
        grid_period = 1.0 / self.config.matching.coarse_fps
        memory_before = resource_snapshot()
        temporary_root = self.config.paths.workspace / "temporal-benchmark-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary_directory:
            temporary = Path(temporary_directory)
            for row in rows:
                episode = str(row["episode"])
                alias = aliases[episode]
                source_path = Path(row["file_path"])
                media_duration = float(row["duration"])
                usable = media_duration - source_duration_seconds - 0.5
                phases = _balanced_phase_assignments(randomizer, samples_per_episode)
                for sample_index in range(samples_per_episode):
                    bin_start = 0.25 + usable * sample_index / samples_per_episode
                    bin_end = 0.25 + usable * (sample_index + 1) / samples_per_episode
                    start = _sample_off_grid_start(
                        randomizer, bin_start, bin_end,
                        phases[sample_index] % PHASE_BUCKET_COUNT,
                    )
                    interval_id = f"{alias}-I{sample_index + 1:03d}"
                    for mode in active_modes:
                        speed = (
                            0.8 if mode == "speed_0.8x"
                            else 1.25 if mode == "speed_1.25x" else 1.0
                        )
                        suffix = ".mkv" if mode == "direct" else ".mp4"
                        query_path = temporary / f"{interval_id}-{mode}{suffix}"
                        if mode == "direct":
                            _make_lossless_direct_clip(
                                source_path, query_path, start, source_duration_seconds
                            )
                        elif mode in TRANSCODE_PROFILES:
                            profile = TRANSCODE_PROFILES[mode]
                            _make_platform_transcode_clip(
                                source_path, query_path, start, source_duration_seconds,
                                profile["codec"], profile["scale"], profile["bitrate"],
                                profile["maxrate"], profile["bufsize"],
                                profile.get("codec_params_flag"), profile.get("codec_params"),
                            )
                        else:
                            _make_clip(
                                source_path, query_path, start, source_duration_seconds,
                                f"setpts=PTS/{speed}",
                            )
                        accused_duration = source_duration_seconds / speed
                        truth = [_truth(
                            episode, 0.0, start, speed, accused_duration
                        )]
                        started = time.perf_counter()
                        result = matcher.analyze_file(
                            f"REAL_{interval_id}_{mode}", query_path, persist=False
                        )
                        elapsed = time.perf_counter() - started
                        metrics = evaluate_segments(
                            truth, result.segments, result.status, result.alternatives
                        )
                        attribution = None
                        usable_hits = 0
                        query_frames = 0
                        if metrics["source_aware_recalled_segments"] == 0:
                            attribution, usable_hits, query_frames = self._retrieval_attribution(
                                matcher, query_path, episode, start, speed
                            )
                            if attribution is None:
                                attribution = "ALIGNMENT_LIMIT"
                        cases.append({
                            "case_id": f"{interval_id}-{mode}",
                            "interval_id": interval_id,
                            "episode_alias": alias,
                            "profile": mode,
                            "normalized_source_start": round(start / media_duration, 8),
                            "source_subframe_phase_seconds": round(start % grid_period, 6),
                            "status": result.status,
                            "metrics": metrics,
                            "predictions": [item.to_dict() for item in result.segments],
                            "runtime_seconds": elapsed,
                            "failure_attribution": attribution,
                            "alignment_usable_candidate_frame_count": usable_hits,
                            "query_frame_count": query_frames,
                        })
        memory_after = resource_snapshot()
        return {
            "schema_version": "temporal-alignment-characterization-v0.1",
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "real_media_segment_level_temporal_alignment",
            "contains_media_identity": False,
            "episode_count": len(rows),
            "configuration": {
                "source_fps": self.config.matching.coarse_fps,
                "query_fps": self.config.matching.coarse_fps,
                "retrieval_top_k": self.config.matching.top_k,
                "samples_per_episode": samples_per_episode,
                "source_duration_seconds": source_duration_seconds,
                "profiles": requested_modes,
                "timestamp_basis": "nominal_grid_time",
            },
            "profile_status": profile_status,
            "aggregate": _metric_summary(cases),
            "by_profile": _group_cases(cases, "profile"),
            "by_episode": _group_cases(cases, "episode_alias"),
            "by_interval": _group_cases(cases, "interval_id"),
            "performance": {
                "memory_before": memory_before,
                "memory_after": memory_after,
                "observed_peak_lower_bound_bytes": max(memory_after.values()),
                "conservative_combined_upper_bound_bytes": sum(memory_after.values()),
            },
            "cases": cases,
        }


def write_temporal_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
