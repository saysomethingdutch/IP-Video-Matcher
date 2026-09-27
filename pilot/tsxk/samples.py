"""Style examples for the AC prompt (WO-102 §3).

- The guidance's example table (18 rows): one row per category, with Y, U, V, Z and the AC text. Times and
  episode numbers are taken out first — code writes those, and the model imitates what it sees.
- A few of 张亚晨's filled rows, AC text only, for wording: never their column values (the check set's labels
  are known to be simplified), never a row on the check list or sharing its BV. Nothing here is printed.
"""
from __future__ import annotations

import csv
import random
import re
from pathlib import Path

import openpyxl

# 序号 of the guidance examples, one per category in order of preference. 11802 (师兄啊师兄) is left out: the
# owner said that case does not occur in this batch (D-056 ⑤).
GUIDE_PICKS = [("纯cut", 128), ("偷渡", 12429), ("二创（解说类）", 1001), ("二创（单IP混剪类）", 8),
               ("二创（reaction类）", 1829), ("二创（其他类）", 5228), ("引流", 12490), ("错权", 16047),
               ("二创（其他类）", 8799), ("二创（解说类）", 1107)]
L = openpyxl.utils.get_column_letter


def scrub(text: str) -> str:
    """Take out what code writes: lines with clock times, episode numbers, percentages."""
    lines, n = [], 0
    for line in str(text or "").splitlines():
        if re.search(r"\d{1,2}\s*[:：]\s*\d{2}", line):
            continue
        if line.lstrip().startswith("【"):
            n = 0
        m = re.match(r"^\s*\d+\s*[.、．]\s*", line)
        body = line[m.end():] if m else line
        body = re.sub(r"第[\d、,，\s]+集", "相关集数", body)
        body = re.sub(r"\d+\s*%(以上)?", "大部分", body)
        body = re.sub(r"\d", "", body)
        if m:
            n += 1
            body = f"{n}.{body}"
        lines.append(body)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def guidance_examples(path: Path, n: int = 8) -> list[dict]:
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = {}
    for values in ws.iter_rows(min_row=2, values_only=True):
        d = {L(i + 1): v for i, v in enumerate(values)}
        if d.get("B") is not None:
            rows[int(d["B"])] = d
    out = []
    for label, sid in GUIDE_PICKS:
        d = rows.get(sid)
        if d and len(out) < n:
            out.append({"title": scrub(d["C"]), "Y": d["Y"], "U": d["U"], "V": d["V"], "Z": d["Z"], "AC": scrub(d["AC"])})
    return out


def check_rows(list_path: Path) -> set[str]:
    if not list_path.exists():
        return set()
    with list_path.open(encoding="utf-8-sig") as f:
        return {r["序号"] for r in csv.DictReader(f)}


def colleague_examples(workbook: Path, sheet: str, filled_column: str, n: int, exclude: set[str],
                       seed: int = 20260927) -> list[str]:
    """AC texts of n filled rows (Y non-empty), for wording only; the check list is excluded."""
    if n <= 0:
        return []
    ws = openpyxl.load_workbook(workbook, read_only=True, data_only=True)[sheet]
    fi = openpyxl.utils.column_index_from_string(filled_column) - 1
    ac = openpyxl.utils.column_index_from_string("AC") - 1
    bv = openpyxl.utils.column_index_from_string("E") - 1
    pool, banned_bv = [], set()
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    for values in rows:
        if values[1] is not None and str(values[1]) in exclude:
            banned_bv.add(str(values[bv] or ""))
    for values in rows:
        if values[1] is None or str(values[1]) in exclude or str(values[bv] or "") in banned_bv:
            continue
        if values[fi] not in (None, "") and values[ac]:
            pool.append(scrub(values[ac]))
    random.Random(seed).shuffle(pool)
    return [t for t in pool if len(t) > 40][:n]
