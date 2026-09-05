# Phase 2B-3 score-contract experiment

This isolated experiment asks whether one global anchored-affine score mapping can
feed the already validated DINOv2-small INT8 top-8 retrieval evidence into the frozen
Phase 2A Temporal Alignment v2. It does not change or integrate production code.

The binding pre-registration is `PROTOCOL.md`, committed alone at
`277dea444545b5555f6bc4813334c6528df8a4cb`. The old 20 positions are DEV data. Fresh
content selection and media generation are mechanically forbidden until a single
eligible policy has been frozen in ignored workspace output.

## Score contract

For raw cosine `s` and registered anchor `tau`:

```text
visual_score = clip(0.82 + (s - tau) * 0.18 / (1 - tau), 0, 1)
```

All fixed top-8 candidates are retained by the adapter. The frozen aligner applies its
existing `visual_score >= 0.82` admission rule, so the effective raw threshold is
`tau`. The adapted score is not a probability. Candidate GREEN is only an internal
alignment-quality diagnostic; production review remains `REVIEW_REQUIRED`.

## Reproduction stages

Use the repository virtual environment, the already verified local DINOv2-small INT8
model, and the Phase 2B-1 ignored corpus/index inputs. Use a new ignored output folder
when reproducing an already disclosed experiment; such a reproduction is not a new
blind held-out result.

```sh
.venv/bin/python -m experiments.phase2b3.benchmark dev \
  --output workspace/phase2b3-reproduction \
  --model /local/model/dinov2-small-int8.onnx

.venv/bin/python -m experiments.phase2b3.benchmark freeze \
  --output workspace/phase2b3-reproduction \
  --model /local/model/dinov2-small-int8.onnx

.venv/bin/python -m experiments.phase2b3.benchmark make-fresh \
  --output workspace/phase2b3-reproduction \
  --model /local/model/dinov2-small-int8.onnx

.venv/bin/python -m experiments.phase2b3.benchmark held-out \
  --output workspace/phase2b3-reproduction \
  --model /local/model/dinov2-small-int8.onnx

.venv/bin/python -m experiments.phase2b3.reporting \
  --workspace workspace/phase2b3-reproduction \
  --output workspace/phase2b3-reproduction/aggregate.json

.venv/bin/python -m experiments.phase2b3.verify \
  --workspace workspace/phase2b3-reproduction \
  --output workspace/phase2b3-reproduction/verification.json

.venv/bin/python -m pytest -q
```

The DEV command evaluates DCT plus all eight registered policies. `freeze` refuses to
continue if none satisfies every pre-registered eligibility constraint. `make-fresh`
is unavailable before freeze and applies the registered seed, exclusion and spacing
rules without scene inspection. Held-out executes only DCT and the one frozen policy.
Existing raw results are never overwritten.

Raw media, candidates, exact timelines, model/index identities, selection file and
other local provenance remain under ignored `workspace/phase2b3/`. Only sanitized
aggregate metrics, experimental code, tests and the final report may enter Git.

## Published run outcome

The registered run found no eligible policy: every tau failed the A010 H.264 rival
constraint, and several failed additional safety or H.264 constraints. It stopped after
DEV as required. Consequently, the published run has no selected tau and no fresh
held-out media or metrics. The `freeze` command is expected to refuse this run rather
than relaxing eligibility.
