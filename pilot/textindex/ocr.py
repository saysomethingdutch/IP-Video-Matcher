"""Stage 1: decode the bottom subtitle strip at 2 fps and OCR it with macOS Vision (WO-002a §3.1, §3.2).

The raw per-frame OCR of each episode is cached as gzip JSONL (ocr/EPxx.jsonl.gz) so that adding an
episode only OCRs the new file, and merge/flag changes never need a re-OCR.
Frames live in a temp dir only until their OCR line is back; each frame is deleted right after.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

FPS = 2.0
CROP_TOP = 0.78          # keep the strip [0.78 H, H]
LANGS = "zh-Hans"
OCR_VERSION = 1          # bump when anything above changes; invalidates caches
MAX_QUEUED_FRAMES = 6000  # caps temp disk use (~16 KB per strip frame)
DECODERS = 2

SWIFT_SRC = Path(__file__).with_name("vision_ocr.swift")
# "EP01.ts", "qsjx_ep01.ts", "ep1.mp4" -> episode number
EPISODE_RE = re.compile(r"(?:^|[^a-z])ep(\d+)\.(?:ts|mp4|mkv|mov)$", re.IGNORECASE)


@dataclass
class EpisodeJob:
    episode: int
    video: Path
    cache: Path
    fingerprint: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    crop: tuple[int, int, int, int] = (0, 0, 0, 0)
    decode_s: float = 0.0
    frames: dict[int, list] = field(default_factory=dict)
    errors: int = 0
    failed: str = ""


def episode_number(name: str) -> int | None:
    m = EPISODE_RE.search(name)
    return int(m.group(1)) if m else None


def find_episodes(directory: Path) -> dict[int, Path]:
    found = {}
    for p in sorted(directory.iterdir()):
        ep = episode_number(p.name)
        if ep is not None and p.is_file():
            if ep in found:
                raise SystemExit(f"episode {ep} appears twice: {found[ep].name}, {p.name}")
            found[ep] = p
    return found


def fingerprint(path: Path) -> str:
    """Cheap identity of a video file: size + SHA-1 of its first MiB."""
    with path.open("rb") as f:
        head = f.read(1 << 20)
    return f"{path.stat().st_size}:{hashlib.sha1(head).hexdigest()[:16]}"


def ocr_params() -> dict:
    return {"fps": FPS, "crop_top": CROP_TOP, "langs": LANGS, "ocr_version": OCR_VERSION}


def read_cache_header(cache: Path) -> dict | None:
    if not cache.exists():
        return None
    try:
        with gzip.open(cache, "rt", encoding="utf-8") as f:
            return json.loads(f.readline())
    except (OSError, ValueError):
        return None


def cache_is_valid(cache: Path, fp: str) -> bool:
    head = read_cache_header(cache)
    return bool(head) and head.get("fingerprint") == fp and head.get("params") == ocr_params()


def read_cache(cache: Path) -> tuple[dict, list[dict]]:
    """Returns (header, frames); each frame is {"i", "t", "items": [[text, conf, x, y, w, h], ...]}."""
    with gzip.open(cache, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline())
        frames = [json.loads(line) for line in f]
    return header, frames


def probe(video: Path) -> tuple[float, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True).stdout
    info = json.loads(out)
    s = info["streams"][0]
    return float(info["format"]["duration"]), int(s["width"]), int(s["height"])


def ensure_binary(bin_dir: Path) -> Path:
    """Compile vision_ocr.swift if the binary is missing or older than the source."""
    binary = bin_dir / "vision_ocr"
    if not binary.exists() or binary.stat().st_mtime < SWIFT_SRC.stat().st_mtime:
        bin_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(SWIFT_SRC), "-o", str(binary)], check=True)
    return binary


class _OcrProc:
    """One long-lived vision_ocr process driven in lock-step (one path in, one JSON line out)."""

    def __init__(self, binary: Path):
        self.binary = binary
        self.proc = None

    def run(self, path: Path) -> dict:
        for _ in range(2):
            if self.proc is None or self.proc.poll() is not None:
                self.proc = subprocess.Popen([str(self.binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             text=True, encoding="utf-8", bufsize=1,
                                             env={**os.environ, "VISION_OCR_LANGS": LANGS})
            try:
                self.proc.stdin.write(f"{path}\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
                if line:
                    return json.loads(line)
            except (OSError, ValueError):
                pass
            self.close()
        return {"frame": str(path), "error": "ocr process failed"}

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


def _decode(job: EpisodeJob, frame_dir: Path) -> list[Path]:
    job.duration, job.width, job.height = probe(job.video)
    y0 = int(job.height * CROP_TOP) // 2 * 2
    job.crop = (0, y0, job.width, job.height - y0)
    frame_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    # Hardware decoding (VideoToolbox) gives bit-identical frames with ~1/6 of the CPU, leaving the CPU to
    # the OCR processes; fall back to software decoding if it is unavailable.
    for hw in (["-hwaccel", "videotoolbox"], []):
        for old in frame_dir.glob("*.jpg"):
            old.unlink()
        done = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *hw, "-i", str(job.video),
             "-vf", f"fps={FPS:g},crop={job.width}:{job.height - y0}:0:{y0}",
             "-q:v", "2", "-start_number", "0", str(frame_dir / "%06d.jpg")],
            capture_output=True, text=True)
        if done.returncode == 0:
            break
    else:
        raise OSError(f"ffmpeg failed on {job.video}: {done.stderr.strip()[-300:]}")
    job.decode_s = time.monotonic() - t0
    return sorted(frame_dir.glob("*.jpg"))


def _write_cache(job: EpisodeJob, ocr_s: float):
    frames = [job.frames[i] for i in sorted(job.frames)]
    header = {"episode": job.episode, "file": job.video.name, "fingerprint": job.fingerprint,
              "params": ocr_params(), "duration": job.duration, "width": job.width, "height": job.height,
              "crop": list(job.crop), "n_frames": len(frames), "ocr_errors": job.errors,
              "decode_s": round(job.decode_s, 2), "ocr_s": round(ocr_s, 2)}
    job.cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = job.cache.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for fr in frames:
            f.write(json.dumps(fr, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(job.cache)


def run_ocr(jobs: list[EpisodeJob], binary: Path, tmp_root: Path, workers: int = 4, log=print) -> None:
    """Decode episodes (DECODERS at a time) and OCR their frames with `workers` parallel Vision processes."""
    frame_q: queue.Queue = queue.Queue(maxsize=MAX_QUEUED_FRAMES)
    lock = threading.Lock()
    remaining: dict[int, int] = {}
    started: dict[int, float] = {}

    todo: queue.Queue = queue.Queue()
    for job in jobs:
        todo.put(job)
    decoders_left = [DECODERS]

    def decoder():
        while True:
            try:
                job = todo.get_nowait()
            except queue.Empty:
                break
            frame_dir = tmp_root / f"EP{job.episode:02d}"
            try:
                files = _decode(job, frame_dir)
                if not files:
                    raise ValueError("no frames")
            except (subprocess.CalledProcessError, OSError, KeyError, IndexError, ValueError) as exc:
                job.failed = f"decode failed: {exc}"
                log(f"EP{job.episode:02d}: {job.failed}")
                shutil.rmtree(frame_dir, ignore_errors=True)
                continue
            log(f"EP{job.episode:02d}: decoded {len(files)} frames in {job.decode_s:.0f}s")
            with lock:
                remaining[job.episode] = len(files)
            for idx, f in enumerate(files):
                frame_q.put((job, idx, f))
        with lock:
            decoders_left[0] -= 1
            last = decoders_left[0] == 0
        if last:
            for _ in range(workers):
                frame_q.put(None)

    def worker():
        ocr = _OcrProc(binary)
        try:
            while (item := frame_q.get()) is not None:
                job, idx, f = item
                with lock:
                    started.setdefault(job.episode, time.monotonic())
                rec = ocr.run(f)
                f.unlink(missing_ok=True)
                items = [[o["text"], round(o["conf"], 3), round(o["x"], 4), round(o["y"], 4),
                          round(o["w"], 4), round(o["h"], 4)] for o in rec.get("items", [])]
                with lock:
                    job.frames[idx] = {"i": idx, "t": idx / FPS, "items": items}
                    job.errors += "error" in rec
                    remaining[job.episode] -= 1
                    done = remaining[job.episode] == 0
                if done:
                    ocr_s = time.monotonic() - started[job.episode]
                    _write_cache(job, ocr_s)
                    shutil.rmtree(tmp_root / f"EP{job.episode:02d}", ignore_errors=True)
                    log(f"EP{job.episode:02d}: OCR done, {len(job.frames)} frames, {ocr_s:.0f}s")
                    job.frames = {}
        finally:
            ocr.close()

    threads = [threading.Thread(target=decoder, daemon=True) for _ in range(DECODERS)]
    threads += [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        tmp_root.rmdir()   # every frame has been deleted by now
    except OSError:
        pass
