"""One recording → timeline.json (WO-102 §2). See the package docstring for the steps."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from pilot.ledger.pipeline import compile_ocr, parallel_ocr
from pilot.semantic import llm

from . import cloud as C
from . import frames as F
from . import local as Lc
from . import segments as S

VERSION = "wo102-6"
TOP_BAND = [0, 0, 1, 0.2]           # OCR'd again at 2× for the AI label on the top edge (WO-102-R2 A4)
SCENE_DIFF = 12.0                  # 64×36 grey mean absolute difference: below this a frame is the same scene
llm.LOCAL_ALLOWED = False          # D-057 ④: the local vision model never decides a frame (and Ollama is Codex's)


def fingerprint(video: Path, span, step, rules: dict, upload: bool, backends, anchors, duration) -> str:
    st = video.stat()
    raw = json.dumps([VERSION, video.name, st.st_size, int(st.st_mtime), [round(x, 2) for x in span], step,
                      rules["timeline"], rules["work"], upload, list(backends), list(anchors), duration],
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _flag(level: str, reason: str) -> dict:
    return {"level": level, "reason": reason}


def _ocr_image(img: np.ndarray) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        recs = parallel_ocr([str(path)])
    return recs[0]["items"] if recs else []


def scenes(todo: list[dict], need: list[dict]) -> tuple[list[dict], dict[float, float]]:
    """Consecutive unsettled frames that still look like the first of their run (a lyric video, a static page, a
    talking head) are one scene: only the first goes to the cloud, the rest take its answer. Returns (frames to
    send, {frame t: the scene's first frame t})."""
    pending = {f["t"] for f in need}
    send, scene_of, head, head_thumb = [], {}, None, None
    for f in todo:
        if f["t"] not in pending:
            head = None
            continue
        th = F.thumb(f["path"])
        if head is not None and float(np.mean(np.abs(th - head_thumb))) < SCENE_DIFF:
            scene_of[f["t"]] = head["t"]
        else:
            send.append(f)
            head, head_thumb = f, th
    return send, scene_of


def seed_cache(previous: dict | None, frames: list[dict], digest: str, cache_dir: Path, backends) -> int:
    """Answers already paid for in the last timeline of this recording go into the per-frame cache, when they
    came from the same prompt (same digest; "wo102-2" was the last per-collage version with this prompt)."""
    if not previous or not (previous.get("prompt_digest") == digest or previous.get("version") == "wo102-2"):
        return 0
    paths = {f["t"]: f for f in frames}
    n = 0
    for old in previous.get("frames") or []:
        answers = ((old.get("cloud") or {}).get("answers") or {})
        f = paths.get(old.get("t"))
        if f and all(answers.get(b) for b in backends) and not C.cached(f, digest, cache_dir, backends):
            for b in backends:
                C.store(f, digest, cache_dir, b, answers[b], {"seeded_from": previous.get("version")})
            n += 1
    return n


def choose_platforms(cloud: list[str], backed_by_text: set[str]) -> list[str]:
    """When the two providers name different platforms, the one the frame's words support (D-065 ⑤-2)."""
    if len(cloud) > 1 and (both := set(cloud) & backed_by_text):
        return sorted(both)
    return cloud


def mark_ignored(frames: list[dict], content: tuple[float, float] | None) -> None:
    """Frames that decide nothing (WO-102-R2 A1, A2): outside the actual playback, or B站's login popup."""
    by_t = {f["t"]: f for f in frames}
    for f in frames:
        src = by_t[f["dup_of"]] if f.get("dup_of") else f
        if content and not content[0] - 0.5 <= f["t"] <= content[1] + 0.5:
            f["ignored"] = "内容区间外"
        elif (src.get("local") or {}).get("login_box"):
            f["ignored"] = "B站登录弹窗"


def second_round(send: list[dict], scene_of: dict[float, float], todo: list[dict]) -> tuple[list[dict], dict]:
    """For a scene whose first frame came back unknown, up to three more of its frames, evenly spaced."""
    members: dict[float, list[dict]] = {}
    for f in todo:
        if f["t"] in scene_of:
            members.setdefault(scene_of[f["t"]], []).append(f)
    probes = []
    for head in send:
        group = members.get(head["t"]) or []
        if (head.get("cloud") or {}).get("content") == "unknown" and group:
            picks = sorted({round(i * (len(group) - 1) / 2) for i in range(3)} if len(group) >= 3 else range(len(group)))
            probes += [group[i] for i in picks]
    return probes, members


def votes(members: dict[float, list[dict]], probes: list[dict]) -> dict[float, dict]:
    """{scene head t: label for its unasked frames} when at least two of the probed frames give one class."""
    asked = {f["t"] for f in probes}
    out = {}
    for head, group in members.items():
        got = [f["cloud"] for f in group if f["t"] in asked and f.get("cloud") and f["cloud"]["content"] != "unknown"]
        if not got:
            continue
        top, n = Counter((c["content"], c["form"]) for c in got).most_common(1)[0]
        if n >= 2:
            out[head] = {"content": top[0], "form": top[1], "traffic": [], "face": None, "text": None,
                         "flags": [f"同场景补问 {len(got)} 帧，{n} 帧一致"]}
    return out


def run_video(video: Path, out_dir: Path, rules: dict, *, row: str | None = None, span=None, anchors=(),
              duration: float | None = None, step: float = 10, upload: bool = False, board: Path | None = None,
              legend: str = "", backends=C.BACKENDS, workers: int = 4, force: bool = False, ac_clock: str = "recording",
              cache_dir: Path | None = None, content=None, log=print) -> dict:
    """Timeline of one recording. span/anchors/duration come from the web ledger when it has them; content is
    the ledger's actual playback (content_start, content_end, WO-101-R3): frames outside it — end screen,
    replay, error page — and B站's login popup are kept for the record but decide nothing (WO-102-R2 A1, A2)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "timeline.json"
    info = F.probe(video)
    span_source = "台账" if span else "时间线模块按画面变化粗估（台账未提供）"
    span = tuple(float(x) for x in span) if span else F.detect_span(video, info)
    cspan = (float(content[0]), float(content[1])) if content and None not in content else None
    fp = fingerprint(video, span, step, rules, upload, backends, anchors, duration) + ac_clock[0] + json.dumps(cspan)
    previous = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
    if not force and previous and previous.get("fingerprint") == fp:
        return previous
    cache_dir = cache_dir or out_dir.parent / "_frame_cache"
    t0 = time.monotonic()
    flags = [] if span_source == "台账" else [_flag("yellow", f"播放区间为{span_source}")]
    if cspan is None:
        flags.append(_flag("yellow", "台账没有内容区间（content_start–content_end），按播放区间和视频时长推算"))
    player = F.find_player(video, span, info)
    box = player["box"]
    if not player["method"].startswith("默认布局（与"):
        flags.append(_flag("yellow", f"播放器位置：{player['method']}"))
    frames = F.sample(video, span, step, box, out_dir / "frames")
    dups = F.dedupe(frames)

    compile_ocr()                                                         # local screen
    todo = [f for f in frames if not f.get("dup_of")]
    ocr = {r["frame"]: r["items"] for r in parallel_ocr([f["path"] for f in todo])} if todo else {}
    top = {r["frame"]: r["items"] for r in parallel_ocr([f["path"] for f in todo], crop=TOP_BAND, scale=2)} if todo else {}
    screen = Lc.Screen(rules)
    for f in todo:
        name = Path(f["path"]).name
        screen.screen(f, ocr.get(name, []), cv2.imread(f["path"]), top.get(name, []))
    readings = [(f["t"], f["reading"]) for f in todo if f.get("reading")]
    start = cspan[0] if cspan else span[0]
    vclock = S.Clock(start, readings, anchors, duration)                  # the B站 video's own clock
    clock = S.Clock.recording() if ac_clock == "recording" else vclock    # what segments and AC lines use
    if cspan:
        stop = cspan[1]
    else:
        stop = min(span[1], vclock.to_recording(vclock.duration)) if vclock.duration else span[1]
    by_t = {f["t"]: f for f in frames}
    mark_ignored(frames, cspan)
    if ac_clock != "recording":
        flags += [_flag("yellow", x) for x in vclock.flags]

    need = [f for f in todo if not f.get("ignored") and f["local"]["content"] is None and not f["local"]["traffic"]]
    send, scene_of = scenes(todo, need)
    calls = []
    scene_votes: dict[float, dict] = {}
    probes: list[dict] = []
    digest = C.prompt_digest(board, legend) if (upload and board) else None
    if send and upload and board:                                         # cloud, only what is left
        cache = cache_dir
        seed_cache(previous, frames, digest, cache, backends)
        calls = C.label_frames(send, clock, board, legend, out_dir / "cloud_outbox", cache, workers, backends)
        probes, members = second_round(send, scene_of, todo)              # scenes whose first frame stayed unknown
        if probes:
            calls += C.label_frames(probes, clock, board, legend, out_dir / "cloud_outbox", cache, workers, backends)
            scene_votes = votes(members, probes)
    elif send:
        flags.append(_flag("yellow", f"{len(need)} 帧本地判不了，未送云端（{'未授权上传' if not upload else '缺参考图板'}）"))

    for f in frames:
        src = by_t[f["dup_of"]] if f.get("dup_of") else f
        loc, cl = src.get("local") or {}, src.get("cloud")
        if f.get("ignored"):
            f.update(content=None, form="", source=f["ignored"], traffic=[], kinds=[], traffic_evidence=[],
                     face=None, text=None, v=round(vclock(f["t"]), 1))
            continue
        if cl is None and src["t"] in scene_of:
            head = scene_of[src["t"]]
            cl = scene_votes.get(head) or by_t[head].get("cloud")
        tr = loc.get("traffic")
        if loc.get("content"):
            content, form, source = loc["content"], loc.get("form", ""), "本地"
        elif cl:
            content, form, source = cl["content"], cl["form"], "云端" + ("（同场景）" if src["t"] in scene_of else "")
        else:
            content, form, source = (None if tr else "unknown"), "", ("本地" if tr else "未判定")
        cloud_traffic = choose_platforms(list(cl["traffic"]) if cl else [], screen.text_platforms(src.get("ocr") or []))
        f.update(content=content, form=form, source=source + ("（同前一帧）" if f.get("dup_of") else ""),
                 traffic=tr["platforms"] if tr else cloud_traffic,
                 kinds=tr["kinds"] if tr else (["导流信息"] if cl and cl["traffic"] else []),
                 traffic_evidence=tr["evidence"] if tr else (["云端两家都看到导流信息"] if cl and cl["traffic"] else []),
                 face=cl.get("face") if cl else None, text=cl.get("text") if cl else None, v=round(vclock(f["t"]), 1))
    inside = [f for f in frames if not f.get("ignored")]
    S.fill_from_neighbours(inside)

    def probe(a, b):
        ta, tb = F.thumb(a["path"]), F.thumb(b["path"])

        def side(t):
            img = F.grab(video, t, box=box, size=(64, 36))
            if img is None:
                return None
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            return float(np.mean(np.abs(g - ta)) - np.mean(np.abs(g - tb)))
        return side

    def check(t):
        img = F.grab(video, t, box=box)
        if img is None:
            return False
        return screen.traffic(_ocr_image(img), Lc.qr_codes(img)) is not None

    segs = S.segments(inside, clock, stop, probe, start=start) if inside else []
    traffic = S.traffic_intervals(inside, clock, check, step)
    unknown = [s for s in segs if s["content"] == "unknown"]
    if unknown:
        flags.append(_flag("yellow", f"{len(unknown)} 段画面未能判定，请核对：" +
                           "、".join(f"{C.mmss(s['start'])}-{C.mmss(s['end'])}" for s in unknown[:5])))
    for f in todo:
        if f.get("ignored"):
            continue
        for reason in (f.get("cloud") or {}).get("flags", []):
            flags.append(_flag("yellow", f"{C.mmss(clock(f['t']))} {reason}"))
    usage = {k: sum((c.get("usage") or {}).get(k) or 0 for c in calls if not c.get("cached")) for k in ("prompt", "completion", "reasoning")}
    result = {"row": row, "video": video.name, "version": VERSION, "fingerprint": fp, "duration": info["duration"],
              "span": [round(span[0], 2), round(span[1], 2)], "span_source": span_source,
              "content_span": [round(cspan[0], 2), round(cspan[1], 2)] if cspan else None,
              "frames_ignored": dict(Counter(f["ignored"] for f in frames if f.get("ignored"))),
              "clock": {"ac": clock.source, "video_offset": round(vclock.offset, 2), "video_source": vclock.source,
                        "video_duration": vclock.duration, "playback_stop": round(stop, 2), "readings": readings[:20]},
              "player": player, "step": step, "frames_total": len(frames), "frames_duplicate": dups,
              "frames_local": sum(1 for f in todo if not f.get("ignored") and (f["local"]["content"] or f["local"]["traffic"])),
              "frames_cloud": len(send) + len(probes) if (upload and board) else 0, "frames_same_scene": len(scene_of),
              "segments": segs, "traffic": traffic, "ac": S.ac_lines(segs, traffic), "summary": S.summary(inside, segs),
              "flags": flags, "calls": calls, "usage": usage, "seconds": round(time.monotonic() - t0, 1),
              "prompt_digest": digest, "calls_cached": sum(1 for f in send + probes if (f.get("cloud") or {}).get("cached")),
              "frames": [{k: f.get(k) for k in ("t", "v", "content", "form", "source", "traffic", "kinds", "face", "text",
                                                "dup_of", "ignored", "ocr", "captions", "music_tag", "lyric", "ai_note",
                                                "reading", "local", "cloud")}
                         for f in frames]}
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{row or video.name}: {len(frames)} 帧（去重 {dups}，本地定 {result['frames_local']}，云端 {result['frames_cloud']}），"
        f"{len(segs)} 段，引流 {len(traffic)} 处，{result['seconds']} 秒")
    return result
