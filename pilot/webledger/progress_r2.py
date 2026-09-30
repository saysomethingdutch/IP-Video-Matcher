"""Decide completeness from a measured player progress line, one playthrough at a time."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class Decision:
    complete: bool | None
    basis: str
    reason: str
    evidence_times: tuple[float, ...] = ()


def _bridge_outliers(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove brief blue overlays that return to the underlying progress line.

    A genuine seek persists. A coloured effect can last several sampled frames,
    but rejoins the old line after it disappears.
    """
    points = points[:]
    for _ in range(len(points)):
        removed = False
        # An isolated dark frame may make two neighbouring blue overlays look
        # like a persistent seek, so remove single-frame dips first.
        for width in (1, 4, 3, 2):
            for left in range(len(points) - width - 1):
                right = left + width + 1
                t0, p0 = points[left]
                t1, p1 = points[right]
                dt = t1 - t0
                if not 0 < dt <= 18 or not -.04 <= p1 - p0 <= .08 * dt:
                    continue
                deviations = [p - (p0 + (p1 - p0) * (t - t0) / dt)
                              for t, p in points[left + 1:right]]
                high = all(d > .07 for d in deviations)
                low_single = width == 1 and deviations[0] < -.08
                if high or low_single:
                    del points[left + 1:right]
                    removed = True
                    break
            if removed:
                break
        if not removed:
            break
    return points


def _split_passes(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    passes: list[list[tuple[float, float]]] = [[]]
    for current in points:
        if passes[-1]:
            previous = passes[-1][-1]
            restart_near_zero = (previous[1] >= .12 and current[1] <= .12
                                 and previous[1] - current[1] >= .10)
            restart_after_end = (previous[1] >= .75 and current[1] <= .25
                                 and previous[1] - current[1] >= .60)
            if restart_near_zero or restart_after_end:
                passes.append([])
        passes[-1].append(current)
    return passes


def _slope(points: list[tuple[float, float]]) -> float | None:
    rates = [(p1 - p0) / (t1 - t0)
             for (t0, p0), (t1, p1) in zip(points, points[1:])
             if 0 < t1 - t0 <= 12 and .0004 <= (p1 - p0) / (t1 - t0) <= .08]
    return median(rates) if len(rates) >= 2 else None


def decide(points: list[list[float]], target_start: float, target_end: float) -> Decision:
    if target_end <= target_start:
        return Decision(None, '进度条', '目标页面播放区间不足')
    readings = sorted((float(t), float(p)) for t, p in points
                      if target_start <= t <= target_end and 0 <= p <= 1)
    readings = _bridge_outliers(readings)
    if len(readings) < 5:
        return Decision(None, '进度条', '细进度条有效读数不足')

    definite_jumps: list[tuple[float, float]] = []
    possible_incomplete: list[tuple[float, float]] = []
    passes = _split_passes(readings)
    for pass_index, play in enumerate(passes):
        if len(play) < 5:
            continue
        slope = _slope(play)
        if slope is None:
            continue
        jumps = []
        for index, ((t0, p0), (t1, p1)) in enumerate(zip(play, play[1:])):
            dt = t1 - t0
            if dt > 18:
                continue
            excess = p1 - p0 - slope * dt
            if excess < max(.09, .05 + 1.5 * slope * dt):
                continue
            # The new position must remain visible for at least two readings.
            following = play[index + 2:index + 4]
            if following and following[0][0] - t1 <= 10 and all(
                abs(p - (p1 + slope * (t - t1))) <= .065 for t, p in following
            ):
                jumps.append((t0, t1))
        if jumps:
            definite_jumps.extend(jumps)
            continue

        first = play[:min(3, len(play))]
        last = play[-min(3, len(play)):]
        zero_at = median(t - p / slope for t, p in first)
        full_at = median(t + (1 - p) / slope for t, p in last)
        next_start = passes[pass_index + 1][0][0] if pass_index + 1 < len(passes) else None
        reset_at_end = (next_start is not None and play[-1][1] - play[0][1] >= .45
                        and abs(full_at - next_start) <= 6)
        substantial = play[0][1] <= .25 and (
            (play[-1][1] >= .80 and play[-1][1] - play[0][1] >= .60)
            or reset_at_end)
        near_start = play[0][0] <= target_start + 8 and play[0][1] <= .06
        near_end = play[-1][0] >= target_end - 8 and play[-1][1] >= .99
        if substantial and (zero_at >= target_start - 4 or near_start) and (
            full_at <= target_end + 4 or near_end
        ):
            extrapolated = play[0][1] > .015 or play[-1][1] < .99
            basis = '细进度条逐遍判定；起止外推' if extrapolated else '细进度条逐遍判定'
            if reset_at_end:
                basis += '；重播复位佐证'
            return Decision(True, basis,
                            f'本遍无持续跳转；外推起点{zero_at:.1f}秒、终点{full_at:.1f}秒'
                            if extrapolated else '本遍从头播放到结尾')
        t_last, p_last = play[-1]
        if (t_last >= target_end - 20 and p_last < .90
                and full_at > target_end + 4):
            possible_incomplete.append((t_last, p_last))

    if definite_jumps:
        t0, t1 = definite_jumps[0]
        return Decision(False, '细进度条持续跳转', f'本遍向前跳播：{t0:.1f}→{t1:.1f}秒', (t0, t1))
    last_t, last_p = readings[-1]
    if last_t >= target_end - 6 and last_p < .60:
        return Decision(False, '细进度条结束位置', f'目标页面结束前进度仅{last_p:.1%}', (last_t,))
    if possible_incomplete:
        t, p = possible_incomplete[-1]
        return Decision(False, '细进度条结束位置', f'目标页面结束前进度仅{p:.1%}', (t,))
    return Decision(None, '细进度条', '各遍均无足够起止证据')
