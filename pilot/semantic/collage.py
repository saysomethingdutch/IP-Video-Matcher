"""2x2 collages cropped to the player picture, for the §3.4b comparison only (D-018 / D-024).

No phone bezel, status bar, desk or hands: frames are landscape player frames with the UI hidden and
away from any seek (hands touch the screen there); the screen box comes from where the player UI sits in
this row's UI-visible frames, and every tile is inset once more. Existing WO-004a collages get the same
inset before they go to the outbox.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from statistics import median

from PIL import Image, ImageDraw

from pilot.content.extract import READING, SPEED, is_player_frame

INSET = 0.05               # trimmed from every side of a tile
TILE = (640, 360)
RECORDINGS = Path("/Volumes/Element/ip-video-matching-repo_de_material/七时吉祥/231012-哔哩哔哩-爱奇艺（安卓手机）")


def screen_box(frames: dict[int, list[dict]], t0: int, t1: int) -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) of the landscape screen from the player UI in UI-visible frames."""
    lefts, rights, tops, bottoms = [], [], [], []
    for t in range(t0, t1 + 1):
        items = frames.get(t, [])
        reading = [o for o in items if READING.search(o["text"]) and o["x"] < 0.3 and o["y"] > 0.5]
        speed = [o for o in items if SPEED.match(o["text"].strip()) and o["x"] > 0.6]
        if not (reading and speed):
            continue
        lefts.append(reading[0]["x"])
        rights.append(max(o["x"] + o["w"] for o in items if o["y"] > 0.5 and o["x"] > 0.6))
        tops.append(min(o["y"] for o in items))
        bottoms.append(max(o["y"] + o["h"] for o in items))
    if len(lefts) < 2:
        return None
    return median(lefts), median(tops), median(rights), median(bottoms)


def pick_times(row: dict, frames: dict[int, list[dict]], n: int = 4) -> list[int]:
    """Player frames with few text items (UI hidden), at least 3 s away from seeks and UI-visible frames."""
    t0, t1 = int(row["video_start"]), int(row["video_end"])
    seeks = [s["at"] for s in row["seeks"]] if isinstance(row.get("seeks"), list) else []
    busy = {t for t in range(t0, t1 + 1) if any(READING.search(o["text"]) for o in frames.get(t, []))}
    ok = [t for t in range(t0 + 2, t1 - 1)
          if is_player_frame(frames.get(t, [])) and len(frames.get(t, [])) <= 6
          and all(abs(t - s) > 3 for s in seeks) and all(abs(t - b) > 3 for b in busy)]
    if len(ok) < n:
        return []
    return [ok[int((i + 0.5) * len(ok) / n)] for i in range(n)]


def make_collage(row: dict, frames: dict[int, list[dict]], out: Path) -> Path | None:
    box = screen_box(frames, int(row["video_start"]), int(row["video_end"]))
    times = pick_times(row, frames)
    if not box or not times:
        return None
    left, top, right, bottom = box
    tiles = []
    video = RECORDINGS / f"{row['video']}.MP4"
    for t in times:
        tmp = out.with_suffix(f".{t}.jpg")
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(video),
                        "-frames:v", "1", "-y", str(tmp)], check=True)
        im = Image.open(tmp).convert("RGB")
        tmp.unlink()
        w, h = im.size
        tiles.append(_inset(im.crop((int(left * w), int(top * h), int(right * w), int(bottom * h)))))
    return _grid(tiles, out)


def _inset(im: Image.Image) -> Image.Image:
    w, h = im.size
    return im.crop((int(w * INSET), int(h * INSET), int(w * (1 - INSET)), int(h * (1 - INSET)))).resize(TILE)


def _grid(tiles: list[Image.Image], out: Path) -> Path:
    grid = Image.new("RGB", (TILE[0] * 2, TILE[1] * 2))
    draw = ImageDraw.Draw(grid)
    for i, tile in enumerate(tiles):
        x, y = (i % 2) * TILE[0], (i // 2) * TILE[1]
        grid.paste(tile, (x, y))
        draw.rectangle((x, y, x + 22, y + 22), fill=(20, 20, 20))
        draw.text((x + 7, y + 5), str(i + 1), fill=(255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out, quality=88)
    return out


def inset_existing(src: Path, out: Path) -> Path:
    """A WO-004a 2x2 collage with every tile inset again (removes the bezel slivers at the edges)."""
    im = Image.open(src).convert("RGB")
    w, h = im.size
    tiles = [_inset(im.crop((x, y, x + w // 2, y + h // 2))) for y in (0, h // 2) for x in (0, w // 2)]
    return _grid(tiles, out)


def to_outbox(collage: Path, outbox: Path) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    dest = outbox / collage.name
    if collage.resolve() != dest.resolve():
        shutil.copy2(collage, dest)
    return dest
