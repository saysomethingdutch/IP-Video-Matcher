# Phase 1.5 Implementation Results

日期：2026-09-02

状态：实现完成；新 Phase 1 Gate 未通过；停止于 Phase 1.5。

## 1. 旧指标与新指标

| 项目 | 旧 Phase 1 报告 | Phase 1.5 结果 | 差异原因 |
|---|---:|---:|---|
| GT segments | 3 | 23 | 增加 20 条 off-grid direct/re-encode |
| 旧 segment recall | 100% | 已删除 | 旧定义不校验 source timeline |
| episode recall | 无独立指标 | 87.0% | 与 source-aware recall 分离 |
| episode correctness | 旧值与 recall 恒等 | 100% | 只衡量重叠预测中的 episode 正确性 |
| source-aware recall@1s | 未测 | 78.3% | episode + source start/end ≤1s |
| segment precision@1s | 未测 | 90.0% | 2 个 source-aware false positives |
| false-positive seconds | 未测 | 6.0s | 预测 accused 区间并集 |
| boundary MAE | 0.00s | source 0.275s | 新结果另有 3 个 episode-level misses，Gate 不允许忽略 |
| ambiguity | 不可观测 | 2 个输出 | exact duplicate 正确降级；另有 1 个普通样本存在等价竞争 |
| incomplete index | 不可观测 | 1/1 正确拒绝 | 未生成 `MATCH_FOUND` |

旧报告的 100%/0.00s 只来自 0.5 秒网格对齐的 3 个片段。新数字是 measurement definition 修复后的 regression baseline，不能与旧值作为同口径时间序列比较。

## 2. Phase 1.5 Gate

```text
source-aware recall@1s ≥95%       FAIL (78.3%)
segment precision@1s ≥98%         FAIL (90.0%)
source boundary MAE ≤1s           FAIL (0.275s，但有 3 个 episode-level misses)
pure negative FP seconds = 0      PASS
exact duplicate → AMBIGUOUS       PASS
incomplete index refused          PASS
```

总结果：`passed = false`。

机器产物：`benchmark/results/phase1_5_gate.json`。

## 3. A–J Diagnostic

完整 A–J subset 含 12 个 GT segments：

```text
episode recall                    75.0%
episode correctness               81.8%
source-aware recall@1s            16.7%
segment precision@1s              18.2%
false-positive segments           9
false-positive seconds            30.5s
source boundary MAE               1.534s
boundary missed segments          3
speed-ratio MAE                   0.05
```

这些数字包含 Phase 2 尚未实现的镜像、变速、crop、竖屏和组合变换，只作为 diagnostic，不是当前 Gate。

## 4. Safety Cases

- CASE_K pure negative：`MATCH_NOT_FOUND`，false-positive seconds = 0；
- CASE_L exact duplicate cross-episode：`AMBIGUOUS`，返回 EP04/EP05 主要候选；
- CASE_M 45% faststart truncated original：`INCOMPLETE_INDEX`，分析前拒绝。

## 5. Schema Migration

SQLite schema version：2。

主要变更：project-scoped assets、`UNIQUE(project_id, episode)`、index completeness、`match_score`、nullable transformation facts、primary/alternative candidate、safety diagnostics。旧索引迁移后强制标记为 `INCOMPLETE_INDEX` 并要求重建。

详见 `docs/migrations/SCHEMA-V2.md`。

## 6. ADR

- ADR-0002：Temporal Alignment v2，草案，未实现；
- ADR-0003：Match Score / Status / Ambiguity，已实现；
- ADR-0004：Project-scoped Original Assets，已实现；
- ADR-0005：Benchmark Metric Definitions，已实现。

## 7. 性能测量

Cold synthetic gate：

```text
index scope               5 assets / 82.5 media seconds
index elapsed             0.427s
analysis scope            25 cases / 112.707 media seconds
analysis elapsed          1.608s
analysis RTF              0.01426
peak RSS lower bound      133.3 MB
combined upper bound      182.8 MB
```

Warm synthetic gate：

```text
index scope               5/5 cache hits / 82.5 media seconds
index elapsed             0.132s
analysis elapsed          1.679s
analysis RTF              0.01490
```

内存同时记录主进程与已完成 FFmpeg 子进程的 `getrusage` peak RSS，并给出 lower bound 与保守相加 upper bound。该 API 是进程生命周期累计峰值，不能给出严格的同步瞬时总峰值。

以上全部是低分辨率 synthetic 原始测量，不外推真实 30 小时剧集性能。

## 8. Repository Diff 摘要

- schema v2 migration 与 project/index health；
- 五种一等 match status；
- `overall_confidence` 更名 `match_score`；
- uniqueness/competing candidates/contradiction safety gate；
- audit 补齐 originals SHA、config、encoder、index、competition、reason；
- evaluator v0.2 与 off-grid/K/L/M 数据；
- 标准 JSON、cold/warm scope、主/子进程 memory；
- media subprocess cleanup、FPS fallback、duration validation、encoder cache key；
- architecture revision、ADR-0002/0003/0004/0005、migration 文档；
- 18 项 pytest。

## 9. 仍未解决

- Phase 1 新 Gate 未通过；
- Temporal Alignment v2 未实现；
- 普通 off-grid 样本仍有漏检、source timeline 偏差和假阳性；
- synthetic uniqueness threshold 尚未经过真实 golden set 校准；
- 没有真实案件 golden set 或真实规模性能测试；
- 所有 Phase 2、语义、规则和 Excel 工作均未开始。
