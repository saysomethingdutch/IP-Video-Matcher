"""Sequence matching: pick, for a time-ordered list of query lines, the best chain of source subtitles
(same episode, source time increasing at a plausible rate), then decide LINKED / AMBIGUOUS / NO_MATCH
per line (WO-002a §3.8).

Scores (log-odds style, bigger is better):
  emission   line weight * emit_scale * (sim - emit_zero) * place weight    (sim 1.0, no flags -> +6)
             The line weight comes from the OPENING_ENDING / SHORT flags of the best-matching text, so a
             generic line is weak evidence wherever it matches but its exact matches still beat partial
             matches of other lines; the place weight comes from RECAP / BOUNDARY_REGION of each occurrence.
  transition same episode, source time consistent with the query gap -> +1 (continuous; minus up to
             0.3 for straying from the nominal 1.25x rate, which breaks ties between repeats of a line)
             same episode, forward jump (an edit) -> -1; backwards -> -2; other episode -> -2
A chain may skip lines (they stay unmatched) and may restart anywhere at the cost of a jump, so one
best path covers every segment. Forward/backward passes give, for every candidate, the best total
score of a path forced through it; the gap to the best non-equivalent alternative is the margin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .search import Candidate, Index

NO_MATCH, LINKED, AMBIGUOUS = "NO_MATCH", "LINKED", "AMBIGUOUS"


@dataclass(frozen=True)
class Params:
    min_sim: float = 0.6          # candidates below this similarity are ignored
    max_cands: int = 30           # per line (all candidates with sim >= 0.9 are always kept)
    emit_scale: float = 15.0
    emit_zero: float = 0.6
    window: int = 8               # lines looked back for a direct transition
    rate_min: float = 0.5         # plausible source seconds per query second (playback speed, small cuts)
    rate_max: float = 2.2
    slack_s: float = 3.0
    rate_nominal: float = 1.25    # between 1x and 1.5x playback
    rate_dev_weight: float = 0.1
    cont: float = 1.0
    fwd_jump: float = -1.0
    back_jump: float = -2.0
    cross_ep: float = -2.0
    restart: float = -2.0         # joining an earlier chain outside the window
    link_sim: float = 0.7         # LINKED with chain support (a continuous neighbour)
    solo_sim: float = 0.85        # LINKED without chain support
    min_margin: float = 1.0
    amb_sim: float = 0.8          # candidates listed for AMBIGUOUS
    equiv_s: float = 4.0          # candidates in one episode this close are the same place
    solo_factor: float = 0.85     # confidence multiplier without chain support
    max_listed: int = 10


def _is_cont(p: Params, dq: float, a: Candidate, b: Candidate) -> bool:
    ds = b.entry.t_start - a.entry.t_start
    return a.entry.episode == b.entry.episode and p.rate_min * dq - p.slack_s <= ds <= p.rate_max * dq + p.slack_s


def _trans(p: Params, dq: float, a: Candidate, b: Candidate) -> float:
    if a.entry.episode != b.entry.episode:
        return p.cross_ep
    ds = b.entry.t_start - a.entry.t_start
    if _is_cont(p, dq, a, b):
        return p.cont - p.rate_dev_weight * abs(ds - p.rate_nominal * dq) / (dq + 1)
    return p.fwd_jump if ds > 0 else p.back_jump


def _same_place(p: Params, a: Candidate, b: Candidate) -> bool:
    return a.entry.episode == b.entry.episode and abs(a.entry.t_start - b.entry.t_start) <= p.equiv_s


def match_sequence(index: Index, rows: list[tuple[float, str]], p: Params = Params()) -> list[dict]:
    """rows: [(t_query, text), ...] in time order. Returns one SourceLink dict per row:

    status        LINKED / AMBIGUOUS / NO_MATCH (NO_MATCH is normal: the uploader's own subtitles or narration)
    episode, t_source, t_source_end   the linked subtitle entry (seconds from the start of the episode file)
    sim, conf     text similarity; confidence = sim * (1 - exp(-margin / 2)), x0.85 without chain support
    support       path neighbours reached by a continuous transition (0, 1 or 2)
    margin        score gap to the best path through a different place
    candidates    AMBIGUOUS: every place; LINKED: other strong places; NO_MATCH: the closest weak lines
    """
    n = len(rows)
    ts = [float(t) for t, _ in rows]
    cands = [index.search(text, min_sim=p.min_sim, top=p.max_cands) for _, text in rows]
    emit = []
    for cs in cands:
        best = max((c.sim for c in cs), default=0.0)
        line_w = max((c.entry.text_weight for c in cs if c.sim >= best - 1e-9), default=1.0)
        emit.append([line_w * p.emit_scale * (c.sim - p.emit_zero) * c.entry.place_weight for c in cs])

    def trans(i, a, j, b):  # i < j
        return _trans(p, max(0.0, ts[j] - ts[i]), cands[i][a], cands[j][b])

    def cont(i, a, j, b):
        return _is_cont(p, max(0.0, ts[j] - ts[i]), cands[i][a], cands[j][b])

    # forward: F = best path ending at the node; back = (prev node or None, kind)
    fwd = [[0.0] * len(cs) for cs in cands]
    back: list[list[tuple]] = [[(None, "start")] * len(cs) for cs in cands]
    best_so_far = (0.0, None)
    for i in range(n):
        for k in range(len(cands[i])):
            score, node, kind = 0.0, None, "start"
            if best_so_far[1] is not None and best_so_far[0] + p.restart > score:
                score, node, kind = best_so_far[0] + p.restart, best_so_far[1], "restart"
            for j in range(max(0, i - p.window), i):
                for m in range(len(cands[j])):
                    s = fwd[j][m] + trans(j, m, i, k)
                    if s > score:
                        score, node, kind = s, (j, m), "trans"
            fwd[i][k] = emit[i][k] + score
            back[i][k] = (node, kind)
        for k in range(len(cands[i])):
            if fwd[i][k] > best_so_far[0]:
                best_so_far = (fwd[i][k], (i, k))

    # backward: B = best path starting at the node
    bwd = [[0.0] * len(cs) for cs in cands]
    best_after = (0.0, None)
    for i in reversed(range(n)):
        for k in range(len(cands[i])):
            score = max(0.0, best_after[0] + p.restart) if best_after[1] is not None else 0.0
            for j in range(i + 1, min(n, i + p.window + 1)):
                for m in range(len(cands[j])):
                    score = max(score, bwd[j][m] + trans(i, k, j, m))
            bwd[i][k] = emit[i][k] + score
        for k in range(len(cands[i])):
            if bwd[i][k] > best_after[0]:
                best_after = (bwd[i][k], (i, k))

    through = [[fwd[i][k] + bwd[i][k] - emit[i][k] for k in range(len(cands[i]))] for i in range(n)]

    # best path
    on_path: dict[int, int] = {}
    link_in: dict[int, tuple] = {}   # line -> (prev line, prev cand, kind) along the best path
    node = best_so_far[1]
    while node is not None:
        i, k = node
        on_path[i] = k
        prev, kind = back[i][k]
        if prev is not None:
            link_in[i] = (*prev, kind)
        node = prev
    link_out = {j: (i, on_path[i], kind) for i, (j, _, kind) in link_in.items()}

    def edge(i, a, j, b, kind):
        return trans(i, a, j, b) if kind == "trans" else p.restart

    out = []
    for i, (_, text) in enumerate(rows):
        cs = cands[i]
        res = {"i": i, "t_query": ts[i], "text": text, "status": NO_MATCH, "episode": None, "t_source": None,
               "t_source_end": None, "sim": None, "conf": None, "source_text": None, "entry_id": None,
               "flags": [], "support": 0, "margin": None, "candidates": []}
        out.append(res)
        if not cs:
            continue
        k = on_path[i] if i in on_path else max(range(len(cs)), key=lambda m: through[i][m])
        c = cs[k]
        # competing places: other occurrences whose text evidence is comparable (a weaker partial match of
        # another line does not make a line ambiguous)
        floor = min(p.amb_sim, c.sim)
        alts = [through[i][m] for m in range(len(cs)) if cs[m].sim >= floor and not _same_place(p, cs[m], c)]
        margin = through[i][k] - max(alts) if alts else math.inf

        # chain support: a path neighbour reached by a continuous transition that is itself a decent match
        prev_l, next_l = (link_in.get(i), link_out.get(i)) if i in on_path else (None, None)
        support = 0
        if prev_l and prev_l[2] == "trans" and cont(prev_l[0], prev_l[1], i, k) \
                and cands[prev_l[0]][prev_l[1]].sim >= p.link_sim:
            support += 1
        if next_l and next_l[2] == "trans" and cont(i, k, next_l[0], next_l[1]) \
                and cands[next_l[0]][next_l[1]].sim >= p.link_sim:
            support += 1

        if support:
            # also require a margin against dropping the line from the chain (prev -> next directly)
            skip = emit[i][k]
            if prev_l:
                skip += edge(prev_l[0], prev_l[1], i, k, prev_l[2])
            if next_l:
                skip += edge(i, k, next_l[0], next_l[1], next_l[2])
            if prev_l and next_l:
                direct = next_l[0] - prev_l[0] <= p.window
                skip -= trans(prev_l[0], prev_l[1], next_l[0], next_l[1]) if direct else p.restart
            margin = min(margin, skip)
            linked = c.sim >= p.link_sim and margin >= p.min_margin
            conf = c.sim * (1 - math.exp(-margin / 2))
        else:
            linked = c.sim >= p.solo_sim and margin >= p.min_margin
            conf = c.sim * (1 - math.exp(-margin / 2)) * p.solo_factor

        # distinct places, best path score first
        places: list[int] = []
        for m in sorted(range(len(cs)), key=lambda m: (-through[i][m], -cs[m].sim)):
            if not any(_same_place(p, cs[m], cs[x]) for x in places):
                places.append(m)

        def listing(ms):
            return [{**cs[m].as_dict(), "path_score": round(through[i][m], 2)} for m in ms[:p.max_listed]]

        res.update(margin=None if math.isinf(margin) else round(margin, 2), support=support)
        strong = [m for m in places if cs[m].sim >= p.amb_sim]
        if linked:
            e = c.entry
            res.update(status=LINKED, episode=e.episode, t_source=e.t_start, t_source_end=e.t_end,
                       sim=round(c.sim, 3), conf=round(conf, 3), source_text=e.text, entry_id=e.id,
                       flags=list(e.flags), candidates=listing([m for m in strong if not _same_place(p, cs[m], c)]))
        elif strong:
            res.update(status=AMBIGUOUS, sim=round(cs[strong[0]].sim, 3), candidates=listing(strong))
        else:  # NO_MATCH: show the closest (weak) source lines for review
            res.update(sim=round(max(x.sim for x in cs), 3), candidates=listing(places[:3]))
    return out


def segments(links: list[dict], p: Params = Params()) -> list[dict]:
    """Group consecutive LINKED lines that stay in one episode with non-decreasing source time."""
    segs: list[dict] = []
    for r in links:
        if r["status"] != LINKED:
            continue
        s = segs[-1] if segs else None
        if s and s["episode"] == r["episode"] and r["t_source"] >= s["src_end"] - p.slack_s:
            s.update(src_end=r["t_source"], query_end=r["t_query"], lines=s["lines"] + 1)
        else:
            segs.append({"episode": r["episode"], "src_start": r["t_source"], "src_end": r["t_source"],
                         "query_start": r["t_query"], "query_end": r["t_query"], "lines": 1})
    return segs
