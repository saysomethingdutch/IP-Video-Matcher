# Phase 2B-2 candidate adapter experiment

This experiment feeds DINO_RAW candidates into the frozen Phase 2A TemporalAligner.
It does not integrate DINO into production. The protocol-only commit is
`20884b1e9ca5d81f35660bc84a7e1294897084a0`.

## Reproduce the registered experiment

Use the repository virtual environment and the Phase 2B-1 experimental ONNX Runtime
dependency. No additional dependencies are required. Existing local inputs are the
Phase 2B-1 corpus/negative manifests, DCT and DINO source indexes and the verified
public DINOv2-small INT8 model. Do not regenerate the content positions.

From the repository root, in a new ignored output directory:

```sh
.venv/bin/python -m experiments.phase2b2.benchmark calibrate \
  --output workspace/phase2b2-reproduction \
  --model /local/model/dinov2-small-int8.onnx
.venv/bin/python -m experiments.phase2b2.benchmark freeze \
  --output workspace/phase2b2-reproduction
.venv/bin/python -m experiments.phase2b2.benchmark held-out \
  --output workspace/phase2b2-reproduction \
  --model /local/model/dinov2-small-int8.onnx
.venv/bin/python -m experiments.phase2b2.reporting \
  --workspace workspace/phase2b2-reproduction \
  --output workspace/phase2b2-reproduction/aggregate.json
.venv/bin/python -m experiments.phase2b2.verify \
  --workspace workspace/phase2b2-reproduction \
  --output workspace/phase2b2-reproduction/verification.json
.venv/bin/python -m experiments.phase2b2.diagnostics \
  --workspace workspace/phase2b2-reproduction \
  --output workspace/phase2b2-reproduction/diagnostics.json
.venv/bin/python -m pytest -q
```

The default corpus/index locations are the existing ignored Phase 2B-1 workspace.
They can be supplied with `--corpus`, `--negatives` and `--index-dir` if those same
inputs are stored elsewhere. Keep their contents identical. `--previous` on the
diagnostics command identifies the prior Phase 2B-1 raw retrieval records.

The calibration command computes all seven policies on odd positions and odd
negatives only. The freeze command serializes the selected policy and code/evidence
identities. Held-out accepts only that frozen policy and even positions/negatives.
Existing outputs are never overwritten, and calibration refuses to run once a
selection file exists. A reproduction after seeing this report is not a new blinded
experiment; it checks reproducibility of the already-frozen design.

## Input and score contract

`adapter.py` consumes frame records containing rank, accused time, source time,
source alias and raw cosine. It has no GT parameter. It preserves rank identity
in the ignored audit sidecar and canonicalizes order by time/rank. Production
CandidateMatch has no rank field. Only score adaptation and below-floor removal
are performed.

RAW is clipped identity; floor f maps retained scores to `(cosine-f)/(1-f)`.
The frozen aligner still filters at 0.82. Thus floor 0.70 has an effective raw
cosine threshold of 0.946. DCT uses its production `(cosine+1)/2` mapping and full
`VideoMatcher.analyze_file` path against a scratch database populated with the
validated source arrays. The user database is not opened.

DINO query inference is always in chunks of 16, with natural final short batches.
See EXECUTION-NOTES.md for the prior 20-frame speed-query batching discrepancy.
The adapter uses existing production temporal/evaluation functions without copying
or changing them. Model inference, source arrays and candidate construction are
independent of GT; GT is applied only after alignment.

## Deliverables and limits

The committed `benchmark/visual/results/phase2b2-*.json` files are sanitized aggregates,
independent invariants and descriptive failure diagnostics. Detailed candidates,
media paths, exact timelines, code/input fingerprints and scratch DB stay ignored.
`reporting.py` validates chronology, selected policy identity and privacy. `verify.py`
independently recomputes the single-GT taxonomy. `diagnostics.py` only inspects existing
outputs; its PIP pre-filter support counts are not alternative held-out matcher runs.

The result selected FLOOR_0.70 under the registered safety-first objective. It did
not meet the held-out promotion conditions. The full report is
`docs/review/PHASE2B-2-DINO-CANDIDATE-ADAPTER-CALIBRATION-REPORT.md`.
Architecture Owner decides any remediation. No backend integration or ROI work
is authorized by this experiment's outcome.
