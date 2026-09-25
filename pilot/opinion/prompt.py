"""Prompts for the two paragraphs the model writes (WO-004c §3.2, §3.3).

The system prompt (rules + style samples) is the same for every row, so the provider can cache it.
The user prompt carries one row's facts, text only and de-identified: no file path, notarial or case
number, BV number or uploader name. Picture wording comes only from the Z / AA / AB column values.
"""
from __future__ import annotations

import json

UP_TEXT_CHARS = 1500       # §3.2: uploader text cut to about 1,500 characters
FOREIGN_CHARS = 800

RULES = """你为短视频比对表起草"比对意见"里的两段：【使用的性质和目的】和【对原作品的替代可能性】。【使用的比例】和结论句由程序生成，你不用写。

写法（依据客户的比对要求）：
1. 简明扼要，尽量概括而少引用；关键点写到即可。第一段 70–140 字，第二段 40–80 字。
2. 第一段必须写出这条视频特有的内容：UP主的具体评论或观点、混剪的情感主题或人物关系、配乐或字幕的作用。只写"对剧情进行了评论""介绍了剧情"这类话不合格。表达与原作相同的情感不算转换性。
3. 第一段用 **…** 标出 1–2 处体现转换性的关键句（只在第一段），并以"具有转换性。"或"具有一定的转化性使用特征。"结尾。
4. 第二段按类型选角度：reaction——观众观看是为了"获得陪伴感"和"学习解读视角"，不会替代原剧，反而起到宣传推广作用；混剪——未再现原作品情节，只剪辑了零散画面，观众看的是作者视角的观感体验；解说（夹叙夹议、观点评析）——观众观看是为了了解UP主的观点。视频较短时可以写"时长较短"。
5. 用词按著作权法：UP主的行为是复制、改编、传播；描述制作可以用利用、剪辑、选择、编排、重组、组合。不要把"播放"当成UP主的动作。
6. 不要写："只在B站传播""并没有完全或主要以他人作品代替自己的创作""没有单纯介绍原作品剧情"（改为"未再现原作品情节"）"筛选性""独创性""二创"。标题里没有"一口气看完"就不要这样定性。
7. 只写事实里有的东西：画面只能按【画面】里的说法写（没写的出镜、缩小、贴纸、边框、背景音乐都不要写）；不要写任何数字（集数、时长、比例都由程序写）；引号里的话必须逐字来自【UP主文字】；除《七时吉祥》外不要提其他作品名，除非事实里有。
8. 【外语语音】是外语转写，可能混有原剧配音：不要引用原话，只概括反应的类型（例如惊呼、大笑、附和、评论角色），而且要有转写或画面作依据。
9. 每个具体点在 evidence 里附一条证据，quote 必须从【UP主文字】【外语语音】【画面】里原样复制。

只输出一个 JSON 对象：
{"purpose": "第一段正文，不含提示语", "substitution": "第二段正文，不含提示语", "evidence": [{"point": "具体点", "quote": "原样摘抄"}]}"""


def system_prompt(samples: list[dict]) -> str:
    shown = "\n\n".join(f"【样例 {i}：{s['type']}，{s['source']}】\n第一段：{s['purpose']}\n第二段：{s['substitution']}"
                        for i, s in enumerate(samples, 1))
    return (RULES + "\n\n风格样例（只学文风和结构，不学结论；样例里的剧情、人物和数字与要写的视频无关）：\n\n" + shown)


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
