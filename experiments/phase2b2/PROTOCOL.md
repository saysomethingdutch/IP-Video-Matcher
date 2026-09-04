# Phase 2B-2 pre-registered adapter calibration protocol v0.1

This protocol is frozen in a protocol-only Git commit before any Phase 2B-2
calibration outcome or held-out end-to-end outcome is computed. Phase 2A remains
frozen; this is an experimental characterization, not production integration.

## Question and frozen baseline

- Question: can validated `DINO_RAW` top-8 frame candidates pass through the frozen
  Temporal Alignment v2 and produce safer, materially better source segments than the
  frozen DCT control?
- Production closure: `93bf13b`; Phase 2B-1 input: `dccec2820750126b46baabcacb14ce0caf828c7b`.
- No file under `src/ipvm/` or `config/` may be modified.
- Frozen source/query FPS: 2.0/2.0; top-k: 8; DINO model:
  `dinov2-small-int8`; exact NumPy cosine retrieval; DINO inference batch size: 16.
- Temporal Alignment configuration and all matching/evaluator semantics are frozen.
  The adapter calls the existing `TemporalAligner.align_with_diagnostics(...,
  index_complete=True)` entry point with production `CandidateMatch` objects.

## Candidate and score contract

Every Phase 2B-1 candidate has:

```text
(accused_time, source_time, source_id, raw_cosine, rank)
```

The adapter preserves all five fields in ignored experimental audit output. It never
changes candidate timestamps, source identity, rank identity, or top-k membership using
ground truth. The production `CandidateMatch` passed to Temporal Alignment contains the
same accused time, source id and source time plus the adapted visual score. Rank has no
field in `CandidateMatch` and is not consumed by the frozen aligner, so it remains in the
sidecar audit record.

The code audit found that visual score is used both absolutely and relatively:

- candidates below production `minimum_visual_similarity=0.82` are removed;
- remaining scores weight sparse slope/offset votes;
- their inlier mean contributes to match score and bounded explanation quality;
- explanation quality affects winner/rival ordering and ambiguity margin;
- score-derived segment quality contributes to diagnostic candidate tier.

The frozen DCT control reconstructs production score semantics from the Phase 2B-1 raw
cosine as `clip((raw_cosine + 1) / 2, 0, 1)`. Candidate identity remains the exact shared
top-8 set. Temporal Alignment does not consume retrieval rank.

The primary DINO backend is `DINO_RAW`. For `RAW`, the adapted visual score is
`clip(raw_cosine, 0, 1)`, which is identity on valid [0,1] DINO candidates. For a global
floor `f`, candidates with `raw_cosine < f` are excluded; retained candidates receive:

```text
calibrated_score = clip((raw_cosine - f) / (1 - f), 0, 1)
```

Raw cosine is always retained in ignored audit output. This mapping is score-scale
adaptation, not probability calibration.

## Frozen policy grid

The calibration policies, in fixed order, are:

```text
RAW
FLOOR_0.45
FLOOR_0.50
FLOOR_0.55
FLOOR_0.60
FLOOR_0.65
FLOOR_0.70
```

There is no continuous optimization, transform-specific floor, source-specific floor,
case-specific floor, neural/statistical classifier, retrieval retuning, or TTA default.

## Deterministic grouped split

Reuse the exact Phase 2B-1 corpus, positions, query media, source indexes and raw
candidate records. Do not generate or substitute positions after observing outcomes.
The independent unit is content position, not query frame or transformation cell.

- Calibration positives: ordinals 1, 3, 5, 7, 9 from each source = 10 positions =
  110 queries.
- Held-out positives: ordinals 2, 4, 6, 8, 10 from each source = 10 positions =
  110 queries.
- All 11 transformations from one position remain in the same split.
- Calibration negatives: odd negative ordinals = 10.
- Held-out negatives: even negative ordinals = 10.

The synthetic negatives are source-biased safety constraints, not a representative
real-world negative distribution and not the sole basis for policy selection.

## Ground truth and end-to-end evaluation

For each positive query, the pre-existing mapping is:

```text
source_time = source_start + speed_ratio * accused_time
```

Ground truth is unavailable to candidate construction and adapter application. It enters
only the frozen Phase 2A evaluator after alignment. The outcome taxonomy is:

- `CORRECT_UNAMBIGUOUS_MATCH`
- `SAFE_AMBIGUITY_CONTAINS_GT`
- `UNSAFE_WRONG_MATCH`
- `NO_MATCH`

Safe source-aware success is the sum of the first two categories, subject to the
existing full-mapping evaluator. Explicit contained partial evidence remains orthogonal
and never raises recall.

For calibration and held-out separately, report each backend/policy by transformation:
query count; all four taxonomy counts; final status distribution; safe success; unsafe
wrong-match rate; episode correctness; source/accused boundary MAE; applicable speed
MAE; boundary completeness; candidate tier; production review tier; warnings; and
segment count distribution. Every percentage carries numerator and denominator.

For every floor also report retained candidate fraction, raw cosine distributions of
retained/dropped candidates, ambiguity rate, `NOT_FOUND` rate, calibration negative
alignment behavior and boundary errors.

## Calibration selection rule

Choose at most one policy using calibration data only, lexicographically:

1. Safety: minimize `UNSAFE_WRONG_MATCH` and negative false alignment. A policy is
   ineligible if it produces a clearly unsafe/confident wrong-source negative segment or
   materially increases unsafe positive outcomes versus frozen DCT.
2. Coverage: among eligible policies, maximize safe source-aware success.
3. Boundary quality: prefer lower source then accused boundary MAE.
4. Simplicity: if materially tied, prefer `RAW`, then the lowest-complexity/lowest floor
   in the fixed grid.

For deterministic implementation, a strict lexicographic key uses integer unsafe counts
and safe-success counts first. Boundary MAE is compared at the saved numeric precision;
exact ties use the registered policy order above. No held-out statistic is an input.

The selected policy and calibration evidence reference must be serialized to the ignored
`workspace/phase2b2/selected-adapter.json` before held-out evaluation. That file records
the protocol commit hash, selected policy/floor, selection reason and a calibration
metrics digest without private media identifiers. Only then may the held-out split run.

## Held-out promotion facts

The frozen selected DINO adapter may be recommended for later production-integration
engineering only when held-out evidence shows all of the following:

- no material DIRECT/H264 end-to-end safety regression versus DCT;
- source-aware safe segment improvement on at least two of MIRROR, CENTER_CROP and
  VERTICAL_9_16;
- strong 0.8x and 1.25x source-aware behavior;
- no material increase in unsafe wrong matches, with every increase inspected;
- no confidently accepted coherent alignment on held-out unrelated negatives;
- PIP need not pass, but must not become deceptively confident.

Results are reported as facts for Architecture Owner. They do not self-authorize the next
phase, production integration, or Phase 2B PASS.

## Feed-through and anti-leak invariants

The experiment will verify:

1. identical candidate input twice gives identical alignment output;
2. changing only GT after candidate generation leaves the candidate stream unchanged;
3. permuting input order while preserving candidate identity/score/rank leaves output
   deterministic where the frozen contract is order-independent;
4. removing a known correct candidate band degrades coverage rather than improving it;
5. injected coherent wrong-source candidates activate ambiguity/safety behavior rather
   than silently certifying the answer.

It will also verify timestamp/source identity preservation, monotonic-order preservation,
top-k/cap behavior, out-of-range speed handling, boundary completeness and ambiguity
semantics. Shared-opening cross-episode candidates are legitimate visual ambiguity and
will not be suppressed using GT.

## Negative, PIP and safety interpretation

All 20 negatives pass through the same adapter and frozen aligner. The concern is an
emitted coherent false temporal structure, not nearest-neighbor existence. Per negative,
record emitted segments, status, tiers, best visual score, support, temporal coherence,
source concentration, ambiguity and warnings.

PIP is feed-through characterization only. Report correct/competing coherent lines,
taxonomy, ambiguity, boundary completeness and tiers. No ROI/tile repair is allowed.

Production auto-release remains disabled. `candidate_tier=GREEN` is internal diagnostic
only; all production review tiers remain `REVIEW_REQUIRED` for emitted matches. No output
is an automatic legal or infringement conclusion.

## Artifacts, tests and privacy

Raw candidates, exact timestamps, source paths, media, frames, embeddings, model files,
scratch databases and private hashes remain under ignored `workspace/phase2b2/` (or the
existing ignored Phase 2B-1 workspace). Committed outputs use only `SOURCE_A`, `SOURCE_B`,
`POSITION_001`-style aliases, normalized positions, configuration and aggregates.

Focused tests will cover candidate identity/time preservation, RAW/floor mapping and
clipping, below-floor removal, fixed batch metadata, grouped split balance/disjointness,
selection isolation from held-out data, deterministic selection, taxonomy aggregation,
negative semantics, sanitizer behavior and protocol commit metadata. Full repository
pytest must remain green.

The final report will answer the 14 Architecture Owner questions, identify all comparison
differences, report runtime and known limitations, and stop without changing production.
