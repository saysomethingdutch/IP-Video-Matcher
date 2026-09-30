"""Re-measure the R1 progress line after correcting one-pixel codec placement."""
from __future__ import annotations

import json
from pathlib import Path

from pilot.webledger.ledger import EXAMPLES, STATE
from pilot.webledger.progress import measure

EVIDENCE = Path('/Volumes/Element/ip-video-matcher-field-test')


def run():
    outputs = ([STATE / f'{n}.json' for n in range(7813, 8213)] +
               [p for p in STATE.glob('*.json') if p.stem.isdigit() and int(p.stem) > 8212] +
               [STATE / 'samples' / f'{n}.json' for n in range(13, 18)])
    if len(outputs) != 435 or any(not p.exists() for p in outputs):
        raise RuntimeError('Expected all 400 + 30 + 5 ledger outputs before remeasurement')
    for index, output in enumerate(outputs, 1):
        row = json.loads(output.read_text())
        stem = output.stem
        video = (EXAMPLES / f'{stem}.mp4' if int(stem) < 7813 else
                 EVIDENCE / ('evidence' if int(stem) <= 8212 else 'evidence_check30') / f'{stem}.mp4')
        start, end = row.get('play_start'), row.get('play_end')
        row['progress'] = measure(video, start, end, STATE) if start is not None and end is not None else []
        temp = output.with_suffix('.json.tmp')
        temp.write_text(json.dumps(row, ensure_ascii=False, indent=2))
        temp.replace(output)
        if index % 25 == 0 or index == len(outputs):
            print(f'REMEASURE {index}/{len(outputs)}', flush=True)


if __name__ == '__main__':
    run()
