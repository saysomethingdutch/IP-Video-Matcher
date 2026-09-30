"""Copy the G-group workbook and patch only 沈瑞曦's configured output cells.

The original OOXML parts are copied byte-for-byte except the target worksheet,
its relationships, styles (new fill variants only), and package content types.
"""
from __future__ import annotations

import copy
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lxml import etree

from .data import MAIN, R, col_name
from .export import ensure_cell, set_cell, xml_bytes
from .tsxk import OUTPUT, TsxkReviewStore
from .tsxk_views import load_views

PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
VML = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
EXCEL = "urn:schemas-microsoft-com:office:excel"
COLORS = {"yellow": "FFFFF2B2", "red": "FFF7C8C3"}


def _sheet_path(source: zipfile.ZipFile, name: str) -> str:
    workbook = etree.fromstring(source.read("xl/workbook.xml"))
    rels = etree.fromstring(source.read("xl/_rels/workbook.xml.rels"))
    sheets = workbook.find(f"{{{MAIN}}}sheets")
    found = next((x for x in sheets if x.get("name") == name), None)
    if found is None:
        raise ValueError(f"source workbook lacks sheet {name}")
    rid = found.get(f"{{{R}}}id")
    target = next(x.get("Target") for x in rels if x.get("Id") == rid)
    return target.lstrip("/") if target.startswith("/") else "xl/" + target


def _fill_style(styles, old_style: int, level: str, cache: dict) -> int:
    fills = styles.find(f"{{{MAIN}}}fills")
    xfs = styles.find(f"{{{MAIN}}}cellXfs")
    if not 0 <= old_style < len(xfs):
        raise ValueError(f"invalid source cell style {old_style}")
    if level not in cache:
        fill_id = len(fills)
        fill = etree.SubElement(fills, f"{{{MAIN}}}fill")
        pattern = etree.SubElement(fill, f"{{{MAIN}}}patternFill", patternType="solid")
        etree.SubElement(pattern, f"{{{MAIN}}}fgColor", rgb=COLORS[level])
        etree.SubElement(pattern, f"{{{MAIN}}}bgColor", indexed="64")
        fills.set("count", str(len(fills)))
        cache[level] = {"fill_id": fill_id, "styles": {}}
    variants = cache[level]["styles"]
    if old_style not in variants:
        xf = copy.deepcopy(xfs[old_style])
        xf.set("fillId", str(cache[level]["fill_id"]))
        xf.set("applyFill", "1")
        variants[old_style] = len(xfs)
        xfs.append(xf)
        xfs.set("count", str(len(xfs)))
    return variants[old_style]


def _comments_parts(comments: list[tuple[str, int, int, str]], index: int) -> tuple[bytes, bytes]:
    root = etree.Element(f"{{{MAIN}}}comments", nsmap={None: MAIN})
    authors = etree.SubElement(root, f"{{{MAIN}}}authors")
    etree.SubElement(authors, f"{{{MAIN}}}author").text = "机器核对提示"
    entries = etree.SubElement(root, f"{{{MAIN}}}commentList")
    vml = etree.Element("xml")
    layout = etree.SubElement(vml, f"{{{OFFICE}}}shapelayout")
    layout.set(f"{{{VML}}}ext", "edit")
    idmap = etree.SubElement(layout, f"{{{OFFICE}}}idmap")
    idmap.set(f"{{{VML}}}ext", "edit")
    idmap.set("data", str(index))
    shape_type = etree.SubElement(vml, f"{{{VML}}}shapetype", id="_x0000_t202",
                                  coordsize="21600,21600", path="m,l,21600r21600,l21600,xe")
    shape_type.set(f"{{{OFFICE}}}spt", "202")
    etree.SubElement(shape_type, f"{{{VML}}}stroke", joinstyle="miter")
    shape_path = etree.SubElement(shape_type, f"{{{VML}}}path", gradientshapeok="t")
    shape_path.set(f"{{{OFFICE}}}connecttype", "rect")
    for number, (address, row_number, column_number, reason) in enumerate(comments):
        note = etree.SubElement(entries, f"{{{MAIN}}}comment", ref=address,
                                authorId="0", shapeId=str(number))
        text = etree.SubElement(note, f"{{{MAIN}}}text")
        etree.SubElement(text, f"{{{MAIN}}}t").text = reason[:3000]
        shape = etree.SubElement(vml, f"{{{VML}}}shape", id=f"_x0000_s{1026+number}",
                                 type="#_x0000_t202",
                                 style="position:absolute;margin-left:59.25pt;margin-top:1.5pt;width:220px;height:100px;z-index:1;visibility:hidden",
                                 fillcolor="#ffffe1")
        shape.set(f"{{{OFFICE}}}insetmode", "auto")
        etree.SubElement(shape, f"{{{VML}}}fill", color2="#ffffe1")
        etree.SubElement(shape, f"{{{VML}}}shadow", color="black", obscured="t")
        path = etree.SubElement(shape, f"{{{VML}}}path")
        path.set(f"{{{OFFICE}}}connecttype", "none")
        textbox = etree.SubElement(shape, f"{{{VML}}}textbox", style="mso-direction-alt:auto")
        etree.SubElement(textbox, "div", style="text-align:left")
        client = etree.SubElement(shape, f"{{{EXCEL}}}ClientData", ObjectType="Note")
        etree.SubElement(client, f"{{{EXCEL}}}MoveWithCells")
        etree.SubElement(client, f"{{{EXCEL}}}SizeWithCells")
        etree.SubElement(client, f"{{{EXCEL}}}AutoFill").text = "False"
        etree.SubElement(client, f"{{{EXCEL}}}Row").text = str(row_number - 1)
        etree.SubElement(client, f"{{{EXCEL}}}Column").text = str(column_number - 1)
    return xml_bytes(root), etree.tostring(vml, encoding="UTF-8")


def _add_comments(source: zipfile.ZipFile, sheet, sheet_path: str,
                  comments: list[tuple[str, int, int, str]], changed: dict[str, bytes]) -> dict[str, bytes]:
    if not comments:
        return {}
    names = set(source.namelist())
    folder, basename = sheet_path.rsplit("/", 1)
    rel_path = f"{folder}/_rels/{basename}.rels"
    rels = etree.fromstring(source.read(rel_path)) if rel_path in names else etree.Element(f"{{{PKG_REL}}}Relationships", nsmap={None: PKG_REL})
    if any(x.get("Type", "").endswith(("/comments", "/vmlDrawing")) for x in rels):
        raise ValueError("target sheet already has comments or VML; refusing to replace them")
    used = {int(m.group(1)) for n in names if (m := re.fullmatch(r"xl/comments/comment(\d+)\.xml", n))}
    used.update(int(m.group(1)) for n in names if (m := re.fullmatch(r"xl/drawings/commentsDrawing(\d+)\.vml", n)))
    index = max(used, default=0) + 1
    comments_path = f"xl/comments/comment{index}.xml"
    drawing_path = f"xl/drawings/commentsDrawing{index}.vml"
    ids = {x.get("Id") for x in rels}
    next_id = max((int(x[3:]) for x in ids if x and re.fullmatch(r"rId\d+", x)), default=0) + 1
    comment_id, drawing_id = f"rId{next_id}", f"rId{next_id+1}"
    etree.SubElement(rels, f"{{{PKG_REL}}}Relationship", Id=comment_id,
                     Type=f"{R}/comments", Target="/" + comments_path)
    etree.SubElement(rels, f"{{{PKG_REL}}}Relationship", Id=drawing_id,
                     Type=f"{R}/vmlDrawing", Target="/" + drawing_path)
    if sheet.find(f"{{{MAIN}}}legacyDrawing") is not None:
        raise ValueError("target sheet already has a legacy drawing")
    etree.SubElement(sheet, f"{{{MAIN}}}legacyDrawing", attrib={f"{{{R}}}id": drawing_id})
    types = etree.fromstring(source.read("[Content_Types].xml"))
    if not any(x.get("Extension") == "vml" for x in types):
        default = etree.Element(f"{{{CT}}}Default", Extension="vml",
                                ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing")
        first_override = next((i for i, item in enumerate(types)
                               if etree.QName(item).localname == "Override"), len(types))
        types.insert(first_override, default)
    etree.SubElement(types, f"{{{CT}}}Override", PartName="/" + comments_path,
                     ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml")
    comment_xml, vml_xml = _comments_parts(comments, index)
    changed[rel_path] = xml_bytes(rels)
    changed["[Content_Types].xml"] = xml_bytes(types)
    changed[comments_path] = comment_xml
    changed[drawing_path] = vml_xml
    return changed


def _explanation(store: TsxkReviewStore, dest: Path, pending: list[tuple[str, list[str]]],
                 views: dict | None = None, header_added: bool = False) -> Path:
    lines = ["# 吞噬星空导出说明", "",
             f"副本：{dest.name}", f"工作表：{store.sheet_name}；序号 {store.row_ids[0]}–{store.row_ids[-1]}。",
             f"仅写 D、F、L、Q–AC{'、AD' if views is not None else ''}；未核验的列照机器值写入，重点标记带颜色与机器批注。",
             "已核验的格子默认不带机器标记；勾选保留的格子按原级别上色，批注为所有者说明。", "",
             f"## 未核验（{len(pending)} 行）", ""]
    if views is not None:
        lines[7:7] = ["AD 来自独立播放量核对表 views.csv，不计入逐列核验。读数待核的 AD 带黄标与批注。",
                      "副本补上 AD1 表头“播放量”。原表未改。" if header_added else "AD1 原有“播放量”表头保留。", ""]
    lines += [f"- {rid}：{'、'.join(columns)}" for rid, columns in pending] or ["无"]
    changes = store.corrections()
    lines += ["", f"## 修改记录（{len(changes)} 次）", ""]
    lines += [f"- {event['row']} {event['field']}：{event['old']} → {event['new']}；"
              f"{event['who']}，{event['when']}" for event in changes] or ["无"]
    path = dest.with_suffix(".md")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_tsxk(store: TsxkReviewStore, destination: Path | None = None) -> Path:
    export_dir = store.case / "export"
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    dest = Path(destination) if destination else export_dir / f"{store.export_name}-{stamp}.xlsx"
    if dest.resolve() == store.source_path.resolve():
        raise ValueError("refusing to modify source workbook")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prepared = {}
    pending = []
    views = load_views(store.config)
    for rid in store.row_ids:
        fields, flags = store.export_row(rid)
        prepared[rid] = fields
        unreviewed = [f["column"] for f in fields if not f["reviewed"]]
        if unreviewed:
            pending.append((rid, unreviewed))
        if views is not None:
            fields.append(views[rid])
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(store.source_path) as source:
        sheet_path = _sheet_path(source, store.sheet_name)
        sheet = etree.fromstring(source.read(sheet_path))
        data = sheet.find(f"{{{MAIN}}}sheetData")
        rows = {int(row.get("r")): row for row in data.findall(f"{{{MAIN}}}row")}
        styles = etree.fromstring(source.read("xl/styles.xml"))
        cache = {}
        comments = []
        header_added = False
        if views is not None:
            header_added = len(store.headers) < 30 or str(store.headers[29] or "").strip() != "播放量"
            if header_added:
                header = rows.get(1)
                if header is None:
                    header = etree.Element(f"{{{MAIN}}}row", r="1")
                    data.insert(0, header)
                set_cell(ensure_cell(header, 30, 1), "播放量")
            dimension = sheet.find(f"{{{MAIN}}}dimension")
            if dimension is not None:
                bounds = dimension.get("ref").split(":")
                last = bounds[-1]
                match = re.fullmatch(r"([A-Z]+)(\d+)", last)
                if match and openpyxl_column(match[1]) < 30:
                    dimension.set("ref", f"{bounds[0]}:AD{match[2]}")
        for rid in store.row_ids:
            row_number = store.excel_rows[rid]
            row = rows.get(row_number)
            if row is None:
                raise ValueError(f"missing Excel row {row_number} for {rid}")
            for field in prepared[rid]:
                col = field["column"]
                index = openpyxl_column(col)
                address = f"{col}{row_number}"
                cell = ensure_cell(row, index, row_number)
                if col == "AD" and isinstance(field["value"], int):
                    for child in list(cell):
                        cell.remove(child)
                    cell.set("t", "n")
                    etree.SubElement(cell, f"{{{MAIN}}}v").text = str(field["value"])
                else:
                    set_cell(cell, field["value"])
                level = field["flag_level"]
                if level and (not field["reviewed"] or field["retain_mark"]):
                    cell.set("s", str(_fill_style(styles, int(cell.get("s", "0")), level, cache)))
                    reason = (f"所有者说明：{field['mark_note']}" if field["reviewed"] else
                              "\n".join(f"{'标红' if x['level'] == 'red' else '标黄'}：{x['reason']}"
                                        for x in field["flag_details"]))
                    comments.append((address, row_number, index, reason))
        changed = {sheet_path: xml_bytes(sheet)}
        if cache:
            changed["xl/styles.xml"] = xml_bytes(styles)
        _add_comments(source, sheet, sheet_path, comments, changed)
        changed[sheet_path] = xml_bytes(sheet)
        try:
            with zipfile.ZipFile(tmp, "w") as output:
                for info in source.infolist():
                    output.writestr(info, changed.pop(info.filename, source.read(info.filename)))
                for name, payload in changed.items():
                    output.writestr(name, payload, compress_type=zipfile.ZIP_DEFLATED)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    _explanation(store, dest, pending, views, header_added)
    return dest


def openpyxl_column(letter: str) -> int:
    # Local helper avoids importing a workbook writer in the production export path.
    number = 0
    for char in letter:
        number = number * 26 + ord(char) - 64
    return number
