"""Build a self-contained 20-row owner scoring page from already frozen blind opinions."""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from .case_config import CaseConfig
from .robustness import _paths, _sha


LABELS = ("甲", "乙", "丙")
SEED = 20260927
# Both batches are represented; the two available formal commentary rows are both kept.
QUOTAS = {
    ("qsjx_4086", "reaction"): 6,
    ("qsjx_2591", "reaction"): 2,
    ("qsjx_4086", "混剪"): 1,
    ("qsjx_2591", "混剪"): 9,
    ("qsjx_2591", "解说"): 2,
}


@dataclass(frozen=True)
class Candidate:
    case_id: str
    row_id: str
    title: str
    kind: str
    opinions: dict[str, str]
    key: dict[str, str]


def _kind(value: str | None) -> str | None:
    if value == "reaction":
        return "reaction"
    if value == "混剪":
        return "混剪"
    if value and "解说" in value:
        return "解说"
    return None


def _formal(text: str) -> bool:
    return (len(text) >= 100 and text.startswith("【使用的性质和目的】")
            and "【使用的比例】" in text and re.search(r"【结论】.+。$", text) is not None)


def _blind_rows(path: Path) -> dict[str, tuple[str, dict[str, str]]]:
    source = path.read_text(encoding="utf-8")
    rows = {}
    for row in re.finditer(r"^## 总编号 (\d+)\n(.*?)(?=^## 总编号 |\Z)", source, re.M | re.S):
        rid, block = row.groups()
        title = re.search(r"^标题：(.*)$", block, re.M)
        opinions = {part[1]: part[2].strip() for part in re.finditer(
            r"^### 意见 ([甲乙丙])\n\n(.*?)(?=^### 意见 [甲乙丙]$|\Z)", block, re.M | re.S)}
        if title and set(opinions) == set(LABELS):
            rows[rid] = (title[1], opinions)
    return rows


def _candidates(config: CaseConfig) -> list[Candidate]:
    root = config.case_dir / "robustness"
    freeze = json.loads((root / "machine_freeze.json").read_text(encoding="utf-8"))
    paths = _paths(config)
    if freeze["case_id"] != config.case_id or freeze["row_ids"] != config.row_ids or \
       freeze["machine_sha256"] != {name: _sha(path) for name, path in paths.items()}:
        raise RuntimeError(f"{config.case_id}: machine files changed after freeze")
    blind = _blind_rows(root / "blind_opinions.md")
    keys = json.loads((root / "key.json").read_text(encoding="utf-8"))["pairs"]
    found = []
    for rid, (title, opinions) in blind.items():
        if not all(_formal(opinions[label]) for label in LABELS):
            continue
        machine = json.loads((config.case_dir / "semantic" / f"{rid}.json").read_text(encoding="utf-8"))
        glm = machine.get("opinion") or {}
        deepseek = json.loads((root / "deepseek" / f"{rid}.json").read_text(encoding="utf-8"))
        if not all(item.get("kind") == "draft" and (item.get("validation") or {}).get("ok")
                   for item in (glm, deepseek)):
            continue
        kind = _kind((machine.get("classification") or {}).get("nature"))
        if kind and rid in keys and set(keys[rid]) == set(LABELS):
            found.append(Candidate(config.case_id, rid, title, kind, opinions, keys[rid]))
    return found


def _opinion_html(text: str) -> str:
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    return html.escape(plain)


def _page(rows: list[tuple[Candidate, dict[str, str]]]) -> str:
    cards = []
    for n, (row, opinions) in enumerate(rows, 1):
        rid = html.escape(row.row_id, quote=True)
        columns = []
        for label in LABELS:
            options = "".join(
                f'<label><input type="radio" name="score-{rid}-{label}" value="{grade}"> {grade}</label>'
                for grade in ("直接用", "小改", "大改", "不能用"))
            columns.append(f'<section class="opinion"><h3>意见 {label}</h3>'
                           f'<div class="opinion-text">{_opinion_html(opinions[label])}</div>'
                           f'<fieldset><legend>给意见 {label} 评分</legend><div class="choices">{options}</div></fieldset></section>')
        best = "".join(f'<label><input type="radio" name="best-{rid}" value="{label}"> {label}</label>'
                       for label in LABELS)
        cards.append(f'<article class="row" data-row="{rid}"><div class="row-head"><small>{n} / {len(rows)} · 总编号 {rid}</small>'
                     f'<h2>{html.escape(row.title)}</h2></div><div class="opinions">{"".join(columns)}</div>'
                     f'<div class="row-foot"><fieldset><legend>本行最好的一份</legend><div class="choices">{best}</div></fieldset>'
                     f'<label class="note">备注<textarea name="note-{rid}" rows="3" placeholder="可写修改建议或疑点"></textarea></label></div></article>')
    payload = json.dumps([(item.row_id, opinions) for item, opinions in rows], ensure_ascii=False, sort_keys=True)
    version = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return PAGE_START + "\n".join(cards) + PAGE_END.replace("blind20-owner-v1", f"blind20-owner-{version}")


PAGE_START = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>20 行意见盲评</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#203332;background:#f3f6f3}
*{box-sizing:border-box}body{margin:0}header{position:sticky;top:0;z-index:2;background:#173c3a;color:#fff;padding:15px max(20px,calc((100vw - 1460px)/2));display:flex;align-items:center;justify-content:space-between;gap:16px;box-shadow:0 3px 14px #10251f28}
header h1{font-size:19px;margin:0}header p{font-size:13px;color:#d3e7df;margin:4px 0 0}button{font:inherit;border:0;border-radius:7px;background:#d8f3e6;color:#123f37;padding:9px 16px;font-weight:700;cursor:pointer}button:hover{background:#b9e9d2}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #55b9a1;outline-offset:2px}
main{max-width:1500px;margin:0 auto;padding:20px}.intro{margin:0 0 18px;color:#45615a}.row{background:#fff;border:1px solid #d9e5df;border-radius:12px;margin:0 0 24px;overflow:hidden;box-shadow:0 2px 9px #183c2910}.row-head{padding:17px 21px;background:#e8f0ec;border-bottom:1px solid #d9e5df}.row-head small{color:#47675e}.row-head h2{font-size:18px;margin:5px 0 0;line-height:1.4}.opinions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}.opinion{padding:18px;border-right:1px solid #e3ebe6;min-width:0}.opinion:last-child{border-right:0}.opinion h3{margin:0 0 14px;font-size:17px;color:#205b51}.opinion-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;font-size:14px;min-height:220px}.opinion-text strong{font-weight:700}fieldset{border:0;margin:15px 0 0;padding:0}legend{font-size:13px;font-weight:700;color:#355e54;margin-bottom:7px}.choices{display:flex;flex-wrap:wrap;gap:7px 13px}.choices label{font-size:13px;white-space:nowrap;cursor:pointer}.choices input{accent-color:#247d67}.row-foot{border-top:1px solid #e3ebe6;padding:16px 21px;display:flex;flex-wrap:wrap;gap:14px 28px;align-items:start}.row-foot fieldset{margin:0}.note{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:700;color:#355e54;flex:1;min-width:230px}.note textarea{width:100%;font:inherit;font-weight:400;padding:9px;border:1px solid #c8d8d0;border-radius:6px;resize:vertical}
@media(max-width:1000px){.opinions{grid-template-columns:1fr}.opinion{border-right:0;border-bottom:1px solid #e3ebe6}.opinion:last-child{border-bottom:0}.opinion-text{min-height:0}}@media(max-width:560px){header{align-items:flex-start;flex-direction:column}main{padding:12px}.row-head,.opinion,.row-foot{padding:14px}}
</style></head><body><header><div><h1>20 行意见盲评</h1><p id="progress">加载中</p></div><button type="button" id="export">导出结果 CSV</button></header><main><p class="intro">逐份选“直接用 / 小改 / 大改 / 不能用”，再选本行最好的一份。填写会自动保存在此浏览器；页面无需网络。</p><form id="blind-form">
"""

PAGE_END = """</form></main><script>
const form=document.getElementById('blind-form');const cards=[...form.querySelectorAll('.row')];const storage='blind20-owner-v1';
function selected(name){return form.querySelector(`input[name="${name}"]:checked`)?.value||''}
function state(){return cards.map(card=>{const id=card.dataset.row;return {id,scores:['甲','乙','丙'].map(label=>selected(`score-${id}-${label}`)),best:selected(`best-${id}`),note:card.querySelector('textarea').value}})}
function progress(){const done=state().filter(row=>row.scores.every(Boolean)&&row.best).length;document.getElementById('progress').textContent=`${done} / ${cards.length} 行已评分 · 可随时导出`}
function save(){try{localStorage.setItem(storage,JSON.stringify(state()))}catch(_){}progress()}
try{const saved=JSON.parse(localStorage.getItem(storage)||'[]');for(const row of saved){const card=cards.find(card=>card.dataset.row===row.id);if(!card)continue;for(let i=0;i<3;i++){const input=card.querySelector(`input[name="score-${row.id}-${['甲','乙','丙'][i]}"][value="${row.scores?.[i]||''}"]`);if(input)input.checked=true}const best=card.querySelector(`input[name="best-${row.id}"][value="${row.best||''}"]`);if(best)best.checked=true;card.querySelector('textarea').value=row.note||''}}catch(_){}
form.addEventListener('change',save);form.addEventListener('input',save);progress();
function csvCell(value){let text=String(value??'');if(/^\\s*[=+\\-@]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"'}
document.getElementById('export').addEventListener('click',()=>{const table=[['总编号','标题','甲评分','乙评分','丙评分','最好','备注']];for(const row of state()){const card=cards.find(card=>card.dataset.row===row.id);table.push([row.id,card.querySelector('h2').textContent,...row.scores,row.best,row.note])}const csv='\\ufeff'+table.map(record=>record.map(csvCell).join(',')).join('\\r\\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='blind20_scores.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
</script></body></html>
"""


def generate(output: Path | None = None) -> dict:
    configs = [CaseConfig(f"cases/{name}.yaml") for name in ("qsjx_4086", "qsjx_2591")]
    pools: dict[tuple[str, str], list[Candidate]] = {key: [] for key in QUOTAS}
    for config in configs:
        for item in _candidates(config):
            key = (item.case_id, item.kind)
            if key in pools:
                pools[key].append(item)
    rng = random.Random(SEED)
    selected = []
    for key, count in QUOTAS.items():
        pool = sorted(pools[key], key=lambda item: int(item.row_id))
        if len(pool) < count:
            raise RuntimeError(f"not enough formal opinions for {key}: {len(pool)}/{count}")
        selected.extend(rng.sample(pool, count))
    rng.shuffle(selected)
    rows = []
    mapping = {}
    for item in selected:
        old_labels = list(LABELS)
        rng.shuffle(old_labels)
        opinions = {new: item.opinions[old] for new, old in zip(LABELS, old_labels)}
        mapping[item.row_id] = {new: item.key[old] for new, old in zip(LABELS, old_labels)}
        rows.append((item, opinions))
    page = _page(rows)
    output = output or configs[0].output_root / "robustness_owner"
    output.mkdir(parents=True, exist_ok=True)
    html_path = output / "blind20.html"
    html_path.write_text(page, encoding="utf-8")
    key = {"seed": SEED, "html_sha256": hashlib.sha256(page.encode()).hexdigest(),
           "selection": [{"case_id": item.case_id, "row_id": item.row_id, "machine_type": item.kind}
                         for item, _ in rows], "pairs": mapping}
    (output / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rows": len(rows), "type_counts": {kind: sum(item.kind == kind for item, _ in rows)
                                                for kind in ("reaction", "混剪", "解说")},
            "html": str(html_path), "key": str(output / "key.json")}


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False))
