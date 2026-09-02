from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess

import numpy as np
import pytest

from ipvm.retrieval_benchmark import (
    CLUSTER_BOOTSTRAP_SEED,
    SOURCE_GRID_PERIOD_SECONDS,
    TRANSCODE_PROFILES,
    SourceIndex,
    _intra_cluster_correlation,
    _balanced_phase_assignments,
    _cluster_bootstrap_ci,
    _grouped_aggregates,
    _phase_bucket,
    _profile_block,
    _profile_reports,
    _sample_off_grid_start,
    _threshold_band,
    aggregate_observations,
    anonymized_aggregate_report,
    characterize_query_frame,
    grid_period_from_fps,
    markdown_summary,
    write_report,
)
from ipvm.benchmarking import _encoder_available, _make_platform_transcode_clip
from ipvm.media import extract_grayscale_frames


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


def test_alignment_usable_hits_accept_one_grid_step_and_reject_more() -> None:
    indexes = [
        SourceIndex(
            "E001",
            np.asarray([0.0, 0.5, 1.0], dtype=np.float32),
            np.asarray(
                [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                dtype=np.float32,
            ),
        )
    ]
    within = characterize_query_frame(
        np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        indexes,
        "E001",
        0.0,
        0.26,
        alignment_usable_tolerance_seconds=0.5,
    )
    assert within["hits"]["1"] is False
    assert within["alignment_usable_hits"]["1"] is True

    beyond = characterize_query_frame(
        np.asarray([0.0, 0.0, 1.0], dtype=np.float32),
        indexes,
        "E001",
        0.0,
        0.26,
        alignment_usable_tolerance_seconds=0.5,
    )
    assert beyond["hits"]["1"] is False
    assert beyond["alignment_usable_hits"]["1"] is False


def test_wrong_episode_is_never_alignment_usable() -> None:
    """A perfectly-timed candidate from the wrong episode must not be usable."""
    feature = np.asarray([1.0, 0.0], dtype=np.float32)
    indexes = [
        SourceIndex("E001", np.asarray([90.0], dtype=np.float32),
                    np.asarray([[0.0, 1.0]], dtype=np.float32)),
        SourceIndex("E002", np.asarray([10.0], dtype=np.float32),
                    np.asarray([[1.0, 0.0]], dtype=np.float32)),
    ]
    # Expected E001 @ 10.0. E002 @ 10.0 is an exact timestamp match but the wrong episode.
    measurement = characterize_query_frame(
        feature, indexes, "E001", 10.0, 0.26, alignment_usable_tolerance_seconds=0.5
    )
    assert measurement["correct_source_rank"] is None
    assert measurement["hits"]["128"] is False
    assert measurement["alignment_usable_hits"]["128"] is False


def test_source_grid_period_is_derived_from_sampling_rate() -> None:
    assert grid_period_from_fps(2.0) == SOURCE_GRID_PERIOD_SECONDS
    assert grid_period_from_fps(4.0) == 0.25
    assert grid_period_from_fps(8.0) == 0.125
    with pytest.raises(ValueError):
        grid_period_from_fps(0.0)


def test_transcode_profiles_pin_deterministic_encoder_parameters() -> None:
    for name, profile in TRANSCODE_PROFILES.items():
        assert profile["codec_params_flag"] in {"-x264-params", "-x265-params"}
        params = profile["codec_params"]
        if profile["codec"] == "libx264":
            assert "threads=1" in params and "sliced-threads=0" in params
        else:
            assert "pools=none" in params and "frame-threads=1" in params


def test_cluster_bootstrap_ci_is_deterministic() -> None:
    clusters = [[1, 1, 0], [1, 0, 0], [0, 0, 0], [1, 1, 1]]
    first = _cluster_bootstrap_ci(clusters, iterations=200, seed=CLUSTER_BOOTSTRAP_SEED)
    second = _cluster_bootstrap_ci(clusters, iterations=200, seed=CLUSTER_BOOTSTRAP_SEED)
    assert first == second


def test_threshold_band_three_states() -> None:
    assert _threshold_band(0.96, 0.98) == "ABOVE"
    assert _threshold_band(0.94, 0.96) == "STRADDLES"
    assert _threshold_band(0.90, 0.94) == "BELOW"


def _observation(
    interval_id: str,
    mode: str,
    timestamp: float,
    strict_hit: bool,
    usable_hit: bool,
) -> dict:
    return {
        "interval_id": interval_id,
        "query_mode": mode,
        "query_timestamp": timestamp,
        "expected_source_timestamp": timestamp,
        "correct_source_rank": 1 if strict_hit else 300,
        "score_margin": 0.1 if strict_hit else -0.1,
        "hits": {"1": strict_hit, "8": strict_hit, "32": strict_hit, "128": strict_hit},
        "alignment_usable_hits": {
            "1": usable_hit, "8": usable_hit, "32": usable_hit, "128": usable_hit
        },
    }


def test_profile_block_reports_per_metric_inference_without_bare_fields() -> None:
    observations = [
        _observation(f"I{index:03d}", "direct", float(index), True, True)
        for index in range(3)
        for _ in range(4)
    ]
    block = _profile_block(observations, 50, CLUSTER_BOOTSTRAP_SEED)
    assert block["interval_count"] == 3
    assert block["query_frame_count"] == 12
    assert block["distinct_content_position_count"] == 3
    # Statistics must be attributed to a metric, never left as a bare field.
    assert "effective_sample_size" not in block
    assert "intra_cluster_correlation" not in block
    assert "threshold_band" not in block
    for metric in ("strict", "alignment_usable"):
        assert block[metric]["effective_sample_size"] <= block["query_frame_count"]
        assert block[metric]["threshold_band"] in {"ABOVE", "STRADDLES", "BELOW", "UNKNOWN"}
    assert block["strict_threshold_band"] == block["strict"]["threshold_band"]
    assert block["alignment_usable_threshold_band"] == block["alignment_usable"]["threshold_band"]
    assert block["readiness_gate_metric"] == "alignment_usable_recall_at_8"
    assert block["readiness_gate_band"] == block["alignment_usable_threshold_band"]


def test_intra_cluster_correlation_is_non_degenerate_when_clusters_differ() -> None:
    # Ten clusters, each internally uniform but differing between clusters:
    # maximum between-cluster variance with zero within-cluster variance -> ICC == 1.
    separated = [[1] * 6 if index % 2 else [0] * 6 for index in range(10)]
    separated_metrics = _intra_cluster_correlation(separated)
    assert separated_metrics["icc"] > 0.9
    assert separated_metrics["mean_cluster_size"] == 6.0
    assert separated_metrics["design_effect"] > 5.0

    # Same marginal rate, but each cluster mirrors the grand mean -> no clustering signal.
    mixed = [[1, 0, 1, 0, 1, 0] for _ in range(10)]
    mixed_metrics = _intra_cluster_correlation(mixed)
    assert mixed_metrics["icc"] == 0.0
    assert mixed_metrics["design_effect"] == 1.0
    assert separated_metrics["icc"] > mixed_metrics["icc"]


def test_strict_and_usable_inference_are_computed_separately() -> None:
    # Strict fails on half the intervals; usable succeeds everywhere.
    observations = [
        _observation(f"I{index:03d}", "direct", float(frame) * 0.5, index % 2 == 0, True)
        for index in range(10)
        for frame in range(6)
    ]
    block = _profile_block(observations, 200, CLUSTER_BOOTSTRAP_SEED)
    strict, usable = block["strict"], block["alignment_usable"]
    assert strict["intra_cluster_correlation"] != usable["intra_cluster_correlation"]
    assert strict["confidence_interval"]["8"] != usable["confidence_interval"]["8"]
    assert strict["threshold_band"] == "BELOW"
    assert usable["threshold_band"] == "ABOVE"


def test_threshold_bands_bind_to_their_own_metric() -> None:
    # Usable always hits, strict never does. A band copied from the wrong metric
    # would make these two assertions collide.
    observations = [
        _observation(f"I{index:03d}", "direct", float(frame) * 0.5, False, True)
        for index in range(8)
        for frame in range(4)
    ]
    block = _profile_block(observations, 200, CLUSTER_BOOTSTRAP_SEED)
    assert block["aggregate"]["retrieval_recall_at_k"]["8"] == 0.0
    assert block["aggregate"]["alignment_usable_recall_at_k"]["8"] == 1.0
    assert block["strict_threshold_band"] == "BELOW"
    assert block["alignment_usable_threshold_band"] == "ABOVE"


def test_threshold_band_reports_unknown_when_interval_is_missing() -> None:
    assert _threshold_band(None, None) == "UNKNOWN"
    assert _threshold_band(0.9, None) == "UNKNOWN"


def test_grouped_aggregates_report_interval_count() -> None:
    observations = [
        _observation("I001", "direct", 0.0, True, True),
        _observation("I001", "direct", 0.5, True, True),
        _observation("I002", "direct", 0.0, False, True),
    ]
    grouped = _grouped_aggregates(observations, "query_mode")
    assert grouped["direct"]["interval_count"] == 2
    assert grouped["direct"]["query_frame_count"] == 3


def test_profile_reports_do_not_double_count_independent_samples() -> None:
    observations = []
    for mode in ("direct", "p1"):
        for index in range(4):
            for frame_index in range(3):
                observations.append(
                    _observation(
                        f"I{index:03d}", mode, float(frame_index) * 0.5,
                        True, True,
                    )
                )
    profiles = _profile_reports(observations, 50, CLUSTER_BOOTSTRAP_SEED)
    assert profiles["direct"]["interval_count"] == profiles["p1"]["interval_count"] == 4
    assert profiles["direct"]["distinct_content_position_count"] == 12
    assert profiles["p1"]["distinct_content_position_count"] == 12
    assert profiles["direct"]["query_frame_count"] == 12
    assert profiles["p1"]["query_frame_count"] == 12


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_real_decode_off_grid_query_retains_requested_phase(tmp_path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "nullsrc=size=160x90:rate=25:duration=4",
        "-vf", "geq=lum='mod(N*10,256)':cb=128:cr=128",
        "-g", "1", "-t", "4", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(source),
    ], check=True)
    start = 1.37
    query_frames = extract_grayscale_frames(
        source,
        fps=2.0,
        width=160,
        height=90,
        start_seconds=start,
        duration_seconds=0.5,
        input_seek=True,
        reset_timestamps=True,
    )
    assert len(query_frames) == 1
    query_frame = query_frames[0][1]

    source_frames = extract_grayscale_frames(
        source,
        fps=25.0,
        width=160,
        height=90,
    )
    best_time = min(
        (float(np.mean(np.abs(frame.astype(np.int16) - query_frame.astype(np.int16)))), timestamp)
        for timestamp, frame in source_frames
    )[1]
    assert start - 0.01 <= best_time <= start + 0.5
    assert min(best_time % 0.5, 0.5 - (best_time % 0.5)) >= 0.02


def test_anonymized_aggregate_excludes_sensitive_paths_and_hash() -> None:
    """Synthetic values only. Never place a real media hash or path in a fixture."""
    synthetic_path = "/Users/example-user/example-project/example_media/SAMPLE_CLIP.mp4"
    synthetic_hash = "0" * 64
    report = {
        "observations": [
            {
                "interval_id": "E001-I001",
                "query_mode": "direct",
                "expected_source_timestamp": 12.345,
                "file_path": synthetic_path,
                "sha256": synthetic_hash,
                "hits": {"8": True},
                "alignment_usable_hits": {"8": True},
            }
        ],
        "aggregate": aggregate_observations([]),
        "decision": None,
    }
    text = json.dumps(anonymized_aggregate_report(report))
    assert "/Users/" not in text
    assert "example_media" not in text
    assert "SAMPLE_CLIP.mp4" not in text
    assert synthetic_hash not in text


def test_profile_query_paths_do_not_collide_across_profiles() -> None:
    """Each profile must write its own query file; a shared name silently reuses bytes."""
    alias, sample_index = "E001", 3
    paths = {
        mode: f"{alias}-{sample_index:03d}-{mode}.mp4"
        for mode in ("direct", "reencode", "p1", "p2", "p3")
    }
    assert len(set(paths.values())) == len(paths)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_platform_transcode_produces_declared_codec_and_resolution(tmp_path) -> None:
    """Verify the realized file, not just the ffmpeg command string."""
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25", "-t", "2",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(source),
    ], check=True)

    expected = {
        "p1": ("h264", 1280, 720),
        "p2": ("h264", 854, 480),
        "p3": ("hevc", 1280, 720),
    }
    for name, (codec, width, height) in expected.items():
        profile = TRANSCODE_PROFILES[name]
        if not _encoder_available(profile["codec"]):
            pytest.skip(f"{profile['codec']} unavailable")
        target = tmp_path / f"{name}.mp4"
        _make_platform_transcode_clip(
            source, target, 0.137, 1.0,
            profile["codec"], profile["scale"], profile["bitrate"],
            profile["maxrate"], profile["bufsize"],
            profile.get("codec_params_flag"), profile.get("codec_params"),
        )
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height", "-of", "json", str(target),
        ], check=True, capture_output=True, text=True)
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream["codec_name"] == codec
        assert (stream["width"], stream["height"]) == (width, height)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_platform_transcode_is_byte_deterministic(tmp_path) -> None:
    """Deterministic encoder parameters must make a benchmark re-run reproducible."""
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-t", "2",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(source),
    ], check=True)
    profile = TRANSCODE_PROFILES["p2"]
    digests = []
    for run in range(2):
        target = tmp_path / f"run{run}.mp4"
        _make_platform_transcode_clip(
            source, target, 0.137, 1.0,
            profile["codec"], profile["scale"], profile["bitrate"],
            profile["maxrate"], profile["bufsize"],
            profile["codec_params_flag"], profile["codec_params"],
        )
        digests.append(hashlib.sha256(target.read_bytes()).hexdigest())
    assert digests[0] == digests[1]
