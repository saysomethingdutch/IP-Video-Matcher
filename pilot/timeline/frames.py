"""Frames of the played span, cropped to the B站 web player (WO-102 §2.1)."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np

REF_SIZE = (1920, 1080)
DEFAULT_BOX = (168, 258, 1122, 632)     # the web player in Chrome at 1920×1080, samples 13–17 (x, y, w, h)
DUP_DIFF = 3.0                          # mean absolute difference (0–255) of 64×36 grey thumbnails


def probe(video: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(video)],
                         check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    return {"width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0),
            "duration": float((data.get("format") or {}).get("duration") or 0)}


def grab(video: Path, t: float, box=None, size=None) -> np.ndarray | None:
    """One frame at t seconds (BGR), cropped to box and resized to size when given."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    if not raw:
        return None
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    if box:
        x, y, w, h = box
        img = img[y:y + h, x:x + w]
    if size:
        img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    return img


def default_box(width: int, height: int) -> tuple[int, int, int, int]:
    sx, sy = width / REF_SIZE[0], height / REF_SIZE[1]
    x, y, w, h = DEFAULT_BOX
    return round(x * sx), round(y * sy), round(w * sx), round(h * sy)


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(0, min(ax + aw, bx + bw) - max(ax, bx)), max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter or 1)


def find_player(video: Path, span: tuple[float, float], info: dict, samples: int = 12) -> dict:
    """{"box", "method"}: the part of the page that changes during playback, when it is a plausible player
    (about 16:9, at least a third of the default size, in the left part of the page); otherwise the default
    layout, which also covers a static picture (a drive page shown for minutes changes nothing)."""
    default = default_box(info["width"], info["height"])
    a, b = span
    small = (480, 270)
    inside = [img for t, img in keyframes_small(video, size=small) if a + 2 <= t <= b - 2]
    pick = np.linspace(0, len(inside) - 1, min(samples, len(inside))).round().astype(int) if inside else []
    stack = [cv2.cvtColor(inside[i], cv2.COLOR_BGR2GRAY).astype(np.float32) for i in pick]
    if len(stack) < 4:
        return {"box": list(default), "method": "默认布局（取帧失败）"}
    std = np.std(np.stack(stack), axis=0)
    mask = (std > 12).astype(np.uint8)
    mask[: int(small[1] * 0.08)] = 0                       # tab and address bars
    mask[:, int(small[0] * 0.72):] = 0                     # right sidebar (danmaku list, recommendations, ads)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return {"box": list(default), "method": "默认布局（画面几乎不变）"}
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in stats[i, :4])
    sx, sy = info["width"] / small[0], info["height"] / small[1]
    found = (round(x * sx), round(y * sy), round(w * sx), round(h * sy))
    if _iou(found, default) >= 0.6:
        return {"box": list(default), "method": "默认布局（与画面变化区域一致）"}
    aspect = found[2] / max(1, found[3])
    if 1.5 <= aspect <= 2.0 and found[2] * found[3] >= default[2] * default[3] / 3:
        return {"box": list(found), "method": "画面变化区域（与默认布局不同）"}
    return {"box": list(default), "method": "默认布局（变化区域不像播放器）"}


def _filters(box, size) -> list[str]:
    vf = []
    if box:
        x, y, w, h = box
        vf.append(f"crop={w}:{h}:{x}:{y}")
    if size:
        vf.append(f"scale={size[0]}:{size[1]}")
    return vf


def keyframes_small(video: Path, box=None, size=(160, 90)) -> list[tuple[float, np.ndarray]]:
    """All keyframes in one fast pass (the recordings have one every ~5 s), small: [(seconds, BGR image)]."""
    proc = subprocess.run(["ffmpeg", "-v", "info", "-skip_frame", "nokey", "-i", str(video), "-vf",
                           ",".join(_filters(box, size) + ["showinfo"]), "-fps_mode", "vfr", "-f", "rawvideo",
                           "-pix_fmt", "bgr24", "-"], capture_output=True)
    times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)", proc.stderr.decode("utf-8", "replace"))]
    w, h = size
    data = np.frombuffer(proc.stdout, np.uint8)
    n = min(len(times), data.size // (w * h * 3))
    return [(times[i], data[i * w * h * 3:(i + 1) * w * h * 3].reshape(h, w, 3)) for i in range(n)]


def keyframes_to(video: Path, box, out_dir: Path) -> list[tuple[float, Path]]:
    """All keyframes, cropped, written as JPEG files by ffmpeg itself: [(seconds, path)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["ffmpeg", "-v", "info", "-y", "-skip_frame", "nokey", "-i", str(video), "-vf",
                           ",".join(_filters(box, None) + ["showinfo"]), "-fps_mode", "vfr", "-q:v", "2",
                           str(out_dir / "k%05d.jpg")], capture_output=True)
    times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)", proc.stderr.decode("utf-8", "replace"))]
    files = sorted(out_dir.glob("k*.jpg"))
    return list(zip(times, files))


def grid_pick(keys: list[float], span: tuple[float, float], step: float) -> list[tuple[float, float | None]]:
    """For every grid point k·step inside the span, the nearest keyframe within half a step, or None (an exact
    seek at the grid point). The grid starts at 0 s of the recording, not at the span: a shorter span later
    picks a subset of the same frames, so their cached answers are reused (WO-102-R1 §0)."""
    a, b = span
    out, used = [], set()
    for k in range(math.ceil(a / step - 1e-9), math.floor(b / step + 1e-9) + 1):
        g = k * step
        near = [t for t in keys if abs(t - g) <= step / 2 and t not in used and a - 0.5 <= t <= b + 0.5]
        best = min(near, key=lambda t: abs(t - g)) if near else None
        if best is not None:
            used.add(best)
        out.append((round(g, 2), best))
    return out


def sample(video: Path, span: tuple[float, float], step: float, box, out_dir: Path) -> list[dict]:
    """A player crop per grid point of the span (grid_pick), saved as JPEG named by its recording time:
    [{"t", "path"}]. Frames already on disk are kept, so reruns do not decode them again."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_keyframes"
    keys = dict(keyframes_to(video, box, tmp))
    frames = []
    for g, t in grid_pick(sorted(keys), span, step):
        name = out_dir / f"{(t if t is not None else g):08.2f}.jpg"
        if not name.exists():
            if t is not None:
                keys[t].replace(name)
            elif (img := grab(video, g, box=box)) is not None:           # long GOP or missing keyframe
                cv2.imwrite(str(name), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            else:
                continue
        frames.append({"t": round(t if t is not None else g, 2), "path": str(name)})
    for leftover in tmp.glob("k*.jpg"):
        leftover.unlink()
    tmp.rmdir()
    return frames


def thumb(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    return cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def dedupe(frames: list[dict], threshold: float = DUP_DIFF) -> int:
    """Mark frames that repeat their predecessor ("dup_of" = the first frame of the run); returns how many."""
    prev, first, n = None, None, 0
    for f in frames:
        cur = thumb(f["path"])
        if prev is not None and float(np.mean(np.abs(cur - prev))) < threshold:
            f["dup_of"] = first["t"]
            n += 1
        else:
            first = f
        prev = cur
    return n


def detect_span(video: Path, info: dict) -> tuple[float, float]:
    """Rough start and end of playback when the web ledger gives none: from the first to the last keyframe in
    which the player area differs from the previous one (the environment check and the desktop come before).
    Only a fallback; the ledger's span is preferred, and the output says which was used."""
    small = (480, 270)
    sx, sy = small[0] / info["width"], small[1] / info["height"]
    x, y, w, h = (round(v * s) for v, s in zip(default_box(info["width"], info["height"]), (sx, sy, sx, sy)))
    prev, moving = None, []
    for t, img in keyframes_small(video, size=small):
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        page = float(grey[max(0, y - 18):max(1, y - 4), x:x + w].mean()) > 200    # white page above the player
        cur = grey[y:y + h, x:x + w]
        if page and prev is not None and float(np.mean(np.abs(cur - prev[1]))) > 4:
            moving.append(prev[0] if not moving else t)
        prev = (t, cur) if page else None
    if len(moving) < 2:
        return 0.0, info["duration"]
    return max(0.0, moving[0]), min(info["duration"], moving[-1] + 5.0)
