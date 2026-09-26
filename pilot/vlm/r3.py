"""WO-004a-R3: player-canvas collages, local rerun, and cloud cross-check."""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import statistics
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

from .canvas import geometry_16, ocr_index, player_box, tile, trim_persistent_black
from .full import MODEL, atomic_json, candidates, collage, read_ocr_flags, spread, video_frame
from .infer import ENUMS, FIELDS, resident, validate
from .r1 import COLUMNS_R1, ask_visual, detect_description_conflicts, map_description

OUTPUT = Path("workspace/pilot/vlm")
R3 = OUTPUT / "r3"
NO_PLAY = {"1115", "1133", "1194", "1197"}
CASE_ROWS: set[str] | None = None
VIDEO_DIR: Path | None = None


def configure(config):
    """Set case-owned paths and rows; R3 inference and consensus rules remain unchanged."""
    global OUTPUT, R3, NO_PLAY, CASE_ROWS, VIDEO_DIR, MODEL
    OUTPUT = config.vlm_dir
    R3 = OUTPUT / "r3"
    CASE_ROWS = set(config.row_ids)
    VIDEO_DIR = config.path_of(config.raw["evidence"]["video_dir"])
    NO_PLAY = set(config.raw.get("visual", {}).get("no_play_rows", []))
    MODEL = config.raw["models"]["visual"]["local_fallback"]
    if set(config.raw["models"]["visual"]["cloud"]) != set(CLOUDS):
        raise ValueError("R3 currently requires glm-5.3-flash and deepseek-flash")
CLOUDS = {
    "glm-5.3-flash": ("ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
    "deepseek-flash": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions"),
}
CLOUD_COLUMNS = [*COLUMNS_R1, "prompt_tokens", "completion_tokens", "reasoning_tokens",
                 "total_tokens", "cost_usd", "cost_basis", "attempts", "requested_at"]
PROMPT = ("这是一部被诉视频在播放器画布内的四帧，编号 1–4。只依据可见画面回答 JSON。"
          "拍摄曝光造成的暗边、播放器适配的黑边不算上传者加的背景或边框；"
          "只有原剧作为独立小矩形嵌入，且矩形外另有画面或装饰，才判缩小或画中画。"
          "layout 必须从 全屏、原剧缩小加背景或边框、画中画(原剧为主)、画中画(UP主为主)、竖屏裁切、不确定 中选。"
          "rights_content_ratio 必须从 约1、约4/5、约2/3、约1/2、约1/4、其他、不确定 中选，分母是播放器画布而不是手机整屏。"
          "uploader_on_camera 和 other_works 各从 是、否、不确定 中选；古装剧演员不是 UP 主，其他作品仅指非本剧画面。"
          "overlays 是列表，仅从 UP主解说字幕、歌词字幕、贴纸或表情包、装饰花纹或边框、文字标题卡、其他 中选；"
          "平台水印、UP 主水印、弹幕不算。other_works_desc 仅有其他作品时简述，否则空字符串。"
          "form_guess 从 reaction、混剪、解说、原片切条、不确定 中选。evidence_frames 是支持判断的 1–4 整数列表。"
          "看不清就写不确定，不猜剧情和身份，不判断侵权。只输出一个 JSON 对象，包含上述全部八个字段。")


def rows(case: Path) -> list[dict]:
    result = [json.loads(line) for line in (case / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    if CASE_ROWS is not None and {r["row_id"] for r in result} != CASE_ROWS:
        raise ValueError("ledger rows differ from case config")
    if CASE_ROWS is None and (len(result) != 169 or {r["row_id"] for r in result} != {str(n) for n in range(1051, 1220)}):
        raise ValueError("expected ledger rows 1051–1219")
    return result


def video_dir() -> Path:
    if VIDEO_DIR is not None:
        if not VIDEO_DIR.is_dir():
            raise FileNotFoundError(VIDEO_DIR)
        return VIDEO_DIR
    if not Path("/Volumes/Element").is_dir():
        raise FileNotFoundError("/Volumes/Element is not mounted")
    config = json.loads(Path("pilot/cases/qsjx_e18.yaml").read_text(encoding="utf-8"))
    folder = Path(config["video_dir"])
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    return folder


def sixteen(row: dict, flags: dict[int, dict]) -> list[float]:
    playable = candidates(row, flags)
    if len(playable) >= 16:
        return [float(t) for t, _ in spread(playable, 16)]
    # Short played segments still supply 16 distinct subsecond observations.
    segments = [(float(s["video_start"]), float(s["video_end"]))
                for s in row.get("playback_map", []) if s.get("event") == "play"
                and float(s["video_end"]) > float(s["video_start"])]
    total = sum(b - a for a, b in segments)
    if total <= 0:
        raise ValueError("台账无已播放区间")
    times = []
    for i in range(16):
        position = (i + .5) * total / 16
        for a, b in segments:
            if position < b - a:
                times.append(round(a + position, 3))
                break
            position -= b - a
    if len(set(times)) != 16:
        raise ValueError("16 sampled frames are not distinct")
    return times


def _screenshot_collage(row: dict, target: Path) -> dict:
    path = Path(row["screenshot"]["path"])
    with Image.open(path) as source:
        image = source.convert("RGB")
    if image.size != (1080, 2400):
        raise ValueError(f"unexpected screenshot size: {image.size}")
    # The four rows have no ledger playback. Their original phone screenshots
    # still contain the player; a repeated still is marked as snapshot only.
    box = (0, 180, 1080, 712)
    one = tile(image, box)
    path = target / "collage_1.jpg"
    collage([one] * 4, path)
    return {"row_id": row["row_id"], "video": row["video"], "page_mode": "snapshot_only",
            "frame_times": [], "boxes": [box], "geometry": None,
            "collage_path": str(path.resolve()), "ui_status": "静态截图；台账无已播放区间",
            "source": "screenshot_only", "created_at": datetime.now(timezone.utc).isoformat()}


def prepare_one(row: dict, case: Path, videos: Path,
                flags: dict[int, dict], ocr: dict[int, list[dict]]) -> dict:
    target = R3 / row["row_id"]
    target.mkdir(parents=True, exist_ok=True)
    existing = target / "meta.json"
    if existing.exists():
        prior = json.loads(existing.read_text(encoding="utf-8"))
        if Path(prior.get("collage_path", "")).is_file() and (
                prior.get("page_mode") != "landscape" or prior.get("canvas_version") == 2):
            return prior
    if row["row_id"] in NO_PLAY or not any(s.get("event") == "play" for s in row.get("playback_map", [])):
        meta = _screenshot_collage(row, target)
        atomic_json(existing, meta)
        return meta
    times = sixteen(row, flags)
    if row["row_id"] == "1167":
        # The phone turns from its detail page to landscape. Sample the
        # settled landscape player on both played spans, avoiding the turn.
        times = [116.5 + i * .55 for i in range(8)] + [190.2 + i * .5 for i in range(8)]
    frames = [video_frame(videos / f"{row['video']}.MP4", t) for t in times]
    detected = [player_box(im, row["video"], t, ocr) for im, t in zip(frames, times)]
    modes = [x[0] for x in detected]
    landscape_frames = [frame for frame, result in zip(frames, detected) if result[0] == "landscape"]
    landscape_box = trim_persistent_black(detected[modes.index("landscape")][1], landscape_frames) if landscape_frames else None
    boxes = [landscape_box if mode == "landscape" else box for mode, box, _ in detected]
    if row["row_id"] == "1167":
        # The handset fills the camera view; its actual video is inset.
        boxes = [(158, 164, 1055, 524)] * len(frames)
    elif row["row_id"] == "1204":
        # The status bar sits just above this portrait player.
        boxes = [(407, 59, 698, 213)] * len(frames)
    elif row["row_id"] == "1140":
        # The moving detail-page edge and wooden table sit below/right.
        boxes = [(416, 55, 694, 190)] * len(frames)
    elif row["row_id"] == "1218":
        boxes = [(402, 52, 696, 187)] * len(frames)
    cropped = [(frame.crop(box).transpose(Image.Transpose.ROTATE_270)
                if mode == "rotated_portrait" else frame.crop(box))
               for frame, box, mode in zip(frames, boxes, modes)]
    geo = geometry_16(cropped)
    preferred = next((mode for mode in ("rotated_portrait", "portrait") if modes.count(mode) >= 4), None)
    pool = [i for i, mode in enumerate(modes) if mode == preferred] if preferred else list(range(len(times)))
    chosen = [pool[round((i + .5) * len(pool) / 4 - .5)] for i in range(4)]
    if row["row_id"] == "1095":
        # The hand covers the player later in this two-second rotated segment.
        chosen = [0, 1, 2, 3]
    elif row["row_id"] == "1140":
        # The hand covers the player near the end of this segment.
        chosen = [1, 5, 9, 12]
    elif row["row_id"] == "1218":
        # Use clean frames before and after the brief hand and phone overlay.
        chosen = [0, 2, 11, 14]
    chosen_tiles = [tile(frames[i], boxes[i], rotated=modes[i] == "rotated_portrait") for i in chosen]
    destination = target / "collage_1.jpg"
    collage(chosen_tiles, destination)
    for number, image in enumerate(chosen_tiles, 1):
        image.save(target / f"frame_{number}.jpg", "JPEG", quality=88)
    meta = {"row_id": row["row_id"], "video": row["video"],
            "page_mode": Counter(modes[i] for i in chosen).most_common(1)[0][0],
            "canvas_version": 3,
            "mode_counts": dict(Counter(modes)), "frame_times": times, "collage_times": [times[i] for i in chosen],
            "boxes": [boxes[i] for i in chosen], "geometry": geo, "collage_path": str(destination.resolve()),
            "ui_status": "画布已裁；16 帧均在已播放区间", "source": "played_recording",
            "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_json(existing, meta)
    return meta


def prepare(case: Path, selected: set[str] | None = None):
    videos = video_dir()
    flags_by_video: dict[str, dict] = {}
    ocr_by_video: dict[str, dict] = {}
    for row in rows(case):
        rid = row["row_id"]
        if selected and rid not in selected:
            continue
        started = time.monotonic()
        try:
            video = row["video"]
            if video not in flags_by_video:
                flags_by_video[video] = read_ocr_flags(case, video)
                ocr_by_video[video] = ocr_index(case, video)
            meta = prepare_one(row, case, videos, flags_by_video[video], ocr_by_video[video])
            print(f"prepare {rid} {meta['page_mode']} {time.monotonic() - started:.1f}s", flush=True)
        except Exception as exc:
            print(f"prepare {rid} ERROR {type(exc).__name__}: {exc}", flush=True)


def _base(row: dict, meta: dict, model: str) -> dict:
    return {"row_id": row["row_id"], "video_type": row["plaintiff"]["video_type"],
            "model": model, "collage_path": meta["collage_path"], "ui_status": meta["ui_status"],
            **{field: "" for field in FIELDS}, "elapsed_seconds": 0, "peak_resident_mb": 0,
            "peak_vram_mb": 0, "status": "error", "error": "", "description_raw": "",
            "geometry_ratio": (meta.get("geometry") or {}).get("ratio", "不确定"), "flags": []}


def _layout_match(layout: str, raw_ratio: float | None) -> bool:
    if raw_ratio is None:
        return False
    if layout == "全屏":
        return raw_ratio >= .8
    if layout in ("原剧缩小加背景或边框", "画中画(原剧为主)", "画中画(UP主为主)"):
        return raw_ratio < .8
    return False


def normalize_local_description(description: str, mapped: dict) -> dict:
    """Correct text-mapper slips only when the visual prose is explicit."""
    value = dict(mapped)
    inset = bool(re.search(r"小窗|画中画|嵌入|分屏", description)) and not bool(
        re.search(r"无(?:额外)?小窗|没有小窗|无画中画|没有画中画|无分屏", description))
    if inset and re.search(r"主播|UP主|真人|主持", description):
        value["layout"] = ("画中画(原剧为主)" if re.search(r"主播.{0,12}小窗|UP主.{0,12}小窗", description)
                           else "画中画(UP主为主)")
        value["form_guess"] = "reaction"
    elif not inset and re.search(r"无额外边框或背景|整体为电视剧原画|四画面均为古装剧|整屏.*(?:古装剧|电视剧)", description):
        value["layout"] = "全屏"
    if re.search(r"无现代元素或梗图|无现代元素或跨剧画面|无跨剧画面|无其他作品|没有其他作品|无.*其他影视", description) and not re.search(
            r"出现现代|有现代|包含现代|另有其他作品|出现其他作品|另一部.*剧", description):
        value["other_works"] = "否"
        value["other_works_desc"] = ""
    return value


def local_one(row: dict, meta: dict) -> dict:
    started = time.monotonic()
    result = _base(row, meta, MODEL)
    try:
        if meta["source"] != "played_recording":
            result["status"] = "skipped"
            result["error"] = "台账无已播放区间；不做本地推理"
            return result
        collage_path = Path(meta["collage_path"])
        response = ask_visual(MODEL, collage_path)
        description = response["message"]["content"].strip()
        if not description:
            raise ValueError("empty visual description")
        mapped, conflicts, mapper_response = map_description(description)
        mapped = normalize_local_description(description, mapped)
        contradictions = list(dict.fromkeys(conflicts + detect_description_conflicts(description)))
        for field in contradictions:
            if field in ("layout", "rights_content_ratio", "uploader_on_camera", "other_works", "form_guess"):
                mapped[field] = "不确定"
            elif field == "overlays":
                mapped[field] = []
        geo = meta["geometry"]
        visual_ratio = mapped["rights_content_ratio"]
        mapped["rights_content_ratio"] = geo["ratio"]
        warnings = []
        if not _layout_match(mapped["layout"], geo["raw_ratio"]):
            warnings.append(f"画面布局与几何待核：模型 {mapped['layout']}／{visual_ratio}，几何 {geo['ratio']}")
        if contradictions:
            warnings.append("描述自相矛盾：" + ",".join(contradictions))
        result.update({field: json.dumps(mapped[field], ensure_ascii=False) if isinstance(mapped[field], list)
                       else mapped[field] for field in FIELDS})
        result.update({"status": "ok", "error": "; ".join(warnings), "flags": warnings,
                       "description_raw": description, "mapper_raw": mapper_response["message"]["content"],
                       "geometry": geo, "background_visual": mapped["layout"] == "原剧缩小加背景或边框"})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        try:
            peak, vram = resident(MODEL)
            result["peak_resident_mb"] = round(peak / 1e6, 1)
            result["peak_vram_mb"] = round(vram / 1e6, 1)
        except Exception:
            pass
    return result


def _csv(path: Path, records: list[dict], columns: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    os.replace(tmp, path)


def local(case: Path, selected: set[str] | None = None):
    video_dir()
    for row in rows(case):
        rid = row["row_id"]
        if selected and rid not in selected:
            continue
        target = R3 / rid
        meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        path = target / "local.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("status") == "ok":
            continue
        result = local_one(row, meta)
        atomic_json(path, result)
        summarize_local()
        print(f"local {rid} {result['status']} {result['elapsed_seconds']}s {result['error']}", flush=True)
    summarize_local()


def summarize_local():
    records = [json.loads(path.read_text(encoding="utf-8")) for path in R3.glob("*/local.json")]
    records.sort(key=lambda r: int(r["row_id"]))
    _csv(OUTPUT / "predictions_r3.csv", records, COLUMNS_R1)
    good = [r for r in records if r["status"] == "ok"]
    summary = {"rows": len(records), "ok": len(good), "failed": [r["row_id"] for r in records if r["status"] == "error"],
               "skipped": [r["row_id"] for r in records if r["status"] == "skipped"],
               "yellow": sum(any(flag.startswith("画面布局与几何待核") for flag in r.get("flags", [])) for r in good),
               "mean_seconds_per_row": round(statistics.mean(float(r["elapsed_seconds"]) for r in good), 2) if good else None,
               "total_seconds": round(sum(float(r["elapsed_seconds"]) for r in good), 2)}
    atomic_json(OUTPUT / "summary_r3_local.json", summary)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def cloud_one(row: dict, meta: dict, model: str) -> dict:
    env, endpoint = CLOUDS[model]
    key = os.environ.get(env)
    if not key:
        raise RuntimeError(f"{env} absent; launch with zsh -lic")
    result = _base(row, meta, model)
    result.update({"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                   "total_tokens": 0, "cost_usd": "", "cost_basis": "账单待核", "attempts": 0})
    encoded = base64.b64encode(Path(meta["collage_path"]).read_bytes()).decode("ascii")
    image = {"url": f"data:image/jpeg;base64,{encoded}"}
    if model == "deepseek-flash":
        image["detail"] = "high"
    payload = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": image}, {"type": "text", "text": PROMPT}]}],
        "temperature": 0}
    started = time.monotonic()
    requested_at = datetime.now(timezone.utc)
    result["requested_at"] = requested_at.isoformat()
    for attempt in range(1, 4):
        result["attempts"] = attempt
        request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                          method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                data = json.load(response)
            usage = data.get("usage") or {}
            result["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            result["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            details = usage.get("completion_tokens_details") or {}
            result["reasoning_tokens"] += int(details.get("reasoning_tokens") or 0)
            result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
            if model == "deepseek-flash":
                peak = requested_at.weekday() < 5 and (1 <= requested_at.hour < 4 or 6 <= requested_at.hour < 10)
                input_rate, output_rate = ((.30, 1.20) if peak else (.15, .60))
                result["cost_usd"] = round((result["prompt_tokens"] * input_rate +
                                            result["completion_tokens"] * output_rate) / 1_000_000, 6)
                result["cost_basis"] = "DeepSeek 官网未命中缓存牌价估算；实际账单可能更低"
            message = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            mapped = validate(_extract_json(message))
            result.update({field: json.dumps(mapped[field], ensure_ascii=False) if isinstance(mapped[field], list)
                           else mapped[field] for field in FIELDS})
            result.update({"status": "ok", "error": "", "description_raw": message})
            break
        except HTTPError as exc:
            result["error"] = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:250]}"
        except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:250]}"
        if attempt < 3:
            time.sleep(2 * attempt)
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


def cloud(case: Path, selected: set[str] | None = None):
    if any(not os.environ.get(name) for name, _ in CLOUDS.values()):
        raise RuntimeError("cloud API keys absent; launch with zsh -lic")
    work = []
    for row in rows(case):
        rid = row["row_id"]
        if selected and rid not in selected:
            continue
        meta = json.loads((R3 / rid / "meta.json").read_text(encoding="utf-8"))
        for model in CLOUDS:
            path = R3 / rid / f"{model}.json"
            if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("status") == "ok":
                continue
            work.append((row, meta, model))
    # One pool per provider guarantees no more than four requests each.
    pools = {model: ThreadPoolExecutor(max_workers=4) for model in CLOUDS}
    try:
        futures = {pools[model].submit(cloud_one, row, meta, model): (row["row_id"], model)
                   for row, meta, model in work}
        for future in as_completed(futures):
            rid, model = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"row_id": rid, "model": model, "status": "error", "error": f"{type(exc).__name__}: {exc}",
                          "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                          "total_tokens": 0, "elapsed_seconds": 0, "attempts": 0}
            atomic_json(R3 / rid / f"{model}.json", result)
            summarize_cloud()
            print(f"cloud {model} {rid} {result['status']} {result['elapsed_seconds']}s {result['error']}", flush=True)
    finally:
        for pool in pools.values():
            pool.shutdown(wait=True)
    summarize_cloud()


def summarize_cloud():
    records = [json.loads(path.read_text(encoding="utf-8"))
               for model in CLOUDS for path in R3.glob(f"*/{model}.json")]
    records.sort(key=lambda r: (int(r["row_id"]), r["model"]))
    _csv(OUTPUT / "predictions_cloud_full.csv", records, CLOUD_COLUMNS)
    summary = {model: {"rows": len(group), "ok": sum(r["status"] == "ok" for r in group),
                       "failed": [r["row_id"] for r in group if r["status"] != "ok"],
                       "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in group),
                       "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in group),
                       "reasoning_tokens": sum(int(r.get("reasoning_tokens") or 0) for r in group),
                       "mean_seconds_per_row": round(statistics.mean(float(r["elapsed_seconds"]) for r in group), 2) if group else None}
               for model in CLOUDS for group in [[r for r in records if r["model"] == model]]}
    atomic_json(OUTPUT / "summary_r3_cloud.json", summary)


def finalize(case: Path):
    disagreements = []
    consensus = []
    for row in rows(case):
        rid = row["row_id"]
        meta = json.loads((R3 / rid / "meta.json").read_text(encoding="utf-8"))
        a = json.loads((R3 / rid / "glm-5.3-flash.json").read_text(encoding="utf-8"))
        b = json.loads((R3 / rid / "deepseek-flash.json").read_text(encoding="utf-8"))
        local_path = R3 / rid / "local.json"
        loc = json.loads(local_path.read_text(encoding="utf-8")) if local_path.exists() else {}
        item = _base(row, meta, "三方定值")
        flags = []
        if a["status"] != "ok" or b["status"] != "ok":
            flags.append("云端判断失败，需核对")
        else:
            for field in ("layout", "rights_content_ratio", "uploader_on_camera", "other_works", "overlays"):
                same = (set(json.loads(a[field])) == set(json.loads(b[field])) if field == "overlays"
                        else a[field] == b[field])
                if not same:
                    disagreements.append({"row_id": rid, "item": field, "glm_value": a[field],
                                          "deepseek_value": b[field], "collage_path": meta["collage_path"]})
            geo = meta.get("geometry") or {}
            if a["layout"] == b["layout"] and _layout_match(a["layout"], geo.get("raw_ratio")):
                item["layout"] = a["layout"]
                item["rights_content_ratio"] = geo["ratio"]
            else:
                flags.append(f"布局／比例待核：GLM {a['layout']}／{a['rights_content_ratio']}，"
                             f"DeepSeek {b['layout']}／{b['rights_content_ratio']}，几何 {geo.get('ratio','无')}")
                item["layout"] = "不确定"
                item["rights_content_ratio"] = "不确定"
            for field in ("uploader_on_camera", "other_works"):
                if a[field] == b[field]:
                    item[field] = a[field]
                else:
                    item[field] = "不确定"
                    flags.append(f"{field} 待核：GLM {a[field]}，DeepSeek {b[field]}")
            if set(json.loads(a["overlays"])) == set(json.loads(b["overlays"])):
                item["overlays"] = a["overlays"]
            else:
                # Section 2 requires yellow flags for layout, presence, and
                # other works. Overlay differences go to disagreements.csv;
                # retain only mutually observed overlays in the adopted value.
                item["overlays"] = json.dumps(sorted(set(json.loads(a["overlays"])) & set(json.loads(b["overlays"]))), ensure_ascii=False)
            item["other_works_desc"] = a["other_works_desc"] if item["other_works"] == "是" else ""
            item["form_guess"] = a["form_guess"] if a["form_guess"] == b["form_guess"] else "不确定"
            item["evidence_frames"] = a["evidence_frames"]
            item["status"] = "ok"
        item["flags"] = flags
        item["error"] = "; ".join(flags)
        item["geometry"] = meta.get("geometry")
        item["background_visual"] = item["layout"] == "原剧缩小加背景或边框"
        item["local_result"] = loc
        consensus.append(item)
        atomic_json(R3 / rid / "consensus.json", item)
    _csv(OUTPUT / "disagreements.csv", disagreements,
         ["row_id", "item", "glm_value", "deepseek_value", "collage_path"])
    _csv(OUTPUT / "predictions_consensus_r3.csv", consensus, COLUMNS_R1)
    modes = Counter(json.loads((R3 / r["row_id"] / "meta.json").read_text(encoding="utf-8"))["page_mode"] for r in rows(case))
    summary = {"rows": len(consensus), "page_modes": dict(modes),
               "yellow_rows": sum(bool(r["flags"]) for r in consensus),
               "yellow_played_rows": sum(bool(r["flags"]) and r["row_id"] not in NO_PLAY for r in consensus),
               "yellow_layout_rows": sum(any(f.startswith("布局／比例待核") for f in r["flags"]) for r in consensus),
               "disagreement_items": len(disagreements), "disagreement_rows": len({d["row_id"] for d in disagreements}),
               "ab_no_examples": {rid: next(r for r in consensus if r["row_id"] == rid)["rights_content_ratio"] == "约1"
                                  for rid in ("1127", "1175") if any(r["row_id"] == rid for r in consensus)}}
    atomic_json(OUTPUT / "summary_r3.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "local", "cloud", "finalize", "summarize"])
    parser.add_argument("case", type=Path)
    parser.add_argument("--rows", nargs="*", type=str)
    args = parser.parse_args()
    case = args.case.resolve()
    selected = set(args.rows) if args.rows else None
    if args.command == "prepare":
        prepare(case, selected)
    elif args.command == "local":
        local(case, selected)
    elif args.command == "cloud":
        cloud(case, selected)
    elif args.command == "finalize":
        finalize(case)
    else:
        summarize_local()
        summarize_cloud()


if __name__ == "__main__":
    main()
