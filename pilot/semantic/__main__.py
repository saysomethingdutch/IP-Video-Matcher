"""python -m pilot.semantic {run,compare} ..."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .fields import load_rules
from .pipeline import consistency, presume_skipped_reactions, run
from .report import summary, write_consistency, write_pages, write_rows
from .table import load_table

REPO = Path(__file__).resolve().parents[2]
TABLE = Path("/Volumes/Element/ip-video-matching-repo_de_material/七时吉祥/七时吉祥-比对总表 - 副本.xlsx")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pilot.semantic")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="digest, classify and fill every row")
    r.add_argument("case_dir", type=Path)
    r.add_argument("--vlm", type=Path, default=REPO / "workspace/pilot/vlm")
    r.add_argument("--table", type=Path, default=TABLE)
    r.add_argument("--rules", type=Path)
    r.add_argument("--backend", default="local", choices=["local", "deepseek", "zhipu"])
    r.add_argument("--rows", help="comma-separated row ids")
    r.add_argument("--out", type=Path)
    r.add_argument("--workers", type=int, default=1, help="rows classified at once (cloud backends only)")
    r.add_argument("--story-edit-policy", default="strict", choices=["strict", "lenient"],
                   help="subtitle edits told as plot: strict = 剧情解说 (D-041 default), lenient = 混剪类 when music/theme")
    r.add_argument("--fields-only", action="store_true",
                   help="with --reuse: keep every saved classification, recompute the columns only (no model call)")
    r.add_argument("--visual", default="full", choices=["r1", "full", "consensus"],
                   help="r1 = the 19 WO-004a-R1 rows only; full = predictions_full.csv wins over r1; "
                        "consensus = predictions_consensus_r3.csv (WO-004a-R3)")
    r.add_argument("--reuse", action="store_true", help="reuse saved classifications in the output dir; rows whose model call failed are classified again")
    r.add_argument("--no-local", action="store_true", help="never call the local Ollama, not even as a fallback (D-026)")
    c = sub.add_parser("compare", help="§3.4b cloud vs local comparison")
    c.add_argument("case_dir", type=Path)
    c.add_argument("--vlm", type=Path, default=REPO / "workspace/pilot/vlm")
    c.add_argument("--table", type=Path, default=TABLE)
    c.add_argument("--backends", default="local,deepseek,zhipu")
    c.add_argument("--steps", default="visual,classify", help="visual,classify; or summary alone to recompute compare_summary.json")
    c.add_argument("--modes", default="one_call,two_step", help="classification modes to (re)run")
    c.add_argument("--no-local", action="store_true", help="never call the local Ollama, not even as a fallback (D-026)")
    c.add_argument("--workers", type=int, default=1, help="classification calls at once (cloud backends only)")
    c.add_argument("--resume", action="store_true", help="keep saved results; run only the missing (row, model, mode)")
    c.add_argument("--redo-failed", action="store_true", help="with --resume: rerun classifications that had a failed call")
    c.add_argument("--out", type=Path, default=REPO / "workspace/pilot/semantic")
    a = ap.parse_args(argv)
    if a.no_local:
        from . import llm
        llm.LOCAL_ALLOWED = False

    t0 = time.monotonic()
    table = load_table(a.table) if a.table.exists() else None
    if a.cmd == "compare":
        from .compare import compare
        compare(a.case_dir, a.vlm, table, a.out, a.backends.split(","), a.steps.split(","), a.modes.split(","), a.workers,
                a.resume, a.redo_failed)
        return
    rules = load_rules(a.rules) if a.rules else load_rules()
    rules["story_edit_policy"] = a.story_edit_policy
    only = set(a.rows.split(",")) if a.rows else None
    out = a.out or a.case_dir / "semantic"
    workers = a.workers if a.backend != "local" else 1
    results = run(a.case_dir, a.vlm, table, rules, a.backend, only, reuse_dir=out if a.reuse else None, save_dir=out,
                  workers=workers, visual_files=a.visual, fields_only=a.fields_only)
    presume_skipped_reactions(results, rules)          # WO-004b-R3: batch-level, before the consistency check
    warnings = consistency(results)
    write_rows(results, out, table)
    write_consistency(warnings, out)
    pages = write_pages(results, a.case_dir, out, table)
    s = summary(results, warnings, {"backend": a.backend, "visual": a.visual, "table": str(a.table) if table else None,
                                    "pages": pages, "elapsed_s": round(time.monotonic() - t0, 1)})
    (out / "run_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: s[k] for k in ("rows", "natures", "consistency_warnings", "valid_json_rate", "elapsed_s")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
