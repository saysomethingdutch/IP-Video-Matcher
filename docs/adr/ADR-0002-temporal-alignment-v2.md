# ADR-0002：Temporal Alignment v2

状态：已实现，等待独立 Review / Architecture Owner Gate

修订：Phase 2A 实现于 2026-09-03；DCT、source/query 2 FPS 与 retrieval top-k=8 保持不变。

## 问题

现有 Phase 1 对齐器先按常数 offset 分桶，再在桶内拟合斜率。这使 RANSAC 无法覆盖 Architecture Phase 2 要求的 0.75–1.50 速度范围，并会在 offset 漂移时截断片段。

## 被替换的方案

```text
constant-offset buckets → minimum-duration runs → per-run RANSAC
```

该方案只保留在 Git 历史和 before benchmark 中，不再是生产 temporal proposal。

## 决策

```text
sparse episode-local slope-offset voting
→ offset 邻格 soft voting
→ non-maximum-suppressed peaks
→ ±1 source-grid support collection
→ accused-time continuity split
→ deterministic bounded-pair robust refit
→ iterative piecewise extraction and evidence claiming
→ global cross-episode competition and safety classification
```

核心映射仍为：

```text
source_time = a * accused_time + b
a ∈ [0.75, 1.50]
```

速度范围为 `[0.75, 1.50]`，slope bin 为 `0.025`。Offset bin 使用
`1 / coarse_fps`，候选容差使用
`candidate_timestamp_uncertainty_grid_steps / coarse_fps`；当前分别为 0.5 秒和
±0.5 秒，但实现中不硬编码这两个数值。每个 accused frame 在同一 vote cell
最多贡献一次，防止 top-k 重复候选放大证据；offset vote 按距离线性分配给相邻
bin，避免边界两侧的等价候选被割裂。

Robust refit 最多从 32 个按时间均匀抽取的点构造 pair hypotheses，pair 的
accused-time 间隔至少 1 秒，超出物理速度范围的 hypothesis 被拒绝。拟合与
segment extraction 均为确定性；同一 episode 的已接受 inliers 会被 claim 后继续
提取剩余 piecewise segment。相邻 segment 只有在 episode、accused gap 与 source
continuity 均满足条件时合并，且合并后从完整原始 inliers 重拟合。

### 复杂度约束

设过滤后候选数为 `C`、固定 slope bin 数为 `S`（当前 31）、每个 episode
保留 peak 上限为 `P`（当前 128），robust-fit 抽样上限为 `R`（当前 32）。Sparse
vote 构建为 `O(CS)`，空间只与实际触达 cell 数量相关，不创建
`episode × slope × offset` dense tensor。每次 peak support scan 为 episode 候选数
的线性扫描；pair refit 上限为 `O(R² + C)`。每 episode segment 和 peak 数均有
configuration 上限，避免无界 `O(C²)` pair 枚举。

## 已决定与尚未实现的边界

- minimum segment、maximum frame gap、uniqueness、coverage 与 residual 等现有
  safety thresholds 保持配置化；本阶段未为 benchmark 放宽；
- 同一 accused interval 的跨集主要竞争者 margin `< 0.05` 时返回
  `AMBIGUOUS`，primary 仅展示，alternatives 进入 audit/DB；
- 相隔 accused interval 映射到同一 source interval 是 `source_reuse`，不是
  contradiction；
- candidate GREEN 仅作 diagnostic，所有生产结果仍为 `REVIEW_REQUIRED`；
- 未实现 8 FPS 或 media-PTS boundary refinement。

Phase 2A-0 实测要求最终设计显式容忍 `±1` 个 source grid step（当前 2 FPS → 0.5 秒）的 retrieval 偏差；这不是可选优化，而是 89% 的 strict miss 属于"相邻格可用"这一实测结果所对应的必要条件。

## 时间戳语义

当前 index/query 使用 `nominal_grid_time = frame_index / fps`，与真实 decoded media PTS 存在已观察到的系统偏移（两侧同源时通常抵消）。禁止硬编码固定 correction。Segment、DB 与 audit 均保存 `timestamp_basis=nominal_grid_time`；audit 另行标记 `authoritative_media_timeline.basis=media_pts`、`status=NOT_YET_REFINED`、`legal_delivery_allowed=false`。

因此本实现可以用于内部候选定位和人工复核，但 nominal boundary 不能直接成为法律交付时间。后续独立 boundary-refinement 层必须用 decoder 提供的 authoritative media PTS，将 fitted nominal endpoints 映射或局部精修后，才能允许法律交付或 Excel 使用。该层不属于本 ADR 本次实现。

**Phase 2A.1 架构裁定**：authoritative media PTS 映射**不是 Phase 2A 的阻断条件**，也不在 Phase 2A.1 实施。它是 legal-facing evidence analysis 与最终 Excel 输出之前的硬性前置，将在 **Phase 3 Evidence Analysis 边界**处理。任何阶段都禁止硬编码 `+0.23` 秒或其它常数补偿。

因此 `source boundary MAE` 一类指标是 **matcher 内部边界误差**，不是相对权威媒体时间轴的误差；两者在报告中必须分开陈述。

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

Phase 2A regression 明确覆盖短 span 为 null、合并后重拟合、0.75–1.50 speed sweep、±1 grid jitter、piecewise、多 episode、unrelated gaps 与 source reuse。

## 实施约束

本 ADR 已按 Architecture Owner 的 Phase 2A 授权实施。未发现需要 Architecture
Owner 批准的静默偏离：初始 hypothesis 使用固定有界 slope grid 而不是对全部候选
做 pair 枚举；候选 pair 只在 robust refit 内以最多 32 点的确定性采样使用。这是为
满足 sparse、bounded 与避免无界 `O(N²)` 的工程化实现，仍联合估计 slope/offset，
没有退回 constant-offset-only。

实现结果与限制详见
`docs/review/PHASE2A-TEMPORAL-ALIGNMENT-V2-COMPLETION-REPORT.md`。
