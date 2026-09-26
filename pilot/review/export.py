"""Patch only case output cells in a copied OOXML workbook."""
from __future__ import annotations

import copy
import json
import os
import re
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from .data import MAIN, OUTPUT_COLS, R, ReviewStore, col_name

BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def rich_text(value: str):
    """Use CellRichText for the owner's **bold** notation."""
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont

    if not BOLD.search(value):
        return value
    chunks = CellRichText()
    pos = 0
    for match in BOLD.finditer(value):
        if match.start() > pos:
            chunks.append(value[pos:match.start()])
        chunks.append(TextBlock(InlineFont(b=True), match.group(1)))
        pos = match.end()
    if pos < len(value):
        chunks.append(value[pos:])
    return chunks


def xml_bytes(root):
    from lxml import etree
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def set_cell(cell, value: str):
    """Write an inline string while retaining the cell's existing style attribute."""
    from lxml import etree
    from openpyxl.cell.rich_text import CellRichText, TextBlock

    for child in list(cell):
        cell.remove(child)
    cell.set("t", "inlineStr")
    inline = etree.SubElement(cell, f"{{{MAIN}}}is")
    text = rich_text(value)
    if isinstance(text, CellRichText):
        for part in text:
            run = etree.SubElement(inline, f"{{{MAIN}}}r")
            if isinstance(part, TextBlock) and part.font.b:
                props = etree.SubElement(run, f"{{{MAIN}}}rPr")
                etree.SubElement(props, f"{{{MAIN}}}b")
            node = etree.SubElement(run, f"{{{MAIN}}}t")
            node.text = part.text if isinstance(part, TextBlock) else str(part)
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    else:
        node = etree.SubElement(inline, f"{{{MAIN}}}t")
        node.text = text
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def ensure_cell(row, col: int, excel_row: int):
    from lxml import etree
    address = f"{col_name(col)}{excel_row}"
    existing = row.find(f"{{{MAIN}}}c[@r='{address}']")
    if existing is not None:
        return existing
    cell = etree.Element(f"{{{MAIN}}}c", r=address)
    for index, other in enumerate(row):
        if other.tag != f"{{{MAIN}}}c":
            continue
        letters = re.match(r"[A-Z]+", other.get("r", ""))
        if letters:
            number = 0
            for char in letters.group():
                number = number * 26 + ord(char) - 64
            if number > col:
                row.insert(index, cell)
                return cell
    row.append(cell)
    return cell


def export_case(store: ReviewStore):
    from lxml import etree

    export_dir = store.case / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    dest = export_dir / f"{store.export_name}-{stamp}.xlsx"
    if dest.resolve() == store.source_path.resolve():
        raise ValueError("refusing to modify source workbook")
    tmp = export_dir / (dest.name + ".tmp")
    with zipfile.ZipFile(store.source_path) as source:
        workbook = etree.fromstring(source.read("xl/workbook.xml"))
        rels = etree.fromstring(source.read("xl/_rels/workbook.xml.rels"))
        content_types = etree.fromstring(source.read("[Content_Types].xml"))
        sheets = workbook.find(f"{{{MAIN}}}sheets")
        source_sheet = next(x for x in sheets if x.get("name") == store.sheet_name)
        rel_id = source_sheet.get(f"{{{R}}}id")
        target = next(x.get("Target") for x in rels if x.get("Id") == rel_id)
        sheet_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        sheet = etree.fromstring(source.read(sheet_path))
        data = sheet.find(f"{{{MAIN}}}sheetData")
        rows = {int(x.get("r")): x for x in data.findall(f"{{{MAIN}}}row")}
        styles = etree.fromstring(source.read("xl/styles.xml"))
        fills = styles.find(f"{{{MAIN}}}fills")
        xfs = styles.find(f"{{{MAIN}}}cellXfs")
        yellow_fill_id = None
        yellow_styles = {}

        def yellow_style(old_style: int) -> int:
            nonlocal yellow_fill_id
            if yellow_fill_id is None:
                yellow_fill_id = len(fills)
                fill = etree.SubElement(fills, f"{{{MAIN}}}fill")
                pattern = etree.SubElement(fill, f"{{{MAIN}}}patternFill", patternType="solid")
                etree.SubElement(pattern, f"{{{MAIN}}}fgColor", rgb="FFFFF2B2")
                etree.SubElement(pattern, f"{{{MAIN}}}bgColor", indexed="64")
                fills.set("count", str(len(fills)))
            if old_style not in yellow_styles:
                clone = copy.deepcopy(xfs[old_style])
                clone.set("fillId", str(yellow_fill_id))
                clone.set("applyFill", "1")
                yellow_styles[old_style] = len(xfs)
                xfs.append(clone)
                xfs.set("count", str(len(xfs)))
            return yellow_styles[old_style]

        provenance = [["总编号", "比对表单元格", "字段", "取值", "来源", "经所有者修改", "核对状态"]]
        for rid in store.row_ids:
            detail = store.detail(rid)
            excel_row = int(store.rows[rid]["plaintiff"]["source_row"])
            row = rows[excel_row]
            held = detail["status"] == "待定"
            original = store.source_rows[excel_row]
            for col in store.output_columns:
                field = detail["fields"][col - 1]
                value = "待定" if held else field["value"]
                coordinate = f"{col_name(col)}{excel_row}"
                if held or value != original[col - 1] or BOLD.search(value):
                    cell = ensure_cell(row, col, excel_row)
                    set_cell(cell, value)
                    if held:
                        cell.set("s", str(yellow_style(int(cell.get("s", "0")))))
                provenance.append([rid, coordinate, field["name"], value, "所有者待定" if held else field["source"], "是" if field["modified"] else "否", detail["status"]])

        new_id = max(int(x.get("sheetId")) for x in sheets) + 1
        new_rel = "rId" + str(max(int(x.get("Id")[3:]) for x in rels if x.get("Id", "").startswith("rId")) + 1)
        new_path = f"xl/worksheets/sheet{new_id}.xml"
        if new_path in source.namelist():
            raise ValueError("source sheet path collision")
        etree.SubElement(sheets, f"{{{MAIN}}}sheet", name="机器来源", sheetId=str(new_id), attrib={f"{{{R}}}id": new_rel})
        etree.SubElement(rels, f"{{{PKG_REL}}}Relationship", Id=new_rel, Type=f"{R}/worksheet", Target=f"worksheets/sheet{new_id}.xml")
        etree.SubElement(content_types, f"{{{CT}}}Override", PartName="/" + new_path, ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
        new_sheet = etree.Element(f"{{{MAIN}}}worksheet", nsmap={None: MAIN})
        etree.SubElement(new_sheet, f"{{{MAIN}}}dimension", ref=f"A1:G{len(provenance)}")
        sheet_data = etree.SubElement(new_sheet, f"{{{MAIN}}}sheetData")
        for i, record in enumerate(provenance, 1):
            line = etree.SubElement(sheet_data, f"{{{MAIN}}}row", r=str(i))
            for j, value in enumerate(record, 1):
                c = etree.SubElement(line, f"{{{MAIN}}}c", r=f"{col_name(j)}{i}")
                set_cell(c, str(value))
        changed = {sheet_path: xml_bytes(sheet), "xl/workbook.xml": xml_bytes(workbook), "xl/_rels/workbook.xml.rels": xml_bytes(rels), "[Content_Types].xml": xml_bytes(content_types)}
        if yellow_fill_id is not None:
            changed["xl/styles.xml"] = xml_bytes(styles)
        with zipfile.ZipFile(tmp, "w") as output:
            for info in source.infolist():
                output.writestr(info, changed.get(info.filename, source.read(info.filename)))
            output.writestr(new_path, xml_bytes(new_sheet), compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, dest)
    corrections = export_dir / "corrections.jsonl"
    with corrections.open("w", encoding="utf-8") as handle:
        for record in store.corrections():
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return dest
