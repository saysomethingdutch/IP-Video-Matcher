# Phase 2A-0 retrieval characterization

This directory contains only the harness contract, JSON schema, and an anonymized empty template.

Run the harness against a complete 2 FPS index:

```bash
ipvm --root /absolute/project/root retrieval-benchmark
```

The command generates JSON and Markdown under `output/retrieval/` by default. That directory is ignored. Reports contain episode aliases such as `E001`, never original filenames, paths, SHA-256 values, or case names.

The harness measures direct off-grid queries and platform-like transcode proxies at source/query 2 FPS. The default profiles are `direct`, `p1` (H.264 720p ~2 Mbps), `p2` (H.264 480p ~1 Mbps), and `p3` (HEVC 720p ~1.5–2 Mbps); `reencode` remains available for backward compatibility. The transcode proxies change only compression, resolution, and codec—they never introduce crop, mirror, subtitles, or speed changes, and they are explicitly not claimed to reproduce Bilibili / Xiaohongshu / YouTube production pipelines.

Direct extraction seeks before input decoding and resets PTS before the 2 FPS filter so that the decoded query frames, rather than only their labels, retain the requested off-grid phase. Each episode receives a shuffled, balanced allocation across ten source-grid phase buckets so phase is not tied to source-time order.

Each profile reports:

- strict `retrieval_recall_at_k` for `k = 1/8/32/128` (exact source-grid hit);
- `alignment_usable_recall_at_k` with `±1` source-grid step tolerance (0.5 s at 2 FPS);
- `adjacent_grid_only_miss_rate_at_k` and `true_retrieval_loss_rate_at_k`;
- rank distribution, median and p90 rank, and score-margin distribution;
- `interval_count`, `distinct_content_position_count`, `nominal_query_frame_count`;
- interval-cluster `intra_cluster_correlation`, design effect, and `effective_sample_size`;
- deterministic interval-level cluster-bootstrap 95% CI;
- `threshold_band` in `ABOVE` / `STRADDLES` / `BELOW` relative to 0.95.

The primary inference unit is the sampled interval, not the individual query frame. Profiles derived from the same source interval do not increase independent sample size. Episode-level results are descriptive only while `episode_count == 2`.

It makes no architecture-route decision and does not alter matching behavior.

The full JSON report in ignored output retains frame-level observations for audit. A second aggregate JSON, CLI output, and Markdown omit those observations and expose only anonymous aggregate evidence. The harness does not claim semantic scene labels; broad time stratification supplies scene diversity without selecting samples using the retrieval representation under test.

## Readiness gate metric

The readiness gate for entering Temporal Alignment v2 is `alignment_usable_recall@8`
(ADR-0005), judged by the lower bound of its own interval-level cluster bootstrap 95% CI.
`strict_retrieval_recall@k` is retained as a localization diagnostic and does not gate.

Every profile reports two independently-computed bands. The bare `threshold_band` field is
removed because it did not name its metric:

```text
strict_threshold_band             <- from the strict recall@8 CI
alignment_usable_threshold_band   <- from the alignment-usable recall@8 CI (gate)
```

Bands are four-state: `ABOVE` / `STRADDLES` / `BELOW` / `UNKNOWN`. `UNKNOWN` means the
bootstrap produced no interval; it must never be read as `STRADDLES`.

ICC, design effect, effective sample size and confidence intervals are modelled separately
for each metric under `profiles.<profile>.strict` and `profiles.<profile>.alignment_usable`.
No bare cluster-statistics field is emitted.

`source_grid_period_seconds` is derived as `1.0 / matching.coarse_fps`. The statistical
implementation contains no bare `0.5` literal.

## Interpretation limit

The transcode proxies vary **compression, resolution and codec only**. Measured against the
current low-frequency DCT descriptor, those perturbations produce `1 - cos` of roughly
`6e-6` to `1.3e-5`, two to three orders of magnitude below the median score margin between
the correct and best-incorrect frame (about `3.7e-3`). Even at 180p / 60 kbps the
perturbation stays about an order of magnitude below that margin.

The correct reading is therefore:

> Compression, resolution and codec perturbations fall below the main sensitivity range of
> the current low-frequency DCT descriptor.

These results **must not** be generalized to crop, mirror, subtitle overlay,
picture-in-picture, camera capture, or Reaction-style composition. Those transformations
change low-frequency structure and remain unmeasured.

Transcode profiles are pinned to single-threaded, frame-parallel-free encoder settings so a
re-run reproduces identical bytes. Multi-threaded x264/x265 is not bit-deterministic.
