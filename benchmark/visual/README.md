# Visual backend characterization results

This directory contains privacy-safe aggregate outputs from experimental visual
backend evaluations. Raw media, exact source timestamps, local paths, hashes,
embeddings, backend databases, and per-frame outputs remain under the ignored
`workspace/` tree.

`phase2b0-visual-backend-pilot-v0.1.json` is a valid pilot result, not a full
bake-off. Its `experiment_status` and `stop_reason` fields record that the
pre-registered full matrix was not run after the VDF query-to-source interface
failed the pilot gate. Rates retain their numerators and denominators, and the
four source positions—not transformed copies—are the independent content units.
