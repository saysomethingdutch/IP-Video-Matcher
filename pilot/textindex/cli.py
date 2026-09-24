"""CLI: python -m pilot.textindex {build,query,match,stats} ..."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO / "workspace" / "pilot" / "textindex"
DEFAULT_BOUNDARIES = REPO / "experiments" / "phase3a0" / "cases" / "source_boundaries.json"


def cmd_build(a) -> None:
    from .build import build
    t0 = time.monotonic()
    log = lambda m: print(f"[{time.monotonic() - t0:6.0f}s] {m}", flush=True)
    boundaries = Path(a.boundaries) if a.boundaries else None
    stats = build(Path(a.episodes), a.root / a.work, a.root / "bin", boundaries, a.jobs, a.force, log)
    lb, f = stats["last_build"], stats["flags"]
    print(f"episodes={stats['episodes']} entries={stats['entries']} frames={stats['frames']} "
          f"wall={lb['wall_s']}s (OCR {len(lb['ocr_episodes'])} ep, re-merged {len(lb['remerged_episodes'])}, "
          f"unchanged {len(lb['unchanged_episodes'])}, failed {len(lb['failed'])})")
    print("flags: " + ", ".join(f"{k} {v['ratio']:.1%}" for k, v in f.items()))
    print(f"stats: {a.root / a.work / 'stats.json'}")


def cmd_query(a) -> None:
    from .search import Index
    from .text import normalize
    index = Index.load(a.root / a.work / "index.sqlite")
    cands = index.search(a.text, min_sim=a.min_sim, top=a.top)
    if a.json:
        print(json.dumps({"query": a.text, "norm": normalize(a.text), "candidates": [c.as_dict() for c in cands]},
                         ensure_ascii=False, indent=1))
        return
    print(f"query: {a.text}  (norm: {normalize(a.text)})  candidates: {len(cands)}")
    for n, c in enumerate(cands, 1):
        e = c.entry
        print(f"{n:3d}  sim={c.sim:.2f}  EP{e.episode:02d} {e.t_start:7.1f}-{e.t_end:7.1f}  {e.text}"
              + (f"  [{','.join(e.flags)}]" if e.flags else ""))


def read_rows(path: str) -> list[tuple[float, str]]:
    """TSV `t<TAB>text` (or `t text`) lines, or JSONL {"t": .., "text": ..}; '#' starts a comment."""
    lines = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    rows = []
    for line in lines.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            d = json.loads(line)
            rows.append((float(d["t"]), d["text"]))
        else:
            t, _, text = line.replace("\t", " ").partition(" ")
            rows.append((float(t), text.strip()))
    return rows


def cmd_match(a) -> None:
    from .search import Index
    from .sequence import match_sequence, segments
    index = Index.load(a.root / a.work / "index.sqlite")
    links = match_sequence(index, read_rows(a.input))
    if a.json:
        print(json.dumps({"links": links, "segments": segments(links)}, ensure_ascii=False, indent=1))
        return
    for r in links:
        head = f"{r['t_query']:8.1f}  {r['status']:9s}"
        if r["status"] == "LINKED":
            print(f"{head} EP{r['episode']:02d} {r['t_source']:7.1f}  sim={r['sim']:.2f} conf={r['conf']:.2f}"
                  f"  {r['text']}" + (f"  [{','.join(r['flags'])}]" if r["flags"] else ""))
        elif r["status"] == "AMBIGUOUS":
            alts = "; ".join(f"EP{c['episode']:02d}@{c['t_start']:.1f}({c['sim']:.2f})" for c in r["candidates"])
            print(f"{head} {r['text']}  -> {alts}")
        else:
            print(f"{head} {r['text']}")
    for s in segments(links):
        print(f"segment EP{s['episode']:02d} {s['src_start']:.1f}->{s['src_end']:.1f}"
              f"  (query {s['query_start']:.1f}->{s['query_end']:.1f}, {s['lines']} lines)")


def cmd_stats(a) -> None:
    print((a.root / a.work / "stats.json").read_text(encoding="utf-8"))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pilot.textindex", description="Source subtitle index (WO-002a)")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="output root (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="OCR new/changed episodes and (re)build the index")
    b.add_argument("--episodes", required=True, help="directory with the episodes (EP01.ts, qsjx_ep01.ts, ...)")
    b.add_argument("--work", required=True, help="work id, e.g. qsjx")
    b.add_argument("--boundaries", default=str(DEFAULT_BOUNDARIES) if DEFAULT_BOUNDARIES.exists() else None,
                   help="reference head/tail boundaries JSON, only used for BOUNDARY_REGION flags; without it"
                        " the same conservative rule is built in (content = [140 s, duration - 200 s])")
    b.add_argument("--jobs", type=int, default=4, help="parallel OCR processes (default 4)")
    b.add_argument("--force", action="store_true", help="re-OCR every episode")
    b.set_defaults(func=cmd_build)

    q = sub.add_parser("query", help="look up one line")
    q.add_argument("--work", required=True)
    q.add_argument("--top", type=int, default=10)
    q.add_argument("--min-sim", type=float, default=0.5)
    q.add_argument("--json", action="store_true")
    q.add_argument("text")
    q.set_defaults(func=cmd_query)

    m = sub.add_parser("match", help="sequence-match a time-ordered list of lines")
    m.add_argument("--work", required=True)
    m.add_argument("--json", action="store_true")
    m.add_argument("input", help="TSV `t<TAB>text` / JSONL {t,text} file, or - for stdin")
    m.set_defaults(func=cmd_match)

    s = sub.add_parser("stats", help="print stats.json")
    s.add_argument("--work", required=True)
    s.set_defaults(func=cmd_stats)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))
