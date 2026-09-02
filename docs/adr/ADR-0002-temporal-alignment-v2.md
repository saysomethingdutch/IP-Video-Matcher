# ADR-0002：Temporal Alignment v2

状态：草案，未实现

修订：Phase 1.5.1 仅澄清输出语义；本 ADR 的 Temporal Alignment v2 仍未实现。

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

Phase 2A-0 实测要求最终设计显式容忍 `±1` 个 source grid step（当前 2 FPS → 0.5 秒）的 retrieval 偏差；这不是可选优化，而是 89% 的 strict miss 属于"相邻格可用"这一实测结果所对应的必要条件。

## 时间戳语义

当前 index/query 使用 `nominal_grid_time = frame_index / fps`，与真实 decoded media PTS 存在约 0.23 秒的系统性提前量（两侧同源、匹配时抵消）。生产 matcher 在本阶段继续使用 `nominal_grid_time`，但在 Phase 2A 完成前，最终 `source_start/source_end` 必须能够映射或精修到真实 `media_pts`，才能进入法律交付物和未来 Excel。

## 影响

该变更会替换 Temporal Alignment 的候选提案逻辑，并改变 speed ratio 的可检测范围。它不应与视觉 backend 切换同时实施，否则无法归因指标变化。

## speed_ratio 固定语义

```text
speed_ratio = d(source_time) / d(accused_time)
```

- `speed_ratio > 1.0`：被诉视频加速。例如，被诉时间 4 秒覆盖原片 5 秒时为 `1.25`；
- `speed_ratio < 1.0`：被诉视频减速；
- 拟合使用的 accused/source 有效跨度任一小于 3.0 秒时输出 `null`，不得输出不可信斜率；
- 合并相邻 segment 时禁止对既有 `speed_ratio` 做算术平均，必须合并完整 inliers 后重新拟合 slope。

Phase 1.5.1 已修复上述输出与合并语义，但没有实现二维投票、候选峰值或任何 Temporal Alignment v2 能力。

## 实施约束

本 ADR 在 Phase 1.5 只起草，不写生产代码。须经架构负责人批准后才可进入 Phase 2 实现。
