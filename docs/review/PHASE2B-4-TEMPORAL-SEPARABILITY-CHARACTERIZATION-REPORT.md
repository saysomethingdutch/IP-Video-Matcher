# Phase 2B-4 Temporal Separability & Alternative Mapping Completeness Characterization Report

## 1. Executive Summary

Phase 2B-4 completed the registered DEV-only characterization and stopped without
changing production. The primary A010 H.264 DINO cloud does contain an
evaluator-complete affine mapping: the nearest-per-frame oracle fit has slope 1.0284,
RMS residual 0.2207 seconds, and passes the frozen evaluator. More importantly, the
frozen, GT-free peak/support/run/fit machinery itself generates 11 distinct complete
correct-source proposals. None survives episode extraction. The registered first-loss
stage is therefore `EXTRACTION`, and the primary mechanism cell is
`EXTRACTOR_COMPATIBILITY` (E only), not a score-admission loss.

The two emitted correct-source alternatives have slopes 0.751 and 0.789. On the first
SOURCE_A extraction call, the 0.751 proposal's leading fit-quality value is 0.9530,
just above 0.9524 for both a complete 0.911 proposal and a complete exact-slope 1.0
proposal. The frozen lexicographic ordering selects 0.751 first; its inliers are then
removed. The second call selects the remaining 0.789 proposal, after which no complete
proposal remains. This is a characterization of current behavior, not a remediation.

Across all 220 TAU_0.72 positive DEV cells, 81 lack a complete final mapping. Of those,
37 carry extraction-loss flag E, 16 carry separability-limit flag R, and 28 remain
unresolved by the registered flags. E occurs in 13 of 20 content positions and is not
isolated to A010. Synthetic duplicate and plateau probes do not reproduce the
extraction loss; only extreme 4-second timestamp quantization removes complete recovery,
at `SPARSE_PEAK`, which is a different mechanism. Applying the pre-registered decision
rule yields option A: a future, separately authorized narrow Temporal Alignment /
alternative-extraction compatibility remediation. No such change is implemented here,
and this report does not declare Phase 2B PASS.

## 2. Architecture scope

The implementation is isolated under `experiments/phase2b4/`, one focused test module,
two sanitized machine-readable results, and this report. It reads existing Phase 2B-3
DEV candidate audits and replays the frozen `TemporalAligner` through recording
wrappers that delegate every calculation to `super()`.

No change was made to `src/ipvm/`, `config/`, the evaluator, sparse voting, RANSAC,
robust fitting, extraction, merge, rival/ambiguity logic, boundary completeness,
candidate/review tiers, FPS, or top-k. No DTW, Viterbi, dynamic-programming matcher,
sequence model, ROI/tile path, PIP repair, FAISS, new encoder, audio, ASR, Gemma, Excel,
or Phase 3 work was added.

## 3. Phase 2B-3 independent-review premise

Phase 2B-3 closed with no eligible global score contract. Its independent-review
premise for this phase was the A010 H.264 invariant: all 128 top-8 candidates, all 13
correct-source supporting frames, and all 16 rival supporting frames survived at each
registered tau, yet no evaluator-complete correct alternative remained. That evidence
made score admission an implausible primary cause for this case and fixed the Phase
2B-4 question at the candidate-geometry-to-alternative path.

The current replay confirms the premise. At TAU_0.72, 128/128 candidates enter the
aligner, the case remains `AMBIGUOUS / UNSAFE_WRONG_MATCH`, and no complete correct
final mapping exists. TAU_0.68, 0.72, and 0.82 produce the same 128 admitted candidates,
13 correct-support frames, outcome, status, and correct-source alternative slopes
0.751/0.789.

## 4. Protocol commit

The initial protocol-only commit is:

```text
59a834a  Pre-register Phase 2B-4 temporal separability characterization
```

Before implementation or result generation, a protocol-only erratum moved the
synthetic plateau timestamp from an identity that collided with a registered true-line
point to a distinct, still-grid-aligned identity:

```text
79decff3270c028056f34fdd11902545fad0b74a
Correct Phase 2B-4 plateau fixture identity
```

Both commits contain only `experiments/phase2b4/PROTOCOL.md`. The erratum changed no
metric, parameter grid, mechanism flag, recommendation rule, or real-case analysis.
The harness refuses to run if the protocol differs from the second commit.

## 5. Production freeze

The frozen production closure is `93bf13b`. At completion:

```text
git diff 93bf13b -- src/ config/
```

is empty. Source/query sampling remains 2 FPS, top-k remains 8, and the production
review tier remains `REVIEW_REQUIRED`. TAU_0.72 is not integrated into production.

## 6. Data policy / fresh-data preservation

The only real-candidate input is the pre-existing Phase 2B-3 DEV record: two anonymous
sources, 20 previously observed content positions, and 11 transformations per position.
The 220 DINO cells and 220 paired DCT cells are repeated measurements on 20 independent
content positions, not 440 independent samples.

No selected score-contract artifact exists. No fresh or held-out artifact exists under
the Phase 2B-3 workspace, and no fresh-selection, media-generation, or held-out command
was run. The harness checks these conditions before replay. This report contains no
held-out estimate or cross-episode stability claim.

## 7. Diagnostic score reference

TAU_0.72 is the pre-registered DINO reference because it passed Phase 2B-3 eligibility
conditions 1-7 and emitted no wrong-source GREEN on DEV. It is a diagnostic score
mapping, not a selected production policy. TAU_0.68 and TAU_0.82 were used only for the
A010 H.264 invariance check described above; no tau search or post-result adjustment was
performed.

## 8. Temporal-separability metric definitions

All metrics are computed from the same retrieval-ranked candidate records for DCT and
DINO before the frozen 0.82 admission gate. The source-frame identity is the pair
`(episode, nominal source-grid time)`; timestamps from different episodes are never
subtracted.

For each query frame, the harness reports unique top-k identities, unique correct-source
times, top-1 identity, the rank and distance of the nearest correct-source candidate,
episode-local source-time spread, and correct-source spread. A GT-near candidate is on
the correct episode and within one 0.5-second source grid step of the GT mapping.

Across query frames, source-frame multiplicity counts distinct query frames containing
the same identity. Shared-top1 fraction is the largest top-1 identity count divided by
the number of query frames. Top-1 monotonicity uses eligible adjacent same-episode
pairs. GT-nearest temporal order uses all ordered frame pairs, recording inversions and
ties separately.

Local entropy uses centered three-frame windows, natural-log Shannon entropy, and
effective diversity `exp(H)`. Correct-source raw-cosine spread is the per-frame maximum
minus minimum among correct-source candidates. GT-near competition margin is the best
GT-near raw cosine minus the best score outside that set; a non-positive value means an
outside candidate is tied with or ranked above all GT-near candidates.

The oracle band collects all GT-near candidates and reports both all-band OLS and one
nearest-candidate-per-frame OLS for `source_time = slope * accused_time + intercept`.
It is an upper-bound diagnostic that uses GT and is not a runtime algorithm. Exact
formulas, null cases, denominators, tie rules, and quantile rules are frozen in the
protocol.

## 9. A010 H264 candidate cloud

The primary DINO case has 16 query frames and 128 candidates; all 128 are admitted.
The correct episode appears in every query frame, but only 13 frames contain a candidate
within the one-grid-step GT band.

| Candidate-cloud measure | DCT | DINO TAU_0.72 |
|---|---:|---:|
| Admitted / input candidates | 126 / 128 | 128 / 128 |
| Correct-source-present frames | 16 / 16 | 16 / 16 |
| GT-near frames | 16 / 16 | 13 / 16 |
| Median GT-nearest rank | 2 | 5 |
| Median GT-nearest distance (s) | 0.2208 | 0.2208 |
| Correct-source maximum multiplicity | 4 | 8 |
| Shared top-1 fraction | 0.0625 | 0.1250 |
| Correct-source local effective diversity | 5.406 | 6.100 |
| GT-nearest inversion + tie rate | 0 | 0.0500 |
| Median correct-source raw-cosine spread | 0.2077 | 0.01288 |
| Median GT-near competition margin | -0.02275 | -0.02619 |
| Non-positive margin frames | 16 / 16 | 13 / 13 |

DINO is not uniformly “lower diversity”: its local effective-diversity value is higher.
The observed local problem is that more temporal alternatives remain visually close,
the same correct-source timestamps recur more often, nearest candidates rank lower,
and the correct-source score spread is about sixteen times narrower. The combination
reduces temporal distinguishability without deleting the correct-source episode.

## 10. A010 H264 oracle structure

The DINO all-band oracle uses 17 candidates across 13/16 frames and 12 distinct source
frames. Its slope is 1.0353, RMS residual 0.2360 seconds, maximum residual 0.3602
seconds, and it is evaluator-complete.

The stricter nearest-per-frame oracle uses 13 candidates and 10 distinct source frames.
Its slope is 1.0284, RMS residual 0.2207 seconds, maximum residual 0.4160 seconds, and
both reconstructed source endpoints are within 0.18 seconds of GT. It is also
evaluator-complete. Thus the DINO candidate cloud contains sufficient correct affine
structure under the registered diagnostic.

For DCT, the nearest-per-frame oracle covers 16/16 frames with 16 distinct source
frames, slope 1.0, and residual at floating-point zero. The DCT all-band fit also has
slope 1.0, with RMS 0.25 seconds because it intentionally includes both in-band grid
neighbors.

## 11. Frozen alignment stage trace

The tracing subclass records the frozen methods' inputs and outputs and always delegates
calculation to the production implementation. Stored predictions and alternatives are
required to equal the replayed values at the exact JSON persistence boundary.

| Stage count | DCT | DINO TAU_0.72 |
|---|---:|---:|
| Sparse-peak calls | 6 | 8 |
| Returned peaks | 120 | 194 |
| GT-compatible peaks | 1 | 1 |
| Proposal results | 120 | 152 |
| Correct-source proposal results | 61 | 69 |
| Distinct evaluator-complete GT-free proposals | 10 | 11 |
| Complete proposals in first sparse call | 9 | 11 |
| Complete episode-extraction outputs | 2 | 0 |
| Complete final primary/alternatives | 1 | 0 |
| First-loss stage | `NONE` | `EXTRACTION` |

On DINO's first SOURCE_A sparse call, the leading candidates under the frozen fit-quality
ordering are:

| Fitted slope | Leading quality | Inliers | Inlier ratio | RMS (s) | Evaluator-complete |
|---:|---:|---:|---:|---:|---|
| 0.7510 | 0.9530 | 15 | 1.000 | 0.2387 | No |
| 0.9113 | 0.9524 | 14 | 1.000 | 0.2009 | Yes |
| 1.0000 | 0.9524 | 12 | 1.000 | ~0 | Yes |
| 0.9830 | 0.9522 | 14 | 1.000 | 0.1789 | Yes |

The first tuple component wins before inlier count, ratio, residual, and duration are
consulted. The 0.751 proposal therefore wins by 0.0006 in that leading component. The
extractor claims/removes its 15 inliers. On the next SOURCE_A sparse call, slope 0.789
leads with quality 0.9515, 12 inliers, inlier ratio 1.0, and RMS 0.2024 seconds; no
complete proposal remains in that reduced pool. These two selected fits become the
observed correct-source alternatives.

## 12. Extracted-vs-oracle fit

| Mapping | Slope | RMS (s) | Max residual (s) | Inliers | Distinct accused/source | Duplicate concentration | Complete |
|---|---:|---:|---:|---:|---:|---:|---|
| DINO nearest oracle | 1.0284 | 0.2207 | 0.4160 | 13 | 13 / 10 | 0.2308 | Yes |
| Actual alternative 1 | 0.7510 | 0.2387 | 0.4980 | 15 | 15 / 11 | 0.2667 | No |
| Actual alternative 2 | 0.7890 | 0.2024 | 0.3667 | 12 | 12 / 10 | 0.1667 | No |

Alternative 1 differs from the nearest oracle by -0.2774 in slope. Its source-start
error is 0.7792 seconds and source-end error is 1.2128 seconds. Alternative 2 differs
by -0.2394; its start error is 1.6232 seconds and end error is 0.0648 seconds. Both
cover the full accused interval and name the correct episode, but both fail physical
duration/slope consistency and one source boundary. The DCT final correct alternative
has slope 1.0, 16 distinct inliers, zero residual, and is complete.

## 13. DCT-vs-DINO temporal separability

For A010 H.264, the clearest single difference is the median correct-source raw-cosine
spread: 0.2077 for DCT versus 0.01288 for DINO, a roughly sixteen-fold compression.
That does not by itself determine the mapping, but it shows that DINO supplies much
less score separation among correct-episode timestamps. DINO also doubles maximum
correct-source timestamp multiplicity (4 to 8), moves the nearest correct candidate
from median rank 2 to 5, loses the GT-near band on 3/16 frames, and introduces a 0.05
pairwise inversion-plus-tie rate where DCT has zero.

The full DEV distribution prevents a global claim that DINO is always less separable:

| Backend, 220 cells | Effective diversity median [Q1, Q3] | Max multiplicity median [Q1, Q3] | Inversion+tie median [Q1, Q3] | Oracle fits available |
|---|---:|---:|---:|---:|
| DCT | 9.642 [6.970, 11.093] | 7 [5, 10] | 0.2125 [0, 0.6167] | 139 / 220 |
| DINO TAU_0.72 | 11.610 [9.979, 12.952] | 7 [6, 9] | 0.0211 [0, 0.0667] | 218 / 220 |

The primary failure is local and conditional: A010 H.264 has a compressed score
landscape and repeated/less orderly timestamps, but it still produces complete GT-free
proposals. The final loss is extraction compatibility, not absence of affine evidence.

## 14. DIRECT/H264/SPEED_0_8X control

All three controls use SOURCE_A position 010. DCT returns a safe ambiguity containing
GT for each. DINO differs as follows:

| DINO measure | DIRECT | H264 | SPEED_0_8X |
|---|---:|---:|---:|
| Outcome | Safe ambiguity | Unsafe wrong | Safe ambiguity |
| GT-near frames / total | 15 / 16 | 13 / 16 | 18 / 20 |
| Median nearest rank | 5 | 5 | 2 |
| Correct max multiplicity | 7 | 8 | 9 |
| Effective diversity | 5.610 | 6.100 | 5.877 |
| Inversion+tie rate | 0.0500 | 0.0500 | 0.0368 |
| Median raw-cosine spread | 0.01784 | 0.01288 | 0.01971 |
| Nearest-oracle slope | 1.0132 | 1.0284 | 0.7937 |
| Nearest-oracle RMS (s) | 0.2190 | 0.2207 | 0.1479 |
| Complete proposals / extracted / final | 16 / 2 / 2 | 11 / 0 / 0 | 9 / 2 / 2 |
| First-loss stage | `NONE` | `EXTRACTION` | `NONE` |

The controls rule out simple explanations based only on candidate admission, median
nearest rank, or residual size. DIRECT and SPEED_0_8X preserve complete proposals
through extraction. H264 has complete proposals too, but a slightly higher-scored
compressed fit wins the first extraction round and changes the remaining pool. The
measured property is this proposal-order/inlier-removal interaction; the H.264 label is
not treated as a causal mechanism.

## 15. Broader DEV prevalence

The denominator is 220 positive transformation cells nested within 20 content
positions. TAU_0.72 outcomes are C/S/U/N = 82/57/56/25. A complete mapping exists in
139 cells; 81 cells lack one.

| Mechanism among 81 failed cells | Cells | Rate |
|---|---:|---:|
| Extraction loss E | 37 | 45.7% |
| Separability limit R | 16 | 19.8% |
| Neither / unresolved | 28 | 34.6% |
| E+R in the same cell | 0 | 0% |

E appears in 13/20 positions and R in 11/20. Nine positions contain at least one E
cell and a different R cell; this is not a mixed mechanism within one cell.

| Existing failure attribution | Cells | E | R | Main registered first-loss evidence |
|---|---:|---:|---:|---|
| ALIGNMENT_LIMIT | 34 | 26 | 0 | 25 extraction, 1 suppression, 8 complete final |
| No prior failure | 125 | 0 | 0 | 125 complete final |
| PIP_GLOBAL_EMBEDDING_LIMIT | 17 | 0 | 8 | 8 oracle, 9 sparse-peak losses |
| RETRIEVAL | 5 | 0 | 3 | 3 oracle, 2 support/fit losses |
| SCORE_CONTRACT | 8 | 0 | 2 | 2 oracle, 6 sparse-peak losses |
| SCORE_CONTRACT_OR_ALIGNMENT_LIMIT | 21 | 7 | 3 | 7 extraction, 3 oracle, 11 support/fit |
| TRUE_VISUAL_AMBIGUITY | 10 | 4 | 0 | 4 extraction, 6 complete final |

Extraction loss appears in CENTER_CROP 6/20, DIRECT 2/20, H264 3/20, LETTERBOX 8/20,
MIRROR 4/20, SUBTITLE 1/20, VERTICAL 5/20, and ZOOM 8/20. It is absent in both speed
controls and PIP. R is concentrated in PIP (8/20) and VERTICAL (6/20), with one each
in CENTER_CROP and SUBTITLE. These are descriptive DEV counts; they neither authorize
PIP work nor estimate future-media prevalence.

## 16. Synthetic candidate-cloud probes

Each deterministic synthetic setting contains 16 query frames, top-k 8, a known
slope-1 line, and exactly 0.5-second-grid-aligned source timestamps. Only one registered
axis changes at a time.

| Axis | Registered grid | First loss of complete correct mapping | Extraction-loss E |
|---|---|---|---:|
| Distributed duplicate frames | 0, 4, 8, 12, 16 | None | 0 settings |
| Local plateau frames | 0, 2, 4, 6, 8, 12, 16 | None | 0 settings |
| Correct-line noise (s) | 0, 0.5, 1.0, 1.5 | None | 0 settings |
| Active distractors/frame | 0, 1, 3, 7 | None | 0 settings |
| Parallel competitor score | 0.80, 0.82, 0.90, 0.96, 0.99 | None; 0.96/0.99 become ambiguous but retain GT | 0 settings |
| Timestamp quantization (s) | 0.5, 1, 2, 4 | 4 seconds, at `SPARSE_PEAK` | 0 settings |

Increasing duplicate concentration or plateau length alone does not reproduce A010's
extraction failure. Extreme 4-second timestamp collision does remove the correct
mapping, but its first loss is sparse-peak formation, a representation/geometry failure
different from A010's E-only path. Synthetic outputs replay byte-for-byte under the
sanitized contract.

## 17. Evaluator completeness analysis

Evaluator rejection is physically justified. Both DINO correct-source alternatives
cover the full accused interval and select the correct episode, so accused assignment
and source identity pass. Slope 0.751 misses the source end by 1.2128 seconds; slope
0.789 misses the source start by 1.6232 seconds. Both fail the independent
duration/slope-consistency check. Their errors are not merely a shared mapping just
outside one arbitrary endpoint tolerance.

The nearest-per-frame oracle and 11 already-generated GT-free proposals pass the same
unchanged evaluator. Widening slope or boundary tolerances would therefore conceal a
real geometric error and is neither required nor permitted by the evidence.

## 18. Wrong-GREEN sidebar

The registered SOURCE_A position 001 VERTICAL_9_16 sidebar is unchanged:

| Backend | Outcome / status | Candidate tier | Production review tier |
|---|---|---|---|
| DCT | NO_MATCH / MATCH_NOT_FOUND | none | none |
| TAU_0.68 | UNSAFE_WRONG_MATCH / MATCH_FOUND | GREEN | REVIEW_REQUIRED |
| TAU_0.72 | UNSAFE_WRONG_MATCH / MATCH_FOUND | YELLOW | REVIEW_REQUIRED |

TAU_0.68's wrong-source GREEN remains a safety warning against treating candidate tier
as release authority. No score threshold was selected or changed. Every emitted result
in this sidebar remains `REVIEW_REQUIRED`.

## 19. Runtime

The final deterministic run traced all 220 TAU_0.72 positive cells, measured all 220
paired DCT cells, generated six detailed primary traces, and ran the registered
synthetic grid in 16.18 seconds on the local host. The number is a characterization
harness runtime, not a production throughput benchmark. A second run matched the first
after removing only completion timestamp and runtime fields.

## 20. Tests / reproducibility

The final suite contains 205 tests: the 187-test baseline plus 18 Phase 2B-4 focused
tests. Focused coverage includes formulas, identity multiplicity, entropy/diversity,
order statistics, oracle fitting, actual-vs-oracle comparison, exact JSON replay,
trace determinism, all six synthetic grids, protocol provenance, production freeze,
fresh-artifact refusal, and sanitization.

The independent verification artifact records:

- exact aggregate regeneration from ignored raw output;
- exact deterministic synthetic replay;
- a complete six-cell primary DCT/DINO trace matrix;
- 440 paired positive DEV cells;
- 56,640 audited candidates, with zero off-grid source timestamps;
- empty production/config diff, no selected policy, and no fresh data;
- all emitted production review tiers still `REVIEW_REQUIRED`.

Reproduction commands are documented in `experiments/phase2b4/README.md`. Raw output
uses exclusive creation and is never overwritten.

## 21. Privacy

Committed machine-readable results contain SOURCE_A/SOURCE_B aliases, position aliases,
normalized source positions, aggregate counts/rates, slopes, residuals, and boundary
errors. They contain no exact real source/query timeline, per-frame record, raw
candidate, frame, embedding, media path/name, model/index path, database, SHA-256, or
private 64-character fingerprint.

Detailed candidate identities and transition membership remain under ignored
`workspace/phase2b4/`. A recursive sanitizer rejects forbidden keys and private path,
media, model/index, timeline, and hash patterns before writing the committed aggregate.

## 22. Limitations

- The independent observational unit is 20 already-observed DEV positions from two
  sources. There is no held-out sample, population confidence interval, or five-episode
  stability claim.
- The oracle uses GT and only establishes that structure exists; it is not an available
  runtime selection rule.
- E means a complete proposal was generated and later lost. It does not by itself
  specify a safe remediation or prove that one will generalize.
- R is a registered diagnostic flag based on oracle failure or four paired indicators.
  It is not a learned causal classifier.
- The controlled synthetic grid did not reproduce A010's extraction loss. It bounds
  several simple explanations but does not exhaust real candidate-cloud geometry.
- TAU_0.72 is diagnostic-only. No result here supplies a production DINO score contract.
- Detailed membership evidence is intentionally excluded from Git for privacy, so an
  independent reviewer needs the ignored DEV workspace to reproduce the deepest trace.
- The harness records private methods of the frozen aligner. It is intentionally tied
  to the frozen implementation and is not production API surface.

## 23. Architecture recommendation

The pre-registered rule selects **Option A — NARROW TEMPORAL ALIGNMENT /
ALTERNATIVE-EXTRACTION COMPATIBILITY REMEDIATION**, for a future separately authorized
phase. The primary case is E only, and E recurs in 36 additional DEV cells. Option C is
not selected because no cell is E+R and the synthetic grid does not separately
reproduce extraction loss; the existence of 16 R cells remains a secondary, separate
finding. This recommendation does not authorize an implementation and does not close
Phase 2B.

**Q1. Does A010 H264 contain a near-correct affine structure in the retrieved DINO
candidate cloud?** Yes. The nearest oracle is complete at slope 1.0284/RMS 0.2207
seconds, and the frozen GT-free machinery generates 11 distinct complete proposals.

**Q2. Can that structure be identified without GT from ordinary candidate geometry?**
Yes in the limited, directly observed sense that the frozen GT-free proposal generator
already emits complete mappings. GT is used only afterward to label completeness. The
current extractor does not reliably choose them, so this is not a production solution.

**Q3. At which exact frozen stage does the correct mapping lose?** `EXTRACTION`.
Complete proposals exist before `_extract_episode_segments`; no complete extraction
output or final alternative remains.

**Q4. Why are the extracted slopes approximately 0.751/0.789?** The 0.751 proposal has
a 0.9530 leading quality versus 0.9524 for complete alternatives, so it wins the first
lexicographic extraction round and removes its 15 inliers. The reduced second-round
pool then favors 0.789 at 0.9515; complete proposals are no longer available.

**Q5. What most clearly distinguishes DCT from DINO on A010 H264?** The strongest
single measurement is correct-source raw-cosine spread, 0.2077 versus 0.01288. DINO
also has twice the maximum correct timestamp multiplicity, lower nearest ranks, three
missing GT-near frames, and non-zero order error. DINO nonetheless contains complete
affine proposals, so these differences are context for the extraction competition,
not proof of a representation-only failure.

**Q6. Why do DIRECT and SPEED_0_8X pass while H264 fails?** All three produce complete
GT-free proposals. DIRECT and SPEED_0_8X preserve two through extraction/final output;
H264 preserves none because a narrowly higher-quality compressed proposal wins first
and changes the remaining candidate pool. Candidate admission, median nearest rank, or
oracle RMS alone does not explain the difference.

**Q7. How prevalent is the same pattern elsewhere on DEV?** E occurs in 37/220 cells,
including 36 beyond the primary case, across 13/20 positions. Among the 81 cells lacking
a complete final mapping, E accounts for 45.7%. These are nested DEV counts, not a
population estimate.

**Q8. Do duplicate concentration or lower temporal diversity reproduce the failure
synthetically?** Not by themselves. Distributed duplicates through 16/16 frames and
local plateaus through 16/16 retain a complete mapping. Four-second quantization causes
a different `SPARSE_PEAK` loss; no synthetic setting produces E.

**Q9. Is evaluator rejection physically justified?** Yes. The mappings have slopes
0.751/0.789 for a slope-1 truth, fail duration/slope consistency, and each misses one
source endpoint by more than one second. The evaluator is not relaxed.

**Q10. Which next branch does the registered rule select?** Option A. It is a bounded
recommendation for future authorization, not an implementation decision. Production
remains frozen and review-required while independent review and the Architecture Owner
decide the next state.
