"""Read-only, local consistency checks for the 400 finished 吞噬星空 rows.

Findings are review leads, not automatic corrections. This module never writes rows/.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pilot.review.tsxk import OUTPUT, flag_columns
from pilot.tsxk.config import TsxkCase

TIME_RANGE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s*[-–—~至]\s*\d{1,2}:\d{2}(?::\d{2})?")
TIME_POINT = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
L_FORMAT = re.compile(r"\d{4}/\d{1,2}/\d{1,2} \d{2}:\d{2}:\d{2}\Z")
VISUAL = re.compile(
    r"AI\s*(?:生成|绘制|制作).{0,8}(?:图|画面|素材)|"
    r"(?:其他|别的|外部|非原片).{0,8}(?:国漫|动画|作品|IP|画面)|"
    r"真人出镜|真人画面|自制.{0,8}(?:片头|片尾|封面|画面)|"
    r"(?:新增|插入|使用|穿插).{0,8}(?:截图|漫画|游戏画面|画面素材|配图)|"
    r"引流画面|小说页"
)
PROMO = re.compile(r"引流|网盘|二维码|微信群|QQ群|公众号|付费|链接|群号")
NOVEL = re.compile(r"原著|原作小说|小说(?:中|里|版|原文|文字|内容|设定|情节)")
FORMS = ("漫画", "动画剧场版", "手游", "小说", "有声书")
Z_WORDS = {"高": "较高|可能性高|可能性较大|可能性大|合理使用程度高",
           "中": "中等|中等程度|可能性中|可能性一般|合理使用可能性适中",
           "低": "较低|可能性低|可能性较小|可能性小|合理使用程度低"}


def section(ac: str, name: str) -> str:
    match = re.search(rf"【{re.escape(name)}】\s*(.*?)(?=【[^】]+】|\Z)", ac, re.S)
    return match.group(1).strip() if match else ""


def values(row: dict) -> dict[str, str]:
    fields = row.get("fields") or {}
    return {column: str((fields.get(column) or {}).get("value") or "").strip()
            for column in OUTPUT}


def issues(row: dict, original: dict, novel_evidence: str = "") -> list[tuple[str, str, str, str]]:
    """Return (type, columns, explanation, evidence) for one row."""
    v = values(row)
    ac = v["AC"]
    new = section(ac, "新增创作要素情况")
    opinion = section(ac, "比对意见")
    notes = section(ac, "其他")
    found: list[tuple[str, str, str, str]] = []

    def add(code: str, columns: str, explanation: str, evidence: str = "") -> None:
        found.append((code, columns, explanation, evidence[:180].replace("\n", " ")))

    # W is about non-official *visual material*, not captions, BGM, or watermarks.
    visual = VISUAL.search(new)
    if v["W"] == "否" and visual and not re.search(r"(?:没有|未见|未增加|并无).{0,10}" + re.escape(visual.group()), new):
        add("W_AC_VISUAL", "W,AC", "新增要素写了非官方画面素材，W 却为否；疑似素材需人工确认", visual.group())
    if v["Y"] == "偷渡":
        if v["W"] != "是":
            add("SMUGGLE_W", "Y,W", "偷渡按规则应有无关画面，W 应为是", v["W"])
        if not (TIME_RANGE.search(ac) and re.search(r"无关|非吞噬星空|其他作品", ac)):
            add("SMUGGLE_SPAN", "Y,AC", "偷渡说明缺少无关内容的明确时间段", notes)
    if v["X"] and v["X"] != "否" and not (TIME_POINT.search(ac) and PROMO.search(ac)):
        add("TRAFFIC_TIME", "X,AC", "X 有引流，但说明未同时给出引流时间与内容", notes)
    if v["T"] and v["T"] != "否":
        forms = [name for name in FORMS if name in v["T"]]
        aliases = {"小说": ("原著", "原作小说"), "手游": ("游戏",),
                   "动画剧场版": ("血洛大陆", "决战原始星"), "有声书": ("朗读音频",)}
        missing = [name for name in forms if name not in ac and
                   not any(alias in ac for alias in aliases.get(name, ()))]
        if missing:
            add("FORM_AC", "T,AC", "T 所列形式在 AC 未说明", "、".join(missing))
        if "小说" in forms and not NOVEL.search(novel_evidence):
            add("NOVEL_EVIDENCE", "T", "T 涉及小说，但本地画面文字、字幕、口播未找到明确小说提示；需核原视频", v["T"])
    if v["Z"] in Z_WORDS and opinion:
        claimed = {level for level, pattern in Z_WORDS.items() if re.search(pattern, opinion)}
        if claimed and v["Z"] not in claimed:
            add("Z_OPINION", "Z,AC", "比对意见的合理使用等级与 Z 不一致", opinion[-130:])
    commentary = "解说类" in v["Y"]
    if (v["U"] == "是") != commentary and v["Y"] not in ("待定", "待定交人"):
        add("U_CATEGORY", "U,Y", "U 解说值与 Y 解说类别不一致，需核边界", f"U={v['U']}；Y={v['Y']}")
    if v["V"] == "是":
        if commentary:
            add("V_COMMENTARY", "V,Y", "仅增加 BGM 与解说类别并存", v["Y"])
        if re.search(r"(?:新增|加入|添加|自制)[^，。；;\n]{0,6}"
                     r"(?:配音|解说字幕|特效|文字说明|点评字幕|画面素材|AI配图|自制画面)", new):
            add("V_OTHER_ELEMENTS", "V,AC", "仅增加 BGM，但 AC 还写了其他创作要素", new)
    if v["Q"] == "是" and not any(x in v["Y"] for x in ("完全无关", "引流", "错权")):
        add("Q_CATEGORY", "Q,Y", "Q 判完全不含动画，Y 却不在允许的三类", v["Y"])

    flags = row.get("flags") or []
    flagged = set().union(*(flag_columns(f) for f in flags if isinstance(f, dict))) if flags else set()
    for col in OUTPUT:
        if not v[col] and col not in flagged:
            add("UNFLAGGED_BLANK", col, "产出格为空，且无相应标黄或标红", col)
    if v["AA"] != "无法判断":
        add("AA_FIXED", "AA", "AA 应固定为无法判断", v["AA"])
    if not L_FORMAT.fullmatch(v["L"]):
        add("L_FORMAT", "L", "L 不符合 yyyy/m/d hh:mm:ss", v["L"])
    for col, source in (("D", "C"), ("F", "E")):
        field = (row.get("fields") or {}).get(col) or {}
        basis = str(field.get("basis") or "")
        old = str(original.get(source) or "").strip()
        if "照抄" in basis and old and v[col] != old:
            add("COPY_SOURCE", f"{col},{source}", f"{col} 标注照抄原表 {source}，内容却不同", f"{col}={v[col][:70]}；{source}={old[:70]}")
    return found


def _novel_text(case_dir: Path, rid: str) -> str:
    texts = []
    timeline = case_dir / "timeline" / rid / "timeline.json"
    if timeline.is_file():
        data = json.loads(timeline.read_text(encoding="utf-8"))
        for frame in data.get("frames") or []:
            texts += [str(x) for key in ("ocr", "captions") for x in frame.get(key) or []]
    audio = case_dir / "audio" / f"{rid}.json"
    if audio.is_file():
        data = json.loads(audio.read_text(encoding="utf-8"))
        texts.append(str(data.get("transcript") or ""))
    return " ".join(texts)


def run(case: TsxkCase, out_dir: Path) -> dict:
    source = case.read_inputs(rows=case.rows)  # Only configured input columns on 沈瑞曦.
    rows_dir = case.case_dir / "rows"
    results = []
    for rid in case.rows:
        path = rows_dir / f"{rid}.json"
        if not path.is_file():
            results.append({"row": rid, "type": "MISSING_ROW", "columns": "", "explanation": "分类结果不存在", "evidence": ""})
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        for code, cols, explanation, evidence in issues(row, source.get(rid, {}), _novel_text(case.case_dir, rid)):
            results.append({"row": rid, "type": code, "columns": cols,
                            "explanation": explanation, "evidence": evidence})
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "findings.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("row", "type", "columns", "explanation", "evidence"))
        writer.writeheader()
        writer.writerows(results)
    groups = defaultdict(list)
    for item in results:
        groups[item["type"]].append(item)
    counts = {code: len({x["row"] for x in items}) for code, items in sorted(groups.items())}
    lines = ["# 吞噬星空 400 行一致性审计", "", "只读本地结果；问题为待核线索，不自动改分类。", "",
             f"检查 {len(case.rows)} 行；发现 {len(results)} 项、涉及 {len({x['row'] for x in results})} 行。", "",
             "| 类型 | 行数 | 例子（每类至多 2 行） |", "|---|---:|---|"]
    for code, items in sorted(groups.items()):
        examples = []
        for item in items:
            if item["row"] not in [x.split("：", 1)[0] for x in examples]:
                examples.append(f"{item['row']}：{item['explanation']}")
            if len(examples) == 2:
                break
        lines.append(f"| {code} | {counts[code]} | {'；'.join(examples)} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"rows": len(case.rows), "findings": len(results), "affected_rows": len({x["row"] for x in results}), "types": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", nargs="?", default="cases/tsxk.yaml")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    case = TsxkCase(args.case)
    print(json.dumps(run(case, args.out or case.case_dir / "audit"), ensure_ascii=False))


if __name__ == "__main__":
    main()
