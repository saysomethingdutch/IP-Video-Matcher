# Phase 2B-1 frame-embedding retrieval characterization

This experiment directly invokes the verified DINOv2-small INT8 ONNX model with
Python ONNX Runtime. It does not use VDF runtime behavior and does not modify the
production matcher. The frozen comparison is DCT@1, source/query sampling is 2 FPS,
top-k is 8, and retrieval is exact NumPy cosine similarity.

All media, model files, source indexes, raw per-frame candidates, paths, exact source
timestamps, and logs must remain under ignored `workspace/phase2b1/`. Only the harness,
tests, report, and sanitized aggregate belong in Git.

## Experimental dependency

Install ONNX Runtime into the project virtual environment without changing production
dependencies:

```bash
.venv/bin/pip install --no-user onnxruntime==1.23.2
```

## Full protocol

```bash
PYTHONPATH=src .venv/bin/python experiments/phase2b1/benchmark.py make-corpus \
  --source SOURCE_A=/local/source-a.mp4 \
  --source SOURCE_B=/local/source-b.mp4 \
  --root workspace/phase2b1/corpus \
  --output workspace/phase2b1/raw/corpus.json

PYTHONPATH=src .venv/bin/python experiments/phase2b1/benchmark.py make-negatives \
  --root workspace/phase2b1/negatives \
  --count 20 \
  --output workspace/phase2b1/raw/negatives.json

PYTHONPATH=src .venv/bin/python experiments/phase2b1/benchmark.py build-index \
  --manifest workspace/phase2b1/raw/corpus.json \
  --model /local/dinov2-small-int8.onnx \
  --output-dir workspace/phase2b1/index

PYTHONPATH=src .venv/bin/python experiments/phase2b1/benchmark.py run \
  --manifest workspace/phase2b1/raw/corpus.json \
  --negatives workspace/phase2b1/raw/negatives.json \
  --model /local/dinov2-small-int8.onnx \
  --index-dir workspace/phase2b1/index \
  --output workspace/phase2b1/raw/retrieval.json

PYTHONPATH=src .venv/bin/python experiments/phase2b1/benchmark.py aggregate \
  --raw workspace/phase2b1/raw/retrieval.json \
  --manifest workspace/phase2b1/raw/corpus.json \
  --output workspace/phase2b1/raw/aggregate.json
```

`DINO_FLIP_TTA` is pre-registered as a separate diagnostic: the original and
horizontally flipped query embeddings are searched against the same unmodified source
index, and the maximum cosine for each unique source frame is ranked. No similarity
threshold is fitted. The negative corpus therefore characterizes nearest-neighbor score
and concentration distributions; it is not reported as false-positive rate.
