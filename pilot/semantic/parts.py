"""分P and repeated captures (WO-004b-R2 §1.1, 要求"合集与分P""多次取证的情况").

A title ends with the part it shows: "1/2", "-4" ("七时16-4": episode 16, part 4), "（3）", "P2". The ledger's
part list (the part panel read on the page) gives the total when its items share the title's prefix
("16-1" … "16-4"); otherwise the total is inferred from the rows of the same BV in this batch (yellow).

Only the same BV *and* the same part is a repeated capture. Two captures of one BV without any part mark
and with the same title are one single-part video captured twice. When only one side shows a part, the
part is unknown: both rows are compared normally and flagged.
"""
from __future__ import annotations

import re
from collections import defaultdict

from pilot.textindex.text import normalize

SLASH = re.compile(r"(?<![\d/.])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")
DASH = re.compile(r"(?:(?<!\d)(\d{1,3})\s*)?[-－—]\s*(\d{1,2})\s*$")
PAREN = re.compile(r"[（(]\s*(\d{1,2})\s*[)）]\s*$")
PNUM = re.compile(r"(?<![A-Za-z])[Pp]\s*(\d{1,2})(?!\d)")
MAX_PART = 20


def parse_part(title: str | None, ledger_parts: list | None = ()) -> dict | None:
    """{"n", "m" (total or None), "source"} or None when the title and the ledger show no part."""
    t = (title or "").strip()
    listed = [str(x).strip() for x in ledger_parts or ()]
    found = [(int(a), int(b)) for a, b in SLASH.findall(t) if 1 <= int(a) <= int(b) <= MAX_PART and int(b) > 1]
    if found:
        return {"n": found[-1][0], "m": found[-1][1], "source": "标题“n/m”"}
    if (m := DASH.search(t)) and 1 <= int(m.group(2)) <= MAX_PART:
        prefix, n = m.group(1), int(m.group(2))
        same = {int(x.split("-")[-1]) for x in listed if prefix and re.fullmatch(rf"0*{int(prefix)}\s*-\s*\d{{1,2}}", x)}
        return {"n": n, "m": max(same | {n}) if same else None,
                "source": "标题“-N”" + ("，总P数取台账分P列表" if same else "")}
    if (m := PAREN.search(t)) and 1 <= int(m.group(1)) <= MAX_PART:
        return {"n": int(m.group(1)), "m": None, "source": "标题“（N）”"}
    if (m := PNUM.search(t)) and 1 <= int(m.group(1)) <= MAX_PART:
        return {"n": int(m.group(1)), "m": None, "source": "标题“PN”"}
    ps = {int(m.group(1)) for x in listed if (m := re.fullmatch(r"[Pp]\s*(\d{1,2})", x))}
    if len(ps) == 1:
        return {"n": ps.pop(), "m": None, "source": "台账读到的分P"}
    return None


def bv_groups(rows: dict[str, dict]) -> dict[str, list[str]]:
    """{row: rows sharing its BV} from the ledger's duplicate list and the plaintiff's BV column."""
    by_bv = defaultdict(set)
    for rid, r in rows.items():
        bv = (r.get("plaintiff") or {}).get("bv") or r.get("observed_bv")
        if bv:
            by_bv[bv].add(rid)
    out = {}
    for rid, r in rows.items():
        bv = (r.get("plaintiff") or {}).get("bv") or r.get("observed_bv")
        group = set(by_bv.get(bv, ())) | {x for x in r.get("duplicate_bv_rows") or () if x in rows} | {rid}
        out[rid] = sorted(group, key=int)
    return out


def analyse(rows: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """({row: part info for the V/W columns}, {duplicate row: main row}). The main row of a repeat is the
    more complete capture."""
    groups = bv_groups(rows)
    parsed = {rid: parse_part((r.get("plaintiff") or {}).get("title") or r.get("observed_title"), r.get("parts"))
              for rid, r in rows.items()}
    info, repeats = {}, {}
    for rid, group in groups.items():
        p = parsed[rid]
        others = [g for g in group if g != rid]
        flags = []
        total = max((parsed[g]["m"] for g in group if parsed[g] and parsed[g]["m"]), default=None)
        if p and not total and others and any(parsed[g] for g in others):
            total = max(parsed[g]["n"] for g in group if parsed[g])
            flags.append("总P数按本批同 BV 的行推断，可能不全")
        if others and not p and any(parsed[g] for g in others):
            flags.append("同 BV 的其他行有分P标记，本行未识别")
        same_video = bool(others) and not p and not any(parsed[g] for g in others) \
            and all(_same_title(rows[rid], rows[g]) for g in others)       # one single-part video captured again
        info[rid] = {"part": p, "total": total if p else None, "group": group, "flags": flags, "same_video": same_video}
    for rid, group in groups.items():
        for other in group:
            if int(other) <= int(rid):
                continue
            a, b = parsed[rid], parsed[other]
            same_part = (a and b and a["n"] == b["n"]) or (not a and not b and _same_title(rows[rid], rows[other]))
            if not same_part:
                continue
            main, dup = (rid, other) if _fullness(rows[rid]) >= _fullness(rows[other]) else (other, rid)
            repeats[dup] = main
    return info, repeats


def _same_title(a: dict, b: dict) -> bool:
    ta = normalize((a.get("plaintiff") or {}).get("title") or "")
    return bool(ta) and ta == normalize((b.get("plaintiff") or {}).get("title") or "")


def _fullness(r: dict) -> tuple:
    seconds = r.get("capture_seconds")
    return (r.get("complete") is True, seconds if isinstance(seconds, (int, float)) else -1)
