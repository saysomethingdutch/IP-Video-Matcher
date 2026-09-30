"""WO-102 §4: the 30-row check list from 张亚晨's filled rows, and the scoring script (not run in WO-102).

The list is drawn from input columns only — title, uploader, dates, BV, evidence link. Whether a row is
"filled" is read from Y as empty / not empty and nothing else; no human conclusion is looked at. Strata are
a rough guess from the title so that reactions, commentaries, mixes, bare episode titles and the rest are all
covered; at most two rows per uploader, and no two rows of one BV.

Scoring reads the human columns, so it refuses to run without --owner-approved. Where the colleague's filling
is known to be simplified (R all 是, S all 无, X all 否, AB all 无; W mostly 是 but no 偷渡 in Y — WO-102 §4),
the guidance wins: those columns are listed, not scored against the human.
"""
from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

STRATA = [("reaction", r"reaction|react|反应"),
          ("解说", r"解说|解析|讲解|分析|盘点|科普|等级|战力|设定|介绍"),
          ("混剪", r"混剪|踩点|卡点|燃|剪辑|MAD|AMV|名场面|高能"),
          ("集数标题", r"吞噬星空\s*\d{1,3}|第\s*\d{1,3}\s*集|\d{2,3}\s*集"),
          ("剧场版或其他形式", r"剧场版|血洛|原始星|PV|预告|小说|有声|漫画|手游|黎明"),
          ("其他", r".")]
SCORED = ["Q", "T", "U", "V", "Y", "Z"]
GUIDANCE_OVER_HUMAN = ["R", "S", "X", "AB", "W"]
LIST_FIELDS = ["序号", "标题", "BV", "UP主", "发布时间", "取证完成时间", "取证视频链接", "层"]


def stratum(title: str) -> str:
    return next(name for name, pat in STRATA if re.search(pat, title or "", re.I))


def bv_of(url: str) -> str:
    m = re.search(r"BV[0-9A-Za-z]{10}", url or "")
    return m.group(0) if m else ""


def filled_rows(workbook: Path, sheet: str, filled_column: str) -> list[dict]:
    """Input columns of the rows whose filled_column is not empty (its content is not kept)."""
    ws = openpyxl.load_workbook(workbook, read_only=True, data_only=True)[sheet]
    fi = openpyxl.utils.column_index_from_string(filled_column) - 1
    idx = {c: openpyxl.utils.column_index_from_string(c) - 1 for c in ("B", "C", "E", "G", "J", "K", "M")}
    out = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if values[idx["B"]] is None or values[fi] in (None, ""):
            continue
        out.append({"序号": str(values[idx["B"]]), "标题": str(values[idx["C"]] or ""), "BV": bv_of(str(values[idx["E"]] or "")),
                    "UP主": str(values[idx["G"]] or ""), "发布时间": str(values[idx["J"]] or ""),
                    "取证完成时间": str(values[idx["K"]] or ""), "取证视频链接": str(values[idx["M"]] or "")})
    return out


def draw(rows: list[dict], size: int = 30, seed: int = 20260927, per_uploader: int = 2) -> list[dict]:
    """Stratified by the title guess: an equal share per stratum, topped up from the others when one is short."""
    rnd = random.Random(seed)
    by = defaultdict(list)
    for r in rows:
        by[stratum(r["标题"])].append({**r, "层": stratum(r["标题"])})
    for pool in by.values():
        rnd.shuffle(pool)
    picked, ups, bvs = [], Counter(), set()

    def take(r) -> bool:
        if ups[r["UP主"]] >= per_uploader or (r["BV"] and r["BV"] in bvs) or r in picked:
            return False
        picked.append(r)
        ups[r["UP主"]] += 1
        bvs.add(r["BV"])
        return True

    share = size // len(STRATA)
    for name, _ in STRATA:
        n = 0
        for r in by.get(name, []):
            if n >= share:
                break
            n += take(r)
    rest = [r for name, _ in STRATA for r in by.get(name, [])]
    rnd.shuffle(rest)
    for r in rest:
        if len(picked) >= size:
            break
        take(r)
    return sorted(picked, key=lambda r: int(r["序号"]))


def write_list(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LIST_FIELDS)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- scoring (needs --owner-approved)

def norm(col: str, value) -> str:
    s = re.sub(r"\s", "", str(value or "")).replace("(", "（").replace(")", "）")
    if col == "Y":
        s = s.replace("二创（混剪类）", "二创（单IP混剪类）")
    if col == "T":
        s = s.replace("游戏", "手游").replace("手手游", "手游")
    return s


def human_values(workbook: Path, sheet: str, ids: set[str]) -> dict[str, dict]:
    ws = openpyxl.load_workbook(workbook, read_only=True, data_only=True)[sheet]
    L = openpyxl.utils.get_column_letter
    out = {}
    for values in ws.iter_rows(min_row=2, values_only=True):
        if values[1] is not None and str(values[1]) in ids:
            out[str(values[1])] = {L(i + 1): v for i, v in enumerate(values)}
    return out


def score(machine: dict[str, dict], human: dict[str, dict]) -> dict:
    """machine: {序号: {column: value}}; human: {序号: {column letter: value}}."""
    rows = sorted(set(machine) & set(human), key=int)
    report = {"rows": len(rows), "scored": {}, "guidance_over_human": {}, "tou_du": [], "AA": {}}
    for col in SCORED:
        agree = [r for r in rows if norm(col, machine[r].get(col)) == norm(col, human[r].get(col))]
        report["scored"][col] = {"agree": len(agree), "of": len(rows),
                                 "disagree": [{"row": r, "machine": machine[r].get(col), "human": human[r].get(col)}
                                              for r in rows if r not in agree]}
    for col in GUIDANCE_OVER_HUMAN:
        report["guidance_over_human"][col] = {
            "machine": dict(Counter(norm(col, machine[r].get(col)) for r in rows)),
            "human": dict(Counter(norm(col, human[r].get(col)) for r in rows)),
            "differ": [{"row": r, "machine": machine[r].get(col), "human": human[r].get(col)} for r in rows
                       if norm(col, machine[r].get(col)) != norm(col, human[r].get(col))]}
    report["tou_du"] = [{"row": r, "human_Y": human[r].get("Y")} for r in rows if norm("Y", machine[r].get("Y")) == "偷渡"
                        and norm("Y", human[r].get("Y")) != "偷渡"]
    report["AA"] = {"machine_all_unknown": all(norm("AA", machine[r].get("AA")) == "无法判断" for r in rows),
                    "human": dict(Counter(norm("AA", human[r].get("AA")) for r in rows))}
    return report


def markdown(report: dict) -> str:
    lines = [f"# 吞噬星空检验（{report['rows']} 行）", "", "## 计分列（与人工比）", "", "| 列 | 一致 |", "|---|---|"]
    lines += [f"| {c} | {v['agree']}/{v['of']} |" for c, v in report["scored"].items()]
    lines += ["", "## 以指引为准的列（人工填法有简化，不计分）", ""]
    for c, v in report["guidance_over_human"].items():
        lines.append(f"- **{c}**：机器 {v['machine']}；人工 {v['human']}；不同 {len(v['differ'])} 行")
    lines += ["", f"机器判偷渡、人工不是：{len(report['tou_du'])} 行；AA 机器一律无法判断，人工分布 {report['AA']['human']}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the list the Owner rules on (WO-102-R1 §2.3)

def evidence_frames(tl_dir: Path, tl: dict | None, col: str, limit: int = 4) -> list[str]:
    """Frame paths that show what the column rests on: traffic frames for X, captioned frames for U and V,
    other-form frames for T, and one frame per timeline segment for Q, W, Y and Z."""
    if not tl:
        return []
    frames = [f for f in tl["frames"] if not f.get("dup_of")]
    path = lambda f: str(tl_dir / "frames" / f"{f['t']:08.2f}.jpg")
    if col == "X":
        pick = [f for f in frames if f.get("traffic")]
    elif col in ("U", "V"):
        pick = [f for f in frames if f.get("captions") or f.get("text")]
    elif col == "T":
        pick = [f for f in frames if f.get("content") == "tsxk_other"]
    else:
        pick = []
        for sg in tl["segments"]:
            f = next((f for f in frames if f["t"] >= sg["start"] - 0.5 and f.get("content") == sg["content"]), None)
            if f and f not in pick:
                pick.append(f)
    if not pick:
        step = max(1, len(frames) // limit)
        pick = frames[::step]
    return [path(f) for f in pick[:limit]]


def disagreements(report: dict, machine_rows: dict[str, dict], timelines: dict[str, tuple[Path, dict | None]],
                  audio_paths: dict[str, Path], notes: dict[str, str]) -> list[dict]:
    """Every disagreement, scored or listed, with the machine's basis and its evidence (D-058, WO-102-R1 §2.3)."""
    out = []
    groups = [(c, v["disagree"], True) for c, v in report["scored"].items()] + \
             [(c, v["differ"], False) for c, v in report["guidance_over_human"].items()]
    for col, items, scored in groups:
        for d in items:
            rid = d["row"]
            fields = machine_rows[rid]["fields"]
            tl_dir, tl = timelines.get(rid, (None, None))
            entry = {"row": rid, "column": col, "scored": scored, "machine": d["machine"], "human": d["human"],
                     "basis": (fields.get(col) or {}).get("basis"),
                     "frames": evidence_frames(tl_dir, tl, col) if tl_dir and col not in ("R", "S", "AB") else [],
                     "audio": str(audio_paths[rid]) if col in ("U", "V", "X", "Y", "Z") and rid in audio_paths else None,
                     "note": notes.get(rid, "")}
            if col == "R":
                entry["note"] = "；".join(x for x in (entry["note"], "R 在返工（WO-101-R2），只列分歧不计分") if x)
            out.append(entry)
    return sorted(out, key=lambda e: (int(e["row"]), e["column"]))


def disagreements_md(items: list[dict]) -> str:
    lines = ["# 吞噬星空检验：分歧清单（交 Owner 逐条裁定）", "",
             "每条：机器值 / 人工值、机器依据、证据帧。裁定栏请填：机器对 / 人工对 / 都不对（写正确值）。", ""]
    for e in items:
        tag = "计分" if e["scored"] else "不计分，按指引列出"
        lines.append(f"## {e['row']} · {e['column']}（{tag}）")
        lines.append(f"- 机器：{e['machine']}　人工：{e['human']}")
        if e.get("basis"):
            lines.append(f"- 机器依据：{e['basis']}")
        if e.get("note"):
            lines.append(f"- 注意：{e['note']}")
        for f in e["frames"]:
            lines.append(f"- 证据帧：`{f}`")
        if e.get("audio"):
            lines.append(f"- 声音：`{e['audio']}`")
        lines += ["- 裁定：", ""]
    return "\n".join(lines)
