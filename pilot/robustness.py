"""WO-007 blind comparison. Human U–AJ is opened only after machine outputs are frozen."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .case_config import CaseConfig, LOGICAL_OUTPUTS
from .review.data import read_source

FACT_COLUMNS = tuple("U V W X Y Z AA AB AC AD AE".split())
GRID_COLUMNS = ("AG", "AH", "AI", "AJ")
LABELS = ("甲", "乙", "丙")



def run_deepseek_opinions(config: CaseConfig) -> dict:
    """Draft a separate opinion from the same machine facts; never overwrite GLM output."""
    if not config.raw["workbook"].get("blind_holdout"):
        raise ValueError("WO-007 requires workbook.blind_holdout")
    if (config.case_dir / "corrections.jsonl").exists():
        raise ValueError("blind holdout cannot use target-case opinion corrections")
    from pilot.opinion import prompt as P
    from pilot.opinion.pipeline import opinion_for
    from pilot.opinion.proportion import episode_seconds
    from pilot.opinion.samples import client_examples, table_samples
    from pilot.semantic.table import load_table

    wb = config.raw["workbook"]
    style = config.raw["client"]["style_examples"]
    samples = {int(rid) for values in (style.get("table_rows") or {}).values() for rid in values}
    table = load_table(config.path_of(wb["path"]), wb["sheet"], wb["output_columns"], wb["bv_column"],
                       holdout_rows=set(map(int, config.row_ids)), sample_rows=samples)
    fmt = config.raw["client"]["opinion_format"]
    sample_conclusion = None if fmt.get("validation_profile") == "qsjx" else fmt["conclusion"]
    examples = (client_examples(config.path_of(style.get("requirements")), fmt["headings"], sample_conclusion)
                + table_samples(table, style.get("table_rows") or {}, set(map(int, config.row_ids)),
                                fmt["headings"], sample_conclusion))
    rules = config.path_of(fmt["prompt_rules"]).read_text(encoding="utf-8")
    system = P.system_prompt(examples, rules)
    source = config.raw["source"]
    ep_secs = episode_seconds(config.path_of(source["episode_dir"]),
                              config.case_dir / "semantic" / "episode_seconds.json", source["episode_pattern"])
    out = config.case_dir / "robustness" / "deepseek"
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for rid in config.row_ids:
        path = config.case_dir / "semantic" / f"{rid}.json"
        if not path.is_file():
            raise RuntimeError(f"classification/GLM opinion absent: {rid}")
        rows[rid] = json.loads(path.read_text(encoding="utf-8"))
        if rows[rid].get("opinion") is None:
            raise RuntimeError(f"GLM opinion absent: {rid}")
    context = {"work_title": config.raw["client"]["work_title"], "opinion_format": fmt}

    def one(rid: str):
        path = out / f"{rid}.json"
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return rid, opinion_for(rows[rid], config.case_dir, ep_secs, system, "deepseek", saved,
                                bool(source["enabled"]), context)

    with ThreadPoolExecutor(max_workers=4) as executor:
        for rid, opinion in executor.map(one, config.row_ids):
            (out / f"{rid}.json").write_text(json.dumps(opinion, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{config.case_id} {rid} {opinion['kind']} {'reused' if opinion.get('reused') else 'written'}", flush=True)
    return {"case_id": config.case_id, "rows": len(config.row_ids), "output": str(out)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paths(config: CaseConfig) -> dict[str, Path]:
    case = config.case_dir
    if not (case / "local_stage_complete.json").is_file():
        raise RuntimeError("local ledger and captions have not completed")
    paths = {"ledger": case / "ledger.jsonl"}
    for rid in config.row_ids:
        paths[f"content/{rid}"] = case / "content" / f"{rid}.json"
        paths[f"visual/{rid}"] = config.vlm_dir / "r3" / rid / "consensus.json"
        paths[f"semantic/{rid}"] = case / "semantic" / f"{rid}.json"
        paths[f"deepseek/{rid}"] = case / "robustness" / "deepseek" / f"{rid}.json"
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"machine outputs incomplete ({len(missing)} missing): {missing[:8]}")
    for rid in config.row_ids:
        visual = json.loads(paths[f"visual/{rid}"].read_text(encoding="utf-8"))
        if visual.get("status") not in ("ok", "skipped"):
            raise RuntimeError(f"visual facts failed: {rid}: {visual.get('error')}")
        row = json.loads(paths[f"semantic/{rid}"].read_text(encoding="utf-8"))
        if row.get("opinion") is None:
            raise RuntimeError(f"GLM opinion absent: {rid}")
        json.loads(paths[f"deepseek/{rid}"].read_text(encoding="utf-8"))
    return paths


def freeze(config: CaseConfig) -> dict:
    if not config.raw["workbook"].get("blind_holdout"):
        raise ValueError("WO-007 requires workbook.blind_holdout")
    paths = _paths(config)
    hashes = {name: _sha(path) for name, path in paths.items()}
    out = config.case_dir / "robustness"
    out.mkdir(parents=True, exist_ok=True)
    record = {"case_id": config.case_id, "row_ids": config.row_ids, "machine_sha256": hashes}
    (out / "machine_freeze.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(text.split()).casefold()
    return text.replace("reaction类", "reaction")


def generate(config: CaseConfig) -> dict:
    out = config.case_dir / "robustness"
    record = json.loads((out / "machine_freeze.json").read_text(encoding="utf-8"))
    if record["case_id"] != config.case_id or record["row_ids"] != config.row_ids:
        raise RuntimeError("machine freeze does not match this case")
    paths = _paths(config)
    for name, path in paths.items():
        if record["machine_sha256"].get(name) != _sha(path):
            raise RuntimeError(f"machine output changed after freeze: {name}")

    # This is the first and only stage that opens the held-out human U–AJ cells.
    ledger = [json.loads(line) for line in paths["ledger"].read_text(encoding="utf-8").splitlines() if line]
    source_rows = {int(r["plaintiff"]["source_row"]) for r in ledger}
    wb = config.raw["workbook"]
    _, human = read_source(config.path_of(wb["path"]), wb["sheet"], source_rows, max(config.output_indices))
    by_id = {r["row_id"]: r for r in ledger}
    pairs = []
    blind = []
    key = {}
    scores = []
    grid_columns = ["总编号"]
    for col in (*FACT_COLUMNS, *GRID_COLUMNS):
        grid_columns.extend((f"机器_{col}", f"人工_{col}", f"一致_{col}"))
    for rid in config.row_ids:
        row = by_id[rid]
        source = human[int(row["plaintiff"]["source_row"])]
        machine = json.loads(paths[f"semantic/{rid}"].read_text(encoding="utf-8"))
        other = json.loads(paths[f"deepseek/{rid}"].read_text(encoding="utf-8"))
        fields = machine.get("fields") or {}
        rec = {"总编号": rid}
        for col in (*FACT_COLUMNS, *GRID_COLUMNS):
            actual = source[config.output_indices[LOGICAL_OUTPUTS.index(col)] - 1]
            expected = (fields.get(col) or {}).get("value", "")
            rec[f"机器_{col}"] = "" if expected is None else str(expected)
            rec[f"人工_{col}"] = "" if actual is None else str(actual)
            rec[f"一致_{col}"] = ("未填" if actual in (None, "") else
                                "是" if _norm(expected) == _norm(actual) else "否")
        pairs.append(rec)
        human_op = source[config.output_indices[LOGICAL_OUTPUTS.index("AF")] - 1]
        if human_op in (None, ""):
            continue
        opinions = [("人工", str(human_op)), ("GLM", str((machine.get("opinion") or {}).get("text") or "")),
                    ("DeepSeek", str(other.get("text") or ""))]
        random.Random(20260926 + int(rid)).shuffle(opinions)
        key[rid] = dict(zip(LABELS, [item[0] for item in opinions]))
        title = str(row["plaintiff"].get("title") or "")
        blind.append(f"## 总编号 {rid}\n\n标题：{title}\n\n" +
                     "\n\n".join(f"### 意见 {label}\n\n{text or '（没有生成）'}"
                                   for label, (_, text) in zip(LABELS, opinions)))
        scores.append({"总编号": rid, "甲评分": "", "乙评分": "", "丙评分": "", "最好（甲/乙/丙/并列）": "", "备注": ""})
    with (out / "grid_compare.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=grid_columns)
        writer.writeheader(); writer.writerows(pairs)
    (out / "blind_opinions.md").write_text(
        "# 比对意见盲评\n\n逐份评为“直接用 / 小改 / 大改 / 不能用”，再选每行最好的一份。\n"
        "对应来源在 key.json，评分完成前请勿打开。\n\n" + "\n\n".join(blind) + "\n", encoding="utf-8")
    (out / "key.json").write_text(json.dumps({"case_id": config.case_id, "pairs": key}, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out / "blind_scores.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(scores[0]) if scores else
                                ["总编号", "甲评分", "乙评分", "丙评分", "最好（甲/乙/丙/并列）", "备注"])
        writer.writeheader(); writer.writerows(scores)
    summary = {"case_id": config.case_id, "grid_rows": len(pairs), "blind_opinion_rows": len(scores),
               "freeze_hashes": len(record["machine_sha256"])}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("deepseek", "freeze", "generate"))
    parser.add_argument("case_config")
    args = parser.parse_args()
    config = CaseConfig(args.case_config)
    action = {"deepseek": run_deepseek_opinions, "freeze": freeze, "generate": generate}[args.command]
    print(json.dumps(action(config), ensure_ascii=False))


if __name__ == "__main__":
    main()
