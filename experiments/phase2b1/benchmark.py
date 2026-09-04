#!/usr/bin/env python3
"""Phase 2B-1 isolated frame-embedding retrieval characterization.

The harness deliberately lives outside ``src/ipvm``.  It compares the frozen
DCT descriptor with a directly invoked DINOv2-small ONNX encoder on the same
2 FPS query/source grids and exact NumPy cosine search.  Raw paths, timestamps,
frames, embeddings, and per-frame candidates belong under ignored workspace.
"""

from __future__ import annotations

import argparse
import json
import random
import resource
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ipvm.features import DCTVisualEncoder
from ipvm.media import extract_grayscale_frames, probe_media


SEED = 20260904
SOURCE_FPS = 2.0
QUERY_FPS = 2.0
TOP_K = 8
POSITIONS_PER_SOURCE = 10
SOURCE_SPAN_SECONDS = 8.0
PRIMARY_TOLERANCE_SECONDS = 1.0
STRICT_TOLERANCE_SECONDS = 0.5
POSITION_USABILITY_THRESHOLDS = (0.50, 0.75, 1.00)
DINO_INPUT_SIDE = 224
DINO_DIMENSION = 384
DINO_BATCH_SIZE = 16
DCT_WIDTH = 160
DCT_HEIGHT = 90

BACKENDS = ("DCT", "DINO_RAW", "DINO_FLIP_TTA")
TRANSFORMATIONS = (
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

IMAGENET_MEAN = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
IMAGENET_STD = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _parse_source(value: str) -> tuple[str, Path]:
    alias, separator, raw_path = value.partition("=")
    if not separator or not alias or not raw_path:
        raise argparse.ArgumentTypeError("source must be ALIAS=/path/to/video")
    return alias, Path(raw_path).resolve()


def select_positions(
    sources: list[tuple[str, Path]],
    *,
    seed: int = SEED,
    positions_per_source: int = POSITIONS_PER_SOURCE,
    source_span_seconds: float = SOURCE_SPAN_SECONDS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reproduce the Phase 2B-0 deterministic stratified position selection."""
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
            starts.append(round(rng.uniform(stratum_start + guard, stratum_end - guard), 6))
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
                "size_bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
        )
        for ordinal, start in enumerate(starts, start=1):
            positions.append(
                {
                    "position_id": f"{alias}-POSITION_{ordinal:03d}",
                    "source_alias": alias,
                    "source_start_seconds": start,
                    "source_end_seconds": round(start + source_span_seconds, 6),
                    "normalized_source_start": round(start / metadata.duration, 8),
                    "source_span_seconds": source_span_seconds,
                    "ordinal": ordinal,
                }
            )
    return source_records, positions


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
            "filter": "pillow-raster-overlay-v1",
            "speed_ratio": 1.0,
            "overlay_kind": "actual_raster_text",
            "overlay_text": [
                "IP VIDEO MATCHER // SAMPLE SUBTITLE",
                "Evidence copy - fixed experimental overlay",
            ],
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


def create_subtitle_overlay(path: Path) -> None:
    """Create fixed, real raster text without committing a font file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 548, 1280, 682), fill=(0, 0, 0, 164))
    try:
        font_large = ImageFont.load_default(size=32)
        font_small = ImageFont.load_default(size=26)
    except TypeError:  # pragma: no cover - compatibility with older Pillow
        font_large = ImageFont.load_default()
        font_small = font_large
    lines = transformation_spec("SUBTITLE_OVERLAY")["overlay_text"]
    for text, xy, font in (
        (lines[0], (220, 570), font_large),
        (lines[1], (300, 624), font_small),
    ):
        draw.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=(0, 0, 0, 255))
        draw.text(xy, text, font=font, fill=(255, 255, 255, 255))
    canvas.save(path)


def _query_command(
    source: Path,
    target: Path,
    start: float,
    spec: dict[str, Any],
    subtitle_overlay: Path,
) -> list[str]:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.6f}",
        "-t", f"{float(spec['source_span_seconds']):.6f}", "-i", str(source),
    ]
    if spec.get("overlay_kind") == "actual_raster_text":
        command += [
            "-loop", "1", "-i", str(subtitle_overlay), "-filter_complex",
            "[0:v]scale=1280:720:flags=lanczos[base];"
            "[base][1:v]overlay=0:0:shortest=1:format=auto[out]",
            "-map", "[out]", "-t", f"{float(spec['source_span_seconds']):.6f}",
        ]
    else:
        command += ["-map", "0:v:0"]
        if spec.get("filter"):
            command += ["-vf", str(spec["filter"])]
    command += [
        "-an", "-c:v", str(spec["codec"]), "-preset", str(spec["preset"]),
        "-crf", str(spec["crf"]), "-pix_fmt", str(spec["pixel_format"]),
        "-movflags", "+faststart", "-y", str(target),
    ]
    return command


def generate_corpus(
    sources: list[tuple[str, Path]],
    root: Path,
    *,
    seed: int = SEED,
    positions_per_source: int = POSITIONS_PER_SOURCE,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    overlay = root / "assets" / "subtitle-overlay.png"
    create_subtitle_overlay(overlay)
    source_records, positions = select_positions(
        sources, seed=seed, positions_per_source=positions_per_source
    )
    source_paths = {record["alias"]: Path(record["path"]) for record in source_records}
    queries: list[dict[str, Any]] = []
    total = len(positions) * len(TRANSFORMATIONS)
    completed = 0
    for position in positions:
        for name in TRANSFORMATIONS:
            completed += 1
            spec = transformation_spec(name)
            case_id = f"{position['position_id']}-{name}"
            target = root / "queries" / name / f"{case_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            command = _query_command(
                source_paths[position["source_alias"]],
                target,
                float(position["source_start_seconds"]),
                spec,
                overlay,
            )
            expected_duration = SOURCE_SPAN_SECONDS / float(spec["speed_ratio"])
            generated_seconds = 0.0
            reuse = False
            if target.exists():
                try:
                    reuse = abs(probe_media(target).duration - expected_duration) <= 0.12
                except Exception:
                    reuse = False
            if not reuse:
                started = time.perf_counter()
                _run(command)
                generated_seconds = time.perf_counter() - started
            metadata = probe_media(target)
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
                    "generation_elapsed_seconds": round(generated_seconds, 6),
                    "transformation_spec": spec,
                }
            )
            print(f"corpus {completed}/{total} {case_id} {'reused' if reuse else 'generated'}", flush=True)
    return {
        "schema_version": "phase2b1-raw-corpus-v0.1",
        "seed": seed,
        "positions_per_source": positions_per_source,
        "source_span_seconds": SOURCE_SPAN_SECONDS,
        "sources": source_records,
        "positions": positions,
        "transformations": list(TRANSFORMATIONS),
        "subtitle_overlay": {
            "path": str(overlay.resolve()),
            "kind": "actual_raster_text",
            "font": "Pillow built-in default bitmap font",
            "fixed_style_version": "phase2b1-subtitle-v1",
        },
        "queries": queries,
    }


def generate_negative_queries(root: Path, count: int = 20) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index in range(count):
        case_id = f"NEGATIVE_{index + 1:03d}"
        target = root / f"{case_id}.mp4"
        hue = (index * 37) % 360
        grid = 64 + (index % 5) * 16
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc2=size=1280x720:rate=25:duration=8", "-vf",
            f"hue=h={hue}:s={0.7 + (index % 4) * 0.15:.2f},"
            f"drawgrid=w={grid}:h={grid}:t=2:c=white@0.35",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-y", str(target),
        ]
        if not target.exists():
            _run(command)
        records.append(
            {
                "case_id": case_id,
                "position_id": case_id,
                "transformation": "NEGATIVE",
                "query_duration_seconds": probe_media(target).duration,
                "path": str(target.resolve()),
            }
        )
        print(f"negative {index + 1}/{count} {case_id}", flush=True)
    return {"schema_version": "phase2b1-negative-corpus-v0.1", "queries": records}


def preprocess_dino(images: np.ndarray) -> np.ndarray:
    """Resize RGB uint8 frames, apply ImageNet normalization, return NCHW float32."""
    array = np.asarray(images)
    if array.ndim == 3:
        array = array[None, ...]
    if array.ndim != 4 or array.shape[-1] != 3:
        raise ValueError("DINO input must be RGB [N,H,W,3]")
    resized: list[np.ndarray] = []
    for image in array:
        if image.dtype != np.uint8:
            raise ValueError("DINO input must be uint8")
        if image.shape[:2] != (DINO_INPUT_SIDE, DINO_INPUT_SIDE):
            image = np.asarray(
                Image.fromarray(image).resize(
                    (DINO_INPUT_SIDE, DINO_INPUT_SIDE), Image.Resampling.BICUBIC
                ),
                dtype=np.uint8,
            )
        resized.append(image)
    values = np.asarray(resized, dtype=np.float32) / np.float32(255.0)
    values = (values - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(values.transpose(0, 3, 1, 2), dtype=np.float32)


class DinoOnnxEncoder:
    """Direct ONNX Runtime DINOv2-small encoder; independent of VDF runtime."""

    name = "dinov2-small-int8"
    version = "phase2b1-direct-onnx-v1"

    def __init__(self, model_path: Path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = min(8, max(1, ( __import__("os").cpu_count() or 2) // 2))
        self.session = ort.InferenceSession(
            str(model_path.resolve()), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input = self.session.get_inputs()[0]
        outputs = self.session.get_outputs()
        self.output = next(
            (item for item in outputs if item.name in {"image_embeds", "pooler_output"}),
            outputs[0],
        )
        self.ort_version = ort.__version__
        self.providers = self.session.get_providers()

    def encode_images(self, images: np.ndarray) -> np.ndarray:
        tensor = preprocess_dino(images)
        output = np.asarray(
            self.session.run([self.output.name], {self.input.name: tensor})[0],
            dtype=np.float32,
        )
        if output.ndim == 3:
            output = output[:, 0, :]
        if output.ndim != 2:
            raise ValueError(f"unexpected DINO output shape: {output.shape}")
        if output.shape[1] != DINO_DIMENSION:
            raise ValueError(
                f"unexpected DINO embedding dimension: {output.shape[1]} != {DINO_DIMENSION}"
            )
        norms = np.linalg.norm(output, axis=1, keepdims=True)
        return np.asarray(output / np.maximum(norms, 1e-12), dtype=np.float32)

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "implementation_version": self.version,
            "input_name": self.input.name,
            "input_shape": list(self.input.shape),
            "input_type": self.input.type,
            "output_name": self.output.name,
            "output_shape": list(self.output.shape),
            "output_type": self.output.type,
            "embedding_dimension": DINO_DIMENSION,
            "preprocessing": {
                "decode": "ffmpeg-rgb24",
                "resize": "224x224-bicubic",
                "layout": "NCHW",
                "scale": "uint8/255",
                "mean": IMAGENET_MEAN.tolist(),
                "std": IMAGENET_STD.tolist(),
                "pooling": "CLS token at last_hidden_state[:,0,:]",
                "postprocess": "float32 L2 normalization",
            },
            "onnxruntime_version": self.ort_version,
            "providers": self.providers,
            "model_provenance": "DINOv2-small INT8 ONNX distributed with verified VDF 4.1.0 bundle",
            "metadata_verification": "independently inspected with ONNX Runtime",
        }


def iter_rgb_batches(
    path: Path,
    *,
    fps: float = QUERY_FPS,
    batch_size: int = DINO_BATCH_SIZE,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    frame_size = DINO_INPUT_SIDE * DINO_INPUT_SIDE * 3
    command = [
        "ffmpeg", "-v", "error", "-i", str(path), "-vf",
        f"fps={fps},scale={DINO_INPUT_SIDE}:{DINO_INPUT_SIDE}:flags=bicubic,format=rgb24",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file)
        try:
            assert process.stdout is not None
            frame_index = 0
            images: list[np.ndarray] = []
            times: list[float] = []
            while True:
                data = process.stdout.read(frame_size)
                if not data:
                    break
                if len(data) != frame_size:
                    raise RuntimeError(f"incomplete RGB frame from {path}")
                images.append(
                    np.frombuffer(data, dtype=np.uint8)
                    .reshape((DINO_INPUT_SIDE, DINO_INPUT_SIDE, 3))
                    .copy()
                )
                times.append(frame_index / fps)
                frame_index += 1
                if len(images) == batch_size:
                    yield np.asarray(times, dtype=np.float32), np.stack(images)
                    images, times = [], []
            if images:
                yield np.asarray(times, dtype=np.float32), np.stack(images)
            return_code = process.wait()
            if return_code:
                stderr_file.seek(0)
                raise RuntimeError(stderr_file.read().decode("utf-8", errors="replace"))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)


def extract_rgb_frames(path: Path) -> tuple[np.ndarray, np.ndarray]:
    times: list[np.ndarray] = []
    frames: list[np.ndarray] = []
    for batch_times, batch_frames in iter_rgb_batches(path):
        times.append(batch_times)
        frames.append(batch_frames)
    if not frames:
        return (
            np.empty((0,), dtype=np.float32),
            np.empty((0, DINO_INPUT_SIDE, DINO_INPUT_SIDE, 3), dtype=np.uint8),
        )
    return np.concatenate(times), np.concatenate(frames)


def exact_cosine_topk(scores: np.ndarray, k: int = TOP_K) -> tuple[np.ndarray, np.ndarray]:
    """Return deterministic score-desc/index-asc exact top-k for one score vector."""
    values = np.asarray(scores, dtype=np.float32)
    if values.ndim != 1:
        raise ValueError("scores must be one-dimensional")
    count = min(k, values.size)
    if count == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)
    selected = np.lexsort((np.arange(values.size), -values))[:count]
    return selected.astype(np.int64), values[selected]


def map_source_time(source_start: float, speed_ratio: float, accused_time: float) -> float:
    return source_start + speed_ratio * accused_time


def _save_index(path: Path, payload: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **payload)


def _load_index(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as payload:
        return {key: payload[key] for key in payload.files}


def build_source_indexes(
    manifest: dict[str, Any],
    model_path: Path,
    output_dir: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dino_path = output_dir / "dino-source-index.npz"
    dct_path = output_dir / "dct-source-index.npz"
    metadata_path = output_dir / "index-metadata.json"
    encoder = DinoOnnxEncoder(model_path)
    expected_identity = {
        "schema_version": "phase2b1-raw-index-v0.1",
        "source_fps": SOURCE_FPS,
        "source_identity": [
            {
                "alias": source["alias"],
                "path": source["path"],
                "size_bytes": source["size_bytes"],
                "mtime_ns": source["mtime_ns"],
            }
            for source in manifest["sources"]
        ],
        "model_identity": {
            "path": str(model_path.resolve()),
            "size_bytes": model_path.stat().st_size,
            "mtime_ns": model_path.stat().st_mtime_ns,
        },
        "dino": encoder.metadata(),
        "dct": DCTVisualEncoder().cache_identity(),
    }
    if not force and dino_path.exists() and dct_path.exists() and metadata_path.exists():
        existing = _read_json(metadata_path)
        comparable_existing = {key: value for key, value in existing.items() if key != "runtime"}
        if comparable_existing == expected_identity:
            if "dino_index_array_ram_bytes" not in existing.get("runtime", {}):
                dino_cached = _load_index(dino_path)
                dct_cached = _load_index(dct_path)
                existing.setdefault("runtime", {}).update(
                    {
                        "dino_index_array_ram_bytes": sum(
                            array.nbytes for array in dino_cached.values()
                        ),
                        "dct_index_array_ram_bytes": sum(
                            array.nbytes for array in dct_cached.values()
                        ),
                    }
                )
                _write_json(metadata_path, existing)
            print("source indexes reused", flush=True)
            return existing

    aliases: list[str] = []
    times: list[float] = []
    dino_vectors: list[np.ndarray] = []
    dct_vectors: list[np.ndarray] = []
    dct_encoder = DCTVisualEncoder()
    dino_elapsed = 0.0
    dct_elapsed = 0.0
    for source in manifest["sources"]:
        alias = str(source["alias"])
        path = Path(source["path"])
        source_times: list[np.ndarray] = []
        source_vectors: list[np.ndarray] = []
        started = time.perf_counter()
        for batch_times, frames in iter_rgb_batches(path, fps=SOURCE_FPS):
            source_times.append(batch_times)
            source_vectors.append(encoder.encode_images(frames))
        dino_elapsed += time.perf_counter() - started
        current_times = np.concatenate(source_times)
        current_vectors = np.concatenate(source_vectors)
        dino_vectors.append(current_vectors)

        started = time.perf_counter()
        gray = extract_grayscale_frames(
            path, fps=SOURCE_FPS, width=DCT_WIDTH, height=DCT_HEIGHT
        )
        dct_elapsed += time.perf_counter() - started
        gray_times = np.asarray([item[0] for item in gray], dtype=np.float32)
        if not np.array_equal(current_times, gray_times):
            raise ValueError(f"DINO and DCT source grids differ for {alias}")
        started = time.perf_counter()
        dct_vectors.append(dct_encoder.encode_images(frame for _, frame in gray))
        dct_elapsed += time.perf_counter() - started
        aliases.extend([alias] * len(current_times))
        times.extend(float(value) for value in current_times)
        print(f"indexed {alias}: {len(current_times)} frames", flush=True)

    common = {
        "source_alias": np.asarray(aliases, dtype="U32"),
        "source_time": np.asarray(times, dtype=np.float32),
    }
    dino_payload = {**common, "embedding": np.concatenate(dino_vectors).astype(np.float32)}
    dct_payload = {**common, "embedding": np.concatenate(dct_vectors).astype(np.float32)}
    _save_index(dino_path, dino_payload)
    _save_index(dct_path, dct_payload)
    expected_identity["runtime"] = {
        "dino_index_seconds": round(dino_elapsed, 6),
        "dct_decode_encode_seconds": round(dct_elapsed, 6),
        "source_frame_count": len(times),
        "dino_index_bytes": dino_path.stat().st_size,
        "dct_index_bytes": dct_path.stat().st_size,
        "dino_index_array_ram_bytes": sum(array.nbytes for array in dino_payload.values()),
        "dct_index_array_ram_bytes": sum(array.nbytes for array in dct_payload.values()),
    }
    _write_json(metadata_path, expected_identity)
    return expected_identity


def _first_correct_rank(scores: np.ndarray, correct_mask: np.ndarray) -> int | None:
    indexes = np.flatnonzero(correct_mask)
    if indexes.size == 0:
        return None
    correct_scores = scores[indexes]
    best_score = float(correct_scores.max())
    best_index = int(indexes[np.flatnonzero(correct_scores == best_score)[0]])
    return int(
        1
        + np.count_nonzero(scores > best_score)
        + np.count_nonzero((scores == best_score) & (np.arange(scores.size) < best_index))
    )


def evaluate_positive_case(
    case: dict[str, Any],
    backend: str,
    query_times: np.ndarray,
    query_embeddings: np.ndarray,
    index: dict[str, np.ndarray],
    *,
    flip_embeddings: np.ndarray | None = None,
) -> dict[str, Any]:
    if backend == "DINO_FLIP_TTA" and flip_embeddings is None:
        raise ValueError("flip embeddings required for DINO_FLIP_TTA")
    aliases = index["source_alias"]
    source_times = index["source_time"]
    source_embeddings = index["embedding"]
    frame_records: list[dict[str, Any]] = []
    started_retrieval = time.perf_counter()
    for frame_index, (query_time, query_embedding) in enumerate(zip(query_times, query_embeddings)):
        scores = source_embeddings @ query_embedding
        if flip_embeddings is not None:
            scores = np.maximum(scores, source_embeddings @ flip_embeddings[frame_index])
        indexes, top_scores = exact_cosine_topk(scores, TOP_K)
        expected_time = map_source_time(
            float(case["source_start_seconds"]), float(case["speed_ratio"]), float(query_time)
        )
        correct_source = aliases == str(case["source_alias"])
        masks = {
            "0.5": correct_source & (np.abs(source_times - expected_time) <= STRICT_TOLERANCE_SECONDS + 1e-7),
            "1.0": correct_source & (np.abs(source_times - expected_time) <= PRIMARY_TOLERANCE_SECONDS + 1e-7),
        }
        primary = masks["1.0"]
        true_score = float(scores[primary].max()) if primary.any() else None
        wrong_score = float(scores[~primary].max()) if (~primary).any() else None
        frame_records.append(
            {
                "frame_index": frame_index,
                "accused_time": float(query_time),
                "expected_source_time": expected_time,
                "episode_top1": bool(aliases[indexes[0]] == case["source_alias"]),
                "episode_top8": bool(np.any(aliases[indexes] == case["source_alias"])),
                "hits_at_1": {
                    tolerance: bool(mask[indexes[0]]) for tolerance, mask in masks.items()
                },
                "hits_at_8": {
                    tolerance: bool(np.any(mask[indexes])) for tolerance, mask in masks.items()
                },
                "first_correct_rank": {
                    tolerance: _first_correct_rank(scores, mask)
                    for tolerance, mask in masks.items()
                },
                "true_candidate_score": true_score,
                "best_wrong_candidate_score": wrong_score,
                "score_margin": (
                    true_score - wrong_score
                    if true_score is not None and wrong_score is not None
                    else None
                ),
                "top8": [
                    {
                        "rank": rank,
                        "accused_time": float(query_time),
                        "source_id": str(aliases[index]),
                        "source_time": float(source_times[index]),
                        "score": float(score),
                    }
                    for rank, (index, score) in enumerate(zip(indexes, top_scores), start=1)
                ],
            }
        )
    retrieval_elapsed = time.perf_counter() - started_retrieval
    frame_count = len(frame_records)
    rates: dict[str, float] = {}
    for tolerance in ("0.5", "1.0"):
        for k in (1, 8):
            rates[f"source_aware_recall_at_{k}_tolerance_{tolerance}"] = (
                sum(row[f"hits_at_{k}"][tolerance] for row in frame_records) / frame_count
                if frame_count else 0.0
            )
    primary_rate = rates["source_aware_recall_at_8_tolerance_1.0"]
    return {
        "case_id": case["case_id"],
        "position_id": case["position_id"],
        "source_alias": case["source_alias"],
        "source_start_seconds": case["source_start_seconds"],
        "normalized_source_start": case["normalized_source_start"],
        "speed_ratio": case["speed_ratio"],
        "transformation": case["transformation"],
        "backend": backend,
        "query_frame_count": frame_count,
        "episode_top1_frame_count": sum(row["episode_top1"] for row in frame_records),
        "episode_top8_frame_count": sum(row["episode_top8"] for row in frame_records),
        "rates": rates,
        "position_usable": {
            f"{threshold:.2f}": primary_rate + 1e-12 >= threshold
            for threshold in POSITION_USABILITY_THRESHOLDS
        },
        "retrieval_elapsed_seconds": round(retrieval_elapsed, 6),
        "frames": frame_records,
    }


def _max_temporal_concentration(
    aliases: list[str], source_times: list[float], query_times: np.ndarray
) -> float:
    if not aliases:
        return 0.0
    best = 0
    for alias in set(aliases):
        offsets = sorted(
            source_time - float(query_time)
            for source_alias, source_time, query_time in zip(aliases, source_times, query_times)
            if source_alias == alias
        )
        left = 0
        for right, value in enumerate(offsets):
            while value - offsets[left] > 2.0:
                left += 1
            best = max(best, right - left + 1)
    return best / len(aliases)


def evaluate_negative_case(
    case: dict[str, Any],
    backend: str,
    query_times: np.ndarray,
    query_embeddings: np.ndarray,
    index: dict[str, np.ndarray],
    *,
    flip_embeddings: np.ndarray | None = None,
) -> dict[str, Any]:
    aliases = index["source_alias"]
    source_times = index["source_time"]
    source_embeddings = index["embedding"]
    top1_aliases: list[str] = []
    top1_times: list[float] = []
    top1_scores: list[float] = []
    top8_scores: list[float] = []
    frames: list[dict[str, Any]] = []
    started = time.perf_counter()
    for frame_index, (query_time, query_embedding) in enumerate(zip(query_times, query_embeddings)):
        scores = source_embeddings @ query_embedding
        if flip_embeddings is not None:
            scores = np.maximum(scores, source_embeddings @ flip_embeddings[frame_index])
        indexes, selected_scores = exact_cosine_topk(scores, TOP_K)
        top1_aliases.append(str(aliases[indexes[0]]))
        top1_times.append(float(source_times[indexes[0]]))
        top1_scores.append(float(selected_scores[0]))
        top8_scores.extend(float(value) for value in selected_scores)
        frames.append(
            {
                "frame_index": frame_index,
                "accused_time": float(query_time),
                "top8": [
                    {
                        "rank": rank,
                        "accused_time": float(query_time),
                        "source_id": str(aliases[index]),
                        "source_time": float(source_times[index]),
                        "score": float(score),
                    }
                    for rank, (index, score) in enumerate(zip(indexes, selected_scores), start=1)
                ],
            }
        )
    concentration = max(Counter(top1_aliases).values()) / len(top1_aliases) if top1_aliases else 0.0
    return {
        "case_id": case["case_id"],
        "backend": backend,
        "query_frame_count": len(query_times),
        "top1_scores": top1_scores,
        "top8_scores": top8_scores,
        "top1_source_concentration": concentration,
        "top1_temporal_coherence": _max_temporal_concentration(
            top1_aliases, top1_times, query_times
        ),
        "retrieval_elapsed_seconds": round(time.perf_counter() - started, 6),
        "frames": frames,
    }


def _encode_query(
    path: Path, dino: DinoOnnxEncoder, dct: DCTVisualEncoder
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, float], np.ndarray]:
    started = time.perf_counter()
    rgb_times, rgb = extract_rgb_frames(path)
    dino_raw = dino.encode_images(rgb)
    dino_flip = dino.encode_images(rgb[:, :, ::-1, :])
    dino_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    gray = extract_grayscale_frames(
        path, fps=QUERY_FPS, width=DCT_WIDTH, height=DCT_HEIGHT
    )
    gray_times = np.asarray([item[0] for item in gray], dtype=np.float32)
    dct_vectors = dct.encode_images(frame for _, frame in gray)
    dct_elapsed = time.perf_counter() - started
    if not np.array_equal(rgb_times, gray_times):
        raise ValueError(f"DINO and DCT query grids differ for {path.name}")
    return (
        rgb_times,
        {"DCT": dct_vectors, "DINO_RAW": dino_raw, "DINO_FLIP_TTA": dino_raw},
        {"dino_raw_plus_flip_seconds": dino_elapsed, "dct_seconds": dct_elapsed},
        dino_flip,
    )


def run_benchmark(
    manifest: dict[str, Any],
    negatives: dict[str, Any],
    model_path: Path,
    index_dir: Path,
) -> dict[str, Any]:
    started_all = time.perf_counter()
    started_index = time.perf_counter()
    index_metadata = build_source_indexes(manifest, model_path, index_dir)
    index_prepare_elapsed = time.perf_counter() - started_index
    indexes = {
        "DCT": _load_index(index_dir / "dct-source-index.npz"),
        "DINO_RAW": _load_index(index_dir / "dino-source-index.npz"),
        "DINO_FLIP_TTA": _load_index(index_dir / "dino-source-index.npz"),
    }
    if not (
        np.array_equal(indexes["DCT"]["source_alias"], indexes["DINO_RAW"]["source_alias"])
        and np.array_equal(indexes["DCT"]["source_time"], indexes["DINO_RAW"]["source_time"])
    ):
        raise ValueError("DCT and DINO source identity/time grids differ")
    dino = DinoOnnxEncoder(model_path)
    dct = DCTVisualEncoder()
    positives: list[dict[str, Any]] = []
    negative_rows: list[dict[str, Any]] = []
    encoding_times: list[dict[str, Any]] = []
    total = len(manifest["queries"]) + len(negatives["queries"])
    complete = 0
    for case in manifest["queries"]:
        query_times, embeddings, elapsed, flip = _encode_query(Path(case["path"]), dino, dct)
        encoding_times.append({"case_id": case["case_id"], **elapsed})
        for backend in BACKENDS:
            positives.append(
                evaluate_positive_case(
                    case,
                    backend,
                    query_times,
                    embeddings[backend],
                    indexes[backend],
                    flip_embeddings=flip if backend == "DINO_FLIP_TTA" else None,
                )
            )
        complete += 1
        print(f"retrieval {complete}/{total} {case['case_id']}", flush=True)
    for case in negatives["queries"]:
        query_times, embeddings, elapsed, flip = _encode_query(Path(case["path"]), dino, dct)
        encoding_times.append({"case_id": case["case_id"], **elapsed})
        for backend in BACKENDS:
            negative_rows.append(
                evaluate_negative_case(
                    case,
                    backend,
                    query_times,
                    embeddings[backend],
                    indexes[backend],
                    flip_embeddings=flip if backend == "DINO_FLIP_TTA" else None,
                )
            )
        complete += 1
        print(f"retrieval {complete}/{total} {case['case_id']}", flush=True)
    return {
        "schema_version": "phase2b1-frame-retrieval-raw-v0.1",
        "configuration": {
            "source_fps": SOURCE_FPS,
            "query_fps": QUERY_FPS,
            "top_k": TOP_K,
            "primary_tolerance_seconds": PRIMARY_TOLERANCE_SECONDS,
            "strict_tolerance_seconds": STRICT_TOLERANCE_SECONDS,
            "position_usability_thresholds": list(POSITION_USABILITY_THRESHOLDS),
            "similarity": "float32 cosine on L2-normalized embeddings; no threshold",
            "flip_tta": "max(original-query cosine, horizontal-flip-query cosine) per unique source frame",
            "negative_temporal_coherence": "largest same-source top1 offset cluster within a 2.0s-wide window",
        },
        "model": dino.metadata(),
        "index_metadata": index_metadata,
        "positive_cases": positives,
        "negative_cases": negative_rows,
        "encoding_times": encoding_times,
        "runtime": {
            "warm_index_prepare_seconds": round(index_prepare_elapsed, 6),
            "warm_full_query_run_seconds": round(time.perf_counter() - started_all, 6),
            "peak_resident_set_size_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "peak_rss_note": "ru_maxrss reported by macOS; measured at end of warm source-index run",
        },
    }


def _distribution(values: Iterable[float | int | None]) -> dict[str, Any]:
    clean = np.asarray([float(value) for value in values if value is not None], dtype=np.float64)
    if clean.size == 0:
        return {
            "count": 0,
            "minimum": None,
            "p05": None,
            "median": None,
            "p90": None,
            "p95": None,
            "maximum": None,
        }
    return {
        "count": int(clean.size),
        "minimum": float(clean.min()),
        "p05": float(np.quantile(clean, 0.05)),
        "median": float(np.median(clean)),
        "p90": float(np.quantile(clean, 0.90)),
        "p95": float(np.quantile(clean, 0.95)),
        "maximum": float(clean.max()),
    }


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _rank_buckets(values: Iterable[int | None]) -> dict[str, int]:
    buckets = {"rank_1": 0, "rank_2_8": 0, "rank_9_32": 0, "rank_33_128": 0, "rank_129_plus": 0}
    for value in values:
        if value is None:
            continue
        if value == 1:
            buckets["rank_1"] += 1
        elif value <= 8:
            buckets["rank_2_8"] += 1
        elif value <= 32:
            buckets["rank_9_32"] += 1
        elif value <= 128:
            buckets["rank_33_128"] += 1
        else:
            buckets["rank_129_plus"] += 1
    return buckets


def _positive_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    frames = [frame for row in rows for frame in row["frames"]]
    count = len(frames)
    metrics: dict[str, Any] = {
        "position_count": len({row["position_id"] for row in rows}),
        "position_transformation_cell_count": len(rows),
        "query_count": len(rows),
        "query_frame_count": count,
        "episode_top1_accuracy": _rate(sum(frame["episode_top1"] for frame in frames), count),
        "episode_top8_recall": _rate(sum(frame["episode_top8"] for frame in frames), count),
    }
    for tolerance in ("0.5", "1.0"):
        for k in (1, 8):
            metrics[f"source_aware_recall_at_{k}_tolerance_{tolerance}"] = _rate(
                sum(frame[f"hits_at_{k}"][tolerance] for frame in frames), count
            )
        metrics[f"first_correct_rank_tolerance_{tolerance}"] = _distribution(
            frame["first_correct_rank"][tolerance] for frame in frames
        )
        metrics[f"first_correct_rank_buckets_tolerance_{tolerance}"] = _rank_buckets(
            frame["first_correct_rank"][tolerance] for frame in frames
        )
    metrics["position_usable_at_50pct"] = _rate(
        sum(row["position_usable"]["0.50"] for row in rows), len(rows)
    )
    metrics["position_usable_at_75pct"] = _rate(
        sum(row["position_usable"]["0.75"] for row in rows), len(rows)
    )
    metrics["position_usable_at_100pct"] = _rate(
        sum(row["position_usable"]["1.00"] for row in rows), len(rows)
    )
    metrics["true_candidate_cosine_primary_tolerance"] = _distribution(
        frame["true_candidate_score"] for frame in frames
    )
    metrics["best_wrong_candidate_cosine_primary_tolerance"] = _distribution(
        frame["best_wrong_candidate_score"] for frame in frames
    )
    metrics["true_minus_best_wrong_margin"] = _distribution(
        frame["score_margin"] for frame in frames
    )
    metrics["retrieval_seconds_per_query"] = _distribution(
        row["retrieval_elapsed_seconds"] for row in rows
    )
    return metrics


def _negative_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "query_count": len(rows),
        "query_frame_count": sum(row["query_frame_count"] for row in rows),
        "nearest_neighbor_top1_cosine": _distribution(
            value for row in rows for value in row["top1_scores"]
        ),
        "top8_cosine": _distribution(value for row in rows for value in row["top8_scores"]),
        "top1_source_concentration_per_query": _distribution(
            row["top1_source_concentration"] for row in rows
        ),
        "top1_temporal_coherence_per_query": _distribution(
            row["top1_temporal_coherence"] for row in rows
        ),
        "retrieval_seconds_per_query": _distribution(
            row["retrieval_elapsed_seconds"] for row in rows
        ),
        "interpretation": "characterization only; every frame has a nearest neighbor and no FP threshold was fitted",
    }


def sanitized_aggregate(raw: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    positives = raw["positive_cases"]
    negatives = raw["negative_cases"]
    by_backend: dict[str, Any] = {}
    for backend in BACKENDS:
        backend_rows = [row for row in positives if row["backend"] == backend]
        by_transformation = {
            transformation: _positive_aggregate(
                [row for row in backend_rows if row["transformation"] == transformation]
            )
            for transformation in TRANSFORMATIONS
        }
        by_backend[backend] = {
            "all_positive": _positive_aggregate(backend_rows),
            "by_transformation": by_transformation,
            "by_source_alias": {
                alias: _positive_aggregate(
                    [row for row in backend_rows if row["source_alias"] == alias]
                )
                for alias in sorted({row["source_alias"] for row in backend_rows})
            },
            "negatives": _negative_aggregate(
                [row for row in negatives if row["backend"] == backend]
            ),
        }

    position_level: list[dict[str, Any]] = []
    normalized_by_position = {
        position["position_id"]: position["normalized_source_start"]
        for position in manifest["positions"]
    }
    for position_id in sorted(normalized_by_position):
        entry: dict[str, Any] = {
            "position_id": position_id,
            "normalized_source_start": normalized_by_position[position_id],
            "by_backend": {},
        }
        for backend in BACKENDS:
            entry["by_backend"][backend] = {}
            for transformation in TRANSFORMATIONS:
                row = next(
                    item
                    for item in positives
                    if item["position_id"] == position_id
                    and item["backend"] == backend
                    and item["transformation"] == transformation
                )
                entry["by_backend"][backend][transformation] = {
                    "query_frame_count": row["query_frame_count"],
                    "source_aware_recall_at_8_tolerance_1.0": row["rates"][
                        "source_aware_recall_at_8_tolerance_1.0"
                    ],
                    "usable_at_50pct": row["position_usable"]["0.50"],
                    "usable_at_75pct": row["position_usable"]["0.75"],
                    "usable_at_100pct": row["position_usable"]["1.00"],
                }
        position_level.append(entry)

    position_disagreement: dict[str, Any] = {}
    for transformation in TRANSFORMATIONS:
        rows_by_backend = {
            backend: {
                row["position_id"]: row["position_usable"]["0.50"]
                for row in positives
                if row["backend"] == backend and row["transformation"] == transformation
            }
            for backend in BACKENDS
        }
        dct = rows_by_backend["DCT"]
        raw_dino = rows_by_backend["DINO_RAW"]
        flip = rows_by_backend["DINO_FLIP_TTA"]
        position_disagreement[transformation] = {
            "dct_and_dino_raw_usable": sum(dct[key] and raw_dino[key] for key in dct),
            "dino_raw_only_usable": sum((not dct[key]) and raw_dino[key] for key in dct),
            "dct_only_usable": sum(dct[key] and (not raw_dino[key]) for key in dct),
            "neither_dct_nor_dino_raw_usable": sum(
                (not dct[key]) and (not raw_dino[key]) for key in dct
            ),
            "flip_tta_gained_vs_raw": sum((not raw_dino[key]) and flip[key] for key in dct),
            "flip_tta_lost_vs_raw": sum(raw_dino[key] and (not flip[key]) for key in dct),
        }

    model = dict(raw["model"])
    runtime = raw["index_metadata"].get("runtime", {})
    aggregate = {
        "schema_version": "phase2b1-dino-frame-retrieval-v0.1",
        "experiment_status": "CHARACTERIZATION_COMPLETE",
        "contains_media_identity": False,
        "contains_paths": False,
        "contains_hashes": False,
        "contains_per_frame_records": False,
        "frozen_baseline": {
            "commit": "0b5dc6b",
            "dct_encoder": "dct-low-frequency@1",
            "source_fps": SOURCE_FPS,
            "query_fps": QUERY_FPS,
            "top_k": TOP_K,
        },
        "protocol": {
            "seed": SEED,
            "primary_independence_unit": "content_position",
            "primary_tolerance_seconds": PRIMARY_TOLERANCE_SECONDS,
            "strict_tolerance_seconds": STRICT_TOLERANCE_SECONDS,
            "position_usable_definition": "at least 50% of frames have a correct source-aware top-8 candidate within +/-1.0s",
            "similarity_scale": "backend-specific raw cosine; no cross-backend threshold",
            "retrieval": "exact NumPy cosine; no FAISS",
        },
        "model": model,
        "sample_scope": {
            "source_count": len(manifest["sources"]),
            "position_count": len(manifest["positions"]),
            "positive_query_count": len(manifest["queries"]),
            "negative_query_count": len({row["case_id"] for row in negatives}),
            "transformation_count": len(TRANSFORMATIONS),
            "queries_per_transformation": len(manifest["positions"]),
        },
        "transformation_definitions": {
            name: transformation_spec(name) for name in TRANSFORMATIONS
        },
        "by_backend": by_backend,
        "position_level": position_level,
        "position_disagreement": position_disagreement,
        "runtime_resources": {
            "source_frame_count": runtime.get("source_frame_count"),
            "dino_source_index_seconds": runtime.get("dino_index_seconds"),
            "dct_source_decode_encode_seconds": runtime.get("dct_decode_encode_seconds"),
            "dino_index_bytes": runtime.get("dino_index_bytes"),
            "dct_index_bytes": runtime.get("dct_index_bytes"),
            "dino_index_array_ram_bytes": runtime.get("dino_index_array_ram_bytes"),
            "dct_index_array_ram_bytes": runtime.get("dct_index_array_ram_bytes"),
            "query_encoding_dino_raw_plus_flip_seconds": _distribution(
                row["dino_raw_plus_flip_seconds"] for row in raw["encoding_times"]
            ),
            "query_encoding_dct_seconds": _distribution(
                row["dct_seconds"] for row in raw["encoding_times"]
            ),
            **raw.get("runtime", {}),
        },
        "decision_scope": "representation recommendation only; not a Phase 2B or production gate",
    }
    validate_sanitized_payload(aggregate)
    return aggregate


def validate_sanitized_payload(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    forbidden_fragments = ("/Users/", "real_test", "EP01", "EP02", ".mp4", ".mov")
    if any(fragment in serialized for fragment in forbidden_fragments):
        raise ValueError("sanitized payload contains private media identity or path")
    forbidden_keys = {"path", "file_path", "sha256", "source_start_seconds", "source_end_seconds", "frames"}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden_keys & value.keys():
                raise ValueError(f"sanitized payload contains forbidden keys: {forbidden_keys & value.keys()}")
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    corpus = commands.add_parser("make-corpus")
    corpus.add_argument("--source", action="append", required=True, type=_parse_source)
    corpus.add_argument("--root", type=Path, required=True)
    corpus.add_argument("--output", type=Path, required=True)
    corpus.add_argument("--positions-per-source", type=int, default=POSITIONS_PER_SOURCE)

    negative = commands.add_parser("make-negatives")
    negative.add_argument("--root", type=Path, required=True)
    negative.add_argument("--count", type=int, default=20)
    negative.add_argument("--output", type=Path, required=True)

    index = commands.add_parser("build-index")
    index.add_argument("--manifest", type=Path, required=True)
    index.add_argument("--model", type=Path, required=True)
    index.add_argument("--output-dir", type=Path, required=True)
    index.add_argument("--force", action="store_true")

    run = commands.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--negatives", type=Path, required=True)
    run.add_argument("--model", type=Path, required=True)
    run.add_argument("--index-dir", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)

    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--raw", type=Path, required=True)
    aggregate.add_argument("--manifest", type=Path, required=True)
    aggregate.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "make-corpus":
        _write_json(
            args.output,
            generate_corpus(
                args.source, args.root, positions_per_source=args.positions_per_source
            ),
        )
    elif args.command == "make-negatives":
        _write_json(args.output, generate_negative_queries(args.root, args.count))
    elif args.command == "build-index":
        build_source_indexes(
            _read_json(args.manifest), args.model, args.output_dir, force=args.force
        )
    elif args.command == "run":
        _write_json(
            args.output,
            run_benchmark(
                _read_json(args.manifest),
                _read_json(args.negatives),
                args.model,
                args.index_dir,
            ),
        )
    elif args.command == "aggregate":
        _write_json(
            args.output,
            sanitized_aggregate(_read_json(args.raw), _read_json(args.manifest)),
        )


if __name__ == "__main__":
    main()
