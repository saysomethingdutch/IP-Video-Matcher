"""python -m pilot.opinion run CASE_DIR [--backend deepseek] [--workers 4] [--rows …] [--reuse] [--no-local] [--dry OUT]
python -m pilot.opinion ab CASE_DIR [--backend zhipu] [--workers 4] [--no-local]    (WO-004c-R1 §4 blind review)"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pilot.semantic import llm
from pilot.semantic.table import load_table

from .pipeline import dry, run

TABLE = Path("/Volumes/Element/ip-video-matching-repo_de_material/七时吉祥/七时吉祥-比对总表 - 副本.xlsx")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pilot.opinion")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="draft and check the 比对意见 of every row")
    r.add_argument("case_dir", type=Path)
    r.add_argument("--table", type=Path, default=TABLE)
    r.add_argument("--backend", default="deepseek", choices=["deepseek", "zhipu", "local"])
    r.add_argument("--workers", type=int, default=4, help="rows drafted at once (cloud)")
    r.add_argument("--rows", help="comma-separated row ids; the other rows keep their saved opinion")
    r.add_argument("--reuse", action="store_true", help="keep a saved opinion while its inputs are unchanged")
    r.add_argument("--no-local", action="store_true", help="never call the local Ollama, not even as a fallback")
    r.add_argument("--episodes", type=Path, help="folder with qsjx_epNN.ts (default: real_test/ of the main tree)")
    r.add_argument("--revalidate", action="store_true", help="check the saved drafts again with the current checks; no model call")
    r.add_argument("--dry", type=Path, help="write the prompts to this JSON file; no model call, nothing else written")
    b = sub.add_parser("ab", help="the same 20 sampled rows through a second model, laid out for blind review")
    b.add_argument("case_dir", type=Path)
    b.add_argument("--table", type=Path, default=TABLE)
    b.add_argument("--backend", default="zhipu", choices=["deepseek", "zhipu"])
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--no-local", action="store_true")
    b.add_argument("--episodes", type=Path)
    a = ap.parse_args(argv)
    if a.no_local:
        llm.LOCAL_ALLOWED = False
    table = load_table(a.table)
    if a.cmd == "ab":
        from .ab import run_ab
        print(json.dumps(run_ab(a.case_dir, table, a.backend, a.workers, a.episodes), ensure_ascii=False))
        return
    only = set(a.rows.split(",")) if a.rows else None
    if a.dry:
        a.dry.write_text(json.dumps(dry(a.case_dir, table, only, a.episodes), ensure_ascii=False, indent=1), encoding="utf-8")
        return
    s = run(a.case_dir, table, a.backend, only, 1 if a.backend == "local" else a.workers, a.reuse, a.episodes,
            recheck=a.revalidate)
    (a.case_dir / "semantic" / "opinion_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
