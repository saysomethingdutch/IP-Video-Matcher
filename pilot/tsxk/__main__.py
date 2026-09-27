"""吞噬星空 Q–AC (WO-102 §3–4).

    python -m pilot.tsxk run cases/tsxk.yaml [--rows 7813,7814] [--no-local] [--workers 4]
    python -m pilot.tsxk samples cases/tsxk.yaml [--videos 13,16]      # the example recordings, for checking
    python -m pilot.tsxk check-list cases/tsxk.yaml                    # the 30-row list (input columns only)
    python -m pilot.tsxk check-score cases/tsxk.yaml --owner-approved  # reads the human columns: Owner only

Needs the timelines first (python -m pilot.timeline …). Cloud calls: launch with zsh -lic '…'.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openpyxl

from pilot.semantic import llm

from . import check as CK
from .config import TsxkCase
from .pipeline import process_row, save, system_prompt
from .samples import check_rows

OUT_COLUMNS = ["D", "F", "L", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC"]


def _load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _csv(results: dict[str, dict], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", *OUT_COLUMNS, "标黄", "标红"])
        for rid, r in results.items():
            fl = r["flags"]
            w.writerow([rid, *[(r["fields"].get(c) or {}).get("value") for c in OUT_COLUMNS],
                        "；".join(x["reason"] for x in fl if x["level"] == "yellow"),
                        "；".join(x["reason"] for x in fl if x["level"] == "red")])


def sample_inputs(case: TsxkCase) -> dict[str, dict]:
    """Input columns of the guidance examples that match the sample recordings (by 序号)."""
    g = case.raw["guidance"]
    keep = set(case.raw["workbook"]["input_columns"])
    ws = openpyxl.load_workbook(Path(g["dir"]) / g["examples"], read_only=True, data_only=True).worksheets[0]
    by_sid = {}
    for values in ws.iter_rows(min_row=2, values_only=True):
        d = {openpyxl.utils.get_column_letter(i + 1): v for i, v in enumerate(values)}
        if d.get("B") is not None:
            by_sid[int(d["B"])] = {k: v for k, v in d.items() if k in keep}
    return {name: by_sid.get(int(sid), {}) for name, sid in (g.get("sample_rows") or {}).items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pilot.tsxk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "samples", "check-list", "check-score"):
        p = sub.add_parser(name)
        p.add_argument("config")
        p.add_argument("--rows", default="")
        p.add_argument("--videos", default="13,14,15,16,17")
        p.add_argument("--no-local", action="store_true")
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--owner-approved", action="store_true")
        p.add_argument("--machine", default="")
    args = ap.parse_args(argv)
    llm.LOCAL_ALLOWED = False                      # GLM only (D-037); never the local model (Codex's Ollama)
    case = TsxkCase(args.config)
    cs = case.raw["check_set"]
    if args.cmd == "check-list":
        rows = CK.filled_rows(Path(case.raw["workbook"]["path"]), cs["sheet"], cs["filled_column"])
        picked = CK.draw(rows, int(cs["size"]), int(cs["seed"]))
        CK.write_list(picked, case.path_of(cs["list"]))
        print(f"{len(rows)} 行已填，抽 {len(picked)} 行：", dict(__import__("collections").Counter(r["层"] for r in picked)))
        return 0
    if args.cmd == "check-score":
        if not args.owner_approved:
            print("检验会读取人工结论，需要 Owner 放行：加 --owner-approved", file=sys.stderr)
            return 2
        ids = check_rows(case.path_of(cs["list"]))
        mdir = Path(args.machine) if args.machine else case.case_dir / "check" / "rows"
        machine = {rid: {c: (v or {}).get("value") for c, v in r["fields"].items()}
                   for rid in ids if (r := _load(mdir / f"{rid}.json"))}
        report = CK.score(machine, CK.human_values(Path(case.raw["workbook"]["path"]), cs["sheet"], ids))
        out = case.case_dir / "check"
        out.mkdir(parents=True, exist_ok=True)
        (out / "check_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "check_report.md").write_text(CK.markdown(report), encoding="utf-8")
        print(CK.markdown(report))
        return 0
    rules = case.rules()
    system, info = system_prompt(case)
    print(f"风格样例：示例表 {info['guidance_examples']} 条，同事 {info['colleague_examples']} 条"
          + ("（检验名单未生成，未取同事样例）" if info["colleague_skipped"] else ""))
    backend = case.raw["models"]["text"]
    if args.cmd == "samples":
        inputs = sample_inputs(case)
        jobs = [(n, inputs.get(n, {}), None, _load(case.case_dir / "samples" / n / "timeline.json"),
                 case.case_dir / "samples" / n / "fields.json") for n in args.videos.split(",")]
    else:
        rows = [r.strip() for r in args.rows.split(",") if r.strip()] or case.rows
        inputs = case.read_inputs(rows=rows)
        jobs = [(r, inputs.get(r, {}), case.webledger(r), _load(case.case_dir / "timeline" / r / "timeline.json"),
                 case.case_dir / "rows" / f"{r}.json") for r in rows]

    def one(job):
        rid, inp, led, tl, path = job
        res = process_row(inp, led, tl, rules, system, backend)
        save(res, path)
        return rid, res

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for rid, res in ex.map(one, jobs):
            results[rid] = res
            print(f"{rid}: Y={res['fields'].get('Y', {}).get('value')} U={res['fields']['U']['value']} "
                  f"V={res['fields']['V']['value']} Z={res['fields'].get('Z', {}).get('value')} "
                  f"{'标红' if any(f['level'] == 'red' for f in res['flags']) else ''}", flush=True)
    _csv(results, case.case_dir / ("samples_fields.csv" if args.cmd == "samples" else "fields.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
