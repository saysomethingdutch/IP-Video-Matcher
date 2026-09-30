"""WO-106: local, target-page-only playback counts; never changes ledger/review rows.

Run: python -m pilot.webledger.views cases/tsxk.yaml
Cached OCR supplies votes first. Ambiguous digits and conflicting votes are reread
with Apple Vision on enlarged count crops, excluding the play/comment icons.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image

from pilot.tsxk.config import TsxkCase
from pilot.webledger.ledger import address_observation, bv_distance, compile_ocr, OCR_BINARY

COLUMNS = ("序号", "AD", "原始读数", "用到的帧时刻", "一致的帧数", "把握程度", "截图路径")
ICONS = "©◎回口○◉⊙⏵▷▶▻•CＤDＯOo０mpcL"
NUMBER = re.compile(r"^(\d[\d,，]*(?:\.\d+)?)([万亿]?)(\+?)")


def parse_count(text: str, *, digit_crop: bool = False) -> dict | None:
    """A leading 0 is not silently discarded: only an icon-free crop resolves it.

    The first token is the playback count. A comment/date token may follow but
    cannot supply a count. Decimal numbers without a unit are incomplete OCR.
    """
    original = unicodedata.normalize("NFKC", text).strip()
    clean = original
    while clean and clean[0] in ICONS:
        clean = clean[1:].lstrip()
    match = NUMBER.match(clean)
    if not match:
        return None
    digits, unit, plus = match.groups()
    if "." in digits and not unit:
        return None
    # Do not accept partial tokens such as a date, time, a dropped unit, or letters.
    tail = clean[match.end():]
    if tail and tail[0] not in " 日目国田囗口回◎©○\t|":
        return None
    amount = Decimal(digits.replace(",", "").replace("，", ""))
    value = amount * {"": 1, "万": 10000, "亿": 100000000}[unit]
    if value != value.to_integral_value():
        return None
    ambiguous = len(digits) > 1 and digits.startswith("0")
    return {"value": int(value), "raw": digits + unit + plus,
            "ambiguous": ambiguous, "source_text": original}


def target_observations(records: list[dict], bv: str, end: float) -> list[dict]:
    """Only address-confirmed target pages, in the title's left metadata strip.

    Fail closed on absent/wrong addresses. R3's BV distance tolerates OCR glyphs;
    page_end excludes autoplay. Sidebar and comment/date counts are outside x.
    """
    observations = []
    for frame in sorted(records, key=lambda item: item["t"]):
        observed, _ = address_observation(frame)
        if not observed or bv_distance(observed, bv) > 2 or frame["t"] >= end:
            continue
        items = sorted((i for i in frame["items"] if
                        .085 <= i["x"] <= .115 and .185 <= i["y"] <= .235),
                       key=lambda i: i["x"])
        # Leftmost item is the playback count, not the comment or publication date.
        item = next((i for i in items if any(c.isdigit() for c in i["text"])), None)
        if item is not None:
            parsed = parse_count(item["text"])
            observations.append({"t": frame["t"], "item": item, "parsed": parsed,
                                 "method": "cache"})
    return observations


def count_box(image: Image.Image, item: dict) -> tuple[int, int, int, int]:
    """Locate the first ink run after the icon; stop at the gap before comments.

    Bilibili's desktop title metadata starts at x=.098. The icon is at .09.
    Detecting the gap avoids gluing a neighboring comment count onto AD.
    """
    width, height = image.size
    left, right = int(width * .0975), int(width * .18)
    top = int(height * (item["y"] - .003))
    bottom = int(height * (item["y"] + min(item["h"], .025) + .003))
    top, bottom = max(0, top), min(height, bottom)
    band = np.asarray(image.convert("RGB"))[top:bottom, left:right]
    ink = np.flatnonzero((band.min(axis=2) < 205).any(axis=0))
    if not len(ink) or ink[0] > width * .013:
        return left, top, int(width * .13), bottom
    end = ink[0]
    for column in ink[1:]:
        if column - end > max(6, width * .0042):
            break
        end = column
    return left, top, min(width, left + int(end) + 4), bottom


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def extract_frame(video: Path, t: float, path: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t),
                    "-i", str(video), "-frames:v", "1", "-y", str(path)],
                   check=True, capture_output=True)
    return Image.open(path).convert("RGB")


def reread(image: Image.Image, item: dict, path: Path, binary: Path,
           *, context: bool = False) -> dict | None:
    box = count_box(image, item)
    # Sparse digits such as 6900 can be auto-read upside down as 0069 by Vision.
    # The adjacent Chinese/date text fixes orientation; the left play icon stays out.
    if context:
        box = (box[0], box[1], int(image.width * .41), box[3])
    crop = image.crop(box)
    crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS).save(path)
    result = subprocess.run([str(binary), str(path)], check=True, capture_output=True, text=True)
    frames = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    if len(frames) != 1:
        raise RuntimeError(f"local Vision returned no result: {result.stderr[:200]}")
    items = sorted(frames[0]["items"], key=lambda i: i["x"])
    parsed = next((value for i in items if (value := parse_count(i["text"], digit_crop=True))), None)
    if parsed and parsed["ambiguous"] and not context:
        return reread(image, item, path, binary, context=True)
    return parsed


def select_frames(observations: list[dict], limit: int = 5) -> list[dict]:
    # Cover distinct raw readings before filling with time-separated frames.
    chosen, seen = [], set()
    for observation in observations:
        raw = observation["item"]["text"]
        if raw not in seen and len(chosen) < limit:
            seen.add(raw)
            chosen.append(observation)
    for observation in observations:
        if len(chosen) >= limit:
            break
        if all(abs(observation["t"] - old["t"]) >= 3 for old in chosen):
            chosen.append(observation)
    return sorted(chosen, key=lambda observation: observation["t"])


def summarize(observations: list[dict]) -> dict:
    valid = [o for o in observations if o["parsed"] and not o["parsed"]["ambiguous"]]
    votes = Counter(o["parsed"]["value"] for o in valid)
    if not votes:
        return {"value": None, "raw": "", "times": [], "agree": 0,
                "confidence": "未读到", "inconsistent": False}
    value, agree = votes.most_common(1)[0]
    winners = [o for o in valid if o["parsed"]["value"] == value]
    raw = Counter(o["parsed"]["raw"] for o in winners).most_common(1)[0][0]
    conflicting = len(votes) > 1
    confidence = "需核对：读数不一致" if conflicting else (
        "多帧一致" if agree >= 3 else "需核对：仅少量帧")
    return {"value": value, "raw": raw, "times": [o["t"] for o in winners],
            "agree": agree, "confidence": confidence, "inconsistent": conflicting}


def process_row(case: TsxkCase, rid: str, target_bv: str, binary: Path) -> dict:
    folder = case.case_dir / "views"
    crops = folder / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    ledger = case.webledger(rid) or {}
    cache_folder = case.path_of(case.raw["webledger"]["dir"])
    records = {}
    for path in sorted(cache_folder.glob(f"{rid}-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            frame = json.loads(line)
            records[frame["t"]] = frame
    observations = target_observations(list(records.values()), target_bv,
                                       float(ledger.get("play_end") or 0))
    result = summarize(observations)
    cache_summary = dict(result)
    needs_reread = (result["confidence"] != "多帧一致" or any(
        not o["parsed"] or o["parsed"]["ambiguous"] for o in observations))
    crop_path = crops / f"{rid}.png"
    rereads = []
    with tempfile.TemporaryDirectory(prefix=f"wo106-{rid}-") as temp:
        temporary = Path(temp)
        if observations:
            selected = select_frames(observations) if needs_reread else [next(
                o for o in observations if o["parsed"] and o["parsed"]["value"] == result["value"])]
            for index, observation in enumerate(selected):
                image = extract_frame(case.evidence(rid), observation["t"], temporary / "frame.png")
                if index == 0:
                    y = observation["item"]["y"]
                    strip = image.crop((int(image.width * .085), int(image.height * (y - .012)),
                                        int(image.width * .41), int(image.height * (y + .029))))
                    strip.resize((strip.width * 3, strip.height * 3), Image.Resampling.LANCZOS).save(crop_path)
                if needs_reread:
                    parsed = reread(image, observation["item"], temporary / "count.png", binary)
                    rereads.append({"t": observation["t"], "parsed": parsed, "method": "crop4x"})
            if needs_reread:
                reread_result = summarize(rereads)
                # Never export an unresolved icon/reading guess as a fact.
                result = reread_result
        else:
            # A placeholder makes missing observations obvious on the local check page.
            Image.new("RGB", (900, 100), "#eeeeee").save(crop_path)
    if cache_summary["inconsistent"] and result["confidence"] == "多帧一致":
        result["confidence"] = "需核对：缓存有分歧，放大后一致"
    elif (cache_summary["value"] is not None and result["value"] != cache_summary["value"]
          and result["confidence"] == "多帧一致"):
        result["confidence"] = "需核对：缓存与放大值不同"
    return {"row": rid, **result, "crop": str(crop_path), "target_bv": target_bv,
            "cache_summary": cache_summary, "observations": observations,
            "rereads": rereads, "reread": needs_reread}


def write_outputs(case: TsxkCase, results: list[dict]) -> dict:
    folder = case.case_dir / "views"
    path = case.case_dir / "views.csv"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                     dir=case.case_dir, delete=False) as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        for item in sorted(results, key=lambda r: int(r["row"])):
            writer.writerow(dict(zip(COLUMNS, [item["row"], item["value"] if item["value"] is not None else "",
                item["raw"], ";".join(str(t) for t in item["times"]), item["agree"],
                item["confidence"], item["crop"]])))
        temporary = file.name
    os.replace(temporary, path)
    with (folder / "needs_review.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        for item in sorted(results, key=lambda r: int(r["row"])):
            if item["confidence"] != "多帧一致":
                writer.writerow(dict(zip(COLUMNS, [item["row"], item["value"] if item["value"] is not None else "",
                    item["raw"], ";".join(str(t) for t in item["times"]), item["agree"],
                    item["confidence"], item["crop"]])))
    unresolved = [item["row"] for item in results if item["confidence"] != "多帧一致"]
    report = {"total": len(results), "read": sum(r["value"] is not None for r in results),
              "unread": sum(r["value"] is None for r in results),
              "inconsistent": sum(r["inconsistent"] for r in results),
              "cache_inconsistent": sum(r["cache_summary"]["inconsistent"] for r in results),
              "cache_value_changed": sum(r["cache_summary"]["value"] is not None and
                                          r["cache_summary"]["value"] != r["value"] for r in results),
              "reread": sum(r["reread"] for r in results), "needs_review": unresolved}
    atomic_text(folder / "summary.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_text(folder / "observations.json", json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    e = html.escape
    cards = []
    for r in sorted(results, key=lambda r: (r["confidence"] == "多帧一致", int(r["row"]))):
        status = "ok" if r["confidence"] == "多帧一致" else "review"
        evidence = ""
        if status == "review":
            votes = Counter(o["parsed"]["raw"] for o in r["observations"] if o["parsed"])
            readings = "；".join(f"{raw}（{count} 帧）" for raw, count in votes.most_common())
            evidence = f'<details><summary>查看缓存读数与放大依据</summary><p>缓存：{e(readings or "未读到")}</p><p>放大重读 {len(r["rereads"])} 帧；最终采用 {e(r["raw"] or "待核")}。</p></details>'
        cards.append(f'<article class="{status}" id="row-{r["row"]}" data-row="{r["row"]}">'
                     f'<header><b>{r["row"]}</b><span>{e(r["confidence"])}</span></header>'
                     f'<p class="count">AD：{r["value"] if r["value"] is not None else "待核"}</p>'
                     f'<p>原文：{e(r["raw"] or "未识别")} · 一致帧：{r["agree"]}</p>'
                     f'<p class="meta">目标 {e(r["target_bv"])} · 录屏秒：{e(", ".join(map(str, r["times"])) or "无")}</p>'
                     f'<a href="crops/{r["row"]}.png"><img loading="lazy" src="crops/{r["row"]}.png" '
                     f'alt="{r["row"]} 标题下方播放量截图"></a>{evidence}</article>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>吞噬星空 · 播放量核对</title>
<style>body{margin:0;background:#f3f6f6;color:#263a3f;font:16px system-ui,sans-serif}
main{max-width:1180px;margin:24px auto;padding:0 24px}h1{font-size:26px}nav{position:sticky;top:0;background:#16363b;color:white;padding:16px 24px;z-index:1;display:flex;gap:20px;align-items:center;flex-wrap:wrap}
input{font:inherit;padding:8px;border:1px solid #abc;border-radius:5px}label{cursor:pointer}
article{background:white;border:1px solid #d6e0e1;border-radius:8px;padding:18px;margin:16px 0;scroll-margin-top:85px}
article.review{border-left:6px solid #c28a24}header{display:flex;justify-content:space-between;gap:20px}header b{font-size:21px}.review header span{color:#94620d}.ok header span{color:#226955}
.count{font-size:24px;font-weight:650;margin:12px 0}.meta{font-size:13px;color:#64787c;overflow-wrap:anywhere}img{width:100%;height:auto;max-width:1120px}p{margin:8px 0}a{color:#17636b}</style>
<nav><b>播放量单独核对</b><input id="search" placeholder="搜索序号" aria-label="搜索序号"><label><input type="checkbox" id="only">仅看需核对</label></nav><main>
'''
    page += f'<h1>吞噬星空 · AD 播放量</h1><p>{report["total"]} 行；读到 {report["read"]}，未读到 {report["unread"]}，读数不一致 {report["inconsistent"]}。需核对 {len(unresolved)} 行已排在前面。</p><p>截图来自目标页面标题下方；点击可看原图。本页只用于查看，不写核验记录。</p>'
    page += "".join(cards) + '''</main><script>function filter(){document.querySelectorAll('article').forEach(a=>a.hidden=(!a.dataset.row.includes(document.querySelector('#search').value.trim()))||(document.querySelector('#only').checked&&a.classList.contains('ok')))}document.querySelector('#search').addEventListener('input',filter);document.querySelector('#only').addEventListener('change',filter);</script></html>'''
    atomic_text(folder / "check.html", page)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="cases/tsxk.yaml")
    parser.add_argument("--rows", help="small local probe, comma-separated IDs")
    parser.add_argument("--ocr", help="override the local Apple Vision executable")
    args = parser.parse_args()
    case = TsxkCase(args.config)
    candidates = [Path(__file__).resolve().parents[1] / "bin" / "ocr_vision",
                  case.case_dir.resolve().parents[1] / "pilot" / "bin" / "ocr_vision"]
    binary = Path(args.ocr) if args.ocr else next((p for p in candidates if p.is_file()), None)
    if binary is None:
        compile_ocr()
        binary = OCR_BINARY
    ids = args.rows.split(",") if args.rows else case.rows
    inputs = case.read_inputs(rows=ids)
    results = []
    folder = case.case_dir / "views"
    folder.mkdir(parents=True, exist_ok=True)
    for rid in ids:
        match = re.search(r"BV[0-9A-Za-z]{10}", str(inputs[rid].get("E") or ""))
        if not match:
            raise ValueError(f"{rid}: input E lacks a target BV")
        result = process_row(case, rid, match.group(), binary)
        atomic_text(folder / f"{rid}.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        results.append(result)
        if len(results) % 50 == 0:
            print(f"VIEWS {len(results)}/{len(ids)} read={sum(r['value'] is not None for r in results)}", flush=True)
    print(json.dumps(write_outputs(case, results), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
