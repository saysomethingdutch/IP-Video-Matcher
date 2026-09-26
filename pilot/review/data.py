"""Read case outputs and maintain append-only owner decisions."""
from __future__ import annotations

import csv
import json
import re
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
OUTPUT_COLS = tuple(range(21, 37))
STATES = ("待核", "已通过", "已修改", "待定")
RED_WORDS = ("冲突", "失败", "错误", "无法关联", "疑似故意跳播", "SUSPECT_HALLUCINATION")
REPEAT = re.compile(r"同序号\s*(\d+)[，,]?\s*重复取证")


def col_name(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def read_source(path: Path, sheet_name: str = "比对表", source_rows: set[int] | None = None,
                max_column: int = 36) -> tuple[list[str], dict[int, list[str]]]:
    """Read the existing workbook without a third-party dependency."""
    ns = {"m": MAIN, "r": R}
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.findall(".//m:t", ns)) for si in root.findall("m:si", ns)]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        sheet = next(s for s in workbook.findall("m:sheets/m:sheet", ns) if s.get("name") == sheet_name)
        rel_id = sheet.get(f"{{{R}}}id")
        target = next(x.get("Target") for x in rels.findall(f"{{{PKG}}}Relationship") if x.get("Id") == rel_id)
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = ET.fromstring(book.read(target))
        rows = {}
        for row in root.findall("m:sheetData/m:row", ns):
            number = int(row.get("r"))
            if source_rows is not None and number not in source_rows and number != 1:
                continue
            vals = [""] * max_column
            for cell in row.findall("m:c", ns):
                letters = re.match(r"[A-Z]+", cell.get("r", ""))
                if not letters:
                    continue
                col = 0
                for char in letters.group():
                    col = col * 26 + ord(char) - 64
                if not 1 <= col <= max_column:
                    continue
                kind = cell.get("t")
                v = cell.find("m:v", ns)
                if kind == "inlineStr":
                    value = "".join(x.text or "" for x in cell.findall(".//m:t", ns))
                elif v is None:
                    value = ""
                elif kind == "s":
                    value = shared[int(v.text)]
                else:
                    value = v.text or ""
                vals[col - 1] = value
            rows[number] = vals
        return rows[1], {k: v for k, v in rows.items() if k > 1}


def fmt_seconds(value) -> str:
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, str) and re.fullmatch(r"\d{1,3}:\d{1,2}(?::\d{1,2})?", value.strip()):
            parts = [int(part) for part in value.strip().split(":")]
            seconds = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            seconds = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{sec:02}"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class ReviewStore:
    def __init__(self, case: Path, config=None):
        self.case = Path(case).resolve()
        self.rows = {x["row_id"]: x for x in (json.loads(line) for line in (self.case / "ledger.jsonl").read_text(encoding="utf-8").splitlines())}
        self.row_ids = config.row_ids if config else [str(n) for n in range(1051, 1220)]
        if set(self.rows) != set(self.row_ids):
            raise ValueError("ledger rows differ from configured case rows")
        self.sheet_name = config.raw["workbook"]["sheet"] if config else "比对表"
        self.vlm_dir = config.vlm_dir if config else self.case.parent / "vlm"
        self.output_columns = tuple(config.output_indices) if config else OUTPUT_COLS
        self.logical_by_index = {physical: logical for logical, physical in zip(OUTPUT_COLS, self.output_columns)}
        self.export_name = config.raw["output"].get("export_name") if config else "七时吉祥-比对总表-机器填写"
        self.source_path = config.path_of(config.raw["workbook"]["path"]) if config else Path(next(iter(self.rows.values()))["plaintiff"]["source_workbook"])
        source_rows = {int(row["plaintiff"]["source_row"]) for row in self.rows.values()}
        self.headers, self.source_rows = read_source(self.source_path, self.sheet_name, source_rows,
                                                    max(36, *self.output_columns))
        self.decisions_path = self.case / "review" / "decisions.jsonl"
        self.lock = threading.RLock()
        self.decisions: dict[str, dict] = {rid: {"status": "待核", "edits": {}, "modified": []} for rid in self.rows}
        if self.decisions_path.exists():
            for line in self.decisions_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._apply(json.loads(line))
        self.semantic_csv = {}
        csv_path = self.case / "semantic" / "fields.csv"
        if csv_path.exists():
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                for record in csv.DictReader(handle):
                    rid = str(record.get("row_id") or record.get("行号") or record.get("总编号") or "")
                    if rid in self.rows:
                        self.semantic_csv[rid] = record

    def _apply(self, event: dict):
        rid = str(event["row"])
        if rid not in self.decisions:
            return
        field = event["field"]
        state = self.decisions[rid]
        if field == "status":
            state["status"] = event["new"]
        else:
            state["edits"][str(field)] = event["new"]
            if str(field) not in state["modified"]:
                state["modified"].append(str(field))

    def _append(self, event: dict):
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
        self._apply(event)

    def _machine_fields(self, rid: str) -> list[dict]:
        ledger = self.rows[rid]
        source = self.source_rows[int(ledger["plaintiff"]["source_row"])]
        content = load_json(self.case / "content" / f"{rid}.json") or {}
        semantic = load_json(self.case / "semantic" / f"{rid}.json") or {}
        fields = []
        for index in range(1, len(self.headers) + 1):
            logical = self.logical_by_index.get(index)
            value = source[index - 1]
            origin = "原告表格" if index <= 20 else ("原表已有值" if value else "暂无机器数据")
            basis = origin
            provisional = False
            if logical is not None and not value:
                if logical == 21 and ledger.get("video_duration") is not None:
                    value, origin = fmt_seconds(ledger["video_duration"]), "台账：视频时长"
                elif logical == 24 and ledger.get("capture_seconds") is not None:
                    value, origin = fmt_seconds(ledger["capture_seconds"]), "台账：取证播放时长"
                elif logical == 29 and ledger.get("complete") is not None:
                    complete = ledger["complete"]
                    value, origin = (("是" if complete else "否") if isinstance(complete, bool) else str(complete)), "台账：完整播放判断"
                elif logical == 30 and ledger.get("views") not in (None, ""):
                    observed = str(ledger["views"])
                    claimed = str(ledger["plaintiff"].get("views_claimed") or "")
                    value = "是" if observed == claimed else f"否，录像显示 {observed}"
                    origin = "台账：录像播放量与原告表格"
                elif logical == 31 and content.get("verdict"):
                    verdicts = {"CONTRADICTED": "否", "NARROWER": "声称范围较宽", "UNVERIFIED": "无法核实", "CLAIM_UNPARSEABLE": "声称无法解析"}
                    value, origin = verdicts.get(content["verdict"], "无法核实"), "字幕模块：集数核验（基于 R1 台账）"
                    basis = content.get("statement") or origin
                if value:
                    provisional = True
                    if logical != 31:
                        source_key = {21: "video_duration", 24: "capture_seconds", 29: "complete", 30: "views"}.get(logical)
                        evidence = (ledger.get("sources") or {}).get(source_key, []) if source_key else []
                        frames = [f"{x.get('video', '')} {x.get('frame') or x.get('evidence_frame') or ''}（录像 {x.get('t', '—')} 秒）" for x in evidence if isinstance(x, dict)]
                        basis = origin + ("；" + "、".join(dict.fromkeys(frames[:2] + frames[-2:])) if frames else "")
            if logical is not None:
                semantic_fields = semantic.get("fields") or semantic.get("columns") or {}
                found = None
                if isinstance(semantic_fields, dict):
                    found = semantic_fields.get(str(logical)) or semantic_fields.get(col_name(logical)) or semantic_fields.get(self.headers[index - 1])
                if found is None:
                    found = self.semantic_csv.get(rid, {}).get(self.headers[index - 1])
                    if found not in (None, ""):
                        origin = "fields.csv"
                        basis = "WO-004b fields.csv"
                if isinstance(found, dict):
                    origin = found.get("source") or found.get("basis") or "WO-004b 语义字段"
                    basis = str(found.get("basis") or origin)
                    found = found.get("value")
                if found not in (None, ""):
                    value, origin, provisional = str(found), "WO-004b：" + str(origin), False
            edit = self.decisions[rid]["edits"].get(str(index))
            modified = edit is not None
            if modified:
                value, origin, provisional = edit, "所有者修改", False
                basis = "所有者修改；修改前依据：" + basis
            if logical in (21, 24, 25):
                value = fmt_seconds(value)
            fields.append({"index": index, "column": col_name(index), "name": self.headers[index - 1], "value": str(value) if value is not None else "", "source": origin, "basis": basis, "provisional": provisional, "modified": modified, "editable": index in self.output_columns})
        return fields

    def _flags(self, rid: str) -> list[dict]:
        ledger = self.rows[rid]
        content = load_json(self.case / "content" / f"{rid}.json") or {}
        semantic = load_json(self.case / "semantic" / f"{rid}.json") or {}
        vlm = self.vlm_dir
        visual = (load_json(vlm / "r3" / rid / "consensus.json")
                  or load_json(vlm / "r3" / rid / "local.json")
                  or load_json(vlm / "full" / rid / "result.json") or {})
        semantic_flags = semantic.get("flags") or []
        if isinstance(semantic_flags, dict):
            semantic_flags = [{"reason": f"{key}：{value}"} for key, value in semantic_flags.items()]
        raw = list(ledger.get("flags") or []) + list(semantic_flags)
        raw.extend({"level": "yellow", "reason": flag} for flag in visual.get("flags", [])
                   if isinstance(flag, str) and
                   (flag.startswith("画面比例待核") or flag.startswith("画面布局与几何待核")
                    or flag.startswith("布局／比例待核") or flag.startswith("云端判断失败")
                    or flag.startswith("uploader_on_camera 待核") or flag.startswith("other_works 待核")
                    or flag.startswith("遮挡物待核")))
        if content.get("verdict") in ("CONTRADICTED", "UNVERIFIED", "CLAIM_UNPARSEABLE"):
            raw.append("字幕集数" + ("冲突" if content["verdict"] == "CONTRADICTED" else "未核实"))
        elif content.get("verdict") == "NARROWER":
            raw.append("字幕核验范围比原告声称更窄")
        asr = load_json(self.case / "asr" / f"{rid}.json")
        if not asr:
            raw.append("无 ASR")
        elif asr.get("status") == "NO_PLAYBACK":
            raw.append("ASR 无播放片段")
        result = []
        positions = {}
        for flag in raw:
            reason = str(flag.get("reason") or flag.get("message") or flag) if isinstance(flag, dict) else str(flag)
            level = str(flag.get("level") or flag.get("severity") or "").lower() if isinstance(flag, dict) else ""
            level = "red" if level in ("red", "error", "high") or (not level and any(word in reason for word in RED_WORDS)) else "yellow"
            if reason in positions:
                if level == "red":
                    result[positions[reason]]["level"] = "red"
                continue
            positions[reason] = len(result)
            result.append({"level": level, "reason": reason})
        return result

    def summary(self, rid: str) -> dict:
        ledger = self.rows[rid]
        flags = self._flags(rid)
        return {"row_id": rid, "video": ledger["video"], "uploader": ledger["plaintiff"].get("uploader") or "未知 UP 主", "title": ledger["plaintiff"].get("title") or "", "status": self.decisions[rid]["status"], "yellow": sum(x["level"] == "yellow" for x in flags), "red": sum(x["level"] == "red" for x in flags)}

    def all_summaries(self) -> list[dict]:
        return [self.summary(rid) for rid in self.row_ids]

    def detail(self, rid: str) -> dict:
        if rid not in self.rows:
            raise KeyError(rid)
        ledger = self.rows[rid]
        content = load_json(self.case / "content" / f"{rid}.json")
        asr = load_json(self.case / "asr" / f"{rid}.json")
        semantic = load_json(self.case / "semantic" / f"{rid}.json")
        evidence = list(ledger.get("evidence") or [])
        if (ledger.get("screenshot") or {}).get("evidence_frame"):
            evidence.append({"label": "截图", "path": ledger["screenshot"]["evidence_frame"], "t": None})
        opinion_index = self.output_columns[11]
        original = next((f["value"] for f in self._machine_fields(rid) if f["index"] == opinion_index), "")
        match = REPEAT.search(original)
        repeat_main = match.group(1) if match else None
        return {**self.summary(rid), "fields": self._machine_fields(rid), "output_indices": self.output_columns,
                "flags": self._flags(rid), "evidence": evidence, "playback_map": ledger.get("playback_map") or [], "video_duration": ledger.get("video_duration"), "content": content, "asr": asr, "semantic": semantic, "claim": {"bv": ledger["plaintiff"].get("bv") or ledger.get("observed_bv"), "episode": ledger["plaintiff"].get("part_claimed"), "views": ledger["plaintiff"].get("views_claimed"), "video_type": ledger["plaintiff"].get("video_type")}, "repeat_main": repeat_main if repeat_main in self.rows else None}

    def decide(self, rid: str, action: str, edits: dict | None = None) -> dict:
        if rid not in self.rows or action not in ("approve", "modify_approve", "hold"):
            raise ValueError("invalid row or action")
        edits = edits or {}
        if any(int(k) not in self.output_columns for k in edits):
            raise ValueError("only output columns may be edited")
        with self.lock:
            current = {str(f["index"]): f["value"] for f in self._machine_fields(rid)}
            normalized = {str(k): (fmt_seconds(v) if self.logical_by_index.get(int(k)) in (21, 24, 25) else str(v)) for k, v in edits.items()}
            changed = {k: v for k, v in normalized.items() if v != current[k]}
            if action == "modify_approve" and not (changed or self.decisions[rid]["modified"]):
                raise ValueError("no modified field")
            now = datetime.now(timezone.utc).isoformat()
            for field, value in changed.items():
                self._append({"timestamp": now, "row": rid, "field": field, "old": current[field], "new": value, "action": action})
            old_status = self.decisions[rid]["status"]
            new_status = "待定" if action == "hold" else ("已修改" if changed or self.decisions[rid]["modified"] else "已通过")
            self._append({"timestamp": now, "row": rid, "field": "status", "old": old_status, "new": new_status, "action": action})
            return self.summary(rid)

    def batch_approve(self, uploader: str) -> dict:
        approved, excluded, dependent = [], [], []
        candidates = [rid for rid in self.rows if self.summary(rid)["uploader"] == uploader and self.decisions[rid]["status"] == "待核"]
        candidates.sort(key=lambda rid: bool(REPEAT.search(self._machine_fields(rid)[self.output_columns[11]-1]["value"])))
        for rid in candidates:
            summary = self.summary(rid)
            if summary["red"]:
                excluded.append(rid)
                continue
            repeat = REPEAT.search(self._machine_fields(rid)[self.output_columns[11]-1]["value"])
            if repeat and (repeat.group(1) not in self.rows or self.decisions[repeat.group(1)]["status"] not in ("已通过", "已修改")):
                dependent.append(rid)
                continue
            self.decide(rid, "approve")
            approved.append(rid)
        return {"approved": approved, "excluded_red": excluded, "excluded_main": dependent}

    def corrections(self) -> list[dict]:
        records = []
        for rid in self.rows:
            for field in (32, 33, 34, 35, 36):
                if str(field) in self.decisions[rid]["modified"]:
                    item = self._machine_fields(rid)[field - 1]
                    records.append({"row_id": rid, "column": item["column"], "field": item["name"], "value": item["value"], "status": self.decisions[rid]["status"]})
        return records
