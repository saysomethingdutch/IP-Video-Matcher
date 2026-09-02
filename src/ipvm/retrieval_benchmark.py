from __future__ import annotations

import json
import math
import random
import statistics
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .benchmarking import _make_clip
from .benchmarking import (
    _encoder_available,
    _make_platform_transcode_clip,
)
from .config import AppConfig
from .database import Database
from .features import DCTVisualEncoder, ImageEncoder
from .media import extract_grayscale_frames


TOP_K_VALUES = (1, 8, 32, 128)
DEFAULT_COARSE_FPS = 2.0


def grid_period_from_fps(coarse_fps: float) -> float:
    """Source-grid period in seconds, derived from the sampling rate."""
    if coarse_fps <= 0:
        raise ValueError("coarse_fps must be positive")
    return 1.0 / coarse_fps


# Module default for the pure helpers; RetrievalBenchmark derives its own value
# from config.matching.coarse_fps and never relies on this literal.
SOURCE_GRID_PERIOD_SECONDS = grid_period_from_fps(DEFAULT_COARSE_FPS)
PHASE_BUCKET_COUNT = 10
POSITION_BUCKET_COUNT = 5
ALIGNMENT_USABLE_GRID_STEPS = 1
THRESHOLD = 0.95
CLUSTER_BOOTSTRAP_ITERATIONS = 2000
CLUSTER_BOOTSTRAP_SEED = 20260902
# ADR-0005: the readiness gate for entering Temporal Alignment v2 is the
# alignment-usable metric. Strict recall is retained as a localization diagnostic.
READINESS_GATE_METRIC = "alignment_usable_recall_at_8"


# Encoder parameters are pinned to single-threaded, frame-parallel-free settings so
# that a benchmark re-run reproduces the same bytes. Multi-threaded x264/x265 is not
# bit-deterministic and produced 1-5 frame run-to-run drift in the Phase 2A-0.2 run.
TRANSCODE_PROFILES: dict[str, dict[str, str]] = {
    "p1": {
        "codec": "libx264",
        "scale": "1280:720",
        "bitrate": "2000k",
        "maxrate": "2400k",
        "bufsize": "4000k",
        "codec_params_flag": "-x264-params",
        "codec_params": "threads=1:sliced-threads=0:lookahead-threads=1:deterministic=1",
    },
    "p2": {
        "codec": "libx264",
        "scale": "854:480",
        "bitrate": "1000k",
        "maxrate": "1200k",
        "bufsize": "2000k",
        "codec_params_flag": "-x264-params",
        "codec_params": "threads=1:sliced-threads=0:lookahead-threads=1:deterministic=1",
    },
    "p3": {
        "codec": "libx265",
        "scale": "1280:720",
        "bitrate": "1600k",
        "maxrate": "2000k",
        "bufsize": "3200k",
        "codec_params_flag": "-x265-params",
        "codec_params": "pools=none:frame-threads=1:wpp=0",
    },
}


@dataclass(frozen=True)
class SourceIndex:
    episode_alias: str
    timestamps: np.ndarray
    features: np.ndarray


def characterize_query_frame(
    query_feature: np.ndarray,
    source_indexes: Iterable[SourceIndex],
    expected_episode_alias: str,
    expected_source_timestamp: float,
    tolerance_seconds: float,
    alignment_usable_tolerance_seconds: float | None = None,
) -> dict[str, Any]:
    if alignment_usable_tolerance_seconds is None:
        alignment_usable_tolerance_seconds = tolerance_seconds
    ranked: list[tuple[float, str, float]] = []
    for source in source_indexes:
        scores = source.features @ query_feature
        ranked.extend(
            (float(score), source.episode_alias, float(timestamp))
            for score, timestamp in zip(scores, source.timestamps, strict=True)
        )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    correct_ranks = [
        rank for rank, (_, episode, timestamp) in enumerate(ranked, start=1)
        if episode == expected_episode_alias
        and abs(timestamp - expected_source_timestamp) <= tolerance_seconds
    ]
    correct_rank = min(correct_ranks) if correct_ranks else None
    usable_ranks = [
        rank for rank, (_, episode, timestamp) in enumerate(ranked, start=1)
        if episode == expected_episode_alias
        and abs(timestamp - expected_source_timestamp) <= alignment_usable_tolerance_seconds
    ]
    usable_rank = min(usable_ranks) if usable_ranks else None
    correct_scores = [
        score for score, episode, timestamp in ranked
        if episode == expected_episode_alias
        and abs(timestamp - expected_source_timestamp) <= tolerance_seconds
    ]
    incorrect_scores = [
        score for score, episode, timestamp in ranked
        if not (
            episode == expected_episode_alias
            and abs(timestamp - expected_source_timestamp) <= tolerance_seconds
        )
    ]
    score_margin = None
    if correct_scores and incorrect_scores:
        score_margin = max(correct_scores) - max(incorrect_scores)
    return {
        "correct_source_rank": correct_rank,
        "score_margin": round(score_margin, 8) if score_margin is not None else None,
        "hits": {str(k): bool(correct_rank is not None and correct_rank <= k) for k in TOP_K_VALUES},
        "alignment_usable_hits": {
            str(k): bool(usable_rank is not None and usable_rank <= k) for k in TOP_K_VALUES
        },
    }


def aggregate_observations(observations: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [int(item["correct_source_rank"]) for item in observations if item["correct_source_rank"]]
    margins = [float(item["score_margin"]) for item in observations if item["score_margin"] is not None]
    count = len(observations)
    distribution = {"1": 0, "2-8": 0, "9-32": 0, "33-128": 0, ">128": 0, "not_found": 0}
    for item in observations:
        rank = item["correct_source_rank"]
        if rank is None:
            distribution["not_found"] += 1
        elif rank == 1:
            distribution["1"] += 1
        elif rank <= 8:
            distribution["2-8"] += 1
        elif rank <= 32:
            distribution["9-32"] += 1
        elif rank <= 128:
            distribution["33-128"] += 1
        else:
            distribution[">128"] += 1
    strict_recall = {
        str(k): (
            sum(bool(item["hits"][str(k)]) for item in observations) / count
            if count else None
        )
        for k in TOP_K_VALUES
    }
    usable_recall: dict[str, float | None] = {}
    adjacent_grid_only_miss_rate: dict[str, float | None] = {}
    true_retrieval_loss_rate: dict[str, float | None] = {}
    for k in TOP_K_VALUES:
        key = str(k)
        usable_hits = [
            bool(item.get("alignment_usable_hits", {}).get(key, False))
            for item in observations
        ]
        if not any("alignment_usable_hits" in item for item in observations):
            usable_recall[key] = None
            adjacent_grid_only_miss_rate[key] = None
            true_retrieval_loss_rate[key] = None
            continue
        usable_recall[key] = sum(usable_hits) / count if count else None
        adjacent_grid_only_miss_rate[key] = (
            sum(
                (not bool(item["hits"][key])) and bool(usable_hits[index])
                for index, item in enumerate(observations)
            )
            / count
            if count else None
        )
        true_retrieval_loss_rate[key] = (
            sum(
                (not bool(item["hits"][key])) and (not bool(usable_hits[index]))
                for index, item in enumerate(observations)
            )
            / count
            if count else None
        )
    return {
        "query_frame_count": count,
        "retrieval_recall_at_k": strict_recall,
        "alignment_usable_recall_at_k": usable_recall,
        "adjacent_grid_only_miss_rate_at_k": adjacent_grid_only_miss_rate,
        "true_retrieval_loss_rate_at_k": true_retrieval_loss_rate,
        "correct_source_rank_distribution": distribution,
        "median_rank": statistics.median(ranks) if ranks else None,
        "p90_rank": (
            float(np.percentile(np.asarray(ranks), 90, method="higher")) if ranks else None
        ),
        "score_margin": {
            "count": len(margins),
            "mean": statistics.fmean(margins) if margins else None,
            "median": statistics.median(margins) if margins else None,
            "p10": (
                float(np.percentile(np.asarray(margins), 10, method="lower"))
                if margins else None
            ),
            "p90": (
                float(np.percentile(np.asarray(margins), 90, method="higher"))
                if margins else None
            ),
            "minimum": min(margins) if margins else None,
            "maximum": max(margins) if margins else None,
        },
    }


def _position_bucket(normalized_position: float) -> str:
    index = min(POSITION_BUCKET_COUNT - 1, int(normalized_position * POSITION_BUCKET_COUNT))
    lower = index * 100 // POSITION_BUCKET_COUNT
    upper = (index + 1) * 100 // POSITION_BUCKET_COUNT
    return f"P{index + 1}_{lower:02d}-{upper:02d}%"


def _phase_bucket(phase_seconds: float) -> str:
    index = min(
        PHASE_BUCKET_COUNT - 1,
        int((phase_seconds / SOURCE_GRID_PERIOD_SECONDS) * PHASE_BUCKET_COUNT),
    )
    lower = index * SOURCE_GRID_PERIOD_SECONDS / PHASE_BUCKET_COUNT
    upper = (index + 1) * SOURCE_GRID_PERIOD_SECONDS / PHASE_BUCKET_COUNT
    return f"B{index + 1:02d}_{lower:.3f}-{upper:.3f}s"


def _sample_off_grid_start(
    randomizer: random.Random,
    bin_start: float,
    bin_end: float,
    phase_bucket_index: int,
) -> float:
    """Sample a random start in one time stratum and one source-grid phase stratum."""
    phase_width = SOURCE_GRID_PERIOD_SECONDS / PHASE_BUCKET_COUNT
    phase_low = phase_bucket_index * phase_width
    phase_high = (phase_bucket_index + 1) * phase_width
    # Keep every query measurably off the 2 FPS source grid, including edge buckets.
    phase_low = max(phase_low, 0.021)
    phase_high = min(phase_high, SOURCE_GRID_PERIOD_SECONDS - 0.021)
    if phase_low >= phase_high:
        raise ValueError("Phase bucket cannot produce an off-grid sample")
    phase = randomizer.uniform(phase_low, phase_high)
    first_grid = math.ceil((bin_start - phase) / SOURCE_GRID_PERIOD_SECONDS)
    last_grid = math.floor((bin_end - phase) / SOURCE_GRID_PERIOD_SECONDS)
    if first_grid > last_grid:
        raise ValueError("Time stratum is too narrow for phase-stratified sampling")
    grid_index = randomizer.randint(first_grid, last_grid)
    return round(grid_index * SOURCE_GRID_PERIOD_SECONDS + phase, 6)


def _balanced_phase_assignments(
    randomizer: random.Random, sample_count: int
) -> list[int]:
    assignments = [index % PHASE_BUCKET_COUNT for index in range(sample_count)]
    randomizer.shuffle(assignments)
    return assignments


def _interval_count(items: list[dict[str, Any]]) -> int:
    return len({str(item["interval_id"]) for item in items})


def _grouped_aggregates(
    observations: list[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        groups[str(observation[key])].append(observation)
    return {
        group: {
            "query_frame_count": len(items),
            "interval_count": _interval_count(items),
            "aggregate": aggregate_observations(items),
        }
        for group, items in sorted(groups.items())
    }


def _episode_aggregates(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        episodes[str(observation["episode"])].append(observation)
    result: dict[str, dict[str, Any]] = {}
    for episode, items in sorted(episodes.items()):
        result[episode] = {
            "query_frame_count": len(items),
            "interval_count": _interval_count(items),
            "aggregate": aggregate_observations(items),
            "by_query_mode": _grouped_aggregates(items, "query_mode"),
        }
    return result


def _interval_aggregates(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        intervals[str(observation["interval_id"])].append(observation)
    result: dict[str, dict[str, Any]] = {}
    for interval_id, items in sorted(intervals.items()):
        first = items[0]
        result[interval_id] = {
            "episode": first["episode"],
            "normalized_source_start": first["normalized_source_start"],
            "source_position_bucket": first["source_position_bucket"],
            "source_subframe_phase_seconds": first["interval_start_phase_seconds"],
            "query_frame_count": len(items),
            "interval_count": _interval_count(items),
            "aggregate": aggregate_observations(items),
            "by_query_mode": _grouped_aggregates(items, "query_mode"),
        }
    return result


def _threshold_band(
    lower: float | None,
    upper: float | None,
    threshold: float = THRESHOLD,
) -> str:
    """Classify a confidence interval against a fixed threshold.

    A missing interval yields UNKNOWN rather than silently reading as STRADDLES.
    """
    if lower is None or upper is None:
        return "UNKNOWN"
    if lower >= threshold:
        return "ABOVE"
    if upper < threshold:
        return "BELOW"
    return "STRADDLES"


def _phase_bucket_bounds(bucket: str) -> dict[str, Any]:
    range_part = bucket.split("_", 1)[1][:-1]
    lower, upper = range_part.split("-")
    return {
        "index": int(bucket[1:3]) - 1,
        "lower_seconds": float(lower),
        "upper_seconds": float(upper),
    }


def _position_bucket_bounds(bucket: str) -> dict[str, Any]:
    range_part = bucket.split("_", 1)[1][:-1]
    lower, upper = range_part.split("-")
    return {
        "index": int(bucket[1:2]) - 1,
        "lower_position": float(lower) / 100,
        "upper_position": float(upper) / 100,
    }


def _bucket_aggregates(
    observations: list[dict[str, Any]],
    key: str,
    bounds_fn,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        groups[str(observation[key])].append(observation)
    result: dict[str, dict[str, Any]] = {}
    for bucket, items in sorted(groups.items()):
        result[bucket] = {
            "bounds": bounds_fn(bucket),
            "query_frame_count": len(items),
            "interval_count": _interval_count(items),
            "aggregate": aggregate_observations(items),
        }
    return result


def _clusters_for_metric(
    observations: list[dict[str, Any]],
    metric_key: str,
    top_k: int,
) -> list[list[int]]:
    """Group frame-level binary outcomes by interval cluster."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for item in observations:
        value = item.get(metric_key, {}).get(str(top_k))
        grouped[str(item["interval_id"])].append(1 if value else 0)
    return [grouped[key] for key in sorted(grouped)]


def _intra_cluster_correlation(clusters: list[list[int]]) -> dict[str, Any]:
    """One-way ANOVA ICC for interval clusters of binary outcomes."""
    total = sum(len(cluster) for cluster in clusters)
    if total <= 1 or len(clusters) < 2:
        return {"icc": 0.0, "mean_cluster_size": float(total), "design_effect": 1.0}
    grand_mean = sum(sum(cluster) for cluster in clusters) / total
    between_sum_squares = sum(
        len(cluster) * (sum(cluster) / len(cluster) - grand_mean) ** 2
        for cluster in clusters
    )
    within_sum_squares = sum(
        (value - sum(cluster) / len(cluster)) ** 2
        for cluster in clusters
        for value in cluster
    )
    cluster_count = len(clusters)
    ms_between = between_sum_squares / (cluster_count - 1)
    ms_within = within_sum_squares / (total - cluster_count)
    mean_cluster_size = total / cluster_count
    adjusted_cluster_size = (
        total - sum(len(cluster) ** 2 for cluster in clusters) / total
    ) / (cluster_count - 1)
    icc = (ms_between - ms_within) / (
        ms_between + (adjusted_cluster_size - 1) * ms_within
    ) if ms_within >= 0 and (ms_between + (adjusted_cluster_size - 1) * ms_within) > 0 else 0.0
    icc = max(0.0, float(icc))
    design_effect = 1 + (mean_cluster_size - 1) * icc
    return {
        "icc": round(icc, 8),
        "mean_cluster_size": round(mean_cluster_size, 6),
        "design_effect": round(design_effect, 6),
    }


def _cluster_bootstrap_ci(
    clusters: list[list[int]],
    iterations: int,
    seed: int,
    level: float = 0.95,
) -> list[float] | None:
    if not clusters or not any(clusters):
        return None
    rng = np.random.default_rng(seed)
    estimates: list[float] = []
    for _ in range(iterations):
        sample_indexes = rng.integers(0, len(clusters), size=len(clusters))
        pool = [value for index in sample_indexes for value in clusters[int(index)]]
        estimates.append(sum(pool) / len(pool))
    alpha = 1 - level
    return [
        float(np.percentile(np.asarray(estimates), alpha / 2 * 100)),
        float(np.percentile(np.asarray(estimates), (1 - alpha / 2) * 100)),
    ]


def _metric_inference(
    observations: list[dict[str, Any]],
    metric_key: str,
    query_frame_count: int,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    """Cluster inference for one metric. Never shared between strict and usable."""
    clusters_at_8 = _clusters_for_metric(observations, metric_key, 8)
    icc_metrics = _intra_cluster_correlation(clusters_at_8)
    effective_sample_size = (
        query_frame_count / icc_metrics["design_effect"]
        if icc_metrics["design_effect"] > 0 else query_frame_count
    )
    intervals = {
        str(k): _cluster_bootstrap_ci(
            _clusters_for_metric(observations, metric_key, k),
            bootstrap_iterations,
            bootstrap_seed,
        )
        for k in TOP_K_VALUES
    }
    ci_at_8 = intervals["8"]
    return {
        "confidence_interval": intervals,
        "intra_cluster_correlation": icc_metrics["icc"],
        "mean_cluster_size": icc_metrics["mean_cluster_size"],
        "design_effect": icc_metrics["design_effect"],
        "effective_sample_size": round(effective_sample_size, 6),
        "threshold_band": _threshold_band(
            ci_at_8[0] if ci_at_8 else None,
            ci_at_8[1] if ci_at_8 else None,
        ),
    }


def _profile_block(
    observations: list[dict[str, Any]],
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    aggregate = aggregate_observations(observations)
    frame_count = aggregate["query_frame_count"]
    strict = _metric_inference(
        observations, "hits", frame_count, bootstrap_iterations, bootstrap_seed
    )
    usable = _metric_inference(
        observations, "alignment_usable_hits", frame_count,
        bootstrap_iterations, bootstrap_seed,
    )
    return {
        "query_frame_count": frame_count,
        "interval_count": _interval_count(observations),
        "distinct_content_position_count": len({
            (str(item["interval_id"]), float(item["query_timestamp"]))
            for item in observations
        }),
        "nominal_query_frame_count": frame_count,
        "aggregate": aggregate,
        # Per-metric inference. Strict and alignment-usable never share statistics.
        "strict": strict,
        "alignment_usable": usable,
        "strict_threshold_band": strict["threshold_band"],
        "alignment_usable_threshold_band": usable["threshold_band"],
        "readiness_gate_metric": READINESS_GATE_METRIC,
        "readiness_gate_band": usable["threshold_band"],
        "cluster_bootstrap_confidence_interval": {
            "level": 0.95,
            "iterations": bootstrap_iterations,
            "seed": bootstrap_seed,
            "unit": "interval",
            "strict": strict["confidence_interval"],
            "alignment_usable": usable["confidence_interval"],
        },
        "true_retrieval_loss_rate_at_8": aggregate["true_retrieval_loss_rate_at_k"]["8"],
        "adjacent_grid_only_miss_rate_at_8": aggregate["adjacent_grid_only_miss_rate_at_k"]["8"],
    }


def _profile_reports(
    observations: list[dict[str, Any]],
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        groups[str(observation["query_mode"])].append(observation)
    return {
        profile: _profile_block(
            items, bootstrap_iterations, bootstrap_seed
        )
        for profile, items in sorted(groups.items())
    }


def anonymized_aggregate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove frame-level observations while retaining anonymous aggregate evidence."""
    return {
        key: value
        for key, value in report.items()
        if key != "observations"
    }


class RetrievalBenchmark:
    """Phase 2A-0 measurement harness. It does not change matcher behavior."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        encoder: ImageEncoder | None = None,
    ):
        self.config = config
        self.database = database
        self.encoder = encoder or DCTVisualEncoder()
        if config.matching.coarse_fps != 2.0:
            raise ValueError("Phase 2A-0 baseline requires source/query sampling at exactly 2 FPS")
        # Derived from the sampling rate, never a bare literal in the statistical layer.
        self.source_grid_period_seconds = grid_period_from_fps(config.matching.coarse_fps)
        self.alignment_usable_tolerance_seconds = (
            ALIGNMENT_USABLE_GRID_STEPS * self.source_grid_period_seconds
        )

    def run(
        self,
        samples_per_episode: int = 3,
        query_duration_seconds: float = 6.0,
        modes: tuple[str, ...] = ("direct", "p1", "p2", "p3"),
        seed: int = 20260902,
        tolerance_seconds: float = 0.26,
        bootstrap_iterations: int = CLUSTER_BOOTSTRAP_ITERATIONS,
        bootstrap_seed: int = CLUSTER_BOOTSTRAP_SEED,
    ) -> dict[str, Any]:
        invalid_modes = set(modes) - {"direct", "reencode", "p1", "p2", "p3"}
        if invalid_modes:
            raise ValueError(f"Unsupported query modes: {sorted(invalid_modes)}")
        if not modes or len(set(modes)) != len(modes):
            raise ValueError("Query modes must be non-empty and unique")
        profile_status: dict[str, dict[str, str]] = {}
        active_modes: list[str] = []
        for mode in modes:
            if mode == "p3" and not _encoder_available("libx265"):
                profile_status[mode] = {
                    "status": "SKIPPED",
                    "reason": "libx265 HEVC encoder unavailable in local FFmpeg",
                }
                continue
            active_modes.append(mode)
            profile_status[mode] = {"status": "COMPLETED"}
        project_id = self.database.initialize(self.config.paths.root)
        health = self.database.index_health(project_id)
        if health.get("status") != "COMPLETE":
            raise ValueError("A COMPLETE 2 FPS original index is required before retrieval benchmark")
        rows = self.database.list_original_indexes(project_id)
        if not rows:
            raise ValueError("No indexed original episodes are available")
        aliases = {str(row["episode"]): f"E{index:03d}" for index, row in enumerate(rows, start=1)}
        source_indexes: list[SourceIndex] = []
        for row in rows:
            alias = aliases[str(row["episode"])]
            expected_encoder = f"{self.encoder.name}@{self.encoder.version}"
            if str(row["encoder"]) != expected_encoder:
                raise ValueError(
                    f"{alias} index encoder does not match retrieval harness; rebuild originals"
                )
            with np.load(Path(row["index_path"])) as payload:
                try:
                    target_fps = float(payload["target_fps"][0])
                    timestamps = payload["timestamps"].astype(np.float32)
                    features = payload["features"].astype(np.float32)
                except KeyError as exc:
                    raise ValueError(
                        f"{alias} index lacks Phase 2A-0 identity fields; rebuild originals"
                    ) from exc
                if target_fps != 2.0:
                    raise ValueError(f"{alias} source index is not 2 FPS")
                source_indexes.append(SourceIndex(
                    alias,
                    timestamps,
                    features,
                ))

        randomizer = random.Random(seed)
        observations: list[dict[str, Any]] = []
        interval_count = 0
        query_variant_count = 0
        temporary_root = self.config.paths.workspace / "retrieval-benchmark-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary_directory:
            temp = Path(temporary_directory)
            for row in rows:
                episode = str(row["episode"])
                alias = aliases[episode]
                duration = float(row["duration"])
                if duration <= query_duration_seconds + 1.0:
                    raise ValueError(
                        f"{alias} is too short for the requested off-grid query duration"
                    )
                source_path = Path(row["file_path"])
                usable = duration - query_duration_seconds - 0.5
                phase_assignments = _balanced_phase_assignments(
                    randomizer, samples_per_episode
                )
                for sample_index in range(samples_per_episode):
                    bin_start = 0.25 + usable * sample_index / samples_per_episode
                    bin_end = 0.25 + usable * (sample_index + 1) / samples_per_episode
                    phase_bucket_index = phase_assignments[sample_index]
                    start = _sample_off_grid_start(
                        randomizer, bin_start, bin_end, phase_bucket_index
                    )
                    normalized_start = start / duration
                    interval_id = f"{alias}-I{sample_index + 1:03d}"
                    interval_start_phase = start % self.source_grid_period_seconds
                    interval_count += 1
                    for mode in active_modes:
                        if mode == "direct":
                            frames = extract_grayscale_frames(
                                source_path,
                                fps=2.0,
                                width=self.config.matching.frame_width,
                                height=self.config.matching.frame_height,
                                start_seconds=start,
                                duration_seconds=query_duration_seconds,
                                input_seek=True,
                                reset_timestamps=True,
                            )
                        else:
                            query_path = temp / f"{alias}-{sample_index:03d}-{mode}.mp4"
                            if mode == "reencode":
                                _make_clip(
                                    source_path, query_path, start, query_duration_seconds
                                )
                            else:
                                profile = TRANSCODE_PROFILES[mode]
                                _make_platform_transcode_clip(
                                    source_path,
                                    query_path,
                                    start,
                                    query_duration_seconds,
                                    profile["codec"],
                                    profile["scale"],
                                    profile["bitrate"],
                                    profile["maxrate"],
                                    profile["bufsize"],
                                    profile.get("codec_params_flag"),
                                    profile.get("codec_params"),
                                )
                            frames = extract_grayscale_frames(
                                query_path,
                                fps=2.0,
                                width=self.config.matching.frame_width,
                                height=self.config.matching.frame_height,
                            )
                        features = self.encoder.encode_images(frame for _, frame in frames)
                        query_variant_count += 1
                        for (query_timestamp, _), feature in zip(frames, features, strict=True):
                            expected_source_timestamp = start + float(query_timestamp)
                            measurement = characterize_query_frame(
                                feature, source_indexes, alias,
                                expected_source_timestamp, tolerance_seconds,
                                alignment_usable_tolerance_seconds=(
                                    self.alignment_usable_tolerance_seconds
                                ),
                            )
                            observations.append({
                                "episode": alias,
                                "interval_id": interval_id,
                                "query_mode": mode,
                                "query_timestamp": round(float(query_timestamp), 6),
                                "expected_source_timestamp": round(expected_source_timestamp, 6),
                                "normalized_source_start": round(normalized_start, 8),
                                "source_position_bucket": _position_bucket(normalized_start),
                                "query_subframe_phase_seconds": round(
                                    float(query_timestamp) % SOURCE_GRID_PERIOD_SECONDS, 6
                                ),
                                "source_subframe_phase_seconds": round(
                                    expected_source_timestamp % SOURCE_GRID_PERIOD_SECONDS, 6
                                ),
                                "source_query_temporal_phase": round(
                                    expected_source_timestamp % SOURCE_GRID_PERIOD_SECONDS, 6
                                ),
                                "source_phase_bucket": _phase_bucket(
                                    expected_source_timestamp % SOURCE_GRID_PERIOD_SECONDS
                                ),
                                "interval_start_phase_seconds": round(
                                    interval_start_phase, 6
                                ),
                                **measurement,
                            })

        aggregate = aggregate_observations(observations)
        profiles = _profile_reports(
            observations, bootstrap_iterations, bootstrap_seed
        )
        by_interval = _interval_aggregates(observations)
        phase_distances = [
            min(
                float(item["source_subframe_phase_seconds"]),
                SOURCE_GRID_PERIOD_SECONDS - float(item["source_subframe_phase_seconds"]),
            )
            for item in observations
        ]
        return {
            "schema_version": "retrieval-characterization-v0.3",
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "phase2a_0_retrieval_characterization",
            "measurement_only": True,
            "contains_media_identity": False,
            "configuration": {
                "source_fps": 2.0,
                "query_fps": 2.0,
                "top_k_values": list(TOP_K_VALUES),
                "tolerance_seconds": tolerance_seconds,
                "alignment_usable_tolerance_seconds": self.alignment_usable_tolerance_seconds,
                "alignment_usable_grid_steps": ALIGNMENT_USABLE_GRID_STEPS,
                "samples_per_episode": samples_per_episode,
                "query_duration_seconds": query_duration_seconds,
                "query_profiles": list(modes),
                "source_grid_period_seconds": self.source_grid_period_seconds,
                "source_grid_period_derivation": "1.0 / matching.coarse_fps",
                "readiness_gate_metric": READINESS_GATE_METRIC,
                "phase_bucket_count": PHASE_BUCKET_COUNT,
                "source_position_bucket_count": POSITION_BUCKET_COUNT,
                "sampling": "time-stratified random off-grid starts with shuffled balanced phase strata",
                "encoder": self.encoder.cache_identity(),
                "cluster_bootstrap": {
                    "iterations": bootstrap_iterations,
                    "seed": bootstrap_seed,
                    "level": 0.95,
                },
                "threshold": THRESHOLD,
            },
            "episode_count": len(rows),
            "sampled_interval_count": interval_count,
            "query_variant_count": query_variant_count,
            "aggregate": aggregate,
            "aggregate_scope_note": (
                "aggregate combines all completed query profiles; use `profiles` "
                "for per-profile inference."
            ),
            "profiles": profiles,
            "profile_status": profile_status,
            "by_episode": _episode_aggregates(observations),
            "by_query_mode": _grouped_aggregates(observations, "query_mode"),
            "by_source_position_bucket": _bucket_aggregates(
                observations, "source_position_bucket", _position_bucket_bounds
            ),
            "by_source_phase_bucket": _bucket_aggregates(
                observations, "source_phase_bucket", _phase_bucket_bounds
            ),
            "by_interval": by_interval,
            "sampling_audit": {
                "all_query_frames_off_source_grid": all(distance >= 0.02 for distance in phase_distances),
                "minimum_distance_from_source_grid_seconds": min(phase_distances),
                "source_subframe_phase_range_seconds": {
                    "minimum": min(
                        float(item["source_subframe_phase_seconds"])
                        for item in observations
                    ),
                    "maximum": max(
                        float(item["source_subframe_phase_seconds"])
                        for item in observations
                    ),
                },
                "interval_count_by_episode": {
                    episode: sum(
                        interval["episode"] == episode for interval in by_interval.values()
                    )
                    for episode in sorted({interval["episode"] for interval in by_interval.values()})
                },
                "interval_count_by_source_position_bucket": {
                    bucket: sum(
                        interval["source_position_bucket"] == bucket
                        for interval in by_interval.values()
                    )
                    for bucket in sorted(
                        {interval["source_position_bucket"] for interval in by_interval.values()}
                    )
                },
                "interval_count_by_source_phase_bucket": {
                    bucket: sum(
                        _phase_bucket(float(interval["source_subframe_phase_seconds"])) == bucket
                        for interval in by_interval.values()
                    )
                    for bucket in sorted(
                        {
                            _phase_bucket(float(interval["source_subframe_phase_seconds"]))
                            for interval in by_interval.values()
                        }
                    )
                },
                "distinct_content_position_count": len({
                    (str(item["interval_id"]), float(item["query_timestamp"]))
                    for item in observations
                }),
            },
            "observations": observations,
            "decision": None,
            "decision_note": "Architecture Owner applies the Phase 2A-0 decision tree; harness reports measurement only.",
        }


def markdown_summary(report: dict[str, Any]) -> str:
    def percent(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2%}"

    def number(value: float | int | None, digits: int = 6) -> str:
        if value is None:
            return "n/a"
        if float(value).is_integer():
            return str(int(value))
        return f"{float(value):.{digits}f}"

    def ci_text(value: object) -> str:
        if isinstance(value, list) and len(value) == 2:
            return f"[{number(value[0], 4)}, {number(value[1], 4)}]"
        return "n/a"

    aggregate = report["aggregate"]
    recall = aggregate["retrieval_recall_at_k"]
    score_margin = aggregate["score_margin"]
    lines = [
        "# Phase 2A-0 Retrieval Characterization",
        "",
        "> Measurement only. Candidate GREEN is not an automatic-release state.",
        "",
        f"- Episodes: {report['episode_count']} (anonymized aliases)",
        f"- Sampled intervals: {report['sampled_interval_count']}",
        f"- Query variants: {report.get('query_variant_count', report['sampled_interval_count'])}",
        f"- Query frames: {aggregate['query_frame_count']}",
        f"- recall@1: {percent(recall['1'])}",
        f"- recall@8: {percent(recall['8'])}",
        f"- recall@32: {percent(recall['32'])}",
        f"- recall@128: {percent(recall['128'])}",
        f"- Median correct-source rank: {number(aggregate['median_rank'])}",
        f"- P90 correct-source rank: {number(aggregate['p90_rank'])}",
        "- Score margin p10 / median / p90: "
        f"{number(score_margin['p10'])} / {number(score_margin['median'])} / "
        f"{number(score_margin.get('p90'))}",
        "",
        "",
    ]
    if report.get("profiles"):
        lines.extend([
            "## Profile summary",
            "",
            "Readiness gate metric: `alignment_usable_recall@8` (ADR-0005). "
            "Strict recall is a localization diagnostic and does not gate Temporal Alignment v2.",
            "",
            "Each confidence interval sits next to the band derived from it. "
            "Strict CI never sits next to the alignment-usable band.",
            "",
            "| Profile | Frames | Intervals | Strict R@8 | Strict R@8 95% CI | Strict band | "
            "Usable R@8 | Usable R@8 95% CI | Usable band (gate) | True loss@8 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for profile, block in report["profiles"].items():
            profile_aggregate = block["aggregate"]
            profile_recall = profile_aggregate["retrieval_recall_at_k"]
            profile_usable = profile_aggregate["alignment_usable_recall_at_k"]
            bootstrap = block.get("cluster_bootstrap_confidence_interval", {})
            strict_ci = bootstrap.get("strict", {}).get("8")
            usable_ci = bootstrap.get("alignment_usable", {}).get("8")
            lines.append(
                f"| {profile} | {block['query_frame_count']} | {block['interval_count']} | "
                f"{percent(profile_recall['8'])} | {ci_text(strict_ci)} | "
                f"{block.get('strict_threshold_band', 'UNKNOWN')} | "
                f"{percent(profile_usable['8'])} | {ci_text(usable_ci)} | "
                f"{block.get('alignment_usable_threshold_band', 'UNKNOWN')} | "
                f"{percent(block.get('true_retrieval_loss_rate_at_8'))} |"
            )
        lines.extend([
            "",
            "Strict R@1 / R@32 / R@128 and the alignment-usable equivalents are in the JSON aggregate.",
            "",
            "Interpretation limit: the transcode proxies vary compression, resolution and codec only. "
            "Those perturbations fall below the main sensitivity range of the current low-frequency DCT "
            "descriptor, so these results must not be generalized to crop, mirror, subtitle overlay, "
            "picture-in-picture, or camera capture robustness.",
            "",
        ])
    lines.extend([
        "## Correct-source rank distribution",
        "",
        "| Rank band | Frames |",
        "|---|---:|",
    ])
    lines.extend(
        f"| {rank_band} | {count} |"
        for rank_band, count in aggregate["correct_source_rank_distribution"].items()
    )

    def add_group_table(title: str, groups: dict[str, dict[str, Any]]) -> None:
        lines.extend([
            "",
            f"## {title}",
            "",
            "| Group | Frames | Intervals | R@1 | R@8 | R@32 | R@128 | Median rank | P90 rank | Margin median |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for group, value in groups.items():
            metrics = value.get("aggregate", value)
            grouped_recall = metrics["retrieval_recall_at_k"]
            interval_count = value.get("interval_count", "n/a")
            lines.append(
                f"| {group} | {metrics['query_frame_count']} | {number(interval_count)} | "
                f"{percent(grouped_recall['1'])} | "
                f"{percent(grouped_recall['8'])} | {percent(grouped_recall['32'])} | "
                f"{percent(grouped_recall['128'])} | {number(metrics['median_rank'])} | "
                f"{number(metrics['p90_rank'])} | "
                f"{number(metrics['score_margin']['median'])} |"
            )

    if report.get("by_episode"):
        add_group_table("By episode", report["by_episode"])
    if report.get("by_query_mode"):
        add_group_table("By query mode", report["by_query_mode"])
    if report.get("by_source_position_bucket"):
        add_group_table("By normalized source position", report["by_source_position_bucket"])
    if report.get("by_source_phase_bucket"):
        add_group_table("By source/query sub-frame phase", report["by_source_phase_bucket"])
    if report.get("by_interval"):
        add_group_table("By sampled interval", report["by_interval"])
        lines.extend([
            "",
            "Interval aliases are anonymous. Normalized positions and exact off-grid phases are in the JSON aggregate.",
        ])

    lines.extend([
        "",
        "No original filename, path, SHA-256, or case identity is included.",
        "Architecture Owner must apply the decision tree; this report makes no route decision.",
    ])
    return "\n".join(lines) + "\n"


def write_report(
    report: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
    aggregate_json_path: Path | None = None,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    markdown_path.write_text(markdown_summary(report), encoding="utf-8")
    if aggregate_json_path is not None:
        aggregate_json_path.parent.mkdir(parents=True, exist_ok=True)
        aggregate_json_path.write_text(
            json.dumps(
                anonymized_aggregate_report(report),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
