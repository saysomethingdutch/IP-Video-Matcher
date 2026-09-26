"""Local-only D-043 development-set check; never reads held-out human columns."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from pilot.case_config import CaseConfig
from pilot.textindex.search import Index
from pilot.textindex.text import normalize

from .pipeline import run


SOURCE_TYPES = {"切条", "原片"}
COMMENTARY_TYPE = "解说"


def commentary_caption(content: dict, index: Index) -> bool:
    """Only a non-source bottom caption shown during verified player frames counts."""
    if int((content.get("counts") or {}).get("player_frames") or 0) < 3:
        return False
    for event in content.get("events") or []:
        if event.get("status") != "NO_MATCH" or event.get("category") != "底部解说字幕":
            continue
        text = event.get("text") or ""
        if len(normalize(text)) >= 6 and not index.search(text, min_sim=.5, top=1):
            return True
    return False


def validate(config: CaseConfig, selected: list[str]) -> dict:
    rows = {row["row_id"]: row for row in (json.loads(line) for line in
            (config.case_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line)}
    index = Index.load(config.index_dir / "index.sqlite")
    counts = Counter()
    details = []
    for rid in selected:
        row = rows[rid]
        expected = row["plaintiff"]["video_type"] == COMMENTARY_TYPE
        audio = json.loads((config.case_dir / "audio" / f"{rid}.json").read_text(encoding="utf-8"))
        if audio.get("status") != "ok":
            counts["unscored"] += 1
            details.append({"row_id": rid, "expected_type": row["plaintiff"]["video_type"],
                            "status": audio.get("status"), "reason": audio.get("reason") or audio.get("error")})
            continue
        content_path = config.case_dir / "content" / f"{rid}.json"
        content = json.loads(content_path.read_text(encoding="utf-8")) if content_path.is_file() else {}
        oral = bool(audio.get("uploader_speech"))
        caption = commentary_caption(content, index)
        predicted = oral or caption
        cell = "TP" if expected and predicted else "FN" if expected else "FP" if predicted else "TN"
        counts[cell] += 1
        counts["source_with_oral_candidate"] += int(not expected and oral)
        counts["commentary_with_oral_candidate"] += int(expected and oral)
        counts["commentary_with_caption"] += int(expected and caption)
        details.append({"row_id": rid, "expected_type": row["plaintiff"]["video_type"],
                        "cell": cell, "oral_candidate": oral, "caption_commentary": caption,
                        "uploader_clause_count": len(audio.get("uploader_speech") or []),
                        "player_frames": (content.get("counts") or {}).get("player_frames", 0)})
    result = {"case_id": config.case_id, "basis": "原告 A–T 的视频类型；只比较开发集切条/原片/解说，未读人工 U–AJ",
              "positive_rule": "至少一条不少于6字的口播候选，或至少一条已播放画面的非原剧解说字幕",
              "counts": dict(counts), "rows": details}
    out = config.case_dir / "audio" / "validation_d043.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case_config")
    args = parser.parse_args()
    config = CaseConfig(args.case_config)
    rows = [json.loads(line) for line in (config.case_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    selected = [row["row_id"] for row in rows if row["plaintiff"].get("video_type") in SOURCE_TYPES | {COMMENTARY_TYPE}]
    summary = run(config, selected)
    if summary["error"]:
        raise RuntimeError(f"pilot audio failed for {summary['error']} rows")
    result = validate(config, selected)
    print(json.dumps({"audio": summary, "confusion": result["counts"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
