"""WO-107 detached continuation: check gate → 299 rows → validate/backup/install.

Launch with zsh -lic and a detached Popen; this process remains PPID 1 during
the full run. It waits for the existing check process, never polls individual
rows, never repeats the check run, and stops on a failed gate.
"""
from __future__ import annotations

import argparse
import fcntl
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

from pilot.semantic import llm
from pilot.timeline.cloud import share_slots, THROTTLE
from pilot.timeline.owner_feedback import repair
from . import classify as K, fields as FD, sound as SD
from .config import TsxkCase
from .owner_feedback import (cached_inputs, classify_rows, decisions, digest, load,
                             prepare, score, span_score, values, COLUMNS)
from .pipeline import save


def state(root, phase, **extra):
    save({'phase': phase, 'pid': os.getpid(), 'ppid': os.getppid(),
          'at': datetime.now(timezone.utc).isoformat(), **extra}, root / 'v2/state.json')
    print(phase, extra, flush=True)


def reconcile(case, plan, system):
    """Reask only a row whose facts changed after a locally tested boundary fix.

    This compares prompts without consulting owner labels, and retains the
    earlier response. It does not redraw or retune the evaluation set.
    """
    changed = []
    inputs = cached_inputs(case, plan['check'])
    rules = case.rules()
    root = case.case_dir / 'v2/check'
    for rid in plan['check']:
        old = load(root / 'rows' / f'{rid}.json')
        tl = repair(load(case.case_dir / 'timeline' / rid / 'timeline.json'))
        led = case.webledger(rid)
        a, _ = FD.ledger_fields(inputs[rid], led)
        b, _, _ = FD.page_fields(led, rules)
        c, hints, _ = FD.timeline_fields(tl, led, (led or {}).get('title') or inputs[rid]['C'], rules)
        fields = {**a, **b, **c, 'AA': FD._f('无法判断', 'fixed', '')}
        fixed = {k: fields[k] for k in ('Q','W','X','S','AB','AA') + (('Y','Z') if hints['y_fixed'] else ())}
        audio = case.audio(rid)
        prompt = K.user_prompt(K.facts_text(inputs[rid], led, tl, fixed, hints, audio, SD.music_evidence(audio, tl)), hints['y_fixed'])
        if prompt != old['prompt']:
            prior = root / 'prior_boundary_fix' / f'{rid}.json'
            prior.parent.mkdir(exist_ok=True)
            shutil.move(root / 'rows' / f'{rid}.json', prior)
            changed.append(rid)
        save(tl, root / 'timeline' / f'{rid}.json')
    save({'rows': changed, 'reason': 'locally tested cached boundary inclusion fix; no label-based tuning'}, root / 'reconciliation.json')
    if changed:
        classify_rows(case, changed, root, system)
    save({'spans': span_score(case, root / 'spans')}, root / 'span_score.json')


def restart(case):
    """Restart only the known review command after an inactive period, retaining its port and records."""
    record = case.case_dir / 'review/decisions.jsonl'
    if time.time() - record.stat().st_mtime < 600:
        raise ValueError('review active within last 10 minutes; restart deferred')
    # Preflight the new store before interrupting the live server.
    from pilot.review.tsxk import TsxkReviewStore
    store = TsxkReviewStore(case)
    before = digest(record)
    port = subprocess.run(['lsof','-tiTCP:8765','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()
    if len(port) != 1:
        raise ValueError('expected exactly one existing review listener')
    pid = int(port[0])
    command = subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
    if '-m pilot review cases/tsxk.yaml' not in command:
        raise ValueError('8765 listener is not the known review command')
    if subprocess.run(['pgrep','-P',str(pid)],capture_output=True,text=True).stdout.strip():
        raise ValueError('unexpected child of review server')
    if digest(record) != before or time.time() - record.stat().st_mtime < 600:
        raise ValueError('review changed during restart preflight; restart deferred')
    os.kill(pid,signal.SIGTERM)
    for _ in range(40):
        if not subprocess.run(['lsof','-tiTCP:8765','-sTCP:LISTEN'],capture_output=True,text=True).stdout.strip():
            break
        time.sleep(.1)
    log = (case.case_dir / 'v2/review-server.log').open('ab',buffering=0)
    new = subprocess.Popen([sys.executable,'-m','pilot','review','cases/tsxk.yaml'],cwd=Path(__file__).resolve().parents[2],
                           stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(80):
        try:
            import json
            data = json.loads(urllib.request.urlopen('http://127.0.0.1:8765/api/rows',timeout=2).read())
            if len(data) == 400:
                assert digest(record) == before
                return {'old_pid': pid, 'new_pid': new.pid, 'rows': 400,
                        'reviewed_cells': sum(r['reviewed'] for r in data), 'decisions_unchanged': True}
        except (OSError, ValueError):
            time.sleep(.25)
    raise RuntimeError('new review service did not become ready; see v2/review-server.log')


def report(case, plan, result, phase, tests):
    check = case.case_dir / 'v2/check'
    cal = load(check / 'calibration.json')
    cols = '；'.join(f"{c} {s['old']}→{s['new']}" for c,s in result['per_column'].items())
    short = {'二创（单IP混剪类）':'单IP','二创（解说类）':'解说','二创（多IP混剪类）':'多IP','二创（其他类）':'其他二创','完全无关内容':'无关'}
    label = lambda value: short.get(value,value)
    six = '；'.join(f"{s['row']} {label(s['old'])}→{label(s['new'])}（人：{label(s['owner'])}，{'✓' if s['agree'] else '×'}）" for s in result['six_y'])
    spans = '；'.join(f"{s['row']} {'✓' if s['agree'] else '×'}" for s in result['spans'])
    cleanup = load(check / 'approved_cleanup.json')
    lines = ['# WO-107 交付', '', f'分支：`tsxk/wo-107-owner-feedback`；状态：**{phase}**。', '',
             '缓存修正结束页、全轮投票及连续两帧；动画画面统一按正片。人声和文字事实用中性称谓，采用所有者 5 条示例；模型【其他】禁填、禁用句及无旁证创作条目均过滤，原文存 `ac_removed`。短于 30 秒的待定段仅技术说明。', '',
             f'**检验**：18 行指定集与种子 20260930 的随机 30 行去重后 {len(plan["check"])} 行；门槛 {result["gates"]}。随机 Y 不同 {len(result["random_diff"]["Y"])} 行（≤3），Z 不同 {len(result["random_diff"]["Z"])} 行（≤6）；禁用命中 {len(result["banned_rows"])}，模型【其他】 {len(result["model_other_rows"])}。',
             f'一致数（旧→新，分母 {len(plan["check"])}）：{cols}。', f'六行 Y：{six}。',
             f'时间段 {sum(s["agree"] for s in result["spans"])}/14：{spans}。7876 两段保留 01:27–01:52、04:35–05:00，03:12–03:30 删除；7851 两家认其他动画，与所有者不同。',
             f'过滤校准（7884 起实际修改）：删除句命中 {cal["deleted_hit"]}/{cal["deleted_total"]}；保留句误删 {cal["kept_false_delete"]}/{cal["kept_total"]}。这 {cal["kept_false_delete"]} 句亦触及 D-077 明令禁用内容；仅统计，已核行不改。', '',
             f'**核对页**：Q/R/T/U/V/W/X/Y/Z 下拉、非法值标红、临时核验人提示、AC 条目删除并自动编号；清理入口列出 {cleanup["row_count"]} 行 {cleanup["sentence_count"]} 条，待所有者确认。临时页面已验证单次修改记录，未点清理确认。']
    m = case.case_dir / 'v2/metrics.json'
    if m.exists():
        metric = load(m)
        lines += ['', f'**全量 {metric["rows"]} 行**：']
        for c,d in metric['distributions'].items():
            lines.append(f'{c}（旧→新）：'+'；'.join(f'{label(v)} {d["old"].get(v,0)}→{d["new"].get(v,0)}' for v in sorted(set(d['old'])|set(d['new'])))+'。')
        lines += ['每列改值：'+'；'.join(f'{c} {n}' for c,n in metric['changed'].items())+'。',
                  f'【其他】句数 {metric["other_sentences"]["old"]}→{metric["other_sentences"]["new"]}；标红 {metric["red_rows"]} 行、重点 {metric["focus_rows"]} 行。']
    else:
        lines += ['', '**全量**：自查未达标，299 行未启动，未备份替换、未重启服务。']
    lines += ['', f'**验证**：{tests}。正式核验记录及已核 101 行哈希未变；逐行计分、校准、清单、截图与运行记录在 `workspace/tsxk/v2/`。未读张亚晨结论，未重看图、未传音频；不 push、不合并 main。']
    dest = Path(__file__).resolve().parents[2] / 'docs/workorders/WO-107-RESULT.md'
    dest.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-pid',type=int,required=True)
    args = parser.parse_args()
    case = TsxkCase('cases/tsxk.yaml'); root = case.case_dir
    plan = prepare(case)
    run_lock = (root/'v2/.run.lock').open('a')
    fcntl.flock(run_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    state(root,'WAITING_CHECK',check_pid=args.check_pid)
    while True:
        try:os.kill(args.check_pid,0)
        except ProcessLookupError:break
        time.sleep(60)  # process completion only; never a per-row poll
    if any(not (root/'v2/check/rows'/f'{r}.json').exists() for r in plan['check']):
        state(root,'CHECK_FAILED',reason='missing check output; see check/run.log');return 2
    llm.LOCAL_ALLOWED=False;llm.CALL_LOG=root/'usage/calls.jsonl';llm.CALL_RUN='WO-107 continuation'
    share_slots(root/'.cloud_slots');THROTTLE['zhipu'].limited()
    system=load(root/'v2/system.json')['system']
    reconcile(case,plan,system)
    result=score(case,plan)
    phase='自查未达标，已停止'
    if result['passed']:
        state(root,'RUNNING_299',gates=result['gates'])
        final,_=decisions(root/'review/decisions.jsonl')
        ids=[r for r in plan['remaining'] if r not in final]
        classify_rows(case,ids,root/'v2',system)
        from .feedback_install import verify_staged,metrics,install
        verify_staged(case,ids)
        save(metrics(case,ids,root/'rows',root/'v2/rows'),root/'v2/metrics.json')
        phase='299 行已完成、核验通过'
        if Path(case.raw['workbook']['path']).exists():
            receipt=install(case,plan);save(receipt,root/'v2/install_receipt.json')
            restarted=restart(case);save(restarted,root/'v2/restart_receipt.json')
            phase='299 行完成并替换，核对页已重启'
        else:
            phase='299 行已完成；外接盘未挂载，暂留 v2，未替换或重启'
        state(root,'RUN_FINISHED',status=phase)
    else:
        state(root,'GATE_FAILED',gates=result['gates'])
    # Full suite runs locally. A source-dependent skip is recorded, never hidden.
    test_log=root/'v2/tests.log'
    with test_log.open('w') as handle:
        test=subprocess.run([sys.executable,'-m','pytest','tests','pilot','-q','--tb=short'],stdout=handle,stderr=subprocess.STDOUT)
    if test.returncode:
        state(root,'TEST_FAILED',log=str(test_log));return 3
    summary=test_log.read_text().splitlines()[-1]
    report(case,plan,result,phase,summary)
    # Commit exactly the owned task files, leaving main and any foreign changes alone.
    repo=Path(__file__).resolve().parents[2]
    if subprocess.check_output(['git','branch','--show-current'],text=True,cwd=repo).strip()!='tsxk/wo-107-owner-feedback':
        raise ValueError('wrong branch; refusing commit')
    owned=load(root/'v2/code_files.json')['files']
    subprocess.run(['git','add','--',*owned,'docs/workorders/WO-107-RESULT.md'],cwd=repo,check=True)
    subprocess.run(['git','commit','-m','WO-107: owner feedback, cache repair, review controls and gated rerun'],cwd=repo,check=True)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    state(root,'DELIVERED',status=phase,commit=commit,tests=summary)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        case=TsxkCase('cases/tsxk.yaml');state(case.case_dir,'FAILED',error=f'{type(exc).__name__}: {exc}')
        raise
