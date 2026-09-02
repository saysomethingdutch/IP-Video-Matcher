# ADR-0005：Benchmark Metric Definitions

状态：已接受，Phase 1.5 已实现

## 匹配判据

默认 source tolerance 为 1.0 秒。

- `episode_recall`：存在 accused overlap ≥ 50% 且 episode 正确的预测之 GT 数 / GT 总数；
- `source_aware_recall@1s`：在 episode recall 基础上，source start 和 source end 均在 1 秒内；
- `segment_precision@1s`：满足 source-aware 判据的预测数 / 预测总数；
- `episode_correctness`：与某个 GT accused 区间重叠的预测中，episode 正确的比例。

这些指标分别计算，禁止复用同一个表达式。

## False Positive

报告 unmatched predicted segments、false-positive segment count，以及这些预测在 accused timeline 上区间并集的秒数。pure negative case 要求 false-positive seconds 为 0。

## Boundary Error

accused-side 与 source-side boundary MAE 分开报告。只要存在未评估边界，同时报告 `boundary_missed_segments` 和 unmatched GT；Gate 不允许通过过滤缺失/无限值获得更好的 MAE。

没有可评估边界时输出 JSON `null`，禁止输出 `Infinity`、`NaN` 或其他非标准 JSON 数值。

## Ambiguity 与 Index Safety

报告 ambiguous prediction 数、ambiguity case correctness、incomplete-index refusal 数。exact duplicate 的两个合法 source 用同一 `equivalence_group` 表示。

## Speed Ratio

evaluator 记录 `speed_ratio_mae`。Phase 1 不要求通过变速 Gate；该字段为 Phase 2 留出可比较基线。

## Ground Truth

synthetic 与 `input/golden/` 使用同一 schema。start/end 必须允许 off-grid 浮点时间，生成器不得吸附到 coarse sampling grid。
