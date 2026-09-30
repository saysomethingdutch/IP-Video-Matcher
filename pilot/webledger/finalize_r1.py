"""Reconcile completed R1 ledger batches with the asynchronous certificate run."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path

from pilot.ezcun.fetch import STATE as EZCUN_STATE, entries, check_entries
from pilot.webledger.ledger import STATE, same_title
from pilot.webledger.progress import decide


def finalize():
    summaries = []
    sources = {str(item['row']): item for item in entries() + check_entries()}
    for csv_name in ('ledger_400_r1.csv', 'ledger_check30_r1.csv', 'ledger_samples_r1.csv'):
        csv_path = STATE / csv_name
        if not csv_path.exists():
            raise FileNotFoundError(csv_path)
        with csv_path.open(newline='') as handle:
            reader = csv.DictReader(handle)
            columns, rows = reader.fieldnames, list(reader)
        for row in rows:
            stem = Path(row['sample']).stem
            result_path = STATE / ('samples' if csv_name == 'ledger_samples_r1.csv' else '') / f'{stem}.json'
            data = json.loads(result_path.read_text())
            start, end = data.get('play_start'), data.get('play_end')
            points = data.get('progress') or []
            pages = data.get('other_pages') or []
            if pages and end is not None:
                for (t0, p0), (t1, p1) in zip(points, points[1:]):
                    if end - 5 <= t1 < end and p0 >= .90 and p1 <= .10:
                        end = t1
                        data['play_end'] = end
                        pages[0]['start'] = end
                        points = [[t, p] for t, p in points if t < end]
                        data['progress'] = points
                        break
            if start is not None and end is not None:
                result, estimated, reasons = decide(points, start, end,
                    bool(pages and pages[0].get('autoplay')))
                data['complete'] = result
                if estimated:
                    data['duration'] = estimated
                    data['duration_basis'] = '进度条估算（按 1 倍速）'
            else:
                result, reasons = None, ['目标页面播放区间不足']
            flags = [f for f in data.get('flags', [])
                     if not f.startswith(('细进度条', '目标页面结束时细进度条',
                                          '自动连播佐证', '证书尚未读到'))]
            source = sources.get(stem)
            if source and data.get('title'):
                title_same = same_title(data['title'], source['input_title'])
                data['title_same_as_input'] = title_same
                if title_same:
                    data['title'] = source['input_title']
                    flags = [f for f in flags if f != '录屏标题与原表 C 不同']
                elif '录屏标题与原表 C 不同' not in flags:
                    flags.append('录屏标题与原表 C 不同')
                row['D'] = data['title']
                row['title_same_as_input'] = title_same
            flags.extend(reasons)
            cert_path = EZCUN_STATE / 'certificates' / f'{stem}.json'
            if cert_path.exists():
                stamp = json.loads(cert_path.read_text())['certificate_finished']
                data['finished_witness_time'] = stamp
                data['l_basis'] = '证书'
                if data.get('api_finished_time'):
                    fmt = '%Y/%m/%d %H:%M:%S'
                    seconds = int((datetime.strptime(data['api_finished_time'], fmt) -
                                   datetime.strptime(stamp, fmt)).total_seconds())
                    if abs(seconds) > 2:
                        flags.append(f'证书与接口时间相差{seconds:+d}秒；核对证书关联')
            elif csv_name == 'ledger_400_r1.csv':
                flags.append('证书读取失败；L 暂用接口时间')
                data['l_basis'] = '接口（证书读取失败）'
            elif csv_name == 'ledger_check30_r1.csv':
                data['l_basis'] = '接口（检验集无需证书）'
            data['flags'] = list(dict.fromkeys(flags))
            temp = result_path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            temp.replace(result_path)
            row['R'] = {True: '是', False: '否', None: '待核'}[result]
            row['L'] = data.get('finished_witness_time') or ''
            row['flags'] = '；'.join(data['flags'])
            if start is not None and end is not None:
                row['page_visible_seconds'] = round(end - start, 2)
            summaries.append((stem, result, bool(pages), data.get('title_same_as_input'),
                              data.get('address_same_as_input'), data.get('l_basis')))
        temp = csv_path.with_suffix('.csv.tmp')
        with temp.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(csv_path)
    baseline = json.loads((STATE / 'r1_baseline.json').read_text())
    known = [(stem, result) for stem, result, *_ in summaries
             if int(stem) >= 7813 and stem in baseline and baseline[stem]['complete'] is not None]
    agreed = sum(baseline[stem]['complete'] == result for stem, result in known)
    decisive = [(stem, result) for stem, result in known if result is not None]
    decisive_agreed = sum(baseline[stem]['complete'] == result for stem, result in decisive)
    changed = [stem for stem, result, *_ in summaries
               if stem in baseline and baseline[stem]['complete'] != result]
    print('ROWS', len(summaries))
    print('R', dict(Counter({True: '是', False: '否', None: '待核'}[x[1]] for x in summaries)))
    print('SWITCH', sum(x[2] for x in summaries))
    print('TITLE_DIFFERENT', sum(x[3] is False for x in summaries))
    print('ADDRESS_DIFFERENT', sum(x[4] is False for x in summaries))
    print('CERT', Counter(x[5] for x in summaries))
    print('OLD_KNOWN_AGREEMENT', agreed, '/', len(known))
    print('OLD_KNOWN_DECISIVE_AGREEMENT', decisive_agreed, '/', len(decisive))
    print('R_CHANGED', len(changed), ','.join(changed[:50]))


if __name__ == '__main__':
    finalize()
