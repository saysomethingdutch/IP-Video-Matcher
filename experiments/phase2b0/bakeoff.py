#!/usr/bin/env python3
"""Privacy-safe Phase 2B-0 visual-backend characterization harness.

Raw manifests and backend output belong under an ignored workspace.  This module
does not modify ``src/ipvm`` and imports the frozen DCT matcher as a control.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ipvm.config import load_config
from ipvm.database import Database
from ipvm.features import DCTVisualEncoder
from ipvm.matching import VideoMatcher
from ipvm.media import extract_grayscale_frames, probe_media


DEFAULT_SEED = 20260904
SOURCE_SPAN_SECONDS = 8.0
POSITION_COUNT = 10
SOURCE_GRID_TOLERANCE_SECONDS = 0.5
OFFSET_TOLERANCE_SECONDS = 1.0
VDF_COARSE_OFFSET_TOLERANCE_SECONDS = 15.0

PRIMARY_TRANSFORMATIONS = (
    "DIRECT",
    "H264_REENCODE",
    "MIRROR",
    "CENTER_CROP",
    "VERTICAL_9_16",
    "ZOOM",
    "LETTERBOX",
    "SUBTITLE_OVERLAY",
    "PIP_REDUCED_VIEWPORT",
    "SPEED_0_8X",
    "SPEED_1_25X",
)

PILOT_TRANSFORMATIONS = (
    "DIRECT",
    "MIRROR",
    "CENTER_CROP",
    "VERTICAL_9_16",
    "SUBTITLE_OVERLAY",
    "PIP_REDUCED_VIEWPORT",
)

VDF_MODES: dict[str, tuple[str, ...]] = {
    "CLASSIC_DEFAULT": ("--threshold", "5", "--percent", "96"),
    "AI_UNION_DEFAULT": ("--ai-matching", "--ai-percent", "94"),
    "AI_PARTIAL_DEFAULT": ("--ai-partial", "--ai-partial-hit-percent", "89"),
    "AI_PARTIAL_8S_ELIGIBLE": (
        "--ai-partial",
        "--partial-clip-min-ratio",
        "0.0",
        "--ai-partial-hit-percent",
        "89",
    ),
    "AI_UNION_LOOSER": ("--ai-matching", "--ai-percent", "92"),
    "AI_UNION_STRICTER": ("--ai-matching", "--ai-percent", "97"),
    "AI_PARTIAL_LOOSER": (
        "--ai-partial",
        "--partial-clip-min-ratio",
        "0.0",
        "--ai-partial-hit-percent",
        "84",
    ),
    "AI_PARTIAL_STRICTER": (
        "--ai-partial",
        "--partial-clip-min-ratio",
        "0.0",
        "--ai-partial-hit-percent",
        "94",
    ),
}


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_source(value: str) -> tuple[str, Path]:
    alias, separator, raw_path = value.partition("=")
    if not separator or not alias or not raw_path:
        raise argparse.ArgumentTypeError("source must be ALIAS=/path/to/video")
    return alias, Path(raw_path).resolve()


def select_positions(
    sources: list[tuple[str, Path]],
    *,
    seed: int = DEFAULT_SEED,
    positions_per_source: int = POSITION_COUNT,
    source_span_seconds: float = SOURCE_SPAN_SECONDS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select one deterministic start per equal-duration stratum."""
    if positions_per_source < 1:
        raise ValueError("positions_per_source must be positive")
    rng = random.Random(seed)
    source_records: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    for alias, path in sources:
        metadata = probe_media(path)
        lower = 30.0
        upper = metadata.duration - 30.0 - source_span_seconds
        if upper <= lower:
            raise ValueError(f"{alias} is too short for the registered sampling window")
        width = (upper - lower) / positions_per_source
        starts: list[float] = []
        for index in range(positions_per_source):
            stratum_start = lower + index * width
            stratum_end = lower + (index + 1) * width
            guard = min(5.0, max(0.0, (stratum_end - stratum_start) / 10.0))
            start = rng.uniform(stratum_start + guard, stratum_end - guard)
            starts.append(round(start, 6))
        if any(right - left < 20.0 for left, right in zip(starts, starts[1:])):
            raise ValueError(f"{alias} deterministic positions violate 20s separation")
        source_records.append(
            {
                "alias": alias,
                "path": str(path),
                "duration_seconds": metadata.duration,
                "width": metadata.width,
                "height": metadata.height,
                "fps": metadata.fps,
            }
        )
        for index, start in enumerate(starts, start=1):
            positions.append(
                {
                    "position_id": f"{alias}-POSITION_{index:03d}",
                    "source_alias": alias,
                    "source_start_seconds": start,
                    "source_end_seconds": round(start + source_span_seconds, 6),
                    "normalized_source_start": round(start / metadata.duration, 8),
                    "source_span_seconds": source_span_seconds,
                    "ordinal": index,
                }
            )
    return source_records, positions


def _subtitle_like_filter() -> str:
    boxes = [
        "drawbox=x=iw*0.20:y=ih*0.815:w=iw*0.11:h=ih*0.018:color=white@0.95:t=fill",
        "drawbox=x=iw*0.325:y=ih*0.815:w=iw*0.16:h=ih*0.018:color=white@0.95:t=fill",
        "drawbox=x=iw*0.50:y=ih*0.815:w=iw*0.09:h=ih*0.018:color=white@0.95:t=fill",
        "drawbox=x=iw*0.605:y=ih*0.815:w=iw*0.19:h=ih*0.018:color=white@0.95:t=fill",
        "drawbox=x=iw*0.28:y=ih*0.855:w=iw*0.18:h=ih*0.018:color=white@0.95:t=fill",
        "drawbox=x=iw*0.475:y=ih*0.855:w=iw*0.25:h=ih*0.018:color=white@0.95:t=fill",
    ]
    return ",".join(
        ["scale=1280:720", "drawbox=x=0:y=ih*0.77:w=iw:h=ih*0.14:color=black@0.62:t=fill", *boxes]
    )


def transformation_spec(name: str) -> dict[str, Any]:
    common = {
        "codec": "libx264",
        "preset": "medium",
        "pixel_format": "yuv420p",
        "audio": "dropped",
        "source_span_seconds": SOURCE_SPAN_SECONDS,
    }
    specs: dict[str, dict[str, Any]] = {
        "DIRECT": {**common, "crf": 18, "filter": None, "speed_ratio": 1.0},
        "H264_REENCODE": {
            **common,
            "crf": 32,
            "filter": "scale=854:480:flags=lanczos",
            "speed_ratio": 1.0,
        },
        "MIRROR": {
            **common,
            "crf": 23,
            "filter": "hflip,scale=1280:720:flags=lanczos",
            "speed_ratio": 1.0,
        },
        "CENTER_CROP": {
            **common,
            "crf": 23,
            "filter": "crop=trunc(iw*0.70/2)*2:trunc(ih*0.70/2)*2:(iw-ow)/2:(ih-oh)/2,scale=1280:720:flags=lanczos",
            "speed_ratio": 1.0,
        },
        "VERTICAL_9_16": {
            **common,
            "crf": 23,
            "filter": "crop=trunc(ih*9/16/2)*2:ih:(iw-ow)/2:0,scale=720:1280:flags=lanczos",
            "speed_ratio": 1.0,
        },
        "ZOOM": {
            **common,
            "crf": 23,
            "filter": "crop=trunc(iw*0.80/2)*2:trunc(ih*0.80/2)*2:(iw-ow)/2:(ih-oh)/2,scale=1280:720:flags=lanczos",
            "speed_ratio": 1.0,
        },
        "LETTERBOX": {
            **common,
            "crf": 23,
            "filter": "scale=960:540:flags=lanczos,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black",
            "speed_ratio": 1.0,
        },
        "SUBTITLE_OVERLAY": {
            **common,
            "crf": 23,
            "filter": _subtitle_like_filter(),
            "speed_ratio": 1.0,
        },
        "PIP_REDUCED_VIEWPORT": {
            **common,
            "crf": 23,
            "filter": "scale=512:288:flags=lanczos,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x24405c",
            "speed_ratio": 1.0,
            "viewport_width_fraction": 0.40,
        },
        "SPEED_0_8X": {
            **common,
            "crf": 23,
            "filter": "setpts=PTS/0.8,scale=1280:720:flags=lanczos",
            "speed_ratio": 0.8,
        },
        "SPEED_1_25X": {
            **common,
            "crf": 23,
            "filter": "setpts=PTS/1.25,scale=1280:720:flags=lanczos",
            "speed_ratio": 1.25,
        },
    }
    if name not in specs:
        raise ValueError(f"unsupported transformation: {name}")
    return specs[name]


def _ffmpeg_query_command(source: Path, target: Path, start: float, spec: dict[str, Any]) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{float(spec['source_span_seconds']):.6f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
    ]
    if spec.get("filter"):
        command.extend(["-vf", str(spec["filter"])])
    command.extend(
        [
            "-c:v",
            str(spec["codec"]),
            "-preset",
            str(spec["preset"]),
            "-crf",
            str(spec["crf"]),
            "-pix_fmt",
            str(spec["pixel_format"]),
            "-movflags",
            "+faststart",
            "-y",
            str(target),
        ]
    )
    return command


def generate_corpus(
    sources: list[tuple[str, Path]],
    root: Path,
    transformations: Iterable[str],
    *,
    seed: int = DEFAULT_SEED,
    positions_per_source: int = POSITION_COUNT,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    source_records, positions = select_positions(
        sources,
        seed=seed,
        positions_per_source=positions_per_source,
    )
    source_paths = {record["alias"]: Path(record["path"]) for record in source_records}
    queries: list[dict[str, Any]] = []
    normalized_transforms = tuple(transformations)
    for position in positions:
        for name in normalized_transforms:
            spec = transformation_spec(name)
            case_id = f"{position['position_id']}-{name}"
            target = root / "queries" / name / f"{case_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            command = _ffmpeg_query_command(
                source_paths[position["source_alias"]],
                target,
                float(position["source_start_seconds"]),
                spec,
            )
            started = time.perf_counter()
            _run(command)
            elapsed = time.perf_counter() - started
            metadata = probe_media(target)
            expected_duration = SOURCE_SPAN_SECONDS / float(spec["speed_ratio"])
            if abs(metadata.duration - expected_duration) > 0.12:
                raise ValueError(
                    f"{case_id} duration {metadata.duration:.3f}s != {expected_duration:.3f}s"
                )
            queries.append(
                {
                    "case_id": case_id,
                    "position_id": position["position_id"],
                    "source_alias": position["source_alias"],
                    "source_start_seconds": position["source_start_seconds"],
                    "source_end_seconds": position["source_end_seconds"],
                    "normalized_source_start": position["normalized_source_start"],
                    "source_span_seconds": SOURCE_SPAN_SECONDS,
                    "query_duration_seconds": metadata.duration,
                    "speed_ratio": spec["speed_ratio"],
                    "transformation": name,
                    "path": str(target.resolve()),
                    "generation_elapsed_seconds": round(elapsed, 6),
                    "ffmpeg_arguments": command[1:-1],
                    "transformation_spec": spec,
                }
            )
    return {
        "schema_version": "phase2b0-raw-corpus-v0.1",
        "seed": seed,
        "positions_per_source": positions_per_source,
        "source_span_seconds": SOURCE_SPAN_SECONDS,
        "sources": source_records,
        "positions": positions,
        "transformations": list(normalized_transforms),
        "queries": queries,
    }


def generate_negative_queries(root: Path, count: int = 20) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index in range(count):
        case_id = f"NEGATIVE_{index + 1:03d}"
        target = root / f"{case_id}.mp4"
        hue = (index * 37) % 360
        grid = 64 + (index % 5) * 16
        filter_graph = (
            f"hue=h={hue}:s={0.7 + (index % 4) * 0.15:.2f},"
            f"drawgrid=w={grid}:h={grid}:t=2:c=white@0.35"
        )
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=8",
            "-vf",
            filter_graph,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(target),
        ]
        _run(command)
        records.append(
            {
                "case_id": case_id,
                "position_id": case_id,
                "source_alias": None,
                "source_start_seconds": None,
                "source_end_seconds": None,
                "normalized_source_start": None,
                "source_span_seconds": None,
                "query_duration_seconds": probe_media(target).duration,
                "speed_ratio": None,
                "transformation": "NEGATIVE",
                "path": str(target.resolve()),
                "ffmpeg_arguments": command[1:-1],
            }
        )
    return records


def _case_subset(manifest: dict[str, Any], case_ids: set[str] | None) -> list[dict[str, Any]]:
    cases = list(manifest["queries"])
    if case_ids is None:
        return cases
    return [case for case in cases if case["case_id"] in case_ids]


def run_dct(
    manifest: dict[str, Any],
    *,
    repo_root: Path,
    config_path: Path,
    database_path: Path,
    workspace_path: Path,
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    config = load_config(repo_root, config_path)
    config = replace(
        config,
        paths=replace(
            config.paths,
            database=database_path.resolve(),
            workspace=workspace_path.resolve(),
        ),
    )
    database = Database(config.paths.database)
    matcher = VideoMatcher(config, database)
    encoder = DCTVisualEncoder()
    rows = database.list_original_indexes(matcher.project_id)
    source_alias_by_path = {
        str(Path(source["path"]).resolve()): str(source["alias"])
        for source in manifest["sources"]
    }
    episode_to_alias: dict[str, str] = {}
    for row in rows:
        resolved = str(Path(row["file_path"]).resolve())
        if resolved in source_alias_by_path:
            episode_to_alias[str(row["episode"])] = source_alias_by_path[resolved]
    if set(episode_to_alias.values()) != set(source_alias_by_path.values()):
        raise ValueError("source manifest does not map one-to-one to the frozen DCT index")

    results: list[dict[str, Any]] = []
    for case in _case_subset(manifest, case_ids):
        query_path = Path(case["path"])
        expected_alias = case.get("source_alias")
        expected_episode = next(
            (episode for episode, alias in episode_to_alias.items() if alias == expected_alias),
            None,
        )
        started = time.perf_counter()
        analysis = matcher.analyze_file(case["case_id"], query_path, persist=False)
        final_elapsed = time.perf_counter() - started

        frames = extract_grayscale_frames(
            query_path,
            fps=config.matching.coarse_fps,
            width=config.matching.frame_width,
            height=config.matching.frame_height,
        )
        features = encoder.encode_images(frame for _, frame in frames)
        query_times = np.asarray([timestamp for timestamp, _ in frames], dtype=np.float32)
        candidates = matcher.retriever.retrieve(query_times, features)
        by_time: dict[float, list[Any]] = defaultdict(list)
        for candidate in candidates:
            by_time[round(candidate.accused_time, 6)].append(candidate)

        top1_count = 0
        top8_count = 0
        usable_count = 0
        ranks: list[int] = []
        for query_time, per_frame in sorted(by_time.items()):
            ranked = sorted(per_frame, key=lambda item: item.visual_score, reverse=True)
            expected_source_time = (
                float(case["source_start_seconds"])
                + float(case["speed_ratio"]) * query_time
                if expected_episode is not None
                else None
            )
            correct_ranks = [
                index
                for index, candidate in enumerate(ranked, start=1)
                if candidate.source_episode == expected_episode
            ]
            if correct_ranks:
                ranks.append(min(correct_ranks))
                top8_count += 1
                if correct_ranks[0] == 1:
                    top1_count += 1
            if expected_source_time is not None and any(
                candidate.source_episode == expected_episode
                and abs(candidate.source_time - expected_source_time)
                <= SOURCE_GRID_TOLERANCE_SECONDS
                for candidate in ranked
            ):
                usable_count += 1

        correct_segments = [
            segment
            for segment in analysis.segments
            if segment.source_episode == expected_episode
        ]
        wrong_segments = [
            segment
            for segment in analysis.segments
            if expected_episode is None or segment.source_episode != expected_episode
        ]
        best_segment = (
            min(
                correct_segments,
                key=lambda segment: abs(
                    segment.source_start - float(case["source_start_seconds"])
                ),
            )
            if correct_segments and expected_episode is not None
            else None
        )
        source_start_error = (
            abs(best_segment.source_start - float(case["source_start_seconds"]))
            if best_segment is not None
            else None
        )
        source_end_error = (
            abs(best_segment.source_end - float(case["source_end_seconds"]))
            if best_segment is not None
            else None
        )
        episode_correct = bool(correct_segments) and not wrong_segments
        results.append(
            {
                "case_id": case["case_id"],
                "position_id": case["position_id"],
                "source_alias": expected_alias,
                "transformation": case["transformation"],
                "status": analysis.status,
                "query_frame_count": len(frames),
                "correct_source_top1_frame_count": top1_count,
                "correct_source_top8_frame_count": top8_count,
                "source_position_usable_frame_count": usable_count,
                "correct_source_top1_frame_rate": top1_count / len(frames) if frames else None,
                "correct_source_top8_frame_rate": top8_count / len(frames) if frames else None,
                "source_position_usable_frame_rate": usable_count / len(frames) if frames else None,
                "correct_source_rank_median": statistics.median(ranks) if ranks else None,
                "episode_correct": episode_correct,
                "temporally_usable": bool(
                    episode_correct
                    and source_start_error is not None
                    and source_start_error <= OFFSET_TOLERANCE_SECONDS
                ),
                "offset_correct": bool(
                    source_start_error is not None
                    and source_start_error <= OFFSET_TOLERANCE_SECONDS
                ),
                "false_source_match": bool(wrong_segments),
                "source_start_error_seconds": source_start_error,
                "source_end_error_seconds": source_end_error,
                "predicted_segments": [asdict(segment) for segment in analysis.segments],
                "alternative_count": len(analysis.alternatives),
                "runtime_seconds": round(final_elapsed, 6),
            }
        )
    return {
        "schema_version": "phase2b0-dct-raw-v0.1",
        "backend": "frozen-dct",
        "configuration": {
            "coarse_fps": config.matching.coarse_fps,
            "top_k": config.matching.top_k,
            "encoder": encoder.cache_identity(),
            "source_position_tolerance_seconds": SOURCE_GRID_TOLERANCE_SECONDS,
            "offset_tolerance_seconds": OFFSET_TOLERANCE_SECONDS,
        },
        "cases": results,
    }


def _safe_symlink(target: Path, link: Path) -> None:
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target.resolve())


def _vdf_command(
    executable: Path,
    scan_dir: Path,
    db_dir: Path,
    output: Path,
    mode: str,
) -> list[str]:
    if mode not in VDF_MODES:
        raise ValueError(f"unknown VDF mode: {mode}")
    return [
        str(executable.resolve()),
        "scan-and-compare",
        "--include",
        str(scan_dir.resolve()),
        "--db",
        str(db_dir.resolve()),
        "--parallelism",
        "1",
        "--matching-parallelism",
        "1",
        *VDF_MODES[mode],
        "--format",
        "json",
        "--output",
        str(output.resolve()),
    ]


def prepare_vdf_templates(
    manifest: dict[str, Any],
    *,
    executable: Path,
    root: Path,
    modes: Iterable[str],
    force: bool = False,
) -> dict[str, Any]:
    scan_dir = root / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    query_link = scan_dir / "QUERY.mp4"
    if query_link.exists() or query_link.is_symlink():
        query_link.unlink()
    for source in manifest["sources"]:
        _safe_symlink(Path(source["path"]), scan_dir / f"{source['alias']}.mp4")
    records: dict[str, Any] = {}
    for mode in modes:
        template = root / "templates" / mode
        if force and template.exists():
            shutil.rmtree(template)
        template.mkdir(parents=True, exist_ok=True)
        output = root / "template-output" / f"{mode}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        command = _vdf_command(executable, scan_dir, template, output, mode)
        started = time.perf_counter()
        process = _run(command)
        elapsed = time.perf_counter() - started
        records[mode] = {
            "elapsed_seconds": round(elapsed, 6),
            "stdout": process.stdout,
            "cache_files": [
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                }
                for path in sorted(template.iterdir())
                if path.is_file()
            ],
        }
    return records


def _parse_timespan(value: str | None) -> float | None:
    if not value:
        return None
    pieces = value.split(":")
    if len(pieces) != 3:
        return None
    hours, minutes, seconds = pieces
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def interpret_vdf_output(
    groups: list[dict[str, Any]],
    *,
    expected_source_alias: str | None,
    expected_offset_seconds: float | None,
    source_aliases: set[str],
) -> dict[str, Any]:
    query_group: dict[str, Any] | None = None
    query_item: dict[str, Any] | None = None
    for group in groups:
        for item in group.get("Items", []):
            if Path(str(item.get("Path", ""))).name == "QUERY.mp4":
                query_group = group
                query_item = item
                break
        if query_group is not None:
            break
    if query_group is None or query_item is None:
        return {
            "detected": False,
            "episode_correct": False,
            "temporally_usable": False,
            "offset_correct": False,
            "coarse_offset_correct": False,
            "false_source_match": False,
            "similarity": None,
            "partial_match": False,
            "offset_seconds": None,
            "offset_error_seconds": None,
            "group_size": 0,
            "reported_source_aliases": [],
            "flags": [],
        }
    item_aliases = {
        Path(str(item.get("Path", ""))).stem
        for item in query_group.get("Items", [])
    }
    reported_sources = sorted(item_aliases & source_aliases)
    wrong_sources = set(reported_sources) - ({expected_source_alias} if expected_source_alias else set())
    flags = [part.strip() for part in str(query_item.get("Flags", "")).split(",")]
    flags = [flag for flag in flags if flag and flag != "None"]
    partial = "PartialClip" in flags
    offset = _parse_timespan(query_item.get("PartialClipOffset")) if partial else None
    offset_error = (
        abs(offset - expected_offset_seconds)
        if offset is not None and expected_offset_seconds is not None
        else None
    )
    episode_correct = bool(
        expected_source_alias in reported_sources and not wrong_sources
        if expected_source_alias is not None
        else False
    )
    return {
        "detected": True,
        "episode_correct": episode_correct,
        "temporally_usable": bool(episode_correct and partial and offset is not None),
        "offset_correct": bool(
            episode_correct
            and offset_error is not None
            and offset_error <= OFFSET_TOLERANCE_SECONDS
        ),
        "coarse_offset_correct": bool(
            episode_correct
            and offset_error is not None
            and offset_error <= VDF_COARSE_OFFSET_TOLERANCE_SECONDS
        ),
        "false_source_match": bool(wrong_sources),
        "similarity": query_item.get("Similarity"),
        "partial_match": partial,
        "offset_seconds": offset,
        "offset_error_seconds": offset_error,
        "group_size": len(query_group.get("Items", [])),
        "reported_source_aliases": reported_sources,
        "flags": flags,
    }


def run_vdf(
    manifest: dict[str, Any],
    *,
    executable: Path,
    root: Path,
    modes: Iterable[str],
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    modes = tuple(modes)
    templates = root / "templates"
    for mode in modes:
        if not (templates / mode / "ScannedFiles.db").exists():
            raise FileNotFoundError(f"missing VDF template for {mode}")
    scan_dir = root / "scan"
    source_aliases = {str(source["alias"]) for source in manifest["sources"]}
    results: list[dict[str, Any]] = []
    for case in _case_subset(manifest, case_ids):
        _safe_symlink(Path(case["path"]), scan_dir / "QUERY.mp4")
        for mode in modes:
            active = root / "active" / mode
            if active.exists():
                shutil.rmtree(active)
            shutil.copytree(templates / mode, active)
            raw_dir = root / "raw-results" / mode
            raw_dir.mkdir(parents=True, exist_ok=True)
            cold_output = raw_dir / f"{case['case_id']}-cold.json"
            command = _vdf_command(executable, scan_dir, active, cold_output, mode)
            cold_started = time.perf_counter()
            cold_process = _run(command)
            cold_elapsed = time.perf_counter() - cold_started
            cold_groups = _read_json(cold_output)
            cold = interpret_vdf_output(
                cold_groups,
                expected_source_alias=case.get("source_alias"),
                expected_offset_seconds=case.get("source_start_seconds"),
                source_aliases=source_aliases,
            )

            warm_output = raw_dir / f"{case['case_id']}-warm.json"
            warm_command = _vdf_command(executable, scan_dir, active, warm_output, mode)
            warm_started = time.perf_counter()
            warm_process = _run(warm_command)
            warm_elapsed = time.perf_counter() - warm_started
            warm_groups = _read_json(warm_output)
            warm = interpret_vdf_output(
                warm_groups,
                expected_source_alias=case.get("source_alias"),
                expected_offset_seconds=case.get("source_start_seconds"),
                source_aliases=source_aliases,
            )
            results.append(
                {
                    "case_id": case["case_id"],
                    "position_id": case["position_id"],
                    "source_alias": case.get("source_alias"),
                    "transformation": case["transformation"],
                    "mode": mode,
                    "cold": cold,
                    "warm": warm,
                    "cold_runtime_seconds": round(cold_elapsed, 6),
                    "warm_runtime_seconds": round(warm_elapsed, 6),
                    "cold_stdout": cold_process.stdout,
                    "warm_stdout": warm_process.stdout,
                }
            )
    return {
        "schema_version": "phase2b0-vdf-raw-v0.1",
        "vdf_modes": {mode: list(VDF_MODES[mode]) for mode in modes},
        "strict_offset_tolerance_seconds": OFFSET_TOLERANCE_SECONDS,
        "coarse_offset_tolerance_seconds": VDF_COARSE_OFFSET_TOLERANCE_SECONDS,
        "cases": results,
    }


def _ratio_summary(rows: list[dict[str, Any]], fields: Iterable[str]) -> dict[str, Any]:
    total = len(rows)
    return {
        field: {
            "numerator": sum(bool(row.get(field)) for row in rows),
            "denominator": total,
            "rate": sum(bool(row.get(field)) for row in rows) / total if total else None,
        }
        for field in fields
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p90": None, "minimum": None, "maximum": None}
    return {
        "count": len(values),
        "median": statistics.median(values),
        "p90": float(np.percentile(np.asarray(values), 90, method="higher")),
        "minimum": min(values),
        "maximum": max(values),
    }


def _count_rate(rows: list[dict[str, Any]], numerator: str, denominator: str) -> dict[str, Any]:
    numerator_sum = sum(int(row.get(numerator, 0)) for row in rows)
    denominator_sum = sum(int(row.get(denominator, 0)) for row in rows)
    return {
        "numerator": numerator_sum,
        "denominator": denominator_sum,
        "rate": numerator_sum / denominator_sum if denominator_sum else None,
    }


def sanitized_aggregate(
    manifest: dict[str, Any],
    dct_report: dict[str, Any],
    vdf_report: dict[str, Any],
    *,
    experiment_status: str = "PILOT_COMPLETE",
    stop_reason: str | None = None,
    vdf_version: str | None = None,
) -> dict[str, Any]:
    dct_cases = list(dct_report.get("cases", []))
    vdf_cases = list(vdf_report.get("cases", []))
    transformations = sorted({str(case["transformation"]) for case in dct_cases + vdf_cases})
    by_transformation: dict[str, Any] = {}
    for transformation in transformations:
        dct_rows = [row for row in dct_cases if row["transformation"] == transformation]
        vdf_by_mode: dict[str, Any] = {}
        for mode in sorted({str(row["mode"]) for row in vdf_cases}):
            mode_rows = [
                row["cold"]
                for row in vdf_cases
                if row["transformation"] == transformation and row["mode"] == mode
            ]
            raw_mode_rows = [
                row
                for row in vdf_cases
                if row["transformation"] == transformation and row["mode"] == mode
            ]
            vdf_by_mode[mode] = {
                "metrics": _ratio_summary(
                    mode_rows,
                    (
                        "detected",
                        "episode_correct",
                        "temporally_usable",
                        "offset_correct",
                        "coarse_offset_correct",
                        "false_source_match",
                    ),
                ),
                "offset_error_seconds": _distribution(
                    [
                        float(row["offset_error_seconds"])
                        for row in mode_rows
                        if row.get("offset_error_seconds") is not None
                    ]
                ),
                "similarity": _distribution(
                    [float(row["similarity"]) for row in mode_rows if row.get("similarity") is not None]
                ),
                "cold_runtime_seconds": _distribution(
                    [float(row["cold_runtime_seconds"]) for row in raw_mode_rows]
                ),
                "warm_runtime_seconds": _distribution(
                    [float(row["warm_runtime_seconds"]) for row in raw_mode_rows]
                ),
            }
        by_transformation[transformation] = {
            "query_count": len(dct_rows),
            "dct": {
                "metrics": _ratio_summary(
                    dct_rows,
                    ("episode_correct", "temporally_usable", "offset_correct", "false_source_match"),
                ),
                "retrieval": {
                    "correct_source_top1_frames": _count_rate(
                        dct_rows, "correct_source_top1_frame_count", "query_frame_count"
                    ),
                    "correct_source_top8_frames": _count_rate(
                        dct_rows, "correct_source_top8_frame_count", "query_frame_count"
                    ),
                    "source_position_usable_frames": _count_rate(
                        dct_rows, "source_position_usable_frame_count", "query_frame_count"
                    ),
                    "per_query_correct_source_rank_median": _distribution(
                        [
                            float(row["correct_source_rank_median"])
                            for row in dct_rows
                            if row.get("correct_source_rank_median") is not None
                        ]
                    ),
                },
                "final_status_distribution": {
                    status: sum(row.get("status") == status for row in dct_rows)
                    for status in sorted({str(row.get("status")) for row in dct_rows})
                },
                "source_start_error_seconds": _distribution(
                    [
                        float(row["source_start_error_seconds"])
                        for row in dct_rows
                        if row.get("source_start_error_seconds") is not None
                    ]
                ),
                "runtime_seconds": _distribution(
                    [float(row["runtime_seconds"]) for row in dct_rows]
                ),
            },
            "vdf": vdf_by_mode,
        }

    position_ids = sorted({str(case["position_id"]) for case in dct_cases})
    position_level: list[dict[str, Any]] = []
    for position_id in position_ids:
        dct_rows = [row for row in dct_cases if row["position_id"] == position_id]
        vdf_rows = [row for row in vdf_cases if row["position_id"] == position_id]
        position_level.append(
            {
                "position_id": position_id,
                "query_count": len(dct_rows),
                "dct_episode_correct_count": sum(bool(row["episode_correct"]) for row in dct_rows),
                "dct_temporally_usable_count": sum(bool(row["temporally_usable"]) for row in dct_rows),
                "vdf_by_mode": {
                    mode: {
                        "episode_correct_count": sum(
                            bool(row["cold"]["episode_correct"])
                            for row in vdf_rows
                            if row["mode"] == mode
                        ),
                        "temporally_usable_count": sum(
                            bool(row["cold"]["temporally_usable"])
                            for row in vdf_rows
                            if row["mode"] == mode
                        ),
                    }
                    for mode in sorted({str(row["mode"]) for row in vdf_rows})
                },
            }
        )

    return {
        "schema_version": "phase2b0-visual-backend-bakeoff-v0.1",
        "experiment_status": experiment_status,
        "stop_reason": stop_reason,
        "contains_media_identity": False,
        "contains_paths": False,
        "contains_hashes": False,
        "frozen_baseline": {
            "commit": "93bf13b",
            "coarse_fps": 2.0,
            "top_k": 8,
            "encoder": "dct-low-frequency@1",
        },
        "vdf": {
            "version": vdf_version,
            "modes": vdf_report.get("vdf_modes", {}),
            "strict_offset_tolerance_seconds": vdf_report.get(
                "strict_offset_tolerance_seconds"
            ),
            "coarse_offset_tolerance_seconds": vdf_report.get(
                "coarse_offset_tolerance_seconds"
            ),
        },
        "sample_scope": {
            "source_count": len(manifest.get("sources", [])),
            "independent_position_count": len(position_ids),
            "query_count": len(dct_cases),
            "position_is_primary_independence_unit": True,
        },
        "transformation_definitions": {
            name: transformation_spec(name)
            for name in transformations
            if name in PRIMARY_TRANSFORMATIONS
        },
        "by_transformation": by_transformation,
        "position_level": position_level,
    }


def merge_vdf_reports(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Combine independently run VDF modes without hiding duplicate runs."""
    reports = list(reports)
    merged_modes: dict[str, Any] = {}
    cases: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for report in reports:
        for mode, arguments in report.get("vdf_modes", {}).items():
            if mode in merged_modes and merged_modes[mode] != arguments:
                raise ValueError(f"conflicting arguments for VDF mode {mode}")
            merged_modes[mode] = arguments
        for case in report.get("cases", []):
            identity = (str(case["case_id"]), str(case["mode"]))
            if identity in identities:
                raise ValueError(f"duplicate VDF case/mode: {identity}")
            identities.add(identity)
            cases.append(case)
    strict_tolerances = {
        report.get("strict_offset_tolerance_seconds") for report in reports
    }
    coarse_tolerances = {
        report.get("coarse_offset_tolerance_seconds") for report in reports
    }
    if len(strict_tolerances) > 1 or len(coarse_tolerances) > 1:
        raise ValueError("VDF reports use inconsistent offset tolerances")
    return {
        "schema_version": "phase2b0-vdf-raw-v0.1",
        "vdf_modes": merged_modes,
        "strict_offset_tolerance_seconds": next(iter(strict_tolerances), None),
        "coarse_offset_tolerance_seconds": next(iter(coarse_tolerances), None),
        "cases": cases,
    }


def _comma_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _case_ids_from_file(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    corpus = subparsers.add_parser("make-corpus")
    corpus.add_argument("--source", action="append", required=True, type=_parse_source)
    corpus.add_argument("--root", required=True, type=Path)
    corpus.add_argument("--manifest", required=True, type=Path)
    corpus.add_argument("--seed", type=int, default=DEFAULT_SEED)
    corpus.add_argument("--positions-per-source", type=int, default=POSITION_COUNT)
    corpus.add_argument("--transformations", default=",".join(PRIMARY_TRANSFORMATIONS))

    negative = subparsers.add_parser("make-negatives")
    negative.add_argument("--root", required=True, type=Path)
    negative.add_argument("--output", required=True, type=Path)
    negative.add_argument("--count", type=int, default=20)

    prepare = subparsers.add_parser("prepare-vdf")
    prepare.add_argument("--manifest", required=True, type=Path)
    prepare.add_argument("--executable", required=True, type=Path)
    prepare.add_argument("--root", required=True, type=Path)
    prepare.add_argument("--modes", default=",".join(VDF_MODES))
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--output", required=True, type=Path)

    dct = subparsers.add_parser("run-dct")
    dct.add_argument("--manifest", required=True, type=Path)
    dct.add_argument("--repo-root", required=True, type=Path)
    dct.add_argument("--config", required=True, type=Path)
    dct.add_argument("--database", required=True, type=Path)
    dct.add_argument("--workspace", required=True, type=Path)
    dct.add_argument("--case-ids", type=Path)
    dct.add_argument("--output", required=True, type=Path)

    vdf = subparsers.add_parser("run-vdf")
    vdf.add_argument("--manifest", required=True, type=Path)
    vdf.add_argument("--executable", required=True, type=Path)
    vdf.add_argument("--root", required=True, type=Path)
    vdf.add_argument("--modes", default="CLASSIC_DEFAULT,AI_UNION_DEFAULT,AI_PARTIAL_DEFAULT")
    vdf.add_argument("--case-ids", type=Path)
    vdf.add_argument("--output", required=True, type=Path)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--manifest", required=True, type=Path)
    aggregate.add_argument("--dct", required=True, type=Path)
    aggregate.add_argument("--vdf", required=True, action="append", type=Path)
    aggregate.add_argument("--experiment-status", default="PILOT_COMPLETE")
    aggregate.add_argument("--stop-reason")
    aggregate.add_argument("--vdf-version")
    aggregate.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "make-corpus":
        result = generate_corpus(
            args.source,
            args.root,
            _comma_values(args.transformations),
            seed=args.seed,
            positions_per_source=args.positions_per_source,
        )
        _write_json(args.manifest, result)
    elif args.command == "make-negatives":
        _write_json(args.output, {"queries": generate_negative_queries(args.root, args.count)})
    elif args.command == "prepare-vdf":
        result = prepare_vdf_templates(
            _read_json(args.manifest),
            executable=args.executable,
            root=args.root,
            modes=_comma_values(args.modes),
            force=args.force,
        )
        _write_json(args.output, result)
    elif args.command == "run-dct":
        result = run_dct(
            _read_json(args.manifest),
            repo_root=args.repo_root,
            config_path=args.config,
            database_path=args.database,
            workspace_path=args.workspace,
            case_ids=_case_ids_from_file(args.case_ids),
        )
        _write_json(args.output, result)
    elif args.command == "run-vdf":
        result = run_vdf(
            _read_json(args.manifest),
            executable=args.executable,
            root=args.root,
            modes=_comma_values(args.modes),
            case_ids=_case_ids_from_file(args.case_ids),
        )
        _write_json(args.output, result)
    elif args.command == "aggregate":
        result = sanitized_aggregate(
            _read_json(args.manifest),
            _read_json(args.dct),
            merge_vdf_reports(_read_json(path) for path in args.vdf),
            experiment_status=args.experiment_status,
            stop_reason=args.stop_reason,
            vdf_version=args.vdf_version,
        )
        _write_json(args.output, result)


if __name__ == "__main__":
    main()
