"""Finish WO-103-R2 after the single formal background run releases its lock."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import subprocess
import sys

from pilot.audio.summarize_r2 import summarize
from pilot.audio.tsxk_r1 import OUTPUT, VERSION, _fingerprint, recording

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / 'workspace/pilot/audio_env/bin/python'
RESULT = ROOT / 'docs/workorders/WO-103-R2-RESULT.md'


def incomplete() -> list[int]:
    bad = []
    for row in range(7813, 8213):
        video, ledger_path, output = recording(row, 'formal')
        if not output.exists():
            bad.append(row)
            continue
        data = json.loads(output.read_text())
        ledger = json.loads(ledger_path.read_text())
        if (data.get('status') != 'ok' or data.get('analysis_version') != VERSION
                or data.get('fingerprint') != _fingerprint(video, ledger)):
            bad.append(row)
    return bad


def finish() -> None:
    # The producer writes its summary while holding this lock. Once available,
    # no partial output is mistaken for a finished batch.
    with (OUTPUT / '.run.lock').open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        fcntl.flock(handle, fcntl.LOCK_UN)
    for attempt in range(3):
        bad = incomplete()
        if not bad:
            break
        if attempt == 2:
            raise RuntimeError(f'Formal audio incomplete after retries: {bad}')
        print(f'RETRY {attempt + 1} {len(bad)} formal rows', flush=True)
        command = (f'cd {ROOT} && PYTHONDONTWRITEBYTECODE=1 {PYTHON} '
                   f'-m pilot.audio.tsxk_r1 --group formal --rows {",".join(map(str, bad))}')
        subprocess.run(['zsh', '-lic', command], check=False, stdout=sys.stdout,
                       stderr=sys.stderr)

    summary = summarize()
    assert summary['formal_rows'] == 400 and summary['samples'] == 5
    assert summary['frozen_check30_verified'] == 30
    near = summary['near_threshold_rows']
    inverted = summary['rot180_rows']
    mirrored = summary['mirror_rows']
    RESULT.write_text(
        '# WO-103-R2 交付\n\n'
        '正式 400 条和示例 13–17 均完成新版声音处理。音频只在本机由 SenseVoice 转写；'
        'GLM 只收到逐句文字。字幕带支持播放器画面旋转 180°、左右镜像，'
        '拼音部分包含和 ±4 秒匹配；口播须累计 ≥10 秒且占播放时长 ≥3%。\n\n'
        f'正式行：口播 {summary["uploader_speech_rows"]} 条，推广口播 '
        f'{summary["promotional_speech_rows"]} 条，重新配音候选 '
        f'{summary["redub_candidate_rows"]} 条。倒置字幕 {len(inverted)} 条，'
        f'镜像字幕 {len(mirrored)} 条，行号见 '
        '[审计摘要](/Users/lestersun/IP-Video-Matcher/workspace/tsxk/audio/summary_r2_audit.json)。'
        f'门槛附近 {len(near)} 条，逐条秒数和占比见 '
        '[抽查清单](/Users/lestersun/IP-Video-Matcher/workspace/tsxk/audio/r2_near_threshold.csv)。\n\n'
        '示例 13 检出 1 句重新配音；8455 仅作本地 OCR 验证，旋正后可读出字幕，'
        '冻结的检验集输出未改。30 条检验集的 SHA-256 '
        '与 R1 冻结清单逐项一致。\n', encoding='utf-8')
    subprocess.run([str(PYTHON), '-m', 'unittest', 'tests.test_tsxk_audio_r2',
                    'tests.test_tsxk_progress_r2', '-q'], cwd=ROOT, check=True)
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    files = ['pilot/audio/tsxk_r1.py', 'pilot/audio/tsxk_subtitles.py',
             'pilot/audio/summarize_r2.py', 'pilot/audio/finish_r2.py',
             'tests/test_tsxk_audio_r2.py', 'docs/workorders/WO-103-R2-RESULT.md']
    subprocess.run(['git', 'add', *files], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', 'tsxk: harden audio matching and process formal rows'],
                   cwd=ROOT, check=True)
    print('WO103_R2_COMPLETE', json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    finish()
