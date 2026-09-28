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
import unicodedata

from pilot.ezcun.fetch import entries, check_entries, STATE as EZCUN_STATE
from pilot.webledger.progress import measure as measure_progress, decide as decide_progress
from pilot.webledger.progress_r2 import decide as decide_progress_r2
from pilot.webledger.content_span import locate as locate_content

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'workspace/tsxk/webledger'
OCR_SOURCE = ROOT / 'pilot/tools/ocr_vision.swift'
OCR_BINARY = Path('/private/tmp/tsxk-webledger-ocr')
EXAMPLES = Path('/Volumes/Element/ip-video-matcher-field-test/field-test/videos')
BV_PATTERN = re.compile(r'BV[0-9A-Za-z]{10}')
LOOSE_BV_PATTERN = re.compile(r'BV[0-9A-Za-z\u4e00-\u9fff]{10,12}')
TIME_PATTERN = re.compile(r'(\d{1,2}:\d{2}(?::\d{2})?)\s*[/／]\s*(\d{1,2}:\d{2}(?::\d{2})?)')
SINGLE_TIME = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
DATE_PATTERN = re.compile(r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}:\d{2}')
FIXED_TITLE_TEXT = ('未经作者授权，禁止转载', '未经授权，禁止转载')


def normalized_title(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ''.join(char for char in value if char.isalnum())


def same_title(visible, original):
    if not visible or not original:
        return None
    a, b = normalized_title(visible), normalized_title(original)
    if not a or not b:
        return None
    if a == b:
        return True
    if min(len(a), len(b)) >= 12 and abs(len(a) - len(b)) >= 3:
        common = 0
        for left, right in zip(a, b):
            if left != right:
                break
            common += 1
        # The visible title is often clipped by the Chrome window. Allow two
        # OCR characters at its cut edge, but require the whole visible prefix.
        if common >= min(len(a), len(b)) - 2:
            return True
    return edit_distance(a, b) <= (2 if min(len(a), len(b)) >= 12 else 1)


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def bv_distance(left, right):
    if not left or not right:
        return 99
    # OCR frequently confuses BV letter case even while the page stays put.
    a, b = left[2:].casefold(), right[2:].casefold()
    return edit_distance(a, b)


def address_observation(frame):
    for line in positioned(frame, 0, .82, .02, .11):
        match = LOOSE_BV_PATTERN.search(line)
        if match:
            return match.group(0), ('player_end_recommend_autoplay' in line or
                                    'player.switch' in line)
    return None, False


def desktop_visible(frame):
    """A missing address is not proof that Chrome closed; desktop icons are."""
    text = ' '.join(positioned(frame, 0, .32, 0, .24))
    return sum(label in text for label in ('此电脑', '回收站', 'App Center')) >= 2


def tab_matches(frame, title):
    if not title:
        return False
    return any(same_title(line, title) is True
               for line in positioned(frame, 0, .7, 0, .045)
               if '公证云' not in line and '见证实录' not in line)


def error_page_times(records, start, end):
    return [frame['t'] for frame in records if start <= frame['t'] < end and
            any(('出错啦' in item['text'] or '安全风控' in item['text'] or
                 '访问请求被拒绝' in item['text']) for item in frame['items'])]


def refine_switch(path, last_target, first_other, target_bv):
    """Resolve a coarse OCR sampling gap to the first changed address second."""
    if first_other - last_target > 20:
        return first_other
    times = list(range(int(last_target) + 1, int(first_other) + 1))
    if not times:
        return first_other
    compile_ocr()
    with tempfile.TemporaryDirectory(prefix='tsxk-switch-') as temp:
        files = []
        for t in times:
            file = Path(temp) / f'{t}.jpg'
            try:
                command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t),
                         '-i', path, '-frames:v', '1', '-q:v', '4', '-y', file])
            except subprocess.CalledProcessError:
                file = Path(temp) / f'{t}.png'
                command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t),
                         '-i', path, '-frames:v', '1', '-y', file])
            files.append((t, file))
        for t, file in files:
            process = subprocess.run([str(OCR_BINARY), str(file)], capture_output=True, text=True)
            if process.returncode:
                continue
            parsed = [json.loads(line) for line in process.stdout.splitlines() if line.startswith('{')]
            if not parsed:
                continue
            found, _ = address_observation({'items': parsed[0]['items']})
            if found and bv_distance(found, target_bv) > 2:
                return float(t)
    return first_other


def page_segments(records, source_bv, seconds, path, source_title=None):
    observed = [(frame['t'], *address_observation(frame)) for frame in records]
    same_tabs = [tab_matches(frame, source_title) for frame in records]
    desktop = [desktop_visible(frame) for frame in records]
    target_bv = source_bv or next((bv for _, bv, _ in observed if bv), None)
    matches = [i for i, (_, bv, _) in enumerate(observed)
               if (bv and bv_distance(bv, target_bv) <= 2) or
               (not bv and same_tabs[i])]
    if not matches:
        return [], None, None, target_bv, [], False, ['原表 E 的目标 BV 未在录屏地址栏找到']
    first = matches[0]
    first_t = observed[first][0]
    # The first matching sample follows a page navigation by at most one OCR
    # sampling interval; do not include any preceding different BV.
    preceding = next((observed[i][0] for i in range(first - 1, -1, -1)
                      if observed[i][1] and bv_distance(observed[i][1], target_bv) > 2), None)
    start = max(preceding or 0, first_t - 3)
    switch_i = None
    closed_i = None
    for i in range(first + 1, len(observed)):
        t, bv, autoplay = observed[i]
        if bv and bv_distance(bv, target_bv) > 2:
            # A lone corrupt OCR reading between matching BV frames is ignored.
            following = observed[i + 1:i + 3]
            if not autoplay and any(other and bv_distance(other, target_bv) <= 2
                                    for _, other, _ in following):
                continue
            switch_i = i
            break
        if not bv and not same_tabs[i] and i + 1 < len(observed):
            if desktop[i] and desktop[i + 1] and not same_tabs[i + 1]:
                closed_i = i
                break
    if switch_i is not None:
        last_target = max((observed[i][0] for i in matches if i < switch_i), default=first_t)
        end = refine_switch(path, last_target, observed[switch_i][0], target_bv)
    elif closed_i is not None:
        end = observed[closed_i][0]
    else:
        end = seconds
    target_records = [record for record in records if start <= record['t'] < end
                      and (address_observation(record)[0] is None or
                           bv_distance(address_observation(record)[0], target_bv) <= 2)]
    other_pages = []
    if switch_i is not None:
        for t, bv, autoplay in observed[switch_i:]:
            if not bv or bv_distance(bv, target_bv) <= 2:
                continue
            if other_pages and bv_distance(bv, other_pages[-1]['bv']) <= 2:
                other_pages[-1]['end'] = round(t, 2)
                other_pages[-1]['autoplay'] |= autoplay
            else:
                if other_pages:
                    other_pages[-1]['end'] = round(t, 2)
                other_pages.append({'bv': bv, 'start': round(max(t, end), 2),
                                    'end': round(t, 2), 'autoplay': autoplay})
        if other_pages:
            other_pages[-1]['end'] = round(seconds, 2)
            other_pages[0]['start'] = round(end, 2)
    flags = []
    if closed_i is not None:
        flags.append('目标页面之后浏览器关闭（连续桌面帧）')
    return target_records, round(start, 2), round(end, 2), target_bv, other_pages, bool(
        switch_i is not None and observed[switch_i][2]), flags


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
    all_records = extract_ocr(path, seconds)
    input_url = str(source['input_url']).strip() if source else ''
    input_bv_match = BV_PATTERN.search(input_url)
    input_bv = input_bv_match.group(0) if input_bv_match else None
    records, page_start, page_end, target_bv, other_pages, autoplay_after, page_flags = page_segments(
        all_records, input_bv, seconds, path, source['input_title'] if source else None)
    title_candidates, url_candidates, view_candidates, dates, up_candidates = [], [], [], [], []
    tag_seen = desc_seen = collection_seen = False
    tag_text, desc_text, collections = [], [], []
    for frame in records:
        visible_titles = [s for s in positioned(frame, .07, .68, .14, .195)
                          if '公证云' not in s and '见证实录' not in s
                          and not any(fixed in s for fixed in FIXED_TITLE_TEXT)
                          and not DATE_PATTERN.search(s) and not s.startswith(('口', '0'))]
        title_candidates.extend(visible_titles)
        address = positioned(frame, 0, .8, .035, .085)
        for line in address:
            match = BV_PATTERN.search(line)
            if match:
                url_candidates.append(match.group(0))
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
    if source and title_candidates:
        input_title = str(source['input_title']).strip()
        close = [s for s in title_candidates if same_title(s, input_title)]
        if close:
            title, title_votes = winner(close)
    bv, bv_votes = winner(url_candidates)
    if bv and target_bv and bv_distance(bv, target_bv) <= 2:
        bv = target_bv
    span_basis = '原表 E 的 BV 与录屏地址栏匹配；换页边界逐秒复核'
    url = f'https://www.bilibili.com/video/{bv}' if bv else '未显示'
    title_same = address_same = None
    if source:
        input_title = str(source['input_title']).strip()
        title_same = same_title(title, input_title)
        address_same = bv_distance(bv, input_bv) <= 2 if bv and input_bv else None
        if title_same:
            title = input_title
        if address_same:
            url = input_url
    flags = list(page_flags)
    if page_start is not None and page_end is not None:
        errors = error_page_times(all_records, page_start, page_end)
        if errors:
            flags.append(f'目标页出现 B 站报错页：{min(errors):.0f}–{max(errors):.0f}秒')
    if not title or title_votes < 2:
        flags.append('标题 OCR 证据不足')
    if not bv or bv_votes < 2:
        flags.append('地址栏 BV OCR 证据不足')
    if page_start is None or page_end is None:
        flags.append('播放区间起止画面证据不足')
    readouts = player_readouts(records)
    parts, selected_duration = part_information(records, title)
    page_span = page_end - page_start if page_start is not None and page_end is not None else None
    progress_points = measure_progress(path, page_start, page_end, STATE) if page_span and page_span > 5 else []
    if other_pages:
        for (t0, p0), (t1, p1) in zip(progress_points, progress_points[1:]):
            if page_end - 5 <= t1 < page_end and p0 >= .90 and p1 <= .10:
                page_end = t1
                page_span = page_end - page_start
                other_pages[0]['start'] = round(page_end, 2)
                progress_points = [[t, p] for t, p in progress_points if t < page_end]
                break
    _, progress_duration, _ = decide_progress(
        progress_points, page_start or 0, page_end or 0, autoplay_after)
    r2 = decide_progress_r2(progress_points, page_start or 0, page_end or 0)
    content = locate_content(progress_points, page_start, page_end, r2,
                             page_uncertain=page_start is None or page_end is None)
    r_decision = r2.complete
    playback_flags = ([f'R2：{r2.reason}'] if r_decision is not True or '外推' in r2.basis
                      else [])
    r_value = {True: '是', False: '否', None: '待核'}[r_decision]
    flags.extend(playback_flags)
    if title_same is False:
        flags.append('录屏标题与原表 C 不同')
    if address_same is False:
        flags.append('录屏地址 BV 与原表 E 不同')
    row_number = source_row or (source['row'] if source else '')
    metadata_folder = 'metadata_check30' if source and source['row'] > 8212 else 'metadata'
    metadata_path = EZCUN_STATE / metadata_folder / f'{row_number}.json'
    metadata = json.loads(metadata_path.read_text()) if source and metadata_path.exists() else {}
    certificate_path = EZCUN_STATE / 'certificates' / f'{row_number}.json'
    certificate = json.loads(certificate_path.read_text()) if certificate_path.exists() else None
    certificate_time = certificate['certificate_finished'] if certificate else None
    api_time = format_finished(metadata.get('finishedWitnessTime')) or None
    finished_time = certificate_time or api_time
    if source and source['row'] <= 8212 and not certificate_time:
        flags.append('证书尚未读到；L 暂用接口时间')
    total_from_readouts = Counter(x[2] for x in readouts).most_common(1)[0][0] if readouts else None
    video_duration = progress_duration or total_from_readouts or selected_duration
    structured = {
        'row': str(source['row']) if source else path.stem,
        'title': title or None, 'address': url if url != '未显示' else None,
        'bv': bv or None,
        'play_start': round(page_start, 2) if page_start is not None else None,
        'play_end': round(page_end, 2) if page_end is not None else None,
        'play_span_basis': span_basis if page_start is not None else None,
        'other_pages': other_pages,
        'progress': progress_points,
        'anchors': [[round(t, 2), current] for t, current, total in readouts
                    if video_duration is None or abs(total - video_duration) <= 2],
        'duration': video_duration,
        'duration_basis': '进度条估算（按 1 倍速）' if progress_duration else
                          '播放器读数' if total_from_readouts else
                          '选集时长 OCR' if selected_duration else None,
        'complete': {'是': True, '否': False}.get(r_value),
        'complete_basis': r2.basis,
        'complete_reason': r2.reason,
        **content,
        'title_same_as_input': title_same,
        'address_same_as_input': address_same,
        'tags': {'visible': tag_seen, 'items': list(dict.fromkeys(tag_text)), 'activity': None},
        'intro': {'visible': desc_seen, 'text': '；'.join(dict.fromkeys(desc_text)) if desc_seen else ''},
        'collection': {'visible': collection_seen, 'title': winner(collections)[0] or None,
                       'parts': [{'title': name, 'duration': length} for name, length in parts]},
        'comments': {'visible': False, 'traffic': []},
        'finished_witness_time': finished_time,
        'api_finished_time': api_time,
        'l_basis': '证书' if certificate_time else
                   '接口（检验集无需证书）' if source and source['row'] > 8212 and api_time else
                   '接口（证书读取失败）' if api_time else None,
        'views_text': winner(view_candidates)[0] or None,
        'published_at': winner(dates)[0] or None,
        'uploader_text': winner(up_candidates)[0] or None,
        'flags': flags,
        'sources': {'recording': path.name, 'sampled_frames': len(all_records),
                    'recording_seconds': round(seconds, 2),
                    'title_votes': title_votes, 'bv_votes': bv_votes},
    }
    return {
        'sample': path.name, 'row': source['row'] if source else '',
        'D': title, 'F': url, 'L': finished_time or '',
        'R': r_value, 'views_text': winner(view_candidates)[0],
        'title_same_as_input': title_same, 'address_same_as_input': address_same,
        'other_pages_count': len(other_pages),
        'published_at': winner(dates)[0], 'uploader_text': winner(up_candidates)[0],
        'parts_seen': '；'.join(name for name, _ in parts),
        'part_durations_seen': '；'.join(length for _, length in parts),
        'selected_part_seconds': selected_duration or '',
        'page_visible_seconds': round(page_span, 2) if page_span is not None else '',
        'readouts': json.dumps(readouts, ensure_ascii=False),
        'tags_visibility': '已见标签区' if tag_seen else '录屏未显示标签区',
        'description_visibility': '已见简介区' if desc_seen else '录屏未显示简介区',
        'flags': '；'.join(flags), 'sampled_frames': len(all_records),
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
