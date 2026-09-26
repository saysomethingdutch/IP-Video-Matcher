"""Opinion checks (WO-004c §5). Every check returns plain-language errors that go back to the model once.

1 structure and length, 2 numbers, 3 quotation marks, 4 work titles, 5 phrases the client lists as common
errors, 6 at least one concrete point, 7 bold. Also: picture wording must come from the Z / AA / AB column
values (WO-004a-R3 will change them), evidence quotes must be verbatim, the first paragraph ends with
"具有转换性" or "具有一定的转化性使用特征" (§3.2).
"""
from __future__ import annotations

import re

from pilot.textindex.text import normalize

PURPOSE, PROPORTION, SUBSTITUTION = "【使用的性质和目的】", "【使用的比例】", "【对原作品的替代可能性】"
CONCLUSION = "因此该视频构成“介绍、评论、说明”目的的合理使用。"
MIN_CHARS, MAX_CHARS = 150, 330          # R1 §2: aim at 220–300, never above 330
MAX_QUOTES = 2
WORK = "七时吉祥"
CLIENT_PHRASES = ("介绍、评论、说明", "嗑CP", "获得陪伴感", "学习解读视角", "反应+评论", "适当引用")   # 客户示例用语
BANNED = [(re.compile(p), why) for p, why in (
    (r"只在B站传播", "套话“只在B站传播”（常见错误 4）"),
    (r"并没有完全或主要以他人作品代替自己的创作", "把独创性等同于转换性的说法（常见错误 2）"),
    (r"没有单纯介绍原作品剧情", "应改为“未再现原作品情节”（常见错误 15）"),
    (r"筛选性", "自造词“筛选性”（常见错误 14）"),
    (r"播放(?!量|器|平台)", "“播放”不是 UP 主的动作，可用复制、剪辑、选择、编排等（常见错误 13）"),
    (r"独创性|二创|有演绎", "不要把独创性、二创、演绎等同于转换性（常见错误 2）"),
)]
PICTURE_TERMS = {"出镜": "Z", "背景画面": "Z", "背景音乐": "Z", "其他作品": "Z", "缩小": "AB", "虚化": "AB",
                 "贴纸": "AA", "表情包": "AA", "边框": "AA", "花字": "AA", "标题卡": "AA", "字幕": "AA",
                 "画中画": None, "分屏": None}
QUOTE = re.compile(r"“([^”]+)”|\"([^\"]+)\"|「([^」]+)」|‘([^’]+)’")
TITLE = re.compile(r"《([^》]+)》")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
CN_COUNT = re.compile(r"([一二两三四五六七八九十]+)(集|期|分钟|秒)")
BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
TRANSFORM_END = re.compile(r"(具有转换性|具有一定的转化性使用特征)[。．.]?$")
TYPE_WORDS = {"reaction": re.compile(r"(?i)reaction"), "混剪": re.compile("混剪"), "夹叙夹议解说": re.compile("夹叙夹议"),
              "观点评析": re.compile("观点评析"), "剧情解说": re.compile("剧情解说|剧情类解说")}
CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def plain(text: str) -> str:
    return text.replace("**", "")


def char_count(text: str) -> int:
    return len(re.sub(r"\s", "", plain(text)))


def cn_number(s: str) -> int | None:
    """一 .. 九十九 (enough for episode counts and minutes)."""
    if "十" not in s:
        return CN_DIGIT.get(s) if len(s) == 1 else None
    tens, _, ones = s.partition("十")
    t = CN_DIGIT.get(tens, 1) if tens else 1
    o = CN_DIGIT.get(ones, 0) if ones else 0
    return None if (tens and tens not in CN_DIGIT) or (ones and ones not in CN_DIGIT) else t * 10 + o


def validate(text: str, parts: dict, ctx: dict) -> list[str]:
    """ctx: title, up_texts, foreign (lines), picture {Z, AA, AB}, proportion (the code-written paragraph),
    evidence (the model's list of {point, quote})."""
    errors: list[str] = []
    up = "\n".join(ctx["up_texts"])
    picture = "；".join(str(v) for v in ctx["picture"].values())
    title = ctx["title"]
    body = plain(text)

    # 1 structure and length
    positions = [body.find(label) for label in (PURPOSE, PROPORTION, SUBSTITUTION)]
    if -1 in positions or positions != sorted(positions) or positions[0] != 0:
        errors.append("结构：三段提示语【使用的性质和目的】【使用的比例】【对原作品的替代可能性】要按顺序出现")
    if not body.rstrip().endswith(CONCLUSION):
        errors.append(f"结构：最后一句必须是“{CONCLUSION}”")
    n = char_count(text)
    if not MIN_CHARS <= n <= MAX_CHARS:
        errors.append(f"字数：全文 {n} 字，要求 {MIN_CHARS}–{MAX_CHARS} 字（220–300 字为宜）")
    purpose = plain(parts.get("purpose", ""))
    if not TRANSFORM_END.search(purpose.strip()):
        errors.append("第一段结尾要写“具有转换性”或“具有一定的转化性使用特征”")

    # 2 numbers: only the ones in the RowDigest or the proportion paragraph
    allowed = set(NUMBER.findall(ctx["proportion"] + title + up + picture))
    allowed_int = {float(x) for x in allowed}
    for x in NUMBER.findall(body):
        if x not in allowed and float(x) not in allowed_int:
            errors.append(f"数字：正文里的“{x}”在事实里找不到")
    for m in CN_COUNT.finditer(body):
        v = cn_number(m.group(1))
        if v is not None and float(v) not in allowed_int and m.group(0) not in up + title:
            errors.append(f"数字：正文里的“{m.group(0)}”在事实里找不到")

    # 3 quotation marks: verbatim, and few (常见错误 10: 引用一大堆不特别的话)
    up_norm = normalize(up)
    quoted = [q for q in (next(g for g in m.groups() if g) for m in QUOTE.finditer(body))
              if not any(q in p or p in q for p in CLIENT_PHRASES)]
    for q in quoted:
        if not normalize(q) or normalize(q) not in up_norm:
            errors.append(f"引号：“{q}”没有逐字出现在 UP 主文字里（意见尽量概括，少引用）")
    if len(quoted) > MAX_QUOTES:
        errors.append(f"引号：引用了 {len(quoted)} 处，最多 {MAX_QUOTES} 处；要概括，少引用（常见错误 10）")

    # 4 work titles
    known = normalize(picture + up)
    for m in TITLE.finditer(body):
        name = m.group(1)
        if name != WORK and normalize(name) not in known:
            errors.append(f"作品名：《{name}》在画面事实和 UP 主文字里都找不到")

    # 5 common errors from the client
    for rx, why in BANNED:
        if rx.search(body):
            errors.append(f"禁用语：{why}")
    if "一口气看完" in body and "一口气看完" not in title:
        errors.append("禁用语：标题里没有“一口气看完”，不能这样定性（常见错误 5）")

    # 6 a concrete point: >= 4 consecutive characters shared with the uploader's text or the picture facts;
    #   a row with nothing but its title may lean on the title (R1 §2, flagged yellow by the pipeline)
    title_only = ctx.get("title_only")
    p_norm, src = normalize(purpose), normalize(up + picture + (title if title_only else ""))
    t_norm = "" if title_only else normalize(title)
    if not any(p_norm[i:i + 4] in src and p_norm[i:i + 4] not in t_norm for i in range(len(p_norm) - 3)):
        errors.append("具体性：第一段没有写出这条视频特有的内容（与 UP 主文字或画面事实没有 4 字以上的重合），是套话")

    # 7 bold: one or two, only in the first paragraph
    bolds = BOLD.findall(text)
    first = text.split(PROPORTION)[0]
    if not 1 <= len(bolds) <= 2:
        errors.append(f"加粗：要有 1–2 处 **…**，现在 {len(bolds)} 处")
    elif len(BOLD.findall(first)) != len(bolds):
        errors.append("加粗：只能在第一段")
    if text.count("**") % 2:
        errors.append("加粗：** 没有成对")

    # picture wording comes from the Z / AA / AB columns
    model_text = plain(parts.get("purpose", "") + parts.get("substitution", ""))
    for term, column in PICTURE_TERMS.items():
        if term in model_text and (column is None or term not in str(ctx["picture"].get(column, ""))) \
                and term not in up:
            where = f"{column} 列" if column else "Z/AA/AB 列"
            errors.append(f"画面：“{term}”在{where}（{ctx['picture'].get(column, '无') if column else '无此项'}）里找不到")

    # the type named in the text agrees with the classification (not changed here, §9), and is one type
    named = {t for t, rx in TYPE_WORDS.items() if rx.search(model_text)}
    nature = ctx.get("nature")
    if nature in TYPE_WORDS and named - {nature}:
        errors.append(f"类型：意见里写成了{'、'.join(sorted(named - {nature}))}，分类结果是{nature}，要按分类结果写")
    elif nature not in TYPE_WORDS and len(named) > 1:
        errors.append(f"类型：两段写的类型不一致（{'、'.join(sorted(named))}），全文只能认定一种")

    # evidence quotes are verbatim
    shown = "；".join(f"{k}：{v}" for k, v in (ctx.get("picture_named") or {}).items())   # the line as the prompt shows it
    corpus = normalize(up + "\n".join(ctx.get("foreign", [])) + picture + shown + (title if title_only else ""))
    quotes = [e for e in ctx.get("evidence") or [] if isinstance(e, dict) and str(e.get("quote") or "").strip()]
    bad = [e["quote"] for e in quotes if normalize(str(e["quote"])) not in corpus]
    if not quotes:
        errors.append("证据：evidence 里至少要有一条逐字摘抄")
    for q in bad:
        errors.append(f"证据：“{str(q)[:30]}”不是逐字摘抄（要从【UP主文字】【外语语音】【画面】里原样复制）")
    return errors
