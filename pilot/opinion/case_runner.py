"""Case-configured entry point for the unchanged opinion drafting algorithm."""
from __future__ import annotations

import json

from pilot.semantic import llm
from pilot.semantic.table import load_table

from .pipeline import run


def run_case(config, rows: list[str], force: bool = False) -> dict:
    workbook = config.raw["workbook"]
    if workbook.get("blind_holdout") and (config.case_dir / "corrections.jsonl").exists():
        raise ValueError("blind holdout cannot use target-case opinion corrections")
    style_rows = {int(rid) for values in config.raw["client"]["style_examples"].get("table_rows", {}).values()
                  for rid in values}
    holdout = set(map(int, config.row_ids)) if workbook.get("blind_holdout") else None
    table = load_table(config.path_of(workbook["path"]), workbook["sheet"], workbook["output_columns"],
                       workbook["bv_column"], holdout_rows=holdout, sample_rows=style_rows if holdout else None)
    model = config.raw["models"].get("opinion", config.raw["models"]["text"])
    backend = {"deepseek-flash": "deepseek", "glm-5.3-flash": "zhipu", "local": "local"}[model]
    llm.LOCAL_ALLOWED = backend == "local"
    result = run(config.case_dir, table, backend, set(rows), 1 if backend == "local" else 4,
                 reuse=not force, case_config=config)
    (config.case_dir / "semantic" / "opinion_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
