"""Locate the video player in each evidence frame before visual inference."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .full import LANDSCAPE, PORTRAIT_PLAYER


def ocr_index(case: Path, video: str) -> dict[int, list[dict]]:
    paths = list((case / "ocr_cache").glob(f"{video}-*.jsonl"))
    if len(paths) != 1:
        raise FileNotFoundError(f"one OCR cache required for {video}; found {len(paths)}")
    return {round(frame["t"]): frame.get("items", [])
            for frame in (json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines())}


def _page_mask(frame: Image.Image) -> np.ndarray:
    # The phone's white detail page has a blue cast in the source recording.
    # RGB brightness alone misses it and would confuse bright drama scenes.
    rgb = np.asarray(frame, dtype=np.int16)
    return (rgb[:, :, 2] - rgb[:, :, 0] > 35) & (rgb[:, :, 2] > 150) & (rgb[:, :, 0] > 70)


def _runs(values: np.ndarray, max_gap: int = 3) -> list[tuple[int, int]]:
    indexes = np.flatnonzero(values)
    groups: list[list[int]] = []
    for index in indexes:
        if not groups or index > groups[-1][-1] + max_gap + 1:
            groups.append([int(index)])
        else:
            groups[-1].append(int(index))
    return [(g[0], g[-1] + 1) for g in groups]


def player_box(frame: Image.Image, video: str, second: float,
               ocr: dict[int, list[dict]]) -> tuple[str, tuple[int, int, int, int], dict]:
    """Use the page below the portrait player and OCR tabs to find its canvas."""
    items = ocr.get(round(second), [])
    nominal = PORTRAIT_PLAYER[video]
    if int(video[-1]) <= 4:
        # These recordings occasionally show a portrait detail page while the
        # physical phone is horizontal. The page is to the right of the player,
        # and OCR text is sideways in the source camera frame.
        side_text = sum(1 for item in items if 400 < item.get("x", 0) * 1280 < 1120
                        and 100 < item.get("y", 0) * 720 < 620 and str(item.get("text", "")))
        if side_text >= 12:
            return "rotated_portrait", (max(140, nominal[0] + 10), 100,
                                         min(400, nominal[2] - 10), 580), {"side_ocr": side_text}
    region = (max(0, nominal[0] - 65), min(1279, nominal[2] + 65))
    page = _page_mask(frame)
    profile = page[280:600].mean(axis=0)
    bands = [(a, b) for a, b in _runs(profile > .68)
             if b - a >= 180 and a < region[1] and b > region[0]]
    if bands:
        x0, x1 = max(bands, key=lambda pair: pair[1] - pair[0])
        x0, x1 = max(0, x0 - 4), min(1280, x1 + 4)
    else:
        x0, x1 = nominal[0], nominal[2]
    detail_texts = [item for item in items if 100 < item.get("y", 0) * 720 < 350
                    and x0 - 30 < item.get("x", 0) * 1280 < x1 + 30
                    and ("简介" in str(item.get("text", "")) or "评论" in str(item.get("text", "")))]
    body_texts = [item for item in items if x0 - 15 < item.get("x", 0) * 1280 < x1 + 15
                  and 235 < item.get("y", 0) * 720 < 680 and str(item.get("text", ""))]
    # A long pale page band and several OCR lines are both needed; a blue sky
    # in the underlying video cannot by itself switch a horizontal player.
    portrait = bool(bands and (len(body_texts) >= 4 or detail_texts))
    if not portrait:
        return "landscape", LANDSCAPE[video], {"body_ocr": len(body_texts), "page_band": bands}
    line = page[:, x0:x1].mean(axis=1)
    candidates = [y for y in range(150, 350)
                  if np.median(line[y:y + 20]) > .75 and np.min(line[y:y + 20]) > .55]
    page_top = min(candidates) + 1 if candidates else nominal[3]
    tab_y = min((int(item["y"] * 720) for item in detail_texts), default=None)
    if tab_y is not None and abs(page_top - tab_y) > 35:
        page_top = tab_y - 6
    y0 = 45  # below the phone status bar, seen by OCR around y=30–40
    y1 = max(y0 + 80, min(350, page_top))
    return "portrait", (x0, y0, x1, y1), {"body_ocr": len(body_texts),
             "page_band": bands, "tab_y": tab_y, "page_top": page_top}


def trim_persistent_black(box: tuple[int, int, int, int], frames: list[Image.Image]) -> tuple[int, int, int, int]:
    """Trim only rows/columns near black in almost every sampled frame."""
    if not frames:
        return box
    x0, y0, x1, y1 = box
    # The old R1 rectangles occasionally reach the wooden table by a few
    # pixels. Remove that stable warm strip before looking for black bars.
    original = np.stack([np.asarray(im.crop(box), dtype=np.int16) for im in frames])
    warm = ((original[:, :, :, 0] > 80) &
            (original[:, :, :, 0] > original[:, :, :, 1] + 10) &
            (original[:, :, :, 0] > original[:, :, :, 2] + 25))
    warm_rows = warm.mean(axis=(0, 2)) > .7
    warm_top = next((i for i, v in enumerate(warm_rows[:50]) if not v), 0)
    warm_bottom = next((i for i, v in enumerate(warm_rows[::-1][:50]) if not v), 0)
    y0 += warm_top + (4 if warm_top else 0)
    y1 -= warm_bottom + (4 if warm_bottom else 0)
    box = (x0, y0, x1, y1)
    arr = np.stack([np.asarray(im.crop(box).resize((320, 180)), dtype=np.uint8) for im in frames])
    # Camera exposure lifts a pure-black phone letterbox into RGB 70–90.
    # Require the dark region to persist across frames and occupy most of an
    # outer row/column, so a single dark scene does not shrink the canvas.
    black = np.max(arr, axis=3) < 95
    persistent = np.mean(black, axis=0) > .9
    cols = persistent.mean(axis=0) > .78
    rows = persistent.mean(axis=1) > .78
    left = next((i for i, v in enumerate(cols) if not v), 0)
    right = next((i for i, v in enumerate(cols[::-1]) if not v), 0)
    top = next((i for i, v in enumerate(rows) if not v), 0)
    bottom = next((i for i, v in enumerate(rows[::-1]) if not v), 0)
    # Conservative limit: never trim away a scene simply because it is dark.
    left, right = min(left, 48), min(right, 48)
    top, bottom = min(top, 27), min(bottom, 27)
    return (x0 + round((x1 - x0) * left / 320), y0 + round((y1 - y0) * top / 180),
            x1 - round((x1 - x0) * right / 320), y1 - round((y1 - y0) * bottom / 180))


def tile(frame: Image.Image, box: tuple[int, int, int, int], rotated: bool = False) -> Image.Image:
    crop = frame.crop(box)
    if rotated:
        crop = crop.transpose(Image.Transpose.ROTATE_270)
    scale = min(1280 / crop.width, 720 / crop.height)
    crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
    result = Image.new("RGB", (1280, 720), "#101010")
    result.paste(crop, ((1280 - crop.width) // 2, (720 - crop.height) // 2))
    return result


def ratio_label(value: float) -> str:
    if value >= .8:
        return "约1"
    return min(((.8, "约4/5"), (2/3, "约2/3"), (.5, "约1/2"), (.25, "约1/4")),
               key=lambda pair: abs(pair[0] - value))[1]


def geometry_16(crops: list[Image.Image]) -> dict:
    """Use 16 played frames; require a persistent inset border before reducing area."""
    if len(crops) < 16:
        raise ValueError("geometry requires at least 16 played frames")
    gray = np.stack([np.asarray(im.resize((320, 180)).convert("L"), dtype=np.float32) for im in crops])
    # Average gradients detect a lasting inset boundary even when the drama
    # inside it is static. Temporal activity alone caused R2's dark-scene error.
    gx = np.mean(np.abs(np.diff(gray, axis=2)), axis=0)
    gy = np.mean(np.abs(np.diff(gray, axis=1)), axis=0)
    gx = ndimage.gaussian_filter(gx, 1)
    gy = ndimage.gaussian_filter(gy, 1)
    vscore = ndimage.gaussian_filter1d(gx.mean(axis=0), 2)
    hscore = ndimage.gaussian_filter1d(gy.mean(axis=1), 2)
    candidates = []
    for ya in np.argsort(hscore[25:155])[-16:] + 25:
        for xa in np.argsort(vscore[25:255])[-18:] + 25:
            for xb in np.argsort(vscore[65:310])[-18:] + 65:
                if xb - xa < 65 or xb - xa > 285:
                    continue
                # An inset often touches the canvas bottom; otherwise seek a
                # second persistent horizontal edge.
                for yb in (179, *list(np.argsort(hscore[ya + 35:179])[-8:] + ya + 35)) if ya + 35 < 179 else (179,):
                    if yb - ya < 35:
                        continue
                    area = (xb - xa) * (yb - ya) / (320 * 180)
                    if not .08 <= area < .8:
                        continue
                    top = float(np.mean(gy[ya, xa:xb]))
                    left = float(np.mean(gx[ya:yb, xa]))
                    right = float(np.mean(gx[ya:yb, xb - 1]))
                    bottom = float(np.mean(gy[min(yb - 1, 178), xa:xb])) if yb < 179 else 0
                    edges = sorted((top, left, right, bottom), reverse=True)
                    background = float(np.median(np.concatenate((gx.ravel(), gy.ravel()))))
                    if edges[2] < max(8, background * 2.1):
                        continue
                    strength = sum(edges[:3]) / 3
                    candidates.append((strength, area, (xa, ya, xb, yb), edges))
    if candidates:
        strength, area, rect, edges = max(candidates, key=lambda item: item[0])
        if strength < 25:
            return {"ratio": "不确定", "raw_ratio": None,
                    "candidate_rectangle": [int(v) for v in rect],
                    "border_strength": round(float(strength), 2),
                    "method": "16_frame_weak_inset_border", "sample_count": len(crops)}
        return {"ratio": ratio_label(float(area)), "raw_ratio": round(float(area), 3),
                "rectangle": [int(v) for v in rect], "border_strength": round(float(strength), 2),
                "method": "16_frame_persistent_inset_border", "sample_count": len(crops)}
    return {"ratio": "约1", "raw_ratio": 1.0,
            "method": "16_frame_no_persistent_inset_border", "sample_count": len(crops)}
