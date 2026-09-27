"""Sound facts from WO-008 (pilot/audio, local only, D-019) read by the classification (WO-004b-R2 §1.2).

Interface, one file per row: <case>/audio/<row>.json. Unknown keys are ignored; a missing file means no
sound facts, and then the RowDigest and the prompt are exactly what they were without sound.

    {
      "row_id": "1663",
      "engine": "SenseVoice-Small",            # "whisper" for the Thai rows
      "played_seconds": 312.5,                 # only the ledger's played spans are analysed
      "segments": [                            # seconds on the suspect video's timeline
        {"start": 3.2, "end": 6.8, "text": "…", "kind": "uploader",       # "uploader" | "source"
         "episode": null, "similarity": null}                             # "source": matched the drama index
      ],
      "events": {"speech": 0.62, "bgm": 0.30, "laughter": 0.03},          # shares of the played time
      "uploader_music": {"suspected": true, "basis": "原剧台词很少、BGM 占主导"}
    }

Accepted spellings: kind "uploader" / "up" / "UP主口播" / "NON_ORIGINAL" (the WO-003b word); events keys
"speech" or "speech_ratio", "bgm" or "bgm_ratio", "laughter" or "laughter_ratio"; "uploader_music" as an
object or a bare boolean.
"""
from __future__ import annotations

import json
from pathlib import Path

UPLOADER_KINDS = {"uploader", "up", "up主口播", "NON_ORIGINAL", "non_original", "non_source"}


def _ratio(events: dict, *keys: str) -> float | None:
    for k in keys:
        if events.get(k) is not None:
            try:
                return round(float(events[k]), 3)
            except (TypeError, ValueError):
                return None
    return None


def load_audio(case_dir: Path, rid: str) -> dict | None:
    path = case_dir / "audio" / f"{rid}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    speech = []
    for s in raw.get("segments") or []:
        kind = str(s.get("kind") or s.get("classification") or "")
        text = str(s.get("text") or "").strip()
        if text and (kind in UPLOADER_KINDS or kind.lower() in UPLOADER_KINDS):
            speech.append({"t": s.get("start", s.get("content_start_seconds")), "text": text})
    events = raw.get("events") or {}
    music = raw.get("uploader_music")
    suspected = music.get("suspected") if isinstance(music, dict) else music if isinstance(music, bool) else None
    label = {True: "是", False: "否"}.get(suspected, "无旁证")   # D-043: only corroborated music counts (R4)
    return {"up_speech": speech, "speech_ratio": _ratio(events, "speech", "speech_ratio"),
            "bgm_ratio": _ratio(events, "bgm", "bgm_ratio"), "laughter_ratio": _ratio(events, "laughter", "laughter_ratio"),
            "uploader_music": suspected, "music_label": label,
            "music_basis": music.get("basis", "") if isinstance(music, dict) else "",
            "engine": raw.get("engine"), "played_seconds": raw.get("played_seconds"),
            "source": f"audio/{rid}.json（WO-008 {raw.get('engine') or '声音'}）"}


def reliable(a: dict | None) -> bool:
    """A sound fact the classification may see (WO-004b-R4): filtered uploader speech, or music whose
    attribution is corroborated either way. Raw BGM / speech shares stay in the RowDigest for people only."""
    return bool(a) and (bool(a.get("up_speech")) or a.get("music_label", "无旁证") != "无旁证")


def describe(a: dict) -> str:
    """The 【声音】 line of the classification prompt: two items only (WO-004b-R4 §1.3)."""
    n = len(a.get("up_speech") or [])
    speech = f"UP主口播 {n} 句（已并入【UP主文字】）" if n else "UP主口播：未检出（检出率有限，不代表没有口播）"
    label = a.get("music_label", "无旁证")
    music = f"疑似UP主配乐：{label}" + (f"（旁证：{a['music_basis']}）" if label == "是" and a.get("music_basis") else "")
    return f"{speech}；{music}"
