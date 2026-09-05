# Phase 2B-3 pre-registered DINO score-contract protocol v0.1

This protocol is frozen in a protocol-only Git commit before any Phase 2B-3
DEV execution, policy selection, fresh-content selection, fresh-media generation, or
fresh held-out evaluation. Phase 2A remains frozen. Phase 2B-3 is an isolated
experimental remediation and cannot authorize production integration or a global
Phase 2B PASS.

## Question and frozen inputs

The sole question is whether one simple global monotonic score contract can translate
the validated DINOv2-small INT8 raw cosine into the frozen Temporal Alignment v2 visual
score semantics while preserving correct and competing evidence and improving safe
end-to-end segment outcomes on genuinely fresh content.

- Frozen production closure: `93bf13b`.
- Phase 2B-1 validated retrieval input: `dccec2820750126b46baabcacb14ce0caf828c7b`.
- Phase 2B-2 valid negative characterization: `4a5b7dedc2fa3189d62e6a0478cad0cddc8e90cc`.
- Source/query FPS: 2.0/2.0; top-k: 8; exact NumPy cosine.
- Backend: DINOv2-small INT8, 384-dimensional L2-normalized CLS embedding,
  fixed maximum ONNX batch size 16 with a natural short final batch.
- All source arrays, preprocessing, transformations, Temporal Alignment code and
  configuration, evaluator, candidate tiers, review tiers, DCT control and database
  schema remain frozen.
- No file under `src/ipvm/` or `config/` may change.

The experiment does not use VDF runtime, Flip TTA, FAISS, SigLIP, larger models,
learned calibration, transform/source-specific calibration, ROI/tile retrieval, PIP
repair, screen normalization, audio, ASR, Gemma, Excel or Phase 3.

## Stage discipline

The stages are irreversible:

1. repository and code audit;
2. this protocol-only commit;
3. experimental harness implementation;
4. DEV execution only;
5. freeze exactly one eligible global policy;
6. only after freeze, select fresh positions and generate fresh positive/negative media;
7. evaluate fresh held-out with DCT and the selected policy only;
8. tests, report and sanitized experiment commit;
9. stop for independent review and Architecture Owner.

After stage 5, adapter, evaluator, score grid, selection rules, candidate generation,
transformations and fresh sampling may not change. A material implementation defect
found after freeze terminates this run; it may not be repaired in-place.

## DEV corpus

All 20 previously observed Phase 2B-1/2 content positions are DEV data. They are never
called held-out again.

- Two sources, 10 old positions per source, 20 independent content positions.
- Every position has the same 11 fixed transformations, for 220 positive queries.
- The existing 20 deterministic source-biased synthetic negatives are DEV safety
  diagnostics, not an estimate of a real-world false-positive rate.
- All 220 positives and 20 negatives run through frozen production DCT.
- All eight registered DINO policies run on all DEV positives and negatives.
- The independent content unit is the position; its 11 transformation cells and its
  query frames are paired observations, not independent samples.

## Fresh held-out sampling, frozen before DEV

Fresh media must not exist before the selected policy file exists. Once it exists, the
harness creates exactly five new positions per source and all 11 transformations:
10 independent positions and 110 positive queries. Sampling is automatic; scenes are
not inspected and no difficult, repetitive, static or shared-content position may be
manually rejected.

The source interval length is 8 seconds. A legal start is initially in the closed
domain `[30, source_duration - 30 - 8]`. For every old interval
`[old_start, old_start + 8]`, remove the closed start-coordinate interval
`[old_start - 38, old_start + 38]`, clipped to the initial domain. This guarantees at
least 30 seconds between an old interval and a fresh 8-second interval. Merge touching
or overlapping removed intervals before taking the complement.

For deterministic selection:

1. sort sources by anonymous alias;
2. concatenate that source's remaining legal ranges in chronological order and measure
   their total continuous legal mass `M`;
3. split cumulative legal mass into five equal strata of mass `M/5`;
4. use one shared `random.Random(20260905)` stream in sorted-source then stratum order;
5. in each stratum draw one `u = 0.1 + 0.8 * rng.random()` and map cumulative coordinate
   `(stratum_index + u) * M/5` back to source time;
6. round each start to six decimal places and validate every rule again.

The 10% stratum guard is part of the registered algorithm. Fresh intervals within a
source must have at least 30 seconds between their 8-second spans, equivalently adjacent
starts differ by at least 38 seconds. The harness performs no retry or alternative draw.
If any source lacks five nonempty strata or any rounded output violates the domain,
old-data exclusion or fresh-fresh separation, it stops without relaxing a distance or
generating media. Anonymous committed aliases are `SOURCE_A`/`SOURCE_B` and
`FRESH_POSITION_001` through `FRESH_POSITION_005`; only normalized starts may be
committed.

## Frozen transformations

Every DEV and fresh position uses exactly these Phase 2B-1 definitions and severities:

```text
DIRECT
H264_REENCODE
MIRROR
CENTER_CROP
VERTICAL_9_16
ZOOM
LETTERBOX
SUBTITLE_OVERLAY
PIP_REDUCED_VIEWPORT
SPEED_0_8X
SPEED_1_25X
```

Subtitle remains real raster text, PIP remains a 40% viewport, and no parameter sweep
is permitted. PIP improvement is not required for promotion.

## Anchored affine score contract

For raw DINO cosine `s` and global admission anchor `tau`, every fixed top-8 candidate
is adapted as:

```text
visual_score = clip(0.82 + (s - tau) * 0.18 / (1 - tau), 0, 1)
```

No candidate is manually deleted before Temporal Alignment. The frozen aligner's
existing `visual_score >= 0.82` rule makes the effective raw admission threshold equal
to `tau`. The mapping is monotonic, maps `tau` to 0.82 and maps 1 to 1. Adapted score is
not a probability or confidence percentage.

The two interpretable identities are mandatory:

- `tau=0.82` is exactly `visual_score=s` on `[0,1]`, reproducing Phase 2B-2 RAW.
- `tau=0.64` is exactly `(s+1)/2` on `[0,1]`, reproducing production DCT's affine
  score mapping numerically.

The fixed policy grid, in registered order, is:

```text
TAU_0.55
TAU_0.60
TAU_0.64
TAU_0.68
TAU_0.72
TAU_0.76
TAU_0.80
TAU_0.82
```

No other anchor, interpolation, continuous optimization, learned mapping, or special
policy by source/transformation is allowed.

Every ignored audit record retains raw cosine, adapted visual score, original rank,
source identity, source time and accused time. Candidate identity and order are
canonicalized but membership is the same fixed top-8 for every tau; only adapted score
may differ. Ground truth is absent from retrieval and adaptation.

## Outcome and metric definitions

The frozen Phase 2A evaluator supplies four mutually exclusive outcomes:

- `CORRECT_UNAMBIGUOUS_MATCH` (C)
- `SAFE_AMBIGUITY_CONTAINS_GT` (S)
- `UNSAFE_WRONG_MATCH` (U)
- `NO_MATCH` (N)

Safe success is C+S. Contained partial evidence remains orthogonal and cannot increase
recall. For every backend/policy, report overall and by transformation: C/S/U/N, safe,
unsafe, N, status distribution, episode correctness, source and accused boundary MAE,
applicable speed MAE, candidate tier, production review tier, boundary completeness,
segment-count distribution, alternatives and ambiguities. Every rate retains numerator
and denominator. Candidate GREEN remains an internal alignment-quality diagnostic;
production review stays REVIEW_REQUIRED.

For each tau also report how many fixed top-8 candidates pass the frozen 0.82 gate,
GT-supporting frames before/after the gate, rival-source supporting frames before/after,
wrong-primary-with-safe-alternative count, wrong-primary-without-correct-alternative
count, total alternatives, ambiguities, safe, unsafe and no-match. Explicit comparisons
include TAU_0.82, TAU_0.64 and the selected tau.

## DEV negative semantics

For every tau and DCT, each known DEV negative records emitted segments, MATCH_FOUND,
AMBIGUOUS, candidate tiers, maximum support, temporal coherence and best adapted visual
score. A coherent emitted segment on any of these 20 negatives is a serious warning and
makes a DINO policy ineligible. The result remains a synthetic safety constraint only.

## DEV eligibility constraints

A DINO tau is eligible only if every constraint holds against the frozen DCT results on
the same 220 positive DEV queries and 20 DEV negatives:

1. total U is no greater than DCT total U;
2. total safe success is no less than DCT total safe success;
3. DIRECT safe is at least DCT safe minus 1/20 and DIRECT U is no greater than DCT U
   plus 1/20;
4. H264_REENCODE safe is at least DCT safe minus 1/20 and its U is no greater than DCT
   U plus 1/20;
5. each of SPEED_0_8X and SPEED_1_25X has safe success at least its DCT value minus 1/20;
6. at least two of MIRROR, CENTER_CROP and VERTICAL_9_16 improve safe success over DCT
   by at least 4/20;
7. all 20 DEV negatives produce zero coherent emitted segments;
8. the A010 rival regression below passes for DIRECT, H264_REENCODE and SPEED_0_8X.

If no tau is eligible, the experiment stops before selection freeze and before any
fresh position, positive media or negative media is created. Criteria may not be
relaxed.

## A010 rival-preservation regression

`SOURCE_A` old position ordinal 10 is a DEV-only known cross-source competition family;
it is never hard-coded as a correction rule. For each of DIRECT, H264_REENCODE and
SPEED_0_8X, the regression fails when all are true:

1. the outcome is `UNSAFE_WRONG_MATCH`;
2. the first emitted primary segment is from the wrong source;
3. no frozen-evaluator-recognized complete ground-truth mapping exists in alternatives.

An ambiguous wrong primary passes this regression only if a complete correct
alternative makes the frozen evaluator classify the case as
`SAFE_AMBIGUITY_CONTAINS_GT`. Ground truth is used only after alignment to evaluate this
DEV regression; it never removes, promotes or rewrites a candidate.

## Selection among eligible policies

Select exactly one eligible tau by the following immutable lexicographic key:

1. maximize total safe success;
2. maximize combined safe success over MIRROR, CENTER_CROP and VERTICAL_9_16;
3. minimize total U;
4. minimize total N;
5. minimize source boundary MAE;
6. minimize accused boundary MAE;
7. on an exact tie, choose the higher tau.

No held-out value exists or participates in selection.

## Freeze integrity

Before DEV starts, create an ignored immutable DEV-stage identity record containing
fingerprints of the adapter, benchmark and reporting implementation, the frozen
evaluator, production matcher, Temporal Alignment config, model public identity and
local identity, and source-index identity/provenance. DEV results bind that record.

After DEV selection, exclusively create
`workspace/phase2b3/selected-score-contract.json` with protocol commit, selected tau,
mapping version, selection metrics and reason, DEV aggregate digest/reference, batch
size, experiment/evaluator/config fingerprints, public model provenance, and local
model/index identities as needed. Raw local provenance remains ignored. Once this file
exists, DEV rerun and policy reselection are refused.

Before fresh generation and again before fresh evaluation, recompute and compare every
bound identity. A mismatch terminates the run and does not update the selected file.
Fresh raw output may contain only DCT and the selected DINO policy.

## Fresh negatives

Only after selection freeze, create exactly 10 new deterministic synthetic negatives
with IDs `FRESH_NEGATIVE_001` through `FRESH_NEGATIVE_010`. They reuse the general
eight-second `testsrc2` plus hue/grid mechanism, but parameters are derived from seed
20260905 and ordinal:

```text
hue_degrees = (20260905 mod 360 + 17 + 53 * zero_based_ordinal) mod 360
grid_pixels  = 70 + 14 * (zero_based_ordinal mod 5)
saturation   = 0.73 + 0.06 * (zero_based_ordinal mod 4)
```

The full parameter tuple must differ from all 20 old negative tuples; otherwise the
harness stops. These negatives are a safety smoke test, not a real-world FP estimate,
and cannot change the frozen tau.

## Fresh held-out promotion gate

The selected contract can only be recommended for a later, separately authorized
production-integration engineering phase if every condition holds on the 10 genuinely
fresh positions (110 positive cells) and 10 fresh synthetic negatives:

1. DINO total safe is at least DCT total safe;
2. DINO total U is no greater than DCT total U;
3. DIRECT safe is at least DCT minus 1/10 and DIRECT U no greater than DCT plus 1/10;
4. H264_REENCODE safe is at least DCT minus 1/10 and H264 U no greater than DCT plus 1/10;
5. at least two of MIRROR, CENTER_CROP and VERTICAL_9_16 improve safe success by at
   least 3/10 over DCT;
6. each speed profile has safe success at least its DCT value minus 1/10;
7. all 10 fresh negatives produce zero coherent emitted segments;
8. DINO introduces no wrong-source unambiguous candidate-GREEN case that was safe or
   NO_MATCH under DCT;
9. PIP need not improve, but a wrong-source unambiguous GREEN PIP is a safety failure.

The gate is computed as a factual report after selection; it cannot alter policy,
sampling, mapping, evaluator or data.

## Attribution and anti-leak probes

Ample correct support followed by partial or wrong boundaries is labeled
`ALIGNMENT_LIMIT` and does not authorize a Phase 2A change. Other remaining failures are
reported where supported as SCORE_CONTRACT, RETRIEVAL, TRUE_VISUAL_AMBIGUITY or
PIP_GLOBAL_EMBEDDING_LIMIT; uncertain attribution is explicit.

The experimental verification must demonstrate:

- changing GT after candidate construction leaves candidates identical;
- candidate input permutation is deterministic where the frozen contract promises it;
- removing a correct candidate band degrades coverage;
- two strong coherent cross-source lines activate existing ambiguity/rival behavior;
- one coherent wrong line may look strong but remains production REVIEW_REQUIRED;
- all tau policies preserve identical candidate identity and differ only in score;
- every mapping is monotonic;
- tau 0.64 exactly equals DCT affine mapping and tau 0.82 exactly equals identity.

## Privacy, artifacts and stopping point

Raw candidates, exact source/query timestamps, media paths, fresh media, frames,
embeddings, model, index arrays, scratch databases, private hashes, DEV-stage lock,
selected policy file and fresh raw results remain under ignored `workspace/phase2b3/`.

Committed artifacts may contain only generic experiment code/tests/docs, public model
provenance, anonymous source/position/negative aliases, normalized source positions,
configuration and aggregate metrics. Before the final commit, scan for private paths,
media identities, exact timelines, raw candidates, embeddings and private 64-character
hashes. The final report must contain all 36 requested sections and explicitly answer
Q1-Q18. The final commit contains only Phase 2B-3 files. Then enter stage 9 and stop;
independent review and Architecture Owner alone decide any next phase.
