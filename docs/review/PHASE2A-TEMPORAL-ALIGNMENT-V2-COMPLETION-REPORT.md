# Phase 2A — Temporal Alignment v2 Completion Report

日期：2026-09-03

状态：**IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT REVIEW / ARCHITECTURE OWNER GATE**

本报告不宣告 Phase 2A Gate 通过。实现、测试和规定 benchmark 已完成；端到端
结果同时包含明显改善和明确退化，须由 Architecture Owner 裁定。

## 1. Scope 与锁定输入

本轮只替换 temporal proposal/alignment：

```text
visual backend = DCT（未变）
source FPS = 2（未变）
query FPS = 2（未变）
retrieval top-k = 8（未变）
temporal model = source_time = a * accused_time + b
a range = [0.75, 1.50]
```

未实现 DINOv2、SigLIP、FAISS、source FPS 增密、crop、mirror、subtitle、PiP、
camera normalization、audio、ASR、Gemma、reaction、fair-use 或 Excel。GREEN 自动
放行没有恢复。

## 2. 实现概要

生产 aligner 现在执行：

```text
retrieval candidates
→ episode-local sparse slope/offset voting
→ offset neighboring-bin soft voting
→ bounded peak extraction + NMS
→ ±1 source-grid support collection
→ accused-time continuity split
→ deterministic robust local refit
→ iterative evidence claim / piecewise extraction
→ cross-episode competition
→ ambiguity / conflict / source-reuse safety classification
```

关键语义：

- slope grid 为 0.025，当前共 31 个 bins；offset/grid tolerance 均从
  `coarse_fps` 和 configuration 推导，不裸写 0.5 秒；
- 同一 query frame 对同一 vote cell 最多贡献一次；offset vote 按距离分配到相邻
  bins，避免 bin-edge split；
- robust refit 对最多 32 个时间均匀抽样点生成有界 pair hypotheses，拒绝小于 1 秒
  的 pair delta 和范围外 slope；
- peak support 先按 accused-time gap 拆分，再拟合、去 outlier、验证 minimum
  duration；接受的 inlier evidence 被 claim 后继续提取其余 segment；
- 相邻段只有在 episode、accused gap、source continuity 都兼容时合并；合并结果从
  全部原始 inliers 重拟合，不平均已有 `speed_ratio`；
- effective accused/source span 任一不足 3 秒时 `speed_ratio=null`；
- 相隔 accused 区间复用同一 source 区间记为 `source_reuse=true`，不是
  contradiction；相关段降为 YELLOW / `REVIEW_REQUIRED`；
- 同一 accused interval 存在跨集主要竞争者且 margin `<0.05` 时为
  `AMBIGUOUS`，primary 仅展示，alternatives 保留；不受冲突影响的独立段不清空。

## 3. 输出、数据库与 audit

Segment 新增/正式持久化：`fitted_offset_seconds`、`residual_rms_seconds`、
`residual_max_seconds`、`inlier_count`、`candidate_count`、`inlier_ratio`、
`source_reuse`、`timestamp_basis`。SQLite schema 升至 v4；显式
`BEGIN IMMEDIATE`、SQLite backup API 与 rollback regression 保证迁移原子性。

所有 segment 仍输出 `timestamp_basis=nominal_grid_time`。Audit schema 升至 0.4，
另行记录：

```text
authoritative_media_timeline.basis = media_pts
authoritative_media_timeline.status = NOT_YET_REFINED
authoritative_media_timeline.legal_delivery_allowed = false
```

未硬编码任何 PTS correction。Media-PTS boundary refinement 完成前，当前 nominal
endpoints 只能用于内部定位和人工复核，不能直接成为法律交付或未来 Excel 时间。

Candidate GREEN 仅是 diagnostic；生产 `review_tier` 仍一律
`REVIEW_REQUIRED`。

## 4. 测试结果

最终命令：

```bash
.venv/bin/pytest -q
```

最终结果见本报告第 11 节的复核记录。新增 regression 覆盖：

```text
65 passed in 3.57s
```

- speed 0.75 / 0.80 / 0.90 / 1.00 / 1.10 / 1.25 / 1.40 / 1.50；
- ±1 grid candidate jitter、同集高分 outlier、wrong-episode 高分 candidate；
- ≥5 piecewise segments、≥3 episodes、不同 speed、unrelated gaps、source reuse；
- 合并后完整 inlier refit；
- 短 span `speed_ratio=null`；
- ambiguity、contradiction 保留独立事实、source reuse 非 contradiction；
- v4 diagnostics persistence、audit timestamp basis；
- v3→v4 最后阶段注入失败后的原子 rollback。

## 5. Ideal-candidate benchmark

该 benchmark 不经过视觉 retrieval，因此隔离测量 temporal algorithm。

| Metric | Result |
|---|---:|
| Cases / GT segments | 10 / 14 |
| Predicted segments | 14 |
| Source-aware recall@1s | 100% |
| Segment precision@1s | 100% |
| Episode recall / correctness | 100% / 100% |
| Source boundary MAE | 0.0131 s |
| Accused boundary MAE | 0.0000 s |
| Speed-ratio MAE | 0.00278 |
| Mean residual RMS | 0.0176 s |
| Mean inlier ratio | 0.9962 |
| FP segments / seconds | 0 / 0 |
| Segment-count MAE | 0 |
| Candidate-GREEN precision | 100% diagnostic only |

Five-segment stress case 正确恢复 5 个 segment、3 个 episodes、1.0 / 1.25 /
0.8 / 1.1 / 1.0 speeds、两个 unrelated gaps，以及前后两次 source interval reuse。

可复核机器结果：
`benchmark/temporal/results/phase2a-temporal-ideal-v0.1.json`。

## 6. Expanded synthetic A–M：before / after

Before 是安全 tip 上的 constant-offset aligner；after 是本次 Temporal v2。两次均为
相同 deterministic synthetic A–M 配置。

| Aggregate metric | Before | After |
|---|---:|---:|
| GT / predictions | 80 / 59 | 80 / 69 |
| Episode recall | 68.75% | 78.75% |
| Episode correctness | 94.83% | 92.65% |
| Source-aware recall@1s | 45.00% | 45.00% |
| Segment precision@1s | 61.02% | 52.17% |
| Source boundary MAE | 1.200 s | 1.705 s |
| Accused boundary MAE | 0.171 s | 0.095 s |
| FP segments / seconds | 23 / 78.0 | 33 / 124.5 |
| Speed-ratio MAE | 0.210 | 0.0914 |
| Candidate-GREEN precision | 66.67% | 21.05% |
| Analysis runtime / RTF | 4.541 s / 0.0130 | 6.392 s / 0.0183 |
| Peak lower / conservative upper memory | 133.5 / 182.9 MB | 133.6 / 182.8 MB |

After-only diagnostics：ambiguity rate 1.37%，segment-count MAE 0.181，mean residual
RMS 0.104 秒，mean inlier ratio 1.0。

重点 transformation：

| Group | Recall before→after | Precision before→after | Source MAE before→after | FP segments before→after |
|---|---:|---:|---:|---:|
| direct cut | 40→40% | 40→40% | 2.101→2.084 s | 3→3 |
| normal reencode | 75→75% | 83.33→71.43% | 0.354→1.237 s | 3→6 |
| multi-episode concat | 80→70% | 88.89→70% | 0.359→1.100 s | 1→3 |
| speed 0.8× | 20→40% | 16.67→40% | 2.419→2.118 s | 5→3 |
| speed 1.25× | 40→40% | 50→40% | 1.937→2.291 s | 2→3 |
| unrelated insert | 60→60% | 66.67→60% | 0.757→1.190 s | 3→4 |

结论不是“全面改善”：variable-speed slope estimation 和 episode recall 改善，但
source-aware aggregate recall 没变，precision、FP、source MAE 与 candidate-GREEN
diagnostic 退化。低分辨率 synthetic source 内存在大量低信息/重复视觉候选；ideal
benchmark 证明候选可用时 alignment 正确，但不能据此抹去端到端退化。

Phase 1 gate 仍为 **FAIL**：source-aware recall 68.57%、precision 66.67%、source
boundary MAE 1.319 秒；pure negative、跨集重复 `AMBIGUOUS` 和 incomplete-index
refusal 通过。没有放宽 temporal thresholds 来追求 Gate 数字。

## 7. Real two-episode segment benchmark

测试使用 2 个匿名 episodes（E001/E002），每集 10 个按 source 时间位置分层且
off-grid 的 6 秒 source intervals。Direct 是无损 FFV1 解码/重编码 clip，用于向完整
matcher 提供独立 query 文件；每个 interval 生成 direct、P1、P2、P3、0.8×、
1.25×，共 120 cases / 120 GT。六个 profile 共享同一批 20 个 content positions，
因此不能把 120 当作 120 个独立内容样本；`episode_n=2` 也不能支持跨集稳定性推断。

### 7.1 Aggregate

| Metric | Result |
|---|---:|
| Source-aware recall@1s | 89.17% |
| Segment precision@1s | 89.17% |
| Episode recall / correctness | 94.17% / 94.17% |
| Source / accused boundary MAE | 0.219 s / 0.092 s |
| FP segments / seconds | 13 / 65.0 |
| Ambiguity rate | 10.0% |
| Segment-count MAE | 0 |
| Speed-ratio MAE | 0.0378 |
| Mean residual RMS | 0.0881 s |
| Mean inlier ratio | 1.0 |
| Candidate-GREEN precision | 93.88% diagnostic only |
| Matcher runtime (sum) | 31.57 s |
| Peak lower / conservative upper memory | 514.8 / 573.6 MB |

Memory 包含 benchmark 期间 FFmpeg child-process peak；matcher runtime 从 clip 已生成后
开始累计，不含 transcode 生成时间。

### 7.2 By profile

| Profile | Recall / precision | Episode recall | Source MAE | Accused MAE | FP seg / sec | Ambiguous | Speed MAE | Runtime |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 90 / 90% | 95% | 0.211 s | 0.075 s | 2 / 9.5 | 10% | n/a | 13.11 s |
| P1 H.264 720p | 90 / 90% | 95% | 0.211 s | 0.075 s | 2 / 9.5 | 10% | n/a | 3.14 s |
| P2 H.264 480p | 90 / 90% | 95% | 0.211 s | 0.075 s | 2 / 9.5 | 10% | n/a | 2.53 s |
| P3 HEVC 720p | 90 / 90% | 95% | 0.211 s | 0.075 s | 2 / 9.5 | 10% | n/a | 3.93 s |
| speed 0.8× | 85 / 85% | 95% | 0.237 s | 0.150 s | 3 / 17.0 | 10% | 0.0293 | 4.83 s |
| speed 1.25× | 90 / 90% | 90% | 0.231 s | 0.103 s | 2 / 10.0 | 10% | 0.0469 | 4.03 s |

Direct/P1/P2/P3 在相同 content positions 上结果完全相同；在本次样本中，编码/
分辨率 proxy 不是失败差异的主要来源。这些 profile 只是 platform-like proxies，
不能声称代表任何具体平台，也不能外推到 crop/mirror/subtitle/camera。

### 7.3 By episode 与 source position

| Episode alias | Cases | Recall / precision | Episode correctness | Source MAE | FP | Alignment-limit misses |
|---|---:|---:|---:|---:|---:|---:|
| E001 | 60 | 81.67 / 81.67% | 91.67% | 0.278 s | 11 | 11 |
| E002 | 60 | 96.67 / 96.67% | 96.67% | 0.163 s | 2 | 2 |

13 个 source-aware misses 的归一化位置分布：

- E001 position 0.3235：0.8× 一例，同集但边界/slope 未达 1 秒判据；
- E001 position 0.7780：direct/P1/P2/P3/0.8× 五例，同集定位但 source boundary
  超过 1 秒容差；
- E001 position 0.9895：direct/P1/P2/P3/1.25× 五例，跨集主要竞争者，系统返回
  `AMBIGUOUS`；
- E002 position 0.0150：0.8×/1.25× 两例，跨集主要竞争者，系统返回
  `AMBIGUOUS`。

其中 7 个 miss 是 `AMBIGUOUS` 安全拒判，6 个是 `MATCH_FOUND` 但边界/slope 未达
source-aware criterion。逐 interval 聚合与逐 case 诊断保存在 ignored local output，
不提交真实文件名、路径、hash 或可识别逐样本信息。

## 8. Retrieval-limit 与 alignment-limit 归因

对每个 source-aware miss，harness 单独检查 top-k 中是否存在正确 episode 且在
expected timestamp ±1 source-grid 的候选 frame，并要求数量达到 minimum-segment
frame count。结果：

```text
RETRIEVAL_LIMIT = 0
ALIGNMENT_LIMIT = 13
```

这是当前 harness 的 coarse diagnostic attribution，不是因果证明：它证明候选集合中
存在最低数量的可用时间证据，但不证明这些证据连续、唯一或足以安全压过重复内容。
跨集重复区间返回 AMBIGUOUS 是预期 safety 行为；其余 boundary/slope miss 是
Temporal v2 的真实已知限制。

## 9. 性能与复杂度

Sparse vote 构建为 `O(CS)`，其中 `C` 是过滤后 candidate 数，`S=31` 为固定 slope
bin 数；空间只分配实际触达 cells。Peak support 是有界次数的 episode-local linear
scan。Robust pair hypotheses 使用最多 `R=32` 点，单次 refit 上界
`O(R² + C)`，不对全部 candidates 做无界 `O(C²)` 枚举。每 episode peak 与 segment
上限均为 128。

相对 constant-offset before，synthetic analysis runtime 从 4.541 秒增至 6.392 秒，
约 +40.8%；RTF 从 0.0130 增至 0.0183。Synthetic peak memory 基本不变。Real
120-case matcher 累计 31.57 秒；这些数据是本机工程 characterization，不应外推到
长剧全集或其他硬件。

## 10. 已知问题与待 Architecture Owner 裁定

1. Expanded synthetic 的 precision、FP、source MAE 和 candidate-GREEN precision
   退化；Phase 1 gate 仍 FAIL。
2. Real 20 个独立 content positions 中存在强 episode/position heterogeneity；样本只有
   2 episodes，不能估计跨集稳定性。
3. `AMBIGUOUS` primary 在 evaluator 中仍作为展示 prediction 计入 FP；报告已同时给出
   ambiguity rate 和失败细分，未把安全拒判伪装成确定结论。
4. 当前 retrieval/alignment attribution 只测候选数量，不测候选连续性与竞争证据
   结构，属于 coarse diagnostic。
5. `inlier_ratio=1.0` 只是在最终 peak support/proposal 内的比例，不代表相对原始
   top-k 全候选的纯度，不能单独解释为高置信。
6. Nominal grid boundary 尚未映射到 authoritative media PTS；不能法律交付。
7. Candidate GREEN 在 synthetic after 仅 21.05% precision，在 real 为 93.88%；两者
   都未达到未来自动放行必须为 100% 的要求，自动放行继续禁用。
8. 请 Architecture Owner 判断：当前实现是进入 targeted alignment hardening、调整
   proposal/segment selection，还是保持实现并优先设计 media-PTS boundary refinement。
   本任务不自行选择后续路线。

## 11. Reproducibility 与交付清单

命令：

```bash
python -m ipvm temporal-benchmark
python -m ipvm temporal-benchmark --real-media --samples-per-episode 10 \
  --query-duration 6 --include-speed-profiles
python -m ipvm benchmark --demo --all-diagnostic
python -m ipvm benchmark --demo
pytest
```

交付包括 production aligner、configuration、model/DB/audit fields、schema v4
migration、CLI harness、ideal/real benchmark harness、regression tests、ADR-0002、
architecture revision、README 与本报告。Real media、文件名、hash、本机绝对路径、
逐样本真实结果均未加入 Git。

完成后停止。等待 Claude Code Independent Review 与 Architecture Owner Gate。
