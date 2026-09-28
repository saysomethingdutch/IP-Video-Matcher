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

Promotional speech (WO-103-R1: "本视频需要付费一个硬币才能观看" and the like) is a clue for X: code writes it
into AC【其他】 with its time and flags it when the picture shows no drive link or group number (D-061 ④).
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


def _overlap(spans, segs, contents) -> float:
    """Seconds of `spans` ([start, end] on the recording clock) that fall in segments of the given contents."""
    total = 0.0
    for a, b in spans or []:
        for s in segs:
            if s["content"] in contents:
                total += max(0.0, min(b, s["end"] + 1) - max(a, s["start"]))
    return total


def music_evidence(audio: dict | None, tl: dict | None) -> dict:
    """{"verdict": "是" | "否" | "无旁证" | None, "evidence": [...], "series_music_seconds": float}.
    WO-103-R1 hands over per-second facts (music_facts); older outputs are read from the 4 s chunks."""
    if not usable(audio) or not tl:
        return {"verdict": None, "evidence": [], "series_music_seconds": 0.0}
    segs = tl["segments"]
    facts = audio.get("music_facts")
    if isinstance(facts, dict):
        music = facts.get("music_only_spans") or []
        evidence = []
        if (x := _overlap(music, segs, NOT_SERIES)) >= 1:
            evidence.append(f"非吞噬星空画面上的配乐（约 {round(x)} 秒）")
        if (x := _overlap(facts.get("subtitle_without_speech_spans"), segs, ("tsxk",))) >= 1:
            evidence.append(f"原片画面有台词字幕却听不到人声（约 {round(x)} 秒），原声疑被替换")
        if _overlap(facts.get("lyric_subtitle_spans"), segs, NOT_SERIES + ("unknown",)) >= 1:
            evidence.append("非原片画面上的歌词字幕")
        if facts.get("bilibili_music_tags"):
            evidence.append("B站音乐标签")
        series = _overlap(music, segs, ("tsxk",))
        verdict = "是" if evidence else ("无旁证" if music else "否")
        return {"verdict": verdict, "evidence": evidence or (["未检出纯音乐时段"] if not music else []),
                "series_music_seconds": round(series)}
    chunks = audio.get("audio_chunks") or []                     # WO-103 first round
    music = [c for c in chunks if _music(c)]
    if not music:
        return {"verdict": "否", "evidence": ["未检出音乐"], "series_music_seconds": 0.0}
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


PROMO_KINDS = (("要求投币或付费才能观看", ("硬币", "投币", "付费", "充电")),
               ("引导观众去别处找后续资源", ("资源", "会员", "提前看", "后续", "网盘", "私信", "主页", "简介", "评论区", "群")),
               ("招揽关注和互动", ("关注", "三连", "点赞", "收藏", "订阅")))


def promo_kind(text: str) -> str:
    return next((label for label, words in PROMO_KINDS if any(w in text for w in words)), "推广招揽")


def promotions(audio: dict | None) -> list[dict]:
    """WO-103-R1 promotional_speech, merged when within 5 s: [{start, end, kinds, texts}] (recording s)."""
    if not usable(audio):
        return []
    items = sorted((x for x in audio.get("promotional_speech") or [] if isinstance(x, dict) and x.get("text")),
                   key=lambda x: x.get("recording_start") or 0)
    out = []
    for x in items:
        a, b = float(x.get("recording_start") or 0), float(x.get("recording_end") or x.get("recording_start") or 0)
        if out and a - out[-1]["end"] <= 5:
            out[-1]["end"] = max(out[-1]["end"], b)
            out[-1]["texts"].append(x["text"])
        else:
            out.append({"start": a, "end": b, "texts": [x["text"]]})
    for o in out:
        o["kinds"] = list(dict.fromkeys(promo_kind(t) for t in o["texts"]))
    return out


def redubs(audio: dict | None) -> list[dict]:
    return [x for x in (audio or {}).get("redub_candidates") or [] if isinstance(x, dict)] if usable(audio) else []


def uploader_speech(audio: dict | None) -> list[str]:
    if not usable(audio):
        return []
    lines = audio.get("uploader_speech")
    if lines is None:
        lines = [s["text"] for s in audio.get("segments") or [] if str(s.get("kind")) in ("uploader", "up", "NON_ORIGINAL")]
    return [str(x.get("text") if isinstance(x, dict) else x).strip() for x in lines
            if str(x.get("text") if isinstance(x, dict) else x).strip()]


def section(audio: dict | None, music: dict) -> str:
    """The 【声音】 line (D-047): filtered uploader speech and 疑似UP主配乐, nothing else."""
    if not usable(audio):
        return "【声音】未提供（本行没有声音分析结果）"
    speech = uploader_speech(audio)
    secs = audio.get("uploader_speech_seconds")
    said = f"UP主口播（过滤后）{len(speech)} 句" + (f"（约 {round(secs)} 秒）" if secs else "") + \
        ("：" + " / ".join(speech[:12]) if speech else "")
    parts = [said]
    if (rd := redubs(audio)):
        parts.append(f"改配音候选 {len(rd)} 句（配音与画面字幕不一致）")
    if (pr := promotions(audio)):
        kinds = "、".join(dict.fromkeys(k for p in pr for k in p["kinds"]))
        parts.append(f"推广口播 {sum(len(p['texts']) for p in pr)} 句（{kinds}；时间由程序写进【其他】）")
    verdict = music["verdict"]
    why = f"（依据：{'；'.join(music['evidence'])}）" if verdict == "是" else (
        "（只有原片画面加纯音乐，不能证明是 UP 主加的）" if verdict == "无旁证" and music["series_music_seconds"] else "")
    parts.append(f"疑似UP主配乐：{verdict}{why}")
    return "【声音】" + "；".join(parts)
