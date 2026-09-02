# Phase 2A-0.1 / 2A-0.2 Completion Report

## 1. Implementation summary

在不修改 production matcher、视觉 backend、source/query sampling 的前提下：

- 新增 `p1`（H.264 720p ~2 Mbps）、`p2`（H.264 480p ~1 Mbps）、`p3`（HEVC 720p ~1.5–2 Mbps）三个 platform-like transcode proxy profile；
- 保留 `direct` 作为 baseline，并保留 `reencode` 模式兼容旧调用；
- 正式新增 `alignment_usable_recall@k`（`±1` source grid step），与 `strict_retrieval_recall@k` 并列；
- 统计层改为 interval-level cluster inference：ICC、design effect、effective sample size、deterministic cluster bootstrap 95% CI；
- threshold band 改为 `ABOVE / STRADDLES / BELOW` 三态；
- phase / position / episode / profile 分组表同时报告 frame count 与 interval count，并为 phase / position bucket 输出结构化 bounds。

## 2. Files changed

- `src/ipvm/retrieval_benchmark.py`
- `src/ipvm/benchmarking.py`
- `src/ipvm/cli.py`
- `benchmark/retrieval/schema-v0.2.json`（新增）
- `benchmark/retrieval/anonymized-aggregate-template.json`
- `benchmark/retrieval/README.md`
- `docs/adr/ADR-0002-temporal-alignment-v2.md`
- `docs/adr/ADR-0005-benchmark-metric-definitions.md`
- `README.md`
- `tests/test_retrieval_benchmark.py`
- `benchmark/retrieval/results/phase2a-0-platform-characterization-v0.2.json`（匿名 aggregate）

## 3. Tests

`pytest -q`：`39 passed`。

新增覆盖：alignment-usable 边界（±1 grid 内算 usable、超过不算）、cluster bootstrap determinism、threshold `ABOVE / STRADDLES / BELOW`、profile isolation、interval count、direct/profile 不虚增 independent sample count、真实 decode off-grid 回归、匿名 aggregate 不含敏感 path/hash 字段。

## 4. Statistical methodology

- 主要 inference 单元为 sampled `interval`（每集 50，共 100）。
- 同一 interval 内的 query frames 被当作相关观测一起 resample；direct 与各 transcode profile 派生于同一 source interval，不视为独立样本翻倍。
- 每个 profile 独立报告 `interval_count`、`distinct_content_position_count`、`nominal_query_frame_count`、ICC、design effect、effective sample size。
- ICC 使用 one-way ANOVA 估计，对 strict recall@8 的 interval cluster 计算；设计效应 `1 + (mean_cluster_size - 1) * ICC`。
- 95% CI 使用 interval-level cluster bootstrap，deterministic seed `20260902`，iteration `2000`，percentile method。
- 不使用普通 frame-level binomial CI 替代 cluster bootstrap。

## 5. DIRECT baseline reproduction

`direct` 保持与 Phase 2A-0 相同的 time stratification、balanced off-grid phase、deterministic seed、interval 定义。

```text
strict recall@1   0.8183
strict recall@8   0.9533
strict recall@32  0.9558
strict recall@128 0.9608

usable recall@1   0.9167
usable recall@8   0.9942
usable recall@32  0.9950
usable recall@128 0.9967
```

strict recall@8 的 cluster-bootstrap 95% CI 为 `[0.9383, 0.9675]`，band `STRADDLES`。点估计仍保留。

## 6. P1 results

H.264 720p ~2 Mbps：

```text
strict recall@1/8/32/128  = 0.8167 / 0.9533 / 0.9558 / 0.9608
usable recall@1/8/32/128  = 0.9125 / 0.9933 / 0.9950 / 0.9967
strict recall@8 95% CI     = [0.9383, 0.9675]
threshold band             = STRADDLES
true loss@8                = 0.0067
```

## 7. P2 results

H.264 480p ~1 Mbps：

```text
strict recall@1/8/32/128  = 0.8125 / 0.9525 / 0.9558 / 0.9608
usable recall@1/8/32/128  = 0.9075 / 0.9933 / 0.9950 / 0.9967
strict recall@8 95% CI     = [0.9367, 0.9667]
threshold band             = STRADDLES
true loss@8                = 0.0067
```

## 8. P3 results

HEVC 720p ~1.5–2 Mbps：

```text
status                     = COMPLETED（本机 libx265 可用）
strict recall@1/8/32/128  = 0.8108 / 0.9533 / 0.9558 / 0.9608
usable recall@1/8/32/128  = 0.9117 / 0.9942 / 0.9950 / 0.9967
strict recall@8 95% CI     = [0.9383, 0.9675]
threshold band             = STRADDLES
true loss@8                = 0.0058
```

## 9. strict vs alignment-usable comparison

所有 profile 的 strict recall@8 都落在 `0.9525–0.9533`，而 alignment-usable recall@8 落在 `0.9933–0.9942`。差约 4 个百分点，全部来自"top-8 找到了相邻 source grid 而非精确 grid"的帧。对 Temporal Alignment 而言，这 4 个百分点是可用证据；真正丢失率在 `0.58%–0.67%`。

## 10. Cluster-bootstrap CI

strict recall@8 的 95% CI 四档均跨越 0.95，因此 threshold band 全部为 `STRADDLES`：

```text
DIRECT [0.9383, 0.9675]
P1     [0.9383, 0.9675]
P2     [0.9367, 0.9667]
P3     [0.9383, 0.9675]
```

alignment-usable recall@8 的 95% CI 均高于 0.98：

```text
DIRECT [0.9892, 0.9983]
P1     [0.9883, 0.9975]
P2     [0.9883, 0.9975]
P3     [0.9892, 0.9983]
```

## 11. True-loss analysis

以 `k=8` 计，`true_retrieval_loss_rate`（alignment-usable 也失败）为：

```text
DIRECT 0.58%
P1     0.67%
P2     0.67%
P3     0.58%
```

`adjacent_grid_only_miss_rate@8`（strict miss 但 alignment-usable hit）为：

```text
DIRECT 4.08%
P1     4.00%
P2     4.08%
P3     4.08%
```

即绝大多数 strict miss 仍把相邻 source grid 带回了 top-8。

## 12. Score-margin comparison

四档 score margin 分布几乎相同，说明本阶段的 platform-like transcode 代理没有明显挤压 DCT 判别裕度：

```text
          p10        median     p90
DIRECT    -0.004188  0.003683  0.091005
P1        -0.004211  0.003601  0.090450
P2        -0.004893  0.003621  0.090963
P3        -0.004739  0.003661  0.090430
```

注意：这里的 margin 是原始 cosine 尺度；生产 `CandidateRetriever` 的 uniqueness margin 使用 `(cos+1)/2`，两者不可直接比较。

## 13. Timestamp semantics update

本阶段未做 `timestamp += 0.23` 或任何硬编码补偿。schema / report 继续使用 `nominal_grid_time`。已把要求写入 `docs/adr/ADR-0002-temporal-alignment-v2.md`：在 Phase 2A 完成前，最终 `source_start/source_end` 必须能够映射或精修到真实 `media_pts`，才能进入法律交付物和未来 Excel。

## 14. Anonymization audit

`benchmark/retrieval/results/phase2a-0-platform-characterization-v0.2.json` 已 grep 确认不含：

```text
/Users/
real_test
EP01.mp4
EP02.mp4
sha256 / sha-256 / file_path / media_metadata
```

只含 `E001` / `E002`、`E001-I017` 别名、归一化 source position、benchmark config、aggregate statistics、CI 与 profile 名。

## 15. Known limitations

- `episode_count = 2`；`by_episode` 仅作描述，不能估计集间方差，也不能宣称某集系统性更难。
- platform-like transcode proxies 只改变 compression / resolution / codec 一类变量，不声称复现 Bilibili / 小红书 / YouTube 真实生产转码链。
- ICC 只对 strict recall@8 的 interval cluster 估计；episode 层 clustering 未建模，因此 effective sample size 是乐观值。
- cluster bootstrap 的 resample unit 是 interval，没有进一步纳入 episode 层。
- 真实素材仅两集，future gate 需扩展到至少 5 episodes 后再决定是否替换 DCT visual backend。

## 16. Deferred / Architecture Owner Decision Needed

- 是否把 retrieval 官方口径设为 `alignment_usable_recall@k`，并写入 ADR-0005 已记录但最终裁定仍在 Owner。
- Temporal Alignment v2 是否显式采用 `±1` grid step 软投票。
- 是否需要补偿或改用真实 `media_pts` 作为权威输出时间。
- 在集数扩展到 ≥5 之前，`by_episode` 是否只报告不做比较。
- DINOv2 / 更大 top-k / 更高 source FPS 等路线本阶段未触碰，也未预选。

## Summary table（Phase 2A-0.3 已修正）

**修正说明**：本表的前一版把 `Usable 95% CI` 与由 **strict** CI 推出的 `Band` 并列，strict CI 完全未显示，读者按判定规则核对会得到相反结论。现每个置信区间与由它推出的 band 相邻呈现。数字取自 Phase 2A-0.3 的确定性重跑（`phase2a-0-platform-characterization-v0.3.json`）。

Readiness gate 指标为 `alignment_usable_recall@8`（ADR-0005）；strict recall 是 localization diagnostic，不 gate。

| Profile | Strict R@8 | Strict CI | Strict band | Usable R@8 | Usable CI | Usable band (gate) | True loss@8 |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.9533 | [0.9383, 0.9675] | STRADDLES | 0.9942 | [0.9892, 0.9983] | **ABOVE** | 0.58% |
| p1     | 0.9525 | [0.9367, 0.9667] | STRADDLES | 0.9933 | [0.9883, 0.9975] | **ABOVE** | 0.67% |
| p2     | 0.9525 | [0.9367, 0.9667] | STRADDLES | 0.9942 | [0.9892, 0.9983] | **ABOVE** | 0.58% |
| p3     | 0.9533 | [0.9383, 0.9675] | STRADDLES | 0.9942 | [0.9892, 0.9983] | **ABOVE** | 0.58% |

后续修正与本阶段的全部 remediation 记录见 `docs/review/PHASE2A-0.3-CLOSURE-REPORT.md`。§7/§11 中 profile 之间 `0.9942 vs 0.9933`、`0.58% vs 0.67%` 一类差异属于当时多线程编码器的 run-to-run 漂移（1–5 帧 / 1200），2A-0.3 固定编码参数后已消除，不应作为 profile 效应解读。

本报告只报告事实，不做 DINOv2 / Temporal Alignment v2 路线选择。
