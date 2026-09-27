"""Case-configured wrapper around WO-004b's unchanged classification pipeline."""
from __future__ import annotations

import json
import time

from . import llm
from .fields import load_rules
from .pipeline import consistency, mark_stale_opinions, presume_skipped_reactions, run
from .report import summary, write_consistency, write_pages, write_rows
from .table import load_table


def run_case(config, rows: list[str], force: bool = False, fields_only: bool = False, reuse_from=None) -> dict:
    """fields_only: keep every saved classification even when its facts changed (no model call, WO-004b-R3).
    reuse_from: compare facts against the digests of this folder instead of semantic/ (R4: the pilot's kept
    classifications were made from the snapshot's digests)."""
    workbook = config.raw["workbook"]
    style_rows = {int(rid) for values in config.raw["client"]["style_examples"].get("table_rows", {}).values()
                  for rid in values}
    holdout = set(map(int, config.row_ids)) if workbook.get("blind_holdout") else None
    table = load_table(config.path_of(workbook["path"]), workbook["sheet"], workbook["output_columns"],
                       workbook["bv_column"], holdout_rows=holdout, sample_rows=style_rows if holdout else None)
    rules = load_rules(config.path_of(config.raw["client"]["rules"]))
    rules["story_edit_policy"] = config.raw["client"].get("story_edit_policy", "strict")     # D-041
    backend = {"deepseek-flash": "deepseek", "glm-5.3-flash": "zhipu", "local": "local"}[config.raw["models"]["text"]]
    llm.LOCAL_ALLOWED = backend == "local"
    visual = "consensus" if config.raw["models"]["visual"]["allow_image_upload"] else "local-r3"
    out = config.case_dir / "semantic"
    started = time.monotonic()
    new = run(config.case_dir, config.vlm_dir, table, rules, backend, set(rows),
              reuse_dir=None if force else (reuse_from or out), save_dir=out, workers=4 if backend != "local" else 1,
              visual_files=visual, fields_only=fields_only)
    existing = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*.json")
                if p.stem in config.row_ids}
    existing.update({r["row_id"]: r for r in new})
    all_rows = [existing[rid] for rid in config.row_ids if rid in existing]
    # Same-uploader flags depend on the entire batch. Remove the old derived flags before recomputing.
    for item in all_rows:
        item["flags"] = [f for f in item.get("flags", []) if not f.get("reason", "").startswith("一致性：")]
    presume_skipped_reactions(all_rows, rules)          # WO-004b-R3: needs the uploader's other rows
    mark_stale_opinions(all_rows)                        # R4: only after the batch decisions
    warnings = consistency(all_rows)
    write_rows(all_rows, out, table)
    write_consistency(warnings, out)
    pages = write_pages(new, config.case_dir, out, table)
    result = summary(all_rows, warnings, {"backend": backend, "visual": visual, "table": str(config.path_of(workbook["path"])),
                                          "pages": pages, "elapsed_s": round(time.monotonic() - started, 1)})
    (out / "run_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
