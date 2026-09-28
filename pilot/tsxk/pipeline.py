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
from .config import TsxkCase


def system_prompt(case: TsxkCase) -> tuple[str, dict]:
    """The system prompt and what went into it (counts only; the colleague texts are never printed)."""
    se = case.raw.get("style_examples") or {}
    g = case.raw["guidance"]
    guide = SM.guidance_examples(Path(g["dir"]) / g["examples"], int(se.get("guidance_rows", 8)))
    cs = case.raw["check_set"]
    check_list = case.path_of(cs["list"])
    colleague = []
    if check_list.exists():                        # without the check list, no colleague row can be excluded
        colleague = SM.colleague_examples(Path(case.raw["workbook"]["path"]), cs["sheet"], cs["filled_column"],
                                          int(se.get("colleague_rows", 0)), SM.check_rows(check_list), int(cs["seed"]))
    system = K.SYSTEM.format(standards=K.standards(case.rules()), examples=K.examples_text(guide, colleague))
    return system, {"guidance_examples": len(guide), "colleague_examples": len(colleague),
                    "colleague_skipped": not check_list.exists()}


def span(a: float, b: float) -> str:
    from pilot.timeline.cloud import mmss
    return mmss(a) if round(a) == round(b) else f"{mmss(a)}-{mmss(b)}"


def text_evidence(tl: dict | None, audio: dict | None) -> dict[str, bool]:
    """Whether the captions, on-screen text or filtered speech inside the actual playback name the novel or an
    audiobook (WO-102-R2 A5)."""
    texts = [t for f in (tl or {}).get("frames", []) if not f.get("ignored") for t in (f.get("ocr") or [])]
    texts += SD.uploader_speech(audio)
    blob = " ".join(texts)
    return {"小说": bool(re.search(r"原著|小说", blob)), "有声书": bool(re.search(r"有声书|有声小说|听书", blob))}


def process_row(inputs: dict, led: dict | None, tl: dict | None, rules: dict, system: str, backend: str = "zhipu",
                audio: dict | None = None) -> dict:
    title = (led or {}).get("title") or inputs.get("C") or ""
    f1, fl1 = FD.ledger_fields(inputs, led)
    f2, notes, fl2 = FD.page_fields(led, rules)
    f3, hints, fl3 = FD.timeline_fields(tl, led, title, rules)
    fields = {**f1, **f2, **f3, "AA": FD._f("无法判断", "fixed", "所有者口径，一律填无法判断（D-056 ④）")}
    flags = fl1 + fl2 + fl3 + [FD.flag(f["level"], "时间线：" + f["reason"]) for f in (tl or {}).get("flags", [])]
    fixed = {k: fields[k] for k in ("Q", "W", "X", "S", "AB", "AA") + (("Y", "Z") if hints["y_fixed"] else ())}
    res = K.classify(inputs, led, tl, fixed, hints, system, backend, audio) if tl else \
        {"data": None, "errors": ["没有时间线"], "calls": [], "prompt": "", "music": SD.music_evidence(None, None), "speech": 0}
    data = res["data"] or {}
    if res["errors"]:
        flags.append(FD.flag("red", "分类：" + "；".join(res["errors"])[:300]))
    y = hints["y_fixed"] or data.get("Y")
    if not hints["y_fixed"]:
        fields["Y"] = FD._f(y, "model", "GLM 按指引在纯cut、偷渡和二创各子类中选")
        default = rules["columns"]["Z"]["code_defaults"].get(y)
        if hints.get("ai_note"):                                   # WO-102-R2 A4: AI creation, Z per "高" example ③
            default = "高"
            flags.append(FD.flag("yellow", "AI 创作：画面文字有\"AI虚构\"一类声明，按利用 AI 创作处理（二创，Z 高），请核实"))
        if default and data.get("Z") not in (None, default):
            flags.append(FD.flag("yellow", f"Z：模型给{data.get('Z')}，按指引示例（{y}）改为{default}"))
        fields["Z"] = FD._f(default or data.get("Z"), "code" if default else "model",
                            "指引 Z 的示例" if default else "GLM 按指引 Z 的高、中、低标准")
        if y == "偷渡" and ((rules.get("policies") or {}).get("appended_unrelated") or {}).get("value") == "偷渡":
            flags.append(FD.flag("yellow", "偷渡：原片后面拼接了无关内容（口径开关 B3，待所有者确认），请核对"))
    fields["U"] = FD._f(data.get("U"), "model", "GLM：以配音或字幕讲解、分析剧情")
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
    lines = FD.time_lines(tl, fields, y) + promo_lines + notes + [x for x in (FD.episode_note(title),) if x]
    fields["AC"] = FD._f(A.assemble(data.get("AC") or {}, lines), "model+code", "五段由 GLM 写，时间段等由代码填")
    return {"fields": fields, "flags": flags, "evidence": data.get("evidence") or [], "calls": res["calls"],
            "errors": res["errors"], "prompt": res["prompt"],
            "sound": {"music": music, "uploader_speech": res["speech"], "promotions": promo}}


def save(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
