"""The columns code decides (WO-102 §1, §3), from the web ledger (WO-101) and the content timeline.

Every value carries its basis; a value the guidance leaves open, or one that rests on a fallback, is flagged
yellow. What the model decides (Y when 吞噬星空 animation is shown, U, V, Z, and the AC wording) is left out.
"""
from __future__ import annotations

import re
from datetime import datetime

YES, NO = "是", "否"


def _f(value, by: str, basis: str) -> dict:
    return {"value": value, "by": by, "basis": basis}


def flag(level: str, reason: str) -> dict:
    return {"level": level, "reason": reason}


def related_terms(rules: dict) -> list[str]:
    w = rules["work"]
    return [w["title"], *w["characters"], *w["film_watermarks"], "吞噬", "罗峰"]


def is_related(text: str, rules: dict) -> bool:
    return any(t in (text or "") for t in related_terms(rules))


def _same(a, b) -> bool:
    norm = lambda s: re.sub(r"[\s　]", "", str(s or "")).lower()
    return bool(norm(a)) and norm(a) == norm(b)


def witness_time(value) -> str | None:
    """yyyy/m/d hh:mm:ss (指引 二（三） example "2026/8/12 10:46:06"; D-056 ④)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = re.sub(r"[年月/.]", "-", str(value).strip()).replace("日", " ").replace("时", ":").replace("分", ":").replace("秒", "")
        s = re.sub(r"\s+", " ", s).strip().rstrip(":")
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    return f"{dt.year}/{dt.month}/{dt.day} {dt:%H:%M:%S}"


def ledger_fields(inputs: dict, led: dict | None) -> tuple[dict, list[dict]]:
    """D, F, L, R: the web ledger's (WO-101); empty and flagged while it has not run."""
    if not led:
        return ({c: _f(None, "webledger", "台账（WO-101）尚未提供") for c in ("D", "F", "L", "R")},
                [flag("yellow", "D、F、L、R 待台账（WO-101）")])
    out, flags = {}, []
    title = led.get("title")
    same_title = led["title_same_as_input"] if isinstance(led.get("title_same_as_input"), bool) else _same(title, inputs.get("C"))
    out["D"] = _f(inputs.get("C") if same_title else title, "webledger",
                  "台账判定与 C 列一致，照抄" if same_title else "播放页标题与 C 列不同，填播放页的")
    addr = led.get("address")
    same_addr = led["address_same_as_input"] if isinstance(led.get("address_same_as_input"), bool) else _same(addr, inputs.get("E"))
    out["F"] = _f("未显示" if not addr else (inputs.get("E") if same_addr else addr), "webledger",
                  "地址栏未显示" if not addr else ("台账判定与 E 列一致，照抄" if same_addr else "地址与 E 列不同，填播放页的"))
    when = witness_time(led.get("finished_witness_time")) or witness_time(led.get("certificate_finished_time"))
    out["L"] = _f(when, "webledger", "证书上的取证完成时间（WO-101-R1）")
    if not when:
        flags.append(flag("yellow", "L：台账没有取证完成时间"))
    complete = led.get("complete")
    basis = "；".join(str(v) for k, v in led.items() if k.startswith("complete_") and v not in (None, "", [], {}))[:200]
    prog = led.get("progress")
    if isinstance(prog, list) and prog and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in prog):
        basis = (basis + "；" if basis else "") + f"进度条 {prog[0][1]:.1%} → {prog[-1][1]:.1%}（{len(prog)} 点，WO-101-R1）"
    out["R"] = _f({True: YES, False: NO}.get(complete), "webledger", basis or "台账按进度条、播放器读数和时长判断")
    if complete is None:
        flags.append(flag("yellow", "R：台账判不了是否完整播放"))
    return out, flags


def page_fields(led: dict | None, rules: dict) -> tuple[dict, list[str], list[dict]]:
    """S and AB from the page areas the recording shows; "无" + an AC note when an area is not shown (D-056 ④)."""
    led = led or {}
    notes, flags = [], []
    tags = led.get("tags") or {}
    if tags.get("visible"):
        s = _f(tags.get("activity") or "无", "webledger", "标签区可见：带小电视图标的话题标签" if tags.get("activity") else "标签区可见，没有活动标签")
    else:
        s = _f("无", "code", "录屏未显示标签区（D-056 ④）")
        notes.append("录屏未显示标签区")
    parts = []
    terms = related_terms(rules)
    if tags.get("visible"):
        hit = [t for t in tags.get("items") or [] if any(k in t for k in terms)]
        if hit:
            parts.append(f"标签（{'、'.join(hit)}）")
    intro = led.get("intro") or {}
    if intro.get("visible"):
        hit = [k for k in terms if k in (intro.get("text") or "")]
        if hit:
            parts.append(f"简介（{'、'.join(dict.fromkeys(hit))}）")
    else:
        notes.append("录屏未显示简介区")
    coll = led.get("collection") or {}
    if coll.get("visible") and is_related(coll.get("title"), rules):
        parts.append(f"合集（{coll.get('title')}）")
    ab = _f("、".join(parts) or "无", "webledger" if led else "code",
            "播放页标签、简介、合集里与吞噬星空有关的信息" if parts else ("看到的区域里没有相关信息" if led else "录屏未显示简介区、标签区（D-056 ④）"))
    if not led:
        flags.append(flag("yellow", "S、AB 按录屏未显示处理：台账（WO-101）尚未提供标签区、简介区"))
    return {"S": s, "AB": ab}, notes, flags


def timeline_fields(tl: dict | None, led: dict | None, title: str, rules: dict) -> tuple[dict, dict, list[dict]]:
    """Q, W, X, T (forms seen), and Y/Z when no 吞噬星空 animation is shown. Returns (fields, hints, flags)."""
    if not tl:
        return ({c: _f(None, "timeline", "时间线未生成") for c in ("Q", "W", "X")}, {"forms": [], "y_fixed": None},
                [flag("red", "没有内容时间线，Q、W、X、Y 无法判断")])
    s, flags = tl["summary"], []
    secs = s["seconds"]
    anime = s["has_tsxk"] or s["has_film"]
    out = {"Q": _f(NO if anime else YES, "timeline",
                   "时间线里有吞噬星空动画帧" + ("（含剧场版）" if s["has_film"] else "") if anime else "时间线里没有吞噬星空动画帧")}
    if not anime and s["unknown_frames"]:
        flags.append(flag("yellow", f"Q 填是，但有 {s['unknown_frames']} 帧画面未能判定"))
    if not anime and is_related(title, rules):
        flags.append(flag("yellow", "标题与吞噬星空相关，但画面里没有认出吞噬星空动画，请核对 Q、Y"))
    platforms = set(s["traffic_platforms"]) | {p for p in ((led or {}).get("comments") or {}).get("traffic") or ()}
    unrelated = secs.get("unrelated", 0) > 0
    out["W"] = _f(YES if unrelated or platforms else NO, "timeline",
                  "有无关内容或引流画面" if unrelated or platforms else "画面都来自吞噬星空（含 UP 主自制画面）")
    if s["forms"] and out["W"]["value"] == NO:
        flags.append(flag("yellow", f"含吞噬星空其他形式（{'、'.join(s['forms'])}），W 按“第三方无关内容”口径填否，请所有者确认"))
    if platforms:
        label = "、".join(x for x, k in (("腾讯", "tencent"), ("其他", "other")) if k in platforms)
        out["X"] = _f(f"是（{label}）", "timeline", "画面" + ("或评论区" if (led or {}).get("comments", {}).get("traffic") else "") + "里有引流信息")
    else:
        out["X"] = _f(NO, "timeline", "画面里没有引流信息" + ("" if ((led or {}).get("comments") or {}).get("visible") else "；录屏未显示评论区"))
    hints = {"forms": list(s["forms"]), "y_fixed": None, "z_default": None}
    if not s["has_tsxk"]:
        content = {k: v for k, v in secs.items() if k not in ("blank", "unknown")}
        other_secs = content.get("tsxk_other", 0)
        total = sum(content.values()) or 1
        if platforms:
            hints["y_fixed"], basis = "引流", "没有吞噬星空动画，画面里有引流信息"
            if not is_related(title, rules):
                flags.append(flag("yellow", "Y 判引流，但标题里没有吞噬星空相关字样"))
        elif other_secs and other_secs / total >= 0.8:
            hints["y_fixed"], basis = "错权", f"没有吞噬星空动画正片，画面是吞噬星空的其他形式（{'、'.join(s['forms'])}）"
        else:
            hints["y_fixed"], basis = "完全无关内容", "没有吞噬星空动画，也不是其他形式或引流"
            if other_secs:
                flags.append(flag("yellow", "有少量吞噬星空其他形式的画面，Y 仍判完全无关内容，请核对"))
        out["Y"] = _f(hints["y_fixed"], "code", basis)
        z = rules["columns"]["Z"]["code_defaults"].get(hints["y_fixed"])
        if z is None:
            z = {"完全无关内容": "高", "错权": "低"}[hints["y_fixed"]]
            flags.append(flag("yellow", f"Z：指引没有给{hints['y_fixed']}的标准，按示例填{z}，请所有者确认"))
        out["Z"] = _f(z, "code", "指引 Z 的示例" if hints["y_fixed"] == "引流" else "示例表同类行")
    return out, hints, flags


def episode_note(title: str) -> str | None:
    m = re.search(r"第\s*(\d{1,4})\s*集", title or "")
    return f"标题标注第{m.group(1)}集" if m else None


def time_lines(tl: dict | None, fields: dict, y: str | None) -> list[str]:
    """The lines code writes into AC【其他】: traffic moments and the content segments when there is traffic
    (指引 二（十一）); unrelated and other-form segments otherwise (偷渡, W, T)."""
    if not tl:
        return []
    lines = []
    segs = [s for s in tl["segments"] if s["content"] not in ("blank", "unknown")]
    if fields.get("X", {}).get("value", NO) != NO:
        lines += tl["ac"]["traffic"]
        if segs:
            from pilot.timeline.segments import ac_lines
            lines.append(ac_lines(segs, [])["content"])
    else:
        from pilot.timeline.cloud import mmss
        for s in segs:
            span = mmss(s["start"]) if s["start"] == s["end"] else f"{mmss(s['start'])}-{mmss(s['end'])}"
            if s["content"] == "unrelated":
                lines.append(f"{span}为无关内容")
            elif s["content"] == "tsxk_other":
                lines.append(f"{span}为《吞噬星空》{s['form'] or '其他形式'}内容")
    return lines
