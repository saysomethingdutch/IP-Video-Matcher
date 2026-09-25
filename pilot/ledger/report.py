"""Write CSV, JSONL, and static per-video review pages."""
from __future__ import annotations

import csv
from html import escape
import json
from pathlib import Path


def _clock(t):
    if t is None:
        return "—"
    t = max(0, round(t))
    return f"{t//3600:02d}:{t//60%60:02d}:{t%60:02d}"


def _reading_row(reading, source_frames):
    frame = reading["frame"]
    image = source_frames.get(frame)
    link = f'<a href="{escape(image)}">图</a>' if image else "—"
    return (f'<tr><td>{_clock(reading["t"])}</td>'
            f'<td>{escape(reading["raw"])}</td><td>{reading["speed"] or "—"}</td>'
            f'<td>{escape(frame)}</td><td>{link}</td></tr>')


def _csv(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_r2_changes(output, ledger):
    baseline = output / "ledger_r1_baseline.jsonl"
    if not baseline.exists():
        return 0
    previous = {str(row["row_id"]): row for row in
                (json.loads(line) for line in baseline.read_text().splitlines())}
    changes = []
    for row in ledger:
        old = previous.get(str(row["row_id"]))
        if not old:
            continue
        before, after = old.get("capture_seconds"), row.get("capture_seconds")
        if not isinstance(after, (float, int)) or (isinstance(before, (float, int))
                                                   and abs(after - before) <= 10):
            continue
        reasons = []
        if any(reading.get("truncated_total") for reading in row["readings"]):
            reasons.append("采用截断总长读数")
        if old.get("speed") != row.get("speed"):
            reasons.append("倍速复核")
        if old.get("session_id") != row.get("session_id"):
            reasons.append("会话关联或边界更新")
        if old.get("seeks") != row.get("seeks"):
            reasons.append("跳播前播放区间重算")
        if row.get("unsupported_gaps"):
            reasons.append("缺播放证据区间不计时")
        changes.append({"总编号": row["row_id"], "录像": row["video"],
                        "R1取证时长(秒)": before if isinstance(before, (float, int)) else "未知",
                        "R2取证时长(秒)": after,
                        "变化(秒)": round(after - before, 1) if isinstance(before, (float, int)) else "新增",
                        "原因": "；".join(reasons) or "有效读数及连续播放区间重算"})
    _csv(output / "r2_changes.csv", changes,
         ["总编号", "录像", "R1取证时长(秒)", "R2取证时长(秒)", "变化(秒)", "原因"])
    return len(changes)


def write_outputs(output, ledger, unmatched, videos, durations, elapsed, frames, case_id, fps, audit=None):
    audit = audit or {}
    output.mkdir(parents=True, exist_ok=True)
    with (output / "ledger.jsonl").open("w") as file:
        for row in ledger:
            exported = dict(row)
            if "无播放器读数，需复核" in row["flags"]:
                exported.update(capture_seconds="未知", complete="未知", seeks="未知", speed="未知")
            file.write(json.dumps(exported, ensure_ascii=False) + "\n")
    flat = []
    for item in ledger:
        row = item["plaintiff"]
        flat.append({"总编号": item["row_id"], "录像": item["video"],
                     "会话": item["session_id"] or "", "录像开始": _clock(item["video_start"]),
                     "录像结束": _clock(item["video_end"]), "BV号": row["bv"],
                     "标题": row["title"], "视频时长(秒)": item["video_duration"],
                     "取证时长(秒)": item["capture_seconds"] if item["capture_seconds"] is not None else "未知",
                     "是否完整播放": "未知" if item["complete"] is None else "是" if item["complete"] else "否",
                     "跳播区间": ("未知" if "无播放器读数，需复核" in item["flags"] else
                                  "; ".join(f"{_clock(s['from'])}→{_clock(s['to'])}" for s in item["seeks"])),
                     "最大播放位置": _clock(item["max_position"]), "倍速": item["speed"] if item["speed"] is not None else "未知",
                     "倍速依据": item.get("speed_reason") or "",
                     "读数解析失败占比": item.get("reading_parse_failure_ratio") if item.get("reading_parse_failure_ratio") is not None else "未知",
                     "播放量": item["views"] or "", "分P/合集": "; ".join(item["parts"]),
                     "发布日期": item["published"] or "", "截图": item["screenshot"]["path"] if item["screenshot"] else "",
                     "关联依据": item["association"]["basis"], "置信度": item["association"]["confidence"],
                     "同一会话的表格行": ",".join(item["same_session_rows"]),
                     "同BV行会话关系": item["same_session_status"],
                     "问题标记": "; ".join(item["flags"])})
    _csv(output / "ledger.csv", flat, list(flat[0]) if flat else [])
    _csv(output / "unmatched_sessions.csv", [
        {"会话": s["id"], "录像": s["video"], "开始": _clock(s["start"]),
         "结束": _clock(s["end"]), "播放器总长(秒)": s["total"],
         "BV候选": ";".join(s["bvs"])} for s in unmatched],
         ["会话", "录像", "开始", "结束", "播放器总长(秒)", "BV候选"])
    _csv(output / "rows_without_session.csv", [
        {"总编号": item["row_id"], "录像": item["video"], "BV号": item["plaintiff"]["bv"],
         "标题": item["plaintiff"]["title"]} for item in ledger if not item["session_id"]],
         ["总编号", "录像", "BV号", "标题"])
    unique_sessions = {}
    for item in ledger:
        if item["session_id"]:
            unique_sessions[item["session_id"]] = item
    summary = {"case_id": case_id, "elapsed_seconds": round(elapsed, 1),
               "sample_fps": fps, "frame_time_error_max_seconds": 0.5 / fps,
               "frames": frames, "rows": len(ledger),
               "linked_rows": sum(bool(item["session_id"]) for item in ledger),
               "association_rate": round(sum(bool(item["session_id"]) for item in ledger) / len(ledger), 4) if ledger else 0,
               "seeks": sum(len(item["seeks"]) for item in unique_sessions.values()),
               "unmatched_sessions": len(unmatched), **audit,
               "flags": {flag: sum(flag in row["flags"] for row in ledger)
                         for flag in sorted({flag for row in ledger for flag in row["flags"]})}}
    (output / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    for video in videos:
        write_page(output / f"{video}.html", video, [r for r in ledger if r["video"] == video],
                   durations[video], fps, audit)
    return summary


CSS = """body{font:15px/1.5 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;color:#243044;background:#f5f7f8;margin:0 auto;max-width:1200px;padding:24px}h1{margin:0 0 8px}h2{font-size:20px;margin:28px 0 12px}.muted{color:#627082}.timeline{height:26px;background:#dce3e8;border-radius:6px;position:relative;overflow:hidden}.seg{position:absolute;top:0;height:26px;background:#3b83a4;opacity:.85;border-right:2px solid white}.seg:hover{background:#e08a46}.axis{display:flex;justify-content:space-between;color:#627082;font-size:12px}article{background:white;border:1px solid #d9e2e7;border-radius:10px;margin:16px 0;padding:18px}article h2{margin:0 0 10px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 20px}.flag{color:#a14022;font-weight:600}.images{display:flex;gap:8px;overflow-x:auto;margin:12px 0}.images figure{margin:0;flex:none;width:210px}.images img{width:210px;border-radius:5px}.images figcaption{font-size:12px;color:#627082}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #e3e8eb;text-align:left;padding:4px 8px}.readings{max-height:260px;overflow:auto}a{color:#116487}details{margin-top:10px}code{font-size:12px} .empty{opacity:.6}@media(max-width:700px){.grid{grid-template-columns:1fr}}"""


def write_page(path, video, rows, duration, fps, audit=None):
    audit = audit or {}
    bars = []
    seen = set()
    for row in rows:
        if not row["session_id"] or row["session_id"] in seen:
            continue
        seen.add(row["session_id"])
        left = max(0, row["video_start"] / duration * 100)
        width = max(0.2, (row["video_end"] - row["video_start"]) / duration * 100)
        bars.append(f'<a class="seg" href="#r{escape(row["row_id"])}" title="{escape(row["row_id"])} {_clock(row["video_start"])}–{_clock(row["video_end"])}" style="left:{left:.3f}%;width:{width:.3f}%"></a>')
    cards = []
    for row in rows:
        ident = escape(row["row_id"])
        title = escape(row["plaintiff"]["title"])
        fields = [
            ("录像位置", f"{_clock(row['video_start'])}–{_clock(row['video_end'])}"),
            ("视频时长", _clock(row["video_duration"])),
            ("取证时长", "未知" if row["capture_seconds"] is None else _clock(row["capture_seconds"])),
            ("完整播放", "未知" if row["complete"] is None else "是" if row["complete"] else "否"),
            ("倍速", str(row["speed"] if row["speed"] is not None else "未知")),
            ("读数解析失败占比", str(row.get("reading_parse_failure_ratio") if row.get("reading_parse_failure_ratio") is not None else "未知")),
            ("最大播放位置", _clock(row["max_position"])),
            ("关联", f"{escape(row['association']['basis'])} / {row['association']['confidence']}"),
            ("播放量", escape(str(row["views"] or "—"))),
            ("分P/合集", escape("; ".join(row["parts"]) or "—")),
        ]
        grid = "".join(f"<div><b>{k}</b>　{v}</div>" for k, v in fields)
        images = "".join(f'<figure><img loading="lazy" src="{escape(e["path"])}"><figcaption>{escape(e["label"])} {_clock(e["t"])}</figcaption></figure>' for e in row["evidence"])
        screenshot = row.get("screenshot")
        if screenshot:
            images += f'<figure><a href="{escape(screenshot["path"])}"><img loading="lazy" src="{escape(screenshot["evidence_frame"])}"></a><figcaption>原生截图 · 手机时间 {_clock(screenshot["wall_time"])} / {escape(";".join(screenshot["bvs"]))}</figcaption></figure>'
        seeks = ("未知" if "无播放器读数，需复核" in row["flags"] else
                 "; ".join(f"{_clock(s['from'])}→{_clock(s['to'])} @ {_clock(s['at'])}" for s in row["seeks"]) or "无")
        flags = "; ".join(row["flags"]) or "无"
        source_frames = {s["frame"]: s.get("evidence_frame") for s in row.get("sources", {}).get("capture_seconds", [])}
        reading_table = "".join(_reading_row(r, source_frames) for r in row.get("readings", []))
        cards.append(f'<article id="r{ident}"><h2>#{ident} {title}</h2><p class="muted">BV {escape(row["plaintiff"]["bv"])} · 会话 {escape(str(row["session_id"] or "—"))} · 同BV行 {escape(", ".join(row["duplicate_bv_rows"])) or "—"}（{escape(row["same_session_status"])}）</p><div class="grid">{grid}</div><p><b>跳播</b> {escape(seeks)}　<b>问题</b> <span class="flag">{escape(flags)}</span></p><p>{escape(row.get("speed_reason") or "")}</p><div class="images">{images}</div><details><summary>完整读数表（{len(row.get("readings", []))} 条）</summary><div class="readings"><table><tr><th>录像时间</th><th>播放器读数</th><th>倍速</th><th>帧</th><th>证据图</th></tr>{reading_table}</table></div></details></article>')
    audit_line = (f'截断审计 {audit.get("truncation_audit_rows", "—")} 行 · '
                  f'倍速未知 {audit.get("speed_unknown_rows", "—")} 行（另有未关联 '
                  f'{audit.get("unlinked_rows_without_speed", "—")} 行） · '
                  f'取证时长不足 5 秒 {audit.get("capture_seconds_lt5_rows", "—")} 行 · '
                  f'会话重叠 {audit.get("overlapping_session_pairs", "—")} 对')
    html = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(video)} 取证台账</title><style>{CSS}</style></head><body><h1>{escape(video)} 取证台账</h1><p class="muted">{len(rows)} 行 · 1/{fps} 秒抽帧 · 帧时间误差 ≤ {0.5/fps:g} 秒；推定区间和低置信关联须人工核对。</p><p class="muted">{audit_line}</p><div class="timeline">{"".join(bars)}</div><div class="axis"><span>00:00:00</span><span>{_clock(duration)}</span></div>{"".join(cards)}</body></html>'
    path.write_text(html)
