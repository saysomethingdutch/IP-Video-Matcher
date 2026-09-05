# Phase 2B-4 temporal-separability characterization

This isolated DEV-only experiment explains where a correct-source candidate cloud can
lose an evaluator-complete mapping inside the frozen Phase 2A Temporal Alignment
pipeline. It records diagnostics; it does not implement a matcher, tune a score
threshold, select a DINO policy, or change production behavior.

The binding pre-registration is `PROTOCOL.md`. It was committed alone at `59a834a`,
then received a protocol-only fixture-identity correction at
`79decff3270c028056f34fdd11902545fad0b74a` before any result was generated. The
correction moved a synthetic plateau identity away from the registered true-line
identity; it did not change a parameter grid, metric, mechanism flag, or decision
rule.

## Inputs and outputs

The harness reads only the existing Phase 2B-3 DEV record at
`workspace/phase2b3/dev.json`. Detailed candidate membership, exact nominal-grid
timestamps, and stage traces are written below ignored `workspace/phase2b4/`. The
committed result contains only aliases, normalized positions, aggregate metrics,
errors, slopes, and public Git provenance.

The primary diagnostic DINO score reference is `TAU_0.72`. It remains diagnostic-only.
`TAU_0.68` and `TAU_0.82` are used only for the pre-registered A010 invariance check.

## Reproduce the characterization

Run from the repository root with the existing virtual environment:

```sh
.venv/bin/python -m experiments.phase2b4.benchmark \
  --dev workspace/phase2b3/dev.json \
  --output workspace/phase2b4/raw.json

.venv/bin/python -m experiments.phase2b4.reporting \
  --input workspace/phase2b4/raw.json \
  --output benchmark/visual/results/phase2b4-temporal-separability-v0.1.json

.venv/bin/python -m experiments.phase2b4.verify \
  --raw workspace/phase2b4/raw.json \
  --aggregate benchmark/visual/results/phase2b4-temporal-separability-v0.1.json \
  --output benchmark/visual/results/phase2b4-verification-v0.1.json

.venv/bin/pytest -q
```

Raw outputs are created exclusively: an existing raw file is never overwritten.
Choose a new ignored output filename for a repeat run. The benchmark refuses to run
if the protocol changed, production differs from `93bf13b`, or a Phase 2B-3 selected,
fresh, or held-out artifact is present.

## Interpretation boundary

The 220 positive transformation cells come from 20 DEV content positions in two
anonymous sources. Frames, candidates, backends, and transformations are not
independent observations. The result is descriptive evidence about this DEV set, not
a held-out estimate or authorization to change production. Candidate GREEN remains an
internal alignment-quality label; every production review tier remains
`REVIEW_REQUIRED`.
