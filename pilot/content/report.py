"""Outputs (WO-003a §3.4): <row_id>.json, content_summary.csv, run_summary.json, and copies of the
ledger's review pages with a "字幕与集数" block in each row (the ledger itself is not modified)."""
from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from pathlib import Path

from . import evidence as ev

CSV_FIELDS = ["row_id", "video", "claim", "claim_status", "verdict", "evidenced_episodes", "weak_episodes",
              "source_lines", "non_source_lines", "ambiguous_lines", "events", "statement"]


def _eps(items: list[dict]) -> str:
    return "; ".join(f"{e.get('label', f'EP{e['episode']:02d}')}({e['lines']})" for e in items)


def write_rows(results: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        (out_dir / f"{r['row_id']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out_dir / "content_summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in results:
            c = r["counts"]
            w.writerow({"row_id": r["row_id"], "video": r["video"], "claim": r["claim"]["raw"],
                        "claim_status": r["claim"]["status"], "verdict": r["verdict"],
                        "evidenced_episodes": _eps(r["evidenced_episodes"]), "weak_episodes": _eps(r["weak_episodes"]),
                        "source_lines": c.get("source_lines", 0), "non_source_lines": c.get("non_source_lines", 0),
                        "ambiguous_lines": c.get("ambiguous_lines", 0), "events": c.get("events", 0),
                        "statement": r["statement"]})


def summary(results: list[dict], meta: dict) -> dict:
    events = sum(r["counts"].get("events", 0) for r in results)
    source = sum(r["counts"].get("source_lines", 0) for r in results)
    cats = Counter(x.get("category") for r in results for x in r["events"] if x["status"] == ev.NO_MATCH)
    dropped = Counter()
    for r in results:
        dropped.update(r["dropped"])
    return {**meta, "rows": len(results), "verdicts": dict(Counter(r["verdict"] for r in results)),
            "events": events, "source_lines": source,
            "source_share": round(source / events, 4) if events else 0.0,
            "non_source_lines": sum(r["counts"].get("non_source_lines", 0) for r in results),
            "ambiguous_lines": sum(r["counts"].get("ambiguous_lines", 0) for r in results),
            "non_source_categories": dict(cats), "dropped_items": dict(dropped)}


STYLE = """<style>.captions{border-top:1px dashed #c9d3da;margin-top:14px;padding-top:10px}
.captions h3{font-size:16px;margin:0 0 6px}.verdict{font-size:12px;padding:1px 7px;border-radius:9px;color:#fff;background:#627082}
.v-SUPPORTED{background:#2e7d4f}.v-NARROWER{background:#3b83a4}.v-CONTRADICTED{background:#b3432b}.v-CLAIM_UNPARSEABLE{background:#8a6d1d}
.caplist{list-style:none;padding:0;margin:6px 0;font-size:13px}.caplist li{margin:2px 0;padding:2px 6px;border-left:4px solid #c9d3da}
.caplist .src{border-color:#2f6fad;background:#eef4fb}.caplist .up{border-color:#d9822b;background:#fdf3e8}
.caplist .amb{border-color:#9aa5b1;background:#f3f5f7}.caplist summary{cursor:pointer}.caplist .d{color:#627082;padding:2px 0 2px 16px}
.legend span{margin-right:12px}</style>"""


def _t(v) -> str:
    return "" if v is None else f"{v:.1f}"


def block(r: dict, ledger_name: str) -> str:
    c = r["counts"]
    eps = "、".join(e["label"] for e in r["evidenced_episodes"]) or "无"
    items = []
    for x in r["events"]:
        where = f"录像 {_t(x['rec_start'])}–{_t(x['rec_end'])} s · 被诉视频 {'约' if x['content_estimated'] else ''}{_t(x['content_start'])} s"
        if x["status"] == ev.LINKED:
            cls = "src"
            src_t = _t(x["t_source"]) + " s" if x["t_source"] is not None else "（缺集，无原剧秒）"
            label = f"EP{x['episode']:02d}{'（疑似）' if x.get('suspected') else ''}"
            detail = (f"{where} · 原剧 {label} {src_t} · 原剧字幕“{x['source_text']}” · 相似度 {x['sim']:.2f} · 置信度 {x['conf']:.2f}"
                      + (f" · 前情命中 EP{x['recap']['from_episode']:02d} {_t(x['recap']['from_t'])} s，按 D-011 记为 {label}" if x.get("recap") else ""))
        elif x["status"] == ev.AMBIGUOUS:
            cls = "amb"
            cands = "；".join(f"EP{k['episode']:02d} {_t(k['t_start'])} s（{k['sim']:.2f}）" for k in x.get("candidates", [])[:5])
            detail = f"{where} · 待定，候选：{cands}" + (f" · {x['demoted']['reason']}" if x.get("demoted") else "")
        else:
            cls = "up"
            detail = f"{where} · {x.get('category', '')}" + ("（外文）" if x.get("foreign") else "") + \
                     (f" · {x['demoted']['reason']}（原匹配 EP{x['demoted']['episode'] or 0:02d}）" if x.get("demoted") else "")
        items.append(f'<li class="{cls}"><details><summary>{html.escape(x["text"])}</summary>'
                     f'<div class="d">{html.escape(detail)}</div></details></li>')
    listing = f'<details><summary>逐句（{c.get("events", 0)} 条，点开看录像秒和原剧秒）</summary><ol class="caplist">{"".join(items)}</ol></details>' if items else ""
    return (f'<section class="captions"><h3>字幕与集数 <span class="verdict v-{r["verdict"]}">{r["verdict"]}</span></h3>'
            f'<p>{html.escape(r["statement"])}</p>'
            f'<p class="muted">原告声称 {html.escape(r["claim"]["raw"] or "（空）")} · 有证据的集数 {html.escape(eps)} · '
            f'原剧台词 {c.get("source_lines", 0)} 句 · 非原剧文字 {c.get("non_source_lines", 0)} 句 · 待定 {c.get("ambiguous_lines", 0)} 句'
            f' · 台账 {html.escape(ledger_name)}</p>{listing}</section>')


def write_pages(results: list[dict], case_dir: Path, out_dir: Path, ledger_name: str) -> list[str]:
    """Copy each video's review page into out_dir with a caption block appended to every row."""
    by_video: dict[str, list[dict]] = {}
    for r in results:
        if r["video"]:
            by_video.setdefault(r["video"], []).append(r)
    written = []
    for video, rows in sorted(by_video.items()):
        page = case_dir / f"{video}.html"
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        text = text.replace("<head>", '<head><base href="../">' + STYLE, 1)
        legend = ('<p class="muted legend">“字幕与集数”区块由 WO-003a 追加（pilot.content，台账 '
                  f'{html.escape(ledger_name)}）：<span style="color:#2f6fad">■ 原剧台词</span>'
                  '<span style="color:#d9822b">■ 非原剧文字（UP 主字幕、歌词、花字等）</span>'
                  '<span style="color:#9aa5b1">■ 待定</span></p>')
        text = text.replace("</h1>", "</h1>" + legend, 1)
        for r in rows:
            m = re.search(rf'<article id="r{re.escape(r["row_id"])}"', text)
            if not m:
                continue
            end = text.find("</article>", m.end())
            if end < 0:
                continue
            text = text[:end] + block(r, ledger_name) + text[end:]
        (out_dir / f"{video}.html").write_text(text, encoding="utf-8")
        written.append(f"{video}.html")
    return written
