# Phase 2B-2 execution decisions before calibration

The protocol-only commit is `20884b1e9ca5d81f35660bc84a7e1294897084a0`.
Initial HEAD was `dccec2820750126b46baabcacb14ce0caf828c7b`; initial pytest was
137 passed. No calibration or held-out outcome existed at handover. The previous
attempt wrote adapter.py but the benchmark.py patch failed; there was no benchmark
implementation to preserve.

## Stage invariants

1. Audit completed; production diff against 93bf13b empty.
2. Protocol-only commit completed before any experiment.
3. Implementation uses existing production aligner and evaluator unchanged.
4. Calibration command can evaluate odd positions/negatives only.
5. Separate freeze command writes selected-adapter.json exclusively, with calibration
   digest, protocol commit and hashes of executable harness, adapter, aggregator and
   frozen evaluator/config code. Calibration refuses to run after this file exists.
6. Held-out command checks those identities before evaluating even positions/negatives.
   Results cannot revise the policy. No code/calibration changes after held-out viewing.
7. Tests, report and sanitized aggregates are the final deliverables.
8. Stop for independent review and Architecture Owner. No Phase 2B PASS declaration.

## Batch audit and required correction

The Phase 2B-1 source index used chunks of at most 16, with a natural short final batch.
Its query helper concatenated frames and encoded the complete clip, including a
20-frame call for 0.8x. Therefore copying its old candidate scores while asserting
fixed batch size 16 would be inaccurate. The authorized batch requirement takes
precedence over reusing cached query scores: this harness recomputes queries from the
identical existing media, with chunks of 16 and natural short tails. No padding is
introduced. The source index, preprocessing, model, candidate identity rules and exact
top-8 retrieval are unchanged. Actual call sizes are recorded per query in ignored raw
output. Ordinary queries use 16; 1.25x uses the natural 13-frame tail; 0.8x uses 16+4.
No outcome was inspected to make this correction. No new content positions are sampled.

## DCT control and score audit

DCT runs VideoMatcher.analyze_file(persist=False) using a separate scratch database
populated from the validated DCT index arrays. Source aliases, times and features are
preserved. Production query decoding, encoder, CandidateRetriever, score mapping and
TemporalAligner all execute; a capture wrapper records retrieve's unmodified return.
The user database is not used. DINO calls the same TemporalAligner entry directly.

DCT candidate scores are clip((cosine+1)/2,0,1); RAW DINO scores are clip(cosine,0,1).
The mandatory global floor plus the frozen 0.82 aligner filter means the effective DINO
raw cosine cutoffs are RAW=0.82 and floors 0.45/0.50/0.55/0.60/0.65/0.70 give
0.901/0.910/0.919/0.928/0.937/0.946 respectively. These are structural consequences,
not optimized thresholds. Both adapter retention and effective aligner retention are
reported. The policy grid remains exactly as registered.

Candidate GREEN does not directly threshold visual_score: the score influences proposal
selection and rival margins, while the tier function checks residual/support/coverage,
uniqueness and safety flags. Rank is audit-only. Candidate order is canonicalized by
accused time and original rank; no candidate is renumbered after flooring.

## Deterministic safety operationalization

Before outcomes, the ambiguous word 'material' is operationalized conservatively:
any increase in calibration unsafe-positive count over DCT is ineligible; any emitted
negative segment is ineligible (stricter than rejecting only confident negatives).
Eligible policies use the registered lexicographic order: unsafe positive count,
negative emitted count, descending safe-success count, source MAE, accused MAE, policy
order. Exact numeric ties use RAW then ascending floor. If no policy is eligible, stop
without held-out policy promotion. No held-out information enters this function.

Primary metrics use the frozen Phase 2A evaluator. Four-way functional outcomes remain
separate from contained-partial-evidence safety. A separate full-mapping diagnostic
checks whether any primary/alternative meets the existing strict mapping helper; it
does not replace or improve the primary evaluator.

## Privacy and report surface

The requested durable report is repository Markdown, with JSON aggregate for audit.
No external publication is required. Raw media paths, candidates and exact timestamps
remain ignored. Existing modified .DS_Store and three untracked independent review
documents are user-owned and excluded from staging.

## Closure log — appended after evaluation, not a calibration decision

Stages 4–6 are complete. Calibration completed at 2026-09-05T02:06:07.361503 UTC;
FLOOR_0.70 was frozen at 02:06:57.752496 UTC; held-out completed at 02:08:21.088308 UTC.
Held-out contains only DCT and the selected policy. Do not return to calibration or
edit the frozen adapter, benchmark, reporting, production or evaluator identities.

Stage 7 final full pytest: 157 passed in 7.62 seconds. All three committed aggregate
payloads were recomputed from existing raw output and matched after JSON round-trip
normalization (integer batch-count keys become JSON strings). The frozen executable
identities still match calibration, selection and held-out records. Privacy validation
passed; production/config diff against 93bf13b remains empty. No new calibration or
held-out evaluation was run during this closure check.

The result does not meet promotion conditions. The report documents abstention,
paired unsafe regressions and wrong-source candidate GREEN. Stage 8 follows the
sanitized experiment commit: stop for independent review and Architecture Owner.
