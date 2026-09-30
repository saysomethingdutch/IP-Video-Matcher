"""Finish formal R2 first, then process the independent second check set."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from pilot.audio.finish_r2 import PYTHON, ROOT, finish as finish_formal
from pilot.audio.tsxk_r1 import OUTPUT, VERSION, _fingerprint, recording, selected

STATE = ROOT / 'workspace/tsxk'
READY = STATE / 'webledger/check30b_ready.json'
RESULT = ROOT / 'docs/workorders/WO-103-CHECK30B-RESULT.md'


def wait_for_ledger() -> dict:
    elapsed = 0
    while not READY.exists():
        time.sleep(60)
        elapsed += 60
        if elapsed % 600 == 0:
            print(f'WAIT_CHECK30B_LEDGER {elapsed // 60} minutes', flush=True)
    ready = json.loads(READY.read_text())
    if ready.get('status') != 'ok' or ready.get('rows') != 30 or not ready.get('wo101_commit'):
        raise RuntimeError(f'Second check ledger not verified: {ready}')
    return ready


def incomplete() -> list[int]:
    bad = []
    for row in selected('check30b'):
        video, ledger_path, output = recording(row, 'check30b')
        if not output.exists():
            bad.append(row)
            continue
        value = json.loads(output.read_text())
        ledger = json.loads(ledger_path.read_text())
        if (value.get('status') != 'ok' or value.get('analysis_version') != VERSION
                or value.get('fingerprint') != _fingerprint(video, ledger)
                or value.get('audio_uploaded') is not False
                or value.get('glm_text_only') is not True):
            bad.append(row)
    return bad


def run_audio(rows: list[int] | None = None) -> None:
    command = (f'cd {ROOT} && PYTHONDONTWRITEBYTECODE=1 {PYTHON} '
               '-m pilot.audio.tsxk_r1 --group check30b')
    if rows is not None:
        command += ' --rows ' + ','.join(map(str, rows))
    subprocess.run(['zsh', '-lic', command], cwd=ROOT, check=False,
                   stdout=sys.stdout, stderr=sys.stderr)


def frozen_first_check_unchanged() -> None:
    freeze = json.loads((OUTPUT / 'r1_check30_frozen.json').read_text())
    changed = [row for row, expected in freeze['output_sha256'].items()
               if hashlib.sha256((OUTPUT / f'{row}.json').read_bytes()).hexdigest() != expected]
    if changed:
        raise RuntimeError(f'Frozen first check set changed: {changed}')


def finish() -> None:
    finish_formal()  # Waits for the running 400-row job, retries, audits and commits.
    ready = wait_for_ledger()
    subprocess.run(['git', 'merge', ready['wo101_commit']], cwd=ROOT, check=True)
    if len(selected('check30b')) != 30:
        raise RuntimeError('Second check manifest does not contain 30 rows')
    run_audio()
    for attempt in range(2):
        bad = incomplete()
        if not bad:
            break
        print(f'RETRY_CHECK30B {attempt + 1} rows={len(bad)}', flush=True)
        run_audio(bad)
    bad = incomplete()
    if bad:
        raise RuntimeError(f'Second check audio incomplete: {bad}')
    frozen_first_check_unchanged()
    values = [json.loads(recording(row, 'check30b')[2].read_text())
              for row in selected('check30b')]
    summary = {
        'rows': len(values), 'version': VERSION,
        'uploader_speech_rows': sum(v.get('uploader_speech_seconds', 0) > 0 for v in values),
        'promotional_speech_rows': sum(bool(v.get('promotional_speech')) for v in values),
        'redub_candidate_rows': sum(bool(v.get('redub_candidates')) for v in values),
        'frozen_first_check_verified': 30,
    }
    path = OUTPUT / 'summary_r2_check30b_audit.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    temp.replace(path)
    RESULT.write_text(
        '# 吞噬星空第二批独立检验：声音交付\n\n'
        '在正式 400 行 WO-103-R2 完成并提交后，用同版 SenseVoice-Small、字幕 OCR '
        '和逐句文字 GLM 处理第二批 30 条。音频仅在本机处理，未上传；首批 30 条冻结文件的 '
        'SHA-256 逐项一致。\n\n'
        f'30/30 条成功；检出口播 {summary["uploader_speech_rows"]} 条，'
        f'推广口播 {summary["promotional_speech_rows"]} 条，'
        f'重新配音候选 {summary["redub_candidate_rows"]} 条。'
        '结果在 `workspace/tsxk/audio/<序号>.json`，审计汇总在 '
        '`workspace/tsxk/audio/summary_r2_check30b_audit.json`。'
        '本次未读取人工结论，也未用第二批调参。\n', encoding='utf-8')
    subprocess.run([str(PYTHON), '-m', 'unittest', 'tests.test_tsxk_audio_check30b', '-q'],
                   cwd=ROOT, check=True)
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    subprocess.run(['git', 'add', 'pilot/audio/finish_check30b.py',
                    'tests/test_tsxk_audio_check30b.py',
                    'docs/workorders/WO-103-CHECK30B-RESULT.md'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', 'tsxk: process independent second audio check set'],
                   cwd=ROOT, check=True)
    print('CHECK30B_AUDIO_COMPLETE ' + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    finish()
