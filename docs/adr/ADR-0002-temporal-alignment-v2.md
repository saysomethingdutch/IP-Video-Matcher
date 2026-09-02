# ADR-0002：Temporal Alignment v2

状态：草案，未实现

## 问题

现有 Phase 1 对齐器先按常数 offset 分桶，再在桶内拟合斜率。这使 RANSAC 无法覆盖 Architecture Phase 2 要求的 0.75–1.50 速度范围，并会在 offset 漂移时截断片段。

## 当前方案

```text
constant-offset buckets → minimum-duration runs → per-run RANSAC
```

Phase 1.5 保留该实现，仅用新的 evaluator 测量其行为。

## 拟议方向

```text
slope-offset 2D voting
→ candidate peaks
→ segmented RANSAC refinement
→ temporal/source gap split
→ iterative piecewise extraction
```

核心映射仍为：

```text
source_time = a * accused_time + b
a ∈ [0.75, 1.50]
```

## 尚待决定

- slope/offset 投票分辨率；
- peak suppression 和停止条件；
- piecewise 区间的最小长度与 gap 规则；
- 多个合法映射的 contradiction/ambiguity 处理；
- 与 8 FPS 精对齐的接口边界。

## 影响

该变更会替换 Temporal Alignment 的候选提案逻辑，并改变 speed ratio 的可检测范围。它不应与视觉 backend 切换同时实施，否则无法归因指标变化。

## 实施约束

本 ADR 在 Phase 1.5 只起草，不写生产代码。须经架构负责人批准后才可进入 Phase 2 实现。
