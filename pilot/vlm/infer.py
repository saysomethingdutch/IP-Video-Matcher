"""Run the same local, zero-temperature questionnaire on four source frames per row."""
from __future__ import annotations

import argparse
import base64
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import statistics
import time
from urllib.request import Request, urlopen


OUTPUT = Path("workspace/pilot/vlm")
API = "http://127.0.0.1:11434/api/"
MODELS = ("minicpm-v4.5:q4_K_M", "qwen3-vl:8b-instruct-q4_K_M",
          "minicpm-v4.6:q4_K_M")
ENUMS = {
    "layout": ["全屏", "原剧缩小加背景或边框", "画中画(原剧为主)", "画中画(UP主为主)", "竖屏裁切", "不确定"],
    "rights_content_ratio": ["约1", "约4/5", "约2/3", "约1/2", "约1/4", "其他", "不确定"],
    "uploader_on_camera": ["是", "否", "不确定"],
    "overlays": ["UP主解说字幕", "歌词字幕", "贴纸或表情包", "装饰花纹或边框", "文字标题卡", "其他"],
    "other_works": ["是", "否", "不确定"],
    "form_guess": ["reaction", "混剪", "解说", "原片切条", "不确定"],
}
FIELDS = ("layout", "rights_content_ratio", "uploader_on_camera", "overlays",
          "other_works", "other_works_desc", "form_guess", "evidence_frames")
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {**{key: {"type": "string", "enum": values} for key, values in ENUMS.items()
                            if key != "overlays"},
                         "overlays": {"type": "array", "items": {"type": "string", "enum": ENUMS["overlays"]},
                                      "uniqueItems": True},
                         "other_works_desc": {"type": "string"},
                         "evidence_frames": {"type": "array", "items": {"type": "integer", "enum": [1, 2, 3, 4]},
                                             "minItems": 1, "uniqueItems": True}},
          "required": list(FIELDS)}
PROMPT = """输入的四张图按顺序编号为1、2、3、4，是同一被诉视频的四帧。请只分析手机屏幕里播放的视频内容；取证录像拍到的桌面、手、手机外框，以及手机播放器控件不属于被诉视频。只写四帧里肉眼可见的画面事实，用给定 JSON 字段和枚举回答。看不清就写“不确定”，不要猜剧情、剧集、原剧时间点、上传者身份，也不要判断侵权。rights_content_ratio 是原剧画面占被诉视频画面的面积比例；无法区分原剧画面时写“不确定”。overlays 只列确实可见的内容，不计平台水印、UP主水印和弹幕；没有则写空数组。other_works_desc 只在可见其他作品画面时简述其视觉特征，否则留空。form_guess 只是这四帧的画面形式猜测，不是最终分类。evidence_frames 只列支撑判断的图号。不要输出 JSON 以外的文字。"""
COLUMNS = ["row_id", "video_type", "model", "collage_path", "ui_status", *FIELDS,
           "elapsed_seconds", "peak_resident_mb", "peak_vram_mb", "status", "error"]


def api(endpoint, payload=None, timeout=600):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = Request(API + endpoint, data=data,
                      headers={"Content-Type": "application/json"} if data else {})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def validate(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError(f"expected exactly {FIELDS}, got {sorted(value) if isinstance(value, dict) else type(value)}")
    for name, choices in ENUMS.items():
        if name == "overlays":
            if not isinstance(value[name], list) or any(item not in choices for item in value[name]):
                raise ValueError("invalid overlays")
            value[name] = list(dict.fromkeys(value[name]))
        elif value[name] not in choices:
            raise ValueError(f"invalid {name}: {value[name]}")
    if not isinstance(value["other_works_desc"], str):
        raise ValueError("other_works_desc must be text")
    frames = value["evidence_frames"]
    if not isinstance(frames, list) or not frames or any(type(item) is not int or item not in (1, 2, 3, 4)
                                                         for item in frames):
        raise ValueError("evidence_frames must be unique numbers 1–4")
    value["evidence_frames"] = list(dict.fromkeys(frames))
    return value


def ask(model, sample):
    images = [base64.b64encode(Path(sample[f"frame_{i}_path"]).read_bytes()).decode("ascii")
              for i in range(1, 5)]
    payload = {"model": model, "messages": [{"role": "user", "content": PROMPT, "images": images}],
               "stream": False, "format": SCHEMA, "options": {"temperature": 0, "num_ctx": 8192,
                                                              "num_predict": 512}, "keep_alive": "20m"}
    # The 1B model's default thinking can exhaust the output cap before it
    # emits content. Keep this optional comparison focused on the JSON answer.
    if model == "minicpm-v4.6:q4_K_M":
        payload["think"] = False
    return api("chat", payload)


def resident(model):
    try:
        loaded = api("ps", timeout=10).get("models", [])
        matches = [item for item in loaded if item.get("name") == model or item.get("model") == model]
        return max((item.get("size", 0) for item in matches), default=0), max(
            (item.get("size_vram", 0) for item in matches), default=0)
    except Exception:
        return 0, 0


def predict(model, item):
    started = time.monotonic()
    peak, peak_vram = resident(model)
    response = None
    error = ""
    prediction = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ask, model, item)
        while not future.done():
            size, vram = resident(model)
            peak, peak_vram = max(peak, size), max(peak_vram, vram)
            time.sleep(.5)
        try:
            response = future.result()
            prediction = validate(json.loads(response["message"]["content"]))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
    size, vram = resident(model)
    peak, peak_vram = max(peak, size), max(peak_vram, vram)
    elapsed = round(time.monotonic() - started, 2)
    return ({"row_id": item["row_id"], "video_type": item["video_type"],
             "model": model, "collage_path": item["collage_path"], "ui_status": item["ui_status"],
             **{field: (json.dumps(prediction[field], ensure_ascii=False) if isinstance(prediction[field], list)
                        else prediction[field]) if prediction else "" for field in FIELDS},
             "elapsed_seconds": elapsed, "peak_resident_mb": round(peak / 1e6, 1),
             "peak_vram_mb": round(peak_vram / 1e6, 1),
             "status": "ok" if prediction else "error", "error": error},
            response)


def summarize(rows):
    summary = {}
    for model in sorted({row["model"] for row in rows}):
        group = [row for row in rows if row["model"] == model]
        times = [float(row["elapsed_seconds"]) for row in group]
        summary[model] = {"rows": len(group), "valid_json_rows": sum(row["status"] == "ok" for row in group),
                          "average_seconds_per_row": round(statistics.mean(times), 2) if times else None,
                          "peak_resident_mb": max(float(row["peak_resident_mb"]) for row in group),
                          "peak_vram_mb": max(float(row["peak_vram_mb"]) for row in group)}
    (OUTPUT / "model_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--exclude-rows", nargs="*", default=[])
    parser.add_argument("--only-rows", nargs="*", default=[])
    args = parser.parse_args()
    with (OUTPUT / "sample_manifest.csv").open(encoding="utf-8-sig", newline="") as file:
        samples = list(csv.DictReader(file))
    if len(samples) != 20:
        raise ValueError(f"expected 20 samples, found {len(samples)}")
    destination = OUTPUT / "predictions.csv"
    rows = []
    if destination.exists():
        with destination.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        statuses = {sample["row_id"]: sample["ui_status"] for sample in samples}
        for row in rows:
            row["ui_status"] = statuses[row["row_id"]]
    completed = {(row["row_id"], row["model"]) for row in rows if row["status"] == "ok"}
    for model in args.models:
        for sample in samples:
            if sample["row_id"] in args.exclude_rows or (args.only_rows and sample["row_id"] not in args.only_rows):
                continue
            key = (sample["row_id"], model)
            if key in completed:
                continue
            row, response = predict(model, sample)
            rows = [old for old in rows if (old["row_id"], old["model"]) != key] + [row]
            with destination.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            with (OUTPUT / "responses.jsonl").open("a") as raw:
                raw.write(json.dumps({"row_id": sample["row_id"], "model": model,
                                      "response": response}, ensure_ascii=False) + "\n")
            print(model, sample["row_id"], row["status"], row["elapsed_seconds"],
                  row["error"], flush=True)
        print(json.dumps(summarize(rows).get(model), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
