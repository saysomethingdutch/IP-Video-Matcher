"""AD export input, kept separate from the 16-column owner review store."""
from __future__ import annotations

import csv
import re

from pilot.tsxk.config import TsxkCase


def load_views(case: TsxkCase) -> dict[str, dict] | None:
    config = case.raw.get("views")
    if not config:
        return None
    path = case.path_of(config["csv"])
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            rid, value = row["序号"].strip(), row["AD"].strip()
            if rid in result:
                raise ValueError(f"duplicate views ID: {rid}")
            if value and not re.fullmatch(r"\d+", value):
                raise ValueError(f"invalid AD for {rid}: expected a nonnegative integer")
            confidence = row["把握程度"].strip()
            needs_review = confidence != "多帧一致" or not value
            result[rid] = {"value": int(value) if value else "", "reviewed": False,
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
