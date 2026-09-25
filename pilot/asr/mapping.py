"""Map actual recorded playback to accused-video positions without undoing speed."""
from __future__ import annotations


def _add_clipped(result, rec_start, rec_end, content_start, content_end, played, event):
    if rec_end <= rec_start or content_end <= content_start:
        return
    for a, b in played:
        lo, hi = max(content_start, a), min(content_end, b)
        if hi <= lo:
            continue
        scale = (rec_end - rec_start) / (content_end - content_start)
        result.append({"recording_start": rec_start + (lo - content_start) * scale,
                       "recording_end": rec_start + (hi - content_start) * scale,
                       "content_start": lo, "content_end": hi, "event": event})


def playback_segments(row):
    """Only time covered by played_intervals and a playback_map edge may be transcribed."""
    played = row.get("played_intervals") or []
    if not played:
        return []
    result = []
    seeks = {round(item["at"], 2): item for item in row.get("seeks", [])}
    unsupported = {(round(item["start"], 2), round(item["end"], 2))
                   for item in row.get("unsupported_gaps", [])}
    for item in row.get("playback_map", []):
        start, end = item["video_start"], item["video_end"]
        c_start, c_end = item["content_start"], item["content_end"]
        event = item["event"]
        if event == "play":
            _add_clipped(result, start, end, c_start, c_end, played, event)
        elif event == "seek":
            seek = seeks.get(round(end, 2))
            if seek and seek["from"] > c_start and end - start > 1:
                _add_clipped(result, start, end - 1, c_start, seek["from"], played, event)
        elif event == "replay" and end - start > 1 and (round(start, 2), round(end, 2)) not in unsupported:
            before_end = min(c_start + item["speed"] * (end - start - 1), row["video_duration"])
            _add_clipped(result, start, end - 1, c_start, before_end, played, event)
    for item in row.get("inferred_segments", []):
        if item.get("edge") == "start" and row.get("readings"):
            _add_clipped(result, item["start"], item["end"], 0,
                         row["readings"][0]["position"], played, "inferred_start")
        elif item.get("edge") == "end" and row.get("readings"):
            _add_clipped(result, item["start"], item["end"],
                         row["readings"][-1]["position"], row["video_duration"],
                         played, "inferred_end")
    start_limit, end_limit = row.get("video_start"), row.get("video_end")
    bounded = []
    for item in result:
        lo, hi = max(item["recording_start"], start_limit), min(item["recording_end"], end_limit)
        if hi <= lo:
            continue
        slope = (item["content_end"] - item["content_start"]) / (
            item["recording_end"] - item["recording_start"])
        bounded.append({**item, "recording_start": round(lo, 3), "recording_end": round(hi, 3),
                        "content_start": round(item["content_start"] +
                                               (lo - item["recording_start"]) * slope, 3),
                        "content_end": round(item["content_end"] -
                                             (item["recording_end"] - hi) * slope, 3)})
    return sorted(bounded, key=lambda item: (item["recording_start"], item["recording_end"]))


def recording_spans(segments):
    """Coalesce adjacent play edges; keep seek and replay boundaries separate."""
    spans = []
    for item in segments:
        start, end = item["recording_start"], item["recording_end"]
        if not spans or start - spans[-1]["end"] > .25 or (
                abs(item["content_start"] - spans[-1]["last_content_end"]) > 3):
            spans.append({"start": start, "end": end,
                          "last_content_end": item["content_end"]})
        else:
            spans[-1]["end"] = max(spans[-1]["end"], end)
            spans[-1]["last_content_end"] = item["content_end"]
    return [{"start": span["start"], "end": span["end"]} for span in spans]


def audio_chunks(spans, block=40.0, overlap=2.0):
    chunks = []
    for span_no, span in enumerate(spans):
        start = span["start"]
        while start < span["end"] - .01:
            end = min(start + block, span["end"])
            chunks.append({"start": round(start, 3), "end": round(end, 3),
                           "span": span_no, "number": len(chunks)})
            if end >= span["end"] - .01:
                break
            start = end - overlap
    return chunks


def content_at(segments, recording_second):
    containing = [item for item in segments if item["recording_start"] - .05 <= recording_second <=
                  item["recording_end"] + .05]
    if not containing:
        return None
    item = min(containing, key=lambda item: abs(
        recording_second - (item["recording_start"] + item["recording_end"]) / 2))
    fraction = (recording_second - item["recording_start"]) / max(
        .001, item["recording_end"] - item["recording_start"])
    return round(item["content_start"] + max(0, min(1, fraction)) *
                 (item["content_end"] - item["content_start"]), 3)
