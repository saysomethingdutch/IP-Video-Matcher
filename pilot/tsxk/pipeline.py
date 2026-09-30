"""One row → Q–AC (and D, F, L, R from the ledger): code first, the model for the rest, AC assembled."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import ac as A
from . import classify as K
from . import fields as FD
from . import samples as SM
from . import sound as SD
from .ac_filter import filter_ac
from .config import TsxkCase


def system_prompt(case: TsxkCase) -> tuple[str, dict]:
    """The system prompt and what went into it (counts only; the colleague texts are never printed)."""
    if (case.case_dir / "review/decisions.jsonl").exists():
        from .owner_feedback import owner_system
        return owner_system(case)
    # No colleague conclusions are read. Guidance is the fallback for new cases.
    g = case.raw["guidance"]
    guide = SM.guidance_examples(Path(g["dir"]) / g["examples"], 8)
    system = K.SYSTEM.format(standards=K.standards(case.rules()), examples=K.examples_text(guide, []))
    return system, {"guidance_examples": len(guide), "colleague_examples": 0, "colleague_skipped": True}


def span(a: float, b: float) -> str:
    from pilot.timeline.cloud import mmss
    return mmss(a) if round(a) == round(b) else f"{mmss(a)}-{mmss(b)}"


def text_evidence(tl: dict | None, audio: dict | None) -> dict[str, bool]:
    """Whether the captions, on-screen text or filtered speech inside the actual playback name the novel or an
    audiobook (WO-102-R2 A5)."""
    texts = [t for f in (tl or {}).get("frames", []) if not f.get("ignored") for t in (f.get("ocr") or [])]
    texts += SD.uploader_speech(audio)
    blob = " ".join(texts)
    return {"小说": bool(re.search(r"原著|小说|书中|有声书|第.{1,8}章", blob)), "有声书": bool(re.search(r"有声书|有声小说|听书", blob))}


def process_row(inputs: dict, led: dict | None, tl: dict | None, rules: dict, system: str, backend: str = "zhipu",
                audio: dict | None = None, *, fixed_y: str | None = None, fixed_u: str | None = None,
                disallow_y: tuple = ()) -> dict:
    title = (led or {}).get("title") or inputs.get("C") or ""
    f1, fl1 = FD.ledger_fields(inputs, led)
    f2, notes, fl2 = FD.page_fields(led, rules)
    f3, hints, fl3 = FD.timeline_fields(tl, led, title, rules)
    if fixed_y is not None:
        hints['y_fixed'] = fixed_y
        f3['Y'] = FD._f(fixed_y, 'code', 'D-078：保留默认类别或所有者采用备用版')
        if fixed_y != '完全无关内容':
            f3.pop('Z', None)
        else:
            f3['Z'] = FD._f('不涉及', 'code', '完全无关内容不涉及合理使用（D-077）')
    hints.update(u_fixed=fixed_u, disallow_y=disallow_y)
    fields = {**f1, **f2, **f3, "AA": FD._f("无法判断", "fixed", "所有者口径，一律填无法判断（D-056 ④）")}
    flags = fl1 + fl2 + fl3 + [{**f, "reason": "时间线：" + f["reason"]} for f in (tl or {}).get("flags", [])]
    fixed = {k: fields[k] for k in ("Q", "W", "X", "S", "AB", "AA") + (("Y",) if hints["y_fixed"] else ()) + (("Z",) if hints["y_fixed"] and 'Z' in fields else ())}
    res = K.classify(inputs, led, tl, fixed, hints, system, backend, audio) if tl else \
        {"data": None, "errors": ["没有时间线"], "calls": [], "prompt": "", "music": SD.music_evidence(None, None), "speech": 0}
    data = res["data"] or {}
    weak_high = [e for e in res["errors"] if e.startswith("Z：")] if data.get("Z") == "高" and (not hints["y_fixed"] or fixed_y is not None) else []
    if [e for e in res["errors"] if e not in weak_high]:          # a 高 on a wrong basis is lowered below, not red
        flags.append(FD.flag("red", "分类：" + "；".join(e for e in res["errors"] if e not in weak_high)[:300]))
    y = hints["y_fixed"] or (None if hints.get("dispute_pending") else data.get("Y"))
    if not hints["y_fixed"] and hints.get("ai_note") and ((tl or {}).get("summary") or {}).get("has_tsxk"):
        if y != "二创（单IP混剪类）":                             # D-065 ⑤-3, as example 13 in the guidance table
            flags.append(FD.flag("yellow", f"Y：以吞噬星空画面为基础的 AI 创作归单IP混剪（D-065），模型原判{y}"))
        y = "二创（单IP混剪类）"
    if not hints["y_fixed"] or fixed_y is not None and fixed_y != '完全无关内容':
        if fixed_y is None:
            fields["Y"] = FD._f(y, "code" if hints.get("ai_note") else "model",
                            "AI 创作归单IP混剪（D-065）" if hints.get("ai_note") else "GLM 按指引在纯cut、偷渡和二创各子类中选")
        default = rules["columns"]["Z"]["code_defaults"].get(y)
        if hints.get("ai_note"):                                   # WO-102-R2 A4: AI creation, Z per "高" example ③
            default = "高"
            flags.append(FD.flag("yellow", "AI 创作：画面文字有\"AI虚构\"一类声明，按利用 AI 创作处理（二创，Z 高），请核实"))
        if default and data.get("Z") not in (None, default):
            flags.append(FD.flag("yellow", f"Z：模型给{data.get('Z')}，按指引示例（{y}）改为{default}"))
        z = default or data.get("Z")
        if z == "高" and weak_high and not default:              # D-071: still no valid basis after the retry
            z = "中"
            flags.append(FD.flag("yellow", f"Z：模型给高，重答后依据仍不合格（{data.get('Z_basis') or '未写'}），降为中（D-071）；"
                                           "比对意见请按中核对"))
        fields["Z"] = FD._f(z, "code" if default else "model",
                            "指引 Z 的示例" if default else f"GLM 按指引 Z 的标准（{data.get('Z_basis') or ''}）")
        if y == "偷渡" and ((rules.get("policies") or {}).get("appended_unrelated") or {}).get("value") == "偷渡":
            flags.append(FD.flag("yellow", "偷渡：原片后面拼接了无关内容（口径开关 B3，待所有者确认），请核对"))
    if (fields.get("Z") or {}).get("value") == "高":
        flags.append(FD.flag("yellow", "Z 高：合理使用可能性大，请人确认（D-071）"))
    if (fields.get("Q") or {}).get("value") == FD.YES:
        flags.append(FD.flag("red", "Q=是：判为完全不含吞噬星空动画，交人判断（D-071）"))
    fields["U"] = FD._f(fixed_u if fixed_u is not None else data.get("U"), "code" if fixed_u is not None else "model",
                        "所有者采用混剪版：人声字幕为原片台词" if fixed_u is not None else "GLM：UP 主自己的第三人称讲述、分析点评、吐槽或提问")
    music = res["music"]
    fields["V"] = FD._f(data.get("V"), "model", "GLM：是否只加了背景音乐；疑似UP主配乐："
                        + (f"{music['verdict']}（{'；'.join(music['evidence'])}）" if music["verdict"] else "无声音信息"))
    if music["verdict"] is None:
        flags.append(FD.flag("yellow", "V：没有声音分析结果，是否加了背景音乐按画面判断"))
    elif music["verdict"] == "无旁证" and music["series_music_seconds"] and data.get("V") == FD.NO:
        flags.append(FD.flag("yellow", "V：只有原片画面加纯音乐，不能证明是 UP 主加的配乐，按否填（D-060）"))
    said = text_evidence(tl, audio)                            # WO-102-R2 A5: 小说/有声书 need words to rest on
    extra = []
    for form in data.get("T_extra") or []:
        if form in ("小说", "有声书") and not said.get(form):
            flags.append(FD.flag("yellow", f"T：模型提到{form}，但字幕、口播、画面文字里找不到依据，T 不填{form}（WO-102-R2 A5）"))
        elif form in ("小说", "有声书"):
            extra.append(form)
    forms = [x for x in K.FORMS if x in set(hints["forms"]) | set(extra)]
    fields["T"] = FD._f(f"是（{'、'.join(forms)}）" if forms else FD.NO, "timeline+model",
                        "画面里的其他形式由时间线定，解说或文字里的小说、有声书由 GLM 定")
    promo = SD.promotions(audio)                               # D-061 ④: a clue for X, with its time
    promo_lines = [f"{span(p['start'], p['end'])}出现推广口播，{'、'.join(p['kinds'])}" for p in promo]
    if promo and (fields.get("X") or {}).get("value") == FD.NO:
        flags.append(FD.flag("yellow", f"X：有推广口播（{'、'.join(dict.fromkeys(k for p in promo for k in p['kinds']))}），"
                                       "画面上没有网盘、群号等旁证，X 暂按否，请核对（D-061）"))
    lines = FD.time_lines(tl, fields, y) + (promo_lines if fields.get("X", {}).get("value") != FD.NO else []) + notes + [x for x in (FD.episode_note(title),) if x]
    sections, removed = filter_ac(data.get("AC") or {}, fields, tl)
    fields["AC"] = FD._f(A.assemble(sections, lines), "model+code", "四段由 GLM 写，【其他】只由代码填（D-077）")
    return {"fields": fields, "flags": flags, "evidence": data.get("evidence") or [], "calls": res["calls"],
            "errors": res["errors"], "prompt": res["prompt"], "z_basis": data.get("Z_basis"),
            "sound": {"music": music, "uploader_speech": res["speech"], "promotions": promo},
            "ac_removed": removed, "ac_code_lines": lines, "owner_feedback_version": "D-077-1"}


def save(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
