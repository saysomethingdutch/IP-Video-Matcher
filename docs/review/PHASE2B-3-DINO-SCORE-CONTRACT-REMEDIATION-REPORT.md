# Phase 2B-3 DINO Score Contract Remediation Report

## 1. Executive Summary

Phase 2B-3 reached the pre-registered DEV gate and stopped there. None of the eight
global anchored-affine policies satisfied every eligibility constraint. No policy was
selected, no selection file was created, and no fresh position, transformed query, or
fresh negative was generated.

The score family did what it was designed to test: it admitted materially more of the
fixed DINO top-8 evidence and raised DEV safe success well above DCT. The closest
policies, `TAU_0.68` and `TAU_0.72`, also kept total unsafe no worse than DCT and passed
the direct, H.264, speed, geometry, and DEV-negative constraints. Both nevertheless
failed the mandatory A010 H.264 rival-preservation regression. The correct-source
candidate line remained present, but the frozen temporal/evaluator path did not retain
a complete correct mapping in alternatives.

The registered result is therefore:

```text
NO ELIGIBLE GLOBAL SCORE CONTRACT
```

This is not a fresh held-out result and not a global Phase 2B verdict. Architecture
recommendation Q18 is option C: retrieval remains strong, but score/rival safety is
still unresolved.

## 2. Architecture scope

All implementation stayed under `experiments/phase2b3/` plus one test module and
sanitized documentation/results. The experiment reused the frozen production
`TemporalAligner`, matcher-side DCT control, evaluator, configuration, source/query
sampling rate, and top-k. It did not alter production matching behavior.

No VDF runtime, FAISS, SigLIP, larger DINO model, learned calibration, per-transform
policy, per-source policy, ROI/tile retrieval, PIP repair, audio, ASR, Gemma, Excel, or
Phase 3 work was introduced.

## 3. Frozen Phase 2A baseline

The production closure remains the public Git commit `93bf13b`. A repository check
after the experiment returned an empty diff for `src/` and `config/` relative to that
baseline. Source/query FPS remains 2, top-k remains 8, candidate GREEN remains an
internal diagnostic, and every emitted production review tier remains
`REVIEW_REQUIRED`.

## 4. Phase 2B-1 validated retrieval premise

The experiment reused the Phase 2B-1 arrays and DINOv2-small INT8 representation from
public Git commit `dccec2820750126b46baabcacb14ce0caf828c7b`: 384-dimensional
L2-normalized CLS embeddings, exact NumPy cosine, top-k 8, and fixed ONNX batches of
16 with a natural short tail. It did not rerun a model-family comparison.

## 5. Phase 2B-2 failure diagnosis

Phase 2B-2 used raw DINO cosine as `visual_score`, so the frozen 0.82 gate imposed an
effective raw threshold of 0.82. Its floor family made that threshold stricter and
selected a policy that reduced unsafe cases mainly through abstention. The production
DCT path instead maps cosine as `(s + 1) / 2`, corresponding to a raw threshold of
0.64 at the same aligner gate.

Phase 2B-3 preserved this diagnosis and tested only a lower-threshold anchored-affine
family. It did not reopen temporal alignment.

## 6. Protocol-only commit evidence

The protocol was committed before runnable Phase 2B-3 code or DEV execution:

```text
277dea444545b5555f6bc4813334c6528df8a4cb
Pre-register Phase 2B-3 score contract protocol
```

`git diff-tree` confirms that commit contains only
`experiments/phase2b3/PROTOCOL.md`. The protocol file remained unchanged through the
run.

## 7. DEV vs fresh-held-out methodology

DEV comprised two anonymous sources, 20 already observed content positions, 11 paired
transformations per position, and 20 known synthetic negatives: 220 positive queries
and 20 negative queries. Each input ran through DCT and all eight DINO policies. This
produced 1,980 positive backend-policy evaluations, but the independent content count
is 20, not 1,980.

Fresh held-out was conditional on at least one eligible DEV policy. Because eligibility
failed, fresh held-out sample size is zero. Nothing in this report describes the old 20
positions as held-out.

## 8. Fresh-data exclusion algorithm

The committed protocol and harness define the deterministic seed `20260905`, five
positions per source, eight-second intervals, a 30-second guard at both media ends, a
30-second exclusion around every old interval, and 30 seconds between fresh intervals.
Legal temporal mass is divided into five strata per source and one guarded seeded draw
is mapped into each stratum. The harness has no scene-inspection or retry path.

This algorithm was implemented and tested against synthetic manifests, but it was not
called on the real source manifests because no policy reached the freeze stage.

## 9. DINO batch/model freeze

The run used DINOv2-small INT8 and fixed batch size 16. Observed DINO batch lengths
were 16, 13, and 4; no batch exceeded 16 and no padding batch was introduced. The DEV
identity record binds the adapter, benchmark, reporting code, frozen evaluator,
matching code, config, model, indexes, and DEV manifests in ignored workspace.

## 10. Anchored affine score contract

For raw DINO cosine `s` and anchor `tau`, every fixed top-8 candidate was mapped as:

```text
visual_score = clip(0.82 + (s - tau) * 0.18 / (1 - tau), 0, 1)
```

No candidate was manually removed before temporal alignment. Across DEV, 247,040
adapted DINO candidate records were checked: score mismatches, effective-threshold
mismatches, and non-retained candidates were all zero. The adapted value is not a
probability.

## 11. Mathematical special cases tau=.64/.82

Tests and post-run verification found:

- `tau=.64`: maximum numerical error relative to `(s+1)/2` was about
  `3.3e-16` on the probe grid.
- `tau=.82`: maximum numerical error relative to identity on `[0,1]` was zero.
- All eight mappings were monotonic, mapped `tau` to 0.82, mapped 1 to 1, and clipped
  to `[0,1]`.

## 12. Registered tau grid

The executed grid was exactly:

```text
.55, .60, .64, .68, .72, .76, .80, .82
```

No interpolation, post-result threshold, source-specific policy, or transform-specific
policy was evaluated.

## 13. DEV DCT baseline

On 220 DEV positives, DCT produced C/S/U/N = `51/37/58/74`, for safe success `88/220`
(40%). Status counts were 85 `MATCH_FOUND`, 61 `AMBIGUOUS`, and 74
`MATCH_NOT_FOUND`. It emitted 164 segments: 57 candidate GREEN and 107 YELLOW; every
segment remained `REVIEW_REQUIRED`. Its source-boundary MAE was 50.5361 seconds,
accused-boundary MAE 0.4552 seconds, and applicable speed MAE 0.03438. The large source
MAE is reported as produced by the frozen evaluator and was not suppressed or repaired.

All 20 known DEV synthetic negatives produced zero segments under DCT.

## 14. DEV full tau matrix

`Safe` is C+S. Boundary columns are emitted segment counts, not case counts.

| Policy | C | S | U | N | Safe | Status MF/A/NF | GREEN/YELLOW | Boundary incomplete/unknown | Source MAE | Accused MAE | Speed MAE | Predicted segments | Alternatives |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| DCT | 51 | 37 | 58 | 74 | 88 | 85/61/74 | 57/107 | 2/162 | 50.5361 | 0.4552 | 0.03438 | 164 | 69 |
| TAU_0.55 | 87 | 61 | 66 | 6 | 148 | 117/97/6 | 87/143 | 5/225 | 0.5285 | 0.2476 | 0.03216 | 230 | 144 |
| TAU_0.60 | 85 | 59 | 67 | 9 | 144 | 117/94/9 | 85/139 | 5/219 | 0.5058 | 0.2452 | 0.03216 | 224 | 142 |
| TAU_0.64 | 83 | 60 | 60 | 17 | 143 | 109/94/17 | 82/134 | 5/211 | 0.4842 | 0.1957 | 0.03170 | 216 | 143 |
| TAU_0.68 | 83 | 59 | 56 | 22 | 142 | 108/90/22 | 81/130 | 6/205 | 0.4811 | 0.1982 | 0.03095 | 211 | 137 |
| TAU_0.72 | 82 | 57 | 56 | 25 | 139 | 105/90/25 | 76/133 | 5/204 | 0.4858 | 0.2188 | 0.03145 | 209 | 138 |
| TAU_0.76 | 79 | 52 | 59 | 30 | 131 | 104/86/30 | 75/125 | 6/194 | 0.4739 | 0.2110 | 0.03096 | 200 | 129 |
| TAU_0.80 | 74 | 52 | 57 | 37 | 126 | 102/81/37 | 67/120 | 4/183 | 0.4789 | 0.2334 | 0.03014 | 187 | 122 |
| TAU_0.82 | 79 | 49 | 47 | 45 | 128 | 105/70/45 | 71/108 | 4/175 | 0.4494 | 0.2716 | 0.02983 | 179 | 111 |

Segment-count distributions (`0/1/2` segments, plus DCT's three-segment tail) moved
from DCT `74/131/12` with three 3-segment cases to `.55` `6/198/16`, `.64`
`17/190/13`, `.68` `22/185/13`, `.72` `25/181/14`, and `.82` `45/171/4`.
The sanitized aggregate contains the complete distributions for every policy.

The table below reports `safe/U/N` by transformation for all registered policies.

| Transform | DCT | .55 | .60 | .64 | .68 | .72 | .76 | .80 | .82 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CENTER_CROP | 0/6/14 | 9/11/0 | 9/11/0 | 9/11/0 | 9/11/0 | 9/11/0 | 6/13/1 | 5/13/2 | 7/10/3 |
| DIRECT | 18/2/0 | 18/2/0 | 18/2/0 | 18/2/0 | 18/2/0 | 18/2/0 | 17/3/0 | 17/3/0 | 18/1/1 |
| H264_REENCODE | 18/2/0 | 17/3/0 | 17/3/0 | 17/3/0 | 17/3/0 | 17/3/0 | 17/3/0 | 16/4/0 | 16/3/1 |
| LETTERBOX | 0/10/10 | 11/9/0 | 11/9/0 | 11/9/0 | 11/9/0 | 10/9/1 | 12/7/1 | 12/7/1 | 12/6/2 |
| MIRROR | 0/2/18 | 17/3/0 | 17/3/0 | 17/3/0 | 17/3/0 | 16/4/0 | 16/4/0 | 16/4/0 | 15/4/1 |
| PIP_REDUCED_VIEWPORT | 0/13/7 | 4/11/5 | 1/11/8 | 0/6/14 | 0/2/18 | 0/0/20 | 0/0/20 | 0/0/20 | 0/0/20 |
| SPEED_0_8X | 17/3/0 | 19/1/0 | 19/1/0 | 19/1/0 | 20/0/0 | 20/0/0 | 19/1/0 | 19/1/0 | 20/0/0 |
| SPEED_1_25X | 19/1/0 | 20/0/0 | 20/0/0 | 20/0/0 | 20/0/0 | 20/0/0 | 20/0/0 | 20/0/0 | 19/1/0 |
| SUBTITLE_OVERLAY | 12/5/3 | 16/4/0 | 16/4/0 | 16/4/0 | 16/4/0 | 17/3/0 | 14/6/0 | 13/6/1 | 12/7/1 |
| VERTICAL_9_16 | 0/1/19 | 6/13/1 | 5/14/1 | 5/12/3 | 3/13/4 | 2/14/4 | 2/11/7 | 0/8/12 | 1/4/15 |
| ZOOM | 4/13/3 | 11/9/0 | 11/9/0 | 11/9/0 | 11/9/0 | 10/10/0 | 8/11/1 | 8/11/1 | 8/11/1 |

At the position level, the number of 20 content positions whose full set of 11
transformations was safe was DCT 0, `.55` 2, and 0 for every other tau. This is a
strict paired-position diagnostic, not a promotion criterion.

## 15. DEV policy eligibility

| Policy | Eligible | Failed constraints |
|---|---|---|
| TAU_0.55 | No | total safety; A010 rival preservation |
| TAU_0.60 | No | total safety; A010 rival preservation |
| TAU_0.64 | No | total safety; A010 rival preservation |
| TAU_0.68 | No | A010 rival preservation |
| TAU_0.72 | No | A010 rival preservation |
| TAU_0.76 | No | total safety; A010 rival preservation |
| TAU_0.80 | No | H.264 stability; A010 rival preservation |
| TAU_0.82 | No | H.264 stability; A010 rival preservation |

Every tau passed total coverage, direct stability, speed stability, geometry signal,
and DEV-negative safety. `.68` and `.72` passed every condition except A010. The
criteria were not relaxed after this result.

## 16. Selected tau and selection reasoning

There is no selected tau. The lexicographic ranking was never invoked over an eligible
set because that set was empty. The harness therefore did not create
`selected-score-contract.json`. This is the required abstention point at the experiment
lifecycle level; it is not matcher abstention counted as success.

## 17. A010 rival-preservation regression

DCT classified A010 DIRECT, H.264, and SPEED_0_8X as safe ambiguities with complete
correct alternatives. For every DINO tau:

| Transform | Outcome | Raw/effective correct-source frames | Raw/effective rival frames | Complete correct alternative |
|---|---|---:|---:|---|
| DIRECT | SAFE_AMBIGUITY_CONTAINS_GT | 15/15 | 16/16 | Yes |
| H264_REENCODE | UNSAFE_WRONG_MATCH (AMBIGUOUS) | 13/13 | 16/16 | No |
| SPEED_0_8X | SAFE_AMBIGUITY_CONTAINS_GT | 18/18 | 20/20 | Yes |

The H.264 result did not arise from candidate deletion at the 0.82 raw threshold: all
13 correct-source supporting frames and all 16 rival frames survived even at `.82`, so
lowering tau could not add relevant line membership. Correct-episode alternatives were
present, but their full boundary/slope mappings missed the frozen evaluator contract.
The wrong-source primary was YELLOW and production review remained required, but those
facts do not convert an unsafe mapping into a pass.

## 18. Rival/ambiguity evidence by tau

Counts below include all 220 DEV positives. Candidate input/effective totals include
the 20 DEV negatives; GT and rival support counts include positives only.

| Policy | Effective/input candidates | Effective/raw GT frames | Effective/raw rival-line frames | Alternatives | Ambiguous cases | Wrong primary + safe alternative | Wrong primary without complete alternative |
|---|---:|---:|---:|---:|---:|---:|---:|
| DCT | 18,367/30,880 | 1,740/1,873 | 265/425 | 69 | 61 | 5 | 14 |
| .55 | 27,646/30,880 | 3,153/3,167 | 333/333 | 144 | 97 | 6 | 10 |
| .60 | 26,471/30,880 | 3,115/3,167 | 327/333 | 142 | 94 | 6 | 10 |
| .64 | 25,160/30,880 | 3,045/3,167 | 313/333 | 143 | 94 | 7 | 8 |
| .68 | 23,982/30,880 | 2,979/3,167 | 304/333 | 137 | 90 | 7 | 7 |
| .72 | 23,062/30,880 | 2,925/3,167 | 300/333 | 138 | 90 | 6 | 8 |
| .76 | 21,718/30,880 | 2,843/3,167 | 289/333 | 129 | 86 | 4 | 10 |
| .80 | 19,900/30,880 | 2,709/3,167 | 279/333 | 122 | 81 | 4 | 11 |
| .82 | 18,650/30,880 | 2,600/3,167 | 267/333 | 111 | 70 | 4 | 10 |

Lower anchors retained more correct and rival evidence and increased ambiguity. That
was expected and is not itself a defect. It improved safe coverage, but at `.55`-.64
also increased total unsafe; no anchor fixed the registered A010 H.264 mapping.

## 19. Proof policy frozen before fresh data generation

The stronger fact is that no policy was frozen and no fresh data was generated. The
ignored workspace contains the DEV identity lock, DEV result, scratch DCT control, and
cached DCT arrays only. It contains no selection file, fresh directory, fresh manifest,
fresh negative manifest, or fresh held-out result. Both the DEV-only exporter and the
verification script refuse to publish this stop result if any such artifact exists.

## 20. Fresh held-out sample construction

Not executed. Fresh position count is 0, not 10. The deterministic selector remains
pre-registered and unit-tested, but running it against the actual sources after a
failed DEV gate would have violated the protocol.

## 21. Fresh DCT results

Not measured. DEV DCT metrics must not be substituted for fresh DCT metrics.

## 22. Fresh selected-DINO results

Not measured because there is no selected DINO policy.

## 23. Per-transform fresh matrix

Not available. The only per-transform matrix in this report is explicitly DEV data in
section 14.

## 24. Safe/unsafe/NO_MATCH paired transitions

Fresh paired transitions were not computed. Running any tau on fresh content would
have violated the no-eligible-policy stop.

## 25. Geometry results

On DEV, every tau met the pre-registered geometry-signal constraint. Relative to DCT,
the safe gains for MIRROR/CENTER_CROP/VERTICAL_9_16 were:

| Tau | Mirror | Center crop | Vertical 9:16 |
|---|---:|---:|---:|
| .55 | +17 | +9 | +6 |
| .60 | +17 | +9 | +5 |
| .64 | +17 | +9 | +5 |
| .68 | +17 | +9 | +3 |
| .72 | +16 | +9 | +2 |
| .76 | +16 | +6 | +2 |
| .80 | +16 | +5 | 0 |
| .82 | +15 | +7 | +1 |

These gains remain DEV evidence only. They cannot satisfy the fresh promotion gate.

## 26. Direct/H264 results

DEV DIRECT was stable for `.55`-.72 and `.82` at 18 safe cases; `.76` and `.80` had 17.
DEV H.264 was 17 safe / 3 unsafe for `.55`-.76, compared with DCT 18/2, so it remained
inside the allowed one-case tolerance. `.80` and `.82` failed H.264 stability. All taus
also failed the separate A010 H.264 rival constraint.

## 27. Speed results

Both DEV speed profiles were strong across the grid. SPEED_0_8X ranged from 19 to 20
safe cases versus DCT 17; SPEED_1_25X ranged from 19 to 20 versus DCT 19. Every tau
passed the registered speed-stability criterion. This does not override the failed
A010 constraint.

## 28. PIP characterization

PIP remained weak and threshold-sensitive on DEV. DCT safe/U/N was `0/13/7`; `.55`
was `4/11/5`; `.60` was `1/11/8`; `.64` was `0/6/14`; `.68` was `0/2/18`; and `.72`
through `.82` emitted no safe or unsafe match. PIP was not a required improvement and
did not authorize ROI/tile work. No fresh PIP claim is available.

## 29. Fresh negatives

Fresh negatives were not generated. All 20 already known DEV synthetic negatives
produced zero coherent emitted segments for DCT and every tau. This is a safety
constraint result, not an estimate of real-world false-positive rate.

## 30. Wrong-GREEN analysis

DEV unambiguous wrong-source primary GREEN cases were: `.68` one, `.82` one, and zero
for DCT and all other taus. Wrong-source GREEN segment counts, including ambiguous
cases, were one each for `.55`, `.60`, and `.64`; two for `.68`; one for `.82`; and
zero for `.72`, `.76`, and `.80`. Candidate GREEN precision diagnostics ranged from
about 0.82 to 0.91 across DINO policies and are not release precision estimates.

Every emitted segment in all DEV runs had production review tier `REVIEW_REQUIRED`.
There is no fresh wrong-GREEN result.

## 31. Boundary / ALIGNMENT_LIMIT attribution

The experimental attribution is descriptive and orthogonal to C/S/U/N. For `.68`,
non-full boundary cases were categorized as 36 `ALIGNMENT_LIMIT`, 17
`PIP_GLOBAL_EMBEDDING_LIMIT`, 5 `RETRIEVAL`, 6 `SCORE_CONTRACT`, 19 unresolved
`SCORE_CONTRACT_OR_ALIGNMENT_LIMIT`, and 10 `TRUE_VISUAL_AMBIGUITY`. `.72` produced
34/17/5/8/21/10 in the same order.

A010 H.264 had full-boundary retrieval support for both the correct and rival lines,
yet no evaluator-complete correct alternative. Within the frozen architecture this is
not evidence that a lower raw threshold is missing; it is a score/rival-to-temporal
mapping limitation that the authorized global affine family did not close. No Phase 2A
change was made.

## 32. Runtime

DEV wall time was 231.44 seconds for 240 media inputs, with DCT plus eight DINO policy
replays. Aggregate aligner/matcher time was 35.90 seconds for DCT and, from `.55` to
`.82`, 14.56, 14.16, 13.74, 13.43, 13.04, 12.42, 11.44, and 10.66 seconds. DINO
embedding inference and exact retrieval were performed once per media input and reused
across tau policies.

## 33. Invariants / anti-leak probes

Post-run verification reported:

- independent taxonomy: 1,980 positive backend cases, 0 mismatches;
- candidate identity: 240 DEV case families, 0 cross-tau mismatches;
- score mapping: 247,040 candidates, 0 mapping, threshold, or retention mismatches;
- source grid: 277,920 audited records including policy replays, 0 off-grid;
- repeated and permuted candidate alignment: identical;
- GT mutation after candidate construction: candidate stream identical;
- correct-band removal: synthetic temporal span decreased from 10 to 7 seconds;
- two coherent cross-source lines: ambiguity activated with an alternative;
- one coherent wrong line: it could receive candidate GREEN, but its production tier
  remained `REVIEW_REQUIRED`;
- all DEV synthetic negatives: zero emitted segments.

## 34. Tests/reproducibility

Before Phase 2B-3 work, the repository baseline was 157 passing tests. Before DEV,
the full suite was 186 passing tests, including 29 Phase 2B-3 tests. The final suite
passed 187 tests after adding the explicit no-eligible-policy stop regression. Tests cover protocol constants, formula and special cases,
monotonicity/clipping, top-8 identity, batch size, DEV/fresh lifecycle guards,
deterministic sampling, both 30-second exclusion rules, one-policy held-out routing,
eligibility and lexicographic selection, A010, negative semantics, taxonomy,
sanitization, candidate permutation, band removal, and coherent rival/wrong lines.

Reproduction commands are documented in `experiments/phase2b3/README.md`. A disclosed
reproduction is a reproducibility check, not a new blinded experiment. In this run,
`freeze` must refuse because the eligible set is empty.

## 35. Known limitations and architecture questions

The experiment has only two source episodes and 20 observed DEV content positions.
Transformations within a position are paired. Synthetic negatives are source-biased.
No eligible policy means there is no fresh estimate, no promotion-gate result, and no
basis for production integration. Global-score changes can alter votes and admission,
but cannot force the frozen temporal layer to turn a present, imperfect rival proposal
into a complete evaluator-valid mapping.

### Q1. Did the family explore admission below .82?

Yes. Effective raw anchors ranged from .55 through .82; effective candidate counts
rose from 18,650 at `.82` to 27,646 at `.55`.

### Q2. Does tau=.64 reproduce DCT affine mapping?

Yes, numerically to floating-point error on the verified domain.

### Q3. Which policies were DEV-eligible?

None.

### Q4. Which tau was selected and why?

None was selected because every tau failed at least one hard eligibility constraint.

### Q5. Did a selected policy restore coverage without abstention failure?

There was no selected policy. Several policies increased DEV safe coverage and reduced
NO_MATCH, so the family did avoid the Phase 2B-2 abstention pattern on DEV, but none
cleared safety plus A010.

### Q6. Did A010 DIRECT/H264 avoid the regression?

DIRECT did; H.264 did not. Every tau retained a safe correct alternative for DIRECT,
but every tau produced an unsafe ambiguous H.264 result with a wrong primary and no
complete correct alternative.

### Q7. How did ambiguity and alternatives change?

Lower tau generally increased both. `.82` had 70 ambiguous cases and 111 alternatives;
`.55` had 97 and 144. This preserved more rival evidence but did not guarantee a
complete correct mapping.

### Q8. Did fresh total safe coverage meet DCT?

Not measured; fresh evaluation was forbidden.

### Q9. Did fresh total unsafe remain no worse?

Not measured.

### Q10. Were fresh DIRECT and H264 stable?

Not measured.

### Q11. Did fresh geometry materially improve?

Not measured. DEV geometry improved, but cannot answer the fresh gate.

### Q12. Did both fresh speed profiles remain strong?

Not measured. Both were strong on DEV only.

### Q13. Did any fresh negative emit a segment?

Fresh negatives were not created. DEV negatives emitted zero for every backend/policy.

### Q14. Did any fresh wrong-source candidate GREEN appear?

Not measured. DEV had unambiguous wrong-source primary GREEN cases at `.68` and `.82`.

### Q15. Did PIP remain safely weak on fresh content?

Not measured. It remained weak on DEV and no PIP-specific remediation was attempted.

### Q16. What primarily explains remaining failures?

DEV shows a mixture. At the closest eligible-except-A010 policies, the largest named
bucket was `ALIGNMENT_LIMIT`, followed by unresolved score/alignment cases and
`PIP_GLOBAL_EMBEDDING_LIMIT`; retrieval-only failures were five cases. Ten cases were
classified as coherent true visual ambiguity. These are descriptive counts, not causal
proof.

### Q17. Does evidence support one global DINO score contract?

No registered global contract was validated. Retrieval evidence remains strong, but
the required rival-safety contract was not met and no fresh confirmation was allowed.

### Q18. Architecture recommendation

```text
C. RETRIEVAL REMAINS STRONG BUT SCORE/RIVAL SAFETY STILL UNRESOLVED —
another narrowly scoped remediation is required.
```

This report does not authorize that remediation or any next phase.

## 36. Privacy/scope audit

The committed aggregate contains anonymous source/position aliases, normalized source
positions, aggregate metrics, public Git provenance, and configuration only. It does
not contain media paths, media names, exact source/query timelines, per-frame records,
raw candidates, embeddings, local model/index identity, scratch database contents, or
private 64-character fingerprints.

The raw DEV result, identity lock, scratch DCT database, cached arrays, media, model,
and any local provenance remain ignored. A staged-content scan is required immediately
before the final commit. Production `src/ipvm` and `config` remain unchanged. The run
stops here for independent review and Architecture Owner decision.
