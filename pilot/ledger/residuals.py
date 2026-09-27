"""Evidence-bounded WO-001-R3 repairs for the qsjx_e18 pilot ledger only.

The reviewed frame windows are deliberately narrow.  They prevent an OCR
correction for one video from being borrowed by a neighboring video with a
similar duration, and leave every other ledger row untouched.
"""
from __future__ import annotations

import csv

from .parse import parse_reading
from .pipeline import evidence_image, has_playback_evidence, playback


def _reading(frame, total, position=None):
    raw_candidates = frame.get("reading_raw_candidates") or [
        item["text"] for item in frame.get("items", [])
        if "/" in item.get("text", "") or "104:48" in item.get("text", "")]
    raw = (raw_candidates or ["人工核对录像帧"])[0]
    if position is None:
        candidates = [item for item in frame["readings"] if item["total"] == total]
        candidates += [item for text in frame.get("reading_raw_candidates", [])
                       if (item := parse_reading(text, {total}, total))]
        if not candidates:
            return None
        value = candidates[0]
    else:
        value = {"position": position, "total": total, "raw": raw,
                 "truncated_total": True, "r3_frame_checked": True}
    if not 0 <= value["position"] <= total:
        return None
    return {"t": frame["t"], "position": value["position"], "total": total,
            "raw": value["raw"], "truncated_total": value.get("truncated_total", False),
            "r3_frame_checked": value.get("r3_frame_checked", False),
            "speed": frame["speed"], "speed_source_frame": frame.get("speed_source_frame"),
            "frame": frame["frame"]}


def _recover(obs, start, end, total):
    """Reparse OCR numerators only inside a visually confirmed player session."""
    found = [_reading(frame, total) for frame in obs if start <= frame["t"] <= end]
    return [item for item in found if item]


def _combine(existing, obs, total, corrections, discard=()):
    found = {item["t"]: item for item in existing if item["t"] not in discard}
    frames = {frame["t"]: frame for frame in obs}
    for second, position in corrections.items():
        frame = frames[float(second)]
        # These exact frames were reviewed in the video.  OCR sometimes loses
        # the separator (e.g. 04:29/04:48 becomes 2:29104:48).
        found[float(second)] = _reading(frame, total, position)
    return [found[t] for t in sorted(found)]


def _recalculate(record, readings, obs, video_path, output_dir, *, blocked=(), rates=None):
    total = record["video_duration"]
    preliminary = playback(readings, total, rate_overrides=rates)
    event_pairs = {(item["frame_start"], item["frame_end"])
                   for item in preliminary["playback_map"] if item["event"] in ("seek", "replay")}
    support = {}
    for left, right in zip(readings, readings[1:]):
        pair = (left["frame"], right["frame"])
        if pair not in event_pairs or right["t"] - left["t"] <= 1:
            continue
        if any(left["t"] <= finish and right["t"] >= begin for begin, finish in blocked):
            support[pair] = False
        else:
            support[pair] = has_playback_evidence(obs, video_path, left, right)
    result = playback(readings, total, support, rate_overrides=rates)
    record.update(result)
    record["readings"] = readings
    dynamic = {"跳播", "UI隐藏区间推定", "跳播前缺少播放证据；未计入时长",
               "倍速标签与斜率冲突；采用斜率", "倍速未知", "无播放器读数，需复核"}
    record["flags"] = [flag for flag in record["flags"] if flag not in dynamic]
    if result["seeks"]:
        record["flags"].append("跳播")
    if result["inferred_segments"]:
        record["flags"].append("UI隐藏区间推定")
    if result["unsupported_gaps"]:
        record["flags"].append("跳播前缺少播放证据；未计入时长")
    if result["speed_conflict"]:
        record["flags"].append("倍速标签与斜率冲突；采用斜率")
    if result["speed"] is None:
        record["flags"].append("倍速未知")

    def proof(item):
        destination = output_dir / "evidence" / f"{record['video']}-{round(item['t']):06d}.jpg"
        evidence_image(video_path, item["t"], destination)
        return {"type": "R3复核录像帧", "video": record["video"], "t": item["t"],
                "frame": item["frame"], "evidence_frame": str(destination.relative_to(output_dir))}

    anchors = [readings[0], readings[-1]]
    by_frame = {item["frame"]: item for item in readings}
    for seek in result["seeks"]:
        anchors.extend(by_frame[name] for name in (seek["frame_before"], seek["frame_after"]))
    anchors.extend(item for item in readings if item.get("r3_frame_checked"))
    sources = [proof(item) for item in dict((item["frame"], item) for item in anchors).values()]
    record["sources"].update({"capture_seconds": sources, "complete": sources,
                              "playback_map": sources, "seeks": sources})
    return record


def apply_qsjx_e18_r3(ledger, obs_by_video, video_paths, output_dir):
    """Apply only the reviewed residual changes; preserve all other rows."""
    rows = {record["row_id"]: record for record in ledger}

    def repair(rid, readings, *, blocked=(), rates=None, note):
        row = rows[rid]
        _recalculate(row, readings, obs_by_video[row["video"]], video_paths[row["video"]],
                     output_dir, blocked=blocked, rates=rates)
        row["r3_review"] = {"disposition": "已修", "reason": note}

    # 1070: /10:55 OCR denominators were clipped, but the same-part player
    # shows 00:35 -> 03:15 and further forward seeks in consecutive frames.
    repair("1070", _recover(obs_by_video["MVI_0002"], 116, 208, 655),
           note="同一分P的截断读数复原；00:35→03:15 等跳转按录像帧重算")

    # The 1111 phone is visibly being scrubbed from 937 to 956 seconds.
    row = rows["1111"]
    repair("1111", [r for r in row["readings"] if not 937 <= r["t"] <= 956],
           blocked=((937, 956),), note="进度条拖动帧不计为逐秒播放")

    # Direct player button shows the 1147 rate switching to 1.5x at 662;
    # the 671-774 recording span advances 154 content seconds without a seek.
    row = rows["1147"]
    readings = [r for r in row["readings"] if r["t"] != 663]
    rates = {(left["frame"], right["frame"]): 1.5 for left, right in zip(readings, readings[1:])
             if left["t"] == 671 and right["t"] == 774}
    repair("1147", readings, rates=rates, note="前段约1.0倍；662秒按钮改为1.5倍，长间隙按1.5倍连续播放")
    row["speed"] = 1.5
    row["speed_conflict"] = False
    row["flags"] = [flag for flag in row["flags"]
                    if flag != "倍速标签与斜率冲突；采用斜率"]
    row["speed_segments"] = [
        {"video_start": 655, "video_end": 660, "speed": 1.0, "basis": "655→657秒连续读数斜率"},
        {"video_start": 662, "video_end": 790, "speed": 1.5, "basis": "662、670、774秒播放器按钮及671→774秒读数斜率"},
    ]
    row["speed_reason"] = "录像662秒切到1.5倍；671→774秒读数由00:28前进至03:02，斜率约1.50。"

    # 1115 has its own BV, 1/2 marker, 16:14 total and 18 consecutive
    # recovered readouts between neighboring sessions, not merely a screenshot.
    row = rows["1115"]
    row.update({"video_start": 1783.0, "video_end": 1879.0, "video_duration": 974,
                "session_id": "MVI_0004-R3-1115", "same_session_rows": ["1115"],
                "association": {"basis": "BV+分P+同总长连续读数", "confidence": "high",
                                "source": "R3 OCR缓存/截图"}})
    repair("1115", _recover(obs_by_video["MVI_0004"], 1783, 1864, 974),
           note="跨录像闭集筛选后，MVI_0004 1783–1879秒为本行独立1/2会话；仅计可确认播放片段")

    # OCR readouts 991-993 show 04:03, 04:04, 04:06 / 12:48 on moving video.
    row = rows["1127"]
    repair("1127", _combine(row["readings"], obs_by_video["MVI_0005"], 768,
                            {991: 243, 992: 244, 993: 246}),
           note="991–993秒连续递增的04:03–04:06读数；分母OCR误作32:48")

    # At 565 seconds the image shows 00:39/01:18, not OCR's 00:20;
    # 564->583 then advances 39->57 with changing video frames.
    row = rows["1169"]
    repair("1169", [r for r in row["readings"] if r["t"] != 565],
           note="565秒00:20为OCR误读，564→583秒39→57连续播放")

    row = rows["1199"]
    repair("1199", _combine(row["readings"], obs_by_video["MVI_0008"], 86,
                            {763: 23, 764: 24, 765: 25, 794: 54,
                             796: 56, 797: 57, 798: 58}, discard=(767,)),
           note="01:26总长由742、766、788、798秒完整读数确认；修复夹在其间的截断分母")

    row = rows["1219"]
    repair("1219", _combine(row["readings"], obs_by_video["MVI_0008"], 288,
                            {3022: 17, 3023: 18, 3032: 174, 3038: 268,
                             3039: 269, 3041: 270}),
           note="04:48总长由3012、3024、3031、3034秒确认；3041秒原片实为04:30")

    # These three screenshots establish video identity, but neighboring
    # near-equal-total rows currently own the merged readout sessions.  Reusing
    # those readings would double-count and change protected neighboring rows.
    for rid, neighbor in (("1133", "1134"), ("1194", "1193"), ("1197", "1198")):
        row = rows[rid]
        row["flags"].append(f"R3待复核：与{neighbor}同总长近似、读数会话混合；时长未知")
        row["r3_review"] = {"disposition": "标黄", "reason":
                            f"截图和BV/标题可定位，但播放器读数与{neighbor}混在同一会话；不能安全重用"}

    unchanged = {
        "1122": "仅一处区间外读数，缺少连续播放证据",
        "1124": "仅一处区间外读数，缺少连续播放证据",
        "1126": "区间外虽有连续读数，录像中手指正拖动进度条",
        "1128": "区间外读数出现于拖动进度条时",
        "1167": "区间外读数为跳转闪读；倍速沿用已核按钮/连续片段口径",
        "1181": "区间外读数出现于拖动进度条时",
        "1186": "疑似漏读时点落在原有已播放区间内",
        "1218": "区间外读数属于离线缓存/文件页缩略图，非播放器连续播放",
        "1168": "倍速差异暂维持原判；短斜率不能排除缓冲或跳转",
        "1170": "倍速差异暂维持原判；短斜率不能排除缓冲或跳转",
        "1177": "倍速差异暂维持原判；短斜率不能排除缓冲或跳转",
        "1184": "播放器按钮及连续读数支持原判1.5倍",
        "1187": "倍速差异暂维持原判；短斜率不能排除缓冲或跳转",
        "1191": "倍速差异暂维持原判；短斜率不能排除缓冲或跳转",
        "1215": "未见可靠按钮；48秒连续读数斜率约1.0，维持原判",
    }
    for rid, reason in unchanged.items():
        rows[rid]["r3_review"] = {"disposition": "维持", "reason": reason}
    reviewed = {rid: row for rid, row in rows.items() if "r3_review" in row}
    with (output_dir / "r3_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["行", "结论", "理由", "取证时长秒", "完整播放"])
        for rid in sorted(reviewed, key=int):
            row = reviewed[rid]
            writer.writerow([rid, row["r3_review"]["disposition"], row["r3_review"]["reason"],
                             row["capture_seconds"], row["complete"]])
    return ledger
