"""Nature classification (WO-004b §3.3): the model sees facts only, never the plaintiff's video type.

Two calls per row: the nature with verbatim evidence quotes, and a tag for every uploader line
(叙述剧情 / 评论观点 / 其他). Code then checks the quotes, computes the comment share and draws the
line between the commentary types by rule (not by the model).
"""
from __future__ import annotations

from pilot.textindex.text import normalize

from .llm import ask

NATURES = ["观点评析", "混剪", "夹叙夹议解说", "剧情解说", "reaction", "原片搬运", "其他", "不确定"]
COMMENTARY = ("观点评析", "夹叙夹议解说", "剧情解说")
TAGS = ("叙述剧情", "评论观点", "其他")
TAG_CHUNK = 40
MIN_TAGGED = 5            # fewer tagged lines than this: the comment share is not used
FORBIDDEN = ("合理使用", "辩护", "被告", "本案")   # never in a prompt (WO-004b §3.3)

DEFINITIONS = """性质定义（只能选一个）：
- reaction：UP主观看原剧并实时反应、点评，通常能看到UP主本人或听到其实时评论。
- 混剪：剪辑原剧片段，配音乐、字幕或特效来表达某个主题（人物向、CP向、恶搞向），基本没有讲解。
- 剧情解说：主要是复述、讲解剧情，几乎没有个人观点。
- 夹叙夹议解说：叙述剧情和个人观点、评论大约各占一半。
- 观点评析：以UP主的观点、分析、评论为主，剧情只是素材（子类：深度解读、拉片解读、细节分析、人物小传、服化道盘点、文化科普、恶搞讽刺）。
- 原片搬运：基本就是原剧片段，几乎没有UP主加入的内容。
- 其他：以上都不是。
- 不确定：事实不足以判断。"""


IMAGE_NOTE = "见附图：2×2 拼图，是播放中的 4 个时刻（左上=1，右上=2，左下=3，右下=4），请结合画面判断"


def facts_text(d: dict, vision_note: str = "", with_image: bool = False) -> str:
    """The digest as plain facts for the prompt (no plaintiff type, no file paths, no case numbers)."""
    vis = d["visual"]
    src = d["source_lines"]
    seeks = d["ledger"]["seeks"]["value"]
    parts = [f"【视频标题】{d['title']['value']}"]
    if vis:
        parts.append(f"【画面事实】布局：{vis['layout']}；UP主真人出镜：{vis['uploader_on_camera']}；"
                     f"画面上的附加元素：{'、'.join(vis['overlays']) or '无'}；原剧画面约占：{vis['rights_ratio_geometry'] or vis['rights_ratio_model']}")
    elif vision_note:
        parts.append(f"【画面事实】{vision_note}")
    elif with_image:                        # one call: the model looks at the collage itself
        parts.append(f"【画面事实】{IMAGE_NOTE}")
    else:
        parts.append("【画面事实】无")
    parts.append(f"【原剧台词】字幕中逐句对上原剧台词的有 {src['count']} 句"
                 + (f"，来自第{'、'.join(str(e) for e in src['episodes'])}集" if src["episodes"] else ""))
    extra = d["up_text_extra"]
    if extra.get("foreign_captions"):
        parts.append(f"【外文字幕】{extra['foreign_captions']} 条（原剧的外语配音版字幕）")
    if extra.get("foreign_speech_segments"):
        parts.append(f"【外语语音】{extra['foreign_speech_segments']} 段（语言：{extra['foreign_speech_language']}，未转成文字）")
    if d["music"]["value"]:
        parts.append(f"【背景音乐】{'、'.join(d['music']['value'])}")
    elif d.get("music_counted"):
        parts.append(f"【背景音乐】B站识别到 {d['music_counted']} 首（曲名未读到）")
    parts.append(f"【取证播放】{'不清楚是否跳播' if seeks == '未知' else '有跳播（拖动进度条跳过部分内容）' if seeks else '没有跳播'}")
    lines = d["up_texts"]
    if lines:
        parts.append("【UP主文字】（字幕或口播，按时间排序）\n" + "\n".join(f"{l['i']}. [{l['kind']}] {l['text'][:60]}" for l in lines))
    else:
        parts.append("【UP主文字】无")
    return "\n".join(parts)


def nature_prompt(d: dict, vision_note: str = "", with_image: bool = False) -> str:
    return (f"下面是一条B站视频的客观事实。请判断视频的性质。\n\n{facts_text(d, vision_note, with_image)}\n\n{DEFINITIONS}\n\n"
            "只输出一个 JSON 对象：\n"
            '{"nature": "取值见上", "subtype": "观点评析填子类，混剪填人物向/CP向/恶搞向，其他留空", '
            '"recreation": "只对混剪填写：明显 或 很少（是否有明显的情感主题、字幕、配乐、剪辑等再创作）", '
            '"evidence": [{"point": "判断依据", "quote": "从【UP主文字】中逐字摘抄的一句，没有UP主文字就留空"}], '
            '"reason": "一句话理由"}')


def tag_prompt(lines: list[dict]) -> str:
    body = "\n".join(f"{l['i']}. {l['text'][:60]}" for l in lines)
    ids = f"编号 {lines[0]['i']}–{lines[-1]['i']}，共 {len(lines)} 句" if lines else "共 0 句"
    return (f"下面是一条视频里UP主的文字（字幕或口播，{ids}），逐句判断类型：\n"
            "叙述剧情 = 复述或介绍剧情、人物做了什么；评论观点 = UP主的评价、感受、分析、观点、吐槽、调侃；"
            "其他 = 打招呼、求关注、无意义的片段等。\n\n" + body +
            '\n\n只输出 JSON：{"tags": [{"i": 编号, "tag": "叙述剧情/评论观点/其他"}]}，'
            f"上面每句一个，正好 {len(lines)} 个，不要添加没有的编号。")


def template_words() -> str:
    """Everything the prompts say besides the row's own data (checked against FORBIDDEN in the tests;
    the data itself is never filtered)."""
    empty = {"title": {"value": ""}, "visual": None, "source_lines": {"count": 0, "episodes": []},
             "up_text_extra": {}, "music": {"value": []}, "ledger": {"seeks": {"value": [1]}}, "up_texts": []}
    return nature_prompt(empty) + nature_prompt(empty, with_image=True) + tag_prompt([])


def verify_quotes(evidence: list, corpus: list[str]) -> tuple[list[dict], list[dict]]:
    """Keep quotes that occur verbatim (after normalization) in the captions or transcripts."""
    norm_corpus = [normalize(t) for t in corpus if t]
    ok, bad = [], []
    for e in evidence if isinstance(evidence, list) else []:
        if not isinstance(e, dict):
            continue
        q = normalize(str(e.get("quote") or ""))
        if not q:
            continue
        (ok if any(q in c for c in norm_corpus) else bad).append({"point": str(e.get("point") or ""), "quote": e.get("quote")})
    return ok, bad


def comment_share(tags: dict[int, str]) -> float | None:
    story = sum(1 for t in tags.values() if t == "叙述剧情")
    comment = sum(1 for t in tags.values() if t == "评论观点")
    if story + comment < MIN_TAGGED:
        return None
    return round(comment / (story + comment), 3)


def classify(d: dict, backend: str, rules: dict, images=(), vision_note: str = "", corpus: list[str] = ()) -> dict:
    """{"nature", "subtype", "recreation", "model_nature", "comment_share", "tags", "evidence", "flags", "calls"}."""
    flags, calls = [], []
    r = ask(backend, nature_prompt(d, vision_note, with_image=bool(images)), images=list(images), max_tokens=1024,
            required=("nature",))
    calls.append({"step": "nature", "backend": r.backend, "model": r.model, "seconds": r.seconds, "usage": r.usage,
                  "error": r.error, "fallback": r.fallback, "attempts": r.attempts, "valid_json": r.data is not None})
    data = r.data or {}
    model_nature = data.get("nature") if data.get("nature") in NATURES else None
    if r.data is None:
        flags.append({"level": "yellow", "reason": f"性质：模型没有给出有效 JSON（{r.error}）"})
    elif model_nature is None:
        flags.append({"level": "yellow", "reason": f"性质：模型输出“{data.get('nature')}”不在取值范围"})
    nature = model_nature or "不确定"

    tags: dict[int, str] = {}
    lines = d["up_texts"]
    for k in range(0, len(lines), TAG_CHUNK):
        chunk = lines[k:k + TAG_CHUNK]
        t = ask(backend, tag_prompt(chunk), max_tokens=200 + 24 * len(chunk), required=("tags",))
        calls.append({"step": f"tags {k + 1}-{k + len(chunk)}", "backend": t.backend, "model": t.model,
                      "seconds": t.seconds, "usage": t.usage, "error": t.error, "fallback": t.fallback, "attempts": t.attempts,
                      "valid_json": t.data is not None})
        wanted = {l["i"] for l in chunk}
        for item in (t.data or {}).get("tags") or []:
            if isinstance(item, dict) and item.get("tag") in TAGS:
                try:
                    i = int(item.get("i"))
                except (TypeError, ValueError):
                    continue
                if i in wanted:
                    tags[i] = item["tag"]
    share = comment_share(tags)

    # commentary types are separated by rule on the comment share (§3.3)
    c = rules["commentary"]
    if nature in COMMENTARY:
        if share is None:
            flags.append({"level": "yellow", "reason": "解说类：可判断的UP主文字太少，评论占比无法计算"})
            nature = "不确定"
        elif share >= c["mixed_min"]:
            nature = "观点评析" if model_nature == "观点评析" else "夹叙夹议解说"
        elif share < c["plot_max"]:
            nature = "剧情解说"
        else:
            flags.append({"level": "yellow", "reason": f"解说类：评论占比 {share:.2f}，介于 {c['plot_max']}–{c['mixed_min']}，交所有者判断"})
            nature = "不确定"

    evidence, bad = verify_quotes(data.get("evidence"), list(corpus) + [l["text"] for l in lines])
    if bad:
        flags.append({"level": "yellow", "reason": f"{len(bad)} 条证据引用在字幕或转写里找不到，已丢弃"})
    if not evidence:
        src = d["source_lines"]
        if lines:
            flags.append({"level": "yellow", "reason": "性质判断没有通过校验的逐字引用"})
        evidence = [{"point": "事实", "quote": None,
                     "fact": f"原剧台词 {src['count']} 句；UP主文字 {len(lines)} 句；画面事实：{'有' if d['visual'] else '无'}"}]
    subtype = data.get("subtype") if isinstance(data.get("subtype"), str) else None
    recreation = data.get("recreation") if data.get("recreation") in ("明显", "很少") else None
    return {"nature": nature, "model_nature": data.get("nature"), "subtype": subtype, "recreation": recreation,
            "comment_share": share, "tagged_lines": len(tags), "tags": {str(k): v for k, v in sorted(tags.items())},
            "evidence": evidence, "reason": data.get("reason"), "flags": flags, "calls": calls}
