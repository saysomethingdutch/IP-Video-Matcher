"""Read Bilibili desktop evidence from local video frames, without conclusion columns.

The module samples the visible Chrome/Bilibili page, uses Apple Vision OCR,
and keeps uncertain playback judgments explicit for owner review.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from pilot.ezcun.fetch import entries, check_entries, STATE as EZCUN_STATE

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
            try:
                command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t),
                         '-i', path, '-frames:v', '1', '-q:v', '4', '-y', file])
            except subprocess.CalledProcessError:
                # A few otherwise valid recordings have a YUV range that the
                # MJPEG encoder rejects. PNG accepts the decoded frame.
                file = frame_dir / f'{index:04d}.png'
                command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t),
                         '-i', path, '-frames:v', '1', '-y', file])
            if file.exists():
                files.append((t, file))
        if not files:
            raise RuntimeError(f'No video frames extracted from {path}')
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
    temp_cache = cache.with_suffix('.jsonl.tmp')
    temp_cache.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    temp_cache.replace(cache)
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
    if source_row is None and recording.stem.isdigit():
        source_row = int(recording.stem)
    if source_row is None:
        return None
    return next((item for item in entries() + check_entries() if item['row'] == source_row), None)


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
    title_observations = []
    tag_seen = desc_seen = collection_seen = False
    tag_text, desc_text, collections = [], [], []
    for frame in records:
        visible_titles = [s for s in positioned(frame, .07, .68, .14, .195)
                          if '公证云' not in s and '见证实录' not in s
                          and not DATE_PATTERN.search(s) and not s.startswith(('口', '0'))]
        title_candidates.extend(visible_titles)
        title_observations.extend((frame['t'], value) for value in visible_titles)
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
            tag_text.extend(line for line in lower if line.startswith('#') or '标签' in line)
        if any('简介' in line or '展开更多' in line for line in lower):
            desc_seen = True
            desc_text.extend(lower)
        sidebar = positioned(frame, .68, .95, .18, .7)
        if any('视频选集' in line or '合集' in line for line in sidebar):
            collection_seen = True
            collections.extend(line for line in sidebar if '视频选集' in line or '合集' in line)
    title, title_votes = winner(title_candidates)
    bv, bv_votes = winner(url_candidates)
    span_basis = '地址栏 BV OCR'
    if len(page_times) < 2 and title:
        page_times = [t for t, value in title_observations
                      if SequenceMatcher(None, re.sub(r'\s+', '', value), title).ratio() >= .7]
        span_basis = '网页标题 OCR'
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
    if len(page_times) < 2:
        flags.append('播放区间起止画面证据不足')
    readouts = player_readouts(records)
    parts, selected_duration = part_information(records, title)
    page_span = max(page_times) - min(page_times) if len(page_times) >= 2 else None
    r_value, playback_flags = playback_status(readouts, seconds, page_span, selected_duration)
    flags.extend(playback_flags)
    if source and source['input_title'] and title != str(source['input_title']).strip():
        flags.append('录屏标题与原表 C 不同')
    if source and source['input_url'] and url != str(source['input_url']).strip():
        flags.append('录屏地址与原表 E 不同')
    row_number = source_row or (source['row'] if source else '')
    metadata_folder = 'metadata_check30' if source and source['row'] > 8212 else 'metadata'
    metadata_path = EZCUN_STATE / metadata_folder / f'{row_number}.json'
    metadata = json.loads(metadata_path.read_text()) if source and metadata_path.exists() else {}
    certificate_check = None
    check_path = EZCUN_STATE / 'certificate_check5.json'
    if source and check_path.exists():
        certificate_check = next((item for item in json.loads(check_path.read_text())
                                  if item['row'] == source['row']), None)
    if certificate_check and certificate_check['difference_seconds']:
        flags.append(f"证书取证结束时间与接口相差{certificate_check['difference_seconds']:+d}秒")
    page_start = min(page_times) if page_times else None
    page_end = max(page_times) if page_times else None
    if page_end is not None and seconds - page_end <= 12:
        page_end = seconds
    total_from_readouts = Counter(x[2] for x in readouts).most_common(1)[0][0] if readouts else None
    video_duration = total_from_readouts or selected_duration
    structured = {
        'row': str(source['row']) if source else path.stem,
        'title': title or None, 'address': url if url != '未显示' else None,
        'bv': bv or None,
        'play_start': round(page_start, 2) if page_start is not None else None,
        'play_end': round(page_end, 2) if page_end is not None else None,
        'play_span_basis': span_basis if page_start is not None else None,
        'anchors': [[round(t, 2), current] for t, current, total in readouts
                    if video_duration is None or abs(total - video_duration) <= 2],
        'duration': video_duration,
        'duration_basis': '播放器读数' if total_from_readouts else '选集时长 OCR' if selected_duration else None,
        'complete': {'是': True, '否': False}.get(r_value),
        'tags': {'visible': tag_seen, 'items': list(dict.fromkeys(tag_text)), 'activity': None},
        'intro': {'visible': desc_seen, 'text': '；'.join(dict.fromkeys(desc_text)) if desc_seen else ''},
        'collection': {'visible': collection_seen, 'title': winner(collections)[0] or None,
                       'parts': [{'title': name, 'duration': length} for name, length in parts]},
        'comments': {'visible': False, 'traffic': []},
        'finished_witness_time': format_finished(metadata.get('finishedWitnessTime')) or None,
        'certificate_finished_time': format_finished(certificate_check['certificate_finished'])
                                     if certificate_check else None,
        'views_text': winner(view_candidates)[0] or None,
        'published_at': winner(dates)[0] or None,
        'uploader_text': winner(up_candidates)[0] or None,
        'flags': flags,
        'sources': {'recording': path.name, 'sampled_frames': len(records),
                    'recording_seconds': round(seconds, 2),
                    'title_votes': title_votes, 'bv_votes': bv_votes},
    }
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
        'bv_votes': bv_votes, '_structured': structured,
    }


def analyze_and_save(path, examples=False):
    row = analyze(path)
    structured = row.pop('_structured')
    output = STATE / ('samples' if examples or not path.stem.isdigit() or int(path.stem) < 7813 else '') / f'{path.stem}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.json.tmp')
    temp.write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(output)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('videos', nargs='*', type=Path)
    parser.add_argument('--examples', action='store_true')
    parser.add_argument('--check30', action='store_true')
    parser.add_argument('--all', action='store_true', help='Process every downloaded file in the selected set')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--output', type=Path, default=STATE / 'sample_ledger.csv')
    args = parser.parse_args()
    videos = args.videos or ([EXAMPLES / f'{n}.mp4' for n in range(13, 18)] if args.examples else [])
    if args.all:
        dataset = check_entries() if args.check30 else entries()
        base = Path('/Volumes/Element/ip-video-matcher-field-test/evidence_check30' if args.check30
                    else '/Volumes/Element/ip-video-matcher-field-test/evidence')
        videos = [base / f'{item["row"]}.mp4' for item in dataset]
    if not videos:
        parser.error('Pass videos or --examples')
    if args.workers < 1 or args.workers > 4:
        parser.error('--workers must be between 1 and 4')
    if args.workers > 1:
        compile_ocr()  # Compile once before threads invoke the shared Vision executable.
    available = []
    for path in videos:
        if path.is_file():
            available.append(path)
        else:
            print(f'WEBLEDGER_MISSING {path.name}', flush=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for path, row in zip(available, pool.map(lambda video: analyze_and_save(video, args.examples), available)):
            rows.append(row)
            print(f'WEBLEDGER {path.name} frames={row["sampled_frames"]} D={row["D"]!r} '
                  f'F={row["F"]} R={row["R"]} flags={row["flags"]}', flush=True)
    if not rows:
        parser.error('No recordings were available')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f'WEBLEDGER_COMPLETE count={len(rows)} missing={len(videos)-len(available)}', flush=True)
    print(f'WEBLEDGER_OUTPUT {args.output}', flush=True)


if __name__ == '__main__':
    main()
