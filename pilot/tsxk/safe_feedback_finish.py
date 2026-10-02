"""D-078 detached check/default install, followed by detached alternatives.

No free rerun of all categories, no image reading, no changes to reviewed rows.
Every cloud subprocess is launched through zsh -lic. No recurring monitor.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from .ac_filter import parse
from .config import TsxkCase
from .feedback_finish import restart
from .feedback_install import install, verify_staged, metrics
from .owner_feedback import decisions, digest, load, values
from .pipeline import save
from .safe_feedback import (prepare, cloud, run_rows, score, VERSION, SPEECH,
                            NARRATIVE, SINGLE, refresh_groups)

REPO=Path(__file__).resolve().parents[2]


def state(case, phase, *, alternative=False, **extra):
    save({'phase':phase,'at':datetime.now(timezone.utc).isoformat(),'pid':os.getpid(),
          'ppid':os.getppid(),**extra},case.case_dir/('v2/alt/state.json' if alternative else 'v2/r1state.json'))
    print(phase,extra,flush=True)


def source_check(case):
    receipt=load(case.case_dir/'v2/r1_code_files.json')
    if any(digest(REPO/f)!=sha for f,sha in receipt['sha256'].items()):
        raise ValueError('task source changed after detached launch; automatic installation/commit deferred')
    return receipt['files']


def commit(case, files, message):
    if subprocess.check_output(['git','branch','--show-current'],cwd=REPO,text=True).strip()!='tsxk/wo-107-owner-feedback':
        raise ValueError('wrong branch; refusing commit')
    source_check(case)
    subprocess.run(['git','add','--',*files],cwd=REPO,check=True)
    subprocess.run(['git','commit','-m',message],cwd=REPO,check=True)
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()


def sample_attachment(case, plan):
    base=case.case_dir/'v2/r1check'
    by_group={g:[r for r in plan['protected'] if plan['groups'][r]['group']==g] for g in ('A','B','C')}
    candidates=by_group['A']
    cleaned=[r for r in candidates if any(x.get('reason')=='禁用说法' for x in load(base/'rows'/f'{r}.json').get('ac_removed',[]))]
    selected=[('A',r) for r in (cleaned+[r for r in candidates if r not in cleaned])[:3]]
    selected += [(g,by_group[g][0]) for g in ('B','C')]
    lines=['# WO-107 R1：五份视频说明全文','', '配套交付说明的抽读附件；全部来自已核行的 v2/r1check 试跑，未安装。','']
    for group,rid in selected:
        lines += [f'## {group} 类 · {rid}','',values(load(base/'rows'/f'{rid}.json'))['AC'],'']
    dest=REPO/'docs/workorders/WO-107-R1-AC-SAMPLES.md';dest.write_text('\n'.join(lines),encoding='utf-8')
    save({'samples':selected},base/'report_samples.json')


def report(case, plan, result, phase, tests):
    count=dict(Counter(plan['groups'][r]['group'] for r in plan['remaining']))
    stats=result['agreement'];a=result['gates']['a'];b=result['gates']['b']
    text=['## R1','',f'状态：**{phase}**。原默认值保留，仅 A/B/C 确定性处理；分句过滤保留未命中的事实。',
          f'**自查 a**：{a}；101 行 Y {stats["Y"]["old"]}→{stats["Y"]["new"]}（至少 95），7909={result["row7909"]}；W {stats["W"]["old"]}→{stats["W"]["new"]}；空段/禁用表述/模型【其他】缺陷 {len(result["defects"])} 行。',
          f'**自查 b**：{b}；'+ '；'.join(f'{s["row"]} Y={s["Y"]}、U={s["U"]}、V={s["V"]}、Z={s["Z"]}' for s in result['alternatives'])+'。',
          f'**分组**：A {count.get("A",0)}，B {count.get("B",0)}，C {count.get("C",0)}；原无关片段变更 {sum(plan["groups"][r]["unrelated_changed"] for r in plan["remaining"])} 行；清理后残缺回退重写 {sum(bool(plan["groups"][r]["rewrite_sections"]) for r in plan["remaining"])} 行。',
          '**抽读 c**：正文保持一页，随附 [三份 A、一份 B、一份 C 的视频说明全文](WO-107-R1-AC-SAMPLES.md)，请 Owner 抽读。',
          '**核对页**：备用版按钮仅在所有者点击时同时修改 Y/U/V/Z/AC；每次开行重新读取。Z 改值先展示同步后的比对意见，保存时记录 Z、AC；清理清单增加已核 Y/U/W 和等级不一致的建议，未点击正式确认。']
    receipt=case.case_dir/'v2/r1_install_receipt.json'
    if receipt.exists():
        r=load(receipt);text += [f'**安装**：{r["at"]}；替换 {len(r["installed"])} 行，跳过 {len(r["skipped"])}；v1 备份、fields.csv 和审计已重建，127.0.0.1:8765 已重启。7997、8011 的 Q/Y 联合标红待核值和 11 行 R 待核值保留；修正安装核验对关联 Y 的识别，没有补猜值。']
    else:text += ['**安装**：未安装、未重启。' + ('默认结果待生成及核验。' if result['passed'] else '按门槛停止。')]
    ids_path=case.case_dir/'v2/alt/plan.json';ids=load(ids_path)['rows'] if ids_path.exists() else []
    done=sum((case.case_dir/'v2/alt'/f'{r}.json').exists() for r in ids)
    text += [f'**备用版进度**：{done}/{len(ids)}；普通后台运行，PPID=1；未完成的显示“备用版生成中”。' if ids else '**备用版进度**：未启动。',
             f'**验证**：{tests}；UI 验证仅使用临时核验存储，截图与计分在 `workspace/tsxk/v2/r1check/`。初始已核 101 行保留原文件哈希；程序未修改正式核验记录。不 push、不合并 main。','']
    dest=REPO/'docs/workorders/WO-107-RESULT.md';previous=dest.read_text(encoding='utf-8').split('\n\n## R1')[0].rstrip()
    dest.write_text(previous+'\n\n'+'\n'.join(text),encoding='utf-8')


def alternative_run(case, plan):
    lock=(case.case_dir/'v2/alt/.lock').open('a');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    ids=load(case.case_dir/'v2/alt/plan.json')['rows'];state(case,'GENERATING',alternative=True,total=len(ids))
    cloud(case);run_rows(case,ids,case.case_dir/'v2/alt',plan,alternative=True)
    for rid in ids:
        row=load(case.case_dir/'v2/alt'/f'{rid}.json');v=values(row)
        if v['Y']!=SINGLE or v['U']!='否' or v['V'] not in ('是','否') or v['Z'] not in ('高','中','低') or any(parse(v['AC'])[k] in ('','无') for k in NARRATIVE):
            raise ValueError(f'invalid alternative {rid}')
    # Wait for the parent's tested code/report commit before updating only progress.
    parent=load(case.case_dir/'v2/alt/process.json').get('parent_pid')
    while parent:
        try:os.kill(parent,0)
        except ProcessLookupError:break
        time.sleep(60)
    result=load(case.case_dir/'v2/r1check/score.json')
    parent_state=load(case.case_dir/'v2/r1state.json')
    if parent_state['phase']!='DELIVERED':
        state(case,'COMPLETE_UNCOMMITTED',alternative=True,total=len(ids),completed=len(ids),reason='parent delivery needs attention');return
    tests=parent_state.get('tests','见 r1tests.log')
    report(case,plan,result,'默认结果已安装，备用版全部完成',tests)
    revision=commit(case,['docs/workorders/WO-107-RESULT.md'],'WO-107-R1: record completed owner-selectable alternatives')
    state(case,'COMPLETE',alternative=True,total=len(ids),completed=len(ids),commit=revision)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check-pid',type=int);parser.add_argument('--alternatives',action='store_true')
    args=parser.parse_args();case=TsxkCase('cases/tsxk.yaml');plan=prepare(case);root=case.case_dir/'v2'
    if args.alternatives:return alternative_run(case,plan)
    if not args.check_pid:raise ValueError('--check-pid required')
    lock=(root/'.r1run.lock').open('a');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    state(case,'WAITING_CHECK',check_pid=args.check_pid)
    while True:
        try:os.kill(args.check_pid,0)
        except ProcessLookupError:break
        time.sleep(60)
    source_check(case)
    changed=refresh_groups(case,plan)
    if changed:
        state(case,'RECONCILING_CHECK',rows=changed);cloud(case);run_rows(case,changed,root/'r1check',plan)
    result=score(case,plan);phase='自查未达标，已停止'
    test_log=root/'r1tests.log'
    with test_log.open('w') as handle:
        tested=subprocess.run([sys.executable,'-m','pytest','tests','pilot','-q','--tb=short'],stdout=handle,stderr=subprocess.STDOUT)
    if tested.returncode:raise ValueError('full test suite failed; see r1tests.log')
    tests=test_log.read_text().splitlines()[-1]
    sample_attachment(case,plan)
    report(case,plan,result,'自查通过，默认结果准备中' if result['passed'] else phase,tests)
    if result['passed']:
        cloud(case);latest,_=decisions(case.case_dir/'review/decisions.jsonl')
        ids=[r for r in plan['remaining'] if r not in latest]
        state(case,'RUNNING_DEFAULTS',rows=len(ids));run_rows(case,ids,root,plan)
        verify_staged(case,ids)
        for rid in ids:
            row=load(root/'rows'/f'{rid}.json');v=values(row);group=plan['groups'][rid]['group']
            if row.get('safe_feedback_version')!=VERSION or any(parse(v['AC'])[k] in ('','无') for k in NARRATIVE):raise ValueError(f'R1 preflight failed {rid}')
            if digest(case.case_dir/'rows'/f'{rid}.json')!=plan['original_rows_sha256'][rid]:raise ValueError(f'original row changed {rid}')
            if group in ('A','B') and v['Y']!=plan['groups'][rid]['original_y']:raise ValueError(f'default category changed unexpectedly {rid}')
            if group=='A':
                old=values(load(case.case_dir/'rows'/f'{rid}.json'))
                if any(v[c]!=old[c] for c in ('U','V','Z') if not (c=='Z' and old['Y']=='完全无关内容')):
                    raise ValueError(f'A values unexpectedly changed {rid}')
        source_check(case)
        r=install(case,plan,score_path=root/'r1check/score.json');r['at']=datetime.now(timezone.utc).isoformat()
        restarted=restart(case);save(restarted,root/'r1_restart_receipt.json');save(r,root/'r1_install_receipt.json')
        ids=[r for r in plan['remaining'] if values(load(case.case_dir/'rows'/f'{r}.json'))['Y']==SPEECH]
        save({'rows':ids},root/'alt/plan.json')
        command='exec '+shlex.quote(sys.executable)+' -u -m pilot.tsxk.safe_feedback_finish --alternatives'
        with (root/'alt/run.log').open('ab',buffering=0) as handle:
            child=subprocess.Popen(['/bin/zsh','-lic',command],cwd=REPO,stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
        save({'pid':child.pid,'parent_pid':os.getpid(),'total':len(ids)},root/'alt/process.json')
        phase='默认结果已安装，备用版后台生成中';state(case,'INSTALLED',alt_pid=child.pid,alternatives=len(ids))
    else:state(case,'GATE_FAILED',gates=result['gates'])
    report(case,plan,result,phase,tests)
    files=source_check(case)+['docs/workorders/WO-107-RESULT.md','docs/workorders/WO-107-R1-AC-SAMPLES.md']
    revision=commit(case,files,'WO-107-R1: preserve default categories, safe install and owner-selectable alternatives')
    state(case,'DELIVERED',status=phase,commit=revision,tests=tests)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        state(TsxkCase('cases/tsxk.yaml'),'FAILED',alternative='--alternatives' in sys.argv,error=f'{type(exc).__name__}: {exc}')
        raise
