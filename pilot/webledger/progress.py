"""Measure the thin blue player progress line in locally recorded Chrome frames."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median
import subprocess

import numpy as np


def progress_in_frame(frame: np.ndarray):
    """Find the player lower edge and measure its blue portion; no fixed coordinates."""
    height, width = frame.shape[:2]
    white = np.all(frame > 245, axis=2)
    middle = white[:, int(.15 * width):int(.60 * width)].mean(axis=1)
    candidates = []
    for y in range(int(.15 * height), int(.93 * height) - 4):
        if (middle[y] < .2 and middle[y + 1] > .7
                and min(middle[y + 2:y + 4]) > .95
                and np.mean(middle[y - 20:y - 3]) < .4):
            candidates.append(y)
    for y in reversed(candidates):
        vertical_white = white[y - 70:y - 5].mean(axis=0)
        left = [x for x in range(int(.04 * width), int(.30 * width))
                if vertical_white[x - 3:x].mean() > .9
                and vertical_white[x:x + 8].mean() < .5]
        right = [x for x in range(int(.50 * width), int(.85 * width))
                 if vertical_white[x - 8:x].mean() < .5
                 and vertical_white[x:x + 3].mean() > .9]
        if not left or not right:
            continue
        x0, x1 = left[0], right[0]
        if x1 - x0 < .35 * width:
            continue
        # Codec scaling can put the one-pixel blue bar one or two rows above
        # the last non-white player row. Prefer the lowest row that has it.
        for line_y in (y, y - 1, y - 2):
            line = frame[line_y].astype(np.int16)
            r, g, b = line[:, 0], line[:, 1], line[:, 2]
            blue = (g > 100) & (b > g + 3) & (g > r + 16) & (b > r + 23)
            first = x0
            while first < x1 and not blue[first]:
                first += 1
            if first > x0 + 8:
                continue
            end = first
            gap = 0
            while end < x1:
                gap = 0 if blue[end] else gap + 1
                if gap > 3:
                    break
                end += 1
            ratio = (end - gap - x0) / (x1 - x0)
            if 0 <= ratio <= 1.01:
                return round(min(1.0, ratio), 4)
    return None


def measure(path: Path, start: float, end: float, state: Path):
    cache = state / f'{path.stem}-progress-v2.json'
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get('start') == start and data.get('end') == end:
            return data['points']
    start = max(0.0, float(start))
    end = float(end)
    if end <= start:
        return []
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(start),
            '-i', str(path), '-t', str(end - start), '-vf',
            'fps=1/2,scale=960:540:flags=bilinear', '-pix_fmt', 'rgb24',
            '-f', 'rawvideo', 'pipe:1']
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = 960 * 540 * 3
    points = []
    try:
        index = 0
        while True:
            chunks = []
            remaining = frame_bytes
            while remaining:
                chunk = process.stdout.read(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining:
                break
            frame = np.frombuffer(b''.join(chunks), dtype=np.uint8).reshape(540, 960, 3)
            ratio = progress_in_frame(frame)
            if ratio is not None:
                points.append([round(start + index * 2, 2), ratio])
            index += 1
    finally:
        process.stdout.close()
        error = process.stderr.read().decode(errors='replace')
        if process.wait() != 0:
            raise RuntimeError(f'ffmpeg progress decode failed: {error[:300]}')
    data = {'start': start, 'end': end, 'points': points}
    temp = cache.with_suffix('.tmp')
    temp.write_text(json.dumps(data))
    temp.replace(cache)
    return points


def decide(points, target_start, target_end, autoplay_after=False):
    if len(points) < 5:
        return None, None, ['细进度条有效读数不足']
    raw_points = points
    low_times = [t for t, p in points if p <= .10 and t <= target_end - 8]
    saw_start = any(t2 - t1 <= 4 for t1, t2 in zip(low_times, low_times[1:]))
    # Hover controls can tint a few consecutive pixels blue. Remove short
    # spikes, while a real seek persists and therefore survives the median.
    points = [[t, round(median(p1 for t1, p1 in points[max(0, i - 2):i + 3]
                               if abs(t1 - t) <= 4), 4)]
              for i, (t, _) in enumerate(points)]
    # Adjacent advancing frames give the 1x rate even for fifteen-second
    # collection parts. Long windows would mistake their normal rise for a seek.
    slopes = []
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        dt, dp = t1 - t0, p1 - p0
        if 0 < dt <= 4 and .001 <= dp <= .20:
            slopes.append(dp / dt)
    if not slopes:
        return None, None, ['细进度条无法估算视频时长']
    slope = median(slopes)
    estimated = round(1 / slope) if slope else None
    jumps = []
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        dt, dp = t1 - t0, p1 - p0
        if dp > max(.10, dt * slope * 3.5 + .05):
            jumps.append([round(t0, 1), round(t1, 1), round(dp, 3)])
    if jumps:
        return False, estimated, [f'细进度条向前跳播：{jumps[:3]}']
    last = [(t, p) for t, p in raw_points if t >= target_end - 16]
    saw_end = bool(last and max(p for _, p in last) >= .99)
    if saw_start and (saw_end or autoplay_after and max(p for _, p in points) >= .90):
        return True, estimated, [] if saw_end else ['自动连播佐证播放到结尾']
    if last and max(p + max(0, target_end - t) / estimated for t, p in last) < .90:
        return False, estimated, ['目标页面结束时细进度条未到结尾']
    return None, estimated, ['细进度条未同时覆盖目标视频起点和终点']
