"""Review data and append-only owner decisions for the 吞噬星空 workbook."""
from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

from pilot.tsxk.ac import HEADINGS
from pilot.tsxk.config import TsxkCase

OUTPUT = ("D", "F", "L", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC")
CLASSIFICATION = OUTPUT[3:]
LEVELS = {"yellow": 1, "red": 2}
FIELD_MENTION = re.compile(r"(?<![A-Za-z0-9])(?:AA|AB|AC|D|F|L|Q|R|S|T|U|V|W|X|Y|Z)(?=[:：、，\s]|$)")
AC_HEADING = re.compile(r"【(?:主题概括|段落情节|新增创作要素情况|比对意见|其他)】")


def parse_ac(value: str) -> list[dict]:
    """Keep the five editable sections visible even when a machine section is empty."""
    text = str(value or "")
    found = list(AC_HEADING.finditer(text))
    parts = {}
    for i, match in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        parts[match.group()] = text[match.end():end].strip()
    if not found and text.strip():
        parts[HEADINGS[0][1]] = text.strip()
    return [{"key": key, "heading": heading, "text": parts.get(heading, "")}
            for key, heading in HEADINGS]


def flag_columns(flag: dict) -> list[str]:
    explicit = flag.get("columns") or flag.get("column")
    if isinstance(explicit, str):
        explicit = [explicit]
    if explicit:
        return [col for col in OUTPUT if col in explicit]
    reason = str(flag.get("reason") or "")
    mentioned = set(FIELD_MENTION.findall(reason))
    if mentioned:
        return [col for col in OUTPUT if col in mentioned]
    if reason.startswith("分类") or "时间线" in reason:
        return list(CLASSIFICATION)
    return list(OUTPUT)


def normalize_flags(machine: dict | None, ledger: dict | None) -> list[dict]:
    raw = list((machine or {}).get("flags") or [])
    if not machine:
        raw.append({"level": "red", "reason": "正式行分类输出尚未完成", "columns": list(OUTPUT)})
    if (ledger or {}).get("complete_needs_review"):
        raw.append({"level": "yellow", "reason": "R：台账完整播放判断需要人工复核", "columns": ["R"]})
    seen = set()
    result = []
    for item in raw:
        item = item if isinstance(item, dict) else {"level": "yellow", "reason": str(item)}
        reason = str(item.get("reason") or item.get("message") or "").strip()
        level = "red" if str(item.get("level") or "").lower() in ("red", "error") else "yellow"
        if not reason or (level, reason) in seen:
            continue
        seen.add((level, reason))
        result.append({"level": level, "reason": reason, "columns": flag_columns(item)})
    return result


class TsxkReviewStore:
    kind = "tsxk"

    def __init__(self, case: TsxkCase):
        self.config = case
        self.case = case.case_dir
        self.row_ids = case.rows
        self.sheet_name = case.raw["workbook"]["sheet"]
        if tuple(case.raw["workbook"]["output_columns"]) != OUTPUT:
            raise ValueError("configured output columns differ from WO-104")
        if case.case_id == "tsxk" and (
            self.sheet_name != "沈瑞曦" or self.row_ids != [str(n) for n in range(7813, 8213)]
        ):
            raise ValueError("WO-104 may only review 沈瑞曦 rows 7813–8212")
        self.source_path = case.path_of(case.raw["workbook"]["path"])
        self.output_columns = tuple(openpyxl.utils.column_index_from_string(col) for col in OUTPUT)
        self.export_name = "【G组】吞噬星空视频比对-机器核对"
        self.decisions_path = self.case / "review" / "decisions.jsonl"
        self.lock = threading.RLock()
        self.latest: dict[str, dict[str, dict]] = defaultdict(dict)
        self.events: dict[str, list[dict]] = defaultdict(list)
        wanted = set(self.row_ids)
        id_col = openpyxl.utils.column_index_from_string(case.raw["workbook"]["id_column"])
        book = openpyxl.load_workbook(self.source_path, read_only=True, data_only=True)
        try:
            sheet = book[self.sheet_name]
            self.headers = list(next(sheet.iter_rows(min_row=1, max_row=1, values_only=True)))
            self.excel_rows = {}
            for row_number, cells in enumerate(sheet.iter_rows(min_row=2, min_col=id_col,
                                                                 max_col=id_col, values_only=True), 2):
                value = cells[0]
                if value is None:
                    continue
                rid = str(int(value)) if isinstance(value, (int, float)) and float(value).is_integer() else str(value).strip()
                if rid in wanted:
                    if rid in self.excel_rows:
                        raise ValueError(f"duplicate row ID in {self.sheet_name}: {rid}")
                    self.excel_rows[rid] = row_number
        finally:
            book.close()
        missing = wanted - self.excel_rows.keys()
        if missing:
            raise ValueError(f"{self.sheet_name} is missing {len(missing)} configured IDs: {sorted(missing)[:5]}")
        self.inputs = case.read_inputs(rows=self.row_ids)
        if self.decisions_path.exists():
            for line in self.decisions_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    event = json.loads(line)
                    rid, col = str(event.get("row")), str(event.get("field"))
                    if rid in wanted and col in OUTPUT:
                        self.latest[rid][col] = event
                        self.events[rid].append(event)

    def _load(self, path: Path) -> dict | None:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def machine(self, rid: str) -> dict | None:
        return self._load(self.case / "rows" / f"{rid}.json")

    def ledger(self, rid: str) -> dict | None:
        return self.config.webledger(rid)

    def flags(self, rid: str, machine: dict | None = None, ledger: dict | None = None) -> list[dict]:
        return normalize_flags(machine if machine is not None else self.machine(rid),
                               ledger if ledger is not None else self.ledger(rid))

    def fields(self, rid: str, machine: dict | None = None, flags: list[dict] | None = None) -> list[dict]:
        machine = machine if machine is not None else self.machine(rid)
        flags = flags if flags is not None else self.flags(rid, machine)
        source = (machine or {}).get("fields") or {}
        result = []
        for col in OUTPUT:
            raw = source.get(col) or {}
            if not isinstance(raw, dict):
                raw = {"value": raw}
            value = raw.get("value")
            machine_value = "" if value is None else str(value)
            event = self.latest[rid].get(col)
            final = str(event["new"]) if event else machine_value
            related = [flag for flag in flags if col in flag["columns"]]
            level = max((flag["level"] for flag in related), key=lambda x: LEVELS[x], default=None)
            index = openpyxl.utils.column_index_from_string(col) - 1
            modified = any(e["field"] == col and e["action"] == "modify" for e in self.events[rid])
            result.append({"column": col, "name": str(self.headers[index] or col),
                           "machine_value": machine_value, "value": final,
                           "by": raw.get("by") or "", "basis": raw.get("basis") or "",
                           "reviewed": bool(event), "modified": modified,
                           "reviewer": event.get("who") if event else None,
                           "reviewed_at": event.get("when") if event else None,
                           "flag_level": level, "flag_reasons": [x["reason"] for x in related],
                           "flag_details": [{"level": x["level"], "reason": x["reason"]} for x in related],
                           "sections": parse_ac(final) if col == "AC" else None})
        return result

    def summary(self, rid: str) -> dict:
        if rid not in self.excel_rows:
            raise KeyError(rid)
        machine = self.machine(rid)
        ledger = self.ledger(rid)
        flags = self.flags(rid, machine, ledger)
        y = self.latest[rid].get("Y")
        category = str(y["new"]) if y else str(((machine or {}).get("fields") or {}).get("Y", {}).get("value") or "待分类")
        reviewed = len(self.latest[rid])
        return {"row_id": rid, "title": str(self.inputs.get(rid, {}).get("C") or (ledger or {}).get("title") or ""),
                "category": category, "status": "待核" if reviewed < len(OUTPUT) else
                "已修改" if any(e["action"] == "modify" for e in self.events[rid]) else "已通过",
                "reviewed": reviewed, "total": len(OUTPUT), "machine_ready": machine is not None,
                "yellow": sum(f["level"] == "yellow" for f in flags),
                "red": sum(f["level"] == "red" for f in flags)}

    def all_summaries(self) -> list[dict]:
        return [self.summary(rid) for rid in self.row_ids]

    def _timeline(self, rid: str) -> dict | None:
        return self._load(self.case / "timeline" / rid / "timeline.json")

    def thumbnail_path(self, rid: str, name: str) -> Path | None:
        if rid not in self.excel_rows or Path(name).name != name or not name.lower().endswith(".jpg"):
            return None
        root = (self.case / "timeline" / rid / "frames").resolve()
        path = (root / name).resolve()
        return path if path.is_relative_to(root) and path.is_file() else None

    def video_path(self, rid: str) -> Path | None:
        if rid not in self.excel_rows:
            return None
        video = self.config.evidence(rid)
        return video if video.is_file() else None

    def detail(self, rid: str) -> dict:
        if rid not in self.excel_rows:
            raise KeyError(rid)
        machine = self.machine(rid)
        ledger = self.ledger(rid) or {}
        flags = self.flags(rid, machine, ledger)
        timeline = self._timeline(rid) or {}
        sound = self._load(self.case / "audio" / f"{rid}.json") or {}
        frames = timeline.get("frames") or []
        segments = []
        for segment in timeline.get("segments") or []:
            match = next((f for f in frames if segment.get("start", 0) <= f.get("t", -1) < segment.get("end", 0)), None)
            name = f"{float(match['t']):08.2f}.jpg" if match else ""
            thumb = f"/api/thumb/{rid}/{name}" if name and self.thumbnail_path(rid, name) else None
            segments.append({k: segment.get(k) for k in ("start", "end", "content", "form", "sources")}
                            | {"thumbnail": thumb})
        speech = [s for s in sound.get("segments") or [] if s.get("kind") == "uploader"]
        return {**self.summary(rid), "fields": self.fields(rid, machine, flags),
                "flags": flags, "history": self.events[rid], "excel_row": self.excel_rows[rid],
                "bilibili_url": self.inputs.get(rid, {}).get("E") or ledger.get("address"),
                "video_url": f"/api/video/{rid}" if self.video_path(rid) else None,
                "ledger": {k: ledger.get(k) for k in ("play_start", "play_end", "content_start",
                    "content_end", "content_basis", "complete", "complete_basis", "complete_reason",
                    "complete_needs_review", "passes", "other_pages", "flags")},
                "timeline": {"segments": segments, "flags": timeline.get("flags") or [],
                             "summary": timeline.get("summary") or {}},
                "sound": {"status": sound.get("status"), "uploader_speech": speech,
                          "uploader_text": sound.get("uploader_speech") or [],
                          "promotional_speech": sound.get("promotional_speech") or [],
                          "played_seconds": sound.get("played_seconds")},
                "evidence": (machine or {}).get("evidence") or []}

    def decide(self, rid: str, action: str, edits: dict | None = None, actor: str | None = None) -> dict:
        if rid not in self.excel_rows or action not in ("approve", "modify"):
            raise ValueError("invalid row or action")
        actor = str(actor or "").strip()
        if not 1 <= len(actor) <= 80:
            raise ValueError("请填写核验人（1–80 字）")
        edits = edits or {}
        if len(edits) != 1 or next(iter(edits)) not in OUTPUT:
            raise ValueError("每次只能核验一个产出列")
        col, requested = next(iter(edits.items()))
        if self.machine(rid) is None:
            raise ValueError("该行机器输出尚未生成")
        with self.lock:
            field = next(x for x in self.fields(rid) if x["column"] == col)
            old = field["value"]
            new = old if action == "approve" else str(requested)
            if action == "modify" and new == old:
                raise ValueError("修改值与当前值相同")
            event = {"when": datetime.now(timezone.utc).isoformat(), "who": actor,
                     "row": rid, "field": col, "action": action, "old": old, "new": new,
                     "machine": field["machine_value"]}
            self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
            with self.decisions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.latest[rid][col] = event
            self.events[rid].append(event)
            return self.summary(rid)

    def export_row(self, rid: str) -> tuple[list[dict], list[dict]]:
        machine = self.machine(rid)
        if machine is None:
            raise ValueError(f"missing machine output: {rid}")
        source = machine.get("fields") or {}
        absent = [col for col in OUTPUT if col not in source]
        if absent:
            raise ValueError(f"machine output {rid} lacks {','.join(absent)}")
        flags = self.flags(rid, machine, self.ledger(rid))
        return self.fields(rid, machine, flags), flags

    def corrections(self) -> list[dict]:
        return [event for rid in self.row_ids for event in self.events[rid]
                if event.get("action") == "modify" and event.get("old") != event.get("new")]
