"""D-077: shared AC filtering and an inspectable, never automatic review cleanup."""
from __future__ import annotations

from copy import deepcopy
import re

from .ac import HEADINGS, assemble

BANNED = re.compile(r"剧场版|血洛|原始星|结束页|重播|连播|推荐(?:界面|页|卡片|列表)|播放器界面|登录框|"
                    r"未能判定|无法确认|难以认定|依据不足|不据此|未据此|不计入|不判为|不认定|"
                    r"非\s*UP\s*主新增|属平台|水印|建议核实|需注意|如经|若经|不按.{0,12}(?:处理|认定)|不影响类别认定|未检出UP主|非其他作品内容", re.I)
SENTENCES = re.compile(r"[^。！？!?\n]+[。！？!?]?")
CLAUSES = re.compile(r"[^，,；;。！？!?\n]+[，,；;。！？!?]?")
CODE_OTHER = re.compile(r"^(?:录屏未显示(?:标签|简介)区|标题标注第\d+集|\d+:\d+(?::\d+)?(?:[-–]\d+:\d+(?::\d+)?)?(?:为无关内容|为《吞噬星空》|出现))")


def items(text: str) -> list[str]:
    return [re.sub(r"^\s*\d+[.、．]\s*", "", x).strip() for x in str(text or "").splitlines() if x.strip() and x.strip() != "无"]


def parse(text: str) -> dict:
    chunks = re.split(r"(【[^】]+】)", str(text or ""))
    by_heading = dict(zip(chunks[1::2], chunks[2::2]))
    return {k: items(by_heading.get(h, "")) if k in ("其他", "新增创作要素") else by_heading.get(h, "").strip()
            for k, h in HEADINGS}


def filter_ac(sections: dict, fixed: dict | None = None, timeline: dict | None = None) -> tuple[dict, list[dict]]:
    out, removed = deepcopy(sections), []
    for key in ("主题概括", "段落情节", "新增创作要素", "比对意见", "其他"):
        listed = key in ("新增创作要素", "其他")
        chunks = list(out.get(key) or []) if listed else CLAUSES.findall(str(out.get(key) or ""))
        keep = []
        for chunk in chunks:
            why = "模型不填写其他" if key == "其他" else ("禁用说法" if BANNED.search(chunk) else "")
            if key == "新增创作要素" and fixed is not None:
                if re.search(r"引流|广告|导流|带货|推广", chunk) and fixed.get("X", {}).get("value") == "否":
                    why = "X 未确认"
                if re.search(r"无关内容|其他(?:作品|国漫|动画)|多IP|多部作品", chunk) and not any(
                        s.get("content") == "unrelated" for s in (timeline or {}).get("segments", [])):
                    why = "时间线未确认无关片段"
                if re.search(r"真人|本人出镜", chunk) and (timeline or {}).get("summary", {}).get("face_frames", 0) < 2:
                    why = "真人出镜不足两帧"
            if why:
                removed.append({"section": key, "text": chunk.strip(), "reason": why})
            else:
                keep.append(chunk)
        text = "".join(keep).strip()
        if not listed and len(keep) != len(chunks):
            text = re.sub(r"[，,；;]+$", "。", text).strip("，,；; ")
        out[key] = keep if listed else text
    return out, removed


Z_WORDS = re.compile(r"合理使用(?:的)?可能性\s*(?:评估为|为|属于|属|[：:])?\s*(?:较高|高|中等|中|较低|低|不涉及)|合理使用判断不涉及")


def sync_z_opinion(text: str, z: str) -> str:
    """Only synchronize an explicit grade in 比对意见; do not rewrite reasoning."""
    if z not in ('高', '中', '低', '不涉及'):
        raise ValueError('Z 不在可选值里')
    parts = re.split(r'(【[^】]+】)', str(text or ''))
    replacement = '合理使用判断不涉及' if z == '不涉及' else '合理使用可能性为' + ('中等' if z == '中' else z)
    for i in range(1, len(parts)-1, 2):
        if parts[i] == '【比对意见】':
            parts[i+1] = Z_WORDS.sub(replacement, parts[i+1])
    return ''.join(parts)


def cleanup_other(text: str) -> tuple[str, list[str]]:
    sections = parse(text)
    removed = [s for s in sections["其他"] if not CODE_OTHER.match(s)]
    sections["其他"] = [s for s in sections["其他"] if CODE_OTHER.match(s)]
    before, separator, _ = str(text).partition("【其他】")
    if not separator or not removed:
        return str(text), []
    body = "\n".join(f"{i}.{s}" for i, s in enumerate(sections['其他'], 1))
    return before + separator + ("\n" + body if body else ""), removed
