# ADR-0005：Benchmark Metric Definitions

状态：已接受，Phase 1.5.1 evaluator 修订

## 匹配判据

默认 source tolerance 为 1.0 秒。

- `episode_recall`：存在 accused overlap ≥ 50% 且 episode 正确的预测之 GT 数 / GT 总数；
- `source_aware_recall@1s`：在 episode recall 基础上，source start 和 source end 均在 1 秒内；
- `segment_precision@1s`：满足 source-aware 判据的预测数 / 预测总数；
- `episode_correctness`：与某个 GT accused 区间重叠的预测中，episode 正确的比例。

这些指标分别计算，禁止复用同一个表达式。

prediction 与 GT 必须做确定性一对一 assignment。一条 GT 最多被一条 prediction 认领；重复或近似重复的多余 prediction 计入 false positive。

## False Positive

报告 unmatched predicted segments、false-positive segment count，以及这些预测在 accused timeline 上区间并集的秒数。pure negative case 要求 false-positive seconds 为 0。

## Boundary Error

accused-side 与 source-side boundary MAE 分开报告。只要存在未评估边界，同时报告 `boundary_missed_segments` 和 unmatched GT；Gate 不允许通过过滤缺失/无限值获得更好的 MAE。

没有可评估边界时输出 JSON `null`，禁止输出 `Infinity`、`NaN` 或其他非标准 JSON 数值。

## Ambiguity 与 Index Safety

报告 ambiguous prediction 数、ambiguity case correctness、incomplete-index refusal 数。exact duplicate 的两个合法 source 用同一 `equivalence_group` 表示。

## Speed Ratio

evaluator 同时验证物理自洽性：prediction accused span、source span、prediction slope 与 GT `speed_ratio` 必须相容。即使 source endpoints 看似命中，`accused 60s → source 5s 且 speed_ratio=1.0` 也必须失败。

`speed_ratio_mae` 只在真正的非 1.0× GT 上统计，避免大量 1.0 样本稀释变速误差。Phase 1 不要求通过变速 Gate；该字段为 Phase 2 留出可比较基线。

## Tier Metrics

- `green_precision`：candidate GREEN 中 source-aware 正确的比例；
- `green_recall`：source-aware 正确且 candidate GREEN 的预测数 / GT 数；
- `review_required_precision`：有效 review tier 为 `REVIEW_REQUIRED` 的预测中 source-aware 正确的比例。

Phase 1.5.1 的 candidate GREEN 仅用于诊断，报告必须注明它不是自动放行状态。未来恢复 GREEN 自动放行时，Gate 必须要求 `green_precision == 1.0`。

## Ground Truth

synthetic 与 `input/golden/` 使用同一 schema。start/end 必须允许 off-grid 浮点时间，生成器不得吸附到 coarse sampling grid。
