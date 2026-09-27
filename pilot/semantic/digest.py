"""RowDigest (WO-004b §3.1): one fact sheet per ledger row, every value with its source.

Inputs are read-only module outputs: ledger (R2), content (WO-003a-R1), ASR (WO-003b, rows 1051-1127),
visual facts (WO-004a) and the plaintiff's table.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from pilot.textindex.text import normalize, similarity
from pilot.textindex.t2s import T2S

MAX_UP_LINES = 80          # UP-text lines handed to the model; longer lists are sampled evenly
DEDUP_WINDOW_S = 6.0       # a caption and a speech line this close with the same words are one line
MUSIC_TAG = re.compile(r"^(?:[J♪♫门口]\s+|冂\s*)(\S.{0,20})$")   # bilibili "music detected" chip; the note OCRs as J / 门 / 冂 / 口
MUSIC_COUNT = re.compile(r"^发现\s*(\d+)\s*首")    # the same chip before it names the songs: "发现2首音乐"


def v(value, source: str) -> dict:
    return {"value": value, "source": source}


VISUAL_FILES = {"r1": ("predictions_r1.csv",), "full": ("predictions_r1.csv", "predictions_full.csv"),
                "consensus": ("predictions_consensus_r3.csv",), "local-r3": ("predictions_r3.csv",)}
CONSENSUS = "三方定值"          # WO-004a-R3: GLM, DeepSeek and geometry settled per value; "不确定" where they disagree


def load_visual(vlm_dir: Path, which: str = "full") -> tuple[dict[str, dict], dict[str, str]]:
    """Visual facts per row: "r1" = the 19 Qwen3-VL rows of predictions_r1.csv (WO-004a-R1); "full" = a row of
    predictions_full.csv (WO-004a-R2) wins over the same row of r1; "consensus" = predictions_consensus_r3.csv
    (WO-004a-R3, D-033); "local-r3" = predictions_r3.csv. Returns (rows, {row_id: file name})."""
    rows, source = {}, {}
    for name in VISUAL_FILES[which]:
        path = vlm_dir / name
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            if (r.get("model", "").startswith("qwen3-vl") or r.get("model") == CONSENSUS) and r.get("status", "ok") == "ok":
                rows[r["row_id"]], source[r["row_id"]] = r, str(path)
    return rows, source


def _visual(row: dict | None, source: str) -> dict | None:
    if not row:
        return None
    try:
        overlays = json.loads(row.get("overlays") or "[]")
    except ValueError:
        overlays = [row.get("overlays")]
    return {"layout": row.get("layout") or "", "rights_ratio_model": row.get("rights_content_ratio") or "",
            "rights_ratio_geometry": row.get("geometry_ratio") or "", "uploader_on_camera": row.get("uploader_on_camera") or "",
            "overlays": [o for o in overlays if o], "other_works": row.get("other_works") or "",
            "other_works_desc": row.get("other_works_desc") or "", "ui_status": row.get("ui_status") or "",
            "consensus": row.get("model") == CONSENSUS,
            "source": f"{source}（{'三方定值' if row.get('model') == CONSENSUS else 'Qwen3-VL-8B'}，行 {row['row_id']}）"}


def music_tags(frames: dict[int, list[dict]], t0: float, t1: float) -> list[str]:
    """Song names from bilibili's music chip in the session's frames (background-music clue)."""
    names: dict[str, int] = {}
    for t in range(int(t0), int(t1) + 1):
        for o in frames.get(t, ()):
            m = MUSIC_TAG.match(o["text"].strip())
            if m and o["conf"] >= 0.5:
                name = m.group(1).strip()
                names[name] = names.get(name, 0) + 1
    return [n for n, c in sorted(names.items(), key=lambda x: -x[1]) if c >= 2]


def song_names(raw: list[str]) -> tuple[list[str], int]:
    """(song names, songs counted by the chip): OCR variants of one name ("眉间雪" / "肩间雪") count once,
    the first (most frequent) spelling wins; "发现N首音乐" is a count, not a name."""
    names, counted = [], 0
    for name in raw:
        if m := MUSIC_COUNT.match(name):
            counted = max(counted, int(m.group(1)))
            continue
        k = normalize(name)
        if k and not any(abs(len(k) - len(normalize(o))) <= 1 and similarity(k, normalize(o)) >= 0.5 for o in names):
            names.append(name)
    return names, counted


def _is_noise(text: str, uploader: str) -> bool:
    """Music chips and garbled uploader watermarks that slipped through the caption track."""
    if MUSIC_TAG.match(text.strip()):
        return True
    n, u = normalize(text), normalize(uploader)
    return len(u) >= 4 and any(u[i:i + 4] in n for i in range(len(u) - 3)) and len(n) <= len(u) + 8


def up_texts(content: dict | None, asr: dict | None, uploader: str = "", music: list[str] = (),
             audio: dict | None = None) -> tuple[list[dict], dict]:
    """Uploader text on the suspect video's timeline: non-source captions plus Chinese non-source speech,
    sorted and de-duplicated. Foreign speech (the Thai dubs mix dubbed dialogue with the host) and
    foreign subtitles are only counted."""
    lines, extra = [], {"foreign_speech_segments": 0, "foreign_speech_language": None, "foreign_captions": 0}
    songs = {normalize(m) for m in music}              # the music chip read without its note icon
    for n, e in enumerate((content or {}).get("events", [])):
        if e["status"] != "NO_MATCH":
            continue
        if e.get("foreign"):
            extra["foreign_captions"] += 1
            continue
        if _is_noise(e["text"], uploader) or normalize(e["text"]) in songs:
            continue
        lines.append({"t": e["content_start"], "text": e["text"], "kind": e.get("category", "字幕"),
                      "source": f"content 事件 {n}（录像 {e['rec_start']:.0f} s）"})
    for n, s in enumerate((asr or {}).get("segments", [])):
        if s.get("classification") != "NON_ORIGINAL" or not (s.get("text") or "").strip():
            continue
        if s.get("language") != "zh":
            extra["foreign_speech_segments"] += 1
            extra["foreign_speech_language"] = s.get("language")
            continue
        text = s["text"].translate(T2S).strip()
        t = s.get("content_start_seconds")
        if t is None:
            continue
        dup = any(abs(l["t"] - t) <= DEDUP_WINDOW_S and similarity(normalize(l["text"]), normalize(text)) >= 0.8
                  for l in lines)
        if not dup:
            lines.append({"t": round(t, 1), "text": text, "kind": "语音",
                          "source": f"asr 片段 {n}（录像 {s['recording_start_seconds']:.0f} s）"})
    for n, s in enumerate((audio or {}).get("up_speech") or []):   # WO-008 "UP主口播" (R2)
        text, t = s["text"].translate(T2S).strip(), s.get("t")
        if t is None or not text:
            continue
        dup = any(abs(l["t"] - t) <= DEDUP_WINDOW_S and similarity(normalize(l["text"]), normalize(text)) >= 0.8
                  for l in lines)
        if not dup:
            lines.append({"t": round(float(t), 1), "text": text, "kind": "口播", "source": f"{audio['source']} 片段 {n}"})
    lines.sort(key=lambda l: l["t"])
    seen, out = set(), []
    for l in lines:                        # the same caption repeated later (a recap card) counts once
        key = normalize(l["text"])
        if key and key not in seen:
            seen.add(key)
            out.append(l)
    if len(out) > MAX_UP_LINES:
        step = len(out) / MAX_UP_LINES
        out = [out[int(i * step)] for i in range(MAX_UP_LINES)]
        extra["sampled_from"] = len(lines)
    for i, l in enumerate(out, 1):
        l["i"] = i
    return out, extra


def build_digest(row: dict, content: dict | None, asr: dict | None, visual_row: dict | None, visual_source: str,
                 music: list[str], other_batches: list[int], audio: dict | None = None) -> dict:
    p = row["plaintiff"]
    ledger_src = "台账 ledger.jsonl（R2）"
    raw_music = music + [m.group(1).strip() for e in (content or {}).get("events", [])
                         if (m := MUSIC_TAG.match(e["text"].strip()))]
    music, music_counted = song_names(raw_music)
    lines, extra = up_texts(content, asr, p.get("uploader") or "", raw_music, audio)
    src_events = [e for e in (content or {}).get("events", []) if e["status"] == "LINKED" and not e.get("lyric")]
    return {
        "row_id": row["row_id"],
        "bv": v(p.get("bv") or row.get("observed_bv"), "原告表格 BV号"),
        "title": v(p.get("title") or row.get("observed_title") or "", "原告表格 标题"),
        "uploader": v(p.get("uploader") or row.get("observed_uploader") or "", "原告表格 上传者"),
        "plaintiff": {"part_claimed": p.get("part_claimed"), "views_claimed": p.get("views_claimed"),
                      "video_type": p.get("video_type"), "duration": p.get("duration"),
                      "source": f"原告表格 {p.get('source_sheet')} 第 {p.get('source_row')} 行"},
        "ledger": {
            "video": row.get("video"), "session": [row.get("video_start"), row.get("video_end")],
            "video_duration": v(row.get("video_duration"), ledger_src + " video_duration"),
            "capture_seconds": v(row.get("capture_seconds"), ledger_src + " capture_seconds"),
            "complete": v(row.get("complete"), ledger_src + " complete"),
            "speed": v(row.get("speed"), ledger_src + " speed"),
            "seeks": v([{"at": s.get("at"), "from": s.get("from"), "to": s.get("to")} for s in row.get("seeks")]
                       if isinstance(row.get("seeks"), list) else ("未知" if row.get("seeks") else []),
                       ledger_src + " seeks"),
            "played_intervals": v(row.get("played_intervals") or [], ledger_src + " played_intervals"),
            "views": v(row.get("views"), ledger_src + " views（录像/截图读数）"),
            "parts": v(row.get("parts") or [], ledger_src + " parts"),
            "duplicate_bv_rows": row.get("duplicate_bv_rows") or [],
            "flags": row.get("flags") or [],
        },
        "episodes": {
            "verdict": (content or {}).get("verdict"), "statement": (content or {}).get("statement"),
            "evidenced": (content or {}).get("evidenced_episodes") or [],
            "weak": (content or {}).get("weak_episodes") or [],
            "title_episode": (content or {}).get("title_episode"),
            "claim_missing": (content or {}).get("claim_missing") or {},      # WO-004b-R3: claimed episodes missing locally
            "source": f"content/{row['row_id']}.json（WO-003a-R1）" if content else "无字幕模块输出",
        },
        "source_lines": {"count": len(src_events),
                         "episodes": sorted({e["episode"] for e in src_events}),
                         "first_content_s": min((e["content_start"] for e in src_events), default=None),
                         "last_content_s": max((e["content_start"] for e in src_events), default=None),
                         "lyric_lines": sum(1 for e in (content or {}).get("events", []) if e.get("lyric")),
                         "source": f"content/{row['row_id']}.json"},
        "caption_counts": {k: sum(1 for e in (content or {}).get("events", [])
                                  if e.get("category") == k and not e.get("foreign"))   # a dub's own subtitles are not the uploader's
                           for k in ("底部解说字幕", "竖排或特效花字", "其他")},
        "up_texts": lines,
        "up_text_extra": extra,
        "asr": "有" if asr else ("无 ASR（D-020 中止）" if int(row["row_id"]) > 1127 else "无 ASR"),
        "visual": _visual(visual_row, visual_source),
        "music": v(music, "录像 OCR：B 站识别音乐标签"),
        "music_counted": music_counted,          # "发现N首音乐" without the names
        "other_batches": v(other_batches, "原告总表：同 BV 的其他公证批次行"),
        "audio": audio,                          # WO-008 sound facts, None without them (R2)
    }
