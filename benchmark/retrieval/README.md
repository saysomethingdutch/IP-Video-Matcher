# Phase 2A-0 retrieval characterization

This directory contains only the harness contract, JSON schema, and an anonymized empty template.

Run the harness against a complete 2 FPS index:

```bash
ipvm --root /absolute/project/root retrieval-benchmark
```

The command generates JSON and Markdown under `output/retrieval/` by default. That directory is ignored. Reports contain episode aliases such as `E001`, never original filenames, paths, SHA-256 values, or case names.

The harness measures direct and ordinary re-encoded off-grid queries at source/query 2 FPS. It reports recall@1/8/32/128, rank distribution, median and p90 rank, score margin, temporal phase, episode alias, and query timestamp. It makes no architecture-route decision and does not alter matching behavior.
