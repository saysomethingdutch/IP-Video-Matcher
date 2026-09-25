"""Style samples for the cached system prompt (WO-004c §4, D-007: learn the style, not the conclusion).

- The client's three examples, read from 《比对要求》 at run time.
- Two human opinions per human grid type from the table, rows outside this batch only. The rows were
  picked from the shortest of each type, keeping those whose first paragraph names a concrete point.
- The owner's later edits (corrections.jsonl, D-007); only the reading hook exists for now.

Only the two paragraphs the model writes are kept; the proportion paragraph is code's job.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pilot.semantic.table import Table

REQUIREMENTS = Path("/Volumes/Element/ip-video-matching-repo_de_material/七时吉祥/七时吉祥比对要求.docx")
BATCH = range(1051, 1220)
SAMPLE_ROWS = {"reaction": (851, 272), "混剪": (153, 1602), "夹叙夹议解说": (9, 2), "观点评析": (1591, 640)}
LABELS = {"purpose": r"【使用(?:的)?性质和目的】", "proportion": r"【(?:使用的比例|对原作品的引用比例)】",
          "substitution": r"【对原作品的替代可能性】"}


def paragraphs(text: str) -> dict[str, str]:
    """The three labelled paragraphs of an opinion, without their labels (missing ones are absent)."""
    marks = sorted((m.start(), m.end(), key) for key, rx in LABELS.items() for m in re.finditer(rx, text))
    out = {}
    for i, (_, end, key) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[end:stop].strip()
        body = re.split(r"\n\s*因此该视频构成", body)[0].strip()
        out[key] = body
    return out


def table_samples(table: Table) -> list[dict]:
    out = []
    for kind, rows in SAMPLE_ROWS.items():
        for no in rows:
            assert no not in BATCH, "samples never come from this batch"
            p = paragraphs(str(table.value(no, "AF") or ""))
            if p.get("purpose") and p.get("substitution"):
                out.append({"type": kind, "source": f"比对表人工意见（本批以外，序号 {no}）", **p})
    return out


def client_examples(path: Path = REQUIREMENTS) -> list[dict]:
    """观点评析类 / 混剪类 / Reaction类 examples from 《比对要求》 "4. 比对意见部分"."""
    if not path.exists():
        return []
    text = subprocess.run(["pandoc", "-t", "plain", str(path)], capture_output=True, text=True, check=True).stdout
    part = text.split("示例：", 1)[-1].split("常见错误分享", 1)[0]
    out = []
    for m in re.finditer(r"(\S+?)类示例\s*\n(.*?)(?=\n\S+?类示例|\Z)", part, flags=re.S):
        p = paragraphs(m.group(2))
        if p.get("purpose") and p.get("substitution"):
            out.append({"type": m.group(1), "source": "客户《比对要求》示例", **p})
    return out


def corrections(path: Path) -> list[dict]:
    """Owner-edited opinions from the review page (D-007): JSON lines with "row_id" and the opinion either
    as "AF" or as {"column": "AF", "value": …}."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        text = c.get("AF") or (c.get("value") if c.get("column") == "AF" else None)
        p = paragraphs(str(text or ""))
        if p.get("purpose") and p.get("substitution"):
            out.append({"type": c.get("type") or "所有者修改", "source": f"所有者修改（行 {c.get('row_id')}）", **p})
    return out
