"""Resumable, local 169-row visual fact run using the R1 two-stage protocol."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from .infer import ENUMS, FIELDS, resident
from .r1 import COLUMNS_R1, MAPPER, ask_visual, detect_description_conflicts, geometry, map_description

MODEL = "qwen3-vl:8b-instruct-q4_K_M"
OUTPUT = Path("workspace/pilot/vlm")
LANDSCAPE = {
    "MVI_0001": (168, 92, 1014, 573),
    "MVI_0002": (140, 70, 1115, 605),
    "MVI_0003": (188, 78, 1045, 590),
    "MVI_0004": (202, 92, 1052, 567),
    "MVI_0005": (160, 84, 1072, 601),
    "MVI_0006": (132, 103, 1066, 575),
    "MVI_0007": (140, 66, 1090, 608),
    "MVI_0008": (145, 96, 1000, 585),
}
PORTRAIT_PLAYER = {
    "MVI_0001": (130, 70, 410, 600),
    "MVI_0002": (130, 68, 410, 590),
    "MVI_0003": (125, 80, 410, 590),
    "MVI_0004": (135, 78, 420, 590),
    "MVI_0005": (430, 72, 775, 238),
    "MVI_0006": (420, 70, 780, 238),
    "MVI_0007": (438, 72, 760, 225),
    "MVI_0008": (395, 66, 735, 220),
}
UI_WORDS = ("关注", "评论", "点赞", "投币", "收藏", "分享", "播放量", "发个友善的弹幕", "相关推荐")
SPEED = re.compile(r"(?<!\d)(0\.5|0\.75|1\.0|1\.25|1\.5|2\.0)\s*[xX×]")


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_ocr_flags(case: Path, video: str) -> dict[int, dict]:
    files = list((case / "ocr_cache").glob(f"{video}-*.jsonl"))
    if len(files) != 1:
        raise FileNotFoundError(f"expected one OCR cache for {video}; found {len(files)}")
    flags = {}
    with files[0].open(encoding="utf-8") as handle:
        for line in handle:
            frame = json.loads(line)
            texts = [str(item.get("text") or "") for item in frame.get("items", [])]
            speeds = {match.group(1) for text in texts for match in SPEED.finditer(text)}
            ui_count = sum(any(word in text for word in UI_WORDS) for text in texts)
            flags[int(round(frame["t"]))] = {"menu": len(speeds) >= 3, "detail_ui": ui_count >= 3}
    return flags


def candidates(row: dict, flags: dict[int, dict]) -> list[tuple[int, bool]]:
    seeks = [float(event["at"]) for event in row.get("seeks", []) + row.get("replays", [])]
    pauses = [(float(p["start"]), float(p["end"])) for p in row.get("pauses", [])]
    menus = {t for t, flag in flags.items() if flag["menu"]}
    plays = [seg for seg in row.get("playback_map", []) if seg.get("event") == "play"]
    if not plays:
        raise ValueError("台账无已播放区间")
    for margin in (1.5, 0.2):
        found = {}
        for seg in plays:
            start, end = float(seg["video_start"]), float(seg["video_end"])
            for t in range(math.ceil(start + margin), math.floor(end - margin) + 1):
                if any(abs(t - seek) <= 2 for seek in seeks):
                    continue
                if any(a - 1 <= t <= b + 1 for a, b in pauses):
                    continue
                if any(abs(t - menu) <= 2 for menu in menus):
                    continue
                found[t] = flags.get(t, {}).get("detail_ui", False)
        clean = [(t, False) for t, ui in sorted(found.items()) if not ui]
        needed = 8 if isinstance(row.get("capture_seconds"), (int, float)) and row["capture_seconds"] > 300 else 4
        if len(clean) >= needed:
            return clean
        all_playable = [(t, ui) for t, ui in sorted(found.items())]
        if len(all_playable) >= needed:
            return all_playable
    raise ValueError(f"播放区间中少于 {needed} 个避开跳转和倍速菜单的关键帧")


def spread(items: list[tuple[int, bool]], count: int) -> list[tuple[int, bool]]:
    if len(items) < count:
        raise ValueError(f"need {count} distinct frames; found {len(items)}")
    # Choose from cumulative playable seconds so separated playback intervals
    # each contribute evidence instead of treating a seek as played time.
    return [items[round((i + .5) * len(items) / count - .5)] for i in range(count)]


def frame_times(row: dict, flags: dict[int, dict]) -> list[tuple[int, bool]]:
    items = candidates(row, flags)
    long_video = isinstance(row.get("capture_seconds"), (int, float)) and row["capture_seconds"] > 300
    if not long_video:
        return spread(items, 4)
    midpoint = len(items) // 2
    return spread(items[:midpoint], 4) + spread(items[midpoint:], 4)


def video_frame(video_path: Path, second: int) -> Image.Image:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
        temp = Path(handle.name)
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(second),
                        "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(temp)],
                       check=True, timeout=90)
        with Image.open(temp) as frame:
            if frame.size != (1280, 720):
                raise ValueError(f"unexpected video frame size: {frame.size}")
            return frame.convert("RGB")
    finally:
        temp.unlink(missing_ok=True)


def crop_player(frame: Image.Image, video: str, detail_ui: bool) -> Image.Image:
    box = PORTRAIT_PLAYER[video] if detail_ui else LANDSCAPE[video]
    crop = frame.crop(box)
    if detail_ui and video in ("MVI_0001", "MVI_0002", "MVI_0003", "MVI_0004"):
        crop = crop.transpose(Image.Transpose.ROTATE_270)
    return crop.resize((1280, 720), Image.Resampling.LANCZOS)


def collage(tiles: list[Image.Image], path: Path):
    canvas = Image.new("RGB", (2560, 1440), "#101010")
    painter = ImageDraw.Draw(canvas)
    for index, tile in enumerate(tiles, 1):
        x, y = ((index - 1) % 2) * 1280, ((index - 1) // 2) * 720
        canvas.paste(tile, (x, y))
        painter.rectangle((x + 14, y + 14, x + 65, y + 70), fill="#101010")
        painter.text((x + 32, y + 32), str(index), fill="white")
    canvas.save(path, "JPEG", quality=85)


def choose_ratio(prediction: dict, geo: dict) -> tuple[dict, list[str]]:
    visual_ratio = prediction["rights_content_ratio"]
    geometry_ratio = geo["ratio"]
    flags = []
    prediction["rights_content_ratio"] = geometry_ratio
    if visual_ratio != geometry_ratio:
        flags.append(f"画面比例待核：模型 {visual_ratio}，几何 {geometry_ratio}")
    return prediction, flags


def process(row: dict, case: Path, video_dir: Path, flags: dict[int, dict]) -> dict:
    rid = row["row_id"]
    target = OUTPUT / "full" / rid
    target.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = {"row_id": rid, "video_type": row["plaintiff"]["video_type"], "model": MODEL,
              "collage_path": "", "ui_status": "", **{field: "" for field in FIELDS},
              "elapsed_seconds": 0, "peak_resident_mb": 0, "peak_vram_mb": 0,
              "status": "error", "error": "", "description_raw": "", "geometry_ratio": "不确定",
              "flags": [], "collages": [], "frame_times": [], "visual_descriptions": [],
              "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        video_path = video_dir / f"{row['video']}.MP4"
        if not video_path.is_file():
            raise FileNotFoundError(video_path)
        selected = frame_times(row, flags)
        result["frame_times"] = [{"recording_second": t, "detail_ui": ui} for t, ui in selected]
        tiles = []
        for index, (second, detail_ui) in enumerate(selected, 1):
            tile = crop_player(video_frame(video_path, second), row["video"], detail_ui)
            tile.save(target / f"frame_{index}.jpg", "JPEG", quality=88)
            tiles.append(tile)
        paths = []
        for offset in range(0, len(tiles), 4):
            path = target / f"collage_{offset // 4 + 1}.jpg"
            collage(tiles[offset:offset + 4], path)
            paths.append(path)
        result["collage_path"] = str(paths[0].resolve())
        result["collages"] = [str(path.resolve()) for path in paths]
        result["ui_status"] = "含详情页小窗帧" if any(ui for _, ui in selected) else "已播放且避开菜单"
        geo = geometry([(tile, False) for tile in tiles])
        result["geometry"] = geo
        result["geometry_ratio"] = geo["ratio"]
        descriptions = []
        for path in paths:
            response = ask_visual(MODEL, path)
            description = response["message"]["content"].strip()
            if not description:
                raise ValueError("visual model returned an empty description")
            descriptions.append(description)
        result["visual_descriptions"] = descriptions
        merged = "；".join(descriptions)
        result["description_raw"] = merged
        mapped, mapper_conflicts, mapper_response = map_description(merged)
        result["mapper_raw"] = mapper_response["message"]["content"]
        contradictions = list(dict.fromkeys(mapper_conflicts + detect_description_conflicts(merged)))
        for field in contradictions:
            if field in ("layout", "rights_content_ratio", "uploader_on_camera", "other_works", "form_guess"):
                mapped[field] = "不确定"
            elif field == "overlays":
                mapped[field] = []
        mapped, ratio_flags = choose_ratio(mapped, geo)
        warnings = ratio_flags + (["描述自相矛盾：" + ",".join(contradictions)] if contradictions else [])
        if any(ui for _, ui in selected):
            warnings.append("部分关键帧取自详情页播放器小窗")
        result["flags"] = warnings
        for field in FIELDS:
            result[field] = json.dumps(mapped[field], ensure_ascii=False) if isinstance(mapped[field], list) else mapped[field]
        result["status"] = "ok"
        result["error"] = "; ".join(warnings)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        try:
            peak, vram = resident(MODEL)
            result["peak_resident_mb"] = round(peak / 1e6, 1)
            result["peak_vram_mb"] = round(vram / 1e6, 1)
        except Exception:
            pass
        atomic_json(target / "result.json", result)
    return result


def read_results() -> list[dict]:
    records = []
    for path in sorted((OUTPUT / "full").glob("*/result.json"), key=lambda p: int(p.parent.name)):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def write_outputs(records: list[dict]):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT / "predictions_full.csv.tmp"
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS_R1, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    os.replace(tmp, OUTPUT / "predictions_full.csv")
    timings = [float(row["elapsed_seconds"]) for row in records]
    summary = {"rows": len(records), "ok": sum(row["status"] == "ok" for row in records),
               "failed": [row["row_id"] for row in records if row["status"] != "ok"],
               "mean_seconds_per_row": round(statistics.mean(timings), 2) if timings else None,
               "total_seconds": round(sum(timings), 2),
               "flags": dict(Counter(flag for row in records for flag in row.get("flags", [])))}
    baseline_path = OUTPUT / "predictions_r1.csv"
    if baseline_path.exists():
        with baseline_path.open(encoding="utf-8-sig", newline="") as handle:
            baseline = {row["row_id"]: row for row in csv.DictReader(handle) if row["model"] == MODEL}
        current = {row["row_id"]: row for row in records if row["status"] == "ok"}
        fields = ("uploader_on_camera", "other_works", "rights_content_ratio")
        compared = sorted(set(baseline) & set(current), key=int)
        mismatches = [{"row_id": rid, "fields": {field: [baseline[rid][field], current[rid][field]]
                    for field in fields if baseline[rid][field] != current[rid][field]}}
                    for rid in compared]
        summary["r1_stability"] = {"compared": len(compared), "all_three_match": sum(not x["fields"] for x in mismatches),
                                   "mismatches": [x for x in mismatches if x["fields"]]}
    atomic_json(OUTPUT / "summary_full.json", summary)


def run_all(case: Path, selected: set[str] | None = None):
    if not Path("/Volumes/Element").is_dir():
        raise FileNotFoundError("/Volumes/Element is not mounted")
    config = json.loads(Path("pilot/cases/qsjx_e18.yaml").read_text(encoding="utf-8"))
    video_dir = Path(config["video_dir"])
    if not video_dir.is_dir():
        raise FileNotFoundError(video_dir)
    rows = [json.loads(line) for line in (case / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(rows) != 169 or {row["row_id"] for row in rows} != {str(n) for n in range(1051, 1220)}:
        raise ValueError("expected exactly ledger rows 1051–1219")
    flags_by_video = {}
    for row in rows:
        rid = row["row_id"]
        if selected is not None and rid not in selected:
            continue
        existing = OUTPUT / "full" / rid / "result.json"
        if existing.exists():
            print(f"skip {rid}: existing result", flush=True)
            continue
        try:
            if row["video"] not in flags_by_video:
                flags_by_video[row["video"]] = read_ocr_flags(case, row["video"])
            result = process(row, case, video_dir, flags_by_video[row["video"]])
        except Exception as exc:
            target = OUTPUT / "full" / rid
            result = {"row_id": rid, "video_type": row["plaintiff"]["video_type"], "model": MODEL,
                      "status": "error", "error": f"{type(exc).__name__}: {exc}",
                      "elapsed_seconds": 0, "description_raw": "", "geometry_ratio": "不确定", "flags": []}
            atomic_json(target / "result.json", result)
        write_outputs(read_results())
        print(f"{rid} {result['status']} {result['elapsed_seconds']}s {result['error']}", flush=True)
    write_outputs(read_results())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run-all", "summarize"])
    parser.add_argument("case", type=Path)
    parser.add_argument("--rows", nargs="*", type=str)
    args = parser.parse_args()
    if args.command == "run-all":
        run_all(args.case.resolve(), set(args.rows) if args.rows else None)
    else:
        write_outputs(read_results())
        print((OUTPUT / "summary_full.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
