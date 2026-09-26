"""Frame OCR, session building, evidence calculation, and row linkage."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time

from PIL import Image, ImageChops, ImageOps, ImageStat

from .parse import edit_distance, parse_items, parse_reading, title_score, uploader_score
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


def observations(records, candidate_totals=None):
    result = []
    selected_speed = None
    speed_source = None
    speed_at = float("-inf")
    last_bvs = set()
    for record in records:
        item = {**record, **parse_items(record["items"], candidate_totals)}
        observed_bvs = set(item["bvs"])
        if observed_bvs and last_bvs and not observed_bvs & last_bvs:
            selected_speed = speed_source = None
        if observed_bvs:
            last_bvs = observed_bvs
        if item["speed_menu"] or item["page"] == "搜索结果页":
            selected_speed = speed_source = None
        elif item["speed_cue"] in ("central_confirmation", "player_button"):
            selected_speed = item["speed"]
            speed_source = record["frame"]
            speed_at = record["t"]
        if selected_speed is not None and record["t"] - speed_at <= 120:
            item["speed"] = selected_speed
            item["speed_source_frame"] = speed_source
        else:
            item["speed"] = None
            item["speed_source_frame"] = None
        if any("用" in text and "M" in text and "G" in text for text in item["texts"]):
            item["readings"] = []  # File manager playback previews are not the accused video.
        # Long strings near the page heading are plausible titles, not subtitles.
        item["titles"] = [part["text"] for part in record["items"]
                          if 0.10 < part.get("y", 1) < 0.50 and len(part.get("text", "")) >= 8
                          and not re.search(r"\d\d[:;]\d\d\s*[/7]", part["text"])]
        result.append(item)
    return result


def resolve_nearby_identity_readings(obs, rows):
    """Resolve /MM beside a full readout when local BV/title confirms its row."""
    def same_title_identity(observed, expected):
        if title_score(observed, expected) < .82:
            return False
        observed_ep = re.search(r"EP[.．:]?\s*(\d+)", observed, re.I)
        expected_ep = re.search(r"EP[.．:]?\s*(\d+)", expected, re.I)
        if observed_ep and expected_ep and observed_ep.group(1) != expected_ep.group(1):
            return False
        observed_part = re.search(r"(?<!\d)[12]\s*/\s*2(?!\d)", observed)
        expected_part = re.search(r"(?<!\d)[12]\s*/\s*2(?!\d)", expected)
        return not (observed_part and expected_part and observed_part.group() != expected_part.group())

    by_second = defaultdict(list)
    for frame in obs:
        by_second[round(frame["t"])].append(frame)
    adopted = 0
    for frame in obs:
        if frame["readings"] or not frame["reading_raw_candidates"]:
            continue
        t = round(frame["t"])
        near = [other for second in range(t - 15, t + 16)
                for other in by_second.get(second, [])]
        full = {reading["total"] for other in near if abs(other["t"] - frame["t"]) <= 5
                for reading in other["readings"] if not reading.get("truncated_total")}
        choices = []
        for total in full:
            matching = [row for row in rows if row["duration"] is not None
                        and abs(row["duration"] - total) <= 1]
            for row in matching:
                identity = any((row["bv"] and row["bv"] in other["bvs"]) or
                               any(same_title_identity(title, row["title"]) for title in other["titles"])
                               for other in near)
                if not identity:
                    continue
                for raw in frame["reading_raw_candidates"]:
                    reading = parse_reading(raw, {r["duration"] for r in rows}, total)
                    if reading and reading.get("truncated_total"):
                        choices.append(reading)
        unique = {(reading["position"], reading["total"]): reading for reading in choices}
        if len(unique) == 1:
            frame["readings"].append(next(iter(unique.values())))
            adopted += 1
    return adopted


def resolve_truncated_readings(obs, rows, linked):
    """Use a BV/title-confirmed preliminary session to disambiguate /MM OCR."""
    by_id = {row["row_id"]: row for row in rows}
    confirmed = []
    for row_id, (session, basis, _) in linked.items():
        if row_id not in by_id or ("BV" not in basis and "标题" not in basis):
            continue
        row = by_id[row_id]
        if row["video"] != session["video"] or row["duration"] is None:
            continue
        confirmed.append((session, row))
    if not confirmed:
        return 0
    totals = {row["duration"] for row in rows if row["duration"] is not None}
    by_second = defaultdict(list)
    for item in obs:
        by_second[round(item["t"])].append(item)
    adopted = 0
    for frame in obs:
        if frame["readings"] or not frame["reading_raw_candidates"]:
            continue
        frame_part = next((match.group().replace(" ", "") for text in frame["texts"]
                           if (match := re.search(r"(?<!\d)[12]\s*/\s*2(?!\d)", text))), None)
        choices = []
        for session, row in confirmed:
            row_part_match = re.search(r"(?<!\d)[12]\s*/\s*2(?!\d)", row["title"])
            if frame_part and row_part_match and frame_part != row_part_match.group().replace(" ", ""):
                continue
            start, end, t = session["start"], session["end"], frame["t"]
            distance = max(start - t, t - end, 0)
            if distance > 120:
                continue
            local_full = {reading["total"] for second in range(round(t) - 5, round(t) + 6)
                          for other in by_second.get(second, [])
                          for reading in other["readings"] if not reading.get("truncated_total")}
            if local_full and row["duration"] not in local_full:
                continue
            if distance:
                edge = start if t < start else end
                between = [other for other in obs if min(t, edge) < other["t"] < max(t, edge)]
                if any((other["bvs"] and row["bv"] and
                         all(edit_distance(bv, row["bv"]) > 1 for bv in other["bvs"]))
                       or other["page"] == "搜索结果页"
                       or any("推荐视频" in text for text in other["texts"]) for other in between):
                    continue
            for raw in frame["reading_raw_candidates"]:
                reading = parse_reading(raw, totals, row["duration"])
                if reading and reading.get("truncated_total"):
                    anchor = min(abs(t - item["t"]) for item in session["readings"])
                    choices.append((distance, anchor, session, row, reading))
        choices.sort(key=lambda item: (item[0], item[1]))
        if not choices:
            continue
        if (len(choices) > 1 and choices[1][3]["row_id"] != choices[0][3]["row_id"]
                and choices[1][0] - choices[0][0] <= 5
                and choices[1][1] - choices[0][1] <= 5):
            continue
        frame["readings"].append(choices[0][4])
        adopted += 1
    return adopted


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
        part = next((match.group() for text in frame["texts"]
                     if (match := re.search(r"(?<!\d)[12]\s*/\s*2(?!\d)", text))), None)
        readings.append({"t": frame["t"], "position": reading["position"],
                         "total": reading["total"], "raw": reading["raw"],
                         "part_marker": part,
                         "truncated_total": reading.get("truncated_total", False),
                         "speed": frame["speed"], "speed_source_frame": frame.get("speed_source_frame"),
                         "frame": frame["frame"]})
    # A one or two frame duration switch surrounded by the same duration is
    # normally OCR noise, not a second video page.
    runs = []
    for reading in readings:
        if not runs or abs(reading["total"] - runs[-1][-1]["total"]) > 2:
            runs.append([])
        runs[-1].append(reading)
    readings = [reading for index, run in enumerate(runs)
                if not (len(run) <= 2 and 0 < index < len(runs) - 1
                        and abs(runs[index - 1][-1]["total"] - runs[index + 1][0]["total"]) <= 2
                        and runs[index + 1][0]["t"] - runs[index - 1][-1]["t"] <= 120)
                for reading in run]
    sessions = []
    group = []
    for reading in readings:
        if group:
            last = group[-1]
            gap = reading["t"] - last["t"]
            changed = (abs(reading["total"] - last["total"]) > 2 or
                       bool(reading.get("part_marker") and last.get("part_marker") and
                            reading["part_marker"] != last["part_marker"] and
                            reading["position"] <= 60 and last["position"] > 60))
            intervening = (frame for frame in obs if last["t"] < frame["t"] < reading["t"])
            between = list(intervening)
            search_streak = 0
            boundary = False
            for frame in between:
                if any("推荐视频" in text for text in frame["texts"]):
                    boundary = True
                    break
                search_streak = search_streak + 1 if (frame["page"] == "搜索结果页"
                                                       and len(frame["titles"]) >= 2
                                                       and not frame["bvs"]) else 0
                if search_streak >= 3:
                    boundary = True
                    break
            same_identity = False
            if gap > 120 and not changed and not boundary:
                earlier = [frame for frame in obs if group[0]["t"] - 15 <= frame["t"] <= last["t"]]
                previous_bvs = {bv for frame in earlier for bv in frame["bvs"]}
                previous_titles = {title for frame in earlier for title in frame["titles"]}
                later = [frame for frame in obs if last["t"] < frame["t"] <= reading["t"]]
                later_bvs = {bv for frame in later for bv in frame["bvs"]}
                later_titles = {title for frame in later for title in frame["titles"]}
                other_bv = bool(later_bvs - previous_bvs)
                same_identity = not other_bv and (bool(previous_bvs & later_bvs) or
                    any(title_score(a, b) >= .82 for a in previous_titles for b in later_titles))
            if (gap > 120 and not same_identity) or changed or boundary:
                sessions.append(group)
                group = []
        group.append(reading)
    if group:
        sessions.append(group)
    sessions = [group for group in sessions if len(group) >= 2]
    result = []
    for n, group in enumerate(sessions, 1):
        filtered = []
        for i, reading in enumerate(group):
            if 0 < i < len(group) - 1:
                before, after = group[i - 1], group[i + 1]
                dt = after["t"] - before["t"]
                if (0 < dt <= 15 and .35 * dt <= after["position"] - before["position"] <= 2.3 * dt):
                    expected = before["position"] + (after["position"] - before["position"]) * (
                        reading["t"] - before["t"]) / dt
                    if abs(reading["position"] - expected) > max(5, dt * .6):
                        continue
            filtered.append(reading)
        group = filtered
        start = max(0, group[0]["t"] - 15)
        next_start = sessions[n][0]["t"] if n < len(sessions) else float("inf")
        recommendations = [frame for frame in obs if group[-1]["t"] < frame["t"] < next_start
                           and frame["t"] - group[-1]["t"] <= 120
                           and any("推荐视频" in text for text in frame["texts"])]
        if recommendations and group[-1]["position"] < group[-1]["total"]:
            finish = recommendations[0]
            group = group + [{"t": finish["t"], "position": group[-1]["total"],
                              "total": group[-1]["total"], "raw": "推荐视频结束页（推定）",
                              "speed": None, "frame": finish["frame"]}]
        end = min(obs[-1]["t"], group[-1]["t"] + (0 if recommendations else 15), next_start - 1)
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
    for left, right in zip(result, result[1:]):
        if left["end"] > right["start"]:
            boundary = (left["readings"][-1]["t"] + right["readings"][0]["t"]) / 2
            left["end"] = min(left["end"], boundary)
            right["start"] = max(right["start"], boundary)
    return result


def add_screenshots(sessions, screenshots):
    for shot in screenshots:
        candidates = [s for s in sessions if s["start"] - 30 <= shot["t"] <= s["end"] + 30]
        if candidates:
            session = min(candidates, key=lambda s: abs(shot["t"] - (s["start"] + s["end"]) / 2))
            if shot["bvs"] and not any(old.get("path") == shot.get("path") for old in session["screenshots"]):
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
        ambiguous = any(other["row_id"] != row["row_id"] and other["duration"] is not None
                        and abs(other["duration"] - row["duration"]) <= 2 for other in all_rows)
        if ambiguous:
            if title >= .68:
                return 60 + 10 * title, "标题+表格行序", "medium"
            return -100, "", "none"
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


@lru_cache(maxsize=512)
def _video_patch(path, second):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(second),
                             "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                            check=True, capture_output=True)
    with Image.open(io.BytesIO(result.stdout)) as image:
        width, height = image.size
        box = (round(width * .24), round(height * .27), round(width * .72), round(height * .68))
        return ImageOps.autocontrast(image.convert("L").crop(box).resize((150, 75)))


def has_playback_evidence(obs, video_path, left, right):
    """Require changing subtitles or changing video pixels before a forward seek."""
    frames = [frame for frame in obs if left["t"] <= frame["t"] < right["t"]]
    if any(frame["speed_menu"] or frame["page"] == "搜索结果页" or
           any("推荐视频" in text for text in frame["texts"]) for frame in frames):
        return False
    captions = []
    for frame in frames:
        lines = tuple(item["text"].strip() for item in frame["items"]
                      if .20 <= item.get("x", -1) <= .75 and .55 <= item.get("y", -1) <= .85
                      and len(item.get("text", "").strip()) >= 5
                      and not any(word in item["text"] for word in
                                  ("发个友善", "弹幕", "关注", "选集", "自动", "播放")))
        if lines:
            captions.append(lines)
    if len(set(captions)) >= 2:
        return True
    end = right["t"] - 1
    if end <= left["t"]:
        return False
    times = [left["t"], end] if end - left["t"] <= 4 else [left["t"], (left["t"] + end) / 2, end]
    patches = [_video_patch(str(video_path), round(t, 2)) for t in times]
    return all(ImageStat.Stat(ImageChops.difference(a, b)).mean[0] >= 9
               for a, b in zip(patches, patches[1:]))


def playback(readings, total, seek_playback_evidence=None):
    if len(readings) < 2:
        return {"capture_seconds": None, "complete": None, "seeks": [], "pauses": [],
                "replays": [], "max_position": max((r["position"] for r in readings), default=None),
                "speed": None, "played_intervals": [], "inferred_segments": [],
                "playback_map": [], "speed_conflict": False,
                "unsupported_gaps": [], "slope_evidence": None}
    speeds = [r["speed"] for r in readings if r["speed"]]
    slope_pairs = []
    for i, a in enumerate(readings):
        for b in readings[i + 1:i + 7]:
            dt = b["t"] - a["t"]
            if not 2 <= dt <= 60:
                continue
            between = readings[i + 1:readings.index(b)]
            points = [a, *between, b]
            if any((right["position"] - left["position"]) < -2 or
                   (right["position"] - left["position"]) > 2.5 * (right["t"] - left["t"]) + 2
                   for left, right in zip(points, points[1:])):
                continue
            slope = (b["position"] - a["position"]) / dt
            if .35 <= slope <= 2.2:
                # Prefer longer uninterrupted spans: one-second OCR readings
                # quantize 1.5x playback to alternating one/two-second steps.
                slope_pairs.append((slope, min(dt, 20), a, b))
    if slope_pairs:
        ordered = sorted(slope_pairs, key=lambda item: item[0])
        half = sum(item[1] for item in ordered) / 2
        running = 0
        median_slope = ordered[-1][0]
        for value, weight, _, _ in ordered:
            running += weight
            if running >= half:
                median_slope = value
                break
    else:
        median_slope = None
    inferred_speed = (min((.5, .75, 1, 1.25, 1.5, 2),
                          key=lambda rate: abs(rate - median_slope)) if slope_pairs else None)
    count = sum(abs(slope - inferred_speed) <= .25 for slope, _, _, _ in slope_pairs) if inferred_speed else 0
    label = Counter(speeds).most_common(1)[0][0] if speeds else None
    speed = (label if label is not None and median_slope is not None
             and abs(median_slope - label) <= .2 else
             inferred_speed if count >= 3 else label if label is not None else inferred_speed)
    if speed is None:
        positive = [(b["position"] - a["position"]) / (b["t"] - a["t"])
                    for a, b in zip(readings, readings[1:]) if b["t"] > a["t"]
                    and b["position"] > a["position"]]
        speed = min((.5, .75, 1, 1.25, 1.5, 2),
                    key=lambda rate: abs(rate - statistics.median(positive))) if positive else 1.0
    speed_conflict = bool(label is not None and count >= 3 and label != speed)
    comparable = sorted(slope_pairs, key=lambda pair: (abs(pair[0] - speed), -pair[1])) if speed else []
    long_pairs = [pair for pair in comparable if pair[3]["t"] - pair[2]["t"] >= 10]
    chosen = long_pairs[0] if long_pairs else comparable[0] if comparable else None
    slope_evidence = ({"speed": round(chosen[0], 3), "frame_start": chosen[2]["frame"],
                       "frame_end": chosen[3]["frame"],
                       "span_seconds": chosen[3]["t"] - chosen[2]["t"]} if chosen else None)
    intervals, seeks, pauses, replays, inferred, mapping, unsupported = [], [], [], [], [], [], []
    for left, right in zip(readings, readings[1:]):
        dt = right["t"] - left["t"]
        if dt <= 0:
            continue
        rate = speed
        delta = right["position"] - left["position"]
        expected = rate * dt
        if delta < -4:
            event = "replay"
            replays.append({"at": right["t"], "from": left["position"], "to": right["position"]})
            if seek_playback_evidence is None or seek_playback_evidence.get((left["frame"], right["frame"])):
                intervals.append((left["position"], min(total, left["position"] + rate * max(0, dt - 1))))
            elif dt > 1:
                unsupported.append({"start": left["t"], "end": right["t"]})
        elif delta - expected > 3:
            event = "seek"
            supported = (seek_playback_evidence is None or
                         seek_playback_evidence.get((left["frame"], right["frame"]), False))
            before = left["position"] + (min(rate * max(0, dt - 1), delta) if supported else 0)
            before = min(total, before)
            seeks.append({"at": right["t"], "from": round(before, 1), "to": right["position"],
                          "frame_before": left["frame"], "frame_after": right["frame"]})
            if before > left["position"]:
                intervals.append((left["position"], before))
            elif dt > 1 and not supported:
                unsupported.append({"start": left["t"], "end": right["t"]})
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
            "playback_map": mapping, "speed_conflict": speed_conflict,
            "unsupported_gaps": unsupported, "slope_evidence": slope_evidence}


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


def build_rows(rows, linked, output_dir, video_paths, temp_frames=None, fps=1, video_obs=None):
    ledger = []
    playback_cache = {}
    for row in rows:
        session, basis, confidence = linked.get(row["row_id"], (None, "", "none"))
        record = {"row_id": row["row_id"], "video": row["video"], "plaintiff": row,
                  "session_id": session["id"] if session else None,
                  "same_session_rows": session.get("row_ids", []) if session else [],
                  "association": {"basis": basis, "confidence": confidence,
                                  "source": "OCR读数/截图" if session else None},
                  "flags": [], "evidence": []}
        if session:
            if session["id"] not in playback_cache:
                readings = session["readings"]
                evidence = {}
                if video_obs:
                    preliminary = playback(readings, session["total"])
                    event_pairs = {(item["frame_start"], item["frame_end"])
                                   for item in preliminary["playback_map"]
                                   if item["event"] in ("seek", "replay")}
                    for left, right in zip(readings, readings[1:]):
                        if (left["frame"], right["frame"]) in event_pairs and right["t"] - left["t"] > 1:
                            evidence[(left["frame"], right["frame"])] = has_playback_evidence(
                                video_obs[row["video"]], video_paths[row["video"]], left, right)
                playback_cache[session["id"]] = playback(readings, session["total"], evidence)
            result = playback_cache[session["id"]]
            record.update({"video_start": session["start"], "video_end": session["end"],
                           "video_duration": session["total"], **result})
            nearby_obs = [frame for frame in video_obs.get(row["video"], [])
                          if session["start"] <= frame["t"] <= session["end"]] if video_obs else []
            candidates = [frame for frame in nearby_obs if frame["reading_candidate"]]
            failures = sum(not frame["readings"] for frame in candidates)
            record["reading_parse_failure_ratio"] = round(failures / len(candidates), 3) if candidates else None
            record["reading_candidate_frames"] = len(candidates)
            record["reading_failed_frames"] = failures
            if candidates and failures / len(candidates) > .3:
                record["flags"].append("读数解析失败超过30%")
            if result["unsupported_gaps"]:
                record["flags"].append("跳播前缺少播放证据；未计入时长")
            direct = next((r["speed_source_frame"] for r in session["readings"]
                           if r.get("speed") == result["speed"] and r.get("speed_source_frame")), None)
            slope = result["slope_evidence"]
            reason_parts = []
            if direct:
                reason_parts.append(f"{direct} 的播放器倍速提示或按钮")
            if slope:
                reason_parts.append(f"{slope['frame_start']} 至 {slope['frame_end']} 的 {slope['span_seconds']:g} 秒连续读数斜率 {slope['speed']:g}")
            record["speed_reason"] = (f"依据{'及'.join(reason_parts)}，判定为 {result['speed']:g} 倍。"
                                      if result["speed"] in (.5, .75) and reason_parts else
                                      f"依据 {session['readings'][0]['frame']} 至 {session['readings'][-1]['frame']} 的读数估计为 {result['speed']:g} 倍，需人工复核。"
                                      if result["speed"] in (.5, .75) else None)
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
            if result["speed_conflict"]:
                record["flags"].append("倍速标签与斜率冲突；采用斜率")
            if result["speed"] is None:
                record["flags"].append("倍速未知")
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
                           "reading_parse_failure_ratio": None,
                           "reading_candidate_frames": 0, "reading_failed_frames": 0,
                           "speed_reason": None, "unsupported_gaps": [], "slope_evidence": None,
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


def link_without_readings(ledger, mapped_screens, video_obs, output_dir, video_paths):
    """Keep visual-only associations while leaving playback facts unknown."""
    by_video = defaultdict(list)
    for record in ledger:
        by_video[record["video"]].append(record)
    for video, rows in by_video.items():
        missing = [record for record in rows if not record["session_id"]]
        if not missing:
            continue
        candidates = [("截图", shot) for shot in mapped_screens.get(video, [])]
        candidates += [("录像帧", frame) for frame in video_obs[video] if frame["bvs"] or frame["titles"]]
        for record in missing:
            row = record["plaintiff"]
            hits = []
            for kind, source in candidates:
                if row["bv"] and source["bvs"]:
                    nearest = min(edit_distance(bv, row["bv"]) for bv in source["bvs"])
                    rivals = [other for other in rows if other is not record and other["plaintiff"]["bv"]
                              and min(edit_distance(bv, other["plaintiff"]["bv"]) for bv in source["bvs"]) <= nearest]
                    if nearest <= 1 and not rivals:
                        hits.append((100 - 10 * nearest + (5 if kind == "截图" else 0), kind, source, "截图BV" if kind == "截图" else "BV"))
                        continue
                texts = source["texts"] if kind == "截图" else source["titles"]
                score = max((title_score(text, row["title"]) for text in texts), default=0)
                if score >= .82 and all(other is record or
                                          max((title_score(text, other["plaintiff"]["title"]) for text in texts), default=0) < score
                                          for other in rows):
                    hits.append((score * 80 + (5 if kind == "截图" else 0), kind, source, "标题"))
            if not hits:
                continue
            _, kind, source, basis = max(hits, key=lambda hit: hit[0])
            record["session_id"] = f"{video}-无读数-{row['row_id']}"
            record["association"] = {"basis": basis, "confidence": "medium", "source": kind}
            record["video_start"] = record["video_end"] = round(source["t"], 2)
            record["flags"] = [flag for flag in record["flags"] if flag != "未关联会话"]
            record["flags"].append("无播放器读数，需复核")
            record["flags"].append("倍速未知")
            record["observed_bv"] = source["bvs"][0] if source["bvs"] else None
            if kind == "截图":
                image = output_dir / "evidence" / f"screenshot-{Path(source['path']).stem}.jpg"
                image.parent.mkdir(parents=True, exist_ok=True)
                if not image.exists():
                    with Image.open(source["path"]) as picture:
                        picture.thumbnail((640, 640))
                        picture.convert("RGB").save(image, "JPEG", quality=80)
                record["screenshot"] = {"path": source["path"], "wall_time": source["wall_time"],
                                        "bvs": source["bvs"], "parts": source["parts"],
                                        "views": source["views"], "titles": source["texts"][:5],
                                        "source": "截图", "evidence_frame": str(image.relative_to(output_dir))}
                record["sources"]["screenshot"] = [{"type": "截图", "screenshot": source["path"],
                                                        "evidence_frame": str(image.relative_to(output_dir))}]
                record["sources"]["association"] = record["sources"]["screenshot"]
            else:
                image = output_dir / "evidence" / f"{video}-{int(round(source['t'])):06d}.jpg"
                evidence_image(video_paths[video], source["t"], image)
                proof = {"label": "BV或标题", "path": str(image.relative_to(output_dir)),
                         "video": video, "t": source["t"], "source": "录像帧"}
                record["evidence"].append(proof)
                record["sources"]["association"] = [proof]


def quality_audit(ledger, video_obs):
    unique = {}
    for row in ledger:
        if row["session_id"] and row["readings"]:
            unique[row["session_id"]] = row
    truncations = 0
    for row in unique.values():
        video = row["video"]
        total = row["video_duration"]
        if sum(other["video"] == video and other["plaintiff"]["duration"] is not None
               and abs(other["plaintiff"]["duration"] - total) <= 1 for other in ledger) != 1:
            continue
        if any((row["video_start"] - 120 <= frame["t"] < row["video_start"] or
                row["video_end"] < frame["t"] <= row["video_end"] + 120)
               and any(abs(reading["total"] - total) <= 1 for reading in frame["readings"])
               for frame in video_obs[video]):
            truncations += 1
    periods = [(row["video"], row["video_start"], row["video_end"])
               for row in unique.values() if row["video_start"] is not None]
    overlaps = sum(a[0] == b[0] and max(a[1], b[1]) < min(a[2], b[2])
                   for i, a in enumerate(periods) for b in periods[i + 1:])
    return {"truncation_audit_rows": truncations,
            "truncated_total_readings_adopted": sum(r.get("truncated_total", False)
                for row in unique.values() for r in row["readings"]),
            "speed_unknown_rows": sum(row["session_id"] is not None and row["speed"] is None
                                      for row in ledger),
            "unlinked_rows_without_speed": sum(row["session_id"] is None and row["speed"] is None
                                               for row in ledger),
            "capture_seconds_lt5_rows": sum(row["capture_seconds"] is not None
                                            and row["capture_seconds"] < 5 for row in ledger),
            "overlapping_session_pairs": overlaps}
