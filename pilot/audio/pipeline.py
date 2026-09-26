"""WO-008 local audio facts. Recording audio never leaves this process."""
from __future__ import annotations

import json
import hashlib
import csv
from pathlib import Path
import re
import subprocess
import tempfile
import time

from pilot.case_config import CaseConfig
from pilot.textindex.search import Index
from pilot.textindex.text import normalize, similarity
from pilot.content.extract import MUSIC_TAG, load_ocr

TAG = re.compile(r"<\|([^|]+)\|>")
SPLIT = re.compile(r"[。！？!?；;，,、\n]+")
CHUNK_SECONDS = 4.0  # Keep the transcript near the OCR frame used for the +/-2 s check.
MATCH_THRESHOLD = .5
OCR_WINDOW_SECONDS = 2.0
MIN_UPLOADER_CHARS = 6
MUSIC_ONLY_SECONDS = 20.0
RULE_VERSION = "D-043-audio-v4"
MIN_SPEECH_CHUNKS = 3


def played_recording_spans(row: dict) -> list[tuple[float, float]]:
    """Use recording clock spans marked play, never unproved seeks or pauses."""
    spans = []
    for event in row.get("playback_map") or []:
        if event.get("event") != "play":
            continue
        start, end = event.get("video_start"), event.get("video_end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            continue
        if spans and start <= spans[-1][1] + .25:
            spans[-1] = spans[-1][0], max(end, spans[-1][1])
        else:
            spans.append((start, end))
    chunks = []
    for start, end in spans:
        while end - start > .5:
            stop = min(end, start + CHUNK_SECONDS)
            chunks.append((round(start, 2), round(stop, 2)))
            start = stop
    return chunks


def decode_tags(raw: str) -> tuple[str, list[str]]:
    tags = TAG.findall(raw)
    text = TAG.sub("", raw).strip()
    return text, tags


def _caption_match(clause: str, events: list[dict], recording_time: float) -> tuple[float, dict | None]:
    nearby = (event for event in events
              if event.get("text") and event.get("rec_start") is not None and
              event["rec_start"] <= recording_time + OCR_WINDOW_SECONDS and
              (event.get("rec_end") or event["rec_start"]) >= recording_time - OCR_WINDOW_SECONDS)
    scored = [(similarity(normalize(clause), normalize(event["text"])), event) for event in nearby]
    return max(scored, key=lambda pair: pair[0], default=(0.0, None))


def compare_dialogue(text: str, index: Index | None, events: list[dict],
                     recording_start: float, recording_end: float,
                     *, visual_subtitles_available: bool = True) -> list[dict]:
    clauses = [piece.strip() for piece in SPLIT.split(text) if normalize(piece)]
    result = []
    total = sum(max(1, len(normalize(piece))) for piece in clauses)
    elapsed = 0
    for clause in clauses:
        weight = max(1, len(normalize(clause)))
        midpoint = recording_start + (recording_end - recording_start) * (elapsed + weight / 2) / total
        elapsed += weight
        ocr_sim, caption = _caption_match(clause, events, midpoint)
        matches = index.search(clause, min_sim=MATCH_THRESHOLD, top=1) if index and len(normalize(clause)) >= 4 else []
        best = matches[0] if matches else None
        source = ocr_sim >= MATCH_THRESHOLD or bool(best and best.sim >= MATCH_THRESHOLD)
        concurrent_caption = any(
            event.get("rec_start") is not None and event["rec_start"] <= midpoint + OCR_WINDOW_SECONDS and
            (event.get("rec_end") or event["rec_start"]) >= midpoint - OCR_WINDOW_SECONDS and
            (event.get("status") == "LINKED" or event.get("category") == "底部解说字幕")
            for event in events)
        kind = ("原剧台词" if source else "UP主口播候选"
                if visual_subtitles_available and not concurrent_caption and
                len(normalize(clause)) >= MIN_UPLOADER_CHARS else "未定声音")
        result.append({"text": clause, "kind": kind,
                       "recording_midpoint": round(midpoint, 2),
                       "source_match": caption["text"] if ocr_sim >= MATCH_THRESHOLD else best.entry.text if source and best else None,
                       "source_basis": "同期画面字幕" if ocr_sim >= MATCH_THRESHOLD else "原剧台词索引" if source else None,
                       "source_episode": best.entry.episode if best and best.sim >= MATCH_THRESHOLD else None,
                       "source_similarity": round(max(ocr_sim, best.sim if best else 0.0), 3),
                       "uncertainty": "同期画面有字幕但转写未达匹配阈值" if not source and concurrent_caption else None})
    return result


def _extract_wav(video: Path, start: float, end: float, wav: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-ss", str(start), "-i", str(video), "-t", str(round(end - start, 3)),
                    "-vn", "-ac", "1", "-ar", "16000", "-y", str(wav)], check=True)


def _model(model_dir: Path):
    from funasr import AutoModel
    if not model_dir.is_dir():
        raise FileNotFoundError(f"SenseVoice model absent: {model_dir}")
    return AutoModel(model=str(model_dir), device="cpu", disable_update=True)


def _content_time(row: dict, recording_time: float) -> float | None:
    for event in row.get("playback_map") or []:
        if event.get("event") != "play":
            continue
        start, end = event["video_start"], event["video_end"]
        if start - .01 <= recording_time <= end + .01 and end > start:
            fraction = max(0.0, min(1.0, (recording_time - start) / (end - start)))
            return round(event["content_start"] + fraction *
                         (event["content_end"] - event["content_start"]), 2)
    return None


def _sentence_segments(row: dict, chunks: list[dict]) -> list[dict]:
    result = []
    for chunk in chunks:
        clauses = chunk["clauses"]
        total = sum(max(1, len(normalize(c["text"]))) for c in clauses)
        elapsed = 0
        for clause in clauses:
            weight = max(1, len(normalize(clause["text"])))
            a = chunk["recording_start"] + (chunk["recording_end"] - chunk["recording_start"]) * elapsed / total
            elapsed += weight
            b = chunk["recording_start"] + (chunk["recording_end"] - chunk["recording_start"]) * elapsed / total
            result.append({"start": _content_time(row, a), "end": _content_time(row, b),
                           "recording_start": round(a, 2), "recording_end": round(b, 2),
                           "text": clause["text"],
                           "kind": {"原剧台词": "source", "UP主口播候选": "uploader"}.get(clause["kind"], "uncertain"),
                           "episode": clause["source_episode"],
                           "similarity": clause["source_similarity"],
                           "source_basis": clause.get("source_basis"),
                           "timestamp_basis": "按片段字数比例估计；SenseVoice 未提供逐词时间"})
    return result


def _visual_original(config: CaseConfig, rid: str) -> bool:
    """Only a completed visual consensus showing mostly source footage supports a music-only inference."""
    path = config.vlm_dir / "predictions_consensus_r3.csv"
    if not path.is_file():
        return False
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            if record.get("row_id") != rid or record.get("status") != "ok":
                continue
            ratio = record.get("rights_content_ratio") or ""
            return record.get("other_works") == "否" and ratio in ("约3/4", "约1", "几乎全部")
    return False


def _music_evidence(row: dict, chunks: list[dict], content_events: list[dict],
                    ocr_frames: dict[int, list[dict]], visual_original: bool) -> str | None:
    music_chunks = [c for c in chunks if "BGM" in c["tags"]]
    if not music_chunks:
        return None
    played = [(c["recording_start"], c["recording_end"]) for c in music_chunks]
    def during(t: float) -> bool:
        return any(a - 1 <= t <= b + 1 for a, b in played)
    if any(during(e["rec_start"]) and (e.get("lyric") or "♪" in e["text"] or "♫" in e["text"])
           for e in content_events):
        return "同期 OCR 歌词字幕"
    if any(during(t) and any(MUSIC_TAG.match(item.get("text", "").strip()) for item in items)
           for t, items in ocr_frames.items()):
        return "同期 B 站音乐标签"
    if not visual_original:
        return None
    # A sustained, contiguous music-only interval must have neither source speech nor source captions.
    continuous = 0.0
    last_end = None
    for chunk in music_chunks:
        a, b = chunk["recording_start"], chunk["recording_end"]
        source_speech = any(c["kind"] == "原剧台词" for c in chunk["clauses"])
        source_caption = any(e.get("status") == "LINKED" and e["rec_start"] <= b + 2 and
                             (e.get("rec_end") or e["rec_start"]) >= a - 2 for e in content_events)
        only_music = "Speech" not in chunk["tags"] and not chunk["text"].strip() and not source_speech and not source_caption
        continuous = (continuous if last_end is not None and a <= last_end + .25 else 0.0) + (b - a) if only_music else 0.0
        if continuous >= MUSIC_ONLY_SECONDS:
            return "原剧画面连续至少20秒仅有音乐，未见原剧台词字幕或语音"
        last_end = b if only_music else None
    return None


def reinterpret_existing(row: dict, config: CaseConfig, record: dict, index: Index | None,
                         content_events: list[dict], ocr_frames: dict[int, list[dict]],
                         player_frames: int) -> dict:
    """Re-score saved 4-second transcripts without sending audio through the model again."""
    chunks = record["audio_chunks"]
    for chunk in chunks:
        chunk["clauses"] = compare_dialogue(chunk["text"], index, content_events,
                                            chunk["recording_start"], chunk["recording_end"],
                                            visual_subtitles_available=player_frames >= 3)
    speech_chunks = sum("Speech" in chunk["tags"] and any(
        clause["kind"] == "UP主口播候选" for clause in chunk["clauses"]) for chunk in chunks)
    for chunk in chunks:
        for clause in chunk["clauses"]:
            if clause["kind"] != "UP主口播候选":
                continue
            if "Speech" not in chunk["tags"]:
                clause.update(kind="未定声音", uncertainty="BGM 片段可能是歌曲人声，不能当口播")
            elif speech_chunks < MIN_SPEECH_CHUNKS:
                clause.update(kind="未定声音", uncertainty="独立说话片段不足 3 个，无法排除转写误报")
    all_clauses = [clause for chunk in chunks for clause in chunk["clauses"]]
    total_chars = sum(len(normalize(clause["text"])) for clause in all_clauses)
    source_chars = sum(len(normalize(clause["text"])) for clause in all_clauses
                       if clause["kind"] == "原剧台词")
    played = sum(chunk["recording_end"] - chunk["recording_start"] for chunk in chunks)
    bgm = round(sum(chunk["recording_end"] - chunk["recording_start"] for chunk in chunks
                    if "BGM" in chunk["tags"]) / played, 3) if played else 0.0
    basis = (_music_evidence(row, chunks, content_events, ocr_frames,
                             _visual_original(config, row["row_id"])) if bgm >= .6 else None)
    record.update(rule_version=RULE_VERSION, segments=_sentence_segments(row, chunks),
                  uploader_speech=[clause["text"] for clause in all_clauses
                                   if clause["kind"] == "UP主口播候选"],
                  source_dialogue_ratio=round(source_chars / total_chars, 3) if total_chars else 0.0,
                  bgm_ratio=bgm, suspected_uploader_music=True if basis else None,
                  uploader_music={"suspected": True if basis else None,
                                  "basis": basis or "仅报告 BGM 占比；缺少配乐归属旁证"},
                  interpretation_limit="同期字幕或台词索引相似度≥0.5为原剧；未匹配、同期无字幕且≥6字，至少3个独立Speech片段才列口播；BGM人声未定")
    return record


def analyze_row(row: dict, config: CaseConfig, model, index: Index | None,
                content_events: list[dict] | None = None, ocr_frames: dict[int, list[dict]] | None = None,
                *, player_frames: int = 0) -> dict:
    rid = row["row_id"]
    content_events = content_events or []
    ocr_frames = ocr_frames or {}
    chunks = played_recording_spans(row)
    base = {"row_id": rid, "video": row["video"], "engine": "SenseVoice-Small",
            "audio_uploaded": False, "playback_spans": chunks, "rule_version": RULE_VERSION}
    if not chunks:
        return {**base, "status": "skipped", "reason": "台账无已播放录音区间", "segments": []}
    if "泰配" in row["plaintiff"].get("title", ""):
        prior = config.case_dir / "asr" / f"{rid}.json"
        old = json.loads(prior.read_text(encoding="utf-8")) if prior.is_file() else {}
        segments = [{"start": s.get("content_start_seconds"), "end": s.get("content_end_seconds"),
                     "text": s.get("text", ""), "kind": "uploader" if s.get("classification") == "NON_ORIGINAL" else "source",
                     "episode": s.get("original_episode"), "similarity": s.get("match_similarity")}
                    for s in old.get("segments") or []]
        return {**base, "engine": "whisper", "status": "whisper_thai", "reason": "泰语沿用本地 Whisper",
                "whisper_path": str(prior) if prior.is_file() else None,
                "played_seconds": old.get("recording_audio_seconds"), "segments": segments,
                "events": {}, "uploader_music": {"suspected": None, "basis": "泰语沿用 Whisper，未测声音事件"}}
    preset = config.raw["evidence"]["videos"].get(row["video"])
    if preset is None:
        raise ValueError(f"recording {row['video']} is not in this batch")
    video = config.path_of(config.raw["evidence"]["video_dir"]) / preset.get("file", row["video"] + ".MP4")
    recording_duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        check=True, capture_output=True, text=True).stdout.strip())
    audio_chunks = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wo008-audio-") as temp:
        for n, (start, end) in enumerate(chunks):
            wav = Path(temp) / f"{rid}-{n:04d}.wav"
            _extract_wav(video, start, end, wav)
            output = model.generate(input=str(wav), language="auto", use_itn=True, batch_size_s=30)
            raw = str(output[0].get("text") or "") if output else ""
            text, tags = decode_tags(raw)
            clauses = compare_dialogue(text, index, content_events, start, end,
                                       visual_subtitles_available=player_frames >= 3)
            audio_chunks.append({"recording_start": start, "recording_end": end,
                                 "text": text, "tags": tags, "raw_model_text": raw,
                                 "clauses": clauses})
    played = sum(end - start for start, end in chunks)
    def ratio(tag: str) -> float:
        return round(sum(s["recording_end"] - s["recording_start"] for s in audio_chunks
                         if tag in s["tags"]) / played, 3) if played else 0.0
    all_clauses = [c for segment in audio_chunks for c in segment["clauses"]]
    total_chars = sum(len(normalize(c["text"])) for c in all_clauses)
    source_chars = sum(len(normalize(c["text"])) for c in all_clauses if c["kind"] == "原剧台词")
    source_ratio = round(source_chars / total_chars, 3) if total_chars else 0.0
    uploader = [c["text"] for c in all_clauses if c["kind"] == "UP主口播候选"]
    elapsed = time.monotonic() - started
    music_basis = (_music_evidence(row, audio_chunks, content_events, ocr_frames, _visual_original(config, rid))
                   if ratio("BGM") >= .6 else None)
    suspected_music = True if music_basis else None
    result = {**base, "status": "ok", "segments": _sentence_segments(row, audio_chunks),
            "audio_chunks": audio_chunks,
            "transcript": "".join(s["text"] for s in audio_chunks),
            "uploader_speech": uploader, "source_dialogue_ratio": source_ratio,
            "speech_ratio": ratio("Speech"), "bgm_ratio": ratio("BGM"),
            "laughter_ratio": ratio("Laughter"),
            "events": {"speech": ratio("Speech"), "bgm": ratio("BGM"), "laughter": ratio("Laughter")},
            "suspected_uploader_music": suspected_music,
            "uploader_music": {"suspected": suspected_music,
                               "basis": music_basis or "仅报告 BGM 占比；缺少配乐归属旁证"},
            "played_seconds": round(played, 2),
            "played_recording_seconds": round(played, 2),
            "recording_file_seconds": round(recording_duration, 2),
            "processing_seconds": round(elapsed, 2),
            "realtime_factor": round(elapsed / played, 3) if played else None,
            "interpretation_limit": "片段级声音标签；未做说话人声纹识别"}
    return reinterpret_existing(row, config, result, index, content_events, ocr_frames, player_frames)


def run(config: CaseConfig, row_ids: list[str] | None = None, *, force: bool = False) -> dict:
    selected = set(row_ids or config.row_ids)
    ledger = [json.loads(line) for line in (config.case_dir / "ledger.jsonl").read_text().splitlines() if line]
    output = config.case_dir / "audio"
    output.mkdir(parents=True, exist_ok=True)
    source = Index.load(config.index_dir / "index.sqlite") if (config.index_dir / "index.sqlite").is_file() else None
    model = None
    ocr_cache: dict[str, dict[int, list[dict]]] = {}
    counts = {"ok": 0, "skipped": 0, "whisper_thai": 0, "error": 0, "cached": 0, "reinterpreted": 0}
    def context(row: dict) -> tuple[list[dict], dict[int, list[dict]], int]:
        rid = row["row_id"]
        content_path = config.case_dir / "content" / f"{rid}.json"
        content = json.loads(content_path.read_text(encoding="utf-8")) if content_path.is_file() else {}
        video_name = row.get("video")
        if video_name and played_recording_spans(row) and video_name not in ocr_cache:
            ocr_cache.clear()
            try:
                ocr_cache[video_name] = load_ocr(config.case_dir, video_name)
            except FileNotFoundError:
                ocr_cache[video_name] = {}
        return (content.get("events") or [], ocr_cache.get(video_name, {}),
                int((content.get("counts") or {}).get("player_frames") or 0))
    for row in ledger:
        rid = row["row_id"]
        if rid not in selected:
            continue
        path = output / f"{rid}.json"
        ledger_fingerprint = hashlib.sha256(json.dumps(
            [row["video"], row["session_id"], played_recording_spans(row), "D-043-audio-v2"],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if path.is_file() and not force:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("status") in ("ok", "skipped", "whisper_thai") and old.get("ledger_fingerprint") == ledger_fingerprint:
                if old.get("rule_version") == RULE_VERSION:
                    counts["cached"] += 1
                    continue
                if old.get("status") != "ok" or old.get("audio_chunks"):
                    if old.get("status") == "ok":
                        events, frames, player_frames = context(row)
                        old = reinterpret_existing(row, config, old, source, events, frames, player_frames)
                    else:
                        old["rule_version"] = RULE_VERSION
                    path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
                    counts["reinterpreted"] += 1
                    print(f"audio {rid}: reinterpreted", flush=True)
                    continue
        if played_recording_spans(row) and "泰配" not in row["plaintiff"].get("title", "") and model is None:
            model = _model(config.path_of(config.raw["models"]["audio"]["model"]))
        try:
            events, frames, player_frames = context(row)
            result = analyze_row(row, config, model, source, events, frames,
                                 player_frames=player_frames)
            result["session_id"] = row["session_id"]
            result["ledger_fingerprint"] = ledger_fingerprint
            counts[result["status"]] += 1
        except Exception as error:
            result = {"row_id": rid, "video": row["video"], "session_id": row["session_id"],
                      "ledger_fingerprint": ledger_fingerprint, "status": "error",
                      "error": str(error), "audio_uploaded": False}
            counts["error"] += 1
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"audio {rid}: {result['status']}", flush=True)
    (output / "summary.json").write_text(json.dumps(counts, ensure_ascii=False, indent=1), encoding="utf-8")
    return counts
