"""Caption tracks and episode evidence (WO-003a §3.2, §3.3)."""
from __future__ import annotations

import re
from collections import defaultdict

from pilot.textindex.search import Index
from pilot.textindex.sequence import AMBIGUOUS, LINKED, NO_MATCH, match_sequence
from pilot.textindex.text import normalize

SOURCE, NON_SOURCE, UNCERTAIN = "原剧台词", "非原剧文字", "待定"
SUPPORTED, NARROWER, CONTRADICTED, UNVERIFIED = "SUPPORTED", "NARROWER", "CONTRADICTED", "UNVERIFIED"
CLAIM_UNPARSEABLE = "CLAIM_UNPARSEABLE"

TOTAL_EPISODES = 38
RECAP_WINDOW = (100.0, 160.0)   # seconds from file start; measured recap spans are 109-156 s
MIN_LINES = 2                   # an episode is evidenced by >= 2 linked lines, or by one line that is
LONG_LINE = 6                   # >= 6 characters with confidence >= 0.8
LONG_CONF = 0.8
WEAK_SIM, WEAK_CHARS = 0.8, 5   # weak evidence (D-015): one candidate place, sim >= 0.8, >= 5 characters


class Episodes:
    """What the index knows per episode: which episodes exist, and texts for the recap check."""

    def __init__(self, index: Index):
        self.index = index
        self.present = {e.episode for e in index.entries}
        self.missing = set(range(1, TOTAL_EPISODES + 1)) - self.present
        self.by_norm = defaultdict(list)
        for e in index.entries:
            if not e.parts:                     # single lines only; pair entries are for matching
                self.by_norm[e.norm].append(e)

    def recap_origin(self, episode: int, t_source: float, norm: str):
        """D-011: a line in the recap window of episode N+1 belongs to episode N.

        Returns (episode N, entry in N or None, suspected) or None when the line is not a recap line.
        """
        if not RECAP_WINDOW[0] <= t_source <= RECAP_WINDOW[1] or episode - 1 < 1:
            return None
        prev = episode - 1
        if prev in self.missing:
            return prev, None, True
        originals = [e for e in self.by_norm.get(norm, ()) if e.episode == prev]
        return (prev, originals[-1], False) if originals else None


def caption_links(index: Index, episodes: Episodes, rows: list[tuple[float, str]]) -> list[dict]:
    """match_sequence plus the recap rule; each result gains `track` and, when a recap copy was
    re-assigned, `recap` = {"from_episode", "from_t"}."""
    links = match_sequence(index, rows)
    for r in links:
        if r["status"] == LINKED:
            origin = episodes.recap_origin(r["episode"], r["t_source"], normalize(r["source_text"]))
            if origin:
                _assign_recap(r, *origin)
        elif r["status"] == AMBIGUOUS:
            _resolve_recap_pair(r, episodes)
        r["track"] = {LINKED: SOURCE, NO_MATCH: NON_SOURCE, AMBIGUOUS: UNCERTAIN}[r["status"]]
    return links


def _assign_recap(r: dict, episode: int, entry, suspected: bool) -> None:
    r["recap"] = {"from_episode": r["episode"], "from_t": r["t_source"]}
    r["episode"] = episode
    r["suspected"] = suspected
    if entry is not None:
        r["t_source"], r["t_source_end"], r["entry_id"] = entry.t_start, entry.t_end, entry.id
    else:
        r["t_source"] = r["t_source_end"] = r["entry_id"] = None


def _resolve_recap_pair(r: dict, episodes: Episodes) -> None:
    """AMBIGUOUS only between a line in N and its copy in N+1's recap -> LINKED to N (D-011)."""
    cands = r["candidates"]
    places = set()
    for c in cands:
        origin = episodes.recap_origin(c["episode"], c["t_start"], normalize(c["text"]))
        if origin and origin[1] is None:
            return                    # involves a missing episode: stays ambiguous
        places.add(origin[1].id if origin else c["entry_id"])
    if len(places) != 1:
        return
    entry_id = places.pop()
    original = next((c for c in cands if c["entry_id"] == entry_id), None)
    copy = next((c for c in cands if c["entry_id"] != entry_id), None)
    if original is None or copy is None:
        return
    r.update(status=LINKED, episode=original["episode"], t_source=original["t_start"], t_source_end=original["t_end"],
             sim=original["sim"], conf=round(original["sim"] * 0.85, 3), source_text=original["text"],
             entry_id=entry_id, flags=original["flags"], suspected=False,
             recap={"from_episode": copy["episode"], "from_t": copy["t_start"]})


def is_lyric(r: dict) -> bool:
    """Chanted / sung voice-over (textindex LYRIC): shown, never counted as episode evidence."""
    return "LYRIC" in (r.get("flags") or ())


def evidenced_episodes(links: list[dict]) -> list[dict]:
    """Episodes backed by >= 2 linked lines, or by one line with >= 6 characters and conf >= 0.8."""
    by_ep: dict[tuple[int, bool], list[dict]] = defaultdict(list)
    for r in links:
        if r["status"] == LINKED and not is_lyric(r):
            by_ep[(r["episode"], bool(r.get("suspected")))].append(r)
    out = []
    for (ep, suspected), rs in sorted(by_ep.items()):
        lines = {r["entry_id"] if r["entry_id"] is not None else normalize(r["source_text"]) for r in rs}
        strong = any(len(normalize(r["source_text"])) >= LONG_LINE and (r["conf"] or 0) >= LONG_CONF for r in rs)
        if len(lines) >= MIN_LINES or strong:
            ts = [r["t_source"] for r in rs if r["t_source"] is not None]
            out.append({"episode": ep, "suspected": suspected, "lines": len(lines),
                        "source_range": [min(ts), max(ts)] if ts else None,
                        "label": f"疑似第{ep}集（缺集，经第{ep + 1}集前情命中）" if suspected else f"EP{ep:02d}"})
    return out


def weak_leads(links: list[dict], evidence: list[dict]) -> list[dict]:
    """Weak evidence (D-015): an undecided line with a single candidate place, similarity >= 0.8 and
    >= 5 characters. Shown for the reviewer; never counted and never changes the verdict."""
    done = {e["episode"] for e in evidence}
    hits: dict[int, list[str]] = defaultdict(list)
    for r in links:
        if r["status"] != AMBIGUOUS or len(r["candidates"]) != 1 or len(normalize(r["text"])) < WEAK_CHARS:
            continue
        c = r["candidates"][0]
        if c["sim"] >= WEAK_SIM and c["episode"] not in done and "LYRIC" not in c.get("flags", ()):
            hits[c["episode"]].append(f"{r['text']}→{c['text']}（{c['sim']:.2f}）")
    return [{"episode": ep, "lines": len(v), "examples": v[:3]} for ep, v in sorted(hits.items())]


CLAIM_SPLIT = re.compile(r"[、,，;；/\s]+")
CLAIM_RANGE = re.compile(r"^(\d+)\s*[-－—~～至]\s*(\d+)$")


def parse_claim(raw: str | None) -> set[int] | None:
    """'13' / '12-14' / '9-14、25-32' -> episode set; None when unparseable (e.g. '25-22')."""
    if not raw or not str(raw).strip():
        return None
    eps: set[int] = set()
    for part in CLAIM_SPLIT.split(str(raw).strip()):
        if not part:
            continue
        m = CLAIM_RANGE.match(part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                return None
            eps.update(range(a, b + 1))
        elif part.isdigit():
            eps.add(int(part))
        else:
            return None
    return eps or None


def verdict(evidence: list[dict], claim: set[int] | None) -> str:
    found = {e["episode"] for e in evidence}
    if not found:
        return UNVERIFIED
    if claim is None:
        return CLAIM_UNPARSEABLE
    if found - claim:
        return CONTRADICTED
    return SUPPORTED if found == claim else NARROWER


def _eps_text(evidence: list[dict]) -> str:
    return "、".join(f"{e['label']}（{e['lines']} 句）" for e in evidence)


def unverified_reason(events: list[dict], claim: set[int] | None, missing: set[int]) -> str:
    """Why no episode could be evidenced, from what the captions look like."""
    if claim and claim <= missing:
        note = f"声称的集数本地缺失（{'、'.join(str(e) for e in sorted(claim))}），"
    else:
        note = ""
    if not events:
        return note + "未抽到画面文字（可能是纯 BGM 混剪、无字幕或字幕被遮挡）"
    foreign = [e for e in events if e.get("foreign")]
    if len(foreign) * 2 >= len(events):
        sample = max(foreign, key=lambda e: len(e["text"]))["text"][:30]
        return note + f"画面字幕主要是外文（如“{sample}”），无法与原剧中文台词比对"
    return note + f"有 {len(events)} 条画面文字，但没有能与原剧台词对上的（可能是 UP 主字幕、歌词或花字，或原字幕被遮挡、来自缺失的集）"


def statement(evidence: list[dict], claim_raw: str, claim: set[int] | None, result: str,
              weak: list[dict] = (), reason: str = "") -> str:
    """The reviewer-facing sentence; always about the captured part only."""
    tail = ("另有未达证据门槛的线索：" + "、".join(f"EP{w['episode']:02d}（{w['lines']} 句）" for w in weak) + "。") if weak else ""
    if result == UNVERIFIED:
        return f"已取证部分显示：{reason or '未见可与原剧对上的台词'}，无法用台词核验集数。" + tail
    head = f"已取证部分显示：台词对应 {_eps_text(evidence)}。"
    if result == CLAIM_UNPARSEABLE:
        return head + f"原告声称“{claim_raw}”无法解析，需人工核对。" + tail
    outside = sorted({e["episode"] for e in evidence} - claim)
    if result == CONTRADICTED:
        return head + f"原告声称 {claim_raw}，其中 {'、'.join(f'EP{e:02d}' for e in outside)} 不在声称范围内。" + tail
    if result == NARROWER:
        return head + f"均在原告声称 {claim_raw} 范围内，但声称范围更宽。" + tail
    return head + f"与原告声称 {claim_raw} 一致。" + tail
