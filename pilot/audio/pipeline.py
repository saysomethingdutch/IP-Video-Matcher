"""WO-008 local audio facts. Recording audio never leaves this process."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import time

from pilot.case_config import CaseConfig
from pilot.textindex.search import Index
from pilot.textindex.text import normalize

TAG = re.compile(r"<\|([^|]+)\|>")
SPLIT = re.compile(r"[。！？!?；;，,、\n]+")
CHUNK_SECONDS = 25.0


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


def compare_dialogue(text: str, index: Index | None) -> list[dict]:
    clauses = [piece.strip() for piece in SPLIT.split(text) if normalize(piece)]
    result = []
    for clause in clauses:
        matches = index.search(clause, min_sim=.5, top=1) if index and len(normalize(clause)) >= 4 else []
        best = matches[0] if matches else None
        source = bool(best and best.sim >= .72 and len(normalize(clause)) >= 4)
        result.append({"text": clause, "kind": "原剧台词" if source else "UP主口播候选",
                       "source_match": best.entry.text if source else None,
                       "source_similarity": round(best.sim, 3) if best else None})
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


def analyze_row(row: dict, config: CaseConfig, model, index: Index | None) -> dict:
    rid = row["row_id"]
    chunks = played_recording_spans(row)
    base = {"row_id": rid, "video": row["video"], "engine": "SenseVoice-Small",
            "audio_uploaded": False, "playback_spans": chunks}
    if not chunks:
        return {**base, "status": "skipped", "reason": "台账无已播放录音区间", "segments": []}
    if "泰配" in row["plaintiff"].get("title", ""):
        prior = config.case_dir / "asr" / f"{rid}.json"
        return {**base, "status": "whisper_thai", "reason": "泰语沿用本地 Whisper",
                "whisper_path": str(prior) if prior.is_file() else None, "segments": []}
    preset = config.raw["evidence"]["videos"].get(row["video"])
    if preset is None:
        raise ValueError(f"recording {row['video']} is not in this batch")
    video = config.path_of(config.raw["evidence"]["video_dir"]) / preset.get("file", row["video"] + ".MP4")
    recording_duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        check=True, capture_output=True, text=True).stdout.strip())
    segments = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wo008-audio-") as temp:
        for n, (start, end) in enumerate(chunks):
            wav = Path(temp) / f"{rid}-{n:04d}.wav"
            _extract_wav(video, start, end, wav)
            output = model.generate(input=str(wav), language="auto", use_itn=True, batch_size_s=30)
            raw = str(output[0].get("text") or "") if output else ""
            text, tags = decode_tags(raw)
            clauses = compare_dialogue(text, index)
            segments.append({"recording_start": start, "recording_end": end,
                             "text": text, "tags": tags, "raw_model_text": raw,
                             "clauses": clauses})
    played = sum(end - start for start, end in chunks)
    def ratio(tag: str) -> float:
        return round(sum(s["recording_end"] - s["recording_start"] for s in segments
                         if tag in s["tags"]) / played, 3) if played else 0.0
    all_clauses = [c for segment in segments for c in segment["clauses"]]
    total_chars = sum(len(normalize(c["text"])) for c in all_clauses)
    source_chars = sum(len(normalize(c["text"])) for c in all_clauses if c["kind"] == "原剧台词")
    source_ratio = round(source_chars / total_chars, 3) if total_chars else 0.0
    uploader = [c["text"] for c in all_clauses if c["kind"] == "UP主口播候选"]
    elapsed = time.monotonic() - started
    return {**base, "status": "ok", "segments": segments,
            "transcript": "".join(s["text"] for s in segments),
            "uploader_speech": uploader, "source_dialogue_ratio": source_ratio,
            "speech_ratio": ratio("Speech"), "bgm_ratio": ratio("BGM"),
            "laughter_ratio": ratio("Laughter"),
            "suspected_uploader_music": ratio("BGM") >= .6 and source_ratio < .15,
            "played_recording_seconds": round(played, 2),
            "recording_file_seconds": round(recording_duration, 2),
            "processing_seconds": round(elapsed, 2),
            "realtime_factor": round(elapsed / played, 3) if played else None,
            "interpretation_limit": "片段级声音标签；未做说话人声纹识别，未匹配台词为UP主口播候选"}


def run(config: CaseConfig, row_ids: list[str] | None = None, *, force: bool = False) -> dict:
    selected = set(row_ids or config.row_ids)
    ledger = [json.loads(line) for line in (config.case_dir / "ledger.jsonl").read_text().splitlines() if line]
    output = config.case_dir / "audio"
    output.mkdir(parents=True, exist_ok=True)
    source = Index.load(config.index_dir / "index.sqlite") if (config.index_dir / "index.sqlite").is_file() else None
    model = None
    counts = {"ok": 0, "skipped": 0, "whisper_thai": 0, "error": 0, "cached": 0}
    for row in ledger:
        rid = row["row_id"]
        if rid not in selected:
            continue
        path = output / f"{rid}.json"
        ledger_fingerprint = hashlib.sha256(json.dumps(
            [row["video"], row["session_id"], played_recording_spans(row)],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if path.is_file() and not force:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("status") in ("ok", "skipped", "whisper_thai") and old.get("ledger_fingerprint") == ledger_fingerprint:
                counts["cached"] += 1
                continue
        if played_recording_spans(row) and "泰配" not in row["plaintiff"].get("title", "") and model is None:
            model = _model(config.path_of(config.raw["models"]["audio"]["model"]))
        try:
            result = analyze_row(row, config, model, source)
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
