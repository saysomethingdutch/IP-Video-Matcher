# WO-006 交付说明（待 WO-004c-R2 合并后复核）

- 分支：`pilot/integration`，从 `main` 建立；已合并 WO-001、002a、003a、003b、004a-R3、004b、004c-R1、005。WO-004c-R2 提交后再合并，不 push。
- 交付：`cases/qsjx.yaml`、`cases/_template.yaml`、`python3 -m pilot run/review/export`、逐步缓存、`--from`、`--rows`、按案件列映射与失败/旗标/费用摘要。所有者操作见 `pilot/README.md`。`pyproject.toml` 已声明 `openpyxl` 等运行依赖。
- 验收：现有缓存从入口完整运行到核对页，169 行；核对页 U–AJ 共 2,704 格与 `semantic/<行>.json` 逐格比对，差异 0。隔离目录清除 1051、1127、1175 的逐行缓存后重跑字幕、画面、分类、意见、核对页；三行均产出 16 列，1175 的 DeepSeek 意见因“具体性”校验失败而标红，草稿保留待核。相比旧缓存，变化列为 1051：AA、AF；1127：Z、AF、AG–AJ；1175：AF，均涉及重新调用云端模型。
- 测试：`pilot/ledger` 11 通过，11 subtests 通过；`pilot` 98 通过，11 subtests 通过；完整测试 317 通过、7 项依赖试点缓存的测试跳过、4 subtests 通过。R2 缓存复跑待合并后记录。
- 待核：意见失败行与红黄旗标由所有者在核对页处理；WO-004c-R2 的 GLM 重写完成后复核实际行数、费用与差异。
