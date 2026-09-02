# Phase 2A-0 retrieval characterization

This directory contains only the harness contract, JSON schema, and an anonymized empty template.

Run the harness against a complete 2 FPS index:

```bash
ipvm --root /absolute/project/root retrieval-benchmark
```

The command generates JSON and Markdown under `output/retrieval/` by default. That directory is ignored. Reports contain episode aliases such as `E001`, never original filenames, paths, SHA-256 values, or case names.

The harness measures direct and ordinary re-encoded off-grid queries at source/query 2 FPS. Direct extraction seeks before input decoding and resets PTS before the 2 FPS filter so that the decoded query frames, rather than only their labels, retain the requested off-grid phase. Each episode receives a shuffled, balanced allocation across ten source-grid phase buckets so phase is not tied to source-time order. It reports recall@1/8/32/128, rank distribution, median and p90 rank, score margin, temporal phase, episode alias, source-position quintile, and anonymous interval aggregates. It makes no architecture-route decision and does not alter matching behavior.

The full JSON report in ignored output retains frame-level observations for audit. A second aggregate JSON, CLI output, and Markdown omit those observations and expose only anonymous aggregate evidence. The harness does not claim semantic scene labels; broad time stratification supplies scene diversity without selecting samples using the retrieval representation under test.
