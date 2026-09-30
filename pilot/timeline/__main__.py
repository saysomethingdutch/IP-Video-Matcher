"""Content timeline (WO-102 §2).

    python -m pilot.timeline case cases/tsxk.yaml [--rows 7813,7814] [--no-upload] [--no-local] [--force]
    python -m pilot.timeline check cases/tsxk.yaml [--rows 8233]           # the 30-row check set (evidence_check30)
    python -m pilot.timeline samples cases/tsxk.yaml [--videos 13,16]      # the example recordings, for checking
    python -m pilot.timeline video <mp4> --out DIR --case cases/tsxk.yaml [--span 41,700] [--no-upload]

Cloud calls need the keys from ~/.zshrc: launch with zsh -lic '…'. --no-local is accepted for symmetry with the
other modules; the timeline never uses a local model either way (D-057 ④).
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from pilot.semantic import llm
from pilot.tsxk.config import TsxkCase
from pilot.tsxk.samples import check_rows

from . import cloud as C
from . import frames as F
from .pipeline import run_video


def board_for(case: TsxkCase) -> tuple[Path, str]:
    """The reference board (guidance pictures + series frames), built once per case."""
    tl = case.raw["timeline"]
    ref_dir = case.case_dir / "timeline" / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    series = []
    for i, ref in enumerate(tl.get("series_refs") or [], 1):
        path = ref_dir / f"S{i}.jpg"
        if not path.exists():
            img = F.grab(case.sample_video(ref["video"]), float(ref["t"]), box=ref.get("box"))
            if img is None:
                continue
            cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        series.append(path)
    return C.reference_board(case.rules(), case.guidance_dir, series,
                             case.case_dir / "timeline" / "cloud_outbox" / "reference_board.jpg")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pilot.timeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("case", "check", "samples", "video"):
        p = sub.add_parser(name)
        if name == "video":
            p.add_argument("mp4")
            p.add_argument("--out", required=True)
            p.add_argument("--case", default="cases/tsxk.yaml")
            p.add_argument("--span")
        else:
            p.add_argument("config")
        p.add_argument("--rows" if name in ("case", "check") else "--videos", default="")
        p.add_argument("--row-workers", type=int, default=2)
        p.add_argument("--set", default="a", choices=("a", "b"))
        p.add_argument("--no-upload", action="store_true")
        p.add_argument("--no-local", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--workers", type=int)
    args = ap.parse_args(argv)
    case = TsxkCase(args.case if args.cmd == "video" else args.config)
    C.share_slots(case.case_dir / ".cloud_slots")        # the 8/4 limit counts across processes (D-058)
    llm.CALL_LOG = case.case_dir / "usage" / "calls.jsonl"     # every cloud request with its tokens (D-069)
    llm.CALL_RUN = " ".join(["timeline", args.cmd] + ([f"--set {args.set}"] if args.cmd == "check" else []))
    tl = case.raw["timeline"]
    upload = bool(tl.get("allow_image_upload")) and not args.no_upload
    board, legend = board_for(case) if upload else (None, "")
    common = dict(step=float(tl.get("step", 10)), upload=upload, board=board, legend=legend,
                  backends=tuple(tl.get("backends") or C.BACKENDS), workers=args.workers or int(tl.get("workers", 4)),
                  force=args.force, cache_dir=case.case_dir / "timeline" / "_frame_cache")
    rules = case.rules()
    if args.cmd == "video":
        span = tuple(float(x) for x in args.span.split(",")) if args.span else None
        run_video(Path(args.mp4), Path(args.out), rules, span=span, **common)
        return 0
    if args.cmd == "samples":
        names = [v.strip() for v in (args.videos or "13,14,15,16,17").split(",") if v.strip()]
        jobs = [(f"sample{n}", case.sample_video(f"{n}.mp4"), case.webledger(f"samples/{n}"),
                 case.case_dir / "samples" / n) for n in names]
    elif args.cmd == "check":
        cs = case.check_set(args.set)
        rows = [r.strip() for r in args.rows.split(",") if r.strip()] or sorted(check_rows(cs["list"]), key=int)
        jobs = [(r, Path(cs["evidence_dir"]) / f"{r}.mp4", case.webledger(r), cs["out"] / "timeline" / r) for r in rows]
    else:
        rows = [r.strip() for r in args.rows.split(",") if r.strip()] or case.rows
        jobs = [(r, case.evidence(r), case.webledger(r), case.case_dir / "timeline" / r) for r in rows]
    missing = [j[0] for j in jobs if not j[1].exists()]
    jobs = [j for j in jobs if j[1].exists()]
    no_ledger = [j[0] for j in jobs if not j[2]]
    if missing:
        print(f"缺录屏 {len(missing)} 行：{', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}", file=sys.stderr)
    if no_ledger:
        print(f"缺台账 {len(no_ledger)} 行（播放区间由时间线粗估）：{', '.join(no_ledger[:10])}", file=sys.stderr)

    def one(job):
        row, video, led, out = job
        led = led or {}
        span = (led["play_start"], led["play_end"]) if led.get("play_start") is not None and led.get("play_end") else None
        try:
            run_video(video, out, rules, row=row, span=span, anchors=led.get("anchors") or (), duration=led.get("duration"),
                      content=(led.get("content_start"), led.get("content_end")), **common)
        except Exception as exc:                          # one bad recording must not stop the batch
            print(f"{row}: 失败 {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.row_workers)) as ex:
        list(ex.map(one, jobs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
