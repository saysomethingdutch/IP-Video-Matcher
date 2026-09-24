"""R1 local VLM protocol: phone crop, one collage, free description, text mapping."""
from __future__ import annotations

import argparse
import base64
import csv
from collections import defaultdict
import json
from pathlib import Path
import re
import statistics
import time

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .infer import COLUMNS, ENUMS, FIELDS, OUTPUT, api, resident, validate


MODELS = ("qwen3-vl:8b-instruct-q4_K_M", "minicpm-v4.5:q4_K_M")
MAPPER = "qwen3:4b-instruct"
SCREEN = {
    "MVI_0001": (168, 92, 1014, 573),
    "MVI_0004": (202, 92, 1052, 567),
    "MVI_0005": (160, 84, 1072, 601),
    "MVI_0006": (132, 103, 1066, 575),
}
PROMPT = """这是同一个视频的 4 个画面（2×2 拼图，按左上、右上、左下、右下编号 1–4），是用相机拍摄的手机屏幕。请综合 4 个画面，只描述视频画面本身，150 字以内：画面布局（整屏电视剧，还是真人出镜大画面加电视剧小窗，有无边框或背景）；有无真人出镜；画面上的文字、贴纸、表情包、装饰；有没有不属于这部古装剧的其他画面（例如现代人物、梗图、其他影视剧）。平台水印、UP 主水印和弹幕不用描述。不要推测剧情。只输出一段描述。"""
MAPPING_PROMPT = """你只处理一段视觉模型的中文描述，不能看图，不能补充描述以外的事实。把描述映射到给定 JSON 字段与枚举。画面形式只作视觉猜测，无法支持则写“不确定”。有 UP 主本人真人出镜才把 uploader_on_camera 写“是”；古装剧演员不算 UP 主。其他作品仅在描述明确有非本古装剧的画面时写“是”。overlays 仅填描述明确提及且不是平台水印、UP主水印、弹幕的元素。描述若对同一字段有相反说法，把该字段写“不确定”，并将字段名放进 contradictory_fields；overlays 矛盾时置空数组。rights_content_ratio 若描述未给出可信面积，写“不确定”。evidence_frames 若无明确图号，填 [1,2,3,4]。不要依据样本类别或标题推断。描述如下：\n"""
COLUMNS_R1 = [*COLUMNS, "description_raw", "geometry_ratio"]


def samples():
    with (OUTPUT / "sample_manifest.csv").open(encoding="utf-8-sig", newline="") as file:
        items = [row for row in csv.DictReader(file) if row["row_id"] != "1141"]
    if len(items) != 19:
        raise ValueError(f"expected 19 rows excluding 1141, found {len(items)}")
    return items


def crop_box(sample, index):
    # One source frame in 1146 was filmed before the phone rotated to landscape.
    if sample["row_id"] == "1146" and index == 1:
        return (380, 4, 738, 696)
    return SCREEN[sample["video"]]


def prepare_collage(sample):
    crops = []
    for index in range(1, 5):
        source = Path(sample[f"frame_{index}_path"])
        with Image.open(source) as image:
            crop = image.crop(crop_box(sample, index)).convert("RGB")
            portrait = crop.height > crop.width
            if portrait:
                crop.thumbnail((1280, 720), Image.Resampling.LANCZOS)
                tile = Image.new("RGB", (1280, 720), "#101010")
                tile.paste(crop, ((1280 - crop.width) // 2, 0))
            else:
                tile = crop.resize((1280, 720), Image.Resampling.LANCZOS)
            target = OUTPUT / "crops_r1" / f"{sample['row_id']}-{index}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            tile.save(target, "JPEG", quality=88)
            crops.append((tile, portrait))
    collage = Image.new("RGB", (2560, 1440), "#101010")
    painter = ImageDraw.Draw(collage)
    for index, (tile, _) in enumerate(crops, 1):
        x, y = ((index - 1) % 2) * 1280, ((index - 1) // 2) * 720
        collage.paste(tile, (x, y))
        painter.rectangle((x + 14, y + 14, x + 65, y + 70), fill="#101010")
        painter.text((x + 32, y + 32), str(index), fill="white")
    destination = OUTPUT / "collages_r1" / f"{sample['row_id']}.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    collage.save(destination, "JPEG", quality=85)
    return destination, crops


def geometry(crops):
    landscape = [tile for tile, portrait in crops if not portrait]
    if len(landscape) < 3:
        return {"ratio": "不确定", "layout": "不确定", "raw_ratio": None}
    frames = np.stack([np.asarray(tile.resize((320, 180)).convert("L"), dtype=np.float32)
                       for tile in landscape])
    # Remove camera exposure drift before measuring per-pixel temporal variation.
    frames = frames - np.median(frames, axis=(1, 2), keepdims=True)
    variation = ndimage.gaussian_filter(np.sqrt(np.var(frames, axis=0)), sigma=2)
    p90 = float(np.percentile(variation, 90))
    if p90 < 3:
        return {"ratio": "约1", "layout": "全屏", "raw_ratio": 1.0,
                "method": "low_temporal_variance"}
    corners = [variation[:30, :50], variation[:30, -50:],
               variation[-30:, :50], variation[-30:, -50:]]
    active_corners = sum(float(np.median(corner)) > .32 * p90 for corner in corners)
    if active_corners >= 3 and float(np.median(variation)) > .4 * p90:
        return {"ratio": "约1", "layout": "全屏", "raw_ratio": 1.0,
                "method": "variance_reaches_screen_corners"}
    # A higher threshold separates the moving inset from the commentator's
    # surrounding motion when both regions touch at a lower threshold.
    threshold = float(np.percentile(variation, 82))
    mask = ndimage.binary_opening(ndimage.binary_closing(variation >= threshold, iterations=3),
                                  iterations=2)
    labels, _ = ndimage.label(mask)
    candidates = []
    for label, bounds in enumerate(ndimage.find_objects(labels), 1):
        if bounds is None:
            continue
        ys, xs = bounds
        width, height = xs.stop - xs.start, ys.stop - ys.start
        area = width * height / variation.size
        if not .035 <= area <= .65 or width < 30 or height < 25:
            continue
        filled = float(np.mean(labels[bounds] == label))
        edge = (xs.stop >= 300 or ys.stop >= 165)
        score = filled + (.15 if edge else 0) + min(area, .25) * .2
        candidates.append((score, area, (xs.start, ys.start, xs.stop, ys.stop)))
    if not candidates:
        return {"ratio": "约1", "layout": "全屏", "raw_ratio": 1.0,
                "method": "no_dynamic_rectangle"}
    _, area, box = max(candidates)
    choices = [(1.0, "约1"), (.8, "约4/5"), (2 / 3, "约2/3"), (.5, "约1/2"), (.25, "约1/4")]
    snapped = min(choices, key=lambda item: abs(item[0] - area))[1]
    outside = np.ones(variation.shape, dtype=bool)
    outside[box[1]:box[3], box[0]:box[2]] = False
    moving_outside = float(np.mean((variation[outside] >= threshold))) if outside.any() else 0
    layout = "画中画(UP主为主)" if moving_outside >= .045 and area <= .55 else "原剧缩小加背景或边框"
    return {"ratio": snapped, "layout": layout, "raw_ratio": round(area, 3),
            "rectangle": box, "moving_outside": round(moving_outside, 3),
            "method": "per_pixel_temporal_variance"}


def ask_visual(model, collage, num_predict=None):
    num_predict = num_predict or (100 if model.startswith("minicpm-v4.5") else 220)
    payload = {"model": model, "messages": [{"role": "user", "content": PROMPT,
                                               "images": [base64.b64encode(collage.read_bytes()).decode()]}],
               "stream": False, "think": False,
               "options": {"temperature": 0, "num_ctx": 8192, "num_predict": num_predict},
               "keep_alive": "20m"}
    # Deliberately no format parameter in the visual step.
    return api("chat", payload, timeout=900)


def map_description(description):
    allowed = "；".join(f"{key}={','.join(values)}" for key, values in ENUMS.items())
    payload = {"model": MAPPER,
               "messages": [{"role": "user", "content": MAPPING_PROMPT +
                             "只能使用这些枚举值：" + allowed + "。所有字段只写一次，数组不要重复元素。\n" + description}],
               "stream": False, "think": False, "format": "json",
               "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512},
               "keep_alive": "20m"}
    response = api("chat", payload, timeout=300)
    mapped = json.loads(response["message"]["content"])
    contradictions = mapped.pop("contradictory_fields", [])
    if not isinstance(contradictions, list):
        contradictions = []
    contradictions = [field for field in contradictions if field in FIELDS]
    for field in ("layout", "rights_content_ratio", "uploader_on_camera", "other_works", "form_guess"):
        if mapped.get(field) not in ENUMS[field]:
            mapped[field] = "不确定"
    overlays = mapped.get("overlays", [])
    if not isinstance(overlays, list):
        overlays = []
    normalized = []
    for item in overlays:
        if item in ENUMS["overlays"]:
            normalized.append(item)
        elif isinstance(item, str):
            if "EP." in item or "标题" in item:
                normalized.append("文字标题卡")
            elif "边框" in item or "装饰" in item:
                normalized.append("装饰花纹或边框")
            elif "贴纸" in item or "表情" in item:
                normalized.append("贴纸或表情包")
    mapped["overlays"] = list(dict.fromkeys(normalized))
    mapped["other_works_desc"] = str(mapped.get("other_works_desc", ""))
    if re.search(r"(?:无|没有|未见)[^。；，]{0,24}(?:其他影视|无关画面|现代梗图|其他作品)", description):
        mapped["other_works"] = "否"
        mapped["other_works_desc"] = ""
    if ("主播" in description or "UP主" in description) and ("小窗" in description or "画中画" in description):
        mapped["form_guess"] = "reaction"
    frames = mapped.get("evidence_frames", [])
    mapped["evidence_frames"] = [frame for frame in frames if type(frame) is int and frame in (1, 2, 3, 4)] if isinstance(frames, list) else []
    if not mapped["evidence_frames"]:
        mapped["evidence_frames"] = [1, 2, 3, 4]
    prediction = validate({field: mapped[field] for field in FIELDS})
    for field in contradictions:
        if field in ("layout", "rights_content_ratio", "uploader_on_camera", "other_works", "form_guess"):
            prediction[field] = "不确定"
        elif field == "overlays":
            prediction[field] = []
        elif field == "other_works_desc":
            prediction[field] = ""
    return prediction, contradictions, response


def write_csv(rows):
    with (OUTPUT / "predictions_r1.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS_R1, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows):
    summary = {}
    for model in MODELS:
        group = [row for row in rows if row["model"] == model]
        if not group:
            continue
        timings = [float(row["elapsed_seconds"]) for row in group]
        summary[model] = {"rows": len(group), "valid_rows": sum(row["status"] == "ok" for row in group),
                          "average_seconds_per_row": round(statistics.mean(timings), 2),
                          "visual_and_mapper_seconds": round(sum(timings), 2),
                          "description_conflicts": sum("描述自相矛盾" in row["error"] for row in group)}
    (OUTPUT / "model_summary_r1.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))


def detect_description_conflicts(description):
    fields = []
    modern_present = bool(re.search(r"(?<!无)现代(?:女性|男性)|手持平板|平板电脑", description))
    modern_denied = bool(re.search(r"(?:无|没有|未见)现代(?:人物|人)|(?:无|没有|未见)[^。；，]{0,12}现代人物", description))
    if modern_present and modern_denied:
        fields.append("other_works")
    full_drama = bool(re.search(r"(?:整屏|全屏)(?:是|为)?(?:电视剧|古装剧|剧集)", description))
    host_inset = bool(re.search(r"(?:主播|UP主|真人出镜).{0,25}(?:小窗|画中画)|(?:小窗|画中画).{0,25}(?:主播|UP主)", description))
    if full_drama and host_inset:
        fields.append("layout")
    if ("真人主播" in description or "UP主真人" in description) and re.search(r"(?:无|没有|未见)真人出镜", description):
        fields.append("uploader_on_camera")
    return fields


def audit_predictions():
    path = OUTPUT / "predictions_r1.csv"
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    responses = defaultdict(list)
    for line in (OUTPUT / "responses_r1.jsonl").read_text().splitlines():
        entry = json.loads(line)
        responses[(entry["row_id"], entry["model"])].append(entry)
    findings = []
    for row in rows:
        history = responses[(row["row_id"], row["model"])]
        latest = history[-1]
        row["error"] = latest.get("error", "")
        if latest.get("mapper_response"):
            mapped = json.loads(latest["mapper_response"]["message"]["content"])
            original_other = mapped.get("other_works", "不确定")
            row["other_works"] = original_other if original_other in ENUMS["other_works"] else "不确定"
            row["other_works_desc"] = str(mapped.get("other_works_desc", ""))
            if re.search(r"(?:无|没有|未见)[^。；，]{0,24}(?:其他影视|无关画面|现代梗图|其他作品)", row["description_raw"]):
                row["other_works"] = "否"
                row["other_works_desc"] = ""
        visual = next((entry["visual_response"] for entry in reversed(history)
                       if entry.get("visual_response")), None)
        mapper = next((entry["mapper_response"] for entry in reversed(history)
                       if entry.get("mapper_response")), None)
        if visual and mapper:
            combined = (visual.get("total_duration", 0) + mapper.get("total_duration", 0)) / 1e9
            if combined > float(row["elapsed_seconds"]) + 1:
                row["elapsed_seconds"] = round(combined, 2)
        mapper_conflicts = [field for field in latest.get("contradictions", []) if field in FIELDS]
        conflicts = list(dict.fromkeys(mapper_conflicts + detect_description_conflicts(row["description_raw"])))
        if not conflicts:
            continue
        for field in conflicts:
            if field in ("layout", "rights_content_ratio", "uploader_on_camera", "other_works", "form_guess"):
                row[field] = "不确定"
            elif field == "overlays":
                row[field] = "[]"
            elif field == "other_works_desc":
                row[field] = ""
            if field == "other_works":
                row["other_works_desc"] = ""
        marker = "描述自相矛盾: " + ",".join(conflicts)
        if marker not in row["error"]:
            row["error"] = "; ".join(part for part in (row["error"], marker) if part)
        findings.append({"row_id": row["row_id"], "model": row["model"], "fields": conflicts})
    write_csv(rows)
    write_summary(rows)
    (OUTPUT / "contradiction_audit_r1.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2))
    return findings


def run(remap_existing=False, redo_overlong=False):
    items = samples()
    manifest = {}
    for sample in items:
        collage, crops = prepare_collage(sample)
        manifest[sample["row_id"]] = (sample, collage, geometry(crops))
    (OUTPUT / "geometry_r1.json").write_text(json.dumps(
        {row_id: item[2] for row_id, item in manifest.items()}, ensure_ascii=False, indent=2))
    existing = OUTPUT / "predictions_r1.csv"
    if existing.exists():
        with existing.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
    else:
        rows = []
    done = {(row["row_id"], row["model"]) for row in rows
            if row["status"] == "ok" and not remap_existing
            and not (redo_overlong and len(row.get("description_raw", "")) > 150)}
    previous = {(row["row_id"], row["model"]): row for row in rows}
    for model in MODELS:
        for row_id, (sample, collage, geo) in manifest.items():
            if (row_id, model) in done:
                continue
            started = time.monotonic()
            prediction = None
            visual = mapper = None
            description = ""
            contradictions = []
            error = ""
            output_cap = 50 if (redo_overlong and previous.get((row_id, model)) and
                                len(previous[(row_id, model)].get("description_raw", "")) > 150) else None
            try:
                saved = previous.get((row_id, model))
                if saved and saved.get("description_raw") and (saved["status"] == "error" or remap_existing):
                    description = saved["description_raw"]
                else:
                    visual = ask_visual(model, collage, output_cap)
                    description = visual["message"]["content"].strip()
                if not description:
                    raise ValueError("empty visual description")
                prediction, contradictions, mapper = map_description(description)
                visual_reference = {key: prediction[key] for key in ("layout", "rights_content_ratio")}
                prediction["rights_content_ratio"] = geo["ratio"]
                if "layout" not in contradictions:
                    if geo["ratio"] == "约1":
                        if prediction["layout"] == "原剧缩小加背景或边框":
                            prediction["layout"] = "全屏"
                    elif prediction["layout"] not in ("画中画(原剧为主)", "画中画(UP主为主)"):
                        prediction["layout"] = geo["layout"]
                error = "描述自相矛盾: " + ",".join(contradictions) if contradictions else ""
            except Exception as exc:
                visual_reference = {}
                error = f"{type(exc).__name__}: {exc}"[:500]
            elapsed = round(time.monotonic() - started, 2)
            peak, vram = resident(model)
            record = {"row_id": row_id, "video_type": sample["video_type"], "model": model,
                      "collage_path": str(collage.resolve()), "ui_status": sample["ui_status"],
                      **{field: (json.dumps(prediction[field], ensure_ascii=False) if
                                 isinstance(prediction[field], list) else prediction[field])
                         if prediction else "" for field in FIELDS},
                      "elapsed_seconds": elapsed, "peak_resident_mb": round(peak / 1e6, 1),
                      "peak_vram_mb": round(vram / 1e6, 1),
                      "status": "ok" if prediction else "error", "error": error,
                      "description_raw": description, "geometry_ratio": geo["ratio"]}
            rows = [row for row in rows if (row["row_id"], row["model"]) != (row_id, model)] + [record]
            write_csv(rows)
            write_summary(rows)
            with (OUTPUT / "responses_r1.jsonl").open("a") as file:
                file.write(json.dumps({"row_id": row_id, "model": model, "description": description,
                                       "visual_reference": visual_reference, "geometry": geo,
                                       "contradictions": contradictions, "visual_response": visual,
                                       "mapper_response": mapper, "visual_num_predict": output_cap or
                                       (100 if model.startswith("minicpm-v4.5") else 220),
                                       "error": error}, ensure_ascii=False) + "\n")
            print(model, row_id, record["status"], elapsed, error, flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "audit"])
    parser.add_argument("--remap-existing", action="store_true")
    parser.add_argument("--redo-overlong", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        run(args.remap_existing, args.redo_overlong)
    else:
        print(json.dumps(audit_predictions(), ensure_ascii=False))


if __name__ == "__main__":
    main()
