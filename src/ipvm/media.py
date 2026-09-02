from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .models import MediaMetadata


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm"}


class MediaToolError(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise MediaToolError(f"Required executable not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.decode("utf-8", errors="replace").strip()
        raise MediaToolError(f"Command failed ({command[0]}): {details}") from exc


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _ratio(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    numerator, _, denominator = value.partition("/")
    return float(numerator) / float(denominator or 1)


def probe_media(path: Path) -> MediaMetadata:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    payload = json.loads(result.stdout)
    video = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), {})
    audio = next((item for item in payload.get("streams", []) if item.get("codec_type") == "audio"), {})
    tags = video.get("tags", {}) | payload.get("format", {}).get("tags", {})
    side_rotation = next(
        (entry.get("rotation") for entry in video.get("side_data_list", []) if "rotation" in entry),
        0,
    )
    if not video:
        raise MediaToolError(f"No video stream found: {path}")
    duration_value = video.get("duration") or payload.get("format", {}).get("duration")
    if duration_value is None or float(duration_value) <= 0:
        raise MediaToolError(f"Invalid or missing media duration: {path}")
    average_fps = _ratio(video.get("avg_frame_rate"))
    fps = average_fps if average_fps > 0 else _ratio(video.get("r_frame_rate"))
    return MediaMetadata(
        path=path.resolve(),
        duration=float(duration_value),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=fps,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name"),
        sample_rate=int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        rotation=int(tags.get("rotate", side_rotation) or 0),
        creation_time=tags.get("creation_time"),
    )


def extract_grayscale_frames(
    path: Path,
    fps: float,
    width: int,
    height: int,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
    *,
    input_seek: bool = False,
    reset_timestamps: bool = False,
) -> list[tuple[float, np.ndarray]]:
    frame_size = width * height
    command = ["ffmpeg", "-v", "error"]
    if start_seconds is not None and input_seek:
        command += ["-ss", f"{start_seconds:.6f}"]
    command += ["-i", str(path)]
    if start_seconds is not None and not input_seek:
        command += ["-ss", f"{start_seconds:.6f}"]
    if duration_seconds is not None:
        command += ["-t", f"{duration_seconds:.6f}"]
    filters = []
    if reset_timestamps:
        filters.append("setpts=PTS-STARTPTS")
    filters.extend([
        f"fps={fps}",
        f"scale={width}:{height}:flags=area",
        "format=gray",
    ])
    command += [
        "-vf", ",".join(filters),
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    frames: list[tuple[float, np.ndarray]] = []
    with tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file)
        except FileNotFoundError as exc:
            raise MediaToolError("Required executable not found: ffmpeg") from exc
        try:
            assert process.stdout is not None
            frame_index = 0
            while True:
                data = process.stdout.read(frame_size)
                if not data:
                    break
                if len(data) != frame_size:
                    raise MediaToolError(f"Incomplete frame from {path}")
                frame = np.frombuffer(data, dtype=np.uint8).reshape((height, width)).copy()
                frames.append((frame_index / fps, frame))
                frame_index += 1
            return_code = process.wait()
            if return_code:
                stderr_file.seek(0)
                details = stderr_file.read().decode("utf-8", errors="replace").strip()
                raise MediaToolError(f"ffmpeg frame extraction failed: {details}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return frames


def find_video_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
