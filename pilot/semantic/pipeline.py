"""Row pipeline (WO-004b): RowDigest -> nature -> four grid -> deterministic columns -> flags,
plus repeated-capture handling (§3.2) and the same-uploader consistency check (§3.5)."""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

from pilot.content.extract import load_ocr

from . import fields as F
from .classify import COMMENTARY, classify, commentary_nature, facts_text
from .digest import build_digest, load_visual, music_tags
from .audio import load_audio
from .parts import analyse
from .table import OUTPUT_COLS, Table

def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def repeated_captures(rows: dict[str, dict]) -> dict[str, str]:
    """{duplicate row: main row}: same BV and same part only (WO-004b-R2, parts.py)."""
    return analyse(rows)[1]


def reaction_titled(d: dict, cls: dict, rules: dict) -> bool:
    """A title that calls itself a reaction, with watch-along commentary in the captured part (opinion lines,
    filtered uploader speech or the uploader on camera): the type is reaction, not commentary (rules: reaction_title)."""
    spec = rules.get("reaction_title")
    if not spec or cls["nature"] not in spec["from_natures"]:
        return False
    if not re.search("|".join(map(re.escape, spec["title_words"])), d["title"]["value"] or "", re.I):
        return False
    comments = sum(1 for t in (cls.get("tags") or {}).values() if t == "评论观点")
    return comments > 0 or bool((d.get("audio") or {}).get("up_speech")) or (d.get("visual") or {}).get("uploader_on_camera") == "是"


def lenient_reasons(d: dict, cls: dict) -> str:
    """Why a subtitle edit told as plot may count as a mix under story_edit_policy: lenient (WO-004b-R2 §1.3)."""
    audio = d.get("audio") or {}
    why = []
    if d["music"]["value"] or d.get("music_counted") or audio.get("uploader_music"):
        why.append("有配乐")
    if cls.get("model_nature") == "混剪":
        why.append("模型判为混剪")
    if cls.get("recreation") == "明显":
        why.append("有明显的主题化剪辑")
    return "、".join(why)


def intentional_skip_candidate(d: dict, cls: dict) -> str | None:
    """Only a candidate, never a decision (要求 L116-126 A(a)-(d)): a reaction whose capture skipped part of
    the video and kept at most two comment lines."""
    seeks = d["ledger"]["seeks"]["value"]
    if not isinstance(seeks, list):
        return None
    comments = sum(1 for t in cls["tags"].values() if t == "评论观点")
    extra = d["up_text_extra"]
    if extra.get("foreign_speech_segments") or extra.get("foreign_captions", 0) > len(d["up_texts"]):
        return None                         # the host speaks another language: remaining comments are not countable
    if cls["nature"] == "reaction" and seeks and comments <= 2:
        return f"疑似故意跳播：reaction 取证时跳播 {len(seeks)} 处，只保留 {comments} 句点评；请按要求 A(a)–(d) 人工判断"
    return None


def process_row(row: dict, d: dict, rules: dict, table: Table | None, repeat_of: str | None, backend: str,
                corpus: list[str], reuse: dict | None = None) -> dict:
    rid = row["row_id"]
    flags: list[dict] = []
    original = table.original_repeat(int(rid)) if table else None
    if original:
        fields = {c: F.col(v, "原告表格原有写法", "原表已写“同序号…，重复取证”，保留原表写法，不再比对") for c, v in original.items()}
        return {"row_id": rid, "digest": d, "classification": None, "fields": fields,
                "flags": [F.flag("yellow", "原表已标重复取证，保留原表写法")], "calls": []}
    if repeat_of:
        text = f"同序号{repeat_of}，重复取证"
        fields = {c: F.col(text, "重复取证规则", f"与 {repeat_of} 同 BV 同一P，主行取播放更完整的一次") for c in OUTPUT_COLS}
        return {"row_id": rid, "digest": d, "classification": None, "fields": fields,
                "flags": [F.flag("yellow", f"重复取证：以 {repeat_of} 为主行")], "calls": []}

    if reuse:                                   # a saved classification: re-derive the columns without the model
        cls = {**reuse["classification"], "flags": [f for f in reuse["classification_flags"]], "calls": []}
        if cls.get("presumed_from"):            # a presumption is re-decided on the batch every run
            cls["nature"] = cls.pop("presumed_from")
        if cls.get("retyped_from"):             # so is the reaction-title rule (WO-004c-R3)
            cls["nature"] = cls.pop("retyped_from")
    else:
        cls = classify(d, backend, rules, corpus=corpus)
    cls_flags = cls.pop("flags")
    if reuse and cls.get("model_nature") in COMMENTARY:     # the commentary line is a rule: re-apply it to a saved judgment
        cls["nature"], _, extra = commentary_nature(cls["model_nature"], {int(k): t for k, t in cls["tags"].items()}, rules)
        cls_flags = [f for f in cls_flags if not f["reason"].startswith(("解说类：", "观点偏少"))] + extra
    flags += cls_flags
    if reaction_titled(d, cls, rules):                           # WO-004c-R3 §1.3
        cls["retyped_from"], cls["nature"], cls["subtype"] = cls["nature"], "reaction", None
        flags = [f for f in flags if not f["reason"].startswith(("解说类：", "观点偏少", "性质为"))]
        flags.append(F.flag("yellow", f"{rules['reaction_title']['flag']}（原判 {cls['retyped_from']}）"))
    audio = d.get("audio") or {}
    if cls["nature"] == "混剪" and cls.get("recreation") != "明显" and audio.get("uploader_music") is True:
        cls["recreation"] = "明显"                    # 疑似UP主配乐 counts as re-creation (R2 §1.2)
        flags.append(F.flag("yellow", "混剪：声音里疑似UP主配乐，按有明显再创作计"))
    if rules.get("story_edit_policy", "strict") == "lenient" and cls["nature"] == "剧情解说" and (why := lenient_reasons(d, cls)):
        cls["nature"], cls["subtype"], cls["recreation"] = "混剪", None, "明显"
        flags.append(F.flag("yellow", f"口径：宽松（{why}，字幕剪辑按混剪类）"))
    seeks = d["ledger"]["seeks"]["value"]
    jump = isinstance(seeks, list) and bool(seeks)
    if seeks == "未知":
        flags.append(F.flag("yellow", "跳播情况未知（台账没有播放器读数）"))
    grid, grid_flags = F.four_grid(cls["nature"], cls["subtype"], cls["recreation"], jump, rules)
    flags += grid_flags
    rule_src = f"客户规则：{cls['nature']}" + ("，跳播" if jump else "")
    basis = f"性质 {cls['nature']}（模型 {cls['model_nature']}，评论占比 {cls['comment_share']}）；" + \
            "；".join(e.get("quote") or e.get("fact") or "" for e in cls["evidence"][:2])

    fields = {"U": F.col(F.hms(d["ledger"]["video_duration"]["value"]), "台账 video_duration"),
              "X": F.col(F.hms(d["ledger"]["capture_seconds"]["value"]), "台账 capture_seconds")}
    fields["V"], fields["W"] = F.parts_columns(d, row.get("page_types") or {}, row.get("_part"))
    flags += [F.flag("yellow", f"分P：{x}") for x in (row.get("_part") or {}).get("flags", [])]
    fields["Y"] = F.col(fields["U"]["value"] if cls["nature"] == "原片搬运" else "/", "性质规则",
                        "原片搬运：权利作品画面时长按视频时长计" if cls["nature"] == "原片搬运" else "非纯切条类")
    for c, fn in (("Z", F.non_rights_column), ("AA", F.occlusion_column), ("AB", F.size_column),
                  ("AD", F.views_column), ("AE", F.episode_column)):
        fields[c], extra = fn(d)
        flags += extra
    complete = d["ledger"]["complete"]["value"]
    fields["AC"] = F.col("是" if complete is True else "否" if complete is False else "未知", "台账 complete")
    fields["AF"] = F.col("", "WO-004c", "比对意见由 WO-004c 起草")
    for c, value in zip(("AG", "AH", "AI", "AJ"), grid):
        fields[c] = F.col(value, rule_src, basis)

    if repeat_main_of := [dup for dup, main in (row.get("_repeats") or {}).items() if main == rid]:
        flags.append(F.flag("yellow", f"主行：{'、'.join(repeat_main_of)} 为同一视频的重复取证"))
    if cand := intentional_skip_candidate(d, cls):
        flags.append(F.flag("red", cand))
    if any(w in d["title"]["value"] for w in rules["title_novel"]["words"]):
        flags.append(F.flag("yellow", "标题含“原著/小说”：注意配音是否为小说原文（要求 L134-138）"))
    others = d["other_batches"]["value"]
    if others:
        flags.append(F.flag("yellow", f"同 BV 在本批以外另有取证（序号 {'、'.join(map(str, others))}）"))
    if not d["visual"]:                          # one flag for Z / AA / AB instead of one per column
        flags.append(F.flag("yellow", "无画面事实：真人出镜、其他作品、贴纸/边框、画面大小未核（Z、AA、AB 列）"))
    if d["asr"] != "有" and not d["asr"].endswith("已转写"):
        flags.append(F.flag("yellow", d["asr"]))
    return {"row_id": rid, "digest": d, "classification": cls, "classification_flags": cls_flags, "fields": fields,
            "flags": flags, "calls": cls.pop("calls") or (reuse or {}).get("calls", [])}


def _grid_key(r: dict) -> str:
    """The four cells without the 跳播 prefix, which follows the row's own seek fact."""
    return " / ".join(str(r["fields"][c]["value"]).replace("跳播+", "") for c in ("AG", "AH", "AI", "AJ"))


def carry_opinion(r: dict, prev: dict | None) -> None:
    """A classification rerun keeps the row's drafted 比对意见 (WO-004c); when the nature or the grid changed,
    the opinion is flagged for redrafting (the flag carries the opinion module's prefix, so a redraft clears it)."""
    if not prev or not prev.get("opinion"):
        return
    r["opinion"], r["fields"]["AF"] = prev["opinion"], prev["fields"]["AF"]
    r["flags"] += [f for f in prev.get("flags", []) if f["reason"].startswith("意见：") and "意见待重写" not in f["reason"]]
    r["_prev_state"] = ((prev.get("classification") or {}).get("nature"),
                        [prev["fields"][c]["value"] for c in ("AG", "AH", "AI", "AJ")])


def mark_stale_opinions(results: list[dict]) -> list[str]:
    """After the batch-level passes (presumption): an opinion whose row ended with another nature or grid than
    when it was drafted is flagged for redrafting. Comparing earlier would flag every presumed row."""
    stale = []
    for r in results:
        old = r.pop("_prev_state", None)
        if old is None:
            continue
        new = ((r.get("classification") or {}).get("nature"), [r["fields"][c]["value"] for c in ("AG", "AH", "AI", "AJ")])
        if tuple(old[1]) != tuple(new[1]) or old[0] != new[0]:
            r["flags"].append(F.flag("yellow", f"意见：分类已变（{old[0]} → {new[0]}），意见待重写"))
            stale.append(r["row_id"])
    return stale


def _few_comments(r: dict, spec: dict) -> bool:
    """Little or no commentary in the captured part (R4): <= few_comments_max opinion lines and no filtered
    uploader speech (D-043). Foreign speech cannot be counted, so such a row is never "few"."""
    d, cls = r["digest"], r["classification"]
    comments = sum(1 for t in (cls.get("tags") or {}).values() if t == "评论观点")
    return comments <= spec.get("few_comments_max", 0) and not ((d.get("audio") or {}).get("up_speech")) \
        and not (d.get("up_text_extra") or {}).get("foreign_speech_segments")


def _short_capture(d: dict, spec: dict) -> bool:
    cap, total = d["ledger"]["capture_seconds"]["value"], d["ledger"]["video_duration"]["value"]
    if not isinstance(cap, (int, float)):
        return False
    return cap < spec.get("short_capture_seconds", 0) or \
        (isinstance(total, (int, float)) and total > 0 and cap < spec.get("short_capture_share", 0) * total)


def _set_nature(r: dict, nature: str, jump: bool, rules: dict, source: str, basis: str) -> None:
    cls = r["classification"]
    cls.setdefault("presumed_from", cls["nature"])
    cls["nature"] = nature
    grid, _ = F.four_grid(nature, None, None, jump, rules)
    for c, value in zip(("AG", "AH", "AI", "AJ"), grid):
        r["fields"][c] = F.col(value, source, basis)
    r["fields"]["Y"] = F.col("/", "性质规则", "非纯切条类")
    r["flags"] = [f for f in r["flags"] if not f["reason"].startswith(("原片搬运：", "性质为", "混剪但再创作很少"))]


def _revert(r: dict, rules: dict) -> None:
    """Undo an earlier presumption so the batch is decided afresh (rows loaded from disk carry it)."""
    cls, d = r["classification"], r["digest"]
    cls["nature"] = cls.pop("presumed_from")
    seeks = d["ledger"]["seeks"]["value"]
    grid, grid_flags = F.four_grid(cls["nature"], cls.get("subtype"), cls.get("recreation"),
                                   isinstance(seeks, list) and bool(seeks), rules)
    for c, value in zip(("AG", "AH", "AI", "AJ"), grid):
        r["fields"][c] = F.col(value, f"客户规则：{cls['nature']}", r["fields"][c].get("basis", ""))
    raw = cls["nature"] == "原片搬运"
    r["fields"]["Y"] = F.col(r["fields"]["U"]["value"] if raw else "/", "性质规则",
                             "原片搬运：权利作品画面时长按视频时长计" if raw else "非纯切条类")
    r["flags"] += [f for f in grid_flags if f not in r["flags"]]


def presume_skipped_reactions(results: list[dict], rules: dict) -> list[str]:
    """WO-004b-R3/R4 (rules: skip_reaction_presumption), on the whole batch (it needs the uploader's other rows):
    a reaction whose captured part holds little or no commentary, captured with skips or very briefly, is not
    held against the defendant; and a reaction series whose other rows have commentary is never 原片搬运."""
    spec = rules.get("skip_reaction_presumption")
    if not spec:
        return []
    words = re.compile("|".join(map(re.escape, spec["title_words"])), re.I)
    own = {spec[k] for k in ("flag", "short_flag", "no_presumption_flag", "commented_flag", "undecided_flag") if k in spec}
    groups = defaultdict(list)
    for r in results:
        if r.get("classification"):
            r["flags"] = [f for f in r["flags"] if f["reason"] not in own]
            if r["classification"].get("presumed_from"):
                _revert(r, rules)
            groups[r["digest"]["uploader"]["value"]].append(r)
    changed = []
    for rs in groups.values():
        reactions = {r["row_id"] for r in rs if r["classification"]["nature"] == "reaction"}
        commented = {r["row_id"] for r in rs if not _few_comments(r, spec)}
        all_few = not commented
        for r in rs:
            cls, d = r["classification"], r["digest"]
            if r["fields"]["AG"]["value"] == "是":
                continue
            if not (words.search(d["title"]["value"] or "") or reactions - {r["row_id"]}):
                continue
            seeks = d["ledger"]["seeks"]["value"]
            skipped = isinstance(seeks, list) and bool(seeks)
            short = _short_capture(d, spec)
            siblings_comment = bool(commented - {r["row_id"]})
            if not _few_comments(r, spec):
                if cls["nature"] == "原片搬运" and siblings_comment:          # R4 §1.1: commentary here -> reaction
                    _set_nature(r, "reaction", skipped, rules, "客户规则：reaction 系列不判原片搬运", spec["commented_flag"])
                    r["flags"].append(F.flag("yellow", spec["commented_flag"]))
                    changed.append(r["row_id"])
                continue
            if all_few:
                r["flags"].append(F.flag("yellow", spec["no_presumption_flag"]))
                continue
            if skipped or short:
                _set_nature(r, "reaction", True, rules, "客户规则：跳播 reaction 推定", spec["flag"])
                r["flags"] += [F.flag("yellow", spec["flag"])] + ([F.flag("yellow", spec["short_flag"])] if short and not skipped else [])
                changed.append(r["row_id"])
            elif cls["nature"] == "原片搬运" and siblings_comment:
                _set_nature(r, "不确定", False, rules, "客户规则：reaction 系列不判原片搬运", spec["undecided_flag"])
                r["flags"].append(F.flag("yellow", spec["undecided_flag"]))
                changed.append(r["row_id"])
    return changed


def consistency(results: list[dict]) -> list[dict]:
    """Same uploader (§3.5): a row whose nature or grid differs from the group's most common one while its
    facts resemble those rows gets one warning, also added to its flags. Conclusions are never changed."""
    groups = defaultdict(list)
    for r in results:
        if r["classification"]:
            groups[r["digest"]["uploader"]["value"]].append(r)
    out = []
    for up, rs in groups.items():
        counts = Counter((r["classification"]["nature"], _grid_key(r)) for r in rs)
        if len(counts) < 2:
            continue
        (major, n_major), = counts.most_common(1)
        majority = [r for r in rs if (r["classification"]["nature"], _grid_key(r)) == major]
        feats = [_features(m["digest"], m["classification"]) for m in majority]
        typical = {k: Counter(str(f[k]) for f in feats).most_common(1)[0][0] for k in feats[0]}
        for r in rs:
            if (r["classification"]["nature"], _grid_key(r)) == major:
                continue
            fr = _features(r["digest"], r["classification"])
            shared = [k for k in fr if str(fr[k]) == typical[k]]
            if len(shared) < 3:
                continue
            examples = "、".join(m["row_id"] for m in majority[:5])
            out.append({"uploader": up, "row_id": r["row_id"], "nature": r["classification"]["nature"],
                        "grid": _grid_key(r), "group_rows": len(rs), "majority_nature": major[0], "majority_grid": major[1],
                        "majority_rows": n_major, "majority_examples": examples, "shared_features": "、".join(shared)})
            r["flags"].append(F.flag("yellow", f"一致性：同 UP 主 {len(rs)} 行中 {n_major} 行为“{major[0]}”（如 {examples}），"
                                               f"本行为“{r['classification']['nature']}”，{'、'.join(shared)}相近；结论未改，请核对"))
    return out


def _features(d: dict, cls: dict) -> dict:
    vis = d["visual"] or {}
    share = cls["comment_share"]
    return {"布局": vis.get("layout"), "真人出镜": vis.get("uploader_on_camera"),
            "跳播": d["ledger"]["seeks"]["value"] if not isinstance(d["ledger"]["seeks"]["value"], list) else bool(d["ledger"]["seeks"]["value"]),
            "评论占比档": None if share is None else ("高" if share >= 0.5 else "中" if share >= 0.2 else "低"),
            "有UP主文字": bool(d["up_texts"])}


def run(case_dir: Path, vlm_dir: Path, table: Table | None, rules: dict, backend: str = "local",
        only: set[str] | None = None, log=print, reuse_dir: Path | None = None, save_dir: Path | None = None,
        workers: int = 1, visual_files: str = "full", fields_only: bool = False) -> list[dict]:
    """Rows are saved to save_dir as they finish, so an interrupted run resumes with reuse_dir=save_dir.
    Digests are built in order; the model calls of several rows may run at once (cloud backends).
    fields_only: keep every saved classification even when its facts changed (no model call); the columns
    are recomputed from the new facts and such rows get a yellow flag (e.g. new picture facts, D-030 ④)."""
    rows = {r["row_id"]: r for r in map(json.loads, (case_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines())}
    batch_ids = {int(key) for key in rows}
    visual, visual_sources = load_visual(vlm_dir, visual_files)
    part_info, repeats = analyse(rows)
    if table:                                   # rows the original table already marks keep its wording and pair with nobody here
        repeats = {dup: main for dup, main in repeats.items()
                   if not table.original_repeat(int(dup)) and not table.original_repeat(int(main))}
    for rid, info in part_info.items():
        rows[rid]["_part"] = info
    for dup, main in repeats.items():            # 要求 L68："播放量取最终那次取证"
        a, b = rows[main], rows[dup]
        if (b.get("video") or "", b.get("video_start") or 0) > (a.get("video") or "", a.get("video_start") or 0) and b.get("views"):
            a["views"], a["_views_note"] = b["views"], f"播放量取最后一次取证（{dup}）的读数"
    ocr_cache: dict[str, dict] = {}
    jobs = []
    for rid, row in rows.items():
        if only and rid not in only:
            continue
        content = _load(case_dir / "content" / f"{rid}.json")
        asr = _load(case_dir / "asr" / f"{rid}.json")
        video = row.get("video")
        if video and video not in ocr_cache:
            ocr_cache.clear()
            ocr_cache[video] = load_ocr(case_dir, video)
        music = music_tags(ocr_cache.get(video, {}), row.get("video_start") or 0, row.get("video_end") or 0) if video else []
        others = table.other_batches(int(rid), batch_ids) if table else []
        audio = load_audio(case_dir, rid)
        d = build_digest(row, content, asr, visual.get(rid), visual_sources.get(rid, ""), music, others, audio)
        if row.get("_views_note"):
            d["ledger"]["views"]["source"] += "；" + row["_views_note"]
        corpus = [e["text"] for e in (content or {}).get("events", [])] + \
                 [s.get("text", "") for s in (asr or {}).get("segments", [])] + \
                 [s["text"] for s in (audio or {}).get("up_speech") or []]
        row["_repeats"] = repeats
        saved = _load(reuse_dir / f"{rid}.json") if reuse_dir else None
        usable = saved and saved.get("classification") and "classification_flags" in saved
        same = usable and facts_text(saved["digest"]) == facts_text(d)
        reuse = saved if usable and (fields_only or same and all(c.get("valid_json") for c in saved.get("calls") or [])) \
            else None                                          # redo failed calls and rows whose facts changed
        if reuse and not same:
            reuse = {**reuse, "classification_flags": [f for f in reuse["classification_flags"] if not f["reason"].startswith("事实已更新")]
                     + [{"level": "yellow", "reason": "事实已更新（画面/声音等），性质沿用上次判断，未重判"}]}
        jobs.append((row, d, corpus, reuse))

    def one(job) -> dict:
        row, d, corpus, reuse = job
        r = process_row(row, d, rules, table, repeats.get(row["row_id"]), backend, corpus, reuse)
        carry_opinion(r, _load(save_dir / f"{row['row_id']}.json") if save_dir else None)
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)
            (save_dir / f"{row['row_id']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        return r

    results = []
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for r in ex.map(one, jobs):
            results.append(r)
            cls = r["classification"] or {}
            log(f"[{time.monotonic() - t0:6.0f}s] {r['row_id']} {cls.get('nature', '重复取证')} | "
                + " / ".join(str(r["fields"][c]["value"]) for c in ("AG", "AH", "AI", "AJ")))
    return results
