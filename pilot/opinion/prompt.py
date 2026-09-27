"""Prompts for the two paragraphs the model writes (WO-004c §3.2, §3.3).

The system prompt (rules + style samples) is the same for every row, so the provider can cache it.
The user prompt carries one row's facts, text only and de-identified: no file path, notarial or case
number, BV number or uploader name. Picture wording comes only from the Z / AA / AB column values.
"""
from __future__ import annotations

import json
from pathlib import Path

UP_TEXT_CHARS = 1500       # §3.2: uploader text cut to about 1,500 characters
FOREIGN_CHARS = 800

RULES = Path(__file__).with_name("rules_qsjx.txt").read_text(encoding="utf-8")


def system_prompt(samples: list[dict], rules: str | None = None) -> str:
    shown = "\n\n".join(f"【样例 {i}：{s['type']}，{s['source']}】\n第一段：{s['purpose']}\n第二段：{s['substitution']}"
                        for i, s in enumerate(samples, 1))
    return ((RULES if rules is None else rules) + "\n\n风格样例（只学文风和结构，不学结论；样例里的剧情、人物和数字与要写的视频无关）：\n\n" + shown)


def fit(lines: list[str], limit: int) -> list[str]:
    """Evenly spaced lines whose total length stays under the limit (the ending is kept, it often concludes)."""
    if sum(len(l) for l in lines) <= limit:
        return lines
    step = 1
    while sum(len(l) for l in lines[::step]) > limit and step < len(lines):
        step += 1
    picked = lines[::step]
    return picked if lines[-1] in picked else picked[:-1] + [lines[-1]]


def user_prompt(f: dict) -> str:
    parts = [f"【视频标题】{f['title']}", f"【性质】{f['nature']}", f"【取证播放】{f['jump']}",
             f"【视频时长】{'较短（可写“时长较短”）' if f['short'] else '不算短'}",
             "【画面】（比对表的三列）" + "；".join(f"{k}：{v}" for k, v in f["picture_named"].items())]
    if f["up_texts"]:
        parts.append("【UP主文字】（字幕或口播，按时间排序" + ("，节选" if f["up_cut"] else "") + "）\n"
                     + "\n".join(f"{i}. {t}" for i, t in enumerate(f["up_texts"], 1)))
    else:
        parts.append("【UP主文字】无")
    if f["foreign"]:
        parts.append(f"【外语语音】（{f['foreign_language']}语转写，节选，可能混有原剧配音）\n" + "\n".join(f["foreign"]))
    if f.get("note"):
        parts.append(f"【注意】{f['note']}")
    return "\n".join(parts) + "\n\n请写出这两段。"


def retry_prompt(f: dict, previous: dict | None, errors: list[str]) -> str:
    return (user_prompt(f) + "\n\n上一稿：" + json.dumps(previous or {}, ensure_ascii=False)
            + "\n上一稿没有通过校验，问题如下：\n" + "\n".join(f"- {e}" for e in errors)
            + "\n请逐条改正，重新输出完整的 JSON。")
