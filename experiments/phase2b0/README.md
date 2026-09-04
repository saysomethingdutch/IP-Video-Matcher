# Phase 2B-0 visual-backend bake-off harness

This measurement-only harness creates deterministic query clips, runs the frozen
IPVM DCT control, invokes verified VDF CLI modes, and emits a sanitized aggregate.
It does not modify or replace any production matcher component.

Raw manifests, videos, backend JSON, databases, embeddings, logs, and exact ground
truth must be written under an ignored workspace such as `workspace/phase2b0/`.
Source videos are supplied as CLI arguments; this directory contains no media name,
path, or hash.

## Commands

```bash
python experiments/phase2b0/bakeoff.py make-corpus \
  --source SOURCE_A=/local/source-a.mp4 \
  --source SOURCE_B=/local/source-b.mp4 \
  --root workspace/phase2b0/corpus \
  --manifest workspace/phase2b0/raw/corpus.json

python experiments/phase2b0/bakeoff.py prepare-vdf \
  --manifest workspace/phase2b0/raw/corpus.json \
  --executable /local/vdf-cli \
  --root workspace/phase2b0/vdf \
  --modes CLASSIC_DEFAULT,AI_UNION_DEFAULT,AI_PARTIAL_DEFAULT \
  --output workspace/phase2b0/raw/vdf-indexing.json

python experiments/phase2b0/bakeoff.py run-dct \
  --manifest workspace/phase2b0/raw/corpus.json \
  --repo-root . \
  --config /local/frozen-config.yaml \
  --database /local/scratch-ipvm.db \
  --workspace workspace/phase2b0/dct-workspace \
  --output workspace/phase2b0/raw/dct.json

python experiments/phase2b0/bakeoff.py run-vdf \
  --manifest workspace/phase2b0/raw/corpus.json \
  --executable /local/vdf-cli \
  --root workspace/phase2b0/vdf \
  --output workspace/phase2b0/raw/vdf.json

python experiments/phase2b0/bakeoff.py aggregate \
  --manifest workspace/phase2b0/raw/corpus.json \
  --dct workspace/phase2b0/raw/dct.json \
  --vdf workspace/phase2b0/raw/vdf-defaults.json \
  --vdf workspace/phase2b0/raw/vdf-sensitivity.json \
  --experiment-status PILOT_COMPLETE \
  --vdf-version VERIFIED-UPSTREAM-VERSION \
  --output workspace/phase2b0/raw/aggregate.json
```

The VDF evaluator follows group semantics because the current CLI does not expose
a ranked candidate list. A query is source-correct only when its group contains the
expected source and not another source. `temporally_usable` additionally requires a
`PartialClip` flag and exposed offset. The primary offset tolerance remains ±1 second;
the separate ±15 second diagnostic reflects the current long-source sampling grid and
must not be substituted for the primary result.

For 8-second queries, use `AI_PARTIAL_8S_ELIGIBLE`. It retains the upstream default
89% frame-hit threshold but sets `--partial-clip-min-ratio 0.0`; the upstream 0.10
eligibility default otherwise excludes an 8-second query before comparison against a
roughly 40-minute source. This is an eligibility correction, not a fitted similarity
threshold.

`--vdf` may be repeated when default and pre-registered sensitivity modes were run in
separate invocations. Duplicate case/mode pairs and inconsistent offset tolerances are
rejected instead of being silently counted twice.
