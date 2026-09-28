"""After the single fetch run, verify evidence and build the second check ledger."""
from __future__ import annotations

from collections import Counter
import csv
import fcntl
import json
from pathlib import Path
import subprocess
import sys

from pilot.ezcun.fetch import (ROOT, STATE as EZCUN_STATE, check30b_entries,
                               digest_file, matched_digest)
from pilot.webledger.ledger import STATE as WEBLEDGER_STATE

EVIDENCE = Path('/Volumes/Element/ip-video-matcher-field-test/evidence_check30b')
READY = WEBLEDGER_STATE / 'check30b_ready.json'
RESULT = ROOT / 'docs/workorders/WO-101-CHECK30B-RESULT.md'


def rows_at(path: Path) -> dict[int, dict]:
    with path.open(newline='', encoding='utf-8') as stream:
        return {int(row['row']): row for row in csv.DictReader(stream)}


def verify_evidence() -> tuple[int, Counter]:
    items = check30b_entries()
    wanted = {item['row'] for item in items}
    manifest = rows_at(EZCUN_STATE / 'manifest_check30b.csv')
    downloads = rows_at(EZCUN_STATE / 'downloads_check30b.csv')
    if set(manifest) != wanted or set(downloads) != wanted:
        raise RuntimeError('Second check manifest/download records are incomplete')
    matched = Counter()
    total = 0
    for row in sorted(wanted):
        record = manifest[row]
        download = downloads[row]
        if record['status'] != 'ok' or download['status'] != 'ok':
            raise RuntimeError(f'Second check evidence row {row} is not successful')
        video = EVIDENCE / f'{row}.mp4'
        expected = int(record['file_size_bytes'])
        if not video.is_file() or video.stat().st_size != expected:
            raise RuntimeError(f'Second check evidence row {row} has wrong size')
        metadata = json.loads((EZCUN_STATE / 'metadata_check30b' / f'{row}.json').read_text())
        digest = digest_file(video)
        if digest != download['md5'] or not matched_digest(digest, metadata):
            raise RuntimeError(f'Second check evidence row {row} has wrong MD5')
        matched.update(matched_digest(digest, metadata))
        total += expected
    return total, matched


def verify_ledger() -> tuple[Counter, int]:
    items = check30b_entries()
    path = WEBLEDGER_STATE / 'ledger_check30b.csv'
    with path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 30 or {int(row['row']) for row in rows} != {item['row'] for item in items}:
        raise RuntimeError('Second check ledger CSV does not contain exactly 30 rows')
    result = Counter()
    needs_review = 0
    for item in items:
        row = item['row']
        data = json.loads((WEBLEDGER_STATE / f'{row}.json').read_text())
        if (data.get('row') != str(row) or data.get('content_start') is None or
                data.get('content_end') is None or not isinstance(data.get('passes'), list) or
                not data.get('content_basis') or
                not isinstance(data.get('complete_needs_review'), bool)):
            raise RuntimeError(f'Second check ledger row {row} lacks R3 fields')
        if not data['play_start'] <= data['content_start'] <= data['content_end'] <= data['play_end']:
            raise RuntimeError(f'Second check content interval outside target page: {row}')
        result[{True: '是', False: '否', None: '待核'}[data['complete']]] += 1
        needs_review += bool(data['complete_needs_review'])
    return result, needs_review


def finish() -> None:
    # The fetcher holds this advisory lock through metadata and downloads.
    with (EZCUN_STATE / 'run.lock').open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        fcntl.flock(handle, fcntl.LOCK_UN)
    total, matched = verify_evidence()
    print(f'CHECK30B_EVIDENCE_VERIFIED rows=30 bytes={total} md5={dict(matched)}', flush=True)
    command = [sys.executable, '-m', 'pilot.webledger.ledger', '--all', '--check30b',
               '--workers', '2', '--output', str(WEBLEDGER_STATE / 'ledger_check30b.csv')]
    subprocess.run(command, cwd=ROOT, check=True)
    result, needs_review = verify_ledger()
    RESULT.write_text(
        '# 吞噬星空第二批独立检验：下载与台账交付\n\n'
        f'从 `cases/tsxk_check30b.csv` 的输入列取得分享链接，用有界面 Chrome '
        f'抓取页面响应；30/30 条下载完成，共 {total:,} 字节（{total / 1024**3:.2f} GiB）。'
        f'每条均独立重算文件大小和 MD5：与 originalMd5 相符 {matched["originalMd5"]} 条，'
        f'与 encryptedMd5 相符 {matched["encryptedMd5"]} 条。\n\n'
        f'WO-101-R3 台账 30/30 条完成，R 为是 {result["是"]}、否 {result["否"]}、'
        f'待核 {result["待核"]}；`complete_needs_review` 为 true 的有 {needs_review} 条。'
        'JSON 位于共用 `workspace/tsxk/webledger/<序号>.json`，包含逐遍记录和实际内容区间。'
        '未打开"张亚晨"工作表或读取其结论列；未改原表。\n', encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'unittest', 'tests.test_tsxk_fetch_webledger',
                    'tests.test_tsxk_content_span_r3', '-q'], cwd=ROOT, check=True)
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    subprocess.run(['git', 'add', 'pilot/ezcun/fetch.py', 'pilot/webledger/ledger.py',
                    'pilot/webledger/finish_check30b.py', 'tests/test_tsxk_fetch_webledger.py',
                    'docs/workorders/WO-101-CHECK30B-RESULT.md'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', 'tsxk: fetch and ledger second independent check set'],
                   cwd=ROOT, check=True)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    ready = {'status': 'ok', 'rows': 30, 'wo101_commit': commit}
    temp = READY.with_suffix('.json.tmp')
    temp.write_text(json.dumps(ready, ensure_ascii=False))
    temp.replace(READY)
    print(f'CHECK30B_LEDGER_COMPLETE rows=30 commit={commit}', flush=True)


if __name__ == '__main__':
    finish()
