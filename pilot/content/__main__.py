"""python -m pilot.content run <case_dir> [--ledger PATH] [--index PATH] [--rows 1152,1160] [--out DIR]"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .pipeline import run
from .report import summary, write_pages, write_rows

REPO = Path(__file__).resolve().parents[2]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pilot.content", description="Caption tracks and episode evidence (WO-003a)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="process every ledger row of a case")
    r.add_argument("case_dir", type=Path, help="e.g. workspace/pilot/qsjx_e18 (ledger.jsonl + ocr_cache/)")
    r.add_argument("--ledger", type=Path, help="ledger JSONL (default: <case_dir>/ledger.jsonl)")
    r.add_argument("--index", type=Path, default=REPO / "workspace/pilot/textindex/qsjx/index.sqlite")
    r.add_argument("--rows", help="comma-separated row ids (default: all)")
    r.add_argument("--out", type=Path, help="output dir (default: <case_dir>/content)")
    a = ap.parse_args(argv)

    t0 = time.monotonic()
    ledger = a.ledger or a.case_dir / "ledger.jsonl"
    out = a.out or a.case_dir / "content"
    only = set(a.rows.split(",")) if a.rows else None
    try:
        results = run(a.case_dir, a.index, ledger, only)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))
    write_rows(results, out)
    pages = write_pages(results, a.case_dir, out, ledger.name)
    s = summary(results, {"ledger": str(ledger), "index": str(a.index), "pages": pages,
                          "elapsed_s": round(time.monotonic() - t0, 1)})
    (out / "run_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{s['rows']} rows, {s['elapsed_s']} s -> {out}")
    print("verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(s["verdicts"].items())))
    print(f"caption events {s['events']}, source lines {s['source_lines']} ({s['source_share']:.1%})")


if __name__ == "__main__":
    main()
