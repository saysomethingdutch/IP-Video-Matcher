"""D-078 default A/B/C staging and separately generated, owner-selected alternatives.

Uses only existing timelines, original machine rows and authorized owner records.
Cloud calls are text only; this module must be launched with zsh -lic.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re

from pilot.semantic import llm
from pilot.timeline.cloud import share_slots, THROTTLE
from pilot.timeline.owner_feedback import repair
from . import ac as A, fields as FD, sound as SD
from .ac_filter import BANNED, filter_ac, parse
from .config import TsxkCase
from .owner_feedback import cached_inputs, decisions, digest, load, norm, owner_system, values
from .pipeline import process_row, save, span, text_evidence

VERSION = 'D-078-1'
ALT_CHECK = ['7819', '7841', '7884', '7906']
SINGLE = '二创（单IP混剪类）'
SPEECH = '二创（解说类）'
NARRATIVE = ('主题概括', '段落情节', '比对意见')


def unrelated(tl):
    return [(s['start'], s['end']) for s in tl.get('segments', []) if s.get('content') == 'unrelated']


def local_row(case, rid, old, original, tl):
    """No model call. Original Y/U/V/Z remain default values."""
    out = deepcopy(old)
    led = case.webledger(rid) or {}
    rules = case.rules()
    fields, hints, flags = FD.timeline_fields(tl, led, led.get('title') or values(old)['D'], rules)
    for c in ('Q', 'W', 'X'):
        out['fields'][c] = fields[c]
    # Existing other forms retain their wording/order; only proven forms survive.
    forms = [f for f in ('漫画', '手游', '小说', '有声书') if f in str(values(old)['T'])]
    words = text_evidence(tl, case.audio(rid))
    forms = [f for f in forms if f not in words or words[f]]
    out['fields']['T'] = FD._f('是（'+'、'.join(forms)+'）' if forms else '否', 'code', 'D-078：不区分动画剧场版；小说等须有文字依据')
    if values(old)['Y'] == '完全无关内容':
        out['fields']['Z'] = FD._f('不涉及', 'code', '完全无关内容不涉及合理使用（D-077）')
    sections = parse(values(old)['AC'])
    cleaned, removed = filter_ac(sections, out['fields'], tl)
    _, notes, _ = FD.page_fields(led, rules)
    promo = SD.promotions(case.audio(rid))
    promo_lines = [f"{span(p['start'],p['end'])}出现推广口播，{'、'.join(p['kinds'])}" for p in promo] if values(out)['X'] != '否' else []
    lines = FD.time_lines(tl, out['fields'], values(out)['Y']) + promo_lines + notes + [x for x in (FD.episode_note(led.get('title') or values(old)['D']),) if x]
    out['fields']['AC'] = FD._f(A.assemble(cleaned, lines), 'code', 'D-078：按分句清理，【其他】由修正后的缓存时间线填写')
    out['flags'] = [f for f in out.get('flags', []) if not f.get('reason','').startswith('时间线：') and '剧场版' not in f.get('reason','')]
    out['flags'] += flags + [{**f, 'reason':'时间线：'+f['reason']} for f in tl.get('flags', [])]
    out.update(ac_removed=removed, ac_code_lines=lines, owner_feedback_version='D-077-1', safe_feedback_version=VERSION)
    deficit = [k for k in NARRATIVE if cleaned[k].strip() in ('','无') or len(re.sub(r'\s','',cleaned[k])) <= len(re.sub(r'\s','',sections[k])) / 2]
    before_creative=''.join(sections['新增创作要素']);after_creative=''.join(cleaned['新增创作要素'])
    if before_creative and len(after_creative)<=len(before_creative)/2:
        deficit.append('新增创作要素')
    changed = unrelated(original) != unrelated(tl)
    group = 'C' if values(old)['Y'] in ('偷渡', '二创（多IP混剪类）') and not unrelated(tl) else 'B' if changed or deficit else 'A'
    out['safe_feedback'] = {'group':group, 'unrelated_changed':changed, 'rewrite_sections':deficit,
                            'original_y':values(old)['Y'], 'kept_original': ['Y','U','V','Z'] if group=='A' else []}
    return out


def prepare(case):
    root = case.case_dir/'v2';target=root/'r1plan.json'
    if target.exists():return load(target)
    final, _ = decisions(case.case_dir/'review/decisions.jsonl')
    staged = {}
    for rid in case.rows:
        old = load(case.case_dir/'rows'/f'{rid}.json')
        original = load(case.case_dir/'timeline'/rid/'timeline.json')
        tl = repair(original)
        row = local_row(case, rid, old, original, tl)
        staged[rid] = row['safe_feedback']
        base = root/'r1check' if rid in final else root
        save(row, base/'local'/f'{rid}.json')
        save(tl, base/'timeline'/f'{rid}.json')
    plan = {'created':datetime.now(timezone.utc).isoformat(),'version':VERSION,
            'protected':sorted(final,key=int),'remaining':[r for r in case.rows if r not in final],
            'owner_values':final, 'decisions_sha256':digest(case.case_dir/'review/decisions.jsonl'),
            'protected_rows_sha256':{r:digest(case.case_dir/'rows'/f'{r}.json') for r in final},
            'original_rows_sha256':{r:digest(case.case_dir/'rows'/f'{r}.json') for r in case.rows},
            'groups':staged}
    system, info = owner_system(case, final)
    save({'system':system,**info}, root/'r1system.json')
    save(plan,target)
    return plan


def refresh_groups(case, plan):
    """Reconcile a tested residual-section fix, without changing the sample or owner labels."""
    changed=[];root=case.case_dir/'v2'
    for rid in case.rows:
        original=load(case.case_dir/'timeline'/rid/'timeline.json');tl=repair(original)
        row=local_row(case,rid,load(case.case_dir/'rows'/f'{rid}.json'),original,tl)
        base=root/'r1check' if rid in plan['protected'] else root
        save(row,base/'local'/f'{rid}.json')
        if row['safe_feedback']!=plan['groups'][rid]:
            changed.append(rid);plan['groups'][rid]=row['safe_feedback']
            staged=base/'rows'/f'{rid}.json'
            if staged.exists():
                prior=base/'prior_residual_fix'/f'{rid}.json';prior.parent.mkdir(parents=True,exist_ok=True)
                staged.replace(prior)
    save(plan,root/'r1plan.json')
    save({'rows':changed,'reason':'residual check includes creation section; frozen 101 rows and owner labels unchanged'},root/'r1check/group_reconciliation.json')
    return [r for r in changed if r in plan['protected']]


def run_rows(case, ids, base, plan, *, alternative=False):
    rules=case.rules();system=load(case.case_dir/'v2/r1system.json')['system']
    needs = [r for r in ids if alternative or plan['groups'][r]['group'] != 'A']
    inputs = cached_inputs(case, needs) if needs else {}
    def one(rid):
        dest = base/f'{rid}.json' if alternative else base/'rows'/f'{rid}.json'
        if dest.exists() and load(dest).get('safe_feedback_version') == VERSION:return rid, 'cached'
        local_root = case.case_dir/'v2/r1check' if rid in plan['protected'] else case.case_dir/'v2'
        row = load(local_root/'local'/f'{rid}.json')
        group = plan['groups'][rid]['group']
        if alternative or group != 'A':
            tl = load(local_root/'timeline'/f'{rid}.json')
            y = SINGLE if alternative else values(row)['Y'] if group == 'B' else None
            row = process_row(inputs[rid],case.webledger(rid),tl,rules,system,'zhipu',case.audio(rid),
                              fixed_y=y,fixed_u='否' if alternative else None,
                              disallow_y=('偷渡','二创（多IP混剪类）') if group=='C' and not alternative else ())
            row.update(safe_feedback_version=VERSION,safe_feedback=plan['groups'][rid])
        row.update(row=rid)
        if alternative:row['alternative']={'basis':'所有者采用：没有 UP 主自己的讲述，人声字幕均为原片台词', 'columns':['Y','U','V','Z','AC']}
        save(row,dest)
        return rid, 'alternative' if alternative else group
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(one,r) for r in ids]):
            rid, group = future.result();print(f'completed {rid} ({group})',flush=True)


def score(case, plan):
    base=case.case_dir/'v2/r1check'
    ids=plan['protected'];human=plan['owner_values']
    new={r:load(base/'rows'/f'{r}.json') for r in ids}
    old={r:load(case.case_dir/'rows'/f'{r}.json') for r in ids}
    agreement={c:{'old':sum(norm(values(old[r])[c])==norm(human[r][c]) for r in ids),
                  'new':sum(norm(values(new[r])[c])==norm(human[r][c]) for r in ids)} for c in ('Y','W','U','V','Z')}
    defects=[]
    for r,row in new.items():
        v=values(row);ac=parse(v['AC'])
        if BANNED.search(v['AC']) or ac['其他']!=row['ac_code_lines'] or any(ac[k] in ('','无') for k in NARRATIVE):defects.append(r)
    alt=[{'row':r,**{c:values(load(base/'alt'/f'{r}.json'))[c] for c in ('Y','U','V','Z')}} for r in ALT_CHECK]
    a=agreement['Y']['new']>=max(95,agreement['Y']['old']) and values(new['7909'])['Y']==SINGLE and agreement['W']['new']>=agreement['W']['old'] and not defects
    b=all(x['Y']==SINGLE and x['U']=='否' for x in alt) and sum(x['Z']=='低' for x in alt)>=3 and sum(x['V']=='否' for x in alt)>=3
    result={'passed':bool(a and b),'gates':{'a':bool(a),'b':bool(b)},'agreement':agreement,
            'row7909':values(new['7909'])['Y'],'defects':defects,'alternatives':alt,
            'groups':dict(Counter(plan['groups'][r]['group'] for r in ids))}
    save(result,base/'score.json');return result


def cloud(case):
    llm.LOCAL_ALLOWED=False;llm.CALL_LOG=case.case_dir/'usage/calls.jsonl';llm.CALL_RUN='WO-107-R1'
    share_slots(case.case_dir/'.cloud_slots');THROTTLE['zhipu'].limited()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','check','score','defaults','alternatives'))
    args=parser.parse_args();case=TsxkCase('cases/tsxk.yaml');plan=prepare(case);root=case.case_dir/'v2'
    if args.command=='prepare':
        print({scope:dict(Counter(plan['groups'][r]['group'] for r in plan[scope])) for scope in ('protected','remaining')});return
    if args.command=='score':print(score(case,plan));return
    cloud(case)
    if args.command=='check':
        import fcntl
        check_lock=(root/'r1check/.check.lock').open('a')
        fcntl.flock(check_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        run_rows(case,plan['protected'],root/'r1check',plan)
        run_rows(case,ALT_CHECK,root/'r1check/alt',plan,alternative=True)
        result=score(case,plan);print('SELF_CHECK',result,flush=True)
        return 0 if result['passed'] else 2
    if not load(root/'r1check/score.json')['passed']:raise ValueError('R1 self-check failed')
    if args.command=='defaults':run_rows(case,plan['remaining'],root,plan)
    else:
        ids=[r for r in plan['remaining'] if values(load(case.case_dir/'rows'/f'{r}.json'))['Y']==SPEECH]
        run_rows(case,ids,root/'alt',plan,alternative=True)


if __name__=='__main__':
    import sys
    sys.exit(main())
