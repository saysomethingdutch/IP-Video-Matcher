"""The GLM call (WO-102 §3, D-037): Y (when 吞噬星空 animation is shown), U, V, Z, other forms heard or read,
and the wording of the AC sections — with checks, one retry, then a red flag.

Target-blind: the prompt gives the guidance's standards and the facts, never which side the comparison
serves. The model writes no numbers (times, episodes, shares are code's) and quotes nothing verbatim (D-049).
"""
from __future__ import annotations

import json
import re
import time

from contextlib import nullcontext

from pilot.semantic import llm
from pilot.timeline.cloud import THROTTLE

from . import sound as SD
from pilot.textindex.text import normalize

from .fields import NO, YES

SECTIONS = ("主题概括", "段落情节", "新增创作要素", "比对意见", "其他")
Z_HIGH = ("知识解读", "深度分析", "多作品对比", "AI创作")
ANIME_Y = ("纯cut", "偷渡", "二创（解说类）", "二创（单IP混剪类）", "二创（多IP混剪类）", "二创（reaction类）", "二创（其他类）")
FORMS = ("漫画", "动画剧场版", "手游", "小说", "有声书")
QUOTE = re.compile(r"“([^”]+)”|\"([^\"]+)\"|「([^」]+)」")
SENTENCE = re.compile(r"[^。！？!?]+[。！？!?]?")
LABEL = {"tsxk": "《吞噬星空》动画画面", "tsxk_other": "《吞噬星空》{form}", "creator": "UP主自制画面", "unrelated": "无关内容",
         "traffic": "引流画面", "blank": "黑屏或无画面", "unknown": "画面未能判定"}

SYSTEM = """你为短视频比对表填写几项判断和"视频说明"。判断标准摘自比对说明：

{standards}

写"视频说明"：
1. 分五段：主题概括、段落情节、新增创作要素、比对意见、其他。主题概括、段落情节、比对意见每段 1–3 句，简明扼要，不堆砌。
2. 不写任何数字：时间段、时间点、集数、时长、比例都由程序另行填写。
3. 不直接引用视频里的原话，用概括转述。
4. 只写事实里有的东西：画面依据【画面时间线】【画面描述】，文字依据【画面文字】。事实不够时写得概括些，不要编造情节或人物。
5. 新增创作要素、其他两段写成列表，每条一句；没有就给空列表。【其他】只写前面没有涵盖、可能影响认定的特殊情况。
6. 比对意见结合各项判断和视频内容，说明合理使用可能性高、中或低的理由。

示例（程序填写的时间和集数已去掉）：
{examples}

只输出一个 JSON 对象：
{{"Y": "…", "U": "是或否", "V": "是或否", "Z": "高、中或低", "Z_basis": "Z 填高时写知识解读、深度分析、多作品对比或AI创作，否则留空", "Z_quotes": ["Z 按知识解读或深度分析填高时，原文摘出至少两句口播或 UP 主字幕"], "T_extra": [], "AC": {{"主题概括": "…", "段落情节": "…", "新增创作要素": ["…"], "比对意见": "…", "其他": []}}, "evidence": [{{"point": "判断要点", "basis": "依据的事实"}}]}}"""


def standards(rules: dict) -> str:
    c = rules["columns"]
    pol = {k: (v or {}).get("value") for k, v in (rules.get("policies") or {}).items()}
    opt = lambda k: ((rules.get("policies") or {}).get(k) or {}).get("options", {}).get(pol.get(k), "")
    lines = [f"U 是否为解说：{c['U']['rule']}", f"V 是否仅增加背景音乐：{c['V']['rule']}",
             f"V 的旁证：{c['V']['evidence']}", f"V 与水印：{c['V']['watermark']}",
             "Y 类别（有吞噬星空动画画面时，在下面几类里选）："]
    lines += [f"- {k}：{v}" for k, v in c["Y"]["categories"].items() if k in ANIME_Y]
    lines += ["Z 合理使用可能性："] + [f"- {k}：{v}" for k, v in c["Z"]["levels"].items()]
    lines += [f"T 其他形式：{c['T']['rule']} {c['T']['evidence']} 画面能看出的形式由程序填；解说、字幕或口播明确结合了原著小说、有声书的，在 T_extra 里写\"小说\"或\"有声书\"。"]
    lines += ["补充：" + n for n in c["Y"]["notes"]]
    lines += ["口径（WO-102-R2 B，待所有者确认）：",                      # policy switches from rules_tsxk.yaml
              f"- 一段原片只加了标题和零星的点评或引述字幕、没有连续讲解的：{opt('quote_captions')}",
              f"- 整集原片后面拼接了一段无关内容的：{opt('appended_unrelated')}",
              f"- 预告解析、结合设定或原著的讲解：{opt('z_knowledge')}",
              "- 画面文字有\"AI虚构\"\"AI生成\"一类声明的，按利用 AI 创作处理：属于二创；Z 参照\"高\"的示例③（利用 AI 创作生成非原片画面）填高",
              f"- {c['Z']['high_only']} Z 填高时，在 Z_basis 里写明是哪一种。",
              f"- {c['Z']['tightened']} Z_quotes 只用于核对，不写进视频说明。",
              f"- {c['Z']['ai_y']}"]
    return "\n".join(lines)


def examples_text(guide: list[dict], colleague: list[str]) -> str:
    parts = [f"【示例 {i}】标题：{g['title']}\nY：{g['Y']}　U：{g['U']}　V：{g['V']}　Z：{g['Z']}\n{g['AC']}"
             for i, g in enumerate(guide, 1)]
    parts += [f"【文风参考 {i}】\n{t}" for i, t in enumerate(colleague, 1)]
    return "\n\n".join(parts)


def facts_text(inputs: dict, led: dict | None, tl: dict | None, fixed: dict, hints: dict, audio: dict | None = None,
               music: dict | None = None) -> str:
    title = (led or {}).get("title") or inputs.get("C") or ""
    lines = [f"【视频标题】{title}", f"【UP主】{inputs.get('G') or '未知'}"]
    led = led or {}
    page = []
    for key, name in (("tags", "标签区"), ("intro", "简介区"), ("collection", "合集")):
        area = led.get(key) or {}
        if not area.get("visible"):
            page.append(f"{name}未显示")
        else:
            page.append(f"{name}：{'、'.join(area.get('items') or []) or area.get('text') or area.get('title') or '无'}")
    lines.append("【页面】" + "；".join(page))
    lines.append("【程序已定】" + "；".join(f"{k}={v['value']}" for k, v in fixed.items() if v.get("value") is not None)
                 + (f"；画面里能看出的吞噬星空其他形式：{'、'.join(hints['forms'])}" if hints.get("forms") else ""))
    if tl:
        from pilot.timeline.cloud import mmss
        segs = "；".join(f"{mmss(s['start'])}-{mmss(s['end'])} {LABEL[s['content']].format(form=s['form'] or '其他形式')}"
                         for s in tl["segments"])
        lines.append(f"【画面时间线】{segs}")
        if tl["traffic"]:
            lines.append("【引流】" + "；".join(f"{mmss(t['start'])}-{mmss(t['end'])} {'、'.join(t['kinds'])}" for t in tl["traffic"]))
        notes, seen = [], set()
        for f in tl["frames"]:
            if f.get("ignored"):                   # outside the actual playback, or B站's login popup
                continue
            answers = ((f.get("cloud") or {}).get("answers") or {}).values()
            why = "；".join(dict.fromkeys(a["why"] for a in answers if a and a.get("why")))
            if why and why not in seen:
                seen.add(why)
                notes.append(f"{mmss(f['t'])} {why}")
        if notes:
            lines.append("【画面描述】" + " | ".join(notes[:30]))
        s = tl["summary"]
        inside = [f for f in tl["frames"] if not f.get("ignored")]
        lines.append(f"【真人出镜】{s['face_frames']} 帧；【作者字幕或大段文字】{s['text_frames']} 帧（共 {len(inside)} 帧）")
        if s.get("ai_note_frames"):
            lines.append(f"【AI 声明】画面文字里有\"AI虚构\"\"AI生成\"一类声明（{s['ai_note_frames']} 帧），按利用 AI 创作处理")
        texts = []
        for f in tl["frames"]:                     # text on the series, other forms and the uploader's own frames
            if f.get("ignored") or f.get("content") not in ("tsxk", "tsxk_other", "creator", "unknown"):
                continue
            for t in f.get("ocr") or []:
                t = t.strip()
                if len(t) >= 4 and t not in texts and not re.search(r"bilibili|登录|试看|弹幕", t, re.I):
                    texts.append(t)
        lines.append("【画面文字】" + (" / ".join(texts[:60]) if texts else "无"))
    lines.append(SD.section(audio, music or SD.music_evidence(audio, tl)))
    return "\n".join(lines)


def user_prompt(facts: str, y_fixed: str | None, z_fixed: str | None = None) -> str:
    ask = (f"Y 已由程序定为\"{y_fixed}\"（没有吞噬星空动画画面），Z 已定为\"{z_fixed}\"，照填；请判断 U、V，" if y_fixed else
           f"请按比对说明判断 Y（只能在 {'、'.join(ANIME_Y)} 中选）、U、V、Z，")
    return f"{facts}\n\n{ask}如果解说或文字结合了原著小说、有声书，在 T_extra 里写出；然后写视频说明。"


def _strings(ac: dict) -> list[str]:
    out = []
    for k in SECTIONS:
        v = ac.get(k)
        out += [str(x) for x in v] if isinstance(v, list) else [str(v or "")]
    return out


def quote_found(quote: str, sources: list[str]) -> bool:
    """A quoted sentence is really in the transcript or the on-screen text (WO-102-R4 §2)."""
    q = normalize(str(quote or ""))
    return len(q) >= 6 and any(q in normalize(s) for s in sources) or (len(q) >= 6 and q in normalize("".join(sources)))


LEVEL = {"高": "高", "较高": "高", "中": "中", "中等": "中", "低": "低", "较低": "低"}


def stated_levels(opinion: str) -> set[str]:
    """What 【比对意见】 says the fair-use likelihood is (合理使用可能性高/中等/较低…)."""
    return {LEVEL[x] for x in re.findall(r"合理使用(?:的)?可能性(?:为|是)?(较高|较低|中等|高|中|低)", opinion or "")}


def check(data: dict | None, y_fixed: str | None, fixed: dict, tl: dict | None, music: dict | None = None,
          speech: int = 0, sources: list[str] = ()) -> list[str]:
    """Problems with a reply; empty when it can be used."""
    if not isinstance(data, dict):
        return ["回复不是 JSON 对象"]
    errors = []
    y = data.get("Y")
    if not y_fixed and y not in ANIME_Y:
        errors.append(f"Y：{y!r} 不在可选类别里")
    for k in ("U", "V"):
        if data.get(k) not in (YES, NO):
            errors.append(f"{k}：只能填是或否")
    if data.get("Z") not in ("高", "中", "低"):
        errors.append("Z：只能填高、中、低")
    if any(t not in FORMS for t in data.get("T_extra") or []):
        errors.append("T_extra：只能写漫画、动画剧场版、手游、小说、有声书")
    ac = data.get("AC")
    if not isinstance(ac, dict) or any(k not in ac for k in SECTIONS):
        return errors + ["AC：五段不全"]
    for k in ("主题概括", "比对意见"):
        if not str(ac.get(k) or "").strip():
            errors.append(f"AC：{k}不能为空")
    for k in ("主题概括", "段落情节", "比对意见"):
        n = len([s for s in SENTENCE.findall(str(ac.get(k) or "")) if s.strip("。！？!? ")])
        if n > 3:
            errors.append(f"AC：{k}写了 {n} 句，最多 3 句")
    for k in ("新增创作要素", "其他"):
        if not isinstance(ac.get(k), list) or len(ac[k]) > 6:
            errors.append(f"AC：{k}要写成列表，最多 6 条")
    text = "".join(_strings(ac))
    if re.search(r"\d", text):
        errors.append("AC：不要写数字（时间、集数、比例由程序填）")
    if len(QUOTE.findall(text)) > 1:
        errors.append("AC：不直接引用原话，最多 1 处引号")
    w = (fixed.get("W") or {}).get("value")
    if not y_fixed and y in ("偷渡", "二创（多IP混剪类）") and w == NO:
        errors.append(f"Y：{y}要有无关或其他 IP 的内容，但时间线里没有（W=否）")
    if not y_fixed and y == "纯cut" and (data.get("U") == YES or data.get("V") == YES):
        errors.append("Y：纯cut 不能同时是解说或加了背景音乐（纯cut 的 V 填否）")
    if not y_fixed and y == "二创（解说类）" and data.get("U") != YES:
        errors.append("U：解说类的 U 应为是")
    if not y_fixed and data.get("Z") == "高":                   # D-065 ⑤-4: high only in four cases
        basis = str(data.get("Z_basis") or "").replace(" ", "")
        summary = (tl or {}).get("summary") or {}
        if basis not in Z_HIGH:
            errors.append("Z：填高只限知识解读、深度分析、多作品对比、有声明的 AI 创作，请在 Z_basis 写明，否则改为中")
        elif basis == "AI创作" and not summary.get("ai_note_frames"):
            errors.append("Z：画面上没有 AI 创作声明，不能按 AI 创作填高")
        elif basis == "多作品对比" and not (summary.get("seconds") or {}).get("unrelated"):
            errors.append("Z：画面里没有其他作品，不能按多作品对比填高")
        elif basis in ("知识解读", "深度分析"):                  # WO-102-R4 §2: two sentences that really exist
            found = [q for q in data.get("Z_quotes") or [] if quote_found(q, list(sources))]
            if len(found) < 2:
                errors.append(f"Z：填高要从口播或 UP 主字幕里原文摘出至少两句超出剧情复述的话，核对上 {len(found)} 句；"
                              "摘不出就改为中，并据此改写比对意见")
    want = (fixed.get("Z") or {}).get("value") or data.get("Z")             # 【比对意见】 matches Z (WO-102-R4 §2)
    said = stated_levels(str((data.get("AC") or {}).get("比对意见") if isinstance(data.get("AC"), dict) else ""))
    if want and said and said != {want}:
        errors.append(f"AC：比对意见写的合理使用可能性（{'、'.join(sorted(said))}）要和 Z（{want}）一致")
    if not y_fixed and ((tl or {}).get("summary") or {}).get("ai_note_frames") and not str(y).startswith("二创"):
        errors.append("Y：画面有 AI 创作声明，按利用 AI 创作处理，应在二创各子类里选（WO-102-R2 A4）")
    if data.get("V") == YES:                     # WO-102-R1 §1.5: only BGM added, and evidence that it was added
        others = {s["content"] for s in (tl or {}).get("segments", [])} & {"unrelated", "creator", "traffic", "tsxk_other"}
        if (music or {}).get("verdict") != YES:
            errors.append("V：没有 UP 主配乐的旁证（原片画面加纯音乐不算），V 应为否")
        elif others or speech or data.get("U") == YES:
            errors.append("V：除配乐外还有其他新增内容（非原片画面、口播或解说），V 应为否")
    return errors


RATE_LIMIT_WAITS = (60, 180, 300)          # seconds before asking again after HTTP 429


def ask_patiently(backend: str, prompt: str, system: str, waits=RATE_LIMIT_WAITS, sleep=time.sleep):
    """One classification call. A rate limit (HTTP 429, "账户已达到速率限制") lowers the shared limit to 4 for
    every process (D-058 ③) and the same prompt is asked again after a wait; it is not a validation retry."""
    reply = None
    for wait in (0, *waits):
        if wait:
            sleep(wait)
        with THROTTLE.get(backend, nullcontext()):              # shares the provider limit with the timeline
            reply = llm.ask(backend, prompt, system=system, required=("AC",), timeout=900, label="classification")
        if not reply.error.startswith("HTTP 429"):
            break
        if backend in THROTTLE:
            THROTTLE[backend].limited()
    return reply


def classify(inputs: dict, led: dict | None, tl: dict | None, fixed: dict, hints: dict, system: str,
             backend: str = "zhipu", audio: dict | None = None) -> dict:
    """{"data", "errors", "calls", "prompt"}: the reply after at most one retry."""
    music = SD.music_evidence(audio, tl)
    speech = len(SD.uploader_speech(audio))
    sources = SD.uploader_speech(audio) + [t for f in (tl or {}).get("frames", []) if not f.get("ignored")
                                           for t in (f.get("ocr") or [])]
    prompt = user_prompt(facts_text(inputs, led, tl, fixed, hints, audio, music), hints.get("y_fixed"),
                         (fixed.get("Z") or {}).get("value"))
    calls, errors, data = [], [], None
    for attempt in range(2):
        p = prompt if not errors else prompt + "\n\n上一次的回答有这些问题，请改正后重新输出完整 JSON：\n- " + "\n- ".join(errors)
        reply = ask_patiently(backend, p, system)
        calls.append({"backend": reply.backend, "model": reply.model, "seconds": reply.seconds, "usage": reply.usage,
                      "error": reply.error, "fallback": reply.fallback})
        data = reply.data
        errors = [reply.error] if reply.error and data is None else check(data, hints.get("y_fixed"), fixed, tl, music,
                                                                         speech, sources)
        if not errors:
            break
    return {"data": data, "errors": errors, "calls": calls, "prompt": prompt, "music": music, "speech": speech,
            "sources": sources}
