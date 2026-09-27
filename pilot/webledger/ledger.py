"""Read Bilibili desktop evidence from local video frames, without conclusion columns.

The module samples the visible Chrome/Bilibili page, uses Apple Vision OCR,
and keeps uncertain playback judgments explicit for owner review.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from pilot.ezcun.fetch import entries, STATE as EZCUN_STATE

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'workspace/tsxk/webledger'
OCR_SOURCE = ROOT / 'pilot/tools/ocr_vision.swift'
OCR_BINARY = Path('/private/tmp/tsxk-webledger-ocr')
EXAMPLES = Path('/Volumes/Element/ip-video-matcher-field-test/field-test/videos')
BV_PATTERN = re.compile(r'BV[0-9A-Za-z]{10}')
TIME_PATTERN = re.compile(r'(\d{1,2}:\d{2}(?::\d{2})?)\s*[/／]\s*(\d{1,2}:\d{2}(?::\d{2})?)')
SINGLE_TIME = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
DATE_PATTERN = re.compile(r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}:\d{2}')


def command(args):
    return subprocess.run([str(arg) for arg in args], check=True,
                          capture_output=True, text=True).stdout


def compile_ocr():
    if OCR_BINARY.exists() and OCR_BINARY.stat().st_mtime >= OCR_SOURCE.stat().st_mtime:
        return
    command(['swiftc', '-O', '-module-cache-path', '/private/tmp/tsxk-webledger-swift',
             '-Xcc', '-fmodules-cache-path=/private/tmp/tsxk-webledger-clang',
             OCR_SOURCE, '-o', OCR_BINARY])


def duration(path):
    return float(command(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'default=nw=1:nk=1', path]).strip())


def sample_times(seconds):
    times = {0.0, max(0, seconds - 0.5)}
    t = 2.0
    while t < seconds:
        step = 3 if t < 35 or t > seconds - 35 else 10
        times.add(round(t, 2))
        t += step
    return sorted(times)


def extract_ocr(path, seconds):
    STATE.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(f'{path.resolve()}:{path.stat().st_size}:{seconds:.3f}:v1'.encode()).hexdigest()[:16]
    cache = STATE / f'{path.stem}-{fingerprint}.jsonl'
    if cache.exists():
        return [json.loads(line) for line in cache.read_text().splitlines()]
    compile_ocr()
    records = []
    with tempfile.TemporaryDirectory(prefix='tsxk-webledger-') as temp:
        frame_dir = Path(temp)
        files = []
        for index, t in enumerate(sample_times(seconds)):
            file = frame_dir / f'{index:04d}.jpg'
            command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t),
                     '-i', path, '-frames:v', '1', '-q:v', '4', '-y', file])
            if file.exists():
                files.append((t, file))
        for offset in range(0, len(files), 12):
            group = files[offset:offset + 12]
            process = subprocess.run([str(OCR_BINARY), *map(lambda x: str(x[1]), group)],
                                     capture_output=True, text=True)
            if process.returncode:
                raise RuntimeError(f'Apple Vision OCR failed: {process.stderr[:500]}')
            parsed = {json.loads(line)['frame']: json.loads(line)
                      for line in process.stdout.splitlines() if line.startswith('{')}
            if len(parsed) != len(group):
                raise RuntimeError(f'OCR returned {len(parsed)}/{len(group)} frames: {process.stderr[:500]}')
            for t, file in group:
                records.append({'t': t, 'items': parsed[file.name]['items']})
    cache.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    return records


def positioned(frame, x1, x2, y1, y2):
    return [i['text'].strip() for i in frame['items']
            if x1 <= i['x'] < x2 and y1 <= i['y'] < y2 and i['text'].strip()]


def winner(strings):
    clean = [re.sub(r'\s+', '', s) for s in strings if s]
    if not clean:
        return '', 0
    value, count = Counter(clean).most_common(1)[0]
    return value, count


def clock_seconds(value):
    parts = [int(x) for x in value.split(':')]
    n = 0
    for part in parts:
        n = 60 * n + part
    return n


def player_readouts(records):
    seen = []
    for frame in records:
        # The Chrome page's player controls live in the main, left-hand pane.
        texts = positioned(frame, .07, .68, .68, .89)
        for text in texts:
            match = TIME_PATTERN.search(text)
            if match:
                current, total = map(clock_seconds, match.groups())
                if 0 <= current <= total <= 7200:
                    seen.append((frame['t'], current, total))
    return sorted(set(seen))


def playback_status(readouts, video_seconds, page_span, selected_duration):
    total = Counter(x[2] for x in readouts).most_common(1)[0][0] if readouts else selected_duration
    if total is None:
        return '待核', ['未读到播放器时间或所选分P时长']
    points = [(t, current) for t, current, observed_total in readouts
              if abs(observed_total - total) <= 2]
    jumps = [(round(t1, 1), round(t2, 1), c2 - c1)
             for (t1, c1), (t2, c2) in zip(points, points[1:])
             if c2 - c1 > (t2 - t1) + 8]
    if jumps:
        return '否', [f'播放器读数出现向前跳播：{jumps[:3]}']
    if points and min(c for _, c in points) <= 5 and max(c for _, c in points) >= total - 3:
        return '是', []
    if points and max(c for _, c in points) < total - 10 and points[-1][0] >= video_seconds - 20:
        return '否', ['录屏末段播放器读数距结尾超过10秒']
    if page_span is not None and total - 5 <= page_span <= total + 35:
        return '是', ['无首尾连续读数；按网页可见时长与视频时长接近推定，需复核']
    if page_span is not None and page_span < total - 10:
        return '否', ['网页可见录屏时长短于所选视频时长']
    return '待核', ['播放器读数未覆盖起点与终点，时长证据不足']


def part_information(records, title):
    """Take durations from the sidebar's far-right column, excluding recommendations."""
    observations = []
    for frame in records:
        items = frame['items']
        for clock in items:
            if not (.85 <= clock['x'] <= .93 and .25 <= clock['y'] <= .84
                    and SINGLE_TIME.fullmatch(clock['text'].strip())):
                continue
            same_row = [item['text'].strip() for item in items
                        if .69 <= item['x'] < .85 and abs(item['y'] - clock['y']) <= .018]
            name = ' '.join(same_row).strip()
            observations.append((name, clock['text'].strip()))
    unique = list(dict.fromkeys(observations))
    if not unique:
        return [], None
    # Prefer a sidebar row matching the page title; otherwise the highlighted
    # first part is the defensible default only when it is named "1".
    ranked = [(SequenceMatcher(None, title, name).ratio(), name, length)
              for name, length in unique if name]
    selected = None
    if ranked and max(ranked)[0] >= .55:
        selected = max(ranked)[2]
    elif len(re.sub(r'\W', '', unique[0][0])) <= 3 and '1' in unique[0][0]:
        selected = unique[0][1]
    return unique[:30], clock_seconds(selected) if selected else None


def resolve_row(recording, source_row=None):
    if source_row is None and recording.stem.isdigit() and 7813 <= int(recording.stem) <= 8212:
        source_row = int(recording.stem)
    if source_row is None:
        return None
    return next(item for item in entries() if item['row'] == source_row)


def format_finished(value):
    if not value:
        return ''
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return f'{dt.year}/{dt.month}/{dt.day} {dt:%H:%M:%S}'


def analyze(path, source_row=None):
    path = Path(path)
    source = resolve_row(path, source_row)
    seconds = duration(path)
    records = extract_ocr(path, seconds)
    title_candidates, url_candidates, view_candidates, dates, up_candidates = [], [], [], [], []
    page_times = []
    tag_seen = desc_seen = False
    for frame in records:
        visible_titles = [s for s in positioned(frame, .07, .68, .14, .195)
                          if '公证云' not in s and '见证实录' not in s
                          and not DATE_PATTERN.search(s) and not s.startswith(('口', '0'))]
        title_candidates.extend(visible_titles)
        address = positioned(frame, .045, .8, .035, .085)
        for line in address:
            match = BV_PATTERN.search(line)
            if match:
                url_candidates.append(match.group(0))
                page_times.append(frame['t'])
        for line in positioned(frame, .07, .68, .185, .235):
            if '播放' in line or re.search(r'\d+(?:\.\d+)?万', line):
                view_candidates.append(line)
            match = DATE_PATTERN.search(line)
            if match:
                dates.append(match.group(0))
        up_candidates.extend(positioned(frame, .7, .91, .14, .2))
        lower = positioned(frame, .06, .7, .7, 1.0)
        if any('标签' in line or line.startswith('#') for line in lower):
            tag_seen = True
        if any('简介' in line or '展开更多' in line for line in lower):
            desc_seen = True
    title, title_votes = winner(title_candidates)
    bv, bv_votes = winner(url_candidates)
    url = f'https://www.bilibili.com/video/{bv}' if bv else '未显示'
    if source:
        input_title = str(source['input_title']).strip()
        input_url = str(source['input_url']).strip()
        if re.sub(r'\s+', '', input_title) == title:
            title = input_title
        if BV_PATTERN.search(input_url) and BV_PATTERN.search(input_url).group(0) == bv:
            url = input_url
    flags = []
    if not title or title_votes < 2:
        flags.append('标题 OCR 证据不足')
    if not bv or bv_votes < 2:
        flags.append('地址栏 BV OCR 证据不足')
    readouts = player_readouts(records)
    parts, selected_duration = part_information(records, title)
    page_span = max(page_times) - min(page_times) if len(page_times) >= 2 else None
    r_value, playback_flags = playback_status(readouts, seconds, page_span, selected_duration)
    flags.extend(playback_flags)
    if source and source['input_title'] and title != str(source['input_title']).strip():
        flags.append('录屏标题与原表 C 不同')
    if source and source['input_url'] and url != str(source['input_url']).strip():
        flags.append('录屏地址与原表 E 不同')
    metadata_path = EZCUN_STATE / 'metadata' / f'{source_row or (source["row"] if source else "")}.json'
    metadata = json.loads(metadata_path.read_text()) if source and metadata_path.exists() else {}
    return {
        'sample': path.name, 'row': source['row'] if source else '',
        'D': title, 'F': url, 'L': format_finished(metadata.get('finishedWitnessTime')),
        'R': r_value, 'views_text': winner(view_candidates)[0],
        'published_at': winner(dates)[0], 'uploader_text': winner(up_candidates)[0],
        'parts_seen': '；'.join(name for name, _ in parts),
        'part_durations_seen': '；'.join(length for _, length in parts),
        'selected_part_seconds': selected_duration or '',
        'page_visible_seconds': round(page_span, 2) if page_span is not None else '',
        'readouts': json.dumps(readouts, ensure_ascii=False),
        'tags_visibility': '已见标签区' if tag_seen else '录屏未显示标签区',
        'description_visibility': '已见简介区' if desc_seen else '录屏未显示简介区',
        'flags': '；'.join(flags), 'sampled_frames': len(records),
        'recording_seconds': round(seconds, 2), 'title_votes': title_votes,
        'bv_votes': bv_votes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('videos', nargs='*', type=Path)
    parser.add_argument('--examples', action='store_true')
    parser.add_argument('--output', type=Path, default=STATE / 'sample_ledger.csv')
    args = parser.parse_args()
    videos = args.videos or ([EXAMPLES / f'{n}.mp4' for n in range(13, 18)] if args.examples else [])
    if not videos:
        parser.error('Pass videos or --examples')
    rows = []
    for path in videos:
        row = analyze(path)
        rows.append(row)
        print(f'WEBLEDGER {path.name} frames={row["sampled_frames"]} D={row["D"]!r} '
              f'F={row["F"]} R={row["R"]} flags={row["flags"]}', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f'WEBLEDGER_OUTPUT {args.output}', flush=True)


if __name__ == '__main__':
    main()
