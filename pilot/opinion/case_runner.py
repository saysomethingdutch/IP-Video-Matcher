"""Case-configured entry point for the unchanged opinion drafting algorithm."""
from __future__ import annotations

import json

from pilot.semantic import llm
from pilot.semantic.table import load_table

from .pipeline import run


def run_case(config, rows: list[str], force: bool = False) -> dict:
    workbook = config.raw["workbook"]
    table = load_table(config.path_of(workbook["path"]), workbook["sheet"], workbook["output_columns"],
                       workbook["bv_column"])
    model = config.raw["models"].get("opinion", config.raw["models"]["text"])
    backend = {"deepseek-flash": "deepseek", "glm-5.3-flash": "zhipu", "local": "local"}[model]
    llm.LOCAL_ALLOWED = backend == "local"
    result = run(config.case_dir, table, backend, set(rows), 1 if backend == "local" else 4,
                 reuse=not force, case_config=config)
    (config.case_dir / "semantic" / "opinion_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
