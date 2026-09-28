"""cases/tsxk.yaml and what other modules hand over for one row.

Web ledger (WO-101, Codex) → <webledger.dir>/<序号>.json, one file per row. Unknown keys are ignored and a
missing file only means the timeline estimates the span itself and S/AB fall back to "无" + "录屏未显示…":

    {
      "row": "7813",
      "title": "…",                         # the page title (D)
      "address": "https://www.bilibili.com/video/BV…",   # the address bar (F); null when not shown
      "play_start": 41.2, "play_end": 700.0, # recording seconds: playback starts / ends (or recording ends)
      "anchors": [[62.0, 21.0]],            # [recording s, video s] from the player's hover readings
      "duration": 707.0,                    # this part's own length on B站, seconds
      "complete": true,                     # R
      "tags": {"visible": true, "items": ["吞噬星空"], "activity": null},   # activity = the tag with the TV icon (S)
      "intro": {"visible": false, "text": ""},
      "collection": {"visible": true, "title": "吞噬星空"},
      "comments": {"visible": false, "traffic": []},
      "finished_witness_time": "2026/8/12 10:46:06"                      # L
    }
"""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import yaml

REPO = Path(__file__).resolve().parents[2]
SCHEMA = "tsxk-web-1"


class TsxkCase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.raw, dict) or self.raw.get("schema") != SCHEMA:
            raise ValueError(f"cases/tsxk.yaml needs schema: {SCHEMA}")
        wb = self.raw["workbook"]
        overlap = set(wb["input_columns"]) & set(wb["output_columns"])
        if overlap:
            raise ValueError(f"input and output columns overlap: {sorted(overlap)}")
        self.case_id = self.raw["case_id"]
        self.case_dir = self.path_of(self.raw["output"]["dir"])
        self.rules_path = self.path_of(self.raw["rules"])

    def path_of(self, value) -> Path | None:
        if value in (None, ""):
            return None
        p = Path(value)
        return p if p.is_absolute() else REPO / p

    @property
    def rows(self) -> list[str]:
        r = self.raw["workbook"]["rows"]
        return [str(i) for i in range(int(r["first"]), int(r["last"]) + 1)]

    def rules(self) -> dict:
        return yaml.safe_load(self.rules_path.read_text(encoding="utf-8"))

    def evidence(self, row: str) -> Path:
        ev = self.raw["evidence"]
        return Path(ev["dir"]) / ev["pattern"].format(row=row)

    def sample_video(self, name: str) -> Path:
        return Path(self.raw["evidence"]["samples"]) / name

    @property
    def guidance_dir(self) -> Path:
        return Path(self.raw["guidance"]["dir"])

    def webledger(self, row: str) -> dict | None:
        path = self.path_of(self.raw["webledger"]["dir"]) / f"{row}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def audio(self, row: str) -> dict | None:
        """WO-103's local sound analysis: <case>/audio/<序号>.json (samples: audio/samples/<n>.json)."""
        path = self.case_dir / "audio" / f"{row}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def read_inputs(self, sheet: str | None = None, rows: list[str] | None = None) -> dict[str, dict]:
        """{序号: {column letter: value}} with the input columns only — never the output (conclusion) columns."""
        wb = self.raw["workbook"]
        keep = set(wb["input_columns"])
        book = openpyxl.load_workbook(wb["path"], read_only=True, data_only=True)
        ws = book[sheet or wb["sheet"]]
        idx = openpyxl.utils.column_index_from_string(wb["id_column"]) - 1
        wanted = set(rows) if rows else None
        out = {}
        for values in ws.iter_rows(min_row=2, values_only=True):
            if idx >= len(values) or values[idx] is None:
                continue
            rid = str(values[idx]).strip()
            if wanted is not None and rid not in wanted:
                continue
            out[rid] = {openpyxl.utils.get_column_letter(i + 1): v for i, v in enumerate(values)
                        if openpyxl.utils.get_column_letter(i + 1) in keep}
        book.close()
        return out
