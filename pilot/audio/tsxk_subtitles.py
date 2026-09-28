"""One-second subtitle-strip OCR and local pinyin matching for WO-103-R1."""
from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'workspace/tsxk/audio/subtitles'
OCR_SOURCE = ROOT / 'pilot/tools/ocr_vision.swift'
OCR_BINARY = Path('/private/tmp/tsxk-webledger-ocr')
PINYIN_SOURCE = ROOT / 'pilot/audio/pinyin.swift'
PINYIN_BINARY = Path('/private/tmp/tsxk-audio-pinyin')
UI = ('登录', '注册', '试看', '弹幕', '倍速', '自动连播', '高清视频',
      '发送', '举报', '播放量', '订阅合集', '点赞', '关注', 'bilibili',
      '次创作', '切勿当真')


def compile_ocr():
    if OCR_BINARY.exists() and OCR_BINARY.stat().st_mtime >= OCR_SOURCE.stat().st_mtime:
        return
    subprocess.run(['swiftc', '-O', '-module-cache-path', '/private/tmp/tsxk-webledger-swift',
                    '-Xcc', '-fmodules-cache-path=/private/tmp/tsxk-webledger-clang',
                    str(OCR_SOURCE), '-o', str(OCR_BINARY)], check=True,
                   capture_output=True, text=True)


def player_strip(frame: np.ndarray):
    """Find the player bottom from the visible rectangle, then crop its subtitle band."""
    h, w = frame.shape[:2]
    white = np.all(frame > 245, axis=2)
    middle = white[:, int(.15 * w):int(.60 * w)].mean(axis=1)
    for y in range(int(.93 * h) - 4, int(.15 * h), -1):
        if not (middle[y] < .2 and middle[y + 1] > .7
                and min(middle[y + 2:y + 4]) > .95
                and np.mean(middle[max(0, y - 20):y - 3]) < .4):
            continue
        vertical = white[max(0, y - 70):y - 5].mean(axis=0)
        left = [x for x in range(int(.04 * w), int(.30 * w))
                if vertical[x - 3:x].mean() > .9 and vertical[x:x + 8].mean() < .5]
        right = [x for x in range(int(.50 * w), int(.85 * w))
                 if vertical[x - 8:x].mean() < .5 and vertical[x:x + 3].mean() > .9]
        if left and right and right[0] - left[0] > .35 * w:
            return (left[0] + 25, max(0, y - 115), right[0] - 25, y - 7)
    return (int(.13 * w), int(.60 * h), int(.67 * w), int(.82 * h))


def portrait_bounds(frame: np.ndarray):
    """Find a narrow video centered between black bars in the Bilibili player."""
    h, w = frame.shape[:2]
    y0, y1 = int(.24 * h), int(.81 * h)
    lo, hi = int(.085 * w), int(.67 * w)
    brightness = frame[y0 + 15:y1 - 15, lo:hi].mean(axis=(0, 2))
    bright = brightness > 15
    edges = np.diff(np.r_[False, bright, False].astype(np.int8))
    runs = [(a + lo, b + lo) for a, b in
            zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]
    if not runs:
        return None
    x0, x1 = max(runs, key=lambda run: run[1] - run[0])
    width = x1 - x0
    if not (w * .12 <= width <= w * .29 and
            .30 * w <= (x0 + x1) / 2 <= .47 * w):
        return None
    outside = np.r_[brightness[:max(0, x0 - lo - 10)],
                    brightness[min(len(brightness), x1 - lo + 10):]]
    if len(outside) and np.median(outside) > 12:
        return None
    return x0, y0, x1, y1


def _subtitle_crop(panel: Image.Image, mode: str):
    if mode == 'rot90':
        panel = panel.rotate(90, expand=True)
    elif mode == 'rot270':
        panel = panel.rotate(270, expand=True)
    top = .75 if mode != 'normal' else .63
    panel = panel.crop((int(.08 * panel.width), int(top * panel.height),
                        int(.94 * panel.width), int(.98 * panel.height)))
    return panel.resize((panel.width * 4, panel.height * 4), Image.Resampling.LANCZOS)


def detect_orientation(video: Path, start: float, end: float):
    """Compare local OCR of portrait content in three rotations; no upload."""
    samples = []
    with tempfile.TemporaryDirectory(prefix='tsxk-orientation-') as temp:
        for index, fraction in enumerate((.12, .23, .35, .47, .59, .71, .83, .94)):
            t = start + (end - start) * fraction
            target = Path(temp) / f'frame-{index}.jpg'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t),
                            '-i', str(video), '-frames:v', '1', '-vf', 'scale=960:540',
                            str(target), '-y'], capture_output=True, check=True)
            frame = np.array(Image.open(target).convert('RGB'))
            bounds = portrait_bounds(frame)
            if bounds:
                samples.append((frame, bounds))
        if len(samples) < 5:
            return 'normal'
        files = {}
        for index, (frame, (x0, y0, x1, y1)) in enumerate(samples):
            panel = Image.fromarray(frame[y0:y1, x0:x1])
            for mode in ('normal', 'rot90', 'rot270'):
                file = Path(temp) / f'ocr-{index}-{mode}.jpg'
                _subtitle_crop(panel, mode).save(file, quality=92)
                files[file.name] = mode
        process = subprocess.run([str(OCR_BINARY), *(str(Path(temp) / name) for name in files)],
                                 capture_output=True, text=True)
        if process.returncode:
            raise RuntimeError(f'Orientation OCR failed: {process.stderr[:250]}')
        scores = Counter()
        for line in process.stdout.splitlines():
            if not line.startswith('{'):
                continue
            result = json.loads(line)
            mode = files.get(result['frame'])
            if mode:
                score = sum(min(24, len(re.findall(r'[\u4e00-\u9fff]', item['text'])))
                            for item in result['items'] if not any(u in item['text'] for u in UI))
                scores[mode] += score
        winner = max(('normal', 'rot90', 'rot270'), key=lambda mode: scores[mode])
        return winner if winner != 'normal' and scores[winner] >= scores['normal'] + 15 else 'normal'


def _ocr_group(group):
    process = subprocess.run([str(OCR_BINARY), *(str(p) for _, p, _, _ in group)],
                             capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(f'Subtitle OCR failed: {process.stderr[:250]}')
    parsed = {j['frame']: j for j in (json.loads(line) for line in process.stdout.splitlines()
                                      if line.startswith('{'))}
    records = []
    for t, file, (x0, y0, x1, y1), mode in group:
        entry = parsed.get(file.name)
        items = []
        if entry:
            for item in entry['items']:
                value = item['text'].strip()
                if (len(re.sub(r'\W', '', value)) < 2 or
                        any(term in value for term in UI)):
                    continue
                if mode == 'normal':
                    x = (x0 + item['x'] * (x1 - x0)) / 960
                    y = (y0 + item['y'] * (y1 - y0)) / 540
                    # Long subtitles start farther left than short ones.
                    if x < .19 or x > .64 or (x < .25 and y < .75):
                        continue
                else:
                    x, y = item['x'], item['y']
                    if x < .03 or x > .97:
                        continue
                if re.search(r'\d\d:\d\d\s*[/／]\s*\d\d:\d\d', value):
                    continue
                items.append({'text': value, 'x': round(x, 4), 'y': round(y, 4)})
        records.append({'t': t, 'items': items})
        file.unlink(missing_ok=True)
    return records


def subtitles(video: Path, start: float, end: float):
    STATE.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(f'{video.resolve()}:{video.stat().st_size}:{start:.2f}:{end:.2f}:r4'.encode()).hexdigest()[:16]
    cache = STATE / f'{video.stem}-{fingerprint}.jsonl'
    if cache.exists():
        return [json.loads(line) for line in cache.read_text().splitlines()]
    compile_ocr()
    orientation = detect_orientation(video, start, end)
    if orientation == 'normal':
        older = hashlib.sha256(
            f'{video.resolve()}:{video.stat().st_size}:{start:.2f}:{end:.2f}:r3'.encode()
        ).hexdigest()[:16]
        prior = STATE / f'{video.stem}-{older}.jsonl'
        if prior.exists():
            cache.write_bytes(prior.read_bytes())
            return [json.loads(line) for line in cache.read_text().splitlines()]
    start_second = int(start)
    length = max(0, int(end - start_second + .999))
    process = subprocess.Popen(
        ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(start_second),
         '-i', str(video), '-t', str(length), '-vf', 'fps=1,scale=960:540:flags=bilinear',
         '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = 960 * 540 * 3
    records = []
    try:
        with tempfile.TemporaryDirectory(prefix='tsxk-subtitles-') as temp:
            group = []
            index = 0
            while True:
                chunks, remaining = [], size
                while remaining:
                    block = process.stdout.read(remaining)
                    if not block:
                        break
                    chunks.append(block)
                    remaining -= len(block)
                if remaining:
                    break
                t = start_second + index
                index += 1
                if t < start or t >= end:
                    continue
                frame = np.frombuffer(b''.join(chunks), dtype=np.uint8).reshape(540, 960, 3)
                if orientation == 'normal':
                    bounds = player_strip(frame)
                    x0, y0, x1, y1 = bounds
                    crop = Image.fromarray(frame[y0:y1, x0:x1]).resize(
                        ((x1 - x0) * 2, (y1 - y0) * 2), Image.Resampling.BICUBIC)
                else:
                    bounds = portrait_bounds(frame)
                    if bounds is None:
                        bounds = (int(.288 * 960), int(.24 * 540),
                                  int(.472 * 960), int(.81 * 540))
                    x0, y0, x1, y1 = bounds
                    crop = _subtitle_crop(Image.fromarray(frame[y0:y1, x0:x1]), orientation)
                file = Path(temp) / f'{index:06d}.jpg'
                crop.save(file, quality=88)
                group.append((t, file, bounds, orientation))
                if len(group) == 12:
                    records.extend(_ocr_group(group))
                    group = []
            if group:
                records.extend(_ocr_group(group))
    finally:
        process.stdout.close()
        error = process.stderr.read().decode(errors='replace')
        if process.wait() != 0:
            raise RuntimeError(f'Subtitle ffmpeg failed: {error[:300]}')
    # A logo/watermark stays at the same position across widely separated
    # seconds. Dialogue captions do not; exclude static text before matching.
    positions = defaultdict(list)
    for frame in records:
        for item in frame['items']:
            key = (re.sub(r'\W', '', item['text']).casefold(),
                   round(item['x'] * 20), round(item['y'] * 20))
            positions[key].append(frame['t'])
    fixed = {key for key, times in positions.items()
             if len(times) >= 12 and max(times) - min(times) >= 20}
    for frame in records:
        frame['items'] = [item for item in frame['items']
                          if (re.sub(r'\W', '', item['text']).casefold(),
                              round(item['x'] * 20), round(item['y'] * 20)) not in fixed]
    temp = cache.with_suffix('.tmp')
    temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    temp.replace(cache)
    return records


def compile_pinyin():
    if PINYIN_BINARY.exists() and PINYIN_BINARY.stat().st_mtime >= PINYIN_SOURCE.stat().st_mtime:
        return
    subprocess.run(['swiftc', '-O', '-module-cache-path', '/private/tmp/tsxk-audio-swift',
                    str(PINYIN_SOURCE), '-o', str(PINYIN_BINARY)], check=True,
                   capture_output=True, text=True)


def pinyin_values(values):
    compile_pinyin()
    values = list(dict.fromkeys(str(value) for value in values))
    process = subprocess.run([str(PINYIN_BINARY)], input=json.dumps(values, ensure_ascii=False),
                             capture_output=True, text=True, check=True)
    result = json.loads(process.stdout)
    def compact(value):
        value = unicodedata.normalize('NFKD', value).casefold()
        return ''.join(c for c in value if c.isalnum() and not unicodedata.combining(c))
    return dict(zip(values, map(compact, result)))


def match_caption(text, start, end, records, romanized):
    nearby = [item['text'] for frame in records if start - 2 <= frame['t'] <= end + 2
              for item in frame['items']]
    if not nearby:
        return 0.0, None, False
    a = romanized.get(text, '')
    scored = []
    for value in nearby:
        b = romanized.get(value, '')
        if not b:
            continue
        matcher = SequenceMatcher(None, a, b)
        similarity = matcher.ratio()
        # SenseVoice's four-second chunks often split a displayed sentence.
        # Compare how much of the spoken clause is covered by that caption,
        # allowing a small homophone/OCR error without accepting one shared
        # word from a substantially different sentence.
        if len(a) >= 4:
            coverage = sum(block.size for block in matcher.get_matching_blocks()) / len(a)
            if coverage >= .85:
                similarity = max(similarity, coverage)
        scored.append((similarity, value))
    if not scored:
        return 0.0, None, True
    score, value = max(scored)
    return round(score, 3), value, True
