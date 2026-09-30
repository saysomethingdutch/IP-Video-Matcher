"""Recalculate page and actual playback spans from existing OCR/progress caches."""
from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from pilot.webledger.content_span import locate
from pilot.webledger.ledger import (STATE, address_observation, bv_distance,
                                    duration, error_page_times, extract_ocr,
                                    page_segments)
from pilot.webledger.progress import measure as measure_progress
from pilot.webledger.progress_r2 import decide
from pilot.webledger.rerun_r2 import SETS, _video


def atomic_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def atomic_csv(path: Path, columns, rows) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def rerun() -> None:
    before, after = Counter(), Counter()
    basis_counts = Counter()
    review_count = 0
    examples = {}
    named = {'7844', '8528', '8325', '8516', '8452'}
    processed = 0
    for csv_name, folder, expected in SETS:
        csv_path = STATE / csv_name
        with csv_path.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            columns, rows = reader.fieldnames, list(reader)
        if len(rows) != expected:
            raise RuntimeError(f'{csv_name}: expected {expected}, found {len(rows)}')
        for csv_row in rows:
            stem = Path(csv_row['sample']).stem
            output = folder / f'{stem}.json'
            data = json.loads(output.read_text(encoding='utf-8'))
            old = {field: data.get(field) for field in (
                'play_start', 'play_end', 'content_start', 'content_end', 'complete')}
            before[csv_row['R']] += 1
            video = _video(stem)
            if not video.is_file():
                raise FileNotFoundError(video)
            seconds = duration(video)
            records = extract_ocr(video, seconds)
            flags = [flag for flag in data.get('flags') or []
                     if not flag.startswith('R2：') and
                     not flag.startswith('目标页出现 B 站报错页：')]
            page_start, page_end = data.get('play_start'), data.get('play_end')
            other_pages = data.get('other_pages') or []
            if stem == '7844' or any('浏览器关闭' in flag for flag in flags):
                flags = [flag for flag in flags if '浏览器关闭' not in flag]
                (_, page_start, page_end, _, other_pages, _, page_flags) = page_segments(
                    records, data.get('bv'), seconds, video, data.get('title'))
                flags.extend(page_flags)
            if page_start is not None and page_end is not None:
                errors = error_page_times(records, page_start, page_end)
                if errors:
                    flags.append(f'目标页出现 B 站报错页：{min(errors):.0f}–{max(errors):.0f}秒')
            for page in other_pages:
                if any(autoplay and found and bv_distance(found, page.get('bv')) <= 2
                       and page['start'] - 5 <= frame['t'] <= page['end'] + 2
                       for frame in records
                       for found, autoplay in [address_observation(frame)]):
                    page['autoplay'] = True
            progress = data.get('progress') or []
            if (page_start, page_end) != (old['play_start'], old['play_end']):
                progress = (measure_progress(video, page_start, page_end, STATE)
                            if page_start is not None and page_end is not None
                            and page_end - page_start > 5 else [])
            progress = [[t, p] for t, p in progress
                        if page_start is not None and page_end is not None
                        and page_start <= t <= page_end]
            result = decide(progress, page_start or 0, page_end or 0)
            content = locate(progress, page_start, page_end, result,
                             page_uncertain=page_start is None or page_end is None)
            if result.complete is not True or '外推' in result.basis:
                flags.append(f'R2：{result.reason}')
            data.update(content)
            data.update({'play_start': page_start, 'play_end': page_end,
                         'other_pages': other_pages, 'progress': progress,
                         'complete': result.complete, 'complete_basis': result.basis,
                         'complete_reason': result.reason, 'flags': flags})
            csv_row['R'] = {True: '是', False: '否', None: '待核'}[result.complete]
            csv_row['flags'] = '；'.join(flags)
            csv_row['page_visible_seconds'] = (
                round(page_end - page_start, 2)
                if page_start is not None and page_end is not None else '')
            csv_row['other_pages_count'] = len(other_pages)
            atomic_json(output, data)
            after[csv_row['R']] += 1
            basis_counts[content['content_basis'].split('；')[0]] += 1
            review_count += content['complete_needs_review']
            if stem in named:
                examples[stem] = {'before': old, 'after': {
                    field: data.get(field) for field in old},
                    'content_basis': content['content_basis'],
                    'complete_needs_review': content['complete_needs_review']}
            processed += 1
            if processed % 25 == 0:
                print(f'R3 processed {processed}/435', flush=True)
        atomic_csv(csv_path, columns, rows)
    if processed != 435:
        raise RuntimeError(f'Expected 435 rows, processed {processed}')
    summary = {'processed': processed, 'before_R': dict(before),
               'after_R': dict(after), 'content_basis_counts': dict(basis_counts),
               'complete_needs_review': review_count, 'named_rows': examples}
    atomic_json(STATE / 'r3_summary.json', summary)
    print('R3_COMPLETE ' + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    rerun()
