# Phase 2B-4 pre-registered temporal-separability characterization protocol v0.1

This protocol is frozen in a protocol-only Git commit before any Phase 2B-4
characterization code is written or any Phase 2B-4 result is generated. Phase 2A
production remains frozen at `93bf13b`. Phase 2B-4 is descriptive diagnosis only: it
does not select a DINO policy, authorize production integration, or remediate Temporal
Alignment.

## Question and immutable scope

The sole question is why a correct-source candidate cloud that survives retrieval and
score admission can fail to produce an evaluator-complete correct alternative. The
known primary case is `SOURCE_A-POSITION_010-H264_REENCODE`; it is not a rule, training
target, or exception in the experiment.

- Starting experiment commit: `78123a295a3f025cb728c0ace84a9c52520a9941`.
- Frozen production closure: `93bf13b`.
- Phase 2B-3 DEV result is the only real-candidate input.
- Source/query sampling remains 2 FPS; retrieval remains exact top-k 8.
- `src/ipvm/`, `config/`, the evaluator, Temporal Alignment, score thresholds,
  RANSAC, sparse voting, fitting, merge, rival/ambiguity logic, completeness, and tier
  semantics may not change.
- No fresh content is selected, generated, decoded, or evaluated. No Phase 2B-3
  `freeze`, `make-fresh`, or `held-out` command is run.
- No ROI/tile or PIP repair, FAISS, new model, SigLIP, sequence matcher, DTW,
  Viterbi, dynamic programming, learned calibrator, audio, ASR, Gemma, Excel, or Phase
  3 work is allowed.
- Evaluator tolerances remain frozen. In particular, the slope/duration tolerance is
  not widened to make a failed mapping pass.

## Irreversible execution stages

1. Audit repository, inputs, prior reports, frozen code, and the 187-test baseline.
2. Commit only this protocol. No Phase 2B-4 result may exist before that commit.
3. Implement an isolated read-only replay/measurement harness under
   `experiments/phase2b4/` and focused tests.
4. Run the registered DEV characterization and controlled synthetic probes.
5. Generate a sanitized aggregate and the required technical report.
6. Run all tests, reproducibility checks, production-diff guard, fresh-absence guard,
   and privacy scans; commit only Phase 2B-4 artifacts.
7. Stop for independent review and Architecture Owner decision.

There is no calibration, policy-freeze, or held-out stage in Phase 2B-4. Later evidence
cannot alter these definitions, grids, or decision rules.

## Data and comparison policy

All old 20 observed content positions are DEV. The independent observational unit is
the content position, not a query frame, candidate, transformation cell, backend, or
policy replay. No population-generalization or held-out claim is permitted.

Primary deep dive, with DCT and DINO compared on the same content position:

```text
SOURCE_A-POSITION_010-H264_REENCODE
SOURCE_A-POSITION_010-DIRECT
SOURCE_A-POSITION_010-SPEED_0_8X
```

Secondary prevalence uses all 220 existing positive DEV transformation cells. DCT and
the diagnostic DINO reference are paired within cell. Existing negative queries are
outside the main prevalence denominator; they may only be checked as an unchanged
safety invariant.

The primary DINO reference is `TAU_0.72`. It is diagnostic only and is not a selected
or proposed production policy. `TAU_0.68` and `TAU_0.82` may be replayed only for the
known A010 H.264 membership/geometry invariance check. No tau is optimized and no
`selected-score-contract.json` is created.

## Candidate notation and normalization

For query frame `i`, let `q_i` be accused nominal-grid time and let the fixed top-k
candidate at retrieval rank `r` be

```text
c_ir = (episode_ir, source_time_ir, raw_cosine_ir, rank_ir).
```

The source-frame identity is `(episode, source_time)`. Numeric timestamps in different
episodes are never treated as the same frame and are never subtracted from one another.
For a ground-truth segment with speed `a*`, accused start `q0`, and source start `s0`,

```text
g(q) = s0 + a* (q - q0).
```

The frozen source-grid step is `h = 0.5 s`. A GT-near candidate has the correct episode
and `abs(source_time - g(q)) <= h + 1e-6`. This is a diagnostic use of ground truth,
identical to the existing one-source-grid retrieval-attribution neighborhood; it is
never used to construct or alter a runtime result.

Raw detailed output retains nominal times only below ignored `workspace/phase2b4/`.
Committed output may retain aliases, slopes, counts, rates, normalized positions, and
errors in seconds or grid steps, but never exact source/query timelines.

## Pre-registered temporal-separability metrics

Metrics are computed identically for DCT and DINO from retrieval-ranked candidate
records before the frozen 0.82 admission gate, except where an explicit admitted-pool
cut is also reported. Per-frame values remain in ignored raw traces; committed output
contains only case/group summaries.

1. **Unique source frames in top-k.**
   `U_i = |{(episode_ir, source_time_ir)}|`. The adapter already forbids duplicate
   identity within one frame; this metric is retained as a contract check.
2. **Unique correct-source timestamps.**
   `U_i_correct = |{source_time_ir : episode_ir = episode*}|`.
3. **Top-1 source frame.** The identity at minimum retrieval rank. Exact time is raw
   only; committed summaries retain source correctness, repeat share, and order rates.
4. **GT-nearest rank and distance.** Among correct-source candidates choose the one
   minimizing `(abs(source_time-g(q_i)), rank)`. Record its rank and absolute distance;
   both are null when no correct-source candidate exists. A separate Boolean records
   whether it is GT-near.
5. **Episode-local source-time spread.** For each episode represented in a frame take
   `max(source_time)-min(source_time)`. The all-top-k spread is the maximum of these
   episode-local ranges, never a cross-episode subtraction. Correct-source spread is
   the corresponding correct-episode range. A singleton range is zero; an absent
   correct source is null.
6. **Repeated source frames across query frames.** For every source-frame identity
   count the number of distinct query frames containing it, `m_x`. Report the number
   with `m_x >= 2`, repeat surplus `sum_x max(0,m_x-1)`, and maximum multiplicity
   `max_x m_x`. The same fields are also reported for the correct source.
7. **Shared top-1 fraction.** `max_x n_top1(x) / n_query_frames`, where `n_top1(x)` is
   the number of query frames whose top-1 identity is `x`.
8. **Top-1 temporal monotonicity violations.** For consecutive query frames whose
   top-1 candidates are in the same episode, a violation occurs when the later source
   time is smaller by more than `1e-6`. Report count, eligible adjacent-pair
   denominator, and rate. Equal timestamps are separately counted as plateaus.
9. **Candidate temporal-order inversions.** Form the per-frame sequence of the
   GT-nearest correct-source candidate after dropping null frames. For all pairs
   `i < j`, an inversion is `source_time_i > source_time_j + 1e-6`; equality within
   `1e-6` is a tie. Report inversion/tie counts, pair denominator, and rates. This
   metric is GT-labeled but uses the same formula for both backends.
10. **Local entropy and effective diversity.** For each centered three-frame window
    (previous/current/next, truncated at case boundaries), form the multiset of all
    candidate source-frame identities. With empirical shares `p_x`, compute natural-log
    Shannon entropy `H=-sum p_x log(p_x)`, effective diversity `D=exp(H)`, and normalized
    entropy `H/log(N)` for `N>1` candidate slots (zero otherwise). Report mean/min/max
    across windows. Repeat on the correct-source-only multiset; an empty window is null.
11. **Correct-source raw-cosine spread.** Within each frame this is
    `max(raw_cosine)-min(raw_cosine)` over correct-source candidates; singleton is zero
    and absent is null. Report median, p10, p90, and mean over defined frames.
12. **GT-near competition margin.** Per frame, `best raw cosine among GT-near
    candidates - best raw cosine outside the GT-near set`. It is null if either side is
    empty. Negative values mean a competing candidate outranks every GT-near candidate.
    Report median, p10, p90, mean, and fraction non-positive.

Counts/rates are reported with denominators. Quantiles use NumPy's default linear
interpolation. Case-level comparisons are descriptive paired differences; no frame-
level confidence interval or independence claim is made.

## Oracle-band diagnostic

The oracle is an upper-bound diagnostic, never a runtime path. For every query frame it
collects **all** GT-near candidates. It reports candidate count, distinct accused-frame
coverage, distinct source-frame count, repeat count/surplus/max multiplicity, and the
order metrics above.

Two registered fits are computed:

- **All-band OLS:** ordinary unweighted least squares over every collected candidate,
  including duplicates across query frames.
- **Nearest-per-frame OLS:** at most one candidate per covered frame, chosen by the
  registered `(GT distance, rank)` tie-break.

For either set with at least two distinct accused times, fit
`source_time = slope * accused_time + intercept`. Report slope, raw-only intercept,
RMS residual, maximum absolute residual, source values reconstructed at the GT accused
boundaries, and start/end errors. The committed form replaces intercept and reconstructed
absolute source values with intercept error and boundary errors. Fits with insufficient
distinct accused times are null.

An oracle fit is **evaluator-complete** only when a diagnostic SegmentMatch formed from
its accused coverage and fitted source endpoints passes the unchanged frozen evaluator:
correct episode, accused overlap/assignment, both source endpoints within 1 second,
and physical duration/slope consistency. No tolerance is copied or approximated in a
second classifier; the frozen evaluator result is authoritative.

## External trace of the frozen alignment

The replay harness may subclass or wrap methods only to record inputs and outputs while
delegating every computation to `TemporalAligner`. It may not copy, replace, or modify
the algorithm. Candidate IDs are deterministic hashes/ordinals of canonical candidate
records in raw output; committed output contains stage counts only.

For each primary backend/case, record:

1. input candidate stream and 0.82-admitted membership;
2. each `_sparse_peaks` input and returned peak `(episode, slope, offset, vote,
   support_frames)`;
3. `_support_for_peak` membership;
4. `_continuous_runs` membership;
5. every `_robust_fit` input, inlier membership, slope, intercept, RMS, and max residual;
6. every `_proposal_to_segment` input and result;
7. `_extract_episode_segments` outputs and truncation flag;
8. merge input/output membership;
9. accepted primary, suppressed/ranked rivals, final alternatives, and status as exposed
   by the frozen outcome and its competing-candidate audit;
10. the unchanged evaluator result for each primary and correct-source alternative.

A registered peak is GT-compatible for diagnostic labeling when it is in the correct
episode, its slope differs from GT by no more than one frozen slope bin (`0.025`), and
its predicted source time at both GT accused boundaries differs from GT by no more than
one source-grid step (`0.5 s`). This label is applied after peak generation and cannot
affect it.

A **GT-free geometry proposal** is any proposal produced by the frozen
peak→support→run→fit machinery before winner suppression/ranking. Its construction has
no GT input. Ground truth may subsequently label whether that already-generated
proposal is evaluator-complete.

The first-loss stage for a case is the earliest of:

- `CANDIDATE_ORACLE`: nearest-per-frame oracle is not evaluator-complete;
- `SPARSE_PEAK`: oracle is complete but no GT-compatible frozen peak exists;
- `SUPPORT_OR_FIT`: a compatible peak exists but no generated GT-free proposal is
  evaluator-complete;
- `EXTRACTION`: a complete proposal exists but no complete episode-extraction output
  survives;
- `SUPPRESSION_OR_ALTERNATIVE`: a complete extracted result exists but no complete
  final primary/alternative survives;
- `NONE`: the final output contains an evaluator-complete correct mapping.

This stage taxonomy is diagnostic and does not change the case outcome.

## Actual-versus-oracle comparison

For every primary case compare both oracle fits with every actual correct-source final
alternative. Report slope, intercept error, RMS, max residual, accused start/end errors,
source start/end errors, inlier count, distinct accused frames, distinct source frames,
and duplicate-source concentration `1 - distinct_source_frames/inlier_count`. Raw exact
intercepts/boundaries remain ignored. Deltas are `actual - nearest-per-frame oracle`;
absolute-error deltas use `actual absolute error - oracle absolute error`.

The frozen evaluator is run independently on each mapping and its failed conditions are
reported as: source identity, accused assignment/boundaries, source boundaries, and
physical duration/slope consistency. A slope near 0.75 for a true 1.0 mapping is a
physical mapping error, not a tolerance inconvenience.

## Representation and extraction contribution flags

The architecture recommendation is based on two separately counted flags, registered
before results:

- **Extraction-loss flag E:** the frozen GT-free geometry proposal set contains at
  least one evaluator-complete correct mapping, but the final primary/alternatives do
  not.
- **Separability-limit flag R:** the final output has no complete correct mapping and
  either (a) the nearest-per-frame oracle is itself incomplete, or (b) it is complete
  but no GT-free geometry proposal is complete **and** DINO is directionally worse than
  paired DCT on at least three of four registered indicators: higher correct-source
  maximum multiplicity, higher shared-top-1 fraction, lower correct-source effective
  diversity, and higher GT-nearest inversion-plus-tie rate. At least one comparison
  must be strict.

The flags yield descriptive mechanism cells: `E only` (extractor compatibility),
`R only` (representation temporal separability), `E+R` (mixed), and `neither/unresolved`.
The split is quantified as counts and rates among failed positive DEV cells and as
counts of the 20 content positions with at least one flagged transformation. It is not
a causal population estimate.

Recommendation Q10 is determined as follows:

- **A** only if the primary H.264 case is `E only` and E is not isolated to that one
  cell (at least one additional DEV cell or one registered synthetic setting has E).
- **B** if the primary case is `R only` and registered duplicate/quantization/plateau
  probes reproduce loss of complete recovery as temporal diversity decreases.
- **C** if the primary case is `E+R`, or if both E and R recur outside the primary case
  and the synthetic probes separately reproduce both contributions.
- **D** if the primary case is neither/unresolved or its registered signal is isolated
  and the synthetic probes do not reproduce it.

If evidence falls between rules, report `UNRESOLVED` and recommend D (no architecture
change) rather than reinterpret the rules.

## Transform-severity control

Within `SOURCE_A-POSITION_010`, DIRECT, H264_REENCODE, and SPEED_0_8X use the same full
metric/trace/oracle calculation. Explanations are based on measured candidate-cloud
changes, never on the transformation label alone. Required paired comparisons include
candidate support, multiplicity, entropy/diversity, ordering, GT-near margin, oracle
geometry, GT-free proposal geometry, first-loss stage, and final evaluator condition.

## Broader DEV prevalence

For DCT and `TAU_0.72`, all 220 existing positive cells are summarized overall, by
existing C/S/U/N outcome, by existing Phase 2B-3 failure attribution, and by
transformation. Report medians and interquartile ranges for continuous metrics plus
counts/rates for E/R cells. Also report a position-level matrix: number of cells per
position, failed cells, E cells, R cells, and cells exhibiting both. DCT/DINO comparisons
remain paired and descriptive. The study has two source episodes and 20 DEV positions;
no population CI or cross-episode stability claim is made.

## Controlled synthetic candidate-cloud probes

Synthetic streams use 16 accused frames at 2 FPS over `[0,8)`, a correct episode, and
the affine truth `source = accused + 100`. Every frame has exactly top-8 identities on
the 0.5-second source grid. The correct-line candidate has raw/adapted visual score
0.96. Inactive filler candidates score 0.70 and do not pass the frozen 0.82 gate.
Deterministic ties are resolved by registered rank. Each condition is replayed twice
to require byte-for-byte deterministic sanitized output.

Only one axis changes from the base cloud at a time:

1. **Distributed duplicate concentration:** a same-correct-episode plateau candidate
   at source time 120.0 and score 0.98 is active on an evenly distributed deterministic
   subset of `0, 4, 8, 12, 16` frames; the true candidate remains at rank 2.
2. **Timestamp quantization/collision:** the true candidate source time is rounded to
   the nearest grid with step `0.5, 1.0, 2.0, 4.0` seconds (half-to-even NumPy rounding),
   while all other base fields remain fixed.
3. **Distractor density:** activate `0, 1, 3, 7` deterministic incoherent distractors
   per frame at score 0.94. Their source times are generated by a fixed arithmetic
   permutation and validated to have no six-frame frozen coherent line.
4. **Correct-line residual noise:** add the repeating signed pattern
   `(-1, 0, +1, 0)` times amplitude `0.0, 0.5, 1.0, 1.5` seconds to the correct
   candidate source time.
5. **Competing parallel-line strength:** add one wrong-episode slope-1 line at score
   `0.80, 0.82, 0.90, 0.96, 0.99`; all other fields remain base. The correct line is
   unchanged.
6. **Local temporal plateau length:** activate a same-correct-episode source-time
   120.0, score-0.98 plateau over the centered contiguous block of
   `0, 2, 4, 6, 8, 12, 16` query frames; the true candidate remains present at rank 2.

For every setting report frozen outcome/status, complete correct primary or alternative,
first-loss stage, E/R flags where applicable, fitted alternatives, ambiguity, and the
registered separability metrics. The failure point is the first ordered grid value at
which no complete correct mapping remains. This grid is characterization, not a
proposed matcher or tuning search.

## Safety sidebar

The existing `TAU_0.68` result for
`SOURCE_A-POSITION_001-VERTICAL_9_16` is retained as a read-only sidebar. Its temporal-
separability metrics may be compared with DCT and `TAU_0.72`, but no score-selection or
exception rule may follow. All candidate GREEN values remain internal alignment-quality
diagnostics and all production review tiers remain `REVIEW_REQUIRED`.

## Tests, provenance, and privacy gates

Focused tests must cover metric formulas, duplicate concentration, entropy/diversity,
monotonicity/inversions, oracle diagnostics, actual-versus-oracle deltas, trace
determinism, every synthetic axis/grid, production diff, fresh-data absence, and
sanitization. All prior tests must remain green; the starting baseline is 187 passed.

The harness refuses to run unless:

- the protocol-only commit contains only this file and the committed protocol is
  unchanged;
- `git diff 93bf13b -- src/ config/` is empty;
- Phase 2B-3 DEV input exists and contains no fresh/held-out records;
- no selected-score-contract file or fresh artifact exists anywhere under the Phase
  2B-3 workspace;
- input cases are the registered old DEV aliases and the reference policy exists.

Detailed traces, exact timestamps, candidates, media/model/index paths and identities,
embeddings, frames, databases, and private hashes stay under ignored
`workspace/phase2b4/`. The committed aggregate validator rejects path/hash/time/candidate
keys, media/model/index names, absolute paths, exact timelines, per-frame records, and
64-character private hexadecimal fingerprints. Allowed committed content is limited to
public Git provenance, source/position aliases, normalized positions, registered
configuration, aggregate metrics, slopes, and normalized errors.

The required report is
`docs/review/PHASE2B-4-TEMPORAL-SEPARABILITY-CHARACTERIZATION-REPORT.md`, with all 23
authorized sections and explicit Q1-Q10 answers. It must distinguish descriptive,
diagnostic, and architecture-recommendation claims, state denominators before rates,
and preserve negative findings and limitations. The final response reports both commit
IDs, the primary first-loss stage, DCT/DINO and synthetic findings, broader prevalence,
the registered A/B/C/D recommendation, production diff, pytest count, privacy status,
and then stops.
