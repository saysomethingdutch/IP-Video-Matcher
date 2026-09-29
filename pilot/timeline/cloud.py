"""Cloud cross-check of the frames the local screen could not settle (WO-102 §2.3, D-033, D-057 ③).

Four player crops go into one 2×2 collage, each tile marked "1 01:20" (index and video time); a reference
board goes with it: the guidance pictures (films, 2D and 3D mobile games) and a few series frames with the
official logo. GLM and DeepSeek get the same prompt and pictures; a frame's content counts only when both
give the same class, otherwise it stays "unknown" and is flagged. The prompt never names the case or what it
is for (target-blind), and never gives the video title (it would pull every frame towards 吞噬星空).

Only player crops are sent (D-057 ③: for accuracy — the sidebar thumbnails would read as other works). Every
picture sent is written to cloud_outbox/ first. Answers are cached per frame (by the frame picture, the prompt
and the board), so a rerun, a shorter play span or a regrouped collage costs nothing again (WO-102-R1 §0).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from pilot.semantic import llm

TILE = (640, 360)
REF_TILE = (384, 216)
BACKENDS = ("zhipu", "deepseek")
NAMES = {"zhipu": "GLM", "deepseek": "DeepSeek"}
CONTENT = ("tsxk", "tsxk_other", "creator", "unrelated", "blank", "unknown")
FORMS = ("漫画", "动画剧场版", "手游", "小说", "有声书")
TRAFFIC = ("none", "tencent", "other")

PROMPT = """你在帮忙整理一批网络视频的画面内容。

第一张图是参考图板：
{legend}

第二张图是同一个视频按时间先后取的 {n} 格画面（只截了播放器），每格左上角标了序号和时间。
请逐格判断画面主要是哪一类：
- tsxk：《吞噬星空》动画（正片、官方预告、官方海报或剧照，以及它们的录屏、截屏、翻拍）；画中画里有这部动画的也算；原片画面上叠了字幕、水印、弹幕、贴纸、边框，或者缩小、调了透明度的，仍然算 tsxk
- tsxk_other：《吞噬星空》的其他形式——漫画、动画剧场版、手游、小说或有声书的文字画面；form 填其中一种
- creator：画面主体是视频作者自己做的内容，例如作者本人出镜点评、自制封面或片头、说明文字页；只有明显是 AI 生成的新画面（画风、质感与参考图的正片明显不同，或画面注明 AI）才算 AI 画面，拿不准的 3D 动画按与参考图是否一致判断
- unrelated：与《吞噬星空》无关的内容，例如其他动画或影视、音乐 MV、歌词页或音乐播放器画面、其他游戏、广告带货、App 或网页截图、无关图片
- blank：黑屏或加载中
- unknown：只在画面看不清、确实无法判断时使用；能看出与《吞噬星空》无关的，一律填 unrelated
另外：
- traffic：画面里有网盘链接、二维码、群号、网址、公众号之类的导流信息时，微信、QQ、腾讯微云填 tencent，其他填 other；没有填 none。
- face：画面里有没有真人出镜（true/false）。
- text：画面上有没有作者加的解说字幕或大段文字（true/false）。
注意：
- 画面上飘过的弹幕文字、作者水印和"二次创作"之类的声明，不影响对画面内容的判断。
- 《斗破苍穹》《完美世界》《斗罗大陆》《凡人修仙传》《仙逆》等国漫画风相近，人物、场景对不上参考图时，不要判成 tsxk；只根据画面判断，不要猜。

只输出 JSON：{{"frames": [{{"i": 1, "content": "…", "form": "", "traffic": "none", "face": false, "text": false, "why": "不超过20字"}}]}}"""


def mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def _mark(img: np.ndarray, text: str, scale: float = 1.0) -> np.ndarray:
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.9 * scale, 2)
    cv2.rectangle(img, (0, 0), (w + 16, h + 16), (0, 0, 0), -1)
    cv2.putText(img, text, (8, h + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9 * scale, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def _fit(img: np.ndarray, size) -> np.ndarray:
    """Letterbox into size without distortion."""
    w, h = size
    s = min(w / img.shape[1], h / img.shape[0])
    small = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
    out = np.full((h, w, 3), 40, np.uint8)
    y, x = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2
    out[y:y + small.shape[0], x:x + small.shape[1]] = small
    return out


def reference_board(rules: dict, guidance_dir: Path, series: list[Path], out: Path) -> tuple[Path, str]:
    """R1… guidance pictures, S1… series frames, three to a row; returns (path, legend for the prompt)."""
    tiles, legend = [], []
    for i, ref in enumerate(rules["work"]["references"], 1):
        img = cv2.imdecode(np.fromfile(str(guidance_dir / ref["file"]), np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            tiles.append(_mark(_fit(img, REF_TILE), f"R{i}", 0.8))
            legend.append(f"R{i}：{ref['label']}")
    for i, path in enumerate(series, 1):
        img = cv2.imread(str(path))
        if img is not None:
            tiles.append(_mark(_fit(img, REF_TILE), f"S{i}", 0.8))
            legend.append(f"S{i}：《吞噬星空》动画正片（3D 国漫，右下角有官方台标）")
    while len(tiles) % 3:
        tiles.append(np.full((REF_TILE[1], REF_TILE[0], 3), 40, np.uint8))
    board = np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)])
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), board, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out, "\n".join(legend)


def collage(paths: list[str], times: list[float], out: Path) -> Path:
    tiles = [_mark(_fit(cv2.imread(p), TILE), f"{i} {mmss(t)}") for i, (p, t) in enumerate(zip(paths, times), 1)]
    while len(tiles) < 4:
        tiles.append(np.full((TILE[1], TILE[0], 3), 90, np.uint8))
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out


def parse(data: dict | None, n: int) -> list[dict] | None:
    """The n per-tile answers in order, normalised; None when the reply does not cover every tile."""
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        return None
    got = {}
    for f in data["frames"]:
        try:
            i = int(f.get("i"))
        except (TypeError, ValueError):
            continue
        content = str(f.get("content") or "unknown").strip()
        form = str(f.get("form") or "").strip()
        traffic = str(f.get("traffic") or "none").strip().lower()
        got[i] = {"content": content if content in CONTENT else "unknown",
                  "form": next((x for x in FORMS if x in form), "") if content == "tsxk_other" else "",
                  "traffic": traffic if traffic in TRAFFIC else "none",
                  "face": f.get("face") is True, "text": f.get("text") is True, "why": str(f.get("why") or "")[:40]}
    return [got[i] for i in range(1, n + 1)] if all(i in got for i in range(1, n + 1)) else None


def cross(answers: dict[str, dict | None]) -> dict:
    """One frame's label from the two answers: adopted only when both give the same content."""
    a, b = (answers.get(k) for k in BACKENDS)
    out = {"content": "unknown", "form": "", "traffic": [], "face": None, "text": None, "flags": []}
    if not a or not b:
        missing = "、".join(NAMES[k] for k in BACKENDS if not answers.get(k))
        out["flags"].append(f"云端：{missing} 没有给出可用的回答")
        return out
    pair = {a["content"], b["content"]}
    film_or_series = pair == {"tsxk", "tsxk_other"} and \
        all(x["form"] in ("", "动画剧场版") for x in (a, b) if x["content"] == "tsxk_other")
    if film_or_series:                                          # D-065 ⑤-1: animation either way (T still needs the watermark)
        out["content"] = "tsxk"
        out["flags"].append("两家都认出吞噬星空动画，正片还是剧场版说法不一，按动画画面计")
    elif a["content"] == b["content"]:
        out["content"] = a["content"]
        if a["content"] == "tsxk_other":
            out["form"] = a["form"] if a["form"] == b["form"] else ""
            if a["form"] != b["form"]:
                out["flags"].append(f"云端：同为吞噬星空其他形式，形式不一致（GLM {a['form'] or '未写'}，DeepSeek {b['form'] or '未写'}）")
    else:
        out["flags"].append(f"云端两家不一致：GLM 判 {a['content']}，DeepSeek 判 {b['content']}")
    if a["traffic"] != "none" and b["traffic"] != "none":
        out["traffic"] = sorted({a["traffic"], b["traffic"]})
    elif a["traffic"] != b["traffic"]:
        out["flags"].append("云端：只有一家看到导流信息，未采用")
    out["face"] = a["face"] if a["face"] == b["face"] else None
    out["text"] = a["text"] if a["text"] == b["text"] else None
    return out


class Throttle:
    """At most `high` calls at a time to one provider, across every process that points at the same slot
    directory (the 400-row run, the check set and the samples may run together); after a rate-limit reply
    (HTTP 429) at most `low` for everyone (D-058 ③: 8 at once, 4 when throttled). Slots are lock files held
    with flock, so a crashed process frees its slot. Without a slot directory the limit is per process."""

    def __init__(self, backend: str, high: int = 8, low: int = 4, slot_dir: Path | None = None):
        self.backend, self.high, self.low = backend, high, low
        self.slot_dir = slot_dir
        self.cap_local, self.active = high, 0
        self.cond = threading.Condition()
        self.held = threading.local()

    def cap(self) -> int:
        if self.slot_dir is None:
            return self.cap_local
        f = self.slot_dir / f"{self.backend}.cap"
        try:
            return max(1, min(self.high, int(f.read_text().strip())))
        except (OSError, ValueError):
            return self.high

    def __enter__(self):
        if self.slot_dir is None:
            with self.cond:
                while self.active >= self.cap_local:
                    self.cond.wait()
                self.active += 1
            return self
        self.slot_dir.mkdir(parents=True, exist_ok=True)
        while True:
            for i in range(self.cap()):
                fd = os.open(self.slot_dir / f"{self.backend}.{i}.lock", os.O_CREAT | os.O_RDWR, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    continue
                self.held.fd = fd
                return self
            time.sleep(0.5)

    def __exit__(self, *exc):
        if self.slot_dir is None:
            with self.cond:
                self.active -= 1
                self.cond.notify_all()
            return
        fd = getattr(self.held, "fd", None)
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            self.held.fd = None

    def limited(self) -> None:
        if self.slot_dir is None:
            with self.cond:
                self.cap_local = min(self.cap_local, self.low)
            return
        (self.slot_dir / f"{self.backend}.cap").write_text(str(self.low))


THROTTLE = {b: Throttle(b) for b in BACKENDS}


def share_slots(slot_dir: Path) -> None:
    """Make every provider's limit count across processes (call once at start-up)."""
    for b in BACKENDS:
        THROTTLE[b] = Throttle(b, slot_dir=slot_dir)


def prompt_digest(board: Path, legend: str) -> str:
    """What an answer depends on besides the frame itself: the prompt and the reference board."""
    h = hashlib.sha256(PROMPT.format(legend=legend, n=4).encode("utf-8"))
    h.update(Path(board).read_bytes())
    return h.hexdigest()[:16]


def frame_key(path: str, digest: str) -> str:
    return hashlib.sha256(Path(path).read_bytes() + digest.encode("ascii")).hexdigest()[:24]


def cached(frame: dict, digest: str, cache_dir: Path, backends=BACKENDS) -> dict | None:
    """Both providers' earlier answers for this very picture, or None. Answers are kept per frame, so a
    shorter play span or a new grouping of the collages costs nothing again (WO-102-R1 §0)."""
    key, out = frame_key(frame["path"], digest), {}
    for b in backends:
        p = cache_dir / f"{key}.{b}.json"
        if not p.exists():
            return None
        out[b] = json.loads(p.read_text(encoding="utf-8"))["answer"]
    return out


def store(frame: dict, digest: str, cache_dir: Path, backend: str, answer: dict, call: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{frame_key(frame['path'], digest)}.{backend}.json").write_text(
        json.dumps({"answer": answer, "call": call}, ensure_ascii=False), encoding="utf-8")


def ask_group(board: Path, legend: str, picture: Path, n: int,
              backends=BACKENDS) -> tuple[dict[str, list[dict] | None], list[dict]]:
    """Both providers on one collage, in parallel, each under its throttle: ({backend: answers}, [calls])."""
    prompt = PROMPT.format(legend=legend, n=n)

    def call(backend):
        """One provider call. A malformed reply that breaks the adapter (an empty JSON body under load) counts
        as no answer for these frames — they stay unknown and flagged — instead of failing the whole row."""
        try:
            with THROTTLE[backend]:
                return llm.ask(backend, prompt, images=[board, picture], required=("frames",), timeout=600)
        except Exception as exc:
            return llm.Reply(backend, llm.BACKENDS[backend].model, error=f"{type(exc).__name__}: {str(exc)[:200]}")

    def one(backend):
        reply = call(backend)
        if reply.error.startswith("HTTP 429"):
            THROTTLE[backend].limited()
        parsed = parse(reply.data, n)
        if parsed is None and not reply.error:                      # a reply that misses tiles: ask once more
            reply = call(backend)
            parsed = parse(reply.data, n)
        return backend, parsed, {"backend": backend, "model": reply.model, "seconds": reply.seconds,
                                 "usage": reply.usage, "error": reply.error, "fallback": reply.fallback,
                                 "picture": picture.name}

    with ThreadPoolExecutor(max_workers=len(backends)) as ex:
        results = list(ex.map(one, backends))
    return {b: parsed for b, parsed, _ in results}, [call for _, _, call in results]


def label_frames(frames: list[dict], clock, board: Path, legend: str, outbox: Path, cache_dir: Path,
                 workers: int = 4, backends=BACKENDS) -> list[dict]:
    """Cross-check `frames` (dicts with t and path): cached pictures first, the rest in collages of four.
    Sets frame["cloud"]; returns the calls made."""
    digest = prompt_digest(board, legend)
    todo = []
    for f in frames:
        got = cached(f, digest, cache_dir, backends)
        if got:
            f["cloud"] = {"collage": "逐帧缓存", "answers": got, "cached": True, **cross(got)}
        else:
            todo.append(f)
    groups = [todo[i:i + 4] for i in range(0, len(todo), 4)]

    def one(group):
        pic = collage([f["path"] for f in group], [clock(f["t"]) for f in group],
                      outbox / f"collage_{group[0]['t']:08.2f}.jpg")
        return group, pic, *ask_group(board, legend, pic, len(group), backends)

    calls = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for group, pic, answers, group_calls in ex.map(one, groups):
            calls += group_calls
            by_backend = {c["backend"]: c for c in group_calls}
            for k, f in enumerate(group):
                per = {b: (answers.get(b) or [None] * len(group))[k] for b in backends}
                f["cloud"] = {"collage": pic.name, "answers": per, **cross(per)}
                for b, ans in per.items():
                    if ans is not None:
                        store(f, digest, cache_dir, b, ans, by_backend.get(b, {}))
    return calls
