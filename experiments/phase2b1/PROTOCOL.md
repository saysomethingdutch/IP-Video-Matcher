# Phase 2B-1 pre-registered protocol v0.1

Frozen before the full 220-positive/20-negative run.

- Baseline commit: `0b5dc6b`
- Primary independent unit: 20 content positions (2 sources × 10 positions)
- Seed: `20260904`; each source uses one random start in each equal-duration stratum
- Query/source sampling: 2 FPS; source span: 8 seconds; exact NumPy cosine top-k: 8
- Backends: frozen `dct-low-frequency@1`, `DINO_RAW`, and separate diagnostic
  `DINO_FLIP_TTA`
- DINO: 224×224 RGB bicubic input, ImageNet mean/std normalization, CLS token from
  `last_hidden_state`, 384-dimensional float32 L2-normalized output
- `DINO_FLIP_TTA`: embed original and horizontally flipped query frames; for every
  unique source frame use the larger cosine before exact top-8 ranking. Sources are
  never flipped.
- Transformations: DIRECT, H264_REENCODE, MIRROR, CENTER_CROP, VERTICAL_9_16,
  ZOOM, LETTERBOX, SUBTITLE_OVERLAY, PIP_REDUCED_VIEWPORT, SPEED_0_8X,
  SPEED_1_25X. There is one fixed configuration per transformation and no sweep.
- Subtitle overlay: fixed, actual Pillow-rasterized text on a translucent band; no
  external font file.
- Speed GT: `source_time = source_start + speed_ratio × accused_time`; expected query
  durations are 10.0 seconds at 0.8× and 6.4 seconds at 1.25×.
- Correct candidate: correct source identity and source time within the stated
  tolerance. Primary tolerance is ±1.0 second; strict diagnostic is ±0.5 second.
- Report per backend/transformation: episode top-1 accuracy, episode top-8 recall,
  source-aware recall@1/@8 at both tolerances, first-correct-rank distribution,
  position usability, true-neighborhood cosine, best-wrong cosine, and their margin.
- Primary position usability: at least 50% of query frames have a correct source-aware
  top-8 candidate within ±1.0 second. Also report the 75% and 100% cutoffs.
- Positive true score: maximum cosine among source-aware candidates within ±1.0 second.
  Best-wrong score: maximum cosine over its complement. Margin is true minus wrong.
- Negatives: 20 deterministic unrelated synthetic clips. Report top-1/top-8 cosine,
  dominant top-1 source concentration, and the largest same-source top-1 offset cluster
  inside a 2.0-second-wide window. Do not fit or imply a false-positive threshold.
- Representation guidance: no material direct/H264 regression means no more than 0.05
  absolute loss in primary source-aware recall@8 and no more than one lost usable
  position. Substantial geometry improvement means at least +0.20 primary recall@8 or
  +4/20 usable positions, on at least two of MIRROR/CENTER_CROP/VERTICAL_9_16.
  Negative separation is interpreted from the reported distributions, not a fitted
  cutoff. Mixed evidence remains `MIXED`.
- No temporal aligner, matcher, database, FAISS, FPS, top-k, or production changes.
