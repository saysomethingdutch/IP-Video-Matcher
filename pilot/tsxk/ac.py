"""AC (视频说明) assembled: the model's five sections, then the lines code writes into【其他】— times, areas
the recording does not show, the episode the title names (指引 二（十五）, WO-102 §3)."""
from __future__ import annotations

HEADINGS = (("主题概括", "【主题概括】"), ("段落情节", "【段落情节】"), ("新增创作要素", "【新增创作要素情况】"),
            ("比对意见", "【比对意见】"), ("其他", "【其他】"))


def _listed(items: list[str]) -> str:
    return "\n".join(f"{i}.{str(x).strip().rstrip('；;')}" for i, x in enumerate(items, 1))


def assemble(sections: dict, code_lines: list[str]) -> str:
    parts = []
    for key, heading in HEADINGS:
        value = sections.get(key)
        if key == "新增创作要素":
            body = _listed(value) if value else "无"
        elif key == "其他":
            items = [str(x) for x in (value or [])] + [x for x in code_lines if x]
            body = _listed(items)
        else:
            body = str(value or "").strip() or "无"
        parts.append(f"{heading}\n{body}".rstrip())
    return "\n\n".join(parts)
