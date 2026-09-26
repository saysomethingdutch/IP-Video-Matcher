"""Per-row pipeline: caption events -> tracks -> episode evidence -> claim verdict (WO-003a)."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from pilot.textindex.search import Index
from pilot.textindex.text import normalize

from . import evidence as ev
from . import titles
from .extract import HAN, Event, load_ocr, merge_events, row_lines, to_content

MIN_EVENT_CHARS = 2       # a single character carries no evidence
MIN_SINGLE_CONF = 0.5     # a one-frame event needs a readable OCR line
BOTTOM_Y = 0.55           # centre y below which a centred line counts as a bottom caption
EFFECT_H_RATIO = 1.8      # lines this much taller than the row's usual caption are title cards / effect text

BOTTOM_CAPTION, EFFECT_TEXT, OTHER_TEXT = "底部解说字幕", "竖排或特效花字", "其他"
DEFAULT_BAND_Y = 0.72     # centre y of the burned-in source subtitle in these recordings
OFF_BAND = 0.08           # farther than this from the subtitle band: a quote in a title card / effect text
STRONG_SIM, STRONG_CHARS = 0.95, 5
QUOTES = "“”\"「」『』"      # burned-in subtitles carry no quotation marks; narration quoting a line does
SOLO_MIN_CHARS = 5        # a short line without a continuous neighbour ("话说", "我爱你") is not a link


def _category(e: Event, screen_cx: float, caption_h: float) -> str:
    """Rough split of non-source text: bottom captions, vertical / effect text, other."""
    x, y, w, h = e.best.box
    if h > 1.5 * w or h > EFFECT_H_RATIO * caption_h:
        return EFFECT_TEXT
    if e.best.cy >= BOTTOM_Y and abs(e.best.cx - screen_cx) <= 0.2:
        return BOTTOM_CAPTION
    return OTHER_TEXT


def _strong(r: dict) -> bool:
    return r["status"] == ev.LINKED and r["sim"] >= STRONG_SIM and len(normalize(r["source_text"])) >= STRONG_CHARS


def _demote(r: dict, status: str, track: str, reason: str) -> None:
    r["demoted"] = {"status": r["status"], "episode": r.get("episode"), "t_source": r.get("t_source"),
                    "sim": r.get("sim"), "reason": reason}
    r["status"], r["track"] = status, track


def _apply_caption_priors(placed: list, links: list[dict]) -> None:
    """What a burned-in source subtitle looks like, beyond its text:
    - it sits in one band: a weaker match elsewhere is a quote in a title card or effect text;
    - it has no quotation marks: a quoted line is narration quoting the drama;
    - a short line only counts with a continuous neighbour on the chain."""
    ys = [p[3].best.cy for p, r in zip(placed, links) if _strong(r)]
    band = median(ys) if ys else DEFAULT_BAND_Y
    for p, r in zip(placed, links):
        if r["status"] == ev.NO_MATCH:
            continue
        text = p[3].best.text
        if abs(p[3].best.cy - band) > OFF_BAND and not _strong(r):
            _demote(r, ev.NO_MATCH, ev.NON_SOURCE, "不在字幕带内的台词引用")
        elif any(q in text for q in QUOTES):
            _demote(r, ev.NO_MATCH, ev.NON_SOURCE, "引号内的台词引用")
        elif r["status"] == ev.LINKED and not r["support"] and len(normalize(text)) < SOLO_MIN_CHARS:
            r["candidates"] = [{"episode": r["episode"], "t_start": r["t_source"], "sim": r["sim"],
                                "text": r["source_text"]}] + r["candidates"]
            _demote(r, ev.AMBIGUOUS, ev.UNCERTAIN, "孤立短句，前后没有连贯的台词")


def process_row(row: dict, frames: dict[int, list[dict]], index: Index, episodes: ev.Episodes) -> dict:
    rid = row["row_id"]
    claim_raw = row["plaintiff"].get("part_claimed") or ""
    claim = ev.parse_claim(claim_raw)
    base = {"row_id": rid, "video": row.get("video"), "session": [row.get("video_start"), row.get("video_end")],
            "claim": {"raw": claim_raw, "episodes": sorted(claim) if claim else None,
                      "status": "OK" if claim else ev.CLAIM_UNPARSEABLE}}
    if not row.get("video") or row.get("video_start") is None:
        result = ev.UNVERIFIED
        return {**base, "verdict": result, "statement": "未关联到录像会话，无法核验。", "evidenced_episodes": [],
                "weak_episodes": [], "title_episode": None, "evidence": [], "events": [], "counts": {},
                "dropped": {}}

    pmap, speed, duration = row.get("playback_map") or [], row.get("speed"), row.get("video_duration")
    rf = row_lines(frames, row)
    lines, playing = {}, []
    for t in rf.player:
        c, _ = to_content(t, pmap, speed)
        if c is not None and duration and c >= duration - 0.5:
            rf.dropped["视频结束后"] += len(rf.lines[t])   # end screen / recommendations after the video ended
            continue
        lines[t] = rf.lines[t]
        playing.append(t)
    title_ep = titles.title_episode(frames, playing, row.get("observed_title"), claim)
    events = [e for e in merge_events(lines)
              if len(e.best.norm) >= MIN_EVENT_CHARS and (len(e.lines) > 1 or e.conf >= MIN_SINGLE_CONF)]

    placed = []
    for e in events:
        c, estimated = to_content(e.rec_start, pmap, speed)
        c_end, _ = to_content(e.rec_end, pmap, speed)
        placed.append((c if c is not None else e.rec_start - row["video_start"], estimated, c_end, e))
    placed.sort(key=lambda p: (p[0], p[3].rec_start))
    links = ev.caption_links(index, episodes, [(p[0], p[3].best.text) for p in placed])
    _apply_caption_priors(placed, links)

    bottom = [p[3].best for p in placed if p[3].best.cy >= BOTTOM_Y]
    screen_cx = median(l.cx for l in bottom) if bottom else 0.48
    caption_h = median(l.box[3] for l in bottom) if bottom else 0.04
    out_events = []
    for (c, estimated, c_end, e), r in zip(placed, links):
        item = {"rec_start": e.rec_start, "rec_end": e.rec_end,
                "content_start": round(c, 1), "content_end": None if c_end is None else round(c_end, 1),
                "content_estimated": estimated, "text": e.best.text, "conf_ocr": round(e.conf, 2),
                "frames": len(e.lines), "bbox": [round(v, 4) for v in e.best.box], "track": r["track"],
                "status": r["status"]}
        if r["status"] == ev.LINKED:
            item.update(episode=r["episode"], t_source=r["t_source"], source_text=r["source_text"],
                        sim=r["sim"], conf=r["conf"], suspected=bool(r.get("suspected")), lyric=ev.is_lyric(r))
            if r.get("recap"):
                item["recap"] = r["recap"]
        elif r["status"] == ev.AMBIGUOUS:
            item["candidates"] = [{k: c[k] for k in ("episode", "t_start", "sim", "text")} for c in r["candidates"]]
            if r.get("demoted"):
                item["demoted"] = r["demoted"]
        else:
            item["category"] = _category(e, screen_cx, caption_h)
            if not HAN.search(e.best.norm):
                item["foreign"] = True           # e.g. English subtitles of a dubbed version
            if r.get("demoted"):
                item["demoted"] = r["demoted"]
        out_events.append(item)

    found = ev.evidenced_episodes(links)
    result = ev.verdict(found, claim)
    evidence = [{"rec_start": x["rec_start"], "rec_end": x["rec_end"], "content_start": x["content_start"],
                 "episode": x["episode"], "t_source": x["t_source"], "text": x["text"],
                 "source_text": x["source_text"], "sim": x["sim"], "conf": x["conf"],
                 "suspected": x["suspected"], **({"recap": x["recap"]} if "recap" in x else {})}
                for x in out_events if x["status"] == ev.LINKED and not x["lyric"]]
    counts = {"events": len(out_events),
              "source_lines": sum(x["status"] == ev.LINKED and not x["lyric"] for x in out_events),
              "lyric_lines": sum(x["status"] == ev.LINKED and x["lyric"] for x in out_events),
              "non_source_lines": sum(x["status"] == ev.NO_MATCH for x in out_events),
              "ambiguous_lines": sum(x["status"] == ev.AMBIGUOUS for x in out_events),
              "player_frames": len(rf.player)}
    weak = ev.weak_leads(links, found)
    text = ev.statement(found, claim_raw, claim, result, weak,
                        ev.unverified_reason(out_events, claim, episodes.missing) if result == ev.UNVERIFIED else "")
    if result == ev.UNVERIFIED and title_ep:
        text += "参考：" + titles.describe(title_ep, claim_raw)
    return {**base, "verdict": result, "statement": text, "evidenced_episodes": found, "weak_episodes": weak,
            "title_episode": title_ep, "evidence": evidence, "events": out_events, "counts": counts,
            "dropped": dict(rf.dropped)}


def run(case_dir: Path, index_path: Path | None, ledger: Path | None = None, only: set[str] | None = None,
        log=print) -> list[dict]:
    index = Index.load(index_path) if index_path else Index([])  # no source index: captions still extracted
    episodes = ev.Episodes(index)
    ledger = ledger or case_dir / "ledger.jsonl"
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    if only:
        rows = [r for r in rows if r["row_id"] in only]
    cache: dict[str, dict] = {}
    results = []
    for row in rows:
        video = row.get("video")
        if video and video not in cache:
            cache.clear()                            # rows are grouped by video; keep one recording in memory
            cache[video] = load_ocr(case_dir, video)
        results.append(process_row(row, cache.get(video, {}), index, episodes))
        r = results[-1]
        log(f"{r['row_id']} {r['verdict']:<17} {', '.join(e['label'] for e in r['evidenced_episodes']) or '-'}"
            f"  (claim {r['claim']['raw']}; {r['counts'].get('source_lines', 0)} source / "
            f"{r['counts'].get('non_source_lines', 0)} other lines)")
    return results
