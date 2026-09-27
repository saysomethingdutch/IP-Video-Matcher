"""Incremental index build: OCR only new/changed episodes, re-derive entries, recompute flags and stats."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from . import ocr, store
from .merge import MERGE_VERSION, merge_frames


def build(episodes_dir: Path, work_dir: Path, bin_dir: Path, boundaries_path: Path | None,
          workers: int = 4, force: bool = False, log=print, lyrics_path: Path | None = None) -> dict:
    t0 = time.monotonic()
    found = ocr.find_episodes(episodes_dir)
    if not found:
        raise SystemExit(f"no episode files (EP01.ts, qsjx_ep01.ts, ... .ts/.mp4/.mkv/.mov) in {episodes_dir}")
    con = store.connect(work_dir / "index.sqlite")
    known = store.episode_rows(con)
    boundaries = store.load_boundaries(boundaries_path)

    # 1) OCR only episodes without a valid raw OCR cache (new or changed files)
    prints = {ep: ocr.fingerprint(video) for ep, video in found.items()}
    jobs = [ocr.EpisodeJob(ep, video, work_dir / "ocr" / f"EP{ep:02d}.jsonl.gz", prints[ep])
            for ep, video in found.items()
            if force or not ocr.cache_is_valid(work_dir / "ocr" / f"EP{ep:02d}.jsonl.gz", prints[ep])]
    if jobs:
        log(f"OCR {len(jobs)} episode(s): {', '.join(f'EP{j.episode:02d}' for j in jobs)} ({workers} processes)")
        ocr.run_ocr(jobs, ocr.ensure_binary(bin_dir), work_dir / "tmp", workers, log)
    t_ocr = time.monotonic() - t0
    failed = {j.episode: j.failed for j in jobs if j.failed}

    # 2) (re)derive entries where the cache or the merge rules changed since the last build
    merged, kept = [], []
    params = json.dumps(ocr.ocr_params(), sort_keys=True)
    for ep in sorted(found):
        cache = work_dir / "ocr" / f"EP{ep:02d}.jsonl.gz"
        if ep not in failed and not ocr.cache_is_valid(cache, prints[ep]):
            failed[ep] = "no valid OCR cache after OCR"
        if ep in failed:
            continue
        row = known.get(ep)
        if (not force and row is not None and row["fingerprint"] == prints[ep]
                and row["merge_version"] == MERGE_VERSION and row["ocr_params"] == params):
            kept.append(ep)
            continue
        header, frames = ocr.read_cache(cache)
        entries = merge_frames(ep, frames, header["params"]["fps"])
        bounds = store.episode_bounds(boundaries, ep, header["duration"])
        store.replace_episode(con, header, found[ep].name, bounds, entries, MERGE_VERSION)
        merged.append(ep)
        log(f"EP{ep:02d}: {len(entries)} entries")

    # 3) flags depend on all episodes, so they are recomputed every time (cheap)
    lyrics_path = lyrics_path or store.LYRICS_DIR / f"{work_dir.name}.json"
    lyric_ranges = store.load_lyric_ranges(lyrics_path)
    store.recompute_flags(con, lyric_ranges)
    stats = store.compute_stats(con)
    stats = {
        "work_id": work_dir.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": {**ocr.ocr_params(), "merge_version": MERGE_VERSION, "ocr_workers": workers,
                   "lyric_ranges": lyric_ranges},
        "last_build": {
            "wall_s": round(time.monotonic() - t0, 1),
            "ocr_wall_s": round(t_ocr, 1),
            "ocr_episodes": [j.episode for j in jobs if not j.failed],
            "remerged_episodes": merged,
            "unchanged_episodes": kept,
            "failed": failed,
        },
        **stats,
    }
    (work_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    con.close()
    return stats
