"""What the classification may know about the sound (WO-102-R1 §1.2, §1.5; D-047, D-060).

Input: WO-103's <case>/audio/<序号>.json (local SenseVoice; audio is never uploaded). The prompt gets only the
filtered uploader speech and one line "疑似UP主配乐：是 / 否 / 无旁证" — never the raw BGM share: the series has
its own music, so music alone proves nothing.

Whether music was added by the uploader is decided here, with the picture: WO-103 hands it over as "交分类
判断". Evidence (D-060 ③④, WO-102-R1 §1.5), all on the recording clock:
1. music over pictures that are not 吞噬星空 (unrelated, the uploader's own, traffic pages);
2. series frames that show dialogue captions while the sound has music and no voice (the original sound
   was replaced);
3. lyric captions on pictures that are not the series;
4. B站's music chip on screen.
Series pictures with music only ("原片画面加纯音乐") are not evidence: V stays 否 and is flagged.
"""
from __future__ import annotations

from pilot.textindex.text import normalize

MUSIC_TAGS = ("BGM", "Music")
NOT_SERIES = ("unrelated", "creator", "traffic")


def usable(audio: dict | None) -> bool:
    return bool(audio) and audio.get("status", "ok") == "ok" and bool(audio.get("audio_chunks") or audio.get("segments"))


def _music(chunk: dict) -> bool:
    return any(t in (chunk.get("tags") or []) for t in MUSIC_TAGS)


def _pure(chunk: dict) -> bool:
    return _music(chunk) and len(normalize(chunk.get("text") or "")) < 4


def _content_at(segments: list[dict], t: float) -> str | None:
    for s in segments:
        if s["start"] <= t <= s["end"] + 0.99:
            return s["content"]
    return None


def music_evidence(audio: dict | None, tl: dict | None) -> dict:
    """{"verdict": "是" | "否" | "无旁证" | None, "evidence": [...], "series_music_seconds": float}."""
    if not usable(audio) or not tl:
        return {"verdict": None, "evidence": [], "series_music_seconds": 0.0}
    chunks = audio.get("audio_chunks") or []
    music = [c for c in chunks if _music(c)]
    if not music:
        return {"verdict": "否", "evidence": ["未检出音乐"], "series_music_seconds": 0.0}
    segs = tl["segments"]
    mid = lambda c: (c["recording_start"] + c["recording_end"]) / 2
    dur = lambda cs: round(sum(c["recording_end"] - c["recording_start"] for c in cs))
    evidence = []
    off_series = [c for c in music if _content_at(segs, mid(c)) in NOT_SERIES]
    if off_series:
        evidence.append(f"非吞噬星空画面上的配乐（约 {dur(off_series)} 秒）")
    pure = [c for c in chunks if _pure(c)]
    silent = [f for f in tl["frames"] if f.get("content") == "tsxk" and f.get("captions")
              and any(c["recording_start"] - 1 <= f["t"] <= c["recording_end"] + 1 for c in pure)]
    if silent:
        evidence.append(f"原片画面有台词字幕却听不到人声（{len(silent)} 帧），原声疑被替换")
    if any(f.get("lyric") and f.get("content") != "tsxk" for f in tl["frames"]):
        evidence.append("非原片画面上的歌词字幕")
    if any(f.get("music_tag") for f in tl["frames"]):
        evidence.append("B站音乐标签")
    series = [c for c in pure if _content_at(segs, mid(c)) == "tsxk"]
    return {"verdict": "是" if evidence else "无旁证", "evidence": evidence, "series_music_seconds": dur(series)}


def uploader_speech(audio: dict | None) -> list[str]:
    if not usable(audio):
        return []
    lines = audio.get("uploader_speech")
    if lines is None:
        lines = [s["text"] for s in audio.get("segments") or [] if str(s.get("kind")) in ("uploader", "up", "NON_ORIGINAL")]
    return [str(x).strip() for x in lines if str(x).strip()]


def section(audio: dict | None, music: dict) -> str:
    """The 【声音】 line (D-047): filtered uploader speech and 疑似UP主配乐, nothing else."""
    if not usable(audio):
        return "【声音】未提供（本行没有声音分析结果）"
    speech = uploader_speech(audio)
    said = f"UP主口播（过滤后）{len(speech)} 句" + ("：" + " / ".join(speech[:12]) if speech else "")
    verdict = music["verdict"]
    why = f"（依据：{'；'.join(music['evidence'])}）" if verdict == "是" else (
        "（只有原片画面加纯音乐，不能证明是 UP 主加的）" if verdict == "无旁证" and music["series_music_seconds"] else "")
    return f"【声音】{said}；疑似UP主配乐：{verdict}{why}"
