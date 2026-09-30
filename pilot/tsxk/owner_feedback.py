"""WO-107 owner-only examples, deterministic evaluation and protected staging.

No colleague sheet or images are read. 'check' and the gated 'run' invoke GLM,
and never write formal rows. A failed gate never starts the 299-row run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import sys

from pilot.semantic import llm
from pilot.timeline.cloud import share_slots, THROTTLE
from pilot.timeline.owner_feedback import repair
from . import classify as K
from . import ac as A
from .ac_filter import BANNED, SENTENCES, cleanup_other, parse, filter_ac
from .config import TsxkCase
from .pipeline import process_row, save
from .samples import scrub

CHANGED = '7813 7815 7816 7819 7833 7840 7841 7851 7852 7854 7857 7860 7864 7876 7884 7906 7909 8212'.split()
Y_SIX = '7819 7841 7851 7884 7906 7909'.split()
EXAMPLES = '7817 7818 7822 7834 7856'.split()
SPANS = {'7873': True, '8212': True, '7876': [(87, 112), (275, 300)],
         **{r: False for r in '7814 7815 7851 7860 7884 7894 7896 7898 7900 7903 7909'.split()}}
COLUMNS = 'D F L Q R S T U V W X Y Z AA AB AC'.split()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decisions(path: Path) -> tuple[dict, list]:
    events = [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    final = {}
    for e in events:
        final.setdefault(str(e['row']), {})[e['field']] = e['new']
    return final, events


def owner_system(case: TsxkCase, final: dict | None = None) -> tuple[str, dict]:
    if final is None:
        final, _ = decisions(case.case_dir / 'review/decisions.jsonl')
    examples = []
    for rid in EXAMPLES:
        d = final[rid]
        ac = parse(d['AC'])
        ac, _ = filter_ac(ac)  # examples must not teach the prohibited wording
        ac['其他'] = []
        examples.append({'title': d['D'], **{c: d[c] for c in ('Y', 'U', 'V', 'Z')},
                         'AC': scrub(A.assemble(ac, []))})
    return K.SYSTEM.format(standards=K.standards(case.rules()), examples=K.examples_text(examples, [])), {
        'owner_examples': EXAMPLES, 'guidance_examples': 0, 'colleague_examples': 0, 'colleague_skipped': True}


def values(row: dict) -> dict:
    return {c: row['fields'].get(c, {}).get('value') for c in COLUMNS}


def cached_inputs(case: TsxkCase, ids: list) -> dict:
    """Replay only inputs actually used by the saved classification.

    The original title/address were copied verbatim when the ledger matched.
    When it did not match, the ledger wins again; K is unused when a certificate
    time exists. Keep an explicit source receipt for an unmounted input drive.
    """
    if Path(case.raw['workbook']['path']).is_file():
        return case.read_inputs(rows=ids)
    result = {}
    for rid in ids:
        old = load(case.case_dir / 'rows' / f'{rid}.json')
        v = values(old)
        match = re.search(r'【UP主】([^\n]*)', old.get('prompt', ''))
        result[rid] = {'B': rid, 'C': v['D'], 'E': v['F'], 'K': v['L'], 'G': match[1] if match else ''}
    save({'source': '既有逐行输出与已发送事实；原工作簿未挂载', 'rows': ids}, case.case_dir / 'v2/input_cache_receipt.json')
    return result


def norm(value):
    return '二创（单IP混剪类）' if value == '二创（单IP混剪）' else value


def calibration(events: list) -> dict:
    deleted, kept = [], []
    for e in events:
        if e['field'] != 'AC' or e['action'] != 'modify' or int(e['row']) < 7884:
            continue
        old = [re.sub(r'^\s*\d+[.、．]', '', s).strip() for s in SENTENCES.findall(e['old'])
               if not s.lstrip().startswith('【')]
        new = [re.sub(r'^\s*\d+[.、．]', '', s).strip() for s in SENTENCES.findall(e['new'])
               if not s.lstrip().startswith('【')]
        deleted += [{'row': e['row'], 'text': s, 'hit': bool(BANNED.search(s))} for s in old if s not in new]
        kept += [{'row': e['row'], 'text': s, 'hit': bool(BANNED.search(s))} for s in old if s in new]
    return {'deleted_total': len(deleted), 'deleted_hit': sum(s['hit'] for s in deleted),
            'kept_total': len(kept), 'kept_false_delete': sum(s['hit'] for s in kept),
            'deleted': deleted, 'kept': kept}


def span_score(case: TsxkCase, out: Path) -> list:
    result = []
    for rid, expected in SPANS.items():
        tl = repair(load(case.case_dir / 'timeline' / rid / 'timeline.json'))
        save(tl, out / 'timeline' / f'{rid}.json')
        actual = [(s['start'], s['end']) for s in tl['segments'] if s['content'] == 'unrelated']
        agree = actual == expected if isinstance(expected, list) else bool(actual) == expected
        result.append({'row': rid, 'expected': expected, 'actual': actual, 'agree': agree})
    return result


def prepare(case: TsxkCase) -> dict:
    root = case.case_dir / 'v2'
    path = root / 'plan.json'
    if path.exists():
        return load(path)  # no redrawing an evaluation set after seeing its score
    final, events = decisions(case.case_dir / 'review/decisions.jsonl')
    approved = sorted((r for r, d in final.items() if set(COLUMNS) <= d.keys() and r not in EXAMPLES), key=int)
    selected = random.Random(20260930).sample(approved, 30)
    ids = sorted(set(CHANGED + selected), key=int)
    assert not set(ids) & set(EXAMPLES)
    plan = {'created': datetime.now(timezone.utc).isoformat(), 'seed': 20260930, 'examples': EXAMPLES,
            'changed': CHANGED, 'random': selected, 'check': ids,
            'protected': sorted(final, key=int), 'remaining': [r for r in case.rows if r not in final],
            'decisions_sha256': digest(case.case_dir / 'review/decisions.jsonl'),
            'protected_rows_sha256': {r: digest(case.case_dir / 'rows' / f'{r}.json') for r in final},
            'owner_values': final}
    save(plan, path)
    save(calibration(events), root / 'check/calibration.json')
    save({'spans': span_score(case, root / 'check')}, root / 'check/span_score.json')
    system, info = owner_system(case, final)
    save({'system': system, **info}, root / 'system.json')
    return plan


def classify_rows(case: TsxkCase, ids: list, out: Path, system: str) -> None:
    inputs = cached_inputs(case, ids)
    rules = case.rules()
    def one(rid):
        dest = out / 'rows' / f'{rid}.json'
        if dest.exists() and load(dest).get('owner_feedback_version') == 'D-077-1':
            return rid, True
        tl = repair(load(case.case_dir / 'timeline' / rid / 'timeline.json'))
        save(tl, out / 'timeline' / f'{rid}.json')
        result = process_row(inputs[rid], case.webledger(rid), tl, rules, system, 'zhipu', case.audio(rid))
        result['row'] = rid
        save(result, dest)
        return rid, False
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(one, r) for r in ids]):
            rid, cached = future.result()
            print(f'completed {rid}' + (' (staged cache)' if cached else ''), flush=True)


def score(case: TsxkCase, plan: dict) -> dict:
    root = case.case_dir / 'v2/check'
    new = {r: load(root / 'rows' / f'{r}.json') for r in plan['check']}
    human = plan['owner_values']
    per_column = {}
    for col in COLUMNS:
        per_column[col] = {'old': sum(norm(values(load(case.case_dir / 'rows' / f'{r}.json'))[col]) == norm(human[r][col]) for r in new),
                           'new': sum(norm(values(new[r])[col]) == norm(human[r][col]) for r in new), 'total': len(new)}
    six = [{'row': r, 'owner': norm(human[r]['Y']), 'old': values(load(case.case_dir / 'rows' / f'{r}.json'))['Y'],
            'new': values(new[r])['Y'], 'agree': norm(values(new[r])['Y']) == norm(human[r]['Y'])} for r in Y_SIX]
    banned = [r for r, row in new.items() if BANNED.search(values(row)['AC'])]
    other = [r for r, row in new.items() if parse(values(row)['AC'])['其他'] != row.get('ac_code_lines')]
    random_diff = {c: [r for r in plan['random'] if norm(values(new[r])[c]) != norm(human[r][c])] for c in ('Y', 'Z')}
    spans = load(root / 'span_score.json')['spans']
    gates = {'a': not banned and not other, 'b': sum(s['agree'] for s in six) >= 4 and six[-1]['agree'],
             'c': len(random_diff['Y']) <= 3 and len(random_diff['Z']) <= 6, 'd': sum(s['agree'] for s in spans) >= 11}
    # Transport failures cannot pass by emitting a scrubbed but empty AC.
    valid = all(values(row)['Y'] and values(row)['Z'] and parse(values(row)['AC'])['主题概括'] != '无' for row in new.values())
    result = {'gates': gates, 'passed': all(gates.values()) and valid, 'valid': valid,
              'per_column': per_column, 'six_y': six, 'random_diff': random_diff, 'spans': spans,
              'banned_rows': banned, 'model_other_rows': other}
    save(result, root / 'score.json')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'check', 'score', 'run'))
    parser.add_argument('config', nargs='?', default='cases/tsxk.yaml')
    args = parser.parse_args(argv)
    case = TsxkCase(args.config)
    plan = prepare(case)
    if args.command == 'prepare':
        print(json.dumps({k: len(plan[k]) for k in ('protected', 'remaining', 'check', 'random')}, ensure_ascii=False))
        return
    if args.command == 'score':
        print(json.dumps(score(case, plan), ensure_ascii=False))
        return
    llm.LOCAL_ALLOWED = False
    llm.CALL_LOG = case.case_dir / 'usage/calls.jsonl'
    llm.CALL_RUN = 'WO-107 ' + args.command
    share_slots(case.case_dir / '.cloud_slots')
    # Persist the lower shared cap: all timeline and text calls use the same slots.
    THROTTLE['zhipu'].limited()
    system = load(case.case_dir / 'v2/system.json')['system']
    if args.command == 'check':
        classify_rows(case, plan['check'], case.case_dir / 'v2/check', system)
        result = score(case, plan)
        print(json.dumps({'gates': result['gates'], 'passed': result['passed']}, ensure_ascii=False), flush=True)
        if not result['passed']:
            sys.exit(2)
    else:
        import fcntl
        run_lock = (case.case_dir / 'v2/.run.lock').open('a')
        fcntl.flock(run_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not load(case.case_dir / 'v2/check/score.json')['passed']:
            raise ValueError('WO-107 self-check gates did not pass')
        final, _ = decisions(case.case_dir / 'review/decisions.jsonl')
        ids = [r for r in plan['remaining'] if r not in final]
        classify_rows(case, ids, case.case_dir / 'v2', system)
        print(f'RUN_COMPLETE {len(ids)} rows; awaiting validated replacement', flush=True)


if __name__ == '__main__':
    main()
