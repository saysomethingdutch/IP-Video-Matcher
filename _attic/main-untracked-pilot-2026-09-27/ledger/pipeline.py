"""Frame OCR, session building, evidence calculation, and row linkage."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time

from PIL import Image

from .parse import edit_distance, parse_items, title_score, uploader_score
from .workbook import read_qsjx, read_zhuyu

REPO = Path(__file__).resolve().parents[2]
OCR = REPO / "pilot/bin/ocr_vision"


def command(args):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True).stdout


def compile_ocr():
    source = REPO / "pilot/tools/ocr_vision.swift"
    OCR.parent.mkdir(parents=True, exist_ok=True)
    if not OCR.exists() or OCR.stat().st_mtime < source.stat().st_mtime:
        command(["swiftc", "-O", "-module-cache-path", "/private/tmp/wo001-swift-cache",
                 "-Xcc", "-fmodules-cache-path=/private/tmp/wo001-clang-cache",
                 source, "-o", OCR])


def video_duration(path):
    return float(command(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path]).strip())


def _ocr_chunk(paths, crop, scale):
    args = [OCR]
    if crop:
        args += ["--crop", ",".join(map(str, crop))]
    if scale != 1:
        args += ["--scale", str(scale)]
    args += paths
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[:1000])
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    if len(records) != len(paths):
        raise RuntimeError(f"Vision returned {len(records)}/{len(paths)} frames: {result.stderr[:1000]}")
    return records


def parallel_ocr(paths, crop=None, scale=1):
    groups = [paths[i::4] for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_ocr_chunk, group, crop, scale) for group in groups if group]
        return [record for future in futures for record in future.result()]


def _signature(path, fps, crop, scale):
    raw = json.dumps([path.name, path.stat().st_size, fps, crop, scale], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _crop_for(t, video_spec, force_full=False):
    if force_full:
        return None, 1
    for segment in video_spec.get("segments", []):
        if segment.get("start", 0) <= t < segment.get("end", float("inf")):
            return segment.get("crop"), segment.get("scale", 1)
    return video_spec.get("crop"), video_spec.get("scale", 1)


def frame_ocr(path, video_spec, fps, cache_dir, temp_dir, *, force_full=False):
    cache_dir.mkdir(parents=True, exist_ok=True)
    crop_marker = None if force_full else [video_spec.get("crop"), video_spec.get("scale", 1), video_spec.get("segments")]
    signature = _signature(path, fps, crop_marker, 1)
    cache_path = cache_dir / f"{path.stem}-{signature}.jsonl"
    if cache_path.exists():
        return [json.loads(line) for line in cache_path.read_text().splitlines()]
    pattern = temp_dir / "frame_%06d.jpg"
    command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
             "-vf", f"fps={fps}", "-q:v", "4", pattern])
    frames = sorted(temp_dir.glob("frame_*.jpg"))
    grouped = defaultdict(list)
    for frame in frames:
        index = int(frame.stem.split("_")[-1])
        t = (index - 1) / fps
        crop, scale = _crop_for(t, video_spec, force_full)
        grouped[(tuple(crop) if crop else None, scale)].append(frame)
    records = []
    for (crop, scale), frame_group in grouped.items():
        records.extend(parallel_ocr(frame_group, crop, scale))
    by_name = {record["frame"]: record for record in records}
    if len(by_name) != len(frames):
        raise RuntimeError(f"OCR cache incomplete: {len(by_name)}/{len(frames)}")
    records = []
    for frame in frames:
        record = by_name[frame.name]
        record["t"] = round((int(frame.stem.split("_")[-1]) - 1) / fps, 3)
        record["video"] = path.name
        record["video_size"] = path.stat().st_size
        record["ocr_crop"], record["ocr_scale"] = _crop_for(record["t"], video_spec, force_full)
        records.append(record)
    cache_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    return records


def screenshot_time(path, case):
    name = path.name
    if case == "qsjx":
        match = re.search(r"Screenshot_\d{4}-\d\d-\d\d-(\d\d)-(\d\d)-(\d\d)-", name)
    else:
        match = re.search(r"S\d{5,6}-(\d\d)(\d\d)(\d\d)\d*", name)
    if not match:
        return None
    h, m, s = map(int, match.groups())
    return h * 3600 + m * 60 + s


def screenshot_ocr(paths, case, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    result = []
    missing = []
    for path in paths:
        if screenshot_time(path, case) is None:
            continue
        signature = _signature(path, 0, None, 1)
        cache = cache_dir / f"{path.stem}-{signature}.json"
        if cache.exists():
            record = json.loads(cache.read_text())
            result.append(record)
        else:
            missing.append((path, cache))
    if missing:
        for offset in range(0, len(missing), 200):
            batch = missing[offset:offset + 200]
            records = parallel_ocr([p for p, _ in batch])
            by_name = {r["frame"]: r for r in records}
            for path, cache in batch:
                record = by_name[path.name]
                record["path"] = str(path)
                record["wall_time"] = screenshot_time(path, case)
                cache.write_text(json.dumps(record, ensure_ascii=False))
                result.append(record)
    return sorted(result, key=lambda r: r["wall_time"])


def observations(records):
    result = []
    for record in records:
        item = {**record, **parse_items(record["items"])}
        # Long strings near the page heading are plausible titles, not subtitles.
        item["titles"] = [part["text"] for part in record["items"]
                          if 0.10 < part.get("y", 1) < 0.50 and len(part.get("text", "")) >= 8
                          and not re.search(r"\d\d[:;]\d\d\s*[/7]", part["text"])]
        result.append(item)
    return result


def clock_offset(obs):
    values = [item["clock"] * 60 - item["t"] for item in obs if item["clock"] is not None]
    if len(values) < 3:
        return None
    # OCR clocks are minute-precision; median is stable against minute transitions.
    center = statistics.median(values)
    values = [v for v in values if abs(v - center) < 180]
    return statistics.median(values) if values else None


def assign_screenshots(screens, video_obs):
    offsets = {video: clock_offset(obs) for video, obs in video_obs.items()}
    mapped = defaultdict(list)
    for shot in screens:
        options = []
        for video, obs in video_obs.items():
            offset = offsets[video]
            if offset is None:
                continue
            t = shot["wall_time"] - offset
            if -60 <= t <= obs[-1]["t"] + 60:
                options.append((abs(t - max(0, min(t, obs[-1]["t"]))), video, t))
        if len(options) == 1:
            _, video, t = options[0]
            mapped[video].append({**shot, **parse_items(shot["items"]), "t": t})
    return mapped, offsets


def build_sessions(obs, fps, video):
    readings = []
    for frame in obs:
        valid = [r for r in frame["readings"] if 5 <= r["total"] <= 4 * 3600
                 and 0 <= r["position"] <= r["total"] + 5]
        if not valid:
            continue
        reading = max(valid, key=lambda r: r["total"])
        readings.append({"t": frame["t"], "position": reading["position"],
                         "total": reading["total"], "raw": reading["raw"],
                         "speed": frame["speed"], "frame": frame["frame"]})
    sessions = []
    group = []
    for reading in readings:
        if group:
            last = group[-1]
            gap = reading["t"] - last["t"]
            changed = abs(reading["total"] - last["total"]) > 2
            restarted = reading["position"] <= 3 and last["position"] > 12 and gap >= 3
            delta = reading["position"] - last["position"]
            continuous_hidden = (gap <= 180 and delta > 0 and
                                 any(abs(delta - rate * gap) <= max(5, gap * .07)
                                     for rate in (.5, .75, 1, 1.25, 1.5, 2)))
            if (gap > 45 and not continuous_hidden) or changed or restarted:
                sessions.append(group)
                group = []
        group.append(reading)
    if group:
        sessions.append(group)
    result = []
    for n, group in enumerate(sessions, 1):
        if len(group) < 2:
            continue
        start = max(0, group[0]["t"] - 15)
        end = min(obs[-1]["t"], group[-1]["t"] + 15)
        nearby = [frame for frame in obs if start <= frame["t"] <= end]
        bvs = Counter(bv for frame in nearby for bv in frame["bvs"])
        titles = Counter(title for frame in nearby for title in frame["titles"])
        pages = Counter(frame["page"] for frame in nearby)
        title_sources = {text: {"t": frame["t"], "frame": frame["frame"]}
                         for frame in nearby for text in frame["titles"]}
        bv_sources = {bv: {"t": frame["t"], "frame": frame["frame"]}
                      for frame in nearby for bv in frame["bvs"]}
        metadata = [{"t": frame["t"], "frame": frame["frame"],
                     "views": frame["views"], "parts": frame["parts"],
                     "dates": frame["dates"], "bvs": frame["bvs"]} for frame in nearby
                    if frame["views"] or frame["parts"] or frame["dates"]]
        result.append({"id": f"{video}-{n:03d}", "video": video, "start": start,
                       "end": end, "readings": group, "total": Counter(r["total"] for r in group).most_common(1)[0][0],
                       "bvs": bvs, "titles": titles, "pages": pages,
                       "title_sources": title_sources, "bv_sources": bv_sources,
                       "metadata": metadata, "screenshots": []})
    return result


def add_screenshots(sessions, screenshots):
    for shot in screenshots:
        candidates = [s for s in sessions if s["start"] - 30 <= shot["t"] <= s["end"] + 30]
        if candidates:
            session = min(candidates, key=lambda s: abs(shot["t"] - (s["start"] + s["end"]) / 2))
            if shot["bvs"]:
                session["screenshots"].append(shot)
                session["bvs"].update(shot["bvs"])
                session["titles"].update(t for t in shot["texts"] if len(t) >= 8)


def _candidate_score(session, row, all_rows):
    distinct_bvs = {r["bv"] for r in all_rows if r["bv"].startswith("BV1")}
    bv_hits = []
    for value, count in session["bvs"].items():
        distances = [(edit_distance(value, bv), bv) for bv in distinct_bvs]
        if distances:
            nearest = min(distance for distance, _ in distances)
            winners = [bv for distance, bv in distances if distance == nearest]
            if nearest <= 2 and len(winners) == 1 and winners[0] == row["bv"]:
                bv_hits.append((nearest, count))
    duration_gap = abs((row["duration"] or -10000) - session["total"])
    title = max((title_score(t, row["title"]) for t in session["titles"]), default=0)
    uploader = max((uploader_score(t, row["uploader"]) for t in session["titles"]), default=0)
    if bv_hits:
        if duration_gap > 2:
            return -100, "", "none"
        distance, count = min(bv_hits)
        return 100 - 10 * distance + min(count, 5) + (10 if duration_gap <= 2 else 0), "BV闭集唯一匹配", "high"
    if title >= 0.68 and uploader >= 0.45 and duration_gap <= 2:
        return 75 + 10 * title + 5 * uploader, "标题前缀+UP主", "medium"
    if duration_gap <= 2:
        return 45 - duration_gap + 5 * title, "播放器总长±2秒", "medium" if title >= 0.5 else "low"
    return -100, "", "none"


def match_sessions(sessions, rows):
    rows_by_video = defaultdict(list)
    for row in rows:
        rows_by_video[row["video"]].append(row)
    linked = {}
    unmatched = []
    for video, group in rows_by_video.items():
        video_sessions = [s for s in sessions if s["video"] == video]
        cursor = 0
        used = set()
        for session in video_sessions:
            options = []
            for index, row in enumerate(group):
                if row["row_id"] in used:
                    continue
                score, basis, confidence = _candidate_score(session, row, group)
                if score < 0:
                    continue
                if basis == "播放器总长±2秒" and abs(index - cursor) > 2:
                    continue
                # Table order only breaks close ties; direct BV/title evidence dominates.
                order_penalty = 0.1 * abs(index - cursor)
                if index < cursor:
                    order_penalty += 2
                options.append((score - order_penalty, index, basis, confidence))
            if not options:
                unmatched.append(session)
                continue
            score, index, basis, confidence = max(options)
            if score < 40:
                unmatched.append(session)
                continue
            row = group[index]
            used.add(row["row_id"])
            cursor = max(cursor, index + 1)
            linked[row["row_id"]] = (session, basis, confidence)
            session["row_ids"] = [row["row_id"]]
        # A repeated table row may document the same evidence session.
        for row in group:
            if row["row_id"] in linked:
                continue
            row_part = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", row["title"])
            candidates = [(s, original) for original, (s, _, _) in linked.items()
                          if s["video"] == video and row["bv"] and
                          any(r["row_id"] == original and r["bv"] == row["bv"] and
                              abs((r["duration"] or 0) - (row["duration"] or 0)) <= 2
                              and not (row_part and (other_part := re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", r["title"]))
                                       and row_part.groups() != other_part.groups())
                              for r in group)]
            if len(candidates) == 1:
                session, _ = candidates[0]
                linked[row["row_id"]] = (session, "同BV且同总长；复用同一会话", "low")
                session["row_ids"].append(row["row_id"])
    return linked, unmatched


def playback(readings, total):
    if len(readings) < 2:
        return {"capture_seconds": None, "complete": None, "seeks": [], "pauses": [],
                "replays": [], "max_position": max((r["position"] for r in readings), default=None),
                "speed": None, "played_intervals": [], "inferred_segments": [],
                "playback_map": []}
    speeds = [r["speed"] for r in readings if r["speed"]]
    slopes = []
    for a, b in zip(readings, readings[1:]):
        dt = b["t"] - a["t"]
        slope = (b["position"] - a["position"]) / dt if dt else 0
        nearest = min((.5, .75, 1, 1.25, 1.5, 2), key=lambda rate: abs(rate - slope))
        if dt >= 2 and abs(nearest - slope) <= .15:
            slopes.append(nearest)
    speed = Counter(speeds or slopes).most_common(1)[0][0] if speeds or slopes else 1.0
    intervals, seeks, pauses, replays, inferred, mapping = [], [], [], [], [], []
    for left, right in zip(readings, readings[1:]):
        dt = right["t"] - left["t"]
        if dt <= 0:
            continue
        rate = left["speed"] or right["speed"] or speed
        delta = right["position"] - left["position"]
        expected = rate * dt
        if delta < -4:
            event = "replay"
            replays.append({"at": right["t"], "from": left["position"], "to": right["position"]})
            intervals.append((left["position"], min(total, left["position"] + min(dt, 1) * rate)))
        elif delta - expected > 4:
            event = "seek"
            before = min(total, left["position"] + min(dt, 1) * rate)
            seeks.append({"at": right["t"], "from": round(before, 1), "to": right["position"],
                          "frame_before": left["frame"], "frame_after": right["frame"]})
            intervals.append((left["position"], before))
        elif delta < max(1, dt * 0.25) and dt >= 2:
            event = "pause"
            pauses.append({"start": left["t"], "end": right["t"]})
        else:
            event = "play"
            intervals.append((max(0, left["position"]), min(total, right["position"])))
            if dt > 1.5:
                inferred.append({"start": left["t"], "end": right["t"], "speed": rate})
        mapping.append({"video_start": left["t"], "video_end": right["t"],
                        "content_start": left["position"], "content_end": right["position"],
                        "slope": round(delta / dt, 3) if event == "play" else None,
                        "speed": rate, "event": event, "inferred": dt > 1.5,
                        "frame_start": left["frame"], "frame_end": right["frame"]})
    first, last = readings[0], readings[-1]
    if 0 < first["position"] <= 10:
        intervals.append((0, first["position"]))
        inferred.append({"start": round(first["t"] - first["position"] / speed, 1),
                         "end": first["t"], "speed": speed, "edge": "start"})
    if 0 < total - last["position"] <= 10:
        intervals.append((last["position"], total))
        inferred.append({"start": last["t"],
                         "end": round(last["t"] + (total - last["position"]) / speed, 1),
                         "speed": speed, "edge": "end"})
    intervals = sorted((a, b) for a, b in intervals if b > a)
    merged = []
    for a, b in intervals:
        if merged and a <= merged[-1][1] + 0.5:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    played = round(sum(b - a for a, b in merged), 1)
    complete = bool(merged and merged[0][0] <= 3 and merged[-1][1] >= total - 3
                    and all(b[0] - a[1] <= 3 for a, b in zip(merged, merged[1:])))
    return {"capture_seconds": played, "complete": complete, "seeks": seeks,
            "pauses": pauses, "replays": replays,
            "max_position": max(r["position"] for r in readings), "speed": speed,
            "played_intervals": merged, "inferred_segments": inferred,
            "playback_map": mapping}


def evidence_image(video_path, t, destination, temp_frames=None, fps=1):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    frame = None
    if temp_frames:
        index = round(t * fps) + 1
        possible = temp_frames / f"frame_{index:06d}.jpg"
        if possible.exists():
            frame = possible
    if frame is None:
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "frame.jpg"
            command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", max(0, t),
                     "-i", video_path, "-frames:v", "1", "-q:v", "3", frame])
            with Image.open(frame) as image:
                image.thumbnail((640, 640))
                image.convert("RGB").save(destination, "JPEG", quality=80)
            return
    with Image.open(frame) as image:
        image.thumbnail((640, 640))
        image.convert("RGB").save(destination, "JPEG", quality=80)


def build_rows(rows, linked, output_dir, video_paths, temp_frames=None, fps=1):
    ledger = []
    for row in rows:
        session, basis, confidence = linked.get(row["row_id"], (None, "", "none"))
        record = {"row_id": row["row_id"], "video": row["video"], "plaintiff": row,
                  "session_id": session["id"] if session else None,
                  "same_session_rows": session.get("row_ids", []) if session else [],
                  "association": {"basis": basis, "confidence": confidence,
                                  "source": "OCR读数/截图" if session else None},
                  "flags": [], "evidence": []}
        if session:
            result = playback(session["readings"], session["total"])
            record.update({"video_start": session["start"], "video_end": session["end"],
                           "video_duration": session["total"], **result})
            record["observed_bv"] = min(session["bvs"],
                                        key=lambda bv: edit_distance(bv, row["bv"])) if session["bvs"] else None
            observed_title = max(session["titles"],
                                 key=lambda title: title_score(title, row["title"])) if session["titles"] else None
            observed_uploader = max(session["titles"],
                                    key=lambda text: uploader_score(text, row["uploader"])) if session["titles"] else None
            record["observed_title"] = observed_title if observed_title and title_score(observed_title, row["title"]) >= .5 else None
            record["observed_uploader"] = observed_uploader if observed_uploader and uploader_score(observed_uploader, row["uploader"]) >= .5 else None
            record["page_types"] = dict(session["pages"])
            record["readings"] = session["readings"]
            if len(session["readings"]) < 3:
                record["flags"].append("读数稀疏")
            shots = sorted(session["screenshots"],
                           key=lambda shot: min((edit_distance(bv, row["bv"]) for bv in shot["bvs"]),
                                                default=100))
            shot = shots[0] if shots and any(edit_distance(bv, row["bv"]) <= 2
                                               for bv in shots[0]["bvs"]) else None
            if shot:
                shot_image = output_dir / "evidence" / f"screenshot-{Path(shot['path']).stem}.jpg"
                shot_image.parent.mkdir(parents=True, exist_ok=True)
                if not shot_image.exists():
                    with Image.open(shot["path"]) as image:
                        image.thumbnail((640, 640))
                        image.convert("RGB").save(shot_image, "JPEG", quality=80)
                record["screenshot"] = {"path": shot["path"], "wall_time": shot["wall_time"],
                                        "bvs": shot["bvs"], "parts": shot["parts"],
                                        "views": shot["views"], "titles": shot["texts"][:5],
                                        "source": "截图", "evidence_frame": str(shot_image.relative_to(output_dir))}
            else:
                record["screenshot"] = None
            metadata = ([shot] if shot else []) + [frame for frame in session["metadata"]
                                                    if any(edit_distance(bv, row["bv"]) <= 2
                                                           for bv in frame["bvs"])]
            view_origin = next(((frame, v) for frame in metadata for v in frame["views"]), None)
            part_origins = [(frame, p) for frame in metadata for p in frame["parts"]]
            date_origin = next(((frame, d) for frame in metadata for d in frame["dates"]), None)
            record["views"] = view_origin[1] if view_origin else None
            record["parts"] = list(dict.fromkeys(p for _, p in part_origins))
            record["published"] = date_origin[1] if date_origin else None
            if result["seeks"]:
                record["flags"].append("跳播")
            if result["inferred_segments"]:
                record["flags"].append("UI隐藏区间推定")
            if not any(r["speed"] for r in session["readings"]):
                record["flags"].append("倍速未直接读出")
            points = [("开始", session["start"]),
                      ("首个读数", session["readings"][0]["t"])]
            for seek in result["seeks"]:
                before_t = next(r["t"] for r in session["readings"]
                                if r["frame"] == seek["frame_before"])
                points.extend([("跳转前", before_t), ("跳转后", seek["at"])])
            points.append(("结束", session["end"]))
            for label, t in points:
                image = output_dir / "evidence" / f"{row['video']}-{int(round(t*fps)):06d}.jpg"
                evidence_image(video_paths[row["video"]], t, image,
                               temp_frames.get(row["video"]) if temp_frames else None, fps)
                record["evidence"].append({"label": label, "path": str(image.relative_to(output_dir)),
                                            "video": row["video"], "t": round(t, 2),
                                            "source": "录像帧"})
            def frame_source(reading, source_type="OCR读数"):
                image = output_dir / "evidence" / f"{row['video']}-{int(round(reading['t']*fps)):06d}.jpg"
                evidence_image(video_paths[row["video"]], reading["t"], image,
                               temp_frames.get(row["video"]) if temp_frames else None, fps)
                return {"type": source_type, "video": row["video"], "t": reading["t"],
                        "frame": reading["frame"], "evidence_frame": str(image.relative_to(output_dir))}

            first = frame_source(session["readings"][0])
            last = frame_source(session["readings"][-1])
            start_source = frame_source({"t": session["start"],
                                         "frame": f"frame_{int(round(session['start']*fps))+1:06d}.jpg"}, "推定边界")
            end_source = frame_source({"t": session["end"],
                                       "frame": f"frame_{int(round(session['end']*fps))+1:06d}.jpg"}, "推定边界")
            anchors = [first, last]
            seek_sources = []
            for seek in result["seeks"]:
                before = next(r for r in session["readings"]
                              if r["frame"] == seek["frame_before"])
                before_source = frame_source(before)
                after_source = frame_source({"t": seek["at"], "frame": seek["frame_after"]})
                seek_sources.extend([before_source, after_source])
                anchors.extend([before_source, after_source])

            def metadata_source(origin):
                if not origin:
                    return []
                item = origin[0]
                if "path" in item:
                    return [{"type": "截图", "video": row["video"], "t": item["t"],
                             "screenshot": item["path"],
                             "evidence_frame": record["screenshot"]["evidence_frame"]}]
                return [frame_source(item)]

            record["sources"] = {
                "plaintiff": [{"type": "原告表格", "workbook": row["source_workbook"],
                               "sheet": row["source_sheet"], "row": row["source_row"]}],
                "video_start": [start_source], "video_end": [end_source], "video_duration": [first],
                "capture_seconds": [{**a, "type": "OCR读数+推定" if result["inferred_segments"] else "OCR读数"}
                                    for a in anchors],
                "playback_map": anchors,
                "complete": anchors, "seeks": seek_sources,
                "max_position": [last],
                "speed": [frame_source(next(r for r in session["readings"] if r["speed"]))]
                         if any(r["speed"] for r in session["readings"]) else
                         [{**first, "type": "推定"}],
                "views": metadata_source(view_origin),
                "parts": [src for origin in part_origins for src in metadata_source(origin)],
                "published": metadata_source(date_origin),
                "screenshot": metadata_source((shot, None)) if shot else [],
                "association": metadata_source((shot, None)) if shot else [first],
                "observed_bv": [frame_source(session["bv_sources"][record["observed_bv"]])]
                               if record["observed_bv"] in session["bv_sources"] else
                               metadata_source((shot, None)) if shot and record["observed_bv"] in shot["bvs"] else [],
                "observed_title": [frame_source(session["title_sources"][record["observed_title"]])]
                                  if record["observed_title"] in session["title_sources"] else
                                  metadata_source((shot, None)) if shot and record["observed_title"] in shot["texts"] else [],
                "observed_uploader": [frame_source(session["title_sources"][record["observed_uploader"]])]
                                     if record["observed_uploader"] in session["title_sources"] else
                                     metadata_source((shot, None)) if shot and record["observed_uploader"] in shot["texts"] else [],
                "page_types": [first],
            }
        else:
            record.update({"video_start": None, "video_end": None, "video_duration": None,
                           "capture_seconds": None, "complete": None, "seeks": [],
                           "pauses": [], "replays": [], "max_position": None, "speed": None,
                           "played_intervals": [], "inferred_segments": [], "screenshot": None,
                           "playback_map": [],
                           "views": None, "parts": [], "published": None, "sources": {},
                           "readings": [], "observed_bv": None, "observed_title": None,
                           "observed_uploader": None, "page_types": {}})
            record["sources"] = {"plaintiff": [{"type": "原告表格",
                                                 "workbook": row["source_workbook"],
                                                 "sheet": row["source_sheet"],
                                                 "row": row["source_row"]}]}
            record["flags"].append("未关联会话")
        ledger.append(record)
    by_bv = defaultdict(list)
    for record in ledger:
        if record["plaintiff"]["bv"]:
            by_bv[(record["video"], record["plaintiff"]["bv"])].append(record)
    for record in ledger:
        peers = by_bv.get((record["video"], record["plaintiff"]["bv"]), [])
        record["duplicate_bv_rows"] = [peer["row_id"] for peer in peers if peer is not record]
        if len(peers) == 1:
            record["same_session_status"] = "无重复BV"
        elif any(not peer["session_id"] for peer in peers):
            record["same_session_status"] = "未确认"
        elif len({peer["session_id"] for peer in peers}) == 1:
            record["same_session_status"] = "同一会话"
        else:
            record["same_session_status"] = "不同会话"
    return ledger
