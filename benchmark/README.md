# Synthetic Benchmark

The generator creates deterministic FFmpeg cases and a machine-readable `ground_truth.json` shared with `input/golden/`:

| Case | Transformation | Current gate |
|---|---|---|
| A | off-grid direct cut/re-encode | Phase 1 |
| B | horizontal mirror | Phase 2 diagnostic |
| C | 1.25× speed | Phase 2 diagnostic |
| D | 0.8× speed | Phase 2 diagnostic |
| E | center crop | Phase 2 diagnostic |
| F | vertical crop | Phase 2 diagnostic |
| G | subtitle-style overlay | Phase 2 diagnostic |
| H | mirror + speed + overlay | Phase 2 diagnostic |
| I | clips from multiple episodes | Phase 1 |
| J | unrelated content inserted | Phase 2 diagnostic |
| K | pure negative | Phase 1.5 safety gate |
| L | exact duplicate cross-episode | Phase 1.5 safety gate |
| M | incomplete faststart source index | Phase 1.5 safety gate |

The suite also generates twenty additional off-grid direct/re-encode clips for the Phase 1 regression gate.

Run the self-contained gate with:

```bash
python -m ipvm benchmark --demo
```

Run non-gating Phase 2 diagnostics with:

```bash
python -m ipvm benchmark --demo --all-diagnostic
```

Generated media stays under `workspace/`; JSON reports go to `output/reports/`. Neither directory is tracked by Git.

The checked-in gate report is written directly by the benchmark runner to `benchmark/results/phase1_5_gate.json`; it is not manually transcribed.
