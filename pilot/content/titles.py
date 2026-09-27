"""TITLE_EPISODE evidence (WO-003a-R1 §1-4): the episode number that the video itself states, in the
player's title bar or as an on-screen logo ("EP.31", "第31集", "31集"). Kept apart from the dialogue
evidence and never changes the verdict; it only gives UNVERIFIED rows a reference.

Only landscape player frames are read: a portrait detail page lists other episodes of the same series
(row 1053's ledger title says EP.28 while the player shows EP.7).
"""
from __future__ import annotations

import re
from collections import Counter

TOTAL_EPISODES = 38
MIN_FRAMES = 2            # a number must be on screen in at least two player frames
MINOR_SHARE = 0.25        # numbers seen in fewer frames than this share of the main one are session spill-over

# "EP.192/2" is EP.19 part 2/2 with the space lost, so digits running into "/" give up their last one
EP_RE = re.compile(r"(?i)EP\s*[.．。·:]?\s*(\d{1,3})(/)?")
DI_RE = re.compile(r"第\s*(\d{1,2})\s*集")
JI_RE = re.compile(r"(?<![\d全共\-~～至])(\d{1,2})\s*集(?![全完])")   # not "全38集", "38集全", "1-38集"
LOGO_CHARS = 12           # shorter texts are logos; longer ones are the title bar


def episode_numbers(text: str) -> list[int]:
    found = []
    for m in EP_RE.finditer(text):
        digits = m.group(1)
        if m.group(2):
            if len(digits) < 2:
                continue
            digits = digits[:-1]
        found.append(int(digits))
    for rx in (DI_RE, JI_RE):
        found += [int(m.group(1)) for m in rx.finditer(text)]
    return [n for n in dict.fromkeys(found) if 1 <= n <= TOTAL_EPISODES]


def title_episode(frames: dict[int, list[dict]], player_frames: list[int], observed_title: str | None,
                  claim: set[int] | None) -> dict | None:
    """{"episodes": [main, others...], "frames": {ep: n}, "sources", "examples", "ledger_title", "agrees"} or None.

    The first episode is the one on screen most often; the claim check ("agrees") uses it alone, the
    others (another video at the session edge) are only listed.
    """
    per_ep: Counter = Counter()
    sources: dict[int, Counter] = {}
    examples: dict[int, str] = {}
    for t in player_frames:
        seen = {}
        for o in frames.get(t, ()):
            for ep in episode_numbers(o["text"]):
                seen.setdefault(ep, "台标" if len(o["text"].strip()) <= LOGO_CHARS else "标题栏")
                examples.setdefault(ep, o["text"].strip()[:40])
        for ep, kind in seen.items():
            per_ep[ep] += 1
            sources.setdefault(ep, Counter())[kind] += 1
    ranked = per_ep.most_common()
    eps = [ep for ep, n in ranked if n >= max(MIN_FRAMES, MINOR_SHARE * ranked[0][1])] if ranked else []
    ledger = episode_numbers(observed_title or "")
    if not eps and not ledger:
        return None
    via_frames = bool(eps)
    eps = eps or ledger[:1]
    return {"episodes": eps,
            "frames": {ep: per_ep[ep] for ep in eps if per_ep[ep]},
            "sources": {ep: dict(sources[ep]) for ep in eps if ep in sources} if via_frames else {eps[0]: {"台账标题": 1}},
            "examples": {ep: examples[ep] for ep in eps if ep in examples} or {eps[0]: observed_title or ""},
            "ledger_title": ledger[0] if ledger else None,
            "agrees": None if claim is None else eps[0] in claim}


def describe(te: dict | None, claim_raw: str) -> str:
    """One sentence for the reviewer, e.g. "标题/台标标明第 31 集（29 帧），与原告声称 31 一致。"."""
    if not te:
        return ""
    main, others = te["episodes"][0], te["episodes"][1:]
    where = (f"{'、'.join(te['sources'][main])} {te['frames'][main]} 帧" if main in te["frames"] else "仅台账标题")
    if te["agrees"] is None:
        verdict = f"原告声称“{claim_raw}”无法解析"
    else:
        verdict = f"与原告声称 {claim_raw} {'一致' if te['agrees'] else '不一致'}"
    extra = "；会话中另见" + "、".join(f"第 {ep} 集（{te['frames'][ep]} 帧）" for ep in others) if others else ""
    return f"标题/台标标明第 {main} 集（{where}），{verdict}{extra}。"
