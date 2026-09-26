"""Validate and resolve the case-owned inputs for the WO-006 entry point."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
LOGICAL_OUTPUTS = tuple("U V W X Y Z AA AB AC AD AE AF AG AH AI AJ".split())


class CaseConfig:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.raw, dict) or self.raw.get("schema_version") != 1:
            raise ValueError("case config requires schema_version: 1")
        self.case_id = str(self.raw.get("case_id") or "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", self.case_id):
            raise ValueError("case_id must start with a letter and contain only letters, digits, _ or -")
        for key in ("workbook", "evidence", "source", "client", "models", "output"):
            if not isinstance(self.raw.get(key), dict):
                raise ValueError(f"case config missing {key} section")
        book = self.raw["workbook"]
        if not book.get("sheet") or not isinstance(book.get("columns"), dict):
            raise ValueError("workbook.sheet and workbook.columns are required")
        if not re.fullmatch(r"[A-Z]{1,3}", str(book.get("bv_column") or "")):
            raise ValueError("workbook.bv_column must be an Excel column letter")
        self.row_ids = self._row_ids(book.get("row_ids"))
        examples = self.raw["client"].get("style_examples") or {}
        selected = set(map(int, self.row_ids))
        sample_rows = {int(rid) for values in (examples.get("table_rows") or {}).values() for rid in values}
        if sample_rows & selected:
            raise ValueError(f"style examples overlap current rows: {sorted(sample_rows & selected)}")
        if book.get("blind_holdout") and not examples.get("exclude_current_rows", True):
            raise ValueError("blind holdout requires style_examples.exclude_current_rows")
        if any(not rid.isdecimal() for rid in self.row_ids):
            raise ValueError("row IDs must be numeric for the current pilot modules")
        mapping = book.get("output_columns") or {}
        if set(mapping) != set(LOGICAL_OUTPUTS):
            raise ValueError("workbook.output_columns must map every logical U–AJ column")
        physical = [str(mapping[key]).upper() for key in LOGICAL_OUTPUTS]
        if len(set(physical)) != 16 or any(not re.fullmatch(r"[A-Z]{1,3}", value) for value in physical):
            raise ValueError("workbook.output_columns must contain 16 distinct Excel letters")
        self.output_indices = tuple(self.column_index(value) for value in physical)
        if min(self.output_indices) <= 20:
            raise ValueError("output columns must not overwrite original A–T fields")
        if bool(self.raw["source"].get("enabled")) and not book["columns"].get("part_claimed"):
            raise ValueError("source.enabled requires the workbook episode-claim column")
        if bool(self.raw["source"].get("enabled")) and not self.raw["source"].get("episode_dir"):
            raise ValueError("source.enabled requires source.episode_dir")
        if not self.raw["client"].get("rules") or not self.raw["client"].get("opinion_format"):
            raise ValueError("client.rules and client.opinion_format are required")
        if not self.raw["client"].get("work_title") or not isinstance(self.raw["client"].get("style_examples"), dict):
            raise ValueError("client.work_title and client.style_examples are required")
        opinion = self.raw["client"]["opinion_format"]
        if len(opinion.get("headings") or []) != 3 or not opinion.get("conclusion"):
            raise ValueError("opinion_format needs three headings and a conclusion")
        if opinion.get("validation_profile", "qsjx") not in ("qsjx", "generic"):
            raise ValueError("opinion_format.validation_profile must be qsjx or generic")
        if not opinion.get("prompt_rules"):
            raise ValueError("client.opinion_format.prompt_rules is required")
        if not self.raw["evidence"].get("videos"):
            raise ValueError("evidence.videos must list the recording files")
        ledger = self.raw.get("ledger") or {}
        if ledger.get("case") not in ("qsjx", "zhuyu", "custom"):
            raise ValueError("ledger.case must be qsjx, zhuyu, or custom")
        if ledger["case"] == "custom" and not ledger.get("screenshot_time_pattern"):
            raise ValueError("custom ledger case needs screenshot_time_pattern")
        for video, preset in (self.raw.get("visual", {}).get("canvas_boxes") or {}).items():
            if video not in self.raw["evidence"]["videos"] or "landscape" not in preset:
                raise ValueError(f"visual.canvas_boxes invalid video or missing landscape: {video}")
            for mode, box in preset.items():
                if mode not in ("landscape", "portrait", "rotated_portrait") or len(box) != 4 or not all(
                        isinstance(value, int) for value in box):
                    raise ValueError(f"visual.canvas_boxes invalid {video}.{mode}")
                x0, y0, x1, y1 = box
                if not (0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 720):
                    raise ValueError(f"visual.canvas_boxes out of frame: {video}.{mode}")
        if not isinstance(self.raw["models"].get("visual", {}).get("allow_image_upload"), bool):
            raise ValueError("models.visual.allow_image_upload must be true or false")
        if self.raw["models"].get("text") not in ("deepseek-flash", "glm-5.3-flash", "local"):
            raise ValueError("models.text must be glm-5.3-flash, deepseek-flash or local")
        if self.raw["models"].get("opinion", self.raw["models"]["text"]) not in ("deepseek-flash", "glm-5.3-flash", "local"):
            raise ValueError("models.opinion must be glm-5.3-flash, deepseek-flash or local")
        audio = self.raw["models"].get("audio") or {}
        if audio.get("enabled") and not audio.get("model"):
            raise ValueError("models.audio.model is required when local audio is enabled")
        self.case_dir = self.path_of(self.raw["output"]["dir"])
        self.output_root = self.case_dir.parent
        self.vlm_dir = self.path_of(self.raw["output"].get("visual_dir")) or self.case_dir / "vlm"
        self.index_dir = self.output_root / "textindex" / str(self.raw["source"].get("index_name") or self.case_id)

    @staticmethod
    def column_index(letter: str) -> int:
        n = 0
        for ch in letter:
            n = n * 26 + ord(ch) - 64
        return n

    @staticmethod
    def _row_ids(value) -> list[str]:
        if isinstance(value, dict):
            first, last = int(value["first"]), int(value["last"])
            if first > last:
                raise ValueError("workbook.row_ids.first must be <= last")
            return [str(n) for n in range(first, last + 1)]
        if isinstance(value, list) and value:
            ids = [str(x) for x in value]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate workbook row ID")
            return ids
        raise ValueError("workbook.row_ids must be an inclusive range or nonempty list")

    def path_of(self, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        return (path if path.is_absolute() else REPO / path).resolve()

    def config_digest(self, *sections: str) -> str:
        chosen = {key: self.raw.get(key) for key in sections}
        return hashlib.sha256(json.dumps(chosen, ensure_ascii=False, sort_keys=True, default=str)
                              .encode("utf-8")).hexdigest()

    def selected_rows(self, requested: str | None) -> list[str]:
        if not requested:
            return self.row_ids
        ids = [x.strip() for x in requested.split(",") if x.strip()]
        if not ids or len(ids) != len(set(ids)) or any(x not in self.row_ids for x in ids):
            raise ValueError("--rows must list distinct row IDs in the case range")
        return ids
