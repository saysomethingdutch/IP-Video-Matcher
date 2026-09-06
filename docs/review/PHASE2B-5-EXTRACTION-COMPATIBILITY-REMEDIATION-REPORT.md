# Phase 2B-5 near-tie extraction stability and rival survival remediation

## 1. Executive Summary

Phase 2B-5 completed the pre-registered seven-policy experiment, but selected no
policy. The dedicated synthetic fixture reproduced the Phase 2B-4 extraction loss.
Only P4 (frozen ranking with `INLIER_ONLY` occupancy) passed every synthetic safety
condition; P4 then failed the mandatory A010 H264 regression and left extraction loss
in 11 content positions, above the gate of 10.

P5 was the best diagnostic DEV policy under the pre-registered lexicographic key. On
DINO DEV it changed safe/unsafe/no-match from 139/56/25 to 163/32/25 and E from 37 to
16, but it was ineligible: its clean synthetic 1.25x mapping was not evaluator-complete
and E still covered 11 content positions. No selected experimental policy artifact was
created. Fresh validation and production modification remain unauthorized.

## 2. Architecture scope

The experiment starts after proposal generation and changes only two extraction
decisions in an experimental `TemporalAligner` subclass:

- near-tie proposal selection within a q-max-relative shortlist; and
- exact-inlier-only occupancy instead of line-band occupancy.

Sparse voting, peaks, support, continuous runs, robust fit, merge, change-point logic,
the evaluator, boundary completeness, review tier, DINO, DCT, FPS, top-k, and the 0.82
admission threshold remain frozen. All implementation is under
`experiments/phase2b5/`; production `src/ipvm/` and `config/` are unchanged.

## 3. Phase 2B-4 validated premise

The registered A010 H264 DINO case contained 11 distinct evaluator-complete proposals
in round 0. Frozen ranking chose an incomplete slope-0.751 proposal because its
explanation quality, 0.9530, exceeded the complete candidates' 0.9524. Line-band
occupancy removed 20 candidates; recomputation then produced no complete proposal.
The failure was therefore near-tie ordering plus destructive occupancy plus refit
instability, not complete exhaustion of correct inliers.

## 4. Protocol commit

The protocol-only commit is
`54531ce7e268b20c1292c9b3721b1307eb072835` (`Pre-register Phase 2B-5 extraction
compatibility remediation`). `git diff-tree` shows only
`experiments/phase2b5/PROTOCOL.md`. The protocol was not amended after synthetic or DEV
results became visible.

## 5. Production freeze

`git diff 93bf13b -- src/ config/` is empty. P0 replayed all 480 stored DEV records
(220 positive plus 20 negative records for each of DCT and TAU_0.72) at the JSON
persistence boundary. Production GREEN auto-release remains disabled; every emitted
primary and alternative mapping in this experiment has `review_tier=REVIEW_REQUIRED`.

## 6. Fresh-data preservation

Only the existing Phase 2B-3 DEV artifact was read. The harness rejects non-DEV input
and refuses to run when a fresh, held-out, or selected-score-contract artifact appears
in the registered input area. No fresh data was generated, decoded, inspected, or
evaluated. No `selected-score-contract.json` exists.

## 7. Frozen extraction mechanism

For each episode, frozen extraction repeatedly generates peak-support-run fits,
selects the maximum `_fit_quality` tuple, claims a line band on the selected inlier
frames, and regenerates proposals from the remaining candidates. The experimental
wrapper delegates every generation and fit operation to frozen helpers. Its only
behavioral seams are `choose_fit()` and occupancy; a trace-only proposal hook delegates
unchanged to the production fit implementation.

## 8. Registered policy family

| Policy | Selection | Epsilon | Occupancy |
|---|---|---:|---|
| P0 | frozen | null | LINE_BAND |
| P1 | near-tie geometry | 0.0005 | LINE_BAND |
| P2 | near-tie geometry | 0.0010 | LINE_BAND |
| P3 | near-tie geometry | 0.0020 | LINE_BAND |
| P4 | frozen | null | INLIER_ONLY |
| P5 | near-tie geometry | 0.0010 | INLIER_ONLY |
| P6 | near-tie geometry | 0.0020 | INLIER_ONLY |

No eighth policy or post-result epsilon was tested.

## 9. Near-tie shortlist semantics

The shortlist is inclusive and relative to the single round maximum:
`q >= q_max - epsilon`. It is not a pairwise epsilon comparator. Within that shortlist,
the fixed descending key is inlier ratio, negative RMS, accused span, inlier count,
explanation quality, and negative original proposal ordinal. Tests cover the inclusive
boundary, the non-transitive three-value case, geometry priority, and deterministic
ties. Runtime selection has no truth or evaluator input.

## 10. Inlier-only occupancy semantics

`INLIER_ONLY` removes only candidate objects present in the selected fitted proposal's
actual inlier list. Object identity is backed by the original input ordinal. A
regression test constructs two value-equal `CandidateMatch` instances and confirms
that selecting one does not remove the other. Minimum support, fit tolerance, and
proposal regeneration are unchanged.

## 11. Dedicated synthetic extraction-loss reproducer

The anonymous 16-frame, top-k-8, 2-FPS fixture is deterministic and fully source-grid
aligned. Under P0, round 0 contains four distinct evaluator-complete mappings. P0
selects an incomplete slope-0.8353 mapping at quality 0.9530 and finishes with zero
complete final mappings. The registered exact slope-1 line has quality 0.9524, so the
registered gap is 0.0006 and lies inside the pre-registered range.

The closest complete round-0 proposal is only 0.0003 below q-max. This does not alter
the registered gate: P1 includes that candidate but its geometry tuple selects another
incomplete proposal, so P1 still fails to restore a complete final mapping.

## 12. Synthetic safety probes

Every fixture contains 16 frames, top-k 8, and grid-aligned source timestamps. P0
exactly equals production on all nine fixtures. The table reports
`complete-final mappings / total final mappings`.

| Policy | Extraction loss | Clean 1x | Clean .8x | Clean 1.25x | Strong rivals | Wrong line | Parallel | Duplicate | Plateau |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P0 | 0/2 | 1/1 | 1/1 | 1/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |
| P1 | 0/2 | 1/1 | 1/1 | 0/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |
| P2 | 1/1 | 1/1 | 1/1 | 0/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |
| P3 | 1/1 | 1/1 | 1/1 | 0/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |
| P4 | 1/2 | 1/1 | 1/1 | 1/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |
| P5 | 1/2 | 1/1 | 1/1 | 0/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |
| P6 | 1/2 | 1/1 | 1/1 | 0/1 | 1/2 A | 0/1 R | 1/2 A | 1/1 | 1/1 |

`A` means AMBIGUOUS; `R` means the coherent wrong output remains
`REVIEW_REQUIRED`. No fixture exceeded twice its P0 mapping count.

## 13. Synthetic eligibility matrix

| Policy | Restores loss | Clean 1/.8/1.25 | Rival ambiguity | Wrong-line review | Bounded | Synthetic gate |
|---|---|---|---|---|---|---|
| P1 | FAIL | PASS/PASS/FAIL | PASS | PASS | PASS | FAIL |
| P2 | PASS | PASS/PASS/FAIL | PASS | PASS | PASS | FAIL |
| P3 | PASS | PASS/PASS/FAIL | PASS | PASS | PASS | FAIL |
| P4 | PASS | PASS/PASS/PASS | PASS | PASS | PASS | PASS |
| P5 | PASS | PASS/PASS/FAIL | PASS | PASS | PASS | FAIL |
| P6 | PASS | PASS/PASS/FAIL | PASS | PASS | PASS | FAIL |

Synthetic eligibility was fixed before DEV. Later DEV improvement cannot rehabilitate
P1, P2, P3, P5, or P6.

## 14. Baseline DEV replay

P0 reproduced the registered DINO baseline C/S/U/N = 82/57/56/25, safe 139,
E 37 across 13 positions. It also reproduced DCT C/S/U/N = 51/37/58/74,
safe 88, and E 6 across five positions. The 220 positive cells per backend are nested
within 20 content positions; neither transformations nor policies create independent
observations.

## 15. Full DINO policy matrix

`wrong G` is wrong-source GREEN segments / ambiguous wrong-source GREEN segments /
unambiguous wrong-source GREEN cases. `maps` is primary / alternatives.

| P | C | S | U | N | safe | MF/A/MNF | G/Y | wrong G | maps | complete | E/pos | R/unresolved | primary inflation | max maps | runtime s |
|---|---:|---:|---:|---:|---:|---|---|---|---|---:|---|---|---:|---:|---:|
| P0 | 82 | 57 | 56 | 25 | 139 | 105/90/25 | 76/133 | 0/0/0 | 209/138 | 167 | 37/13 | 16/28 | 1.000 | 6 | 13.60 |
| P1 | 82 | 59 | 54 | 25 | 141 | 106/89/25 | 77/132 | 0/0/0 | 209/135 | 166 | 37/13 | 16/28 | 1.000 | 6 | 13.42 |
| P2 | 82 | 61 | 52 | 25 | 143 | 105/90/25 | 80/131 | 0/0/0 | 211/137 | 172 | 35/12 | 16/28 | 1.010 | 6 | 13.49 |
| P3 | 86 | 61 | 48 | 25 | 147 | 108/87/25 | 85/122 | 0/0/0 | 207/136 | 174 | 32/13 | 16/28 | 0.990 | 6 | 13.35 |
| P4 | 48 | 108 | 39 | 25 | 156 | 61/134/25 | 36/173 | 0/0/0 | 209/269 | 221 | 21/11 | 16/28 | 1.000 | 7 | 20.29 |
| P5 | 50 | 113 | 32 | 25 | 163 | 63/132/25 | 42/169 | 0/0/0 | 211/266 | 230 | 16/11 | 16/28 | 1.010 | 6 | 20.91 |
| P6 | 54 | 107 | 34 | 25 | 161 | 66/129/25 | 47/159 | 0/0/0 | 206/249 | 228 | 18/13 | 16/28 | 0.986 | 6 | 20.85 |

By transformation, each entry is `safe/U/N/E` out of 20 positions:

| Transformation | P0 | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|---|
| CENTER_CROP | 9/11/0/6 | 7/13/0/8 | 8/12/0/7 | 8/12/0/7 | 11/9/0/4 | 12/8/0/3 | 10/10/0/5 |
| DIRECT | 18/2/0/2 | 19/1/0/1 | 19/1/0/1 | 20/0/0/0 | 19/1/0/1 | 20/0/0/0 | 20/0/0/0 |
| H264_REENCODE | 17/3/0/3 | 18/2/0/3 | 19/1/0/2 | 19/1/0/2 | 17/3/0/3 | 19/1/0/1 | 19/1/0/1 |
| LETTERBOX | 10/9/1/8 | 11/8/1/8 | 11/8/1/8 | 9/10/1/10 | 13/6/1/5 | 14/5/1/4 | 13/6/1/5 |
| MIRROR | 16/4/0/4 | 17/3/0/3 | 18/2/0/2 | 19/1/0/1 | 19/1/0/1 | 20/0/0/1 | 20/0/0/1 |
| PIP_REDUCED_VIEWPORT | 0/0/20/0 | 0/0/20/0 | 0/0/20/0 | 0/0/20/0 | 0/0/20/0 | 0/0/20/0 | 0/0/20/0 |
| SPEED_0_8X | 20/0/0/0 | 20/0/0/0 | 19/1/0/1 | 19/1/0/1 | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 |
| SPEED_1_25X | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 |
| SUBTITLE_OVERLAY | 17/3/0/1 | 16/4/0/2 | 16/4/0/2 | 16/4/0/2 | 17/3/0/1 | 17/3/0/1 | 17/3/0/1 |
| VERTICAL_9_16 | 2/14/4/5 | 2/14/4/5 | 2/14/4/5 | 4/12/4/4 | 6/10/4/1 | 6/10/4/1 | 6/10/4/1 |
| ZOOM | 10/10/0/8 | 11/9/0/7 | 11/9/0/7 | 13/7/0/5 | 14/6/0/5 | 15/5/0/5 | 16/4/0/4 |

## 16. Full DCT policy matrix

| P | C | S | U | N | safe | MF/A/MNF | G/Y | wrong G | maps | complete | E/pos | R/unresolved | primary inflation | max maps | runtime s |
|---|---:|---:|---:|---:|---:|---|---|---|---|---:|---|---|---:|---:|---:|
| P0 | 51 | 37 | 58 | 74 | 88 | 85/61/74 | 57/107 | 0/0/0 | 164/69 | 112 | 6/5 | 108/19 | 1.000 | 4 | 7.39 |
| P1 | 51 | 37 | 58 | 74 | 88 | 85/61/74 | 57/107 | 0/0/0 | 164/70 | 110 | 7/5 | 108/19 | 1.000 | 4 | 7.61 |
| P2 | 52 | 36 | 58 | 74 | 88 | 86/60/74 | 60/103 | 0/0/0 | 163/69 | 110 | 7/5 | 108/19 | 0.994 | 4 | 7.60 |
| P3 | 51 | 35 | 60 | 74 | 86 | 87/59/74 | 62/102 | 0/0/0 | 164/66 | 104 | 10/7 | 108/19 | 1.000 | 4 | 7.60 |
| P4 | 28 | 62 | 56 | 74 | 90 | 60/86/74 | 35/131 | 0/0/0 | 166/129 | 146 | 4/4 | 108/19 | 1.012 | 5 | 12.43 |
| P5 | 29 | 60 | 57 | 74 | 89 | 61/85/74 | 38/126 | 0/0/0 | 164/123 | 142 | 5/5 | 108/19 | 1.000 | 5 | 12.39 |
| P6 | 29 | 59 | 58 | 74 | 88 | 61/85/74 | 38/129 | 0/0/0 | 167/118 | 132 | 6/6 | 108/19 | 1.018 | 7 | 12.42 |

By transformation, each entry is `safe/U/N/E` out of 20 positions:

| Transformation | P0 | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|---|
| CENTER_CROP | 0/6/14/1 | 0/6/14/1 | 0/6/14/1 | 0/6/14/1 | 0/6/14/1 | 0/6/14/1 | 0/6/14/1 |
| DIRECT | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 |
| H264_REENCODE | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 | 18/2/0/0 |
| LETTERBOX | 0/10/10/0 | 0/10/10/0 | 0/10/10/0 | 0/10/10/0 | 0/10/10/0 | 0/10/10/0 | 0/10/10/0 |
| MIRROR | 0/2/18/0 | 0/2/18/0 | 0/2/18/0 | 0/2/18/0 | 0/2/18/0 | 0/2/18/0 | 0/2/18/0 |
| PIP_REDUCED_VIEWPORT | 0/13/7/0 | 0/13/7/0 | 0/13/7/0 | 0/13/7/0 | 0/13/7/0 | 0/13/7/0 | 0/13/7/0 |
| SPEED_0_8X | 17/3/0/3 | 17/3/0/4 | 18/2/0/3 | 17/3/0/5 | 19/1/0/1 | 19/1/0/1 | 18/2/0/2 |
| SPEED_1_25X | 19/1/0/0 | 19/1/0/0 | 19/1/0/0 | 18/2/0/1 | 19/1/0/0 | 19/1/0/0 | 19/1/0/0 |
| SUBTITLE_OVERLAY | 12/5/3/1 | 12/5/3/1 | 12/5/3/1 | 12/5/3/1 | 12/5/3/1 | 12/5/3/1 | 12/5/3/1 |
| VERTICAL_9_16 | 0/1/19/0 | 0/1/19/0 | 0/1/19/0 | 0/1/19/0 | 0/1/19/0 | 0/1/19/0 | 0/1/19/0 |
| ZOOM | 4/13/3/1 | 4/13/3/1 | 3/14/3/2 | 3/14/3/2 | 4/13/3/1 | 3/14/3/2 | 3/14/3/2 |

## 17. A010 H264 regression

| Policy | Round-0 winner slope | quality | inliers | RMS | occupancy | round-1 complete proposals | final complete | outcome |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| P0 | 0.7510 | 0.9530 | 15 | 0.2387 | 20 | 0 | 0 | U |
| P2 | 1.0000 | 0.9524 | 12 | 0.0000 | 20 | 6 | 2 | S |
| P4 | 0.7510 | 0.9530 | 15 | 0.2387 | 15 | 2 | 0 | U |
| P5 | 1.0000 | 0.9524 | 12 | 0.0000 | 12 | 9 | 2 | S |
| P6 | 1.0000 | 0.9524 | 12 | 0.0000 | 12 | 9 | 3 | S |

Near-tie selection at epsilon 0.001 or 0.002 fixes the registered case. Occupancy
alone preserves some complete round-1 proposals but still selects an incomplete fit
and produces no complete final mapping. P5's winner is the exact slope-1 proposal
because all shortlisted candidates have equal inlier ratio and it has minimum RMS.
Its output remains AMBIGUOUS and every mapping remains review-required.

## 18. DIRECT/H264/SPEED controls

For DINO A010, the entries show final outcome and evaluator-complete mapping count.

| Policy | DIRECT | H264 | SPEED .8x |
|---|---|---|---|
| P0 | S / 2 | U / 0 | S / 2 |
| P2 | S / 2 | S / 2 | S / 3 |
| P4 | S / 2 | U / 0 | S / 3 |
| P5 | S / 2 | S / 2 | S / 3 |

P5 passes these three DEV controls. It nevertheless fails the separately registered
synthetic 1.25x safety condition, so the controls cannot make it eligible.

## 19. Extraction-loss E remediation

On all DINO cells, E changes as follows:

- ranking only: P1 37, P2 35, P3 32;
- occupancy only: P4 21;
- combined: P5 16, P6 18.

P4 reduces E by 16 cells and two content positions. P5 reduces E by 21 cells but only
two positions, leaving 11 positions and missing the registered maximum of 10. The
position-level matrix is stored in the sanitized aggregate. For P0/P4/P5, the number
of positions with any E is 13/11/11; improvements are concentrated rather than broad
enough to pass the prevalence gate.

## 20. E paired transitions

P5 is the pre-registered diagnostic target because no policy qualified. For the 37 P0
E cells, its frozen taxonomy transitions are 1 C, 24 S, 12 U, and 0 N. It leaves 15 of
those cells classified E across 10 of the original E positions and creates no new
unsafe transition within the E subset.

Mechanism completeness is stricter than the C/S taxonomy. Only 22 of the 37 cells
finish with an evaluator-complete mapping; three further cells are S only because the
frozen alternative predicate accepts a mapping that the stricter Phase 2B-4
fit-completeness diagnostic rejects. Accordingly, “25 C/S transitions” and “22
evaluator-complete survivals” are reported separately.

Across all 220 cells, the P0-to-P5 matrix is:

| From / to | C | S | U | N |
|---|---:|---:|---:|---:|
| C | 49 | 33 | 0 | 0 |
| S | 0 | 56 | 1 | 0 |
| U | 1 | 24 | 31 | 0 |
| N | 0 | 0 | 0 | 25 |

Thus one previously safe non-E cell becomes unsafe. Aggregate unsafe still falls from
56 to 32, but the paired regression is not hidden.

## 21. Ranking-vs-occupancy attribution

Against P0 on DINO, the strongest ranking-only policy (P3) changes safe by +8 and E by
-5. Occupancy-only P4 changes safe by +17 and E by -16. Adding occupancy at epsilon
0.001 (P2 to P5) changes safe by +20 and E by -19; P0 to P5 is +24 safe and -21 E.
The dominant measured contribution is occupancy, with an additional interaction from
near-tie ranking. This is a paired DEV contrast, not a population causal estimate.

The combined policy is numerically stronger on DEV but is not better under the full
protocol because it fails clean 1.25x synthetic behavior and the E-position gate.

## 22. Wrong-GREEN safety

Every policy has zero wrong-source GREEN segments, zero ambiguous wrong-source GREEN
segments, and zero new unambiguous wrong-source GREEN cases on both DINO and DCT DEV.
P5 has 42 DINO primary GREEN segments with diagnostic precision 0.9286 and 38 DCT
primary GREEN segments with diagnostic precision 0.7105. The less-than-perfect
precision reflects wrong-time/same-episode cases not covered by the wrong-source hard
gate. Candidate GREEN remains internal-only; all release decisions remain
`REVIEW_REQUIRED`.

## 23. Segment/alternative inflation

Every policy stays below the 1.25 primary-segment cap. P5 ratios are 1.010 for DINO and
1.000 for DCT. Its alternative count grows from 138 to 266 on DINO and from 69 to 123
on DCT. This is bounded by the registered primary-count gate but materially increases
review load.

Manual inspection found 15 P5 DINO cases and five P5 DCT cases above four total
mappings. Every one is AMBIGUOUS and every mapping is review-required; maximum total
mappings are six and five, respectively. Their anonymous aliases are retained in the
sanitized aggregate. The concentration includes A010 DIRECT/MIRROR/speed controls,
several SOURCE_A positions, and SOURCE_B position 004/005 controls; it is not a silent
unambiguous explosion.

## 24. DEV negatives

For every one of the seven policies, both DINO and DCT replay 20 existing synthetic
negative queries with zero emitted primary segments. Status is `MATCH_NOT_FOUND`; no
candidate tier or review tier is emitted. These are synthetic safety controls, not
fresh real-negative evidence.

## 25. Boundary/speed effects

| Backend/policy | source-boundary MAE s | accused-boundary MAE s | speed MAE | completeness INCOMPLETE/UNKNOWN |
|---|---:|---:|---:|---|
| DINO P0 | 0.4858 | 0.2188 | 0.03145 | 5/204 |
| DINO P4 | 0.4964 | 0.2148 | 0.03145 | 5/204 |
| DINO P5 | 0.4958 | 0.2201 | 0.03409 | 5/206 |
| DCT P0 | 50.5361 | 0.4552 | 0.03438 | 2/162 |
| DCT P4 | 50.5371 | 0.4552 | 0.03438 | 2/164 |
| DCT P5 | 50.5400 | 0.4534 | 0.04888 | 2/162 |

Extraction remediation does not improve boundary or speed error. DCT source-boundary
MAE is dominated by existing large wrong-timeline errors and should not be read as an
estimate from independent samples. Boundary completeness remains production-derived;
this phase does not change it.

## 26. Runtime

The full seven-policy, two-backend DEV run took 195.11 seconds on the local test host.
P0 alignment time was 13.60 seconds for DINO and 7.39 seconds for DCT; P5 was 20.91
and 12.39 seconds. The experimental full proposal trace is diagnostic overhead and is
not a production performance claim.

## 27. GT-leakage probes

AST/source checks find none of `truth`, `ground_truth`, `expected_source`,
`expected_speed`, `complete`, or `is_correct` in runtime selector and occupancy
inputs or local names. The runtime modules import no evaluator. Mutating GT episode,
boundaries, and speed after candidate generation leaves raw mappings, alternatives,
warnings, and round traces byte-identical; only post-runtime labels change.

## 28. Tests/reproducibility

`pytest -q` reports 226 passed in 10.55 seconds, compared with the 205-test baseline.
Focused coverage includes the protocol matrix, q-max shortlist, non-transitive case,
geometry tuple, deterministic tie, identity occupancy, P0 equality, all synthetic
fixtures, eligibility/selection rules, A010 artifact regression, GT mutation, static
leakage, fresh guard, production guard, and sanitizer.

The verifier independently rebuilds the committed aggregate, replays synthetic
fixtures, checks 480 P0 records, checks all 3,360 DEV policy/backend records, recomputes
eligibility and the diagnostic-best policy, and confirms that no selected artifact
exists. The registered candidate audit contains zero off-grid timestamps.

## 29. Privacy

Detailed DEV rows, exact source/accused timelines, proposal membership, candidate
ordinals, code/input fingerprints, and E-level traces remain under ignored workspace.
Committed artifacts contain only aliases, normalized positions, aggregate metrics,
policy parameters, sanitized slopes/residuals/counts, and public Git provenance. The
recursive sanitizer rejects private paths, media suffixes, exact timeline keys,
candidate records, fingerprints, and 64-character private hashes.

## 30. Limitations

- The evidence is DEV-only: 220 transformation cells are nested in 20 content
  positions and are not 220 independent observations.
- The synthetic fixture reproduces the mechanism, not the media or candidate cloud.
- Only P4 passes synthetic safety, but P4 does not fix A010 H264.
- P5 produces the best aggregate DEV metrics but fails clean 1.25x and E-position
  gates; its extra alternatives increase human review load.
- Frozen C/S alternative semantics are slightly broader than the Phase 2B-4
  evaluator-complete mapping diagnostic, producing the disclosed 25-versus-22 count.
- Coherent wrong lines remain acceptable to the temporal layer and review-required;
  this phase does not solve representation errors.
- PIP remains a retrieval failure. It did not trigger ROI, tile, or any scope change.

## 31. Policy selection

No policy passes synthetic + DINO + DCT + inflation gates. P4 fails A010 H264 and the
E-position gate. P5 is the diagnostic best under the registered selection key, but it
fails synthetic eligibility and the E-position gate. P3 also regresses DCT. Therefore:

- `selected_experimental_policy = null`;
- no selected experimental policy artifact exists;
- no fresh validation is authorized; and
- no production promotion is authorized.

The final experiment commit is the commit containing this report, with subject
`Remediate near-tie extraction stability for Phase 2B-5`; its immutable hash is read
from Git history after commit creation.

## 32. Architecture recommendation

Recommendation: **C — another narrow extraction remediation may be justified, but the
current family did not close the issue.** Occupancy materially improves evaluator-
complete survival, so the Phase 2B-4 mechanism remains actionable. The registered
family nonetheless provides no policy safe enough to advance to fresh validation.
Defining another phase, mechanism, and gate is an Architecture Owner decision; this
experiment does not propose or implement that mechanism.

### Required questions

**Q1. Could BASELINE reproduce the registered extraction loss?** Yes. Four complete
round-0 mappings exist and zero complete final mapping survives.

**Q2. Which policies pass the synthetic gate?** P4 only.

**Q3. Does near-tie geometry selection fix A010 H264?** P2 and P3 do; P1 does not.
This does not make P2/P3 eligible because they fail clean synthetic 1.25x.

**Q4. Does INLIER_ONLY alone fix A010 H264?** No. P4 preserves two complete round-1
proposals but still returns no evaluator-complete final mapping.

**Q5. Is the combined mechanism better than either alone?** It is numerically best on
DEV, but not under the full gate. P5 fails synthetic 1.25x and E-position limits.

**Q6. How many of the 37 baseline E cells are rescued to C/S?** Frozen taxonomy says
25; the stricter evaluator-complete survival count is 22.

**Q7. How many become NO_MATCH?** Zero.

**Q8. How many become new unsafe outputs?** Zero within the baseline E subset; 12
remain U. Across the full matrix, one baseline S cell becomes U.

**Q9. How many E content positions remain?** P5 has E in 11 positions overall. Among
the original baseline E subset, retained E spans 10 positions.

**Q10. Does DINO keep safe >=139 and unsafe <=56?** Yes for P5 (163/32) and P4
(156/39), and in aggregate for every experimental policy.

**Q11. Does DCT remain non-regressed?** P4 and P5 do (90/56 and 89/57 versus 88/58).
P3 does not (86/60).

**Q12. Does any policy introduce a new unambiguous wrong-source GREEN?** No.

**Q13. Does mapping count remain bounded?** Primary counts pass the 1.25 cap. P5
alternatives rise materially; all >4-mapping cases are ambiguous and review-required.

**Q14. Does remediation preserve .8x and 1.25x?** P5 preserves .8x but fails the
registered synthetic 1.25x fixture. P4 preserves both but fails A010 H264.

**Q15. Are synthetic negatives clean?** Yes: zero emitted segments for every
policy/backend combination.

**Q16. Which mechanism contributes most?** Occupancy. P4 yields the largest
single-mechanism reduction; the P2-to-P5 contrast shows further interaction benefit.

**Q17. Was a policy selected?** No.

**Q18. What should follow?** C, if the Architecture Owner authorizes a newly
pre-registered narrow extraction experiment. Do not run fresh validation on this
family.
