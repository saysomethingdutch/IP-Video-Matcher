"""Deterministically select 20 high-confidence rows and four source-video frames."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageDraw

from pilot.ledger.parse import parse_items


TYPES = ("Reaction", "切条", "解说", "原片")
# Frame overrides were visually checked against 16 evenly spaced source-video
# thumbnails per row. Row selection itself remains the fixed ascending rule.
FRAME_OVERRIDES = {
    "1051": (947, 948, 949, 950),
    "1117": (2236, 2247, 2292, 2337),
    # This entire session stays on a portrait detail page. The four times show
    # the embedded video playing, but its surrounding UI cannot be hidden.
    "1141": (3166, 3191, 3223, 3248),
    "1144": (286, 295, 313, 322),
    "1145": (355, 388, 420, 445),
    "1147": (685, 718, 751, 773),
    "1118": (2497, 2517, 2569, 2580),
    "1119": (2658, 2690, 2722, 2738),
    "1120": (2802, 2824, 2870, 2916),
    "1121": (2972, 2988, 3021, 3037),
    "1122": (88, 98, 103, 117),
    "1127": (974, 1006, 1038, 1081),
    "1130": (1650, 1696, 1742, 1788),
    "1143": (188, 212, 230, 254),
}
OUTPUT = Path("workspace/pilot/vlm")
LEDGER = Path("workspace/pilot/qsjx_e18/ledger.jsonl")
CONFIG = Path("pilot/cases/qsjx_e18.yaml")


def select_rows():
    rows = [json.loads(line) for line in LEDGER.read_text().splitlines()]
    selected = []
    for category in TYPES:
        matches = sorted((row for row in rows if row["plaintiff"]["video_type"] == category
                          and row["association"]["confidence"] == "high"),
                         key=lambda row: int(row["row_id"]))
        if len(matches) < 5:
            raise ValueError(f"only {len(matches)} high-confidence {category} rows")
        selected.extend(matches[:5])
    return selected


def load_ocr(video, candidate_totals):
    paths = list(Path("workspace/pilot/qsjx_e18/ocr_cache").glob(f"{video}-*.jsonl"))
    if len(paths) != 1:
        raise FileNotFoundError(f"expected one OCR cache for {video}: {paths}")
    result = {}
    for line in paths[0].read_text().splitlines():
        frame = json.loads(line)
        parsed = parse_items(frame["items"], candidate_totals)
        result[int(round(frame["t"]))] = parsed
    return result


def candidate_times(row, ocr):
    start, end = row["video_start"], row["video_end"]
    events = row["seeks"] + row["replays"]
    bad_times = [event["at"] for event in events]
    for pause in row["pauses"]:
        bad_times.extend(range(int(pause["start"]), int(pause["end"]) + 1))
    candidates = []
    for t in range(int(start) + 2, int(end) - 1):
        frame = ocr.get(t)
        if not frame or frame["readings"] or frame["bvs"] or frame["speed"] is not None:
            continue
        if len(frame["texts"]) > 14 or any(
                word in text for text in frame["texts"] for word in
                ("评论", "关注", "投币", "收藏", "分享", "投稿", "动态", "搜索", "正在看",
                 "取消", "WIFI", "播放量", "粉丝", "发个友善的弹幕")):
            continue
        if any(abs(t - bad) <= 4 for bad in bad_times):
            continue
        if any("推荐视频" in text or "搜索结果" in text for text in frame["texts"]):
            continue
        if any(re.search(r"\d{1,3}[:;.]\d{1,2}\s*[/7|]\s*\d{1,3}", text)
               for text in frame["texts"]):
            continue
        in_play = any(segment["event"] == "play" and segment["video_start"] <= t <= segment["video_end"]
                      for segment in row["playback_map"])
        candidates.append((t, in_play))
    if len(candidates) < 4:
        raise ValueError(f"row {row['row_id']} has only {len(candidates)} UI-hidden candidate frames")
    return candidates


def spread_four(candidates, start, end):
    """Choose four distinct times, preferring confirmed play near each quartile."""
    chosen = []
    for fraction in (.12, .38, .62, .88):
        target = start + fraction * (end - start)
        remaining = [item for item in candidates if item[0] not in {t for t, _ in chosen}]
        if not remaining:
            raise ValueError("fewer than four distinct frames")
        # Position matters most for temporal coverage; a confirmed play interval
        # breaks ties and supplies a reproducible confidence signal.
        chosen.append(min(remaining, key=lambda item: (abs(item[0] - target) +
                                                       (0 if item[1] else 2), item[0])))
    return sorted(chosen)


def select_times(row, candidates):
    if row["plaintiff"]["video_type"] != "Reaction":
        return spread_four(candidates, row["video_start"], row["video_end"])
    # The short Reaction records contain rotation and detail-page intervals.
    # Prefer one uninterrupted UI-hidden run, then spread within that run.
    runs = []
    for item in candidates:
        if not runs or item[0] - runs[-1][-1][0] > 2:
            runs.append([])
        runs[-1].append(item)
    reading_times = [reading["t"] for reading in row["readings"]]
    viable = [run for run in runs if len(run) >= 4]
    if not viable:
        return spread_four(candidates, row["video_start"], row["video_end"])
    def rank(run):
        distances = [min(abs(t - read) for read in reading_times) for t, _ in run]
        return (len(run) + min(10, min(distances)), len(run), -run[0][0])
    run = max(viable, key=rank)
    return spread_four(run, run[0][0], run[-1][0])


def extract(path, second, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(second),
                    "-i", str(path), "-frames:v", "1", "-q:v", "2", str(destination)],
                   check=True)
    with Image.open(destination) as image:
        if image.size != (1280, 720):
            raise ValueError(f"source frame is {image.size}, expected 1280x720: {destination}")


def collage(paths, destination):
    canvas = Image.new("RGB", (1280, 720), "black")
    painter = ImageDraw.Draw(canvas)
    for index, path in enumerate(paths):
        with Image.open(path) as image:
            small = image.resize((640, 360), Image.Resampling.LANCZOS)
            x, y = (index % 2) * 640, (index // 2) * 360
            canvas.paste(small, (x, y))
            painter.rectangle((x + 8, y + 8, x + 36, y + 38), fill="#0d1724")
            painter.text((x + 17, y + 14), str(index + 1), fill="white")
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "JPEG", quality=88)


def main():
    config = json.loads(CONFIG.read_text())
    video_dir = Path(config["video_dir"])
    if not Path("/Volumes/Element").is_dir() or not video_dir.is_dir():
        raise FileNotFoundError("external disk /Volumes/Element is not mounted")
    selected = select_rows()
    all_rows = [json.loads(line) for line in LEDGER.read_text().splitlines()]
    durations = {video: {row["plaintiff"]["duration"] for row in all_rows if row["video"] == video}
                 for video in {row["video"] for row in selected}}
    ocr = {video: load_ocr(video, durations[video]) for video in durations}
    manifest = []
    for row in selected:
        video = row["video"]
        path = video_dir / f"{video}.MP4"
        if not path.exists():
            raise FileNotFoundError(path)
        if row["row_id"] in FRAME_OVERRIDES:
            chosen = FRAME_OVERRIDES[row["row_id"]]
            if any(not row["video_start"] < t < row["video_end"] or
                   ocr[video][t]["readings"] or ocr[video][t]["speed"] is not None or
                   any(abs(t - event["at"]) <= 4 for event in row["seeks"] + row["replays"])
                   for t in chosen):
                raise ValueError(f"visually checked frame override is no longer valid: {row['row_id']}")
            times = [(t, any(segment["event"] == "play" and
                             segment["video_start"] <= t <= segment["video_end"]
                             for segment in row["playback_map"])) for t in chosen]
        else:
            times = select_times(row, candidate_times(row, ocr[video]))
        frames = [OUTPUT / "frames" / f"{row['row_id']}-{index}.jpg" for index in range(1, 5)]
        for (second, _), destination in zip(times, frames):
            extract(path, second, destination)
        panel = OUTPUT / "collages" / f"{row['row_id']}.jpg"
        collage(frames, panel)
        manifest.append({"row_id": row["row_id"], "video_type": row["plaintiff"]["video_type"],
                         "video": video, "collage_path": str(panel.resolve()),
                         "frame_selection": "visual_qc" if row["row_id"] in FRAME_OVERRIDES else "ocr_rule",
                         "ui_status": ("详情页UI始终可见；原片例外" if row["row_id"] == "1141"
                                       else "播放中且UI隐藏"),
                         **{f"frame_{i}_path": str(frame.resolve()) for i, frame in enumerate(frames, 1)},
                         **{f"frame_{i}_second": second for i, (second, _) in enumerate(times, 1)},
                         **{f"frame_{i}_ledger_play_interval": yes for i, (_, yes) in enumerate(times, 1)}})
        print(row["row_id"], video, [second for second, _ in times], flush=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "sample_manifest.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)


if __name__ == "__main__":
    main()
