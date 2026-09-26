"""Row pipeline (WO-004b): RowDigest -> nature -> four grid -> deterministic columns -> flags,
plus repeated-capture handling (§3.2) and the same-uploader consistency check (§3.5)."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

from pilot.content.extract import load_ocr

from . import fields as F
from .classify import classify, facts_text
from .digest import build_digest, load_visual, music_tags
from .table import OUTPUT_COLS, Table

def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _part(title: str) -> tuple[int, int] | None:
    found = [(int(a), int(b)) for a, b in F.PART_RE.findall(title or "") if 1 <= int(a) <= int(b) <= 20 and int(b) > 1]
    return found[-1] if found else None


def repeated_captures(rows: dict[str, dict]) -> dict[str, str]:
    """{duplicate row: main row} for the same BV and the same part inside the batch (D-025: same BV alone is
    usually 分P). The main row is the more complete capture."""
    out = {}
    for rid, row in rows.items():
        for other in row.get("duplicate_bv_rows") or []:
            if other not in rows or int(other) <= int(rid):
                continue
            a, b = row, rows[other]
            if _part(a["plaintiff"]["title"]) != _part(b["plaintiff"]["title"]):
                continue                        # different parts of one BV: both are compared normally
            def fullness(r):
                return (r.get("complete") is True, r.get("capture_seconds") if isinstance(r.get("capture_seconds"), (int, float)) else -1)
            main, dup = (rid, other) if fullness(a) >= fullness(b) else (other, rid)
            out[dup] = main
    return out


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
    else:
        cls = classify(d, backend, rules, corpus=corpus)
    cls_flags = cls.pop("flags")
    flags += cls_flags
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
    fields["V"], fields["W"] = F.parts_columns(d, row.get("page_types") or {})
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
    if d["asr"] != "有":
        flags.append(F.flag("yellow", d["asr"]))
    return {"row_id": rid, "digest": d, "classification": cls, "classification_flags": cls_flags, "fields": fields,
            "flags": flags, "calls": cls.pop("calls") or (reuse or {}).get("calls", [])}


def _grid_key(r: dict) -> str:
    """The four cells without the 跳播 prefix, which follows the row's own seek fact."""
    return " / ".join(str(r["fields"][c]["value"]).replace("跳播+", "") for c in ("AG", "AH", "AI", "AJ"))


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
    repeats = repeated_captures(rows)
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
        d = build_digest(row, content, asr, visual.get(rid), visual_sources.get(rid, ""), music, others)
        if row.get("_views_note"):
            d["ledger"]["views"]["source"] += "；" + row["_views_note"]
        corpus = [e["text"] for e in (content or {}).get("events", [])] + \
                 [s.get("text", "") for s in (asr or {}).get("segments", [])]
        row["_repeats"] = repeats
        saved = _load(reuse_dir / f"{rid}.json") if reuse_dir else None
        usable = saved and saved.get("classification") and "classification_flags" in saved
        same = usable and facts_text(saved["digest"]) == facts_text(d)
        reuse = saved if usable and (fields_only or same and all(c.get("valid_json") for c in saved.get("calls") or [])) \
            else None                                          # redo failed calls and rows whose facts changed
        if reuse and not same:
            reuse = {**reuse, "classification_flags": [f for f in reuse["classification_flags"] if not f["reason"].startswith("事实已更新")]
                     + [{"level": "yellow", "reason": "事实已更新（画面等），性质沿用上次判断，未重判"}]}
        jobs.append((row, d, corpus, reuse))

    def one(job) -> dict:
        row, d, corpus, reuse = job
        r = process_row(row, d, rules, table, repeats.get(row["row_id"]), backend, corpus, reuse)
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
