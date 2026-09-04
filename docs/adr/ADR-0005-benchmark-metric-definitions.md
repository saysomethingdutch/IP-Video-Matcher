# ADR-0005：Benchmark Metric Definitions

状态：已接受，Phase 2A.2 integrity / boundary semantics 修订

修订：Phase 2A-0.2 新增 retrieval 聚类推断与 alignment-usable 口径。

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

Real temporal benchmark 的 case-level outcome 固定为四类，互斥且总和必须等于 case 数：

```text
CORRECT_UNAMBIGUOUS_MATCH
SAFE_AMBIGUITY_CONTAINS_GT
UNSAFE_WRONG_MATCH
NO_MATCH
```

`AMBIGUOUS` 即使 primary 正确也只能归入 `SAFE_AMBIGUITY_CONTAINS_GT`。Alternative
只有在 accused interval、episode、source interval、duration 与 slope/speed 全部匹配 GT
时才能证明“contains GT”。多余 prediction 计 FP，且会阻止
`CORRECT_UNAMBIGUOUS_MATCH`。

边界安全另用正交字段报告，不改变上述四分类和 recall：

```text
FULL_MAPPING_CORRECT
CONTAINED_PARTIAL_EVIDENCE
INCORRECT_COMPLETE_ASSERTION
UNRESOLVED_OR_INCORRECT_MAPPING
NO_OUTPUT
```

明确标记 `INCOMPLETE` 的 contained partial evidence 可以保留供人工复核，但不能按完整
GT 命中，也不能借新 taxonomy 提高 recall。

Retrieval attribution 同时报告 `retrieval_sufficient_for_any_match` 与
`retrieval_sufficient_for_full_boundary`。Tolerated hole 只延续 temporal span，不计作
actual hit；必须另报 actual supported frame count、longest actual-hit run、run temporal
span、tolerated holes 与 supported accused start/end。

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

## Retrieval 双口径

真实 retrieval benchmark 同时报告两个口径，均以 `top-k` 判定，不得只报严格口径：

- `strict_retrieval_recall@k`：正确 source-grid frame 精确进入 top-k；
- `alignment_usable_recall@k`：top-k 中只要存在与真实 source timestamp 相差不超过 `±1` 个 source grid step 的候选，即记为 alignment-usable hit。

`±1` grid step 的秒数从 sampling configuration 推导（当前 2 FPS → 0.5 秒），不得在统计层硬编码。严格口径保留为定位精度指标，alignment-usable 口径描述"是否把足够的正确时序证据交给 Temporal Alignment"。

## Retrieval 聚类推断与阈值三态

主要 inference 单元是 sampled interval，不是 query frame。同一 interval 内的 query frames 高度相关；direct 与各 transcode profile 派生于同一 source interval 时，不得视为独立样本翻倍。

每个 profile 至少报告 `interval_count`、`distinct_content_position_count`、`nominal_query_frame_count`、`intra_cluster_correlation`、`effective_sample_size`，以及 interval-level cluster bootstrap 95% CI。bootstrap 的 iteration 数与 seed 必须写入 report。

对于 95% 阈值，`threshold_band` 取三态：

```text
CI lower >= 0.95  -> ABOVE
CI upper <  0.95  -> BELOW
otherwise          -> STRADDLES
```

报告同时保留 point estimate、confidence interval 和 threshold band；不得用 point estimate 直接宣布通过。

`episode_count == 2` 时，`by_episode` 仅作描述性结果，不得用于估计集间方差或声称某集系统性更难。

## Retrieval Readiness Gate（Phase 2A-0.3 裁定）

进入 Temporal Alignment v2 的 readiness gate 指标正式定为：

```text
alignment_usable_recall@8
```

判定依据是该指标自身的 interval-level cluster bootstrap 95% CI 下界与 0.95 阈值的关系。

`strict_retrieval_recall@k` 保留为 **localization diagnostic**，衡量精确 source-grid 定位能力，**不作为 readiness gate**。它仍必须报告，并在 Temporal Alignment v2 的 8 FPS 精对齐落地后作为边界精度基线。

理由：source index 只在 `1 / coarse_fps` 的网格上存在样本，因此"命中相邻一格"与"命中精确一格"对下游时序对齐几乎等价——±1 grid step 的偏差落在 ADR-0002 的软投票容忍范围与 Phase 1.5 gate 的 ≤1 秒 source tolerance 之内。实测中 89% 的 strict miss 仍把相邻 source grid 带回 top-8。

## Threshold band 的指标归属

`threshold_band` 这一裸字段**已废弃**，不再产出。报告必须同时给出两个具名 band：

```text
strict_threshold_band             <- 由 strict recall@8 的 CI 推出
alignment_usable_threshold_band   <- 由 alignment-usable recall@8 的 CI 推出（= readiness gate）
```

band 枚举为四态：

```text
CI lower >= 0.95  -> ABOVE
CI upper <  0.95  -> BELOW
两者之间           -> STRADDLES
CI 缺失            -> UNKNOWN
```

`UNKNOWN` 与 `STRADDLES` 必须区分：bootstrap 无法产出区间时不得静默显示为 `STRADDLES`。

任何摘要表中，**置信区间必须与由它推出的 band 相邻呈现**；不得把 alignment-usable CI 与 strict band 并列。

## 聚类统计量的指标归属

`intra_cluster_correlation`、`design_effect`、`effective_sample_size`、`confidence_interval` 必须分别对 `strict` 与 `alignment_usable` 建模并置于各自的命名块下。禁止使用不带指标限定的裸字段——两个口径的 hit 率与方差结构不同，共用统计量会造成指标归属歧义。

## Source grid period 的推导

`source_grid_period_seconds` 由 `1.0 / matching.coarse_fps` 推导，`alignment_usable_tolerance_seconds = alignment_usable_grid_steps × source_grid_period_seconds`。统计实现中不得出现裸 `0.5` 字面量。当前阶段保留 `coarse_fps == 2.0` 的守卫。

## Platform-like transcode 的解释边界

transcode proxy（`p1` / `p2` / `p3`）只改变 **compression / resolution / codec**。实测这三类扰动对当前低频 DCT descriptor 的影响量级为 `1 - cos ≈ 6e-6 ~ 1.3e-5`，比 correct 与 best-wrong 帧之间的 median score margin（约 `3.7e-3`）低 2–3 个数量级；即使压到 180p / 60 kbps，扰动仍比该 margin 小约一个数量级。

因此这些结果的正确解释是：

> **compression / resolution / codec 的扰动低于当前低频 DCT descriptor 的主要灵敏度范围。**

**不得**泛化为对 crop、mirror、subtitle overlay、picture-in-picture、摄像头翻拍或 Reaction 画中画的 robustness 结论——这些变换会改变低频结构，属于未测量的另一类威胁向量。

## Benchmark 编码确定性

platform transcode profile 必须使用单线程、无帧级并行的编码参数（x264：`threads=1:sliced-threads=0:lookahead-threads=1:deterministic=1`；x265：`pools=none:frame-threads=1:wpp=0`）。多线程 x264/x265 不是位确定的，会带来 1–5 帧 / 1200 的 run-to-run 漂移，使小于该漂移的 profile 间差异不可复现。
