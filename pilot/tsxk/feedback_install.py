"""WO-107 all-or-nothing preflight, backup and protected row replacement.

JSON replacements are atomic individually. A receipt records every installed
row. Reviewed rows are re-read under the same file lock used by the new server.
The original 8765 server predates this lock; wait for its 10-minute inactivity
before replacement and recheck the record hash before every file replacement.
"""
from __future__ import annotations

from collections import Counter
import fcntl
import os
from pathlib import Path
import shutil
import tempfile
import time

from .ac_filter import BANNED, parse
from .owner_feedback import COLUMNS, cached_inputs, decisions, digest, load, values
from .pipeline import save


def verify_staged(case, ids):
    root = case.case_dir / 'v2'
    for rid in ids:
        row = load(root / 'rows' / f'{rid}.json')
        v = values(row)
        if row.get('owner_feedback_version') != 'D-077-1' or set(COLUMNS) - row['fields'].keys():
            raise ValueError(f'incomplete staged schema {rid}')
        pending = {c for c in ('Q', 'Y') if v[c] in (None, '') and
                   any(w in row['fields'][c].get('basis', '') for w in ('争议', '交人', '待定')) and
                   any(f.get('level') == 'red' for f in row.get('flags', []))}
        if any(v[c] in (None, '') for c in COLUMNS if c != 'AC' and c not in pending):
            raise ValueError(f'blank staged column {rid}')
        if BANNED.search(v['AC']) or parse(v['AC'])['其他'] != row.get('ac_code_lines'):
            raise ValueError(f'AC gate failed {rid}')
        if '动画剧场版' in v['T'] or any(parse(v['AC'])[s] in ('', '无') for s in ('主题概括', '比对意见')):
            raise ValueError(f'invalid staged wording {rid}')
        load(root / 'timeline' / f'{rid}.json')


def metrics(case, ids, old_dir, new_dir):
    old = {r: load(old_dir / f'{r}.json') for r in ids}
    new = {r: load(new_dir / f'{r}.json') for r in ids}
    from pilot.review.tsxk import normalize_flags
    return {'rows': len(ids), 'changed': {c: sum(values(old[r])[c] != values(new[r])[c] for r in ids) for c in COLUMNS},
            'distributions': {c: {'old': dict(Counter(values(x)[c] for x in old.values())),
                                   'new': dict(Counter(values(x)[c] for x in new.values()))} for c in ('Y', 'Z', 'U', 'W')},
            'other_sentences': {name: sum(len(parse(values(x)['AC'])['其他']) for x in rows.values()) for name, rows in (('old', old), ('new', new))},
            'red_rows': sum(any(f['level'] == 'red' for f in x.get('flags', [])) for x in new.values()),
            'focus_rows': sum(any(f['tier'] == 'focus' for f in normalize_flags(x, None)) for x in new.values())}


def install(case, plan):
    root = case.case_dir
    score = load(root / 'v2/check/score.json')
    if not score['passed']:
        raise ValueError('self-check failed; installation forbidden')
    record = root / 'review/decisions.jsonl'
    if time.time() - record.stat().st_mtime < 600:
        raise ValueError('recent review activity; replacement deferred until inactive for 10 minutes')
    final, _ = decisions(record)
    ids = [r for r in plan['remaining'] if r not in final]
    verify_staged(case, ids)
    for rid, sha in plan['protected_rows_sha256'].items():
        if digest(root / 'rows' / f'{rid}.json') != sha:
            raise ValueError(f'protected row was externally changed: {rid}')
    save(metrics(case, ids, root / 'rows', root / 'v2/rows'), root / 'v2/metrics.json')
    backup = root / 'v1'
    if backup.exists():
        raise ValueError('v1 already exists; refusing to replace an earlier backup')
    temp = Path(tempfile.mkdtemp(prefix='.wo107-backup-', dir=root))
    try:
        for name in ('rows', 'fields.csv', 'audit'):
            src = root / name
            if src.is_dir():
                shutil.copytree(src, temp / name)
            elif src.exists():
                shutil.copy2(src, temp / name)
        for rid in ids:
            dest = temp / 'timeline' / rid
            dest.mkdir(parents=True)
            shutil.copy2(root / 'timeline' / rid / 'timeline.json', dest / 'timeline.json')
        save({'decisions_sha256': digest(record), 'rows_sha256': {r: digest(root / 'rows' / f'{r}.json') for r in case.rows}}, temp / 'manifest.json')
        os.replace(temp, backup)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    installed, skipped = [], []
    with record.open('r') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        sha = digest(record)
        reviewed, _ = decisions(record)
        for rid in ids:
            if digest(record) != sha:
                raise ValueError('review changed during replacement; stop and preserve backup')
            if rid in reviewed:
                skipped.append(rid)
                continue
            save(load(root / 'v2/timeline' / f'{rid}.json'), root / 'timeline' / rid / 'timeline.json')
            save(load(root / 'v2/rows' / f'{rid}.json'), root / 'rows' / f'{rid}.json')
            installed.append(rid)
            save({'installed': installed, 'skipped': skipped, 'decisions_sha256': sha}, root / 'v2/install_receipt.json')
    for rid, expected in plan['protected_rows_sha256'].items():
        assert digest(root / 'rows' / f'{rid}.json') == expected
    assert digest(record) == sha
    from .__main__ import _csv
    target = root / '.fields-wo107.csv'
    _csv({r: load(root / 'rows' / f'{r}.json') for r in case.rows}, target)
    os.replace(target, root / 'fields.csv')
    from .audit import run
    audit_temp = Path(tempfile.mkdtemp(prefix='.audit-wo107-', dir=root))
    audit = run(case, audit_temp, inputs=cached_inputs(case, case.rows))
    old_audit = root / '.audit-before-wo107'
    if (root / 'audit').exists():
        os.replace(root / 'audit', old_audit)
    os.replace(audit_temp, root / 'audit')
    if old_audit.exists():
        shutil.rmtree(old_audit)
    receipt = {'installed': installed, 'skipped': skipped, 'decisions_sha256': sha, 'protected_unchanged': True,
               'backup': str(backup), 'audit': audit}
    save(receipt, root / 'v2/install_receipt.json')
    return receipt
