"""WO-105 local export trial; inspect only 沈瑞曦 values, hash other OOXML parts."""
from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import openpyxl
from lxml import etree

from pilot.review.tsxk import OUTPUT, TsxkReviewStore
from pilot.review.tsxk_export import _sheet_path, export_tsxk
from pilot.tsxk.config import TsxkCase


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def run(config_path: str | Path, trial_dir: str | Path | None = None) -> dict:
    case = TsxkCase(config_path)
    store = TsxkReviewStore(case)
    if store.latest:
        raise ValueError("trial requires no formal owner decisions")
    temporary = Path(trial_dir) if trial_dir else Path(tempfile.mkdtemp(prefix="wo105-tsxk-export-"))
    temporary.mkdir(parents=True, exist_ok=True)
    before = digest(store.source_path)
    dest = export_tsxk(store, temporary / "tsxk-trial.xlsx")
    if digest(store.source_path) != before:
        raise AssertionError("original workbook changed")
    permitted = {(store.excel_rows[rid], openpyxl.utils.column_index_from_string(col)): (rid, col)
                 for rid in store.row_ids for col in OUTPUT}
    expected = {rid: {f["column"]: f for f in store.export_row(rid)[0]} for rid in store.row_ids}
    source_book = openpyxl.load_workbook(store.source_path, read_only=True, data_only=False)
    output_book = openpyxl.load_workbook(dest, read_only=True, data_only=False)
    try:
        a, b = source_book[store.sheet_name], output_book[store.sheet_name]
        assert (a.max_row, a.max_column) == (b.max_row, b.max_column)
        changed, flagged = 0, set()
        for row_number, (row_a, row_b) in enumerate(zip(a.iter_rows(), b.iter_rows(), strict=True), 1):
            for column_number, (cell_a, cell_b) in enumerate(zip(row_a, row_b, strict=True), 1):
                key = (row_number, column_number)
                address = f"{openpyxl.utils.get_column_letter(column_number)}{row_number}"
                if key not in permitted:
                    if (cell_a.value, cell_a.data_type, getattr(cell_a, "_style_id", 0)) != (
                        cell_b.value, cell_b.data_type, getattr(cell_b, "_style_id", 0)
                    ):
                        raise AssertionError(f"unexpected change at {address}")
                    continue
                rid, col = permitted[key]
                field = expected[rid][col]
                if str(cell_b.value or "") != str(field["value"] or ""):
                    raise AssertionError(f"wrong machine value at {address}")
                if (cell_a.value, cell_a.data_type, getattr(cell_a, "_style_id", 0)) != (
                    cell_b.value, cell_b.data_type, getattr(cell_b, "_style_id", 0)
                ):
                    changed += 1
                level = field["flag_level"]
                if level:
                    color = "FFFFF2B2" if level == "yellow" else "FFF7C8C3"
                    if cell_b.fill.patternType != "solid" or cell_b.fill.fgColor.rgb != color:
                        raise AssertionError(f"missing {level} fill at {address}")
                    flagged.add(address)
    finally:
        source_book.close()
        output_book.close()
    with zipfile.ZipFile(store.source_path) as original, zipfile.ZipFile(dest) as result:
        target = _sheet_path(original, store.sheet_name)
        sheet_rels = f"{target.rsplit('/', 1)[0]}/_rels/{target.rsplit('/', 1)[1]}.rels"
        allowed_parts = {target, sheet_rels, "xl/styles.xml", "[Content_Types].xml"}
        for name in original.namelist():
            if name not in allowed_parts and original.read(name) != result.read(name):
                raise AssertionError(f"other OOXML part changed: {name}")
        added = set(result.namelist()) - set(original.namelist())
        if not all(name.startswith("xl/comments/") or name.startswith("xl/drawings/commentsDrawing")
                   or name == sheet_rels for name in added):
            raise AssertionError(f"unexpected OOXML parts: {added}")
        comment_refs = set()
        for name in added:
            if name.startswith("xl/comments/"):
                xml = etree.fromstring(result.read(name))
                comment_refs.update(node.get("ref") for node in xml.xpath("//*[local-name()='comment']"))
        if comment_refs != flagged:
            raise AssertionError(f"comments differ from colored cells: {len(comment_refs)} vs {len(flagged)}")
    explanation = dest.with_suffix(".md").read_text(encoding="utf-8")
    if "未核验（400 行）" not in explanation or "D、F、L" not in explanation:
        raise AssertionError("export explanation lacks unreviewed rows or columns")
    report = {"trial_dir": str(temporary), "source_sha256_unchanged": before,
              "target_cells": len(permitted), "changed_cells": changed,
              "colored_commented_cells": len(flagged), "other_ooxml_parts_identical": True,
              "unreviewed_rows": 400}
    (temporary / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else "cases/tsxk.yaml"), ensure_ascii=False))
