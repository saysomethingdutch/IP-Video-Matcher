"""Playback counts: icon ambiguity, page boundaries, votes and numeric AD export."""
import csv
import json
import zipfile
from types import SimpleNamespace

import openpyxl
import pytest
import yaml
from PIL import Image, ImageDraw

from pilot.review.test_tsxk_review import fixture
from pilot.review.tsxk import OUTPUT, TsxkReviewStore
from pilot.review.tsxk_export import export_tsxk
from pilot.review.tsxk_trial_verify import run
from pilot.tsxk.config import TsxkCase
from pilot.webledger.views import COLUMNS, count_box, parse_count, reread, summarize, target_observations


@pytest.mark.parametrize("raw,number", [("12.3万",123000), ("10万+",100000),
    ("1.2亿",120000000), ("口1414 日3 2026-03-24",1414),
    ("© 2755",2755), ("◎15.6万日32026-03-19",156000), ("回 0",0),
    ("30万",300000)])
def test_count_units_and_icons(raw, number):
    assert parse_count(raw)["value"] == number


def test_zero_icon_is_ambiguous_and_decimal_without_unit_is_incomplete():
    assert parse_count("030万")["ambiguous"]
    assert parse_count("030万")["value"] == 300000  # no hidden heuristic strip
    assert not parse_count("30万", digit_crop=True)["ambiguous"]
    assert parse_count("7.1") is None
    assert parse_count("2026-03-24 11:20:00") is None
    assert parse_count("无播放量") is None
    assert parse_count("0069", digit_crop=True)["ambiguous"]


def test_digit_crop_excludes_icons_and_comments_and_uses_context_for_orientation(tmp_path, monkeypatch):
    image = Image.new("RGB", (1920,1080), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((171,220,180,232), fill="gray")  # play icon
    draw.rectangle((193,220,228,232), fill="gray")  # count
    draw.rectangle((267,220,273,232), fill="gray")  # comment count
    item = {"y":.20, "h":.017}
    box = count_box(image, item)
    assert 180 < box[0] < 193 and 228 < box[2] < 267
    widths = []
    def vision(args, **kwargs):
        with Image.open(args[1]) as crop:
            widths.append(crop.width)
        text = "0069" if len(widths) == 1 else "6900 日1 2025-10-04"
        return SimpleNamespace(stdout=json.dumps({"items":[{"x":0,"text":text}]}), stderr="")
    monkeypatch.setattr("pilot.webledger.views.subprocess.run", vision)
    result = reread(image, item, tmp_path / "crop.png", tmp_path / "vision")
    assert result["value"] == 6900 and not result["ambiguous"]
    assert len(widths) == 2 and widths[1] > widths[0]


def frame(t, bv="BV18cwCzbEYs"):
    return {"t": t, "items": [
        {"x":.10,"y":.06,"w":.3,"h":.01,"text":f"bilibili.com/video/{bv}/"},
        {"x":.09,"y":.20,"w":.3,"h":.01,"text":"口1414 日27 2026-03-24"},
        {"x":.72,"y":.21,"w":.1,"h":.01,"text":"12.3万"},
        {"x":.14,"y":.20,"w":.1,"h":.01,"text":"27"}]}


def test_count_target_page_only_not_recommendation_comment_or_autoplay():
    records = [frame(10), frame(20,"BV12NwEzUESk"), frame(30)]
    result = target_observations(records, "BV18cwCzbEYs", 25)
    assert [r["t"] for r in result] == [10]
    assert result[0]["parsed"]["value"] == 1414
    # R3 tolerates an OCR glyph in the BV; an absent address fails closed.
    assert len(target_observations([frame(10,"BV18cwCzb议Ys")], "BV18cwCzbEYs", 25)) == 1
    assert not target_observations([{"t":10,"items":[frame(10)["items"][1]]}], "BV18cwCzbEYs", 25)


def test_vote_conflicts_and_same_frame_count():
    observations = [{"t":t, "parsed":parse_count(raw)} for t,raw in
                    [(10,"1.6万"),(20,"1.6万"),(30,"16万")]]
    result = summarize(observations)
    assert result["value"] == 16000 and result["agree"] == 2
    assert result["inconsistent"] and result["confidence"].startswith("需核对")


def views_fixture(tmp_path, header=False):
    source, config, data = fixture(tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["views"] = {"csv": str(data / "views.csv")}
    config.write_text(yaml.safe_dump(raw, allow_unicode=True))
    with (data / "views.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(dict(zip(COLUMNS,["7813","123000","12.3万","10;20;30",3,"多帧一致","crops/7813.png"])))
        writer.writerow(dict(zip(COLUMNS,["7814","100000","10万+","10;20",2,"需核对：读数不一致","crops/7814.png"])))
    if header:
        book = openpyxl.load_workbook(source)
        book["沈瑞曦"]["AD1"] = "播放量"
        book.save(source)
        book.close()
    return source, config, data


@pytest.mark.parametrize("header",[False,True])
def test_export_ad_is_numeric_preserves_other_cells_and_review_count(tmp_path, header):
    source, config, data = views_fixture(tmp_path, header)
    store = TsxkReviewStore(TsxkCase(config),
                            decisions_path=tmp_path / "trial-decisions.jsonl")
    assert len(store.fields("7813")) == len(OUTPUT) == 16
    dest = export_tsxk(store, tmp_path / "copy.xlsx")
    book = openpyxl.load_workbook(dest)
    sheet = book["沈瑞曦"]
    assert sheet["AD1"].value == "播放量"
    assert sheet["AD2"].value == 123000 and sheet["AD2"].data_type == "n"
    assert sheet["AD2"].comment is None
    assert sheet["AD3"].value == 100000
    assert "AD 播放量" in sheet["AD3"].comment.text
    assert sheet["AD3"].fill.fgColor.rgb == "FFFFF2B2"
    assert sheet["AD4"].value is None
    book.close()
    report = run(config, tmp_path / "verify", tmp_path / "trial-decisions.jsonl")
    assert report["target_cells"] == 34 and report["ad_header_added"] is not header
    with zipfile.ZipFile(source) as a, zipfile.ZipFile(dest) as b:
        assert a.read("xl/worksheets/sheet1.xml") == b.read("xl/worksheets/sheet1.xml")
    assert not (data / "review").exists()


def test_export_rejects_partial_views_not_silently_skipped(tmp_path):
    _, config, data = views_fixture(tmp_path)
    lines = (data / "views.csv").read_text(encoding="utf-8-sig").splitlines()
    (data / "views.csv").write_text("\n".join(lines[:2]),encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        export_tsxk(TsxkReviewStore(TsxkCase(config)),tmp_path / "bad.xlsx")
    assert not (tmp_path / "bad.xlsx").exists()
