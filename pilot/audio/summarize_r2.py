"""Audit WO-103-R2 outputs and produce the Owner's borderline review list."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from pilot.audio.tsxk_r1 import OUTPUT, VERSION, _fingerprint, recording


def summarize() -> dict:
    freeze = json.loads((OUTPUT / 'r1_check30_frozen.json').read_text())
    changed = [row for row, expected in freeze['output_sha256'].items()
               if hashlib.sha256((OUTPUT / f'{row}.json').read_bytes()).hexdigest() != expected]
    if changed:
        raise RuntimeError(f'Frozen check30 outputs changed: {changed}')
    rows = {}
    for row in range(7813, 8213):
        path = OUTPUT / f'{row}.json'
        if not path.exists():
            raise RuntimeError(f'Missing formal audio output: {row}')
        value = json.loads(path.read_text())
        if value.get('status') != 'ok' or value.get('analysis_version') != VERSION:
            raise RuntimeError(f'Formal audio incomplete: {row}: {value.get("status")}')
        video, ledger_path, _ = recording(row, 'formal')
        ledger = json.loads(ledger_path.read_text())
        if value.get('fingerprint') != _fingerprint(video, ledger):
            raise RuntimeError(f'Formal audio uses an older ledger interval: {row}')
        rows[row] = value
    for row in range(13, 18):
        value = json.loads((OUTPUT / 'samples' / f'{row}.json').read_text())
        if value.get('status') != 'ok' or value.get('analysis_version') != VERSION:
            raise RuntimeError(f'Sample audio incomplete: {row}')
    near = []
    for row, value in rows.items():
        seconds = value.get('uploader_candidate_seconds') or 0
        ratio = value.get('uploader_candidate_ratio') or 0
        if 10 <= seconds <= 40 or .02 <= ratio <= .06:
            near.append({'row': row, 'candidate_seconds': seconds,
                         'candidate_ratio': ratio,
                         'accepted_seconds': value.get('uploader_speech_seconds') or 0,
                         'orientation': value.get('subtitle_orientation', 'normal')})
    with (OUTPUT / 'r2_near_threshold.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['row', 'candidate_seconds',
                                                    'candidate_ratio', 'accepted_seconds',
                                                    'orientation'])
        writer.writeheader()
        writer.writerows(near)
    summary = {'formal_rows': 400, 'samples': 5, 'frozen_check30_verified': 30,
               'uploader_speech_rows': sum(v.get('uploader_speech_seconds', 0) > 0
                                           for v in rows.values()),
               'promotional_speech_rows': sum(bool(v.get('promotional_speech'))
                                              for v in rows.values()),
               'redub_candidate_rows': sum(bool(v.get('redub_candidates'))
                                           for v in rows.values()),
               'rot180_rows': [row for row, v in rows.items()
                               if v.get('subtitle_orientation') == 'rot180'],
               'mirror_rows': [row for row, v in rows.items()
                               if v.get('subtitle_orientation') == 'mirror'],
               'near_threshold_rows': [item['row'] for item in near]}
    (OUTPUT / 'summary_r2_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    print(json.dumps(summarize(), ensure_ascii=False, indent=2))
