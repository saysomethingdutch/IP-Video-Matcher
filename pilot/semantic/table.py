"""The plaintiff's comparison table (read-only): headers, BVs in other notarial batches, and rows the
original table already marks as repeated captures."""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

OUTPUT_COLS = ["U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ"]
REPEAT_RE = re.compile(r"同序号\s*\d+\s*[，,]\s*重复取证")


def col_index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


@dataclass
class Table:
    headers: list[str]
    by_no: dict[int, list] = field(default_factory=dict)

    def header(self, letter: str) -> str:
        return self.headers[col_index(letter) - 1]

    def value(self, no: int, letter: str):
        row = self.by_no.get(no)
        return None if row is None else row[col_index(letter) - 1]

    def other_batches(self, no: int, batch: range) -> list[int]:
        """Row numbers outside this batch with the same BV."""
        bv = self.value(no, "L")
        if not bv:
            return []
        return sorted(n for n, r in self.by_no.items() if n not in batch and r[col_index("L") - 1] == bv)

    def original_repeat(self, no: int) -> dict[str, str] | None:
        """U-AJ exactly as the original table writes them, when it already says "同序号…，重复取证"."""
        values = {c: self.value(no, c) for c in OUTPUT_COLS}
        if any(isinstance(x, str) and REPEAT_RE.search(x) for x in values.values()):
            return {c: "" if x is None else str(x) for c, x in values.items()}
        return None


def load_table(path: Path, sheet: str = "比对表") -> Table:
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    headers = [str(h) if h is not None else "" for h in next(rows)]
    table = Table(headers)
    for r in rows:
        if isinstance(r[0], (int, float)):
            table.by_no[int(r[0])] = list(r)
    wb.close()
    return table
