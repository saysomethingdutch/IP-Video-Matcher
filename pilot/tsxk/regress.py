"""WO-108 offline regression against a frozen owner-reviewed answer snapshot.

Only development examples may be printed. Holdout results contain aggregate
counts, never row-level errors. Splits are public row IDs; answers remain local.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re

from .ac_filter import BANNED, parse
from .config import TsxkCase
from .pipeline import save

COLUMNS = ('D','F','L',*('Q','R','S','T','U','V','W','X','Y','Z','AA','AB','AC'))
SEED = 20261002
SPLIT = Path(__file__).resolve().parents[2] / 'cases/tsxk_regression_split.json'


def canonical(value):
    return '' if value is None else str(value).strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(case, *, split_path=SPLIT, output=None):
    output = Path(output or case.case_dir/'wo108/regression')
    if not split_path.exists():
        ids=list(case.rows);random.Random(SEED).shuffle(ids)
        save({'seed':SEED,'development':sorted(ids[:300],key=int),
              'holdout':sorted(ids[300:],key=int)},split_path)
    groups=json.loads(split_path.read_text())
    dev,test=groups['development'],groups['holdout']
    if len(dev)!=300 or len(test)!=100 or set(dev)&set(test) or set(dev+test)!=set(case.rows):
        raise ValueError('invalid fixed 300/100 split')
    record=case.case_dir/'review/decisions.jsonl';target=output/'answers.json'
    if not target.exists():
        final={r:{} for r in case.rows}
        for line in record.read_text().splitlines():
            if not line.strip():continue
            event=json.loads(line);rid=str(event['row']);col=event['field']
            if rid in final and col in COLUMNS:final[rid][col]=event['new']
        if any(set(COLUMNS)-v.keys() for v in final.values()):
            raise ValueError('owner review is incomplete; final values must come from records')
        save({'record_sha256':sha(record),'split_sha256':sha(split_path),
              'created':datetime.now(timezone.utc).isoformat(),'answers':final},target)
    snapshot=json.loads(target.read_text())
    if snapshot['record_sha256']!=sha(record) or snapshot['split_sha256']!=sha(split_path):
        raise ValueError('records or split changed after freeze; use a new output directory')
    return groups,snapshot


def item_key(text):
    return re.sub(r'[\s。；;，,]+','',text)


def evaluate(machine_dir, answers, groups, *, development_errors=False):
    machine_dir=Path(machine_dir);rows={}
    for rid in groups['development']+groups['holdout']:
        path=machine_dir/f'{rid}.json'
        if not path.exists():raise ValueError(f'missing machine row {rid}')
        row=json.loads(path.read_text());fields=row.get('fields',{})
        if set(COLUMNS)-fields.keys():raise ValueError(f'incomplete machine fields {rid}')
        rows[rid]={c:fields[c]['value'] for c in COLUMNS}
    result={'machine_dir':str(machine_dir.resolve()),'split_seed':SEED,'banned_pattern':BANNED.pattern,'sets':{}}
    for group,ids in {**groups,'all':groups['development']+groups['holdout']}.items():
        if group not in ('development','holdout','all'):continue
        counts={c:sum(canonical(rows[r][c])==canonical(answers[r][c]) for r in ids) for c in COLUMNS}
        deleted=Counter();missing_rows=Counter();banned=0
        for rid in ids:
            raw,owner=parse(rows[rid]['AC']),parse(answers[rid]['AC'])
            # Compare numbered items, not paragraphs. A changed/rephrased item
            # counts as absent from the final text, not proven semantic deletion.
            for section in ('新增创作要素','其他'):
                final_items=Counter(item_key(t) for t in owner[section])
                before_items=Counter(item_key(t) for t in raw[section])
                count=sum((before_items-final_items).values())
                deleted[section]+=count;missing_rows[section]+=bool(count)
            banned+=bool(BANNED.search(str(rows[rid]['AC'] or '')))
        result['sets'][group]={'rows':len(ids),'agreement':counts,
              'ac_items_absent_from_final':dict(deleted),'ac_item_rows':dict(missing_rows),
              'banned_rows':banned}
    if development_errors:
        result['development_errors']=[{'row':rid,'column':c,'machine':rows[rid][c],'final':answers[rid][c]}
            for rid in groups['development'] for c in COLUMNS
            if canonical(rows[rid][c])!=canonical(answers[rid][c])]
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',default='cases/tsxk.yaml');p.add_argument('--machine-dir',type=Path)
    p.add_argument('--label',default='candidate');p.add_argument('--out',type=Path)
    p.add_argument('--development-errors',action='store_true')
    args=p.parse_args();case=TsxkCase(args.case);out=args.out or case.case_dir/'wo108/regression'
    groups,snapshot=freeze(case,output=out)
    candidates={args.label:args.machine_dir} if args.machine_dir else {'v1':case.case_dir/'v1/rows','r1':case.case_dir/'rows'}
    for name,directory in candidates.items():
        result=evaluate(directory,snapshot['answers'],groups,development_errors=args.development_errors)
        result.update(answer_sha256=sha(out/'answers.json'),source_record_sha256=snapshot['record_sha256'])
        save(result,out/f'{name}.json')
        print(name,json.dumps(result['sets'],ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
