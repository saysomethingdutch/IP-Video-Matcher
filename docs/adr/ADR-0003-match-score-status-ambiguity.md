# ADR-0003：Match Score、Status 与 Ambiguity 语义

状态：已接受，Phase 2A.2 Safety / Boundary Closure 修订

## 决策

匹配状态固定为：

- `MATCH_FOUND`
- `MATCH_NOT_FOUND`
- `AMBIGUOUS`
- `INCOMPLETE_INDEX`
- `ERROR`

`overall_confidence` 更名为 `match_score`。该值只表达被选提案内部的视觉、coverage 与 temporal consistency 强度，不表达结果正确概率。

## GREEN 暂停自动放行

Phase 2 的外部像素验证尚未实现。在该验证完成并经 Architecture Owner 恢复前：

- 系统可以计算 `candidate_tier=GREEN`，仅用于 benchmark 校准；
- 对外 `review_tier` 必须为 `REVIEW_REQUIRED`，不得为 `GREEN`；
- CLI、数据库、audit 和未来 exporter 均不得把 candidate GREEN 解释为“无需人工复核”；
- 本阶段不实现 8 FPS external verification。

恢复自动放行时，benchmark Gate 必须要求 `green_precision == 1.0`。

## Temporal-line 歧义

对同一 accused 区间高度重叠的主要候选，先按 ADR-0002 的完整 temporal line 判断
相容性。跨 episode 一律不相容；同 episode 但 mapping 在 union domain 上超过 source-grid
uncertainty 的候选也不相容。若不相容竞争解释的 bounded explanation quality margin：

```text
uniqueness_margin = winner.quality - runner_up.quality < 0.05
```

则 case status 为 `AMBIGUOUS`。`ambiguity_score_ratio = 0.99999` 已废弃，不再是主要判据。

其中 `quality = visual_score × min(1, inlier_count / (2 × minimum_segment_seconds × fps))`。
使用同一 bounded evidence quality 进行 primary 排序和 rival margin，避免 minimum-support
高分碎片制造歧义，也避免长而弱的线仅靠长度压掉更强解释。

primary candidate 可保留用于展示，但不构成确定结论；所有满足上述 margin 条件的主要
alternatives 必须进入 audit，并写入数据库且 `is_primary=false`。整个结果必须
`REVIEW_REQUIRED`，不得默认第一集。same-line boundary jitter 不得制造 alternative。

本修订仅处理运行期候选竞争，不实现跨集重复区间的索引期预计算。

## Segment contradiction

一对 segment 矛盾不再清空整案：

- case status 为 `AMBIGUOUS`；
- 冲突节点标记 `conflict=true`、列出 `conflict_with` 与原因；
- 未参与冲突、证据独立且可信的 segment 保留并返回；
- audit 保存 conflict graph、conflicting segment ids 与 reason。

案件存在 ambiguity 不等于案件中所有已确定事实消失。

## Candidate GREEN 诊断条件

candidate GREEN 同时要求：

- project index status 为 `COMPLETE`；
- status 为 `MATCH_FOUND`；
- uniqueness margin ≥ 0.05；
- residual RMS ≤ 配置阈值（当前 0.25 秒）；
- inlier ratio ≥ 0.90 且 inlier count ≥ 6；
- coverage ≥ 0.90；
- temporal consistency ≥ 0.90；
- 无 segment contradiction、source reuse、out-of-range speed；
- `boundary_completeness != INCOMPLETE`，且未命中 segment cap。

任一条件不满足即 candidate YELLOW 或结构化状态降级。`match_score >= 0.95` 本身不能触发 candidate GREEN。

Candidate GREEN 只表示已输出 segment 内部 alignment quality，不表示 segment 边界完整，
更不表示生产自动放行。`BoundaryCompleteness.UNKNOWN` 不会被谎报为 FULL；生产层无论
candidate tier 为何都保持 `REVIEW_REQUIRED`。

上述数值是 synthetic safety regression 的初始阈值，只用于稳定比较，不得对外解释为准确率、概率校准或自动放行资格。

## 未评估事实

镜像和裁剪检测尚未实现，因此 `mirrored`、`cropped` 写入 `null`。只有检测器实际运行后才能写 `true` 或 `false`。
