# WO-009 交付说明（2026-09-27）

- `pilot/integration` 依次合入 `pilot/wo-001-r3`（`94cae76`，合并提交 `1a08176`）和 `pilot/wo-004c-r3`（`011ca08`，合并提交 `61f7cf0`）。后者仅含文风/标题 reaction 规则、校验器、测试及 R3 记录，无整批意见数据。七时吉祥旧意见未整批重写。
- 全仓 `pytest -q tests pilot`：383 项及 11 个子测试通过。`python -m pilot run cases/qsjx.yaml --from review --rows 1051,1098,1146,1175,1216`：退出 0；台账、字幕、画面、分类、意见均复用缓存，1051、1098 的本地声音状态补记为泰配，核对页重建；无新云端调用。
- 合并 main 前，原未跟踪 `pilot/` 的 23 个文件整体移至 `_attic/main-untracked-pilot-2026-09-27/`。其中与 integration 同路径而内容不同的源码为 `ledger/__main__.py`、`ledger/parse.py`、`ledger/pipeline.py`、`ledger/report.py`、`ledger/workbook.py`；其余差异是 8 个 `__pycache__` 文件。23 个文件的逐项清单和 SHA-256 在 `_attic/WO009-backup-manifest.txt`。原 `docs/workorders/` 的 30 个文件原样备份于 `_attic/main-untracked-workorders-2026-09-27/`；同名文件保留 main 原文，分支追加的 R1/R2/R3 记录继续保留。其他未跟踪目录、`.DS_Store` 与 `workspace/` 均未处理。
- main 以 `--no-ff` 合并并标记 `pilot-qsjx-v1`；本地提交和标签均不 push。除 main 外现有 13 个工作树（`pilot/wo-001-ledger`、`wo-001-r3`、`wo-003a-captions`、`wo-003b-asr`、`wo-004a-vlm`、`wo-004b-classify`、`wo-004b-r2`、`wo-004b-r3`、`wo-004c-opinion`、`wo-004c-r3`、`wo-005-review`、`wo-008-ledger-audio`、`integration`）可由所有者确认后清理，本单未删。
- 下一步逐玉摸底：从 `cases/_template.yaml` 新建案件配置，再决定所需证据和模型设置。
