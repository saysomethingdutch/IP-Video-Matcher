# Phase 2A.1 — Calibration & Safety Closure 完成报告

- **基线**：`3f5f4d5 Implement Phase 2A temporal alignment v2`
- **依据**：Architecture Owner Gate Decision（Phase 2A = CONDITIONAL PASS）与
  `docs/review/PHASE2A-TEMPORAL-ALIGNMENT-V2-INDEPENDENT-REVIEW.md`
- **范围**：narrowly scoped remediation。未重写 sparse voting、robust fitting、
  hypothesis extraction 或 piecewise 架构。
- **本报告不做 Gate 判定。** 后续由 Codex 独立 review，再由 Architecture Owner 做 Final Gate。

---

## 1. 逐项实施

### 1. Merge safety（Critical）

`_merge_adjacent` 在原有 episode / accused gap / source 连续性之外，新增两道判据：

- **时间线一致性**：`_lines_stay_consistent()` 恢复两段各自的 `(slope, intercept)`，在
  **union span 的两个端点**上计算两条映射的最大偏离，要求不超过
  `merge_line_divergence_grid_steps × source_grid_period`（默认 1 格 = 0.5 秒）。
  速度不连续时偏离随 span 线性增长，因而在**任何重拟合之前**就被拒绝。
- **重拟合后支持度**：合并必须仍能解释 union 证据，`inlier_ratio` 低于
  `merge_minimum_inlier_ratio`（0.90）则回退为两段。

未使用任意未验证的斜率常数；判据由 source-grid 时间不确定性推导。

**实测**：0.8× 紧接 1.25×、source 连续 → `2 segments, slopes=[0.8, 1.25]`，无虚构 ~1.0 段。

### 2. Competing hypothesis ranking（Critical）

`_fit_quality` 不再以 `match_score × duration` 为首要键。新排序：

```
_explanation_quality = visual_score × min(1, inlier_count / (2 × min_segment_seconds × fps))
排序键 = (quality, inlier_count, inlier_ratio, -residual_rms, duration)
```

- **support 有界**（在 2 倍最短段长处饱和），因此长而弱的线无法靠跨度压过质量；
- **support 必须存在**，因此少数高分离群点构成的短片段无法压过真正解释时间线的长线；
- `coverage` 与 `inlier_ratio` **刻意不进入首要质量**：前者会惩罚
  `maximum_frame_gap_seconds` 本就要桥接的检索空洞，后者相对 run 计算、短纯片段可轻易取满。
  两者保留为 GREEN 诊断量与次级 tiebreak。

被压制但**不兼容**（跨集，或同集 source 起点差 > 1 秒）且质量差距小于
`minimum_uniqueness_margin` 的对手，不再被静默丢弃，而是进入 `alternatives` 并触发 ambiguity。

**实测**：30 秒 score 0.90 错误线 vs 10 秒 score 0.99 正确线 → 正确段被输出，
两段被标 `conflict` → `AMBIGUOUS` / `REVIEW_REQUIRED`。

### 3. Frame-gap 标定

`maximum_frame_gap_seconds: 1.1 → 1.5`（provisional，已在 `config/default.yaml`
与 `docs/ARCHITECTURE-REVISION-0.1.1.md` §9.4 注明须在真实 benchmark 超过 2 集后重标）。

新增回归：`##..########` 式检索空洞保持单段且起点为 0.0；真实无关内容 gap 不被桥接。

### 4. Out-of-range speed

`_robust_fit` 现保留**未裁剪**的最小二乘斜率。当它落在
`[minimum_speed_ratio − slope_bin_size, maximum_speed_ratio + slope_bin_size]` 之外：

```
speed_ratio             = null
speed_out_of_range      = true
review_tier             = REVIEW_REQUIRED
unconstrained_speed_ratio = <诊断值，保留在 segment / DB / audit>
```

容差取 `slope_bin_size`（0.025）：source-grid 量化会让端点速度轻微越界，小于一个 slope bin
的越界不可分辨，不得据此作废一个合法测量。未扩大配置速度范围。

**已知分辨率限制**：0.5 秒 source 网格下，真实 0.70× 的片段其子 run 可拟合出 0.73，
落在容差内，因而不保证被标记。0.65 及以下、1.65 及以上可靠标记。已写入测试注释。

### 5. Real benchmark outcome taxonomy

新增机器推导、互斥、求和等于 case 数的四分类：

```
CORRECT_MATCH / SAFE_AMBIGUITY_CONTAINS_GT / UNSAFE_WRONG_MATCH / NO_MATCH
```

`UNSAFE_WRONG_MATCH` 包含任何未满足 ground-truth episode/timeline 要求的 `MATCH_FOUND`
——**集数正确但源时间轴实质错误仍计为 unsafe wrong match**。

case 记录现持久化 `alternatives`、`ambiguity_contains_ground_truth`、
`alignment_usable_longest_run_frames`、`truncated_episodes`，因此可以直接判定一个
`AMBIGUOUS` 是否包含 ground truth。`_outcome_summary` 内含
`assert sum(counts) == case_count`。所有计数由代码产出，无人工转写。

### 6. Ambiguous cases

未回退为清空 segments。下游语义写入 `docs/ARCHITECTURE-REVISION-0.1.1.md` §9.1：
先查 status，非空 segments 绝不意味着自动接受，冲突/歧义段保持 `REVIEW_REQUIRED`，
未受影响的可靠段可保留。

### 7. Retrieval-vs-alignment attribution

`_retrieval_attribution` 不再只数总支持帧数，改为在配置 gap 容差下计算
**最长连续 ground-truth 支持 run**（`_longest_supported_run`），与 aligner 实际需要的条件一致。
`alignment_usable_longest_run_frames` 写入每个 case 记录。

### 8. GREEN diagnostic

移除同义反复的 `residual_max <= 0.55`（内点由 residual ≤ 0.5 定义，该条件恒真）。
现用可变诊断量：

```
residual_rms  ≤ maximum_green_residual_rms_seconds (0.25)
inlier_ratio  ≥ minimum_green_inlier_ratio (0.90)
inlier_count  ≥ minimum_green_inlier_count (6)
且 not speed_out_of_range, not source_reuse, coverage/temporal 条件不变
```

生产 `review_tier` 仍恒为 `REVIEW_REQUIRED`；GREEN 仅为 `candidate_tier` 诊断。
未为观感调参 synthetic candidate-GREEN。

### 9. Ideal benchmark realism

`_line()` 现将 source 时间戳 `_snap_to_source_grid()` 到配置推导的 source 网格——
检索永远只能返回索引帧时间戳。保持 8 档速度覆盖，新增：

- negative / zero offset
- pure-noise negative
- competing coherent lines
- short **source** span → `speed_ratio = null`
- speed-discontinuity merge-negative
- out-of-range speed（behavioural，无 recall ground truth）

报告拆分 **gating**（12 例）与 **diagnostic**（5 例）聚合，并输出
`behavioural_assertions`，使对抗性用例既不被隐藏、也不扭曲 pass/fail 聚合。
`source_grid_realistic: true` 写入报告。不再据此声称 sub-grid 边界精度。

### 10. Extreme jitter safety

未针对 40–100% jitter 调参。新增守卫：emit 的 segment 需满足与 hypothesis peak
相同的绝对支持下限（`minimum_segment_seconds × coarse_fps` 帧），低支持尾段不再产出。
`inlier_count` / `inlier_ratio` / `residual_rms` 全部上报；extreme-jitter 拟合的
`candidate_tier != GREEN` 已进回归。

### 11. Segment cap

`_extract_episode_segments` 返回 `(segments, truncated)`；命中
`maximum_alignment_segments_per_episode` 时写 `logger.warning`，并在
`AlignmentOutcome.truncated_episodes` 与 `reason` 中显式上报。未提高上限。

### 12. Phase 1 synthetic gate

未改写旧结果。`docs/ARCHITECTURE-REVISION-0.1.1.md` §9.2 记录：

```
Phase 1 end-to-end synthetic gate = FAIL / BACKEND-DEPENDENT / DEFERRED
```

不再阻断 Temporal Alignment 关闭；必须在 geometric retrieval / backend 工作后重跑。

### 13. Timestamp policy

未实现 authoritative media PTS 映射，未硬编码 +0.23。`timestamp_basis = nominal_grid_time`
保持有效。ADR-0002 与 revision §9.3 明确：PTS 映射**不是 Phase 2A 阻断条件**，
在 **Phase 3 Evidence Analysis 边界**处理。

---

## 2. Before / After

### pytest

```
3f5f4d5 : 65 passed
2A.1    : 78 passed
```

### Ideal（网格真实化后）

```
                        3f5f4d5(sub-grid)        2A.1(grid-realistic)
gating recall/precision  1.0 / 1.0 (14 例)        1.0 / 1.0 (12 gating 例)
speed-ratio MAE          0.00278                  0.00693
source boundary MAE      0.01314                  0.02880
behavioural assertions   —                        5/5 true
```

speed/boundary 数字变差是**口径修正**：3f5f4d5 的候选为 sub-grid 实值，物理上不可能由
2 FPS 索引产生。0.00693 / 0.0288 才是网格可达值。

### Real EP01/EP02 temporal benchmark（120 cases / 20 内容位置 × 6 profile）

```
                                   3f5f4d5      2A.1
source-aware recall@1s             0.89167      0.90833
segment precision@1s               0.89167      0.90833
episode correctness                0.94167      0.94167
source boundary MAE                0.21887      0.19934
accused boundary MAE               0.09208      0.06917
false positive segments            13           11
ambiguity rate                     0.10000      0.12500
speed-ratio MAE                    0.03783      0.03689
candidate-GREEN precision (diag)   0.93878      0.95918
runtime                            31.28 s      31.27 s
failure attribution                RETRIEVAL 0 / ALIGNMENT 13   RETRIEVAL 0 / ALIGNMENT 11
```

**Outcome taxonomy（机器产出，counts 求和 = 120）**

```
CORRECT_MATCH               109
SAFE_AMBIGUITY_CONTAINS_GT    7
UNSAFE_WRONG_MATCH            4
NO_MATCH                      0

exact automatic success rate  0.90833
safe ambiguity rate           0.05833
unsafe wrong rate             0.03333
no-match rate                 0.00000
safe coverage                 0.96667
```

**与旧报告 107/108 correct、7 safe ambiguity、5/6 boundary failure 的对账**：
3f5f4d5 的 120 case 中 13 个 source-aware miss = 7 个 `AMBIGUOUS` + 6 个边界截断，
其余 107 个为 correct。旧报告中出现的 108 是把 `MATCH_FOUND` 计数（108）与
correct 计数（107）混用所致；旧文本的 "5/6 boundary failures" 同样在
"E001-I008 的 5 例" 与 "边界类共 6 例（含 E001-I004-speed_0.8x）" 之间摇摆。
本阶段起该类计数一律由 `outcome_taxonomy` 产出，不再人工转写。

### Expanded synthetic A–M（backend-conditioned diagnostic，不作为 readiness 条件）

```
                            3f5f4d5     2A.1
GT / predicted              80 / 69     80 / 69
TP / FP / FN                36/33/44    36/33/44
episode recall              0.7875      0.7875
source-aware recall@1s      0.4500      0.4500
segment precision@1s        0.6102→0.5217(3f5f4d5实测)  0.5217
source boundary MAE         1.705       1.695
accused boundary MAE        0.095       0.103
speed-ratio MAE             0.0914      0.0665
```

合成 precision **未恢复到 61.02%**，按裁定作为 backend-conditioned diagnostic 报告。
该语料的视觉检索质量已被多轮独立审查证明不足以支撑 precision 结论。

---

## 3. Hard safety checks

| 检查 | 结果 |
|---|---|
| SAFE_AMBIGUITY 与其它类别分离 | ✅ 独立类别，7 例全部 `ambiguity_contains_ground_truth = true` |
| 无静默 clamped speed | ✅ `no_clamped_speed_reported = true`；越界时 `speed_ratio = null` + `speed_out_of_range` |
| 对抗性 merge 无虚构段 | ✅ 0.8→1.25 得到 2 段、斜率 [0.8, 1.25] |
| 对抗性 long-weak 不压制 short-strong | ✅ 正确段输出，冲突标记，`REVIEW_REQUIRED` |
| 纯噪声 FP = 0 | ✅ 单元测试 + ideal `pure_noise_produces_no_segments = true` |
| unsafe wrong 显式计数 | ✅ `unsafe_wrong_rate = 0.03333`，4 例 case id 全部列出 |

---

## 4. 残留项（未在本阶段范围内，交 Codex review 与 Owner 裁定）

1. **4 个 UNSAFE_WRONG_MATCH 全部是同一内容位置 E001-I008 的 direct/p1/p2/p3**：

```
GT   EP01 acc[0.000,6.000] src[1867.603,1873.603] speed=1.0
PRED EP01 acc[2.50,6.00]   src[1870.00,1873.50]  a=1.0 inl=7/7 candidate_tier=GREEN
usable_hits=9/12  longest_supported_run=7 frames
```

段起点被截断 2.5 秒。连续性 attribution 判为 `ALIGNMENT_LIMIT`（最长支持 run 7 ≥ 6 帧门槛），
但预测起点受实际支持起点限制。该位置的 speed_0.8x / speed_1.25x profile 现已通过。

2. **截断段仍可达 candidate-GREEN**：GREEN 判据衡量的是"已找到部分"的内部一致性，
   不衡量相对未知真值的完整性。这是既有 C-1 血脉的问题，重设计不在本阶段范围。
   生产 `review_tier` 仍为 `REVIEW_REQUIRED`，无自动放行风险。

3. **out-of-range 在 0.70× 附近不可靠**（0.5 秒网格分辨率限制，见 §1.4）。

4. **ambiguity rate 由 0.100 升至 0.125**：来自被压制且不兼容 rival 的保留，属有意的
   安全性权衡（safe coverage 由 0.95 升至 0.9667）。

---

## 5. 未触碰

DINOv2 / SigLIP / FAISS / source FPS / crop / mirror / subtitle / PiP /
camera-screen normalization / audio / ASR / Gemma / fair-use / Excel / GREEN auto-release
——全部未引入。`coarse_fps` 仍为 2.0，`features.py`、`indexer.py`、`media.py`、
`retrieval_benchmark.py` 本阶段 0 改动。

---

等待 Codex 独立 review 与 Architecture Owner Final Phase 2A Gate。
