# Phase 2B-5 pre-registered near-tie extraction remediation protocol v0.1

This protocol is committed alone before any Phase 2B-5 executable code, synthetic
result, or DEV result exists. Phase 2B-5 is an experimental remediation against the
frozen Phase 2B-4 diagnosis. It does not modify or promote production behavior.

## Question, provenance, and fixed scope

The sole question is whether a small, GT-free, backend-neutral change to proposal
selection and/or candidate occupancy allows already-generated evaluator-complete
affine proposals to survive extraction without degrading end-to-end safety.

- Starting commit: `fb0e6066a6640e772d295f73ba3d0f7234e64a3e`.
- Frozen production closure: `93bf13b`.
- Phase 2B-4 protocol commits: `59a834ae1a567db3321031e8fc0f7114331fbbe5`
  and `79decff3270c028056f34fdd11902545fad0b74a`.
- Existing Phase 2B-3 DEV candidate audit is the only real-candidate input.
- DINO `TAU_0.72` is a diagnostic reference, not a selected production policy.
- DCT is the paired backend-neutral non-regression control.
- Source/query sampling remains 2 FPS, retrieval remains exact top-k 8, and the
  aligner admission threshold remains 0.82.

No file under `src/ipvm/` or `config/` may change. Sparse voting, peak/support/run
generation, robust fitting, the evaluator, merge, change-point logic, rival and
ambiguity semantics, boundary completeness, candidate/review tiers, DINO, DCT, FPS,
top-k, and the score contract remain frozen. There is no DTW, Viterbi, dynamic
programming, sequence model, new encoder, FAISS, ROI/tile, PIP repair, learned ranking,
classifier, neural reranker, audio, ASR, Gemma, Excel, or Phase 3 work.

No fresh or held-out data may be selected, generated, decoded, inspected, or run. The
Phase 2B-3 `make-fresh` and `held-out` commands are forbidden, and no
`selected-score-contract.json` may be created. A selected experimental extraction
policy, if any, is DEV-derived and does not authorize production or fresh validation.

## Irreversible execution stages

1. Audit repository state, frozen extraction, Phase 2B-4 evidence, ignored inputs, and
   the 205-test baseline.
2. Commit only this protocol and verify the commit's one-file tree.
3. Implement the registered synthetic reproducer and safety fixtures.
4. Implement only the seven registered experimental policies.
5. Run the synthetic matrix and determine synthetic eligibility.
6. Run all seven policies on existing DINO and paired DCT DEV, including existing DEV
   synthetic negatives.
7. Apply the registered eligibility gates and select at most one policy.
8. Produce ignored detailed traces, sanitized artifacts, the required report and
   README; run tests and verification; commit only explicit Phase 2B-5 files.
9. Stop for independent review and Architecture Owner decision.

There is no route from a later result back to this protocol. A material protocol bug
found after results requires stopping rather than amending the rules.

## Experimental seam and baseline

The experiment subclasses `TemporalAligner` and replaces only
`_extract_episode_segments`, because production exposes no injection seam between fit
generation, fit selection, and occupancy. Every line outside the two registered seams
must preserve the frozen method's behavior. All peak, support, run, fit, merge, and
change-point work delegates to inherited production helpers.

`P0` is the exact baseline: frozen lexicographic `_fit_quality` selection and frozen
`LINE_BAND` occupancy. P0 output must equal direct production output at the JSON
persistence boundary on every registered DEV case and synthetic fixture. Failure of
this replay invariant invalidates the experiment.

The experimental aligner records candidate-ordinal membership and round transitions
without using GT. GT and the frozen evaluator are applied only after the raw mapping
has been returned.

## Mechanism A: q_max-relative near-tie selection

For each extraction round, let `q(p)` be inherited `_explanation_quality` and

```text
q_max = max(q(p) for p in proposals).
```

For a registered epsilon, the inclusive shortlist is

```text
S = {p : q(p) >= q_max - epsilon}.
```

The shortlist is relative to the single round maximum, never pairwise; no
non-transitive epsilon comparator is permitted. Select the maximum tuple:

```text
(
  inlier_ratio,
  -residual_rms_seconds,
  accused_span_seconds,
  inlier_count,
  explanation_quality,
  -original_proposal_ordinal
)
```

The final field preserves the existing deterministic first-generated tie break. The
selector has no episode-correctness, truth, expected slope/speed, boundary, or evaluator
input. It must behave identically for legitimate 0.8x, 1.0x, and 1.25x lines.

Registered epsilon values are exactly `0.0005`, `0.0010`, and `0.0020`. No larger,
intermediate, continuous, per-transform, or result-dependent value may be evaluated.

## Mechanism B: occupancy

`LINE_BAND` is the frozen rule. It claims every accused-frame ordinal represented by a
selected fit's inliers, obtains the selected fitted line exactly as production does,
and removes every remaining candidate on a claimed frame whose source timestamp is
within the inherited candidate-timestamp tolerance of that line.

`INLIER_ONLY` removes only the exact candidate objects in the selected fit's inlier
list. Each input candidate receives a stable original-stream ordinal. Runtime removal
uses object identity backed by that ordinal, so two candidates with otherwise equal
timestamp/score fields remain distinguishable. It does not remove another candidate
merely because episode, timestamp, accused frame, visual score, or rank is equal.

Neither mode changes minimum support, fit tolerance, peak/support/run generation, or
robust fitting.

## Seven-policy matrix

Exactly these policies are run, in this order:

| Policy | Selection | Epsilon | Occupancy |
|---|---|---:|---|
| P0 | frozen baseline | null | LINE_BAND |
| P1 | near-tie geometry | 0.0005 | LINE_BAND |
| P2 | near-tie geometry | 0.0010 | LINE_BAND |
| P3 | near-tie geometry | 0.0020 | LINE_BAND |
| P4 | frozen baseline | null | INLIER_ONLY |
| P5 | near-tie geometry | 0.0010 | INLIER_ONLY |
| P6 | near-tie geometry | 0.0020 | INLIER_ONLY |

There is no 0.0005 + INLIER_ONLY policy and no eighth policy.

## Dedicated synthetic extraction-loss reproducer

The fixture is named `EXTRACTION_LOSS`. It is deterministic, anonymous, and contains
16 accused frames `q_i = i/2` for `i=0..15`. GT is one segment on episode `TRUE`:

```text
accused [0, 8]
source [100, 108]
speed_ratio 1.0
```

Each frame has exactly top-k 8 candidates. All source timestamps are on the 0.5-second
grid. Candidate order within a frame is descending registered score, then construction
ordinal.

1. Correct line: episode `TRUE`, source `100 + q_i`, score `0.9524`.
2. Compressed step-line: episode `TRUE`, score `0.9530`, with source offset from the
   correct line equal to `1.5` seconds for frames 0-3, `1.0` seconds for frames 4-7,
   and `0.5` seconds for frames 8-15. Thus every point is grid-aligned, the path has a
   lower global fitted slope, and it never duplicates the correct identity within a
   frame.
3. Six fillers: one single-use episode per `(frame, slot)`, grid-aligned source
   `300 + 10*slot + q_i`, score `0.70`. Each filler episode occurs once and cannot
   meet minimum support.

The fixture is successful only if, under P0, at least one evaluator-complete proposal
exists before the first selection, the slightly higher-quality compressed proposal is
selected, and no evaluator-complete final primary or alternative remains. The quality
gap must lie in `[0.0005, 0.0010]`. If these conditions do not hold without changing
the fixture or production helpers, the phase stops before DEV.

## Synthetic safety fixtures

All fixtures use 2 FPS, 16 frames, top-k 8, 0.5-second-grid source candidates, and
single-use score-0.70 filler episodes as above unless stated otherwise. Fillers are
added to reach exactly eight candidates per frame: seven with one active candidate and
six with two active candidates. For speed `a`,
the active correct candidate is episode `TRUE`, score 0.96, at the nearest half-second
grid timestamp to `100 + a*q_i`; GT is accused `[0,8]`, source `[100,100+8a]`, speed
`a`. NumPy half-to-even rounding is used.

- `CLEAN_1_0X`, `CLEAN_0_8X`, `CLEAN_1_25X`: one active correct line at the named
  speed.
- `TWO_STRONG_RIVALS`: add a slope-1 episode `RIVAL` at `200+q_i`, score 0.96, to the
  clean 1x fixture. The final result must remain ambiguous and retain at least two
  incompatible mappings.
- `COHERENT_WRONG_LINE`: only the episode `RIVAL` slope-1 line at `200+q_i`, score
  0.96, while GT remains the absent `TRUE` line. Any emitted mapping must retain
  `review_tier=REVIEW_REQUIRED`.
- `PARALLEL_COMPETITOR`: clean 1x plus episode `RIVAL` at `200+q_i`, score 0.955.
- `DUPLICATE_TIMESTAMPS`: clean 1x plus a same-episode plateau at source 120.0,
  score 0.94, on evenly distributed frames `0,2,4,6,8,10,12,14`.
- `LOCAL_PLATEAU`: clean 1x plus the same plateau candidate on centered frames 4-11.

Identity uniqueness within a frame is mandatory.

## Synthetic gate

P0 must reproduce the dedicated extraction loss before any policy can reach DEV.
Every non-baseline policy is reported. A policy is synthetic-eligible only if all hold:

1. It restores at least one evaluator-complete final primary or alternative on
   `EXTRACTION_LOSS`.
2. The 1.0x, 0.8x, and 1.25x clean fixtures each retain an evaluator-complete mapping.
3. `TWO_STRONG_RIVALS` remains ambiguous with at least two incompatible mappings;
   selecting one wrong line without ambiguity fails.
4. Every emitted mapping on `COHERENT_WRONG_LINE` is `REVIEW_REQUIRED`.
5. On every non-loss fixture, its primary+alternative mapping count is no more than
   twice P0's count for that fixture.

Policies failing this gate are not DEV-eligible candidates. They are nevertheless run
on DEV only because the required full seven-policy matrix is itself pre-registered;
they can never enter final eligibility or selection.

## Existing DEV population and replay

The DEV input contains 20 observed content positions x 11 transformations = 220
positive cells and 20 existing synthetic-negative controls per backend. DINO uses only
TAU_0.72; DCT uses its stored visual-score stream. Each of the seven extraction policies
replays the same candidate objects for both backends. Frames, candidates,
transformations, policies, and backends are not independent observations; the
independent content-position count is 20.

P0 must exactly reproduce all stored DCT/TAU_0.72 predictions, alternatives, status,
warnings, truncation and taxonomy before experimental-policy results are accepted.

## Metrics and post-runtime labels

For each backend/policy, overall and by transformation, report:

- four mutually exclusive frozen outcomes C/S/U/N and safe=C+S, unsafe=U,
  no-match=N;
- `MATCH_FOUND`, `AMBIGUOUS`, and `MATCH_NOT_FOUND` status counts;
- candidate GREEN/YELLOW and production review-tier counts;
- wrong-source GREEN segment count, ambiguous wrong-source GREEN segment count, and
  unambiguous wrong-source primary GREEN case count;
- primary segment count, alternative count, maximum primary+alternative mappings per
  case, complete-final case count, and complete-final mapping count;
- extraction-loss E count and distinct content-position count, R count, and unresolved
  count;
- boundary completeness distribution, source-boundary MAE, accused-boundary MAE,
  speed MAE, and runtime.

All taxonomy and error metrics use the unchanged frozen evaluator and existing
one-to-one assignment. C is a correct unambiguous match; S is an ambiguous result whose
primary or complete alternative contains GT; U is an unsafe wrong match; N has no
output. A status of ambiguous is never counted as C.

E means at least one evaluator-complete GT-free proposal was generated in extraction,
but no complete final primary/alternative exists. For DINO, R retains the Phase 2B-4
definition: final completeness is absent and either the nearest-per-frame oracle is
incomplete, or the oracle is complete, no GT-free proposal is complete, and DINO is
directionally worse than paired DCT on at least three of the four registered Phase
2B-4 indicators. For DCT, R is final incompleteness with an incomplete nearest-frame
oracle. Unresolved is final-incomplete and neither E nor R. These labels are applied
after runtime output and never affect proposal selection or occupancy.

Wrong-source means a primary segment's episode differs from the single GT episode.
Unambiguous wrong-source GREEN is a `MATCH_FOUND` case whose first primary is
wrong-source and GREEN. Ambiguous wrong-source GREEN uses status `AMBIGUOUS`. DEV
synthetic negatives have no GT episode and are reported separately.

`emitted segment count` means primary segments only. Alternatives are reported
separately. `total mappings` means primary plus alternatives and is used for the
per-case manual-inspection threshold. Corpus-wide segment inflation uses primary
segments only.

## Primary regression and E-level trace

The mandatory A010 cases are SOURCE_A position 010 H264_REENCODE, DIRECT, and
SPEED_0_8X. An eligible policy must give H264 an evaluator-complete final primary or
alternative, avoid an unambiguous wrong-source GREEN, preserve
`REVIEW_REQUIRED`, and keep DIRECT and SPEED_0_8X safe.

For A010 H264, ignored raw output records the exact selected round-0 proposal,
selection tuple, removed candidate ordinals, and round-1 fits. For all 37 P0 E cells,
ignored output records P0/remediation round-0 complete-proposal counts, winners,
occupancy counts, next-round complete-proposal counts, and final completeness.
Committed output contains only aggregate distributions and sanitized slopes,
residuals, counts, and policy identifiers.

## Eligibility gates

The P0 DINO reference is safe 139/220, unsafe 56/220, no-match 25/220, E 37 cells,
and E in 13/20 positions. A non-baseline policy is DINO-eligible only if:

1. safe >= 139;
2. unsafe <= 56;
3. no-match <= 25;
4. E <= 27;
5. E content positions <= 10;
6. A010 H264 passes the primary regression;
7. A010 DIRECT remains safe;
8. A010 SPEED_0_8X remains safe;
9. it introduces zero unambiguous wrong-source GREEN cases relative to P0;
10. the 20 existing DINO DEV synthetic negatives emit zero primary segments.

P0 DCT is safe 88/220, unsafe 58/220, and no-match 74/220. DCT non-regression requires
safe >= 88, unsafe <= 58, and zero new unambiguous wrong-source GREEN cases relative
to P0.

For each backend separately, corpus-wide primary segment count must be no greater than
1.25 times that backend's P0 count. Report absolute count, percentage change, and the
maximum primary+alternative mappings in one case. Every case above four total mappings
is listed for manual inspection in ignored raw output and by anonymous case alias in
the committed aggregate.

An experimental policy can enter selection only if it passes the synthetic gate, DINO
eligibility, DCT non-regression, and both backend segment-inflation gates. No threshold
may be relaxed when no policy qualifies.

## Fixed selection rule

If no policy qualifies, no selected experimental artifact is written and the phase
stops. Otherwise select exactly one qualifying policy by the following lexicographic
minimum key:

```text
(
  -DINO_safe,
  DINO_unsafe,
  DINO_E,
  DINO_E_content_positions,
  DINO_no_match,
  DINO_primary_segment_inflation_ratio,
  simplicity_ordinal
)
```

Simplicity order is P1, P2, P3, P4, P5, P6. This places ranking-only before
occupancy-only, a single mechanism before a combination, and smaller epsilon before a
larger epsilon within a family. No fresh result participates.

If selected, write only ignored
`workspace/phase2b5/selected-experimental-policy.json`, containing protocol commit,
policy ID, epsilon, occupancy, DEV/synthetic metrics, selection key/reason, and SHA-256
fingerprints of Phase 2B-5 runtime code, frozen matching/evaluator/config, and DEV
input. It is not a production authorization. Never create `selected-score-contract`.

## Paired transition and mechanism attribution

For the selected policy, or the best policy under the same selection key if none
qualifies, report the 4x4 paired P0-to-policy DINO outcome matrix over all 220 cells.
For the 37 P0 E cells, report transitions to C/S/U/N, no-match conversions, newly
unsafe conversions, retained E, and distinct remaining E positions.

Mechanism attribution uses only the registered paired contrasts:

- ranking only: P0 versus P1/P2/P3;
- occupancy only: P0 versus P4;
- interaction at epsilon 0.001: P2 versus P5;
- interaction at epsilon 0.002: P3 versus P6.

Report deltas in DINO safe/unsafe/no-match/E/E-position counts and segment/alternative
counts. These are controlled policy contrasts on paired DEV cells, not population
causal estimates.

## GT/evaluator leakage prohibition

Runtime proposal selection and occupancy accept no input or captured state named
`truth`, `ground_truth`, `expected_source`, `expected_speed`, `complete`, or
`is_correct`, and import no evaluator helper. A static AST/source audit enforces this.

For a fixed candidate stream, mutating GT episode, source boundaries, speed, or accused
boundaries after candidate generation must leave raw experimental segments,
alternatives, warnings, and round trace byte-identical. Only post-runtime evaluation
labels may change.

## Reproducibility, privacy, and artifacts

The raw result and membership traces remain exclusively under ignored
`workspace/phase2b5/` and are created without overwrite. The harness refuses to run if
the protocol commit is not protocol-only or has changed, production/config differ from
`93bf13b`, the DEV input is not the exact registered 220-positive/20-negative paired
matrix, or any selected-score/fresh/held-out artifact exists under Phase 2B-3.

Committed artifacts may contain public Git provenance, policy identifiers, epsilon,
occupancy, SOURCE_A/SOURCE_B and position aliases, transformations, normalized
positions, aggregate counts/rates/errors, slopes/residual distributions, and anonymous
manual-inspection case aliases. They may not contain exact real source/accused times,
raw candidates, candidate/frame IDs, media names or paths, model/index/workspace paths,
embeddings, databases, private hashes, or scratch files. A recursive sanitizer enforces
this boundary.

The final report is
`docs/review/PHASE2B-5-EXTRACTION-COMPATIBILITY-REMEDIATION-REPORT.md`, with the 32
required sections and explicit Q1-Q18 answers. The report must distinguish 220 nested
cells from 20 content positions, disclose every failed gate and negative result, and
must not describe a DEV-selected policy as production-ready.
