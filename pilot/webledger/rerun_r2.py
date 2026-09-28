"""Recalculate only ledger R from the cached progress lines; save no-evidence frames."""
from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path
import subprocess

from pilot.webledger.ledger import EXAMPLES, STATE
from pilot.webledger.progress_r2 import decide

EVIDENCE = Path('/Volumes/Element/ip-video-matcher-field-test')
SETS = (('ledger_400_r1.csv', STATE, 400),
        ('ledger_check30_r1.csv', STATE, 30),
        ('ledger_samples_r1.csv', STATE / 'samples', 5))
OLD_R_FLAGS = ('细进度条', '目标页面结束时细进度条', '自动连播佐证')


def _video(stem: str) -> Path:
    number = int(stem)
    if number < 7813:
        return EXAMPLES / f'{stem}.mp4'
    group = 'evidence' if number <= 8212 else 'evidence_check30'
    return EVIDENCE / group / f'{stem}.mp4'


def _screenshot(video: Path, time: float, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-ss', str(time), '-i', str(video), '-frames:v', '1',
                    '-q:v', '2', str(path)], check=True)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f'No screenshot at {video}:{time}')


def rerun() -> None:
    pending = []
    baseline = {}
    totals = Counter()
    screenshot_rows = []
    for csv_name, folder, expected in SETS:
        with (STATE / csv_name).open(newline='') as stream:
            reader = csv.DictReader(stream)
            columns, rows = reader.fieldnames, list(reader)
        if len(rows) != expected:
            raise RuntimeError(f'{csv_name}: expected {expected}, got {len(rows)}')
        for csv_row in rows:
            stem = Path(csv_row['sample']).stem
            output = folder / f'{stem}.json'
            data = json.loads(output.read_text())
            result = decide(data.get('progress') or [],
                            data.get('play_start') or 0,
                            data.get('play_end') or 0)
            old_flags = data.get('flags') or []
            flags = [flag for flag in old_flags if not flag.startswith(OLD_R_FLAGS)
                     and not flag.startswith('R2：')]
            if result.complete is not True or '外推' in result.basis:
                flags.append(f'R2：{result.reason}')
            baseline[stem] = {'complete': data.get('complete'), 'flags': old_flags,
                              'csv_R': csv_row['R'], 'csv_flags': csv_row['flags']}
            data['complete'] = result.complete
            data['complete_basis'] = result.basis
            data['complete_reason'] = result.reason
            data['flags'] = flags
            csv_row['R'] = {True: '是', False: '否', None: '待核'}[result.complete]
            csv_row['flags'] = '；'.join(flags)
            totals[(csv_name, result.complete)] += 1
            if result.complete is False:
                video = _video(stem)
                if not video.exists():
                    raise FileNotFoundError(video)
                times = result.evidence_times
                if not times or len(times) > 2:
                    raise RuntimeError(f'{stem}: no screenshot time for definite no')
                screenshots = []
                for index, time in enumerate(times, 1):
                    image = STATE / 'r2_no_screenshots' / f'{stem}_{index}.jpg'
                    _screenshot(video, time, image)
                    screenshots.append(str(image.resolve()))
                screenshot_rows.append({'row': stem, 'reason': result.reason,
                                        'reading_times': ','.join(map(str, times)),
                                        'screenshot_1': screenshots[0],
                                        'screenshot_2': screenshots[1] if len(screenshots) > 1 else ''})
            pending.append((output, data))
        pending.append((STATE / csv_name, (columns, rows)))

    (STATE / 'r2_baseline.json').write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
    list_path = STATE / 'r2_no_screenshot_list.csv'
    with list_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['row', 'reason', 'reading_times',
                                                    'screenshot_1', 'screenshot_2'])
        writer.writeheader()
        writer.writerows(screenshot_rows)
    for path, content in pending:
        temp = path.with_suffix(path.suffix + '.tmp')
        if isinstance(content, tuple):
            columns, rows = content
            with temp.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        else:
            temp.write_text(json.dumps(content, ensure_ascii=False, indent=2))
        temp.replace(path)
    print('R2', dict(totals), 'NO_SCREENSHOTS', len(screenshot_rows), flush=True)


if __name__ == '__main__':
    rerun()
