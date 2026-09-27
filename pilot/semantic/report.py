"""Outputs (WO-004b §4): <row_id>.json, fields.csv (the table's own column names, 比对意见 left empty),
consistency.csv, run_summary.json and review-page copies with a "分类与四格" block."""
from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from pathlib import Path

from .table import OUTPUT_COLS, Table


def write_rows(results: list[dict], out_dir: Path, table: Table | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        (out_dir / f"{r['row_id']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    names = [table.header(c) if table else c for c in OUTPUT_COLS]
    with (out_dir / "fields.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["总编号", *names, "性质", "评论占比", "标记"])
        for r in results:
            cls = r["classification"] or {}
            w.writerow([r["row_id"], *[r["fields"][c]["value"] for c in OUTPUT_COLS], cls.get("nature", "重复取证"),
                        cls.get("comment_share", ""), "；".join(f"[{x['level']}] {x['reason']}" for x in r["flags"])])


def write_consistency(rows: list[dict], out_dir: Path) -> None:
    keys = ["uploader", "row_id", "nature", "grid", "group_rows", "majority_nature", "majority_grid", "majority_rows",
            "majority_examples", "shared_features"]
    with (out_dir / "consistency.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def summary(results: list[dict], warnings: list[dict], meta: dict) -> dict:
    natures = Counter((r["classification"] or {}).get("nature", "重复取证") for r in results)
    grids = Counter(" / ".join(r["fields"][c]["value"] for c in ("AG", "AH", "AI", "AJ")) for r in results)
    flags = Counter()
    for r in results:
        for f in r["flags"]:
            flags[f"[{f['level']}] " + re.sub(r"[\d.]+|（.*?）|“.*?”", "…", f["reason"])[:40]] += 1
    calls = [c for r in results for c in r["calls"]]
    return {**meta, "rows": len(results), "natures": dict(natures), "grids": dict(grids.most_common()),
            "flags": dict(flags.most_common()), "consistency_warnings": len(warnings),
            "calls": len(calls), "valid_json_rate": round(sum(c["valid_json"] for c in calls) / len(calls), 3) if calls else None,
            "model_seconds": round(sum(c["seconds"] for c in calls), 1),
            "tokens": {k: sum((c["usage"] or {}).get(k) or 0 for c in calls) for k in ("prompt", "completion", "reasoning")}}


STYLE = """<style>.semantic{border-top:1px dashed #c9d3da;margin-top:14px;padding-top:10px}.semantic h3{font-size:16px;margin:0 0 6px}
.semantic table{font-size:13px}.semantic .flag-red{color:#b3432b;font-weight:600}.semantic .flag-yellow{color:#8a6d1d}</style>"""


def block(r: dict, table: Table | None) -> str:
    cls = r["classification"] or {}
    rows = "".join(f"<tr><td>{html.escape(table.header(c) if table else c)}</td><td><b>{html.escape(str(r['fields'][c]['value']))}</b></td>"
                   f"<td>{html.escape(str(r['fields'][c]['basis']))[:300]}</td></tr>"
                   for c in OUTPUT_COLS if c != "AF")
    flags = "".join(f'<li class="flag-{f["level"]}">{html.escape(f["reason"])}</li>' for f in r["flags"])
    head = (f"性质：{cls.get('nature', '重复取证')}（模型：{cls.get('model_nature')}；评论占比 {cls.get('comment_share')}；"
            f"已标注 {cls.get('tagged_lines', 0)} 句）") if cls else "重复取证，不再比对"
    quotes = "；".join(html.escape(e.get("quote") or e.get("fact") or "") for e in cls.get("evidence", [])[:3]) if cls else ""
    return (f'<section class="semantic"><h3>分类与四格（WO-004b）</h3><p>{html.escape(head)}</p>'
            + (f'<p class="muted">证据：{quotes}</p>' if quotes else "")
            + f"<table><tr><th>列</th><th>取值</th><th>依据</th></tr>{rows}</table>"
            + (f"<ul>{flags}</ul>" if flags else "") + "</section>")


def write_pages(results: list[dict], case_dir: Path, out_dir: Path, table: Table | None) -> list[str]:
    by_video: dict[str, list[dict]] = {}
    for r in results:
        video = r["digest"]["ledger"]["video"]
        if video:
            by_video.setdefault(video, []).append(r)
    written = []
    for video, rs in sorted(by_video.items()):
        page = case_dir / f"{video}.html"
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8").replace("<head>", '<head><base href="../">' + STYLE, 1)
        for r in rs:
            m = re.search(rf'<article id="r{re.escape(r["row_id"])}"', text)
            end = text.find("</article>", m.end()) if m else -1
            if end >= 0:
                text = text[:end] + block(r, table) + text[end:]
        (out_dir / f"{video}.html").write_text(text, encoding="utf-8")
        written.append(f"{video}.html")
    return written
