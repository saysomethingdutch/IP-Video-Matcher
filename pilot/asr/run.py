"""WO-003b: local whisper.cpp transcription of actual recorded playback."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import unicodedata

from pilot.textindex.search import Index
from pilot.textindex.text import normalize, similarity

from .mapping import audio_chunks, content_at, playback_segments, recording_spans


CASE = Path("workspace/pilot/qsjx_e18")
OUTPUT = CASE / "asr"
MODEL = Path("workspace/pilot").resolve().parents[1] / "models/whisper/ggml-large-v3.bin"
INDEX = Path("workspace/pilot/textindex/qsjx/index.sqlite")
CONFIG = Path("pilot/cases/qsjx_e18.yaml")
CASE_CONFIG = None


def configure(config):
    """Select paths for a case without changing transcription behavior."""
    global CASE, OUTPUT, MODEL, INDEX, CASE_CONFIG
    CASE_CONFIG = config
    CASE = config.case_dir
    OUTPUT = CASE / "asr"
    MODEL = config.path_of(config.raw["models"]["speech"]["model"])
    INDEX = config.index_dir / "index.sqlite"
HALLUCINATION_RESIDUE = re.compile(r"请不吝.{0,8}点赞.{0,8}订阅.{0,8}转发.{0,8}打赏|点赞订阅转发打赏")
LANGUAGE_LINE = re.compile(r"auto-detected language:\s*([a-z-]+)\s*\(p\s*=\s*([0-9.]+)\)")
SUBTITLE_EXCLUDE = ("发个友善", "弹幕", "关注", "选集", "点赞", "投币", "收藏", "分享", "UP主")


def command(args, timeout=900):
    result = subprocess.run([str(item) for item in args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} exited {result.returncode}: " +
                           (result.stderr or result.stdout)[-800:])
    return result


def extract_audio(video_path, chunk, destination):
    command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-ss", chunk["start"], "-t", round(chunk["end"] - chunk["start"], 3),
             "-i", video_path, "-vn", "-ac", 1, "-ar", 16000,
             "-c:a", "pcm_s16le", destination], timeout=120)
    if not destination.exists() or destination.stat().st_size < 128:
        raise RuntimeError(f"empty extracted audio at {chunk['start']}")


def detect_language(wav):
    result = command(["whisper-cli", "-m", MODEL, "-f", wav, "-l", "auto", "-dl"], timeout=300)
    match = LANGUAGE_LINE.search(result.stdout + result.stderr)
    if not match:
        return {"language": "und", "confidence": 0.0}
    return {"language": match.group(1), "confidence": round(float(match.group(2)), 3)}


def select_language(row, acoustic):
    detected = dict(acoustic)
    thai_title = "泰配" in row["plaintiff"].get("title", "")
    if thai_title and not (detected["language"] == "zh" and detected["confidence"] >= .6):
        detected["acoustic_language"] = detected["language"]
        detected["language"] = "th"
        detected["selection"] = "title_hint"
        return detected, "th"
    if detected["confidence"] >= .6:
        language = detected["language"]
        detected["selection"] = "acoustic"
    else:
        detected["acoustic_language"] = detected["language"]
        detected["language"] = language = "zh"
        detected["selection"] = "zh_default"
    return detected, language


def transcribe(wav, language, prefix):
    command(["whisper-cli", "-m", MODEL, "-f", wav, "-l", language,
             "-oj", "-of", prefix, "-np"], timeout=900)
    path = Path(str(prefix) + ".json")
    if not path.exists():
        raise RuntimeError(f"whisper JSON missing: {path}")
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def cache_chunk(row_id, chunk, video_path, language, tempdir):
    cache = OUTPUT / "chunks" / f"{row_id}-{chunk['number']:04d}.json"
    previous = None
    if cache.exists():
        data = json.loads(cache.read_text())
        if (data.get("version") == 1 and data.get("start") == chunk["start"]
                and data.get("end") == chunk["end"]):
            if data.get("language") == language:
                return data
            previous = data
    wav = tempdir / f"chunk-{chunk['number']:04d}.wav"
    extract_audio(video_path, chunk, wav)
    zh_baseline = (previous["zh_baseline"] if previous else
                   transcribe(wav, "zh", tempdir / f"chunk-{chunk['number']:04d}-zh")
                   .get("transcription", []))
    native = None
    if language not in ("zh", "und"):
        native = transcribe(wav, language, tempdir / f"chunk-{chunk['number']:04d}-{language}")
    data = {"version": 1, "start": chunk["start"], "end": chunk["end"],
            "span": chunk["span"], "language": language,
            "zh_baseline": zh_baseline,
            "native": native.get("transcription", []) if native else None}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _repeat_key(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


def repeated_within_segment(text):
    words = [_repeat_key(word) for word in text.split()]
    if any(len(words[i]) >= 2 and words[i] == words[i + 1] == words[i + 2]
           for i in range(len(words) - 2)):
        return True
    normalized = _repeat_key(text)
    for width in range(4, min(13, len(normalized) // 3 + 1)):
        if any(normalized[i:i + width] == normalized[i + width:i + 2 * width] ==
               normalized[i + 2 * width:i + 3 * width]
               for i in range(len(normalized) - 3 * width + 1)):
            return True
    return False


def assemble_segments(chunks, cached, mapping, detected):
    segments = []
    for chunk, data in zip(chunks, cached):
        transcript = data["native"] if data["native"] is not None else data["zh_baseline"]
        for item in transcript:
            text = item.get("text", "").strip()
            if not text:
                continue
            start = max(chunk["start"], chunk["start"] + item["offsets"]["from"] / 1000)
            end = min(chunk["end"], chunk["start"] + item["offsets"]["to"] / 1000)
            if end <= start:
                continue
            midpoint = (start + end) / 2
            content_mid = content_at(mapping, midpoint)
            if content_mid is None:
                continue
            language = detected["language"] if detected["confidence"] >= .6 else "und"
            if re.search(r"[\u0E00-\u0E7F]", text):
                language = "th"
            elif language == "und" and re.search(r"[\u3400-\u9fff]", text):
                language = "zh"
            segments.append({"recording_start_seconds": round(start, 3),
                             "recording_end_seconds": round(end, 3),
                             "content_start_seconds": content_at(mapping, start),
                             "content_end_seconds": content_at(mapping, end),
                             "content_seconds": content_mid, "text": text,
                             "language": language, "language_detection_confidence": detected["confidence"],
                             "chunk_number": chunk["number"], "flags": []})
    segments.sort(key=lambda item: (item["recording_start_seconds"], item["recording_end_seconds"]))
    deduped = []
    for item in segments:
        duplicate = next((i for i in range(max(0, len(deduped) - 3), len(deduped))
                          if deduped[i]["chunk_number"] != item["chunk_number"]
                          and item["recording_start_seconds"] < deduped[i]["recording_end_seconds"]
                          and deduped[i]["recording_start_seconds"] < item["recording_end_seconds"]
                          and similarity(_repeat_key(deduped[i]["text"]),
                                         _repeat_key(item["text"])) >= .8), None)
        if duplicate is None:
            deduped.append(item)
        elif (item["recording_end_seconds"] - item["recording_start_seconds"] >
              deduped[duplicate]["recording_end_seconds"] -
              deduped[duplicate]["recording_start_seconds"]):
            deduped[duplicate] = item
    deduped.sort(key=lambda item: (item["recording_start_seconds"], item["recording_end_seconds"]))
    return deduped


def classify_and_flag(segments, index):
    for item in segments:
        candidates = index.search(item["text"], min_sim=.6, top=1)
        if candidates:
            best = candidates[0]
            item.update(classification="ORIGINAL", match_similarity=round(best.sim, 3),
                        original_episode=best.entry.episode,
                        original_seconds=best.entry.t_start,
                        original_text=best.entry.text)
        else:
            item.update(classification="NON_ORIGINAL", match_similarity=None,
                        original_episode=None, original_seconds=None, original_text=None)
        if HALLUCINATION_RESIDUE.search(item["text"]) or repeated_within_segment(item["text"]):
            item["flags"].append("SUSPECT_HALLUCINATION")
    keys = [_repeat_key(item["text"]) for item in segments]
    start = 0
    while start < len(keys):
        end = start + 1
        while end < len(keys) and keys[end] and keys[end] == keys[start]:
            end += 1
        if keys[start] and end - start >= 3:
            for item in segments[start:end]:
                if "SUSPECT_HALLUCINATION" not in item["flags"]:
                    item["flags"].append("SUSPECT_HALLUCINATION")
        start = end


def subtitle_frames(video, cache):
    if video in cache:
        return cache[video]
    paths = list((CASE / "ocr_cache").glob(f"{video}-*.jsonl"))
    if len(paths) != 1:
        raise FileNotFoundError(f"expected one OCR cache for {video}, found {len(paths)}")
    frames = []
    for line in paths[0].read_text().splitlines():
        frame = json.loads(line)
        texts = [item["text"].strip() for item in frame["items"]
                 if .12 <= item.get("x", -1) <= .88 and .62 <= item.get("y", -1) <= .88
                 and len(item.get("text", "").strip()) >= 2
                 and re.search(r"[\u3400-\u9fff]", item.get("text", ""))
                 and not any(word in item.get("text", "") for word in SUBTITLE_EXCLUDE)]
        if texts:
            frames.append((frame["t"], texts))
    cache[video] = frames
    return frames


def subtitle_alignment(row, segments, cache):
    if row["plaintiff"]["video_type"] != "解说":
        return None
    frames = subtitle_frames(row["video"], cache)
    values = []
    for item in segments:
        if item["language"] != "zh":
            continue
        nearby = [(t, text) for t, texts in frames if item["recording_start_seconds"] - 1 <= t <=
                  item["recording_end_seconds"] + 1 for text in texts]
        if not nearby:
            continue
        ordered = []
        for _, text in sorted(nearby):
            if not ordered or normalize(ordered[-1]) != normalize(text):
                ordered.append(text)
        asr = normalize(item["text"])
        options = [normalize(text) for text in ordered]
        if len(ordered) > 1:
            options.append(normalize("".join(ordered)))
        score = max((similarity(asr, option) for option in options if option), default=None)
        if score is not None:
            item["subtitle_char_similarity"] = round(score, 3)
            values.append(score)
    return round(statistics.median(values), 3) if values else None


def process_row(row, config, index, ocr_cache):
    start_time = time.monotonic()
    row_id = str(row["row_id"])
    mapping = playback_segments(row)
    spans = recording_spans(mapping)
    chunks = audio_chunks(spans)
    if not chunks:
        return {"row_id": row_id, "video": row["video"], "status": "NO_PLAYBACK",
                "reason": "台账没有可转写的播放区间", "language_detection": None,
                "recording_audio_seconds": 0, "chunk_count": 0, "segments": [],
                "processing_seconds": round(time.monotonic() - start_time, 2)}
    video_path = Path(config["video_dir"]) / config["videos"][row["video"]].get(
        "file", row["video"] + ".MP4")
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    with tempfile.TemporaryDirectory(prefix=f"wo003b-{row_id}-") as directory:
        tempdir = Path(directory)
        longest = max(chunks, key=lambda item: item["end"] - item["start"])
        sample = tempdir / "language-sample.wav"
        extract_audio(video_path, longest, sample)
        detected, language = select_language(row, detect_language(sample))
        cached = [cache_chunk(row_id, chunk, video_path, language, tempdir) for chunk in chunks]
    segments = assemble_segments(chunks, cached, mapping, detected)
    classify_and_flag(segments, index)
    subtitle_median = subtitle_alignment(row, segments, ocr_cache)
    return {"row_id": row_id, "video": row["video"], "video_type": row["plaintiff"]["video_type"],
            "status": "OK", "reason": "", "language_detection": detected,
            "recording_audio_seconds": round(sum(span["end"] - span["start"] for span in spans), 2),
            "chunk_audio_seconds_including_overlap": round(sum(chunk["end"] - chunk["start"] for chunk in chunks), 2),
            "chunk_count": len(chunks), "playback_segments": mapping, "recording_spans": spans,
            "subtitle_char_similarity_median": subtitle_median,
            "segments": segments, "processing_seconds": round(time.monotonic() - start_time, 2)}


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    location = p * (len(ordered) - 1)
    lo, hi = int(location), min(len(ordered) - 1, int(location) + 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (location - lo), 3)


def write_summaries(rows):
    results = []
    for row in rows:
        path = OUTPUT / f"{row['row_id']}.json"
        if path.exists():
            results.append(json.loads(path.read_text()))
    flat = []
    scores = []
    for item in results:
        segments = item["segments"]
        scores.extend(s["subtitle_char_similarity"] for s in segments
                      if s.get("subtitle_char_similarity") is not None)
        flat.append({"row_id": item["row_id"], "video": item["video"],
                     "transcribed_seconds": item["recording_audio_seconds"],
                     "NON_ORIGINAL_sentences": sum(s["classification"] == "NON_ORIGINAL" for s in segments),
                     "ORIGINAL_sentences": sum(s["classification"] == "ORIGINAL" for s in segments),
                     "language": item["language_detection"]["language"] if item["language_detection"] else "未知",
                     "suspect_count": sum("SUSPECT_HALLUCINATION" in s["flags"] for s in segments),
                     "subtitle_similarity_median": item.get("subtitle_char_similarity_median"),
                     "status": item["status"], "failure_reason": item["reason"]})
    columns = ["row_id", "video", "transcribed_seconds", "NON_ORIGINAL_sentences",
               "ORIGINAL_sentences", "language", "suspect_count", "subtitle_similarity_median",
               "status", "failure_reason"]
    with (OUTPUT / "asr_summary.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(flat)
    audio = sum(item["recording_audio_seconds"] for item in results if item["status"] == "OK")
    elapsed = sum(item["processing_seconds"] for item in results)
    summary = {"rows_total": len(rows), "rows_written": len(results),
               "rows_with_playback": sum(bool(row.get("played_intervals")) for row in rows),
               "rows_transcribed": sum(item["status"] == "OK" for item in results),
               "rows_no_playback": sum(item["status"] == "NO_PLAYBACK" for item in results),
               "rows_failed": [item["row_id"] for item in results if item["status"] == "ERROR"],
               "audio_seconds": round(audio, 2), "processing_seconds": round(elapsed, 2),
               "seconds_per_audio_hour": round(elapsed / (audio / 3600), 2) if audio else None,
               "language_counts": dict(Counter(item["language_detection"]["language"]
                                               for item in results if item.get("language_detection"))),
               "subtitle_char_similarity": {
                   "segments": len(scores), "median": _percentile(scores, .5),
                   "p10": _percentile(scores, .1), "p25": _percentile(scores, .25),
                   "p75": _percentile(scores, .75), "p90": _percentile(scores, .9),
                   "histogram": {f"{i/5:.1f}-{(i+1)/5:.1f}": sum(i/5 <= x < (i+1)/5 if i < 4 else i/5 <= x <= 1
                                                                   for x in scores) for i in range(5)}}}
    (OUTPUT / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def run(only_rows=None, force=False):
    config = ({"video_dir": str(CASE_CONFIG.path_of(CASE_CONFIG.raw["evidence"]["video_dir"])),
               "videos": CASE_CONFIG.raw["evidence"]["videos"]} if CASE_CONFIG else json.loads(CONFIG.read_text()))
    rows = [json.loads(line) for line in (CASE / "ledger.jsonl").read_text().splitlines()]
    if CASE_CONFIG and {r["row_id"] for r in rows} != set(CASE_CONFIG.row_ids):
        raise ValueError("ledger rows differ from case config")
    if not CASE_CONFIG and len(rows) != 169:
        raise ValueError(f"expected 169 ledger rows, got {len(rows)}")
    if not Path(config["video_dir"]).is_dir():
        raise FileNotFoundError(config["video_dir"])
    if not MODEL.exists() or (not INDEX.exists() and not CASE_CONFIG) or not shutil.which("whisper-cli"):
        raise FileNotFoundError("local whisper.cpp model, index, or executable missing")
    index = Index.load(INDEX) if INDEX.exists() else Index([])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    ocr_cache = {}
    for row in rows:
        row_id = str(row["row_id"])
        if only_rows and row_id not in only_rows:
            continue
        destination = OUTPUT / f"{row_id}.json"
        previous = json.loads(destination.read_text()) if destination.exists() else None
        if destination.exists() and not force:
            if previous.get("status") in ("OK", "NO_PLAYBACK"):
                continue
        attempt_start = time.monotonic()
        try:
            item = process_row(row, config, index, ocr_cache)
        except Exception as exc:
            item = {"row_id": row_id, "video": row["video"], "status": "ERROR",
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
                    "recording_audio_seconds": 0, "language_detection": None,
                    "chunk_count": 0, "segments": [],
                    "processing_seconds": round(time.monotonic() - attempt_start, 2)}
        if previous and previous.get("status") == "OK" and item["status"] == "OK":
            item["processing_seconds"] = max(item["processing_seconds"],
                                             previous.get("processing_seconds", 0))
        destination.write_text(json.dumps(item, ensure_ascii=False, indent=2))
        print(row_id, item["status"], item.get("language_detection"),
              item["chunk_count"], len(item["segments"]), item["reason"], flush=True)
        write_summaries(rows)
    return write_summaries(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--rows", nargs="*")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(set(args.rows) if args.rows else None, args.force), ensure_ascii=False))


if __name__ == "__main__":
    main()
