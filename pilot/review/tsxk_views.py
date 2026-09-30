"""AD export input, kept separate from the 16-column owner review store."""
from __future__ import annotations

import csv
import hashlib
import json
import re

from pilot.tsxk.config import TsxkCase


def load_views(case: TsxkCase) -> dict[str, dict] | None:
    config = case.raw.get("views")
    path = case.path_of(config["csv"]) if config else case.case_dir / "views.csv"
    if not config and not path.is_file():
        return None
    approval_path = case.case_dir / "views" / "approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8")) if approval_path.is_file() else {}
    approved = (approval.get("schema") == "tsxk-views-approval-1" and
                approval.get("approved") is True and
                approval.get("csv_sha256") == hashlib.sha256(path.read_bytes()).hexdigest() and
                approval.get("rows") == case.rows)
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            rid, value = row["序号"].strip(), row["AD"].strip()
            if rid in result:
                raise ValueError(f"duplicate views ID: {rid}")
            if value and not re.fullmatch(r"\d+", value):
                raise ValueError(f"invalid AD for {rid}: expected a nonnegative integer")
            confidence = row["把握程度"].strip()
            needs_review = not approved and (confidence != "多帧一致" or not value)
            result[rid] = {"value": int(value) if value else "", "reviewed": approved,
                           "retain_mark": False, "column": "AD", "mark_note": "",
                           "flag_level": "yellow" if needs_review else None,
                           "flag_details": [{"level": "yellow", "reason":
                               f"AD 播放量：{confidence or '未读到'}；请看 views/check.html；原文：{row['原始读数']}"}]
                               if needs_review else []}
    if set(result) != set(case.rows):
        raise ValueError(f"views.csv must contain exactly the configured IDs: "
                         f"missing={sorted(set(case.rows)-set(result))[:5]}, "
                         f"extra={sorted(set(result)-set(case.rows))[:5]}")
    return result
