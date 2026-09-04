# Phase 2A.2 — Integrity & Boundary Semantics Closure Report

日期：2026-09-04

基线：`c512a80 Complete Phase 2A.1 calibration and safety closure`

提交：本报告与代码将以 `Complete Phase 2A.2 integrity and boundary closure` 一并提交；
commit 不能可靠地在自身内容中记录自身 hash，最终 hash 以 `git log -1` 为准。

Gate：本报告不宣布 Phase 2A PASS。独立 reviewer 与 Architecture Owner 仍需裁定。

## 1. 接管状态与处置

接管时工作树有 14 个已修改代码/配置/测试文件，diff 约 `+1696/-170`；另有两份尚未
跟踪的 Phase 2A.1 review 文档和用户已有的 `.DS_Store` 变化。`.DS_Store` 未修改、未暂存。
接管时使用项目虚拟环境运行测试，结果为 `83 passed`。

对 Claude Code 中断前的改动逐项处理如下：

| 处置 | 内容 |
|---|---|
| 保留方向 | `BoundaryCompleteness` 数据模型；truncation / warning 贯通骨架；v6 schema 意图；source-grid snapping；四类 taxonomy 骨架；以完整 mapping 比较 temporal line 的方向 |
| 重写或修正 | migration 执行顺序与 poisoned-v5 repair；change-point refinement；actual-hit run 统计；boundary orphan 判定；rival quality margin；alternative 完整 mapping 校验；case / position 汇总和 failure attribution |
| 删除 | 对单个 fitted segment 的递归盲切；benchmark 的 `getattr(..., [])` 静默回退；未被实现读取的 `explanation_span_saturation_seconds` 与 `maximum_temporal_residual_seconds` 配置 |

未 reset 工作树，也未以保留原作者修改为目标。每项保留都由 regression 或 fresh probe
复核。

## 2. 实现闭环

### 2.1 Temporal-line compatibility

统一使用：

```text
source_time = slope * accused_time + intercept
```

同 episode proposal 在 union accused domain 两端比较 mapping divergence；阈值由 source
grid uncertainty 推导。该函数用于 suppression、rival preservation、alternatives、
conflict 与 merge。Cross-episode 永不相容。

Regression 覆盖：same mapping / different boundaries 为 compatible；same start / slope
1.0→1.25 为 incompatible；cross-episode 为 incompatible；same line + grid jitter 不产生
artificial ambiguity。Fresh synthetic 中检查 54 对 primary–alternative：11 对跨集，43
对同集但时间线确实分叉，**0 对**为同集 near-equivalent same-line。

### 2.2 Change-point refinement

没有改 sparse voting、RANSAC 主结构或 hypothesis extraction。Refinement 只看已经提取的
相邻、同集、source 连续且 temporal-line incompatible 的 segment pair；从实际 candidate
frame 构建路径，两侧均需 minimum support，split 后的全证据 OLS residual 改善必须达到
配置阈值。不对缺失边界做外推。

Fresh deterministic probes：

| GT speed / accused boundary | prediction accused | prediction source | fitted speed |
|---|---|---|---|
| 0.8: `[0,12]` | `[0,12]` | `[100.014,109.587]` | 0.7978 |
| 1.25: `[12,24]` | `[12,24]` | `[109.540,124.587]` | 1.2539 |
| 1.25: `[0,12]` | `[0,12]` | `[99.961,115.055]` | 1.2578 |
| 0.8: `[12,24]` | `[12,24]` | `[115.014,124.587]` | 0.7978 |
| 1.0: `[0,12]` | `[0,12]` | `[100.000,112.000]` | 1.0000 |
| 1.25: `[12,24]` | `[12,24]` | `[111.961,127.055]` | 1.2578 |

三段 0.8→1.25→1.0 得到三段，边界为 `[0,7.5] / [7.5,16] / [16,24]`，均在
source-aware tolerance 内。1.0→1.05 的两个 4 秒短段保持一段；same-line retrieval hole、
同斜率 score fluctuation 均不误切；source reuse + speed change 保留三段并降级复用段。

Tracked ideal 的 `IDEAL_SPEED_DISCONTINUITY` 使用另一组确定性 grid candidate，GT 两段均
命中：prediction 为 `[0,12] a=0.7978` 与 `[12,24] a=1.2474`，TP/FP/FN=`2/0/0`。

### 2.3 Boundary completeness 与 cap

数据语义为 `FULL / INCOMPLETE / UNKNOWN`。当前实现只在 emitted span 外发现至少两个不同
accused frame 的、与同一 temporal line 相容的 orphan support 时标记 `INCOMPLETE`；相邻
已输出的 piecewise segment evidence 不算 orphan。无法证明完整或缺失时保持 `UNKNOWN`，
不因 RMS、IR 或 coverage 好看而默认 `FULL`。

`INCOMPLETE` 和命中 per-episode segment cap 均禁止 candidate GREEN。Cap 会生成
`ALIGNMENT_SEGMENT_CAP_REACHED` structured warning，并将 episode、limit 与受影响 segment
写入：

```text
TemporalAlignmentOutcome → AnalysisResult → matcher → DB → audit → CLI → benchmark
```

Production `review_tier` 始终为 `REVIEW_REQUIRED`。

### 2.4 Retrieval attribution 与 taxonomy

Benchmark 对每个 case 单独记录：

- `actual_supported_frame_count`
- `longest_run_actual_hit_count`
- `longest_run_temporal_span_seconds`
- `tolerated_hole_count`
- `supported_accused_start/end`
- `retrieval_sufficient_for_any_match`
- `retrieval_sufficient_for_full_boundary`

Tolerated hole 只延续 run span，不增加 actual hit count。Functional taxonomy 固定为：

```text
CORRECT_UNAMBIGUOUS_MATCH
SAFE_AMBIGUITY_CONTAINS_GT
UNSAFE_WRONG_MATCH
NO_MATCH
```

四类互斥并强制求和等于 case count。`AMBIGUOUS` 即使 primary 正确也只能是 SAFE；
alternative 必须同时匹配 accused interval、episode、source interval、duration 和
slope/speed。多余 prediction 计 FP，并阻止 `CORRECT_UNAMBIGUOUS_MATCH`。

Boundary assertion 是正交标签。`CONTAINED_PARTIAL_EVIDENCE` 不增加 TP 或 recall，避免用
新 taxonomy 隐藏不完整时间线。

## 3. Database migration

Schema version 为 6。每步均有独立 function，以 `BEGIN IMMEDIATE` 原子执行；成功后才在
同一事务更新 `user_version`，失败 rollback。Migration transaction 内不使用
`executescript`。迁移或 repair 前创建 `.pre-v6.bak`。

| 输入 | 路径 | 已验证结果 |
|---|---|---|
| fresh DB | create v6 | `user_version=6`，required tables/columns 齐全，`integrity_check=ok` |
| genuine v3 | v3→v4→v5→v6 | ladder test 通过 |
| genuine v4 | v4→v5→v6 | old row 保留；新增列齐全；backup 存在 |
| v4 step failure | v4→v5 中断 | rollback，保持 v4，old row 保留 |
| v5 step failure | v5→v6 中断 | 已完成 v5 保留，不产生半个 v6 |
| poisoned v5 | repair v5→validate→v6 | 先补 v5 required columns，再升级 v6；old row 保留 |
| poisoned-v5 repair failure | repair 中断 | rollback，仍为 v5 且缺列，初始化失败 |
| missing required table | introspection | backup 后抛 `SchemaIntegrityError`，不猜测重建 |
| newer user_version | none | 响亮拒绝 |

真实 benchmark 使用 `real-retrieval` source-index DB 的 scratch copy；该源索引库 fresh
检查前后均为 v4、2 assets，未对它执行 initialize 或 migration。默认空 workspace DB 和
benchmark demo DB 曾由测试流程迁移到 v6；它们不含 asset、accused 或 segment 数据。

## 4. Tests

| 阶段 | 结果 |
|---|---:|
| 接管时 | 83 passed |
| 最终全量 | **107 passed in 6.71s** |
| migration focused | 9 passed，6 deselected |

新增/强化的 regression 包括：boundary completeness / truncated GREEN、segment-cap warning
贯通、temporal-line rival compatibility、双向/三段 speed change、short near-equal slope、
retrieval hole、score fluctuation、source reuse、out-of-range semantics、taxonomy sum 与重复
prediction FP、SAFE GT present/absent、NO_MATCH、correct-episode/wrong-time、source-grid
invariant、genuine v4、poisoned-v5 repair 和逐步 rollback。

Grid jitter 不再按一次输出机械放宽。当前 grid-realistic generator 的 deterministic 输出为
`a=1.2581`、accused `[0,12]`、21/21 inliers、IR=1.0、RMS=0.2776。测试的最低 support
来自生成模式本身：24 个真实线 frame 中每四帧最多损失一帧，因此要求至少 18，不是取
当前结果 21 作为脆弱常数。旧的未 snap raw probe（`a=1.2946, 19/24`）不是当前
grid-realistic benchmark 输入，不用于 Gate。

## 5. Ideal benchmark

Fresh artifact：`benchmark/temporal/results/phase2a-temporal-ideal-v0.1.json`，schema v0.2。

| 项目 | 结果 |
|---|---:|
| total cases | 17 |
| gating / diagnostic cases | 12 / 5 |
| candidate count | 1824 |
| grid aligned / non-grid | 1824 / 0 |
| gating GT / prediction | 16 / 16 |
| gating TP / FP / FN | 16 / 0 / 0 |
| gating precision / recall | 1.0 / 1.0 |
| source boundary MAE | 0.02875 s |
| speed MAE | 0.00693 |

Out-of-range diagnostics 断言 `speed_ratio=null`、`speed_out_of_range=true`、
`unconstrained_speed_ratio` 存在且非 GREEN。Pure noise 为 0 segment。Speed-discontinuity
Gate 现在校验 source-aware TP、FP、预测段数和真正 GT boundary，不再只要求
`predicted_segments >= 2`。

## 6. Real 120-case benchmark

配置保持 2 FPS source / 2 FPS query / top-k 8。两集各 10 个 content positions；每个位置
共享 direct、P1、P2、P3、0.8x、1.25x 六个 profile。`episode_n=2`，120 cases 不是 120
个独立样本。

### 6.1 Case-level

| 指标 | 结果 |
|---|---:|
| cases / content positions | 120 / 20 |
| GT / prediction | 120 / 120 |
| TP / FP / FN | 109 / 11 / 11 |
| precision / recall | 0.9083 / 0.9083 |
| episode correctness | 0.9417 |
| source boundary MAE | 0.1993 s |
| accused boundary MAE | 0.0692 s |
| candidate GREEN count / precision | 93 / 1.0（diagnostic only） |
| matcher runtime sum | 32.67 s |

Outcome：

| 分类 | n |
|---|---:|
| CORRECT_UNAMBIGUOUS_MATCH | 96 |
| SAFE_AMBIGUITY_CONTAINS_GT | 20 |
| UNSAFE_WRONG_MATCH | 4 |
| NO_MATCH | 0 |

Boundary completeness：116 `UNKNOWN`、4 `INCOMPLETE`、0 `FULL`。Boundary assertion：109
`FULL_MAPPING_CORRECT`、4 `CONTAINED_PARTIAL_EVIDENCE`、7
`UNRESOLVED_OR_INCORRECT_MAPPING`。四个 partial 仍在 functional taxonomy 中算
`UNSAFE_WRONG_MATCH`，因此没有提高 recall。

所有 120 cases 都 `sufficient_for_any_match=true`；110 个为
`sufficient_for_full_boundary=true`。11 个 functional miss 中，4 个归因为
`RETRIEVAL_INSUFFICIENT_FOR_FULL_BOUNDARY`，7 个为 `ALIGNMENT_LIMIT`，0 个为
`RETRIEVAL_INSUFFICIENT_FOR_ANY_MATCH`。

### 6.2 Profile-level

| profile | TP/FP/FN | precision/recall | ambiguity | boundary incomplete |
|---|---:|---:|---:|---:|
| direct | 18/2/2 | 0.90/0.90 | 2/20 | 1 |
| P1 | 18/2/2 | 0.90/0.90 | 2/20 | 1 |
| P2 | 18/2/2 | 0.90/0.90 | 2/20 | 1 |
| P3 | 18/2/2 | 0.90/0.90 | 2/20 | 1 |
| 0.8x | 19/1/1 | 0.95/0.95 | 10/20 | 0 |
| 1.25x | 18/2/2 | 0.90/0.90 | 2/20 | 0 |

### 6.3 Position-level matrix

缩写：`C=CORRECT_UNAMBIGUOUS_MATCH`，`A=SAFE_AMBIGUITY_CONTAINS_GT`，
`W=UNSAFE_WRONG_MATCH`，`P=contained partial evidence`。

| position | direct | P1 | P2 | P3 | 0.8x | 1.25x | boundary |
|---|---:|---:|---:|---:|---:|---:|---:|
| E001-I001 | C | C | C | C | C | C | — |
| E001-I002 | C | C | C | C | A | C | — |
| E001-I003 | C | C | C | C | C | C | — |
| E001-I004 | C | C | C | C | C | C | — |
| E001-I005 | C | C | C | C | C | C | — |
| E001-I006 | C | C | C | C | A | C | — |
| E001-I007 | C | C | C | C | A | C | — |
| E001-I008 | W | W | W | W | C | C | P |
| E001-I009 | C | C | C | C | A | C | — |
| E001-I010 | A | A | A | A | A | A | — |
| E002-I001 | A | A | A | A | A | A | — |
| E002-I002 | C | C | C | C | A | C | — |
| E002-I003 | C | C | C | C | C | C | — |
| E002-I004 | C | C | C | C | A | C | — |
| E002-I005 | C | C | C | C | C | C | — |
| E002-I006 | C | C | C | C | A | C | — |
| E002-I007 | C | C | C | C | C | C | — |
| E002-I008 | C | C | C | C | A | C | — |
| E002-I009 | C | C | C | C | C | C | — |
| E002-I010 | C | C | C | C | C | C | — |

9/20 positions 的六个 profile 全部非歧义且完整命中；19/20 positions 没有 unsafe wrong；
1/20 position 含 confirmed partial evidence。Direct/P1/P2/P3 高度 paired，不能当成四份
独立内容证据。

### 6.4 E001-I008 final semantics

四个 direct/P1/P2/P3 case 结果一致：

```text
GT accused:                  0.0–6.0
GT source:                   1867.603–1873.603
confirmed accused span:      2.5–6.0
confirmed source span:       1870.0–1873.5
actual supported frames:     9 / 12
longest actual-hit run:      7 frames
longest run temporal span:   3.5 s
tolerated holes in that run: 0
sufficient for any match:    true
sufficient for full boundary:false
boundary completeness:       INCOMPLETE
candidate / production tier: YELLOW / REVIEW_REQUIRED
```

原因是 segment 前存在两个与同一 line 相容的 orphan hits，但支持不足以与后段形成完整
continuous run。系统保留可确认的 2.5–6.0 秒，不外推 0–2.5 秒；同时明确声明边界不完整。
Functional evaluator 仍将其四例记为 unsafe wrong，正交 boundary label 才表达“部分证据
本身正确”。这四例不再能获得 candidate GREEN。

## 7. Synthetic A–M

Fresh A–M 结果：

| GT / prediction | TP / FP / FN | precision | recall | status |
|---:|---:|---:|---:|---|
| 80 / 69 | 36 / 33 / 44 | 0.5217 | 0.4500 | 37 AMBIGUOUS / 22 MATCH / 13 NOT_FOUND / 1 INCOMPLETE_INDEX |

与 `3f5f4d5` 的 TP/FP/FN 完全一致；没有修改 sampling、GT 或 synthetic evaluator 来换分。
因此它仍是 DCT-backend-conditioned diagnostic，不是 production accuracy。

Ambiguity 历史：

| revision | ambiguous / 73 |
|---|---:|
| `3f5f4d5` | 1 / 73 |
| `c512a80` 独立 review 记录 | 33 / 73 |
| 接管 Claude partial fresh run | 37 / 73 |
| Phase 2A.2 current fresh run | 37 / 73 |

总 ambiguity 没有恢复到 1/73，也没有被隐藏。Phase 2A.2 关闭的是 near-equivalent
same-line proposal 人工制造 ambiguity 的机制；current 54 个 rival pair 中没有这种 pair。
剩余 competition 由 DCT retrieval 给出的跨集或同集不同 temporal line 构成。是否继续处理
属于后续独立 Gate 决策，不能通过放宽 margin 删除。

Synthetic candidate GREEN 为 20 个、precision 0.35，进一步证明 GREEN 只能是内部
alignment-quality diagnostic。

## 8. Pure-noise、structured-wrong 与性能

Reviewer-owned pure-noise fresh probes：

| episode n | duration | top-k | candidates | segments | runtime |
|---:|---:|---:|---:|---:|---:|
| 1 | 30 s | 8 | 480 | 0 | 0.0158 s |
| 1 | 300 s | 32 | 19200 | 0 | 0.8355 s |
| 5 | 30 s | 8 | 480 | 0 | 0.0132 s |
| 5 | 300 s | 32 | 19200 | 0 | 0.7620 s |

一条相干但错误的 structured line 仍会产生一个 candidate GREEN segment；production tier
仍为 `REVIEW_REQUIRED`。Temporal coherence 无法单独判断视觉上相干的错误线，这是 visual /
retrieval backend 的能力边界。本阶段没有为了消除它破坏 temporal alignment。

Real matcher runtime sum 为 32.67 秒；`c512a80` 记录为 31.27 秒，当前单次 fresh run 高约
4.5%，没有观察到数量级或显著性能回归。新 compatibility 只在线段对上工作；change-point
只检查已经有界的相邻 segment pair，受 per-episode segment cap 和 maximum split 数限制，
没有新增全候选的无界 `O(N²)`。

## 9. 已知限制与 scope audit

1. Boundary `FULL` 当前没有 production 证据规则，因此大部分结果诚实保留为 `UNKNOWN`；
   internal mapping 命中 GT 不等于 production 证明完整。
2. E001-I008 四例仍未恢复完整 GT 时间线；新语义关闭的是 unsafe assertion / GREEN 风险，
   不是 boundary recovery 能力。
3. Real benchmark 只有两集、20 个独立位置，不能估计跨集稳定性。
4. Synthetic ambiguity 仍高；已排除 near-equivalent same-line 机制，但 DCT 候选层仍产生
   大量互不相容的竞争线。
5. Coherent wrong line 仍可能被 temporal layer 接受；不能由 temporal consistency 自证视觉
   正确性。
6. Candidate GREEN 不含 production release 语义，且不证明 completeness；所有结果继续
   `REVIEW_REQUIRED`。
7. Endpoints 仍是 `nominal_grid_time`，尚未映射到 authoritative media PTS，不能法律交付。

Scope 检查：DCT 保持不变；source/query 仍 2 FPS；未引入 DINOv2、SigLIP、FAISS、VDF、
crop/mirror/subtitle/PiP matcher、audio、Gemma、ASR、Excel 或 Phase 3；未修改 production
visual matcher。真实媒体、文件名、hash、本机绝对路径与逐帧可识别结果未进入 Git。

## 10. 停止条件

Phase 2A.2 implementation、tests、fresh ideal / real / synthetic characterization 和文档已
完成。此处停止，不自行宣布 Phase 2A PASS，不进入下一阶段。
