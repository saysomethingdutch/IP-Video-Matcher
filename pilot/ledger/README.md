# WO-001 Capture Ledger

Run from the repository root with `/opt/homebrew/bin/python3` (openpyxl 3.1.5 and Pillow are used; no new packages):

```bash
/opt/homebrew/bin/python3 -m pilot.ledger run pilot/cases/qsjx_e18.yaml
/opt/homebrew/bin/python3 -m pilot.ledger run pilot/cases/zhuyu_mah00002.yaml
```

The second command runs both full-frame and cropped OCR. Configurations are JSON syntax in `.yaml` files, which is valid YAML. `crop` uses normalized `[x,y,w,h]` coordinates with a top-left origin; `segments` may override it by video time (`start`, `end`, `crop`, `scale`). `fps` may be 1 or 2. Frame timestamp error is at most half a sample interval (0.5 seconds by default). Vision OCR uses four processes. Its binary is compiled to `pilot/bin/` when needed; the Swift and Clang module caches are in `/private/tmp`.

Input videos, screenshots, and workbooks are read-only. OCR results are cached in `workspace/pilot/<case_id>/ocr_cache/` with the video filename, size, frame rate, and crop settings in the key. Temporary full-size extracted frames are deleted after OCR; only referenced evidence frames, resized to width at most 640 pixels, remain. The output directory contains `ledger.jsonl`, `ledger.csv`, `unmatched_sessions.csv`, `rows_without_session.csv`, one HTML page per video, and `run_summary.json`. Blank fields and low-confidence matches require human review. The HTML page links to original phone screenshots where aligned.
