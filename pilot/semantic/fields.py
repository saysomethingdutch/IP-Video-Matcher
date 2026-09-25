"""Deterministic output columns (WO-004b §3.2) and the four grid cells from the client rules (§3.4).

Every column comes back as {"value", "source", "basis"}; anything the owner must look at goes to flags
as {"level": "yellow"|"red", "reason"}.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).with_name("rules_qsjx.yaml")
PART_RE = re.compile(r"(?<![\d/.])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")
COLLECTION_RE = re.compile(r"合集\D{0,3}(\d+)\s*/\s*(\d+)")
UNKNOWN = "未知"
OVERLAY_NAMES = {"贴纸或表情包": "贴纸/表情包", "装饰花纹或边框": "装饰花纹/边框", "文字标题卡": "文字标题卡",
                 "UP主解说字幕": "UP主字幕", "歌词字幕": "歌词字幕"}


def load_rules(path: Path = RULES_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def col(value, source: str, basis: str = "") -> dict:
    return {"value": value, "source": source, "basis": basis or source}


def flag(level: str, reason: str) -> dict:
    return {"level": level, "reason": reason}


def hms(seconds) -> str:
    if seconds is None or isinstance(seconds, str):
        return UNKNOWN
    s = int(round(float(seconds)))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _number(text) -> float | None:
    if text is None:
        return None
    t = str(text).strip().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(万)?", t)
    return None if not m else float(m.group(1)) * (10000 if m.group(2) else 1)


def parts_columns(d: dict, page_types: dict) -> tuple[dict, dict]:
    """V (合集/分P) and W (共几个/几P，取证了第几个/P); 要求 L49-64."""
    title = d["title"]["value"] or ""
    found = [(int(a), int(b)) for a, b in PART_RE.findall(title) if 1 <= int(a) <= int(b) <= 20 and int(b) > 1]
    if found:
        n, m = found[-1]
        basis = f"原告标题末尾的分P标记“{n}/{m}”" + ("；同 BV 在本批另有行 " + "、".join(d["ledger"]["duplicate_bv_rows"])
                                               if d["ledger"]["duplicate_bv_rows"] else "")
        return col("分P", "原告标题", basis), col(f"共{m}P，取证了第{n}P", "原告标题", basis)
    for p in d["ledger"]["parts"]["value"]:
        m = COLLECTION_RE.search(str(p))
        if m:
            basis = f"台账读到“{p}”"
            return col("合集", "台账 parts", basis), col(f"共{m.group(2)}个，取证了第{m.group(1)}个", "台账 parts", basis)
    if d["ledger"]["duplicate_bv_rows"]:
        basis = "同 BV 在本批另有行 " + "、".join(d["ledger"]["duplicate_bv_rows"]) + "，总P数未识别"
        return col("分P", "台账 同BV行", basis), col("未识别", "台账 同BV行", basis)
    if page_types.get("竖屏详情页"):
        basis = "取证了竖屏详情页，未读到合集/分P标签（推定）"
        return col("否", "台账 页面类型", basis), col("/", "台账 页面类型", basis)
    basis = "台账未读到合集/分P信息"
    return col("未识别", "台账", basis), col("未识别", "台账", basis)


def views_column(d: dict) -> tuple[dict, list]:
    """AD：OCR 读数与表格对比，写"是"、"否，X"或"无，X"。"""
    observed = d["ledger"]["views"]["value"]
    claimed = d["plaintiff"]["views_claimed"]
    src = "台账 views（录像/截图读数）与原告表格播放量"
    if observed in (None, ""):
        return col(UNKNOWN, src, "录像与截图都没有读到播放量"), [flag("yellow", "播放量未读到")]
    if claimed in (None, "", "——", "—", "-", "/"):
        return col(f"无，{observed}", src, f"原告表格未填播放量，录像读数 {observed}"), []
    a, b = _number(observed), _number(claimed)
    if a is not None and a == b or str(observed).strip() == str(claimed).strip():
        return col("是", src, f"录像读数 {observed} = 原告 {claimed}"), []
    return col(f"否，{observed}", src, f"录像读数 {observed}，原告 {claimed}"), [flag("yellow", f"播放量不一致：录像 {observed}，原告 {claimed}")]


def _ep_list(evidence: list[dict]) -> str:
    plain = [str(e["episode"]) for e in evidence if not e.get("suspected")]
    guessed = [f"疑似第{e['episode']}集" for e in evidence if e.get("suspected")]
    return "、".join((["第" + "、".join(plain) + "集"] if plain else []) + guessed)


def episode_column(d: dict) -> tuple[dict, list]:
    """AE 对应集数是否正确，按字幕模块结论（WO-004b §3.2）。"""
    e = d["episodes"]
    verdict, claim = e["verdict"], d["plaintiff"]["part_claimed"]
    src = e["source"]
    eps = _ep_list(e["evidenced"])
    te = e.get("title_episode")
    if verdict == "SUPPORTED":
        return col("是", src, e["statement"]), []
    if verdict == "NARROWER":
        return (col(f"否，已取证部分对应{eps}（原告声称{claim}）", src, e["statement"]),
                [flag("yellow", "对应集数：已取证部分比声称范围窄，措辞由所有者决定")])
    if verdict == "CONTRADICTED":
        return col(f"否，{eps}", src, e["statement"]), [flag("yellow", "对应集数与原告声称冲突")]
    if verdict == "CLAIM_UNPARSEABLE":
        return (col(f"否，{eps}（原告声称“{claim}”无法解析）", src, e["statement"]),
                [flag("yellow", f"原告声称集数“{claim}”无法解析")])
    ref = ""
    if te:
        main = te["episodes"][0]
        ref = f"；标题/台标为第{main}集" + ("，与声称一致" if te.get("agrees") else "，与声称不一致" if te.get("agrees") is False else "")
    value = f"是（未能核实{ref}）"
    flags = [flag("yellow", "对应集数未能用台词核实" + (f"（{ref.lstrip('；')}）" if ref else ""))]
    if te and te.get("agrees") is False:
        flags.append(flag("yellow", f"标题/台标集数与原告声称不一致：画面为第{te['episodes'][0]}集，原告声称 {claim}"))
    return col(value, src, e["statement"] or "无字幕模块输出"), flags


def non_rights_column(d: dict) -> tuple[dict, list]:
    """Z 非权利作品；要求 L72-74，不写商单广告。"""
    vis, items, basis = d["visual"], [], []
    if vis:
        if vis["uploader_on_camera"] == "是":
            items.append("UP主真人出镜")
        if vis["layout"] == "原剧缩小加背景或边框":
            items.append("背景画面")
        if vis["other_works"] == "是":
            items.append(vis["other_works_desc"] or "其他作品画面")
        basis.append(vis["source"])
    for song in d["music"]["value"]:
        items.append(f"背景音乐《{song}》")
    if not d["music"]["value"] and d.get("music_counted"):
        items.append(f"背景音乐（B站识别到{d['music_counted']}首，曲名未读到）")
    if d["music"]["value"] or d.get("music_counted"):
        basis.append(d["music"]["source"])
    if items:
        return col("是，存在" + "、".join(dict.fromkeys(items)), "画面事实/音乐标签", "；".join(basis)), []
    return col("否" if vis else "未识别", "画面事实", "；".join(basis) or "无画面事实"), []


def occlusion_column(d: dict) -> tuple[dict, list]:
    """AA 遮挡物；要求 L76-78，不写 UP主水印和 B 站水印。"""
    items, basis = [], []
    counts = d["caption_counts"]
    if counts.get("底部解说字幕", 0) >= 2:
        items.append("UP主字幕")
    if counts.get("竖排或特效花字", 0) >= 2:
        items.append("花字")
    if items:
        basis.append(f"字幕轨：底部解说字幕 {counts.get('底部解说字幕', 0)} 条，花字 {counts.get('竖排或特效花字', 0)} 条")
    vis = d["visual"]
    if vis:
        items += [OVERLAY_NAMES.get(o, o) for o in vis["overlays"] if "水印" not in o]
        basis.append(vis["source"])
    if items:
        return col("是，存在" + "、".join(dict.fromkeys(items)), "字幕轨/画面事实", "；".join(basis)), []
    return col("否" if vis else "未识别", "字幕轨/画面事实", "；".join(basis) or "字幕轨未见，无画面事实"), []


def size_column(d: dict) -> tuple[dict, list]:
    """AB 画面大小：几何比例与画面模型一致时采用，不一致加标记；要求 L80。"""
    vis = d["visual"]
    if not vis:
        return col("未识别", "画面事实", "无画面事实"), []
    geo, model = vis["rights_ratio_geometry"], vis["rights_ratio_model"]
    ratio = geo or model
    flags = [] if not geo or not model or geo == model else [flag("yellow", f"画面比例：几何法 {geo}，模型 {model}，不一致")]
    value = "否" if ratio in ("约1", "1", "全屏") else f"是，画面缩小为{ratio}"
    return col(value, "画面事实（几何法+模型）", f"{vis['source']}；几何 {geo}，模型 {model}"), flags


def four_grid(nature: str, subtype: str | None, recreation: str | None, jump: bool, rules: dict) -> tuple[list[str], list]:
    """AG / AH / AI / AJ from the client rules (rules_qsjx.yaml)."""
    spec = rules["natures"].get(nature) or {}
    grid, flags = spec.get("grid"), []
    if nature == "混剪" and recreation != "明显":
        grid = None
        flags.append(flag("yellow", "混剪但再创作很少：四格不自动判定"))
    if not grid:
        if nature not in ("混剪",):
            flags.append(flag("yellow", f"性质为“{nature}”：四格不自动判定"))
        return ["不确定"] * 4, flags
    grid = list(grid)
    if "{subtype}" in grid[2]:
        if subtype not in (spec.get("subtypes") or []):
            flags.append(flag("yellow", "观点评析的二级分类未确定"))
            subtype = "待定"
        grid[2] = grid[2].replace("{subtype}", subtype)
    if spec.get("confirm"):
        flags.append(flag("yellow", f"{nature}：四格{spec['confirm']}"))
    if jump:
        grid[2:] = [g if g == "/" else rules["jump"]["prefix"] + g for g in grid[2:]]
    return grid, flags
