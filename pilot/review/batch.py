"""Delegated modifications: preview, explicit confirmation and old-value CAS.

The file contains row,column,old,new,reason. Reading/previewing never writes.
Each applied event retains delegate and reason; mismatched old values are skipped.
"""
import argparse
import csv
from datetime import datetime,timezone
import fcntl
import hashlib
import json
from pathlib import Path


def read_list(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    return data.get('items') if isinstance(data,dict) else data


def normalize(items):
    if not isinstance(items,list) or len(items)>6400:raise ValueError('清单必须是最多 6400 条的列表')
    result=[];seen=set()
    for item in items:
        aliases={'row':('row','行'),'column':('column','field','列'),'old':('old','旧值'),'new':('new','新值'),'reason':('reason','理由')}
        value={k:next((item[a] for a in names if a in item),None) for k,names in aliases.items()}
        if any(value[k] is None for k in ('row','column','old','new','reason')):raise ValueError('每条都需要行、列、旧值、新值和理由')
        value={k:str(v) for k,v in value.items()};value['column']=value['column'].upper()
        if not value['reason'].strip():raise ValueError('理由不能为空')
        key=(value['row'],value['column'])
        if key in seen:raise ValueError('清单中有重复单元格')
        seen.add(key);result.append(value)
    return result


def token(items):return hashlib.sha256(json.dumps(items,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def current(store,rid,col,external=None):
    from openpyxl.utils import column_index_from_string
    if rid not in store.row_ids:raise ValueError('行不在本案核验范围')
    if hasattr(store,'fields'):
        field=next((f for f in store.fields(rid) if f['column']==col),None)
        if field is None:raise ValueError('列不可核验')
        if field.get('options') is not None:allowed=field['options']
        else:allowed=None
        event_col=col
    else:
        index=column_index_from_string(col)
        if index not in store.output_columns:raise ValueError('列不可核验')
        field=next(f for f in store._machine_fields(rid) if f['index']==index)
        allowed=None;event_col=str(index)
    value=field['value']
    if external and (rid,event_col) in external:value=external[rid,event_col]['new']
    return str(value or ''),field,event_col,allowed


def preview(store,items):
    items=normalize(items);proposal=[]
    with store.lock:
        for item in items:
            try:
                value,field,col,allowed=current(store,item['row'],item['column'])
                problem='旧值不一致' if value!=item['old'] else '新值不合规' if allowed is not None and item['new'] not in allowed else ''
            except (ValueError,KeyError,StopIteration):value=None;problem='行或列不在范围'
            proposal.append({**item,'current':value,'apply':not bool(problem),'skip_reason':problem})
    return {'items':proposal,'token':token(items),'matched':sum(x['apply'] for x in proposal),'skipped':sum(not x['apply'] for x in proposal)}


def apply(store,items,*,delegate,confirmed=False,preview_token=None):
    items=normalize(items);delegate=str(delegate or '').strip()
    if confirmed is not True:raise ValueError('请先查看清单并明确确认')
    if not delegate or len(delegate)>80:raise ValueError('请填写代办人姓名')
    if preview_token!=token(items):raise ValueError('清单已变化，请重新预览')
    applied=[];skipped=[];events=[]
    with store.lock:
        store.decisions_path.parent.mkdir(parents=True,exist_ok=True)
        with store.decisions_path.open('a+',encoding='utf-8') as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX);handle.seek(0)
            external={}
            for line in handle:
                if line.strip():
                    e=json.loads(line);external[str(e['row']),str(e['field'])]=e
            for item in items:
                try:
                    value,field,col,allowed=current(store,item['row'],item['column'],external)
                    if value!=item['old']:raise ValueError('旧值不一致')
                    if allowed is not None and item['new'] not in allowed:raise ValueError('新值不合规')
                    if value==item['new']:raise ValueError('新旧值相同')
                except (ValueError,KeyError,StopIteration) as exc:
                    skipped.append({**item,'skip_reason':str(exc)});continue
                prior=external.get((item['row'],col),{})
                e={'when':datetime.now(timezone.utc).isoformat(),'who':'Owner 代办','delegate':delegate,
                   'row':item['row'],'field':col,'action':'modify','old':value,'new':item['new'],
                   'reason':item['reason'],'machine':field.get('machine_value',field.get('value','')),
                   'retain_mark':prior.get('retain_mark',False),'mark_note':prior.get('mark_note','')}
                events.append(e);applied.append(item)
            handle.seek(0,2);handle.write(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events));handle.flush()
            import os
            os.fsync(handle.fileno())
        if hasattr(store,'latest'):
            for e in events:store.events[e['row']].append(e);store.latest[e['row']][e['field']]=e
        else:
            for e in events:store._apply(e)
    return {'applied':len(applied),'skipped':skipped,'delegate':delegate}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('case');p.add_argument('list',type=Path);p.add_argument('--delegate');p.add_argument('--confirm',action='store_true');p.add_argument('--decisions',type=Path)
    args=p.parse_args()
    import yaml
    raw=yaml.safe_load(Path(args.case).read_text())
    if raw.get('schema')=='tsxk-web-1':
        from .tsxk import TsxkReviewStore
        from pilot.tsxk.config import TsxkCase
        store=TsxkReviewStore(TsxkCase(args.case),decisions_path=args.decisions)
    else:
        from pilot.case_config import CaseConfig
        from .data import ReviewStore
        config=CaseConfig(args.case)
        store=ReviewStore(config.case_dir,config=config,decisions_path=args.decisions)
    items=read_list(args.list);proposal=preview(store,items)
    if args.confirm:print(json.dumps(apply(store,items,delegate=args.delegate,confirmed=True,preview_token=proposal['token']),ensure_ascii=False))
    else:print(json.dumps(proposal,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
