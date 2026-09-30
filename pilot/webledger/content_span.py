"""Locate the recorded span of the first complete playthrough.

The page interval includes loading, error screens and recommendations.  This
module only uses the already measured player bar; it never reads sheet results.
"""
from __future__ import annotations

from pilot.webledger.progress_r2 import _bridge_outliers, _slope, decide


def _passes(points):
    clean = _bridge_outliers(sorted((float(t), float(p)) for t, p in points
                                    if 0 <= p <= 1))
    groups = []
    for index, point in enumerate(clean):
        if not groups:
            groups.append([])
        elif groups[-1]:
            previous = groups[-1][-1]
            drop = previous[1] - point[1]
            large_reset = (previous[1] >= .12 and point[1] <= .12 and drop >= .10) or (
                previous[1] >= .75 and point[1] <= .25 and drop >= .60)
            # Progress can reset to 15–25% if the next playthrough has already
            # advanced before the next two-second sample (7824).
            replay_reset = (drop >= .45 and point[1] <= .30 and
                            index + 1 < len(clean) and
                            clean[index + 1][1] >= point[1] + .002)
            # A short first attempt may restart near zero (8452).  Its small
            # reset is real only if the next readings keep moving forward.
            small_reset = (previous[1] >= .025 and point[1] <= .02 and drop >= .02
                           and len(groups[-1]) >= 2)
            if large_reset or replay_reset or small_reset:
                groups.append([])
        groups[-1].append(point)
    return groups


def _span(play, next_start, page_start, page_end, complete):
    first_t, first_p = play[0]
    last_t, last_p = play[-1]
    # End-to-end rate includes pauses and avoids doubling the rate when the
    # one-pixel bar advances only on alternating samples (long recordings).
    slope = ((last_p - first_p) / (last_t - first_t)
             if last_t > first_t and last_p > first_p + .1 else None)
    start_slope = _slope(play[:15]) or slope
    start, end = first_t, last_t
    extrapolated = False
    if complete and slope and .0002 <= slope <= .09:
        start = first_t - first_p / start_slope
        end = last_t + (1 - last_p) / slope
        # At the end of a long video, the bar can sit full while a Bilibili
        # recommendation covers the player.  Stop at the first sustained full
        # reading, not the final frame of that plateau.
        full = [t for i, (t, p) in enumerate(play) if p >= .995 and
                all(later_p >= .985 for _, later_p in play[i + 1:])]
        if full:
            end = min(end, full[0])
    elif len(play) >= 2:
        moving = [i for i in range(len(play) - 1)
                  if play[i + 1][1] - play[i][1] >= .002]
        if moving:
            start, end = play[moving[0]][0], play[moving[-1] + 1][0]
    start = max(page_start, min(first_t, start)) if complete else max(page_start, start)
    end = min(page_end, next_start if next_start is not None else page_end, end)
    end = max(start, end)
    extrapolated = complete and (first_t - start > 1 or end - last_t > 1)
    return round(start, 2), round(end, 2), extrapolated


def locate(points, page_start, page_end, r_decision, page_uncertain=False):
    """Return content fields; fallback page bounds are explicitly reviewable."""
    if page_start is None or page_end is None or page_end <= page_start:
        return {'passes': [], 'content_start': None, 'content_end': None,
                'content_basis': '目标页面区间不明', 'complete_needs_review': True}
    groups = _passes([[t, p] for t, p in points if page_start <= t <= page_end])
    passes = []
    complete_index = None
    for i, play in enumerate(groups):
        next_start = groups[i + 1][0][0] if i + 1 < len(groups) else None
        candidate = play + groups[i + 1][:1] if next_start is not None else play
        result = decide([[t, p] for t, p in candidate], page_start, page_end)
        complete = result.complete
        if complete is None and next_start is not None and max(p for _, p in play) < .8:
            complete = False
        start, end, extrapolated = _span(play, next_start, page_start, page_end,
                                          complete is True)
        passes.append({'recording_start': start, 'recording_end': end,
                       'observed_start': round(play[0][0], 2),
                       'observed_end': round(play[-1][0], 2),
                       'progress_start': round(play[0][1], 4),
                       'progress_end': round(play[-1][1], 4),
                       'complete': complete, 'extrapolated': extrapolated})
        if complete_index is None and complete is True:
            complete_index = i

    if complete_index is None and r_decision.complete is True and passes:
        # The R2 decision may have used a replay reset that was split more
        # finely here.  Keep its accepted completeness, but expose uncertainty.
        complete_index = max(range(len(groups)), key=lambda i:
                             groups[i][-1][1] - groups[i][0][1])
        passes[complete_index]['complete'] = True
        passes[complete_index]['extrapolated'] = True
    if complete_index is not None:
        chosen = passes[complete_index]
        start, end = chosen['recording_start'], chosen['recording_end']
        if r_decision.complete is not True:
            chosen['complete'] = None
            basis = f'进度条第{complete_index + 1}遍疑似完整；R待核'
        else:
            basis = (f'进度条第{complete_index + 1}遍完整；起止外推'
                     if chosen['extrapolated'] else f'进度条第{complete_index + 1}遍完整')
        low_max = max(p for _, p in groups[complete_index]) < .90
        review = (page_uncertain or r_decision.complete is not True or
                  (chosen['extrapolated'] and low_max))
    elif passes:
        moving = [p for p in passes if p['recording_end'] > p['recording_start']]
        start = min((p['recording_start'] for p in moving), default=page_start)
        end = max((p['recording_end'] for p in moving), default=page_end)
        basis = '进度条实测播放；无完整一遍'
        review = page_uncertain or r_decision.complete is None
    else:
        start, end = page_start, page_end
        basis = '进度条量不到，退回页面区间'
        review = True
    return {'passes': passes, 'content_start': round(start, 2),
            'content_end': round(end, 2), 'content_basis': basis,
            'complete_needs_review': bool(review)}
