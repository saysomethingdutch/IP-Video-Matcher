from __future__ import annotations

import json
import math
import platform
import random
import resource
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .database import Database
from .indexer import IndexingResult, OriginalIndexer, episode_from_path
from .matching import AnalysisResult, VideoMatcher
from .media import find_video_files, probe_media
from .models import MatchStatus, SegmentMatch


@dataclass(frozen=True)
class GroundTruthSegment:
    accused_start: float
    accused_end: float
    source_episode: str
    source_start: float
    source_end: float
    speed_ratio: float
    equivalence_group: str | None = None


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    transformation: str
    phase: int
    video_path: str
    expected_status: str
    segments: list[GroundTruthSegment]
    case_type: str = "matching"
    originals_dir: str | None = None


def _ffmpeg(arguments: list[str]) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required for benchmark generation") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", errors="replace")) from exc


def _make_clip(source: Path, target: Path, start: float, duration: float, vf: str | None = None) -> None:
    args = ["-ss", f"{start:.3f}", "-i", str(source)]
    filters = [f"trim=duration={duration:.3f}", "setpts=PTS-STARTPTS"]
    if vf:
        filters.append(vf)
    args += ["-vf", ",".join(filters)]
    args += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(target)]
    _ffmpeg(args)


def _make_platform_transcode_clip(
    source: Path,
    target: Path,
    start: float,
    duration: float,
    codec: str,
    scale: str,
    bitrate: str,
    maxrate: str,
    bufsize: str,
    codec_params_flag: str | None = None,
    codec_params: str | None = None,
) -> None:
    """Create a platform-like transcode proxy without cropping or changing speed.

    `codec_params` pins the encoder to deterministic single-threaded settings so a
    benchmark re-run reproduces identical bytes; multi-threaded x264/x265 is not
    bit-deterministic.
    """
    args = ["-ss", f"{start:.3f}", "-i", str(source)]
    filters = [
        f"trim=duration={duration:.3f}",
        "setpts=PTS-STARTPTS",
        f"scale={scale}:flags=area",
    ]
    args += ["-vf", ",".join(filters)]
    args += [
        "-an",
        "-threads", "1",
        "-c:v", codec,
        "-preset", "veryfast",
        "-b:v", bitrate,
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-pix_fmt", "yuv420p",
    ]
    if codec_params_flag and codec_params:
        args += [codec_params_flag, codec_params]
    args += [str(target)]
    _ffmpeg(args)


def _encoder_available(codec: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return codec in result.stdout


def _concat(clips: list[Path], target: Path, crf: int = 18) -> None:
    args: list[str] = []
    for clip in clips:
        args += ["-i", str(clip)]
    inputs = "".join(f"[{index}:v]" for index in range(len(clips)))
    args += [
        "-filter_complex", f"{inputs}concat=n={len(clips)}:v=1:a=0[outv]",
        "-map", "[outv]", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        str(target),
    ]
    _ffmpeg(args)


def create_demo_originals(directory: Path, duration: float = 11.5) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    filters = [
        "testsrc2=size=640x360:rate=24",
        "testsrc=size=640x360:rate=24",
        "testsrc2=size=640x360:rate=24,negate",
    ]
    for index, source_filter in enumerate(filters, start=1):
        target = directory / f"EP{index:02d}.mp4"
        _ffmpeg([
            "-f", "lavfi", "-i", source_filter, "-t", str(duration),
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(target),
        ])
        results.append(target)

    parts = directory / "_parts"
    parts.mkdir(parents=True, exist_ok=True)
    unique4 = parts / "unique4.mp4"
    unique5 = parts / "unique5.mp4"
    shared20 = parts / "shared-crf20.mp4"
    shared26 = parts / "shared-crf26.mp4"
    for target, crf in ((shared20, 20), (shared26, 26)):
        _ffmpeg([
            "-f", "lavfi", "-i", "mandelbrot=size=640x360:rate=24",
            "-t", "10", "-an", "-c:v", "libx264", "-crf", str(crf),
            "-pix_fmt", "yuv420p", str(target),
        ])
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24,negate",
        "-t", "14", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(unique4),
    ])
    _ffmpeg([
        "-f", "lavfi", "-i", "smptebars=size=640x360:rate=24",
        "-t", "14", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(unique5),
    ])
    for episode, shared, unique, crf in (
        (4, shared20, unique4, 20), (5, shared26, unique5, 26)
    ):
        target = directory / f"EP{episode:02d}.mp4"
        _concat([shared, unique], target, crf=crf)
        results.append(target)
    return results


class SyntheticBenchmarkGenerator:
    def __init__(self, originals: list[Path], output_dir: Path, seed: int = 42):
        if len(originals) < 5:
            raise ValueError("Synthetic safety benchmark requires five original episodes")
        self.originals = originals
        self.output_dir = output_dir
        self.seed = seed

    def generate(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        clips_dir = self.output_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        randomizer = random.Random(self.seed)
        by_episode = {episode_from_path(path): path for path in self.originals}
        cases: list[BenchmarkCase] = []
        episodes = [f"EP{index:02d}" for index in range(1, 6)]

        def independent_interval(episode: str, duration: float) -> tuple[Path, float]:
            source = by_episode[episode]
            source_duration = probe_media(source).duration
            lower = 10.613 if episode in {"EP04", "EP05"} else 0.613
            upper = source_duration - duration - 0.537
            start = round(randomizer.uniform(lower, upper), 3)
            phase = start % (1.0 / 2.0)
            if min(phase, 0.5 - phase) < 0.025:
                start = round(start + 0.073, 3)
            return source, start

        transformations = [
            ("A", "off_grid_direct_cut", None, 1.0, 1),
            ("B", "horizontal_mirror", "hflip", 1.0, 2),
            ("C", "speed_1.25x", None, 1.25, 2),
            ("D", "speed_0.8x", None, 0.8, 2),
            ("E", "center_crop", "crop=iw*0.75:ih*0.75,scale=640:360", 1.0, 2),
            ("F", "vertical_crop", "crop=ih*9/16:ih,scale=360:640", 1.0, 2),
            ("G", "subtitle_overlay", "drawbox=x=40:y=ih-70:w=iw-80:h=45:color=black@0.7:t=fill", 1.0, 2),
            ("H", "mirror_speed_subtitle", "hflip,drawbox=x=40:y=ih-70:w=iw-80:h=45:color=black@0.7:t=fill", 1.25, 2),
        ]
        for letter, transformation, vf, speed, phase_number in transformations:
            for sample_index, episode in enumerate(episodes, start=1):
                duration = round(randomizer.uniform(3.337, 4.613), 3)
                source, start = independent_interval(episode, duration)
                case_id = f"CASE_{letter}_{sample_index:02d}"
                target = self.output_dir / f"{case_id}.mp4"
                filters = [vf] if vf else []
                if speed != 1.0:
                    filters.append(f"setpts=PTS/{speed}")
                _make_clip(source, target, start, duration, ",".join(filters) or None)
                accused_duration = duration / speed
                cases.append(BenchmarkCase(
                    case_id, transformation, phase_number, str(target.resolve()),
                    MatchStatus.MATCH_FOUND.value,
                    [GroundTruthSegment(
                        0.0, accused_duration, episode, start, start + duration, speed,
                    )],
                ))

        for sample_index in range(1, 6):
            first_ep = episodes[(sample_index - 1) % 5]
            second_ep = episodes[sample_index % 5]
            i_duration = round(randomizer.uniform(3.337, 4.173), 3)
            first, first_start = independent_interval(first_ep, i_duration)
            second, second_start = independent_interval(second_ep, i_duration)
            i1 = clips_dir / f"i{sample_index}-1.mp4"
            i2 = clips_dir / f"i{sample_index}-2.mp4"
            _make_clip(first, i1, first_start, i_duration)
            _make_clip(second, i2, second_start, i_duration)
            case_id = f"CASE_I_{sample_index:02d}"
            case_i_path = self.output_dir / f"{case_id}.mp4"
            _concat([i1, i2], case_i_path)
            cases.append(BenchmarkCase(
                case_id, "off_grid_multi_episode_concat", 1, str(case_i_path.resolve()),
                MatchStatus.MATCH_FOUND.value,
                [
                    GroundTruthSegment(0.0, i_duration, first_ep, first_start, first_start + i_duration, 1.0),
                    GroundTruthSegment(i_duration, i_duration * 2, second_ep, second_start, second_start + i_duration, 1.0),
                ],
            ))

        for sample_index in range(1, 6):
            first_ep = episodes[(sample_index + 1) % 5]
            second_ep = episodes[(sample_index + 2) % 5]
            j_duration = round(randomizer.uniform(3.337, 3.973), 3)
            first, first_start = independent_interval(first_ep, j_duration)
            second, second_start = independent_interval(second_ep, j_duration)
            j1 = clips_dir / f"j{sample_index}-1.mp4"
            j2 = clips_dir / f"j{sample_index}-2.mp4"
            unrelated = clips_dir / f"unrelated-{sample_index}.mp4"
            _make_clip(first, j1, first_start, j_duration)
            _make_clip(second, j2, second_start, j_duration)
            insert_duration = round(randomizer.uniform(1.613, 2.237), 3)
            _ffmpeg([
                "-f", "lavfi", "-i", "color=c=magenta:size=640x360:rate=24",
                "-t", str(insert_duration), "-an", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(unrelated),
            ])
            case_id = f"CASE_J_{sample_index:02d}"
            case_j_path = self.output_dir / f"{case_id}.mp4"
            _concat([j1, unrelated, j2], case_j_path)
            cases.append(BenchmarkCase(
                case_id, "unrelated_insert", 2, str(case_j_path.resolve()),
                MatchStatus.MATCH_FOUND.value,
                [
                    GroundTruthSegment(0.0, j_duration, first_ep, first_start, first_start + j_duration, 1.0),
                    GroundTruthSegment(j_duration + insert_duration, j_duration * 2 + insert_duration, second_ep, second_start, second_start + j_duration, 1.0),
                ],
            ))

        # Twenty additional off-grid direct/re-encode clips make the Phase-1 gate a real regression set.
        gate_sources = [by_episode["EP01"], by_episode["EP02"], by_episode["EP03"]]
        for index in range(20):
            source = gate_sources[index % len(gate_sources)]
            episode = episode_from_path(source)
            assert episode is not None
            clip_duration = round(randomizer.uniform(3.337, 5.713), 3)
            start = round(randomizer.uniform(0.613, probe_media(source).duration - clip_duration - 0.537), 3)
            target = self.output_dir / f"P1_{index + 1:03d}.mp4"
            _make_clip(source, target, start, clip_duration)
            cases.append(BenchmarkCase(
                f"P1_{index + 1:03d}", "off_grid_direct_reencode", 1,
                str(target.resolve()), MatchStatus.MATCH_FOUND.value,
                [GroundTruthSegment(0.0, clip_duration, episode, start, start + clip_duration, 1.0)],
            ))

        negative = self.output_dir / "CASE_K.mp4"
        _ffmpeg([
            "-f", "lavfi", "-i", "life=size=640x360:rate=24:ratio=0.35",
            "-t", "6.217", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(negative),
        ])
        cases.append(BenchmarkCase(
            "CASE_K", "pure_negative", 1, str(negative.resolve()),
            MatchStatus.MATCH_NOT_FOUND.value, [],
        ))

        ambiguity = self.output_dir / "CASE_L.mp4"
        ambiguity_start, ambiguity_duration = 1.337, 6.113
        _make_clip(by_episode["EP04"], ambiguity, ambiguity_start, ambiguity_duration)
        cases.append(BenchmarkCase(
            "CASE_L", "independent_crf_duplicate_cross_episode", 1, str(ambiguity.resolve()),
            MatchStatus.AMBIGUOUS.value,
            [
                GroundTruthSegment(0.0, ambiguity_duration, "EP04", ambiguity_start, ambiguity_start + ambiguity_duration, 1.0, "shared_intro"),
                GroundTruthSegment(0.0, ambiguity_duration, "EP05", ambiguity_start, ambiguity_start + ambiguity_duration, 1.0, "shared_intro"),
            ],
        ))

        fixtures = self.output_dir / "fixtures"
        incomplete_originals = fixtures / "incomplete_originals"
        incomplete_originals.mkdir(parents=True, exist_ok=True)
        complete_source = fixtures / "EP91-complete.mp4"
        _ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-t", "20", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(complete_source),
        ])
        incomplete_source = incomplete_originals / "EP91.mp4"
        complete_bytes = complete_source.read_bytes()
        incomplete_source.write_bytes(complete_bytes[: int(len(complete_bytes) * 0.45)])
        incomplete_query = self.output_dir / "CASE_M.mp4"
        _make_clip(complete_source, incomplete_query, 12.137, 4.317)
        cases.append(BenchmarkCase(
            "CASE_M", "faststart_incomplete_original", 1, str(incomplete_query.resolve()),
            MatchStatus.INCOMPLETE_INDEX.value, [], "index_integrity",
            str(incomplete_originals.resolve()),
        ))

        manifest = {
            "schema_version": "0.3",
            "seed": self.seed,
            "generated_at": datetime.now(UTC).isoformat(),
            "ground_truth_schema": "synthetic-and-golden-v0.3",
            "cases": [
                {**asdict(case), "segments": [asdict(segment) for segment in case.segments]}
                for case in cases
            ],
        }
        manifest_path = self.output_dir / "ground_truth.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8"
        )
        return manifest_path


def _overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _union_seconds(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    start, end = ordered[0]
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _source_match(expected: dict[str, Any], predicted: SegmentMatch, tolerance: float) -> bool:
    return (
        predicted.source_episode == expected["source_episode"]
        and abs(predicted.source_start - expected["source_start"]) <= tolerance
        and abs(predicted.source_end - expected["source_end"]) <= tolerance
    )


def _duration_slope_consistent(
    expected: dict[str, Any], predicted: SegmentMatch, tolerance: float
) -> bool:
    accused_span = predicted.accused_end - predicted.accused_start
    source_span = predicted.source_end - predicted.source_start
    expected_speed = float(expected.get("speed_ratio", 1.0))
    if accused_span <= 0 or source_span <= 0:
        return False
    if abs(source_span - accused_span * expected_speed) > tolerance:
        return False
    effective_span = min(accused_span, source_span)
    if effective_span <= 3.0:
        return predicted.speed_ratio is None
    if predicted.speed_ratio is None:
        return False
    return abs(source_span - accused_span * predicted.speed_ratio) <= tolerance


def evaluate_segments(
    truth: list[dict[str, Any]],
    predictions: list[SegmentMatch],
    status: str = MatchStatus.MATCH_FOUND.value,
    alternatives: list[SegmentMatch] | None = None,
    tolerance_seconds: float = 1.0,
) -> dict[str, Any]:
    regular_truth = [item for item in truth if not item.get("equivalence_group")]
    ambiguous_truth = [item for item in truth if item.get("equivalence_group")]
    if ambiguous_truth:
        candidate_green = sum(item.candidate_tier == "GREEN" for item in predictions)
        review_required = sum(item.review_tier == "REVIEW_REQUIRED" for item in predictions)
        return {
            "ground_truth_segments": 0,
            "predicted_segments": len(predictions),
            "episode_recalled_segments": 0,
            "source_aware_recalled_segments": 0,
            "episode_recall": None,
            "source_aware_recall_at_1s": None,
            "segment_precision_at_1s": None,
            "false_positive_segments": len(predictions),
            "false_positive_seconds": _union_seconds([
                (item.accused_start, item.accused_end) for item in predictions
            ]),
            "episode_correctness": None,
            "accused_boundary_mae_seconds": None,
            "source_boundary_mae_seconds": None,
            "speed_ratio_mae": None,
            "green_precision": None if not candidate_green else 0.0,
            "green_recall": None,
            "review_required_precision": None if not review_required else 0.0,
            "candidate_green_predictions": candidate_green,
            "correct_candidate_green_predictions": 0,
            "review_required_predictions": review_required,
            "correct_review_required_predictions": 0,
            "unmatched_ground_truth": [],
            "unmatched_predictions": [item.to_dict() for item in predictions],
            "ambiguous_predictions": 1 if status == MatchStatus.AMBIGUOUS.value else 0,
            "ambiguity_expected": True,
            "ambiguity_correct": (
                status == MatchStatus.AMBIGUOUS.value
                and len(predictions) + len(alternatives or []) >= 2
            ),
            "boundary_evaluated_segments": 0,
            "boundary_missed_segments": 0,
        }

    episode_recalled: set[int] = set()
    source_recalled: set[int] = set()
    true_predictions: set[int] = set()
    overlapping_predictions = 0
    correct_episode_predictions = 0
    accused_errors: list[float] = []
    source_errors: list[float] = []
    speed_errors: list[float] = []

    assignment_edges: list[tuple[tuple[Any, ...], int, int]] = []
    for prediction_index, prediction in enumerate(predictions):
        for truth_index, expected in enumerate(regular_truth):
            expected_duration = expected["accused_end"] - expected["accused_start"]
            overlap_ratio = _overlap(
                prediction.accused_start, prediction.accused_end,
                expected["accused_start"], expected["accused_end"],
            ) / max(expected_duration, 1e-9)
            if overlap_ratio >= 0.5:
                source_match = _source_match(expected, prediction, tolerance_seconds)
                physical = _duration_slope_consistent(expected, prediction, tolerance_seconds)
                episode_correct = prediction.source_episode == expected["source_episode"]
                boundary_error = (
                    abs(prediction.accused_start - expected["accused_start"])
                    + abs(prediction.accused_end - expected["accused_end"])
                    + abs(prediction.source_start - expected["source_start"])
                    + abs(prediction.source_end - expected["source_end"])
                )
                priority = (
                    int(source_match and physical), int(episode_correct), overlap_ratio,
                    -boundary_error, -prediction_index, -truth_index,
                )
                assignment_edges.append((priority, prediction_index, truth_index))

    assigned_predictions: set[int] = set()
    assigned_truth: set[int] = set()
    assignments: list[tuple[int, int]] = []
    for _, prediction_index, truth_index in sorted(
        assignment_edges, key=lambda item: item[0], reverse=True
    ):
        if prediction_index in assigned_predictions or truth_index in assigned_truth:
            continue
        assigned_predictions.add(prediction_index)
        assigned_truth.add(truth_index)
        assignments.append((prediction_index, truth_index))

    physically_inconsistent: set[int] = set()
    for prediction_index, truth_index in assignments:
        prediction = predictions[prediction_index]
        expected = regular_truth[truth_index]
        overlapping_predictions += 1
        accused_errors.extend([
            abs(prediction.accused_start - expected["accused_start"]),
            abs(prediction.accused_end - expected["accused_end"]),
        ])
        if prediction.source_episode == expected["source_episode"]:
            correct_episode_predictions += 1
            episode_recalled.add(truth_index)
            source_errors.extend([
                abs(prediction.source_start - expected["source_start"]),
                abs(prediction.source_end - expected["source_end"]),
            ])
            physical = _duration_slope_consistent(expected, prediction, tolerance_seconds)
            if not physical:
                physically_inconsistent.add(prediction_index)
            if (
                prediction.speed_ratio is not None
                and float(expected.get("speed_ratio", 1.0)) != 1.0
            ):
                speed_errors.append(
                    abs(prediction.speed_ratio - float(expected.get("speed_ratio", 1.0)))
                )
            if _source_match(expected, prediction, tolerance_seconds) and physical:
                source_recalled.add(truth_index)
                true_predictions.add(prediction_index)

    false_predictions = [
        prediction for index, prediction in enumerate(predictions) if index not in true_predictions
    ]
    total_truth = len(regular_truth)
    total_predictions = len(predictions)
    unmatched_truth = [
        expected for index, expected in enumerate(regular_truth) if index not in source_recalled
    ]
    candidate_green_indices = {
        index for index, item in enumerate(predictions) if item.candidate_tier == "GREEN"
    }
    review_required_indices = {
        index for index, item in enumerate(predictions)
        if item.review_tier == "REVIEW_REQUIRED"
    }
    correct_green = len(candidate_green_indices & true_predictions)
    correct_review_required = len(review_required_indices & true_predictions)
    return {
        "ground_truth_segments": total_truth,
        "predicted_segments": total_predictions,
        "episode_recalled_segments": len(episode_recalled),
        "source_aware_recalled_segments": len(source_recalled),
        "episode_recall": len(episode_recalled) / total_truth if total_truth else None,
        "source_aware_recall_at_1s": len(source_recalled) / total_truth if total_truth else None,
        "segment_precision_at_1s": (
            len(true_predictions) / total_predictions if total_predictions else (1.0 if not total_truth else 0.0)
        ),
        "false_positive_segments": len(false_predictions),
        "false_positive_seconds": round(_union_seconds([
            (item.accused_start, item.accused_end) for item in false_predictions
        ]), 6),
        "episode_correctness": (
            correct_episode_predictions / overlapping_predictions if overlapping_predictions else None
        ),
        "accused_boundary_mae_seconds": (
            sum(accused_errors) / len(accused_errors) if accused_errors else None
        ),
        "source_boundary_mae_seconds": (
            sum(source_errors) / len(source_errors) if source_errors else None
        ),
        "speed_ratio_mae": sum(speed_errors) / len(speed_errors) if speed_errors else None,
        "green_precision": (
            correct_green / len(candidate_green_indices) if candidate_green_indices else None
        ),
        "green_recall": correct_green / total_truth if total_truth else None,
        "review_required_precision": (
            correct_review_required / len(review_required_indices)
            if review_required_indices else None
        ),
        "candidate_green_predictions": len(candidate_green_indices),
        "correct_candidate_green_predictions": correct_green,
        "review_required_predictions": len(review_required_indices),
        "correct_review_required_predictions": correct_review_required,
        "physical_inconsistent_predictions": len(physically_inconsistent),
        "unmatched_ground_truth": unmatched_truth,
        "unmatched_predictions": [item.to_dict() for item in false_predictions],
        "ambiguous_predictions": 1 if status == MatchStatus.AMBIGUOUS.value else 0,
        "ambiguity_expected": False,
        "ambiguity_correct": None,
        "boundary_evaluated_segments": len(source_errors) // 2,
        "boundary_missed_segments": total_truth - len(episode_recalled),
        "overlapping_predictions": overlapping_predictions,
        "correct_episode_predictions": correct_episode_predictions,
        "raw_accused_boundary_errors": accused_errors,
        "raw_source_boundary_errors": source_errors,
        "raw_speed_ratio_errors": speed_errors,
    }


def resource_snapshot() -> dict[str, int]:
    self_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    child_rss = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    factor = 1 if platform.system() == "Darwin" else 1024
    return {"main_process_peak_bytes": self_rss * factor, "child_process_peak_bytes": child_rss * factor}


class BenchmarkRunner:
    def __init__(self, config: AppConfig, database: Database):
        self.config = config
        self.database = database

    def run(
        self,
        manifest_path: Path,
        phase1_only: bool = True,
        indexing_results: list[IndexingResult] | None = None,
        index_resource_before: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        matcher = VideoMatcher(self.config, self.database)
        case_results: list[dict[str, Any]] = []
        analysis_resource_before = resource_snapshot()
        for case in manifest["cases"]:
            if phase1_only and int(case["phase"]) != 1:
                continue
            if case.get("case_type") == "index_integrity":
                result = self._run_incomplete_index_case(case, manifest_path.parent)
            else:
                result = matcher.analyze_file(
                    f"BENCH_{case['case_id']}", Path(case["video_path"]), persist=False
                )
            metrics = evaluate_segments(
                case["segments"], result.segments, result.status, result.alternatives
            )
            case_results.append({
                "case_id": case["case_id"],
                "transformation": case["transformation"],
                "phase": case["phase"],
                "expected_status": case["expected_status"],
                "actual_status": result.status,
                "status_correct": result.status == case["expected_status"],
                "metrics": metrics,
                "analysis_seconds": result.elapsed_seconds,
                "video_duration_seconds": (
                    probe_media(Path(case["video_path"])).duration
                    if Path(case["video_path"]).exists() else None
                ),
                "predictions": [segment.to_dict() for segment in result.segments],
                "alternative_candidates": [segment.to_dict() for segment in result.alternatives],
                "reason": result.reason,
            })
        analysis_resource_after = resource_snapshot()
        aggregate = self._aggregate(case_results)
        index_results = indexing_results or []
        indexed_media_seconds = sum(item.duration for item in index_results)
        index_elapsed = sum(item.elapsed_seconds for item in index_results)
        analysis_seconds = sum(item["analysis_seconds"] for item in case_results)
        analysis_media_seconds = sum(
            item["video_duration_seconds"] or 0.0 for item in case_results
            if item["transformation"] != "faststart_incomplete_original"
        )
        index_resource_after = analysis_resource_before
        performance = {
            "index_scope": {
                "asset_count": len(index_results),
                "media_duration_seconds": indexed_media_seconds,
                "elapsed_seconds": index_elapsed,
                "cold_assets": sum(not item.cached for item in index_results),
                "warm_cache_assets": sum(item.cached for item in index_results),
                "per_asset": [
                    {**asdict(item), "file_path": Path(item.file_path).name}
                    for item in index_results
                ],
            },
            "analysis_scope": {
                "case_count": len(case_results),
                "media_duration_seconds": analysis_media_seconds,
                "elapsed_seconds": analysis_seconds,
                "rtf": analysis_seconds / analysis_media_seconds if analysis_media_seconds else None,
            },
            "memory_measurement": {
                "method": "resource.getrusage peak RSS; main and all completed ffmpeg children reported separately",
                "index_before": index_resource_before,
                "index_after": index_resource_after,
                "analysis_before": analysis_resource_before,
                "analysis_after": analysis_resource_after,
                "observed_peak_lower_bound_bytes": max(
                    analysis_resource_after["main_process_peak_bytes"],
                    analysis_resource_after["child_process_peak_bytes"],
                ),
                "conservative_combined_upper_bound_bytes": (
                    analysis_resource_after["main_process_peak_bytes"]
                    + analysis_resource_after["child_process_peak_bytes"]
                ),
            },
            "normalization_warning": (
                "Synthetic low-resolution timings are raw regression measurements and must not be "
                "extrapolated to real 30-hour drama indexes."
            ),
        }
        gate = self._gate(aggregate, case_results) if phase1_only else None
        report = {
            "schema_version": "0.3",
            "scope": "phase1_5_gate" if phase1_only else "a_to_m_diagnostic",
            "created_at": datetime.now(UTC).isoformat(),
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "synthetic_only": True,
            },
            "metric_definitions": {
                "episode_recall": "GT segments with an accused-overlapping prediction from the correct episode / GT segments",
                "source_aware_recall_at_1s": "episode recall plus both source endpoints within 1 second",
                "segment_precision_at_1s": "predicted segments satisfying the source-aware criterion / predicted segments",
                "green_precision": "source-aware correct candidate-GREEN predictions / candidate-GREEN predictions",
                "green_recall": "source-aware correct candidate-GREEN predictions / GT segments",
                "tier_safety_note": (
                    "candidate GREEN is diagnostic only; effective review_tier is REVIEW_REQUIRED "
                    "until external pixel verification exists"
                ),
            },
            "green_auto_release_enabled": False,
            "future_green_auto_release_gate": {"required_green_precision": 1.0},
            "aggregate": aggregate,
            "transformation_groups": self._transformation_groups(case_results),
            "subsets": {
                "a_to_j": self._aggregate([
                    item for item in case_results
                    if any(item["case_id"].startswith(f"CASE_{letter}_") for letter in "ABCDEFGHIJ")
                ]),
                "off_grid_phase1": self._aggregate([
                    item for item in case_results
                    if item["transformation"] in {
                        "off_grid_direct_cut", "off_grid_multi_episode_concat",
                        "off_grid_direct_reencode",
                    }
                ]),
                "safety_status_cases": {
                    item["case_id"]: {
                        "expected_status": item["expected_status"],
                        "actual_status": item["actual_status"],
                        "status_correct": item["status_correct"],
                    }
                    for item in case_results
                    if item["case_id"] in {"CASE_K", "CASE_L", "CASE_M"}
                },
            },
            "gate": gate,
            "performance": performance,
            "cases": case_results,
        }
        return report

    def _run_incomplete_index_case(self, case: dict[str, Any], manifest_dir: Path) -> AnalysisResult:
        runtime = manifest_dir / "incomplete_runtime"
        paths = replace(
            self.config.paths,
            originals=Path(case["originals_dir"]),
            originals_metadata=Path(case["originals_dir"]) / "metadata.json",
            workspace=runtime / "workspace",
            output=runtime / "output",
            database=runtime / "workspace" / "db" / "ipvm.db",
        )
        isolated = replace(self.config, paths=paths)
        database = Database(paths.database)
        database.initialize(isolated.paths.root)
        OriginalIndexer(isolated, database).index_all(force=True)
        return VideoMatcher(isolated, database).analyze_file(
            f"BENCH_{case['case_id']}", Path(case["video_path"]), persist=False
        )

    @staticmethod
    def _aggregate(case_results: list[dict[str, Any]]) -> dict[str, Any]:
        normal = [item for item in case_results if not item["metrics"]["ambiguity_expected"]]
        truth_count = sum(item["metrics"]["ground_truth_segments"] for item in normal)
        prediction_count = sum(item["metrics"]["predicted_segments"] for item in normal)
        episode_recalled = sum(item["metrics"]["episode_recalled_segments"] for item in normal)
        source_recalled = sum(item["metrics"]["source_aware_recalled_segments"] for item in normal)
        false_positive_segments = sum(item["metrics"]["false_positive_segments"] for item in normal)
        overlapping_predictions = sum(item["metrics"].get("overlapping_predictions", 0) for item in normal)
        correct_episode_predictions = sum(
            item["metrics"].get("correct_episode_predictions", 0) for item in normal
        )
        accused_errors = [
            error for item in normal for error in item["metrics"].get("raw_accused_boundary_errors", [])
        ]
        source_errors = [
            error for item in normal for error in item["metrics"].get("raw_source_boundary_errors", [])
        ]
        speed_errors = [
            error for item in normal for error in item["metrics"].get("raw_speed_ratio_errors", [])
        ]
        ambiguous = [item for item in case_results if item["metrics"]["ambiguity_expected"]]
        candidate_green = sum(
            item["metrics"].get("candidate_green_predictions", 0) for item in normal
        )
        correct_green = sum(
            item["metrics"].get("correct_candidate_green_predictions", 0) for item in normal
        )
        review_required = sum(
            item["metrics"].get("review_required_predictions", 0) for item in normal
        )
        correct_review_required = sum(
            item["metrics"].get("correct_review_required_predictions", 0) for item in normal
        )
        predicted_segment_payloads = [
            prediction for item in normal for prediction in item.get("predictions", [])
        ]
        residual_rms_values = [
            float(item["residual_rms_seconds"])
            for item in predicted_segment_payloads
            if item.get("residual_rms_seconds") is not None
        ]
        inlier_ratios = [
            float(item["inlier_ratio"])
            for item in predicted_segment_payloads
            if item.get("inlier_ratio") is not None
        ]
        segment_count_errors = [
            abs(
                int(item["metrics"]["predicted_segments"])
                - int(item["metrics"]["ground_truth_segments"])
            )
            for item in normal
        ]
        return {
            "ground_truth_segments": truth_count,
            "predicted_segments": prediction_count,
            "episode_recall": episode_recalled / truth_count if truth_count else None,
            "episode_correctness": (
                correct_episode_predictions / overlapping_predictions
                if overlapping_predictions else None
            ),
            "source_aware_recall_at_1s": source_recalled / truth_count if truth_count else None,
            "segment_precision_at_1s": (
                (prediction_count - false_positive_segments) / prediction_count
                if prediction_count else (1.0 if truth_count == 0 else 0.0)
            ),
            "false_positive_segments": false_positive_segments,
            "false_positive_seconds": sum(
                item["metrics"]["false_positive_seconds"] for item in normal
            ),
            "accused_boundary_mae_seconds": (
                sum(accused_errors) / len(accused_errors) if accused_errors else None
            ),
            "source_boundary_mae_seconds": (
                sum(source_errors) / len(source_errors) if source_errors else None
            ),
            "speed_ratio_mae": sum(speed_errors) / len(speed_errors) if speed_errors else None,
            "green_precision": correct_green / candidate_green if candidate_green else None,
            "green_recall": correct_green / truth_count if truth_count else None,
            "review_required_precision": (
                correct_review_required / review_required if review_required else None
            ),
            "candidate_green_predictions": candidate_green,
            "review_required_predictions": review_required,
            "ambiguity_rate": (
                sum(item["actual_status"] == MatchStatus.AMBIGUOUS.value for item in case_results)
                / len(case_results) if case_results else None
            ),
            "mean_absolute_segment_count_error": (
                sum(segment_count_errors) / len(segment_count_errors)
                if segment_count_errors else None
            ),
            "mean_residual_rms_seconds": (
                sum(residual_rms_values) / len(residual_rms_values)
                if residual_rms_values else None
            ),
            "mean_inlier_ratio": (
                sum(inlier_ratios) / len(inlier_ratios) if inlier_ratios else None
            ),
            "boundary_evaluated_segments": len(source_errors) // 2,
            "boundary_missed_segments": truth_count - episode_recalled,
            "unmatched_ground_truth_count": truth_count - source_recalled,
            "ambiguous_predictions": sum(
                item["metrics"]["ambiguous_predictions"] for item in case_results
            ),
            "ambiguity_cases_correct": sum(
                bool(item["metrics"]["ambiguity_correct"]) for item in ambiguous
            ),
            "ambiguity_cases_total": len(ambiguous),
            "incomplete_index_failures": sum(
                item["actual_status"] == MatchStatus.INCOMPLETE_INDEX.value
                for item in case_results
                if item["expected_status"] == MatchStatus.INCOMPLETE_INDEX.value
            ),
        }

    @classmethod
    def _transformation_groups(
        cls, case_results: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in case_results:
            grouped.setdefault(item["transformation"], []).append(item)
        result: dict[str, dict[str, Any]] = {}
        for transformation, cases in sorted(grouped.items()):
            statuses: dict[str, int] = {}
            for item in cases:
                statuses[item["actual_status"]] = statuses.get(item["actual_status"], 0) + 1
            aggregate = cls._aggregate(cases)
            result[transformation] = {
                "sample_count": len(cases),
                "recall": aggregate["source_aware_recall_at_1s"],
                "precision": aggregate["segment_precision_at_1s"],
                "episode_recall": aggregate["episode_recall"],
                "episode_correctness": aggregate["episode_correctness"],
                "accused_boundary_error_seconds": aggregate["accused_boundary_mae_seconds"],
                "source_boundary_error_seconds": aggregate["source_boundary_mae_seconds"],
                "false_positive_segments": aggregate["false_positive_segments"],
                "false_positive_seconds": aggregate["false_positive_seconds"],
                "ambiguity_rate": aggregate["ambiguity_rate"],
                "mean_absolute_segment_count_error": aggregate[
                    "mean_absolute_segment_count_error"
                ],
                "speed_ratio_mae": aggregate["speed_ratio_mae"],
                "mean_residual_rms_seconds": aggregate["mean_residual_rms_seconds"],
                "mean_inlier_ratio": aggregate["mean_inlier_ratio"],
                "candidate_green_precision_diagnostic": aggregate["green_precision"],
                "runtime_seconds": sum(item["analysis_seconds"] for item in cases),
                "status_distribution": statuses,
            }
        return result

    @staticmethod
    def _gate(aggregate: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
        negative_cases = [item for item in cases if item["transformation"] == "pure_negative"]
        ambiguity_cases = [
            item for item in cases
            if item["transformation"] in {
                "exact_duplicate_cross_episode", "independent_crf_duplicate_cross_episode"
            }
        ]
        incomplete_cases = [item for item in cases if item["transformation"] == "faststart_incomplete_original"]
        checks = {
            "source_aware_recall_at_1s_gte_0_95": (aggregate["source_aware_recall_at_1s"] or 0) >= 0.95,
            "segment_precision_at_1s_gte_0_98": (aggregate["segment_precision_at_1s"] or 0) >= 0.98,
            "source_boundary_mae_lte_1s": (
                aggregate["source_boundary_mae_seconds"] is not None
                and aggregate["source_boundary_mae_seconds"] <= 1.0
                and aggregate["boundary_missed_segments"] == 0
            ),
            "pure_negative_false_positive_seconds_zero": all(
                item["metrics"]["false_positive_seconds"] == 0 for item in negative_cases
            ) and bool(negative_cases),
            "duplicate_cross_episode_is_ambiguous": all(
                item["actual_status"] == MatchStatus.AMBIGUOUS.value for item in ambiguity_cases
            ) and bool(ambiguity_cases),
            "incomplete_index_refused": all(
                item["actual_status"] == MatchStatus.INCOMPLETE_INDEX.value
                and not item["predictions"] for item in incomplete_cases
            ) and bool(incomplete_cases),
        }
        return {"passed": all(checks.values()), "checks": checks}


def prepare_demo_benchmark(config: AppConfig) -> tuple[AppConfig, Path]:
    demo_root = config.paths.workspace / "benchmark_demo"
    originals_dir = demo_root / "originals"
    originals = create_demo_originals(originals_dir)
    demo_paths = replace(
        config.paths,
        originals=originals_dir,
        originals_metadata=originals_dir / "metadata.json",
        database=demo_root / "ipvm-v2.db",
    )
    demo_config = replace(config, paths=demo_paths)
    generator = SyntheticBenchmarkGenerator(originals, demo_root / "cases")
    return demo_config, generator.generate()
