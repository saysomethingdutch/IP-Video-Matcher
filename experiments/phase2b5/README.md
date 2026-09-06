# Phase 2B-5 extraction compatibility remediation

Phase 2B-5 is a synthetic-plus-existing-DEV experiment over the frozen Phase 2A
temporal aligner. It tests exactly two extraction seams: a q-max-relative near-tie
geometry shortlist and exact-inlier-only occupancy. It does not alter production,
retrieval, fitting, merging, change-point logic, or review-tier behavior.

The binding pre-registration is `PROTOCOL.md`, committed alone at `54531ce7`. Seven
fixed policies (`P0` through `P6`) are evaluated. `P0` must exactly replay the frozen
production extraction; no fresh or held-out input is permitted.

## Reproduce

Run from the repository root with the existing virtual environment. Outputs under
`workspace/phase2b5/` are ignored, contain detailed candidate traces, and are created
exclusively rather than overwritten.

```sh
.venv/bin/python -m experiments.phase2b5.benchmark synthetic \
  --output workspace/phase2b5/synthetic.json

.venv/bin/python -m experiments.phase2b5.benchmark dev \
  --input workspace/phase2b3/dev.json \
  --synthetic workspace/phase2b5/synthetic.json \
  --output workspace/phase2b5/dev.json \
  --selected workspace/phase2b5/selected-experimental-policy.json

.venv/bin/python -m experiments.phase2b5.reporting \
  --dev workspace/phase2b5/dev.json \
  --synthetic workspace/phase2b5/synthetic.json \
  --output benchmark/visual/results/phase2b5-extraction-remediation-v0.1.json

.venv/bin/python -m experiments.phase2b5.verify \
  --raw workspace/phase2b5/dev.json \
  --synthetic workspace/phase2b5/synthetic.json \
  --aggregate benchmark/visual/results/phase2b5-extraction-remediation-v0.1.json \
  --dev workspace/phase2b3/dev.json \
  --selected workspace/phase2b5/selected-experimental-policy.json \
  --output benchmark/visual/results/phase2b5-verification-v0.1.json

.venv/bin/pytest -q
```

## Interpretation boundary

The 220 positive transformation cells per backend are nested within 20 DEV content
positions. They are neither independent samples nor held-out evidence. A selected
experimental policy would remain non-production and review-required; if no policy
passes every pre-registered gate, no selected artifact is created. Phase 2B-5 does
not authorize fresh validation or any production change.
