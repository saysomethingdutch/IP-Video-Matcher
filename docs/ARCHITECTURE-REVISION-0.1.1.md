# Architecture Revision 0.1.1

状态：生效

日期：2026-09-02

本修订补充 Architecture Baseline v0.1，不改写历史文档。若与 v0.1 冲突，以本修订为准。

## 1. 阶段裁定

Architecture v0.1 §16 的镜像、0.75–1.5× 变速、字幕和裁剪指标，是 Phase 2 结束时的 Gate，不是 Phase 1 Gate。

Phase 1 只验证直接剪切、常规重编码、基础多段拼接，以及：

```text
被诉时间轴 → 集数 → 原片时间轴
```

Phase 1 独立 Review 的裁定为 `CONDITIONAL PASS`。Phase 2 开始前新增 Phase 1.5：Measurement & Safety Hardening。本阶段只修 benchmark、状态语义、索引完整性、数据作用域和审计链，不增加匹配能力。

## 2. 匹配状态

匹配结果必须使用以下一等状态：

- `MATCH_FOUND`
- `MATCH_NOT_FOUND`
- `AMBIGUOUS`
- `INCOMPLETE_INDEX`
- `ERROR`

等价竞争候选不能唯一消歧时返回 `AMBIGUOUS` 和主要候选。项目索引不完整时返回 `INCOMPLETE_INDEX`，且不得生成 `MATCH_FOUND`。

## 3. Match Score 与放行条件

原 `overall_confidence` 更名为 `match_score`。它只表示内部匹配证据强度，不是结果正确概率。Phase 1.5 不训练概率校准器。

GREEN 不能由单一分数阈值触发。它至少要求索引完整、无等价竞争者、唯一性 margin 达标、时间残差达标、coverage 与 temporal consistency 达标、无 segment contradiction。阈值定义见 ADR-0003，均属于 synthetic regression threshold，不得解释为概率。

## 4. 项目作用域

V1 中一个 project 对应一部权利作品。`original_assets` 必须引用 `project_id`；episode 唯一性是 `UNIQUE(project_id, episode)`。

集数来源顺序：

1. `input/originals/metadata.json` 显式映射；
2. 明确且唯一的 filename pattern；
3. 无法唯一解析时 `NEED_METADATA`，不得使用任意数字猜测。

## 5. Golden Set

真实 golden set 放在 `input/golden/`，与 synthetic benchmark 共用 ground-truth schema。真实视频、案件名称、文件 hash 和详细案件结果不得提交 Git。

## 6. Phase 1.5 Gate

在 ADR-0005 指标下：

- off-grid direct cut/re-encode：source-aware recall@1s ≥ 95%；
- segment precision@1s ≥ 98%；
- source boundary MAE ≤ 1.0 秒，且漏检必须单独报告；
- pure negative：false-positive matched seconds = 0；
- exact duplicate cross-episode：`AMBIGUOUS` 或合法上下文消歧；
- incomplete index：必须拒绝，不能产生 `MATCH_FOUND`。

这些是 synthetic regression gates，不是对真实案件准确率的声明。

## 7. 暂停项

Phase 1.5 完成后停止，等待第二次独立 Review 和架构负责人 Gate 决策。DINOv2、SigLIP、FAISS、Temporal Alignment v2、镜像、变速、crop、audio、ASR、Gemma、合理使用和 Excel 均不得在本阶段实现。

## 8. Phase 1.5 第二次 Review 后执行顺序

第二次独立 Review 裁定：

- Phase 1 Gate：`FAIL`，等待后续能力改善；
- Phase 1.5：`CONDITIONAL PASS`；
- 先执行 Phase 1.5.1 Safety Closure；
- Safety Closure 验收后只进入 Phase 2A-0 Retrieval Characterization；
- Phase 2A-0 是测量工具，不是 matcher 能力变更。

Phase 2A-0 只测真实素材在 source/query 2 FPS 下的 retrieval recall@1/8/32/128、rank 与 margin。当前仍禁止 Temporal Alignment v2、DINOv2、FAISS、非对称采样、8 FPS 外部验证及其它 Phase 2 能力。测量完成后必须停止，由 Architecture Owner 按决策树裁定后续路线。

## 9. Phase 2A-0 关闭与 Temporal Alignment v2 授权

Phase 2A-0 已由 Architecture Owner 判定 `PASS / CLOSED`。Phase 2A 的锁定输入为：

```text
visual backend = DCT
source FPS = 2
query FPS = 2
retrieval top-k = 8
```

Phase 2A 只用 piecewise `source_time = a * accused_time + b` 替换
constant-offset temporal proposal，其中 `a ∈ [0.75, 1.50]`。算法必须显式容忍
±1 个 source grid step、支持多段/多集/不同速度/unrelated gaps/source reuse，
并保留 Phase 1.5.1 的 ambiguity、contradiction 与 GREEN safety 语义。

本阶段禁止同时更换 visual backend、source FPS 或 top-k，禁止新增 crop、mirror、
subtitle、PiP、camera、audio、ASR、Gemma、fair-use 与 Excel 能力。

## 10. Temporal Alignment v2 时间戳与法律交付约束

Temporal Alignment v2 内部输出为 `nominal_grid_time`。Segment model、SQLite 与
audit 必须携带该 basis。已观察到 nominal grid 与 media content PTS 的系统偏移，
但不得硬编码任何常量 correction。

在 authoritative media-PTS boundary refinement 完成前：

- `source_start/source_end` 只能作为内部定位与人工复核候选；
- audit 必须标记 media-PTS refinement 尚未完成；
- 不能将 nominal endpoints 作为法律交付或未来 Excel 的最终时间；
- candidate GREEN 仍不具有自动放行语义，生产 downstream 一律
  `REVIEW_REQUIRED`。

Phase 2A 实现完成后必须停止，等待 Claude Code 独立复核与 Architecture Owner
Gate，不得自行进入其他 Phase 2 能力。

## 9. Phase 2A.1 Calibration & Safety Closure 裁定

### 9.1 AMBIGUOUS 与 segments 并存（有意）

`status = AMBIGUOUS` 可以与非空 `segments` / `alternatives` 并存。这是有意设计，不得回退为清空全部 segment。

下游语义必须显式：

1. **先检查 case/segment status**，再看 `segments`；
2. **非空 `segments` 绝不意味着自动接受**；
3. 冲突或歧义的 segment 保持 `review_tier = REVIEW_REQUIRED`；
4. 未受影响的可靠 segment 可以保留。

生产 `review_tier` 恒为 `REVIEW_REQUIRED`；`candidate_tier`（含 `GREEN`）仅为诊断量，不得作为自动放行依据。

### 9.2 Phase 1 end-to-end synthetic gate

```text
Phase 1 end-to-end synthetic gate = FAIL / BACKEND-DEPENDENT / DEFERRED
```

该 gate 建立在低分辨率合成语料上，其视觉检索质量已被多轮独立审查证明不足以支撑 precision 结论。

- 旧结果**不得**被改写为 PASS；
- 它**不再阻断** Temporal Alignment 的关闭；
- 必须在 geometric retrieval / visual backend 工作之后**重新运行**。

### 9.3 Timestamp policy

`timestamp_basis = nominal_grid_time` 在 Phase 2A 有效。authoritative media PTS 映射是 legal-facing evidence analysis 与最终 Excel 之前的硬性前置，在 **Phase 3 Evidence Analysis 边界**处理。禁止硬编码 +0.23 秒。

### 9.4 Frame-gap 标定

`maximum_frame_gap_seconds` 由 1.1 调整为 **1.5（provisional）**。依据：独立审查复现出 5 个真实 miss 仅由一个 1.5 秒候选空洞造成，调至 1.5 全部修复，且纯噪声 false positive 保持为 0。真实 benchmark 扩展到 2 集以上后必须重新标定。

### 9.5 Real benchmark outcome taxonomy

真实 benchmark 必须输出机器推导、互斥且求和等于 case 数的四分类：

```text
CORRECT_MATCH
SAFE_AMBIGUITY_CONTAINS_GT
UNSAFE_WRONG_MATCH
NO_MATCH
```

`UNSAFE_WRONG_MATCH` 包含任何未满足 ground-truth episode/timeline 要求的 `MATCH_FOUND`——**集数正确但源时间轴实质错误仍属 unsafe wrong match**。禁止人工转写计数。

