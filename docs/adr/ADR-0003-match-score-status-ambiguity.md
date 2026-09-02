# ADR-0003：Match Score、Status 与 Ambiguity 语义

状态：已接受，Phase 1.5 已实现

## 决策

匹配状态固定为：

- `MATCH_FOUND`
- `MATCH_NOT_FOUND`
- `AMBIGUOUS`
- `INCOMPLETE_INDEX`
- `ERROR`

`overall_confidence` 更名为 `match_score`。该值只表达被选提案内部的视觉、coverage 与 temporal consistency 强度，不表达结果正确概率。

## 歧义

对 accused 区间高度重叠、但 source episode 或 source timeline 不同的竞争候选，记录 winner、runner-up、score ratio 和 margin。Phase 1.5 的 exact-duplicate regression threshold 为 `runner_up / winner >= 0.99999`。达到阈值时返回 `AMBIGUOUS`，不输出单一 primary segment；audit 和数据库保存主要候选。

该阈值只用于捕获 synthetic exact duplicate，不是概率阈值。真实素材上的阈值必须由 golden set 重新校准。

## GREEN 安全条件

GREEN 同时要求：

- project index status 为 `COMPLETE`；
- status 为 `MATCH_FOUND`；
- uniqueness margin ≥ 0.05；
- temporal residual ≤ 0.55 秒；
- coverage ≥ 0.90；
- temporal consistency ≥ 0.90；
- 无 segment contradiction。

任一条件不满足即 YELLOW、RED 或结构化状态降级。`match_score >= 0.95` 本身不能触发 GREEN。

上述数值是 Phase 1.5 synthetic safety regression 的初始阈值，只用于稳定比较，不得对外解释为准确率或概率校准。

## 未评估事实

镜像和裁剪检测尚未实现，因此 `mirrored`、`cropped` 写入 `null`。只有检测器实际运行后才能写 `true` 或 `false`。
