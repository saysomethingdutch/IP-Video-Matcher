# Phase 2A Temporal Alignment v2 独立技术审查（第五次）

- **审查对象**：`3f5f4d5 Implement Phase 2A temporal alignment v2`
- **上游安全基线**：`9ae51db Complete Phase 2A-0 platform characterization and safety closure`
- **审查人身份**：独立算法与技术 reviewer，非本阶段开发者
- **审查日期**：2026-09-03
- **方法**：全量阅读 diff（2966 insertions）、ADR-0002/0003/0005、schema v4；**未修改任何生产代码**；完整重跑 ideal + real temporal benchmark 与 expanded synthetic A–M；运行 10 组 reviewer 自建 adversarial probe（不复用 Codex 的测试构造）
- **前置**：Phase 1 / 1.5 / 2A-0 / 2A-0.1-0.2 四份独立审查

---

# 1. Executive Verdict

## **CONDITIONAL PASS**

**需要的是小范围 Phase 2A.1 calibration / safety closure，不是核心算法重做。**

这个区分是本次审查最重要的结论。我用自己构造的候选集反复验证：**Temporal Alignment v2 的核心机制是正确的、健壮的、可复现的**。所有缺陷都集中在少数几个明确定位的判据上，每一个都能独立修复而不触碰投票、拟合或抽取逻辑。

### 核心能力已经证实（reviewer 自建输入，非 Codex 测试）

| 能力 | 验证 | 结果 |
|---|---|---|
| 变速覆盖 | 0.75/0.80/0.90/1.00/1.10/1.25/1.40/1.50 全部单独扫描 | ✅ **8/8 恢复，误差 ≤ 0.0008** |
| 旧结构缺陷 | 第一次审查测得的 0.81–1.19 天花板 | ✅ **已消失**，现覆盖 0.75–1.50 全域 |
| piecewise | 我自建 5 段 / 3 集 / 2 gap / 4 种速度 / source reuse 的时间轴 | ✅ **5/5 召回，0 FP，reuse 精确标记在 A 与 E** |
| source reuse | 同一 source clip 重复三次 | ✅ 三段全部提取并全部标记 `source_reuse` |
| 噪声拒绝 | 纯随机候选，1–5 集，30–300 秒，top_k 8/16/32 | ✅ **每一种配置都是 0 segment** |
| 跨集歧义 | 等强竞争线 / 共享片头 | ✅ 正确返回 `AMBIGUOUS` 并记录正确的 alternative |
| 抽取顺序无关 | 30 次随机置换同一候选集 | ✅ **输出完全一致** |
| 复杂度 | 1/5/10/20/30 分钟被诉视频 | ✅ **线性**（1.13 s → 18.89 s），无 O(N²) |
| 多集扩展 | 1 → 40 集 | ✅ 反而更快（5.67 s vs 7.72 s），无爆炸 |

### 阻断 PASS 的两个 Critical

**C-1　`_merge_adjacent` 会把斜率不兼容的相邻段合并成一个虚构段。** 我构造 0.8× 紧接 1.25×、source 连续的情形：抽取阶段**本来是对的**（两段，斜率 0.8491 / 1.2477，inlier_ratio 均为 1.00），merge 把它们压成 **一段 a=0.9678、inlier_ratio 0.52、accused 末端从 24.0 截断到 19.0**。合并判据只检查 episode、accused gap 和 `|source_start − prev_source_end| ≤ 1.5`，**完全不检查斜率兼容性**。

**C-2　长而弱的错误假设会静默压掉短而强的正确段。** 30 秒 score 0.90 的错误线 + 10 秒 score 0.99 的正确线 → 只输出错误段（tier GREEN），**正确段连 alternative 都没有**。根因是 `_fit_quality` 以 `match_score × duration` 为首要排序键，时长压过质量。

---

# 2. Scope Audit

| 检查项 | 结论 |
|---|---|
| visual backend 仍为 DCT | ✅ `features.py` **0 改动**，`name = "dct-low-frequency"` |
| source/query FPS 仍 2 FPS | ✅ `coarse_fps: 2.0`，`indexer.py` / `media.py` / `retrieval_benchmark.py` **均 0 改动** |
| 无 DINOv2 / SigLIP / FAISS | ✅ 全库 grep 仅命中 `doctor.py` 的可用性探测（既有）与 gitignore 的 egg-info |
| 未偷偷放宽 retrieval | ✅ `CandidateRetriever` 与 `top_k=8`、`minimum_visual_similarity=0.82` 未变 |
| GREEN auto-release 未恢复 | ✅ `review_tier` 在全部 4 个写入点硬置为 `"REVIEW_REQUIRED"`；`"GREEN"` 只作为 `candidate_tier` 出现 |
| `.DS_Store` 未纳入 | ✅ 不在 commit 内 |
| real media / path / hash 未进 Git | ✅ 全历史扫描：真实 SHA-256 与绝对路径 0 命中；`real_test/*.mp4` 从未作为 blob 被跟踪 |

**config 变更（合理，属本阶段）**：移除 `offset_bin_seconds`，新增 `temporal_algorithm_version`、`minimum_speed_ratio 0.75`、`maximum_speed_ratio 1.50`、`slope_bin_size 0.025`、`candidate_timestamp_uncertainty_grid_steps 1`、两个上限。

**未发现 scope creep。**

---

# 3. Algorithm Reconstruction

```
候选（episode 分组）
  → _sparse_peaks : 每帧 × 每候选 × 31 个 slope bin，offset 软投票到相邻两 bin
                    （同帧对同 cell 只取最大贡献，避免 top-k 重复计票）
                    support ≥ minimum_frames 才入选；3×3 邻域峰抑制
  → _support_for_peak : 残差 ≤ 0.5s 的候选，每帧留最优一个
  → _continuous_runs  : 按 maximum_frame_gap_seconds 切成连续 run
  → _robust_fit       : peak 假设 + ≤496 个有界采样对假设 → 内点最多者 → 3 轮 lstsq 精修
  → 取 _fit_quality 最优者 emit，按「帧已认领 AND 落在该线上」移除证据
  → 迭代，最多 maximum_alignment_segments_per_episode 次
跨集汇总 → 全局 claimed_frames 去重 → _merge_adjacent → 歧义/冲突评估
```

**投票不是成对 Hough**，而是直接累加器：复杂度 `O(n_candidates × slope_bins × 2)`。这是一个比我在第二次审查中建议的成对参数化**更简洁且更省**的实现——因为 slope 轴被离散化后，每个候选可以直接投影，不需要枚举候选对。**这个设计选择是对的。**

---

# 4. Ideal Candidate Audit

## 覆盖度核实（14/14 PASS，recall 100%，precision 100%，speed MAE 0.00278）

完整重跑确认：`ideal_passed: True`，14 GT / 14 predicted，recall 1.0，precision 1.0，speed MAE 0.0027818，source boundary MAE 0.0131。

| 要求覆盖项 | 是否覆盖 |
|---|---|
| 8 档速度 0.75–1.50 | ✅ `IDEAL_SPEEDS` 与要求逐项一致 |
| candidate ±1 grid jitter | ✅ `jitter = (-0.5, 0.0, 0.5, 0.0)` |
| wrong-episode candidates | ✅ EP99 干扰 |
| false high-score candidates | ✅ score 0.999/1.0 的干扰线（高于真线 0.96） |
| unrelated outliers | ✅ 伪随机干扰 |
| gap split / multiple hypotheses | ✅ 5 段 piecewise 各带干扰 |
| repeated source interval | ✅ EP01@10.0 出现两次 |
| **positive / negative offset** | ❌ **全部为正**（我另行验证：负偏移与 2300 s 大偏移均正常） |
| **short spans（<3 s）** | ❌ 无 case 覆盖 `speed_ratio=null` 路径 |
| **pure noise → MATCH_NOT_FOUND** | ❌ **完全没有负样本** |
| **两条弱的偶然线（ambiguity）** | ❌ 干扰是伪随机点，不是相干竞争线 |
| **突变速度 + 连续 source** | ❌ 未覆盖（正是 C-1 失效点） |

## M-1（Major）　ideal benchmark 的候选在物理上不可能出现

`_line()` 生成 `source_time = source_start + speed × index × 0.5`：

```
speed 1.25 -> [40.0, 40.625, 41.25, 41.875, 42.5, 43.125]
speed 0.8  -> [40.0, 40.4,   40.8,  41.2,   41.6, 42.0]
speed 1.4  -> [40.0, 40.7,   41.4,  42.1,   42.8, 43.5]
      全部在 2 FPS source 网格上（0.5 的整数倍）？  False
```

**真实候选的 `source_time` 永远是索引帧时间戳，即 0.5 的整数倍。** `_line()` 喂进去的是子网格实值，检索层永远产不出这种候选。

后果：ideal benchmark 给了拟合器**比物理可达更精确的输入**，因此 `speed MAE 0.00278` 与 `source boundary MAE 0.013` 是不可达的乐观值。我用**网格对齐**候选做同样的速度扫描，得到速度误差 ≤ 0.0008（更好，因为我的段更长）但 **source_start 误差可达 0.25 秒**（网格量化下界）——这才是可达精度。

这不影响「算法在理想候选下正确」这个结论，但**不能用这两个数字外推真实边界精度**。

## 是否存在 self-confirming test

`_line()` / `_truth()` 同时被 ideal benchmark 与 `tests/test_alignment.py` 使用，且 `_truth` 直接由 `_line` 的同一组参数构造。这是构造性一致，不是逻辑循环（ground truth 不依赖被测算法的输出）。**但我没有只依赖它们**——本报告的全部结论都基于我自建的候选集。

---

# 5. Slope / Offset Audit

模型 `source_time = a × accused_time + b`，`speed_ratio = a = d(source)/d(accused)`。

| 检查项 | 结论 |
|---|---|
| `a > 1` = accused 加速 | ✅ 实测 a=1.50：30 s accused 覆盖 **44.97 s** source；a=0.75：覆盖 **22.50 s** |
| bounds 覆盖 [0.75, 1.50] | ✅ 31 个 bin，步长 0.025，端点即 0.75 与 1.50 |
| 端点可恢复 | ✅ 0.75 → 0.7500（误差 0）；1.50 → 1.4992（误差 0.0008） |
| **无残留 0.81–1.19 限制** | ✅ 细扫 0.70–1.55，各层均无旧限制 |
| offset 量纲 | ✅ `offset_bin_seconds = 1/coarse_fps`，从配置推导，非硬编码 |
| merge 后重新 refit | ✅ `_merge_adjacent` 合并 inlier 后调 `_proposal_to_segment(proposal)`（无初始斜率）→ 全量重拟合，**不是平均旧斜率** |
| <3 s span → `speed_ratio=null` | ✅ `min(accused_span, source_span) >= 3.0` 才输出斜率 |

## M-7（Major）　超出 [0.75, 1.50] 的斜率被 clamp 后仍然 emit

细扫发现 **0.70–0.74 与 1.51–1.55 共 10 个真实斜率仍然产出 segment**，斜率被 `np.clip` 钉在 0.75 / 1.50。

对法律交付物而言这是**静默错误的 speed_ratio**：一段真实 1.55× 的引用会被报成 1.50×，而没有任何"超出可检测范围"的信号。正确行为应是拒绝或标记 `speed_out_of_range`。

---

# 6. Soft Voting Audit

`_sparse_peaks` 对 offset 做线性双 bin 软投票：`fraction = coord − floor(coord)`，权重 `(1−fraction, fraction)`。slope 轴**不做**软投票（直接枚举全部 31 个 bin，每个候选对每个 slope 都投票），因此 slope 维度不存在 bin 边界问题。

## bin edge 扫描（源起点在子网格上滑动）

```
 src_start   预测 src_start    误差
   100.000        100.000    0.000
   100.249        100.000    0.249
   100.250        100.232    0.018
   100.251        100.500    0.249
   100.499        100.500    0.001
   100.500        100.500    0.000
   100.501        100.500    0.001
```

**0.499 / 0.501 落入同一有效峰，误差均为 0.001，无不连续跳变。** 软投票达到了设计目的。最大误差 0.25 秒是 source 网格量化的理论下界，非算法缺陷。

## 相邻真实段是否被糊成一个峰

- 两个 source 相距 200 s 的 EP01 段 → 正确分成两段 ✓
- 两个 source 仅相距 3 s 的 EP01 段 → 正确分成两段，且均标记 `source_reuse` ✓

**未发现真实段被软投票糊合的情形。**

## M-6（Major）　±1 grid jitter 下的斜率与偏移退化

Phase 2A-0 已证明 ±1 grid 不确定性是主导工况。我按比例注入 jitter：

```
 jitter 比例  segs      a   src_start 误差   inl/cand
        0.0     1  1.2504         0.006      60/60
        0.2     1  1.2425         0.142      52/53
        0.4     2  1.5000         0.250        4/4     <- 碎片化 + 斜率钉在上限
        0.6     1  1.2289         1.708      42/48
        0.8     1  1.2339         4.801      40/42
        1.0     1  1.2352         1.722      36/38
```

真值 a=1.25。**在 40% jitter 时输出两段、斜率报成 1.5000（clamp 上限）、仅 4 个 inlier**；60–100% jitter 时 source_start 误差达 **1.7–4.8 秒**。这与 C-1、M-7 是同源的：拟合器在证据变脏时倾向于给出一个自洽但错误的极端解，而不是降级。

---

# 7. Piecewise / Merge Audit

## reviewer 自建时间轴（Owner 指定结构）

```
A: EP01 acc[0,12]   src[100.00,112.00] a=1.00
   gap
B: EP02 acc[16,28]  src[500.00,515.00] a=1.25
C: EP01 acc[28,40]  src[800.00,809.60] a=0.80
   gap
D: EP03 acc[46,58]  src[200.00,216.80] a=1.40
E: EP01 acc[58,70]  src[100.00,112.00] a=1.00   (复用 A 的 source 区间)
```

**输出：5 段全对，0 FP，episode 全对，source 区间全在 1 秒内，斜率误差 ≤ 0.0078，`source_reuse` 精确标记在 S001 与 S005。** 状态 `ambiguous=False, contradiction=False`。

追加压力测试：
- **同一 source clip 重复三次**（accused 0–8 / 20–28 / 40–48）→ 三段全部提取，全部标记 reuse ✓
- **100 个真实短段** → 100 段全部提取，13.23 秒 ✓

**C（是否只对 completion benchmark 的构造方式有效）的答案是 NO——这是真实能力。**

## Merge 四例

| Case | 构造 | 结果 |
|---|---|---|
| 1 | 同集、offset 相同，中间 20 秒真实 gap | ✅ **正确不合并**（gap > `maximum_frame_gap_seconds`） |
| 2 | 同集、source 连续，速度 0.8 → 1.25 突变 | ❌ **错误合并**（见 C-1） |
| 3 | 两个独立 clip，offset 仅差 0.2 秒 | ⚠️ 合并了。但 0.2 秒**低于 0.5 秒网格分辨率**，两者在测量意义上不可区分——**不构成已证实缺陷** |
| 4 | 一段被 2 秒证据空洞切成两块 | ⚠️ **未合并**（gap 2.5 s > 1.1 s），输出两段，斜率均正确 |

## C-1（Critical）　merge 缺少斜率兼容性检查

```
PRE-MERGE   acc[ 1.50,15.00] a=0.8491 ir=1.00 rms=0.235
            acc[15.00,24.00] a=1.2477 ir=1.00 rms=0.145
POST-MERGE  acc[ 1.50,19.00] a=0.9678 ir=0.52 rms=0.332   <- 虚构段
```

抽取阶段已经**正确地**分出了 0.85 与 1.25 两段；merge 把它们压成一个 0.9678 的段，丢弃一半证据（inlier_ratio 1.00 → 0.52），且 accused 末端从 24.0 截到 19.0。

这正是 Owner 在 Case 2 中要求"不得被平均成一个虚假的 1.0 左右 segment"的情形。**注意：不是平均斜率造成的**（重拟合是正确实现的），而是**合并前没有判断两段是否属于同一条线**。修复只需在 `compatible` 中加一条斜率差判据。

## Case 4 的另一面：过分割

2 秒证据空洞就把一段引用切成两段。`maximum_frame_gap_seconds = 1.1` 只能跨越 1 个缺帧。这直接产生 evaluator 层的 FP，并且是真实 benchmark 中 5/13 misses 的根因（见 M-2）。

**merge 判据同时「对斜率过松」与「对 gap 过紧」——这是一个连贯的、定位明确的缺陷。**

---

# 8. Noise Rejection Audit（Hypothesis 4 判定）

| 探针 | 配置 | 结果 |
|---|---|---|
| 纯随机候选 | 1 集 / 30 s / top_k 8 | **0 segment** |
| | 1 集 / 60 s | **0** |
| | 2 集 / 60 s | **0** |
| | 3 集 / 120 s | **0** |
| | 5 集 / 120 s | **0** |
| | 1 集 / 300 s | **0** |
| | 5 集 / 300 s | **0** |
| 密集错集数噪声 | 3 集 / 60 s / top_k **8 / 16 / 32**（最多 3840 候选） | **全部 0** |
| 噪声中的弱真线 | 噪声 k=2 / 4 / 8 | **真线全部召回，0 FP** |
| 两条等强偶然线 | EP01 0.900 vs EP02 0.899 | **`AMBIGUOUS`**，alternative 正确记录 |
| 重复 source（共享片头） | EP01/EP02 同一 source | **`AMBIGUOUS`** |

**Hypothesis 4（aligner 在没有有效候选时强行拟合噪声）在纯噪声下被证伪。**

阈值敏感性下同样稳健：把 `maximum_frame_gap_seconds` 放宽到 5.0、`minimum_visual_similarity` 降到 0.70、`minimum_segment_seconds` 降到 2.0，**纯噪声 FP 始终为 0**。

**但（重要限定）**：在**结构化的错误候选**下（镜像查询使检索稳定地返回另一集的相干帧），aligner 确实会拟合出一条相干但错误的线——见 §12 的 5 个 wrong-episode FP 全部来自 mirror 组。**噪声不会骗过它，结构化的错误会。**

---

# 9. Real Benchmark Reproduction

完整重跑，与报告逐项一致：

```
case_count 120   GT 120   predicted 120
source-aware recall/precision  0.891667      episode correctness 0.941667
source boundary MAE 0.218869   accused boundary MAE 0.092083
FP 13 段 / 65.0 秒             ambiguity rate 0.10
speed MAE 0.037832             runtime 31.28 s
failure_attribution: RETRIEVAL_LIMIT 0, ALIGNMENT_LIMIT 13
```

## M-3（Major）　120 不是 120 个独立样本

`direct` / `p1` / `p2` / `p3` 四档的指标**逐位相同**（recall 0.9，source MAE 0.2114631578947354 到小数点后 16 位一致，FP 2，GREEN 17）。这与 Phase 2A-0.1/0.2 的结论一致：转码扰动低于 DCT 灵敏度。

**per-content-position failure matrix（20 位置 × 6 profile）：**

```
position       direct  p1  p2  p3  0.8x  1.25x   fails
E001-I001           H   H   H   H     H      H    0/6
E001-I002 … I003    H   H   H   H     H      H    0/6
E001-I004           H   H   H   H     x      H    1/6
E001-I005 … I007    H   H   H   H     H      H    0/6
E001-I008           x   x   x   x     x      H    5/6
E001-I009           H   H   H   H     H      H    0/6
E001-I010           A   A   A   A     H      A    5/6
E002-I001           H   H   H   H     A      A    2/6
E002-I002 … I010    H   H   H   H     H      H    0/6

全 6 档失败: 0    全 6 档通过: 16    部分失败: 4
```

**全部 13 个 miss 只来自 20 个内容位置中的 4 个。16/20 位置六档全通过。**

因此"89.17% over 120 cases"的真实统计单位是 **16/20 = 80% 的位置级成功率**，二项 95% CI 约 [56%, 94%]——远宽于 89.17% 给人的印象。profile 维度几乎不提供独立信息。

---

# 10. Failure Taxonomy（13 个 miss 重新分类）

## 类别 A：安全性拒绝（7 个 AMBIGUOUS，来自 2 个位置）

| 位置 | 归一化位置 | 性质 |
|---|---|---|
| E001-I010（5 例） | **0.9895**（EP01 末尾） | 片尾 ED，EP01/EP02 共享 |
| E002-I001（2 例） | **0.0150**（EP02 开头） | 片头 OP，EP01/EP02 共享 |

**两个簇都精确落在片头与片尾——真实的跨集重复内容。**

我直接复跑并检查 alternatives：

```
E001-I010: PRIMARY E002 src[2264.00,2270.00] ms=0.9979
           ALT     E001 src[2375.50,2381.50] ms=0.9967   *** 就是 ground truth（GT 2375.39）***
E002-I001: PRIMARY E002 src[34.51,40.39] ms=0.9248       *** 正确 episode ***
           ALT     E001 src[34.51,40.39] ms=0.9247
```

**这 7 个不是错误答案，是包含正确答案的安全拒绝。** 按 Owner 要求的四分类：

```
correct match emitted            108
safe ambiguity containing GT       7   <- 不应与错误等价计分
unsafe wrong match                 0
no match                           0
```

**M-4（Major）**：temporal benchmark 的 per-case 记录**不持久化 alternatives**，因此单看提交产物无法区分"安全拒绝"与"错误答案"。这是报告层缺口，应补 `ambiguity_contains_ground_truth` 字段。

## 类别 B：边界截断（6 个 MATCH_FOUND，来自 2 个位置）

全部是**同一失效模式：段起点被截断**，拟合线本身正确。

| 位置 | GT | 预测 | 根因 |
|---|---|---|---|
| E001-I008（5 例） | acc[0,6] src[1867.603,…] | acc[**2.50**,6] src[1870.00,…] a=1.0 **rms=0.0** | 候选覆盖 `##..########`，帧 1→4 的 1.5 s 空洞 **> `maximum_frame_gap_seconds` 1.1**，前 2 帧被切成 1.0 s 的 run 而丢弃 |
| E001-I004-0.8x（1 例） | acc[0,7.5] src[776.469,…] | acc[**3.00**,7.5] a=0.7667 | 覆盖 15/15 完整，属真实对齐层不足 |

**M-2（Major）　`maximum_frame_gap_seconds = 1.1` 单独造成 5/13（38%）的真实 miss。** 合成复现该覆盖模式：

```
 max_gap  segs  acc_start  src_start   误差
     1.1     1       2.00    1869.50    2.00
     1.5     1       0.00    1867.50    0.00   <- 完全修复
     2.0     1       0.00    1867.50    0.00
```

而放宽该参数在我的全部纯噪声探针中 **FP 仍为 0**。

## 按失败阶段归类

| 阶段 | 命中 |
|---|---|
| hypothesis generation | 0 |
| voting | 0 |
| **split（run 切分 / gap 阈值）** | **5**（E001-I008 全部） |
| RANSAC / refit | 1（E001-I004-0.8x） |
| merge | 0（真实 benchmark 每例只有单段） |
| **cross-episode competition** | **7**（安全拒绝，非错误） |
| insufficient support | 0 |
| retrieval candidate quality | 0（见 §13） |

---

# 11. Retrieval-vs-Alignment Attribution

## harness 的判据

```python
hits = {帧 | 存在正确 episode 且 |source_time − expected| ≤ 0.5s 的候选}
attribution = RETRIEVAL_LIMIT if len(hits) < minimum_segment_seconds × coarse_fps (=6)
```

**这个判据偏弱**：它统计**任意位置**的命中数，而 aligner 实际需要的是**连续 run**（gap ≤ `maximum_frame_gap_seconds`）达到 `minimum_segment_seconds`。

## reviewer 自定义判据

```
retrieval_sufficient_for_ground_truth :=
    最长的「GT 支持帧连续 run」（容许 ≤ 1.1 s 空洞）≥ 3.0 秒（6 帧）
```

对 13 个 miss 逐一复算（复刻 harness 的完整 query 构造链）：

```
case                        GT帧  harness命中  最长run  harness  reviewer  覆盖模式
E001-I004-speed_0.8x          15          15       15    ALIGN     ALIGN  ###############
E001-I008-direct              12          10        8    ALIGN     ALIGN  ##..########
E001-I008-p1/p2/p3            12          10        8    ALIGN     ALIGN  ##..########
E001-I008-speed_0.8x          15          15       15    ALIGN     ALIGN  ###############
E001-I010-{direct,p1,p2,p3}   12          12       12    ALIGN     ALIGN  ############
E001-I010-speed_1.25x         10          10       10    ALIGN     ALIGN  ##########
E002-I001-speed_0.8x          15          15       15    ALIGN     ALIGN  ###############
E002-I001-speed_1.25x         10          10       10    ALIGN     ALIGN  ##########
```

**`RETRIEVAL_LIMIT = 0` 可信。** 在我更严格的连续性判据下，13 个 miss **仍然全部**归为 ALIGNMENT_LIMIT。候选覆盖率 10–15/12–15，最长 run 8–15 帧，全部远超 6 帧门槛。**不存在 attribution inflation。**

**m-4（Minor）**：判据本身应改为连续 run 口径。当前结论正确是因为覆盖足够好，不是因为判据严谨。

---

# 12. Synthetic Regression Analysis（precision 61.02% → 52.17%）

完整重跑 expanded A–M，复现全部数字：GT 80 / predicted 69 / recall 45.00% / precision 52.17% / FP 33 段 124.5 秒 / source MAE 1.705 s / speed MAE 0.0914。

## 关键算术

| | Before | After | Δ |
|---|---:|---:|---:|
| predicted | 59 | 69 | **+10** |
| **TP** | **36** | **36** | **0** |
| FP | 23 | 33 | **+10** |
| FN | 44 | 44 | 0 |
| precision | 36/59 = 61.02% | 36/69 = 52.17% | −8.85 pt |

**v2 没有丢失任何一个 true positive。它新增了 10 条预测，全部错误。** 这是 precision 下降的完整数学解释。

## 33 个 FP 的分类

| 类别 | 数量 | 占比 |
|---|---:|---:|
| **right episode, wrong source-time（>3 s）** | **16** | 48% |
| **right episode, wrong source-time（1–3 s）** | **10** | 30% |
| wrong episode | 5 | 15% |
| duplicate of another prediction | 2 | 6% |

**26/33（79%）是"集数对、时间戳错"。**

## 逐条判定 Owner 的四个假设

| 假设 | 判定 | 证据 |
|---|---|---|
| **H1**：输出更多 segment，阈值未重校准 | ❌ **证伪** | predicted 69 **< GT 80**；59 个 case 中 predicted==GT，**12 个欠分割，仅 1 个过分割** |
| **H2**：piecewise 对弱峰过于积极 | ⚠️ **部分成立** | 新增的 10 条确实来自更强的假设机制，但它们不是"多余的段"，而是"位置错的段" |
| **H3**：split/merge 制造重复 FP | ❌ **基本证伪** | 33 个 FP 中只有 **2 个** duplicate |
| **H4**：无有效候选时仍拟合假线 | ⚠️ **纯噪声下证伪，结构化错误下成立** | 纯噪声 FP=0（§8）；但 5 个 wrong-episode FP **全部来自 mirror 组**，那里检索返回的是相干的错误候选 |

## 真正的机制

**v2 把「无预测」转化为「正确集数但时间戳错误的预测」。** episode recall 68.75% → 78.75%（+10 pt）与 predicted +10 完全对应；source-aware recall 停在 45.00% 不动。合成语料是低分辨率 `testsrc` 系列，DCT 描述子在其上动态范围仅约 0.026（第二次审查已量化），候选是**结构化但错误**的——v2 更强的假设机制在这些候选里找到了相干的线，v1 的常数 offset 分桶找不到。

**这是检索质量受限的结果，通过对齐层的 precision 表现出来，不是对齐算法本身的缺陷。**

---

# 13. Transformation Group Analysis

| Group | GT | pred | TP | FP | FN | recall | precision | epCorr | srcMAE | spdMAE | 本阶段范围内？ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| off_grid_direct_cut | 5 | 5 | 2 | **3** | 3 | 0.400 | 0.400 | 1.000 | 2.084 | — | **是** |
| off_grid_direct_reencode | 20 | 21 | 15 | **6** | 5 | 0.750 | 0.714 | 1.000 | 1.237 | — | **是** |
| off_grid_multi_episode_concat | 10 | 10 | 7 | **3** | 3 | 0.700 | 0.700 | 1.000 | 1.100 | — | **是** |
| speed_0.8x | 5 | 5 | 2 | **3** | 3 | 0.400 | 0.400 | 1.000 | 2.118 | 0.108 | **是** |
| speed_1.25x | 5 | 5 | 2 | **3** | 3 | 0.400 | 0.400 | 1.000 | 2.291 | 0.049 | **是** |
| unrelated_insert | 10 | 10 | 6 | **4** | 4 | 0.600 | 0.600 | 1.000 | 1.190 | — | **是** |
| center_crop | 5 | 4 | 0 | 4 | 5 | 0.000 | 0.000 | 0.750 | 3.563 | — | 否 |
| subtitle_overlay | 5 | 5 | 2 | 3 | 3 | 0.400 | 0.400 | 1.000 | 3.319 | — | 否 |
| horizontal_mirror | 5 | 2 | 0 | 2 | 5 | 0.000 | 0.000 | **0.000** | — | — | 否 |
| mirror_speed_subtitle | 5 | 2 | 0 | 2 | 5 | 0.000 | 0.000 | **0.000** | — | — | 否 |
| vertical_crop | 5 | 0 | 0 | 0 | 5 | 0.000 | — | — | — | — | 否 |
| **TOTAL** | **80** | **69** | **36** | **33** | **44** | **0.450** | **0.522** | | | | |

## 对 Owner 判据的直接回答

> 「precision collapse 是否主要来自本阶段明确不要求解决的 mirror/crop/subtitle groups？如果是，Phase 2A 可以有条件通过。如果 direct/speed/multi-segment 本身 precision 也显著下降，则是 blocker。」

**FP 分布：范围内 22/33（67%），范围外 11/33（33%）。**

**collapse 不主要来自范围外的组。** 按 Owner 的字面判据，这指向 blocker。

**但两点必须并列陈述，才是完整判断：**

1. **范围内组的 TP 没有下降**。report 的 before/after 表显示 `direct cut` precision 40→40% 持平，`speed 0.8×` **改善** 16.67→40%；退化集中在 `normal reencode`（83.33→71.43%）与 `multi-episode concat`（88.89→70%）——而这两组的 FP 增量同样是"集数对、时间戳错"，**不是新的错误类型**。
2. **合成语料的检索质量已被前四次审查反复证明是病态的**（DCT 在 `testsrc` 上整集帧对余弦挤在 0.026 内）。在该语料上，"更强的对齐 ⇒ 更多结构化错误候选被拟合成线" 是必然结果，而**真实素材上同一算法给出 89.17% recall / 94.17% episode correctness / 0 unsafe wrong match**。

**我的判断**：这不是 blocker，但**也不能按"主要来自范围外组"来轻松放行**。正确的表述是：合成 precision 下降是**检索受限语料下的可解释代价**，其中 79% 是时间戳偏差而非虚构片段，0 个是纯噪声拟合。真实素材上不存在对应的 precision collapse。

---

# 14. Ambiguity / Safety

`uniqueness_margin < 0.05` 的 Phase 1.5.1 语义**未被绕过**：

```python
major_rivals = [r for r in rivals
                if r.source_episode != winner.source_episode
                and winner.match_score - r.match_score < minimum_uniqueness_margin]
is_ambiguous = bool(major_rivals)
```

| 场景 | 结果 |
|---|---|
| 完全重复跨集 | ✅ `AMBIGUOUS` |
| 重编码重复（真实片头/片尾） | ✅ `AMBIGUOUS`（真实 benchmark 的 7 个案例即是） |
| 两集强候选、margin 低 | ✅ `AMBIGUOUS`，alternative 记录正确 episode |
| 一集票数略高但 margin 低 | ✅ `AMBIGUOUS`（E002-I001：0.9248 vs 0.9247） |

**没有为了 temporal coherence 强行压掉真实 ambiguity。**

**语义变更备案**：v2 在 `ambiguous or contradiction` 时**仍返回 `segments`**（Phase 1.5 是返回 `[]`）。这是我在第三次审查 Q4 中建议的 (c) 方案——状态诚实表达"存在无法调和的证据"，同时不丢弃已确认部分。**方向正确**，但请 Owner 确认这是有意采纳而非副作用（见 Q3）。

---

# 15. GREEN Audit

```
synthetic candidate GREEN precision = 21.05%    real = 93.88%（我复现 93.88%）
```

| 检查 | 结论 |
|---|---|
| downstream 仍全部 REVIEW_REQUIRED | ✅ `review_tier` 在 4 个写入点全部硬置 `"REVIEW_REQUIRED"` |
| candidate GREEN 纯 diagnostic | ✅ `"GREEN"` 只出现在 `_candidate_tier` 返回值 |
| schema/audit 未暗中恢复 GREEN | ✅ DB 与 audit 分列 `candidate_tier` / `review_tier` |
| v2 是否让 GREEN safety 更差且隐藏 | ⚠️ synthetic candidate-GREEN precision 66.67% → **21.05%**，**报告已如实披露** |

**明确建议：GREEN 仍不得恢复为 production release。** 合成语料上 21.05% 的 candidate-GREEN precision 本身就是决定性证据。

## M-5（Major）　GREEN 的 residual 条件仍是同义反复

```
candidate_timestamp_tolerance（内点阈值） = 0.5 s
maximum_temporal_residual_seconds（GREEN 判据） = 0.55 s
temporal_residual_seconds = residual_max ≤ 0.5  （由内点定义所限）
=> residual 条件恒真
```

这是我在第二次审查中提出的问题，**未修复**。好消息是 `residual_rms_seconds`、`inlier_count`、`candidate_count`、`inlier_ratio` 现在是真实独立诊断量（我的探针中 rms 从 0.000 到 0.332 有效变化）——**字段已具备，只是 GREEN 判据仍用了那个恒真的**。改用 `residual_rms` 或 `inlier_ratio` 即可。

---

# 16. Residual Semantics

| 量 | 是否独立诊断 |
|---|---|
| `residual_rms_seconds` | ✅ 真实独立（观测范围 0.000–0.332） |
| `residual_max_seconds` | ⚠️ 被内点阈值 0.5 s 硬限，作为门槛无信息（M-5） |
| `inlier_ratio` | ✅ 真实（C-1 的虚构段即由 0.52 暴露） |
| `inlier_count` / `candidate_count` | ✅ 真实 |
| 单位 | ✅ 秒，`timestamp_basis = nominal_grid_time` 显式记录 |
| low-support 段是否获得"漂亮但无意义"的 residual | ⚠️ **会**：M-6 中 4 个 inlier 的段 rms 很低但 a=1.5000 完全错误。**`inlier_count` 是唯一能暴露它的量，而它不在 GREEN 判据里** |

---

# 17. Timestamp Audit

| 检查 | 结论 |
|---|---|
| `timestamp_basis = nominal_grid_time` | ✅ models / matching / database / audit / temporal_benchmark 五处一致 |
| 无 +0.23 硬编码 | ✅ 全库 grep 无命中 |
| GT 与预测同一时间基准 | ✅ 两侧都来自 2 FPS 索引标签，δ≈+0.23 s 抵消 |

## 0.219 s 是内部误差，不是权威时间轴误差

第三次审查已实测：索引与查询两侧都携带约 **+0.23 秒**的内容-标签偏移（ffmpeg `fps` 滤镜取帧约定），匹配时抵消。

因此 **`source boundary MAE 0.219 s` 是 matcher 内部边界误差**。相对**权威 media PTS** 时间轴，还需叠加约 0.23 秒的系统性提前量。**两者必须区分**，ADR-0002 已记录该要求但尚未实现映射。

---

# 18. Schema Migration Audit

## 真实升级路径（9ae51db → 3f5f4d5）

我用**上一 commit 的代码**建库、写入一条 segment，再用**当前代码**迁移：

```
previous-commit SCHEMA_VERSION = 3   user_version: 3   rows: 1
--- migrate ---
user_version after migrate: 4  (target 4)
missing v4 columns: NONE
speed_ratio notnull flag: 0  (nullable ✓)
legacy row preserved: [('OLD_CASE','EP01',1.0)]
v2 write/read back: ('NEW_CASE','S001',1.25,0.11,1,'nominal_grid_time','YELLOW','REVIEW_REQUIRED')
nullable speed round-trip: None
```

**✅ 真实升级路径完全正确**：全部 v4 列到位、`speed_ratio` 可空、旧数据保留、v2 结果可读写、null 斜率往返正常。

## 原子性

`BEGIN IMMEDIATE` + 失败 `ROLLBACK` + 迁移前自动生成 `.pre-v4.bak` 备份 + `_migration_checkpoint` 故障注入点。**我在第二次审查中提的 M-6（迁移非原子）已修复。**

## m-2（Minor）　迁移分派条件不一致

```python
if existing and version < 3:                          self._migrate_v2_to_v3(...)   # 按版本号
if existing and "fitted_offset_seconds" not in cols:  self._migrate_v3_to_v4(...)   # 按列存在
```

我构造了一个 `user_version=3` 但 `segment_matches` 仍是 v2 形状的库（如部分恢复的备份）：`_migrate_v2_to_v3` 被跳过，缺 5 列，`user_version` 仍被标成 4，随后每次 `replace_case_result` 都抛 `OperationalError: no column named candidate_tier`。

**当前代码不会产生这种状态**（上一 commit 的 schema 已含 `candidate_tier`），所以不是现实缺陷。但 `_migrate_v2_to_v3` 内部本来就有列存在性守卫，把分派也改成列存在性即可消除这个脆弱面——与 v3→v4 的写法保持一致。

---

# 19. Runtime / Complexity

## 结构分析

```
slope bins S = 31；每候选触碰 S×2 = 62 个稀疏 cell
投票构建 O(n_candidates × S)  —— 不是成对，无无界 O(N²)
_robust_fit 采样对：min(32,n) 个下标 → 至多 496 对（有界）
峰抑制 O(peaks²)，上限 maximum_hypothesis_peaks_per_episode = 128
抽取循环最多 maximum_alignment_segments_per_episode = 128 轮，每轮对剩余候选重新投票
  → 最坏 O(K × n × S)
```

## 实测（top_k=8，单集）

| accused | 帧数 | 候选 | cell 量级 | 对齐耗时 | Python 峰值 | 段数 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 min | 120 | 900 | 55,800 | 1.13 s | 8.6 MB | 1 |
| 5 min | 600 | 4,500 | 279,000 | 4.48 s | 26.7 MB | 1 |
| 10 min | 1,200 | 9,000 | 558,000 | 7.69 s | 46.7 MB | 1 |
| 20 min | 2,400 | 18,000 | 1,116,000 | 13.62 s | 62.8 MB | 1 |
| 30 min | 3,600 | 27,000 | 1,674,000 | 18.89 s | 79.9 MB | 1 |

**线性，无爆炸。**

## 多集扩展（10 分钟被诉）

| 集数 | 对齐耗时 | 峰值 |
|---:|---:|---:|
| 1 | 7.72 s | 46.7 MB |
| 5 | 6.05 s | 15.0 MB |
| 40 | **5.67 s** | **3.7 MB** |

**集数增加反而更快**——候选按 episode 分组后每组更小，投票图更稀疏。**不存在多集复杂度爆炸。**

## 恶意/长视频

100 个真实短段（1200 候选）：13.23 秒，**100 段全部提取**。抽取循环可扩展。

## 报告数字的解读

- **`matcher cumulative = 31.57 s`**：120 个 case 的**累计**分析时间，含检索与解码，不是单次。我复现 31.28 s。
- **`peak memory lower bound = 514.8 MB`**："lower bound" 是因为它取 `max(主进程, 已回收 ffmpeg 子进程)` 的 `getrusage` 生命周期峰值——两者不同步，真实瞬时总峰值介于 lower bound 与两者之和之间（Phase 2A-0.2 已确立此口径）。
- **M3 24 GB 兼容性**：对齐层 30 分钟被诉视频仅 ~80 MB Python 峰值，加上 ffmpeg 解码与索引，仍在数百 MB 量级。**与 24 GB 目标相容，不是瓶颈。**

## m-1（Minor）　每集 128 段上限

真实混剪单集引用超过 128 段时会被静默截断。当前无告警。

---

# 20. Test Quality

`65 passed`。新增 9 个 alignment 测试，质量**明显高于**前几轮：

| 测试 | 评价 |
|---|---|
| `test_temporal_alignment_v2_ideal_speed_sweep`（参数化 8 档） | ✅ 真实覆盖 |
| `..._tolerates_grid_jitter_and_high_score_outliers` | ✅ 含 ±1 grid jitter + 高分干扰 + 错误 episode |
| `test_candidate_uncertainty_is_derived_from_configured_grid_period` | ✅ 防硬编码回归 |
| `..._extracts_five_piecewise_segments_with_reuse` | ✅ 5 段 + reuse |
| `..._merge_refits_all_inliers_instead_of_averaging_slopes` | ✅ 断言机制正确（但见下） |

## 高价值缺失测试

1. **纯噪声 → `MATCH_NOT_FOUND`**（完全没有；这是 v2 最强的一项能力，却无回归保护）
2. **merge 的否定用例**：斜率突变必须**不**合并（C-1 就在这里）、真实 gap 必须**不**合并
3. **长弱错误段不得压掉短强正确段**（C-2）
4. **超出 [0.75,1.50] 的斜率行为**（M-7）
5. **抽取顺序无关性**（我验证通过，但无测试锁定）
6. **`speed_ratio=null` 短跨度路径**
7. **竞争线的 ambiguity**（只有 exact duplicate 一例）

注：`..._merge_refits_all_inliers_instead_of_averaging_slopes` 断言的是"合并时重拟合"——而 C-1 恰恰说明**重拟合本身没问题，缺的是合并前的斜率兼容性判断**。该测试无法捕获 C-1。

---

# 21. Q A–E 直答

## A．核心算法实现是否正确？

**是。** 稀疏 slope-offset 投票、软投票、峰抑制、有界假设采样、迭代抽取与线特定证据认领，全部实现正确且我逐项独立验证：8 档速度全恢复、抽取顺序无关（30 次置换输出一致）、复杂度线性有界、纯噪声零 FP、跨集歧义正确触发。

**两处判据有缺陷**（merge 缺斜率检查 C-1；`_fit_quality` 时长主导 C-2），但它们位于**投票与拟合之外**，属于后处理排序与合并规则，可独立修复。

## B．是否真正解决了旧 constant-offset 对变速的结构缺陷？

**是，彻底解决。** 第一次审查实测旧架构的结构上限是 `|s−1| ≤ offset_bin/min_segment = 0.167`，实际可用区间 **0.81–1.19**。v2 实测 **0.75/0.80/0.90/1.00/1.10/1.25/1.40/1.50 全部恢复，误差 ≤ 0.0008**，方向语义正确（a=1.5 → 30 s accused 覆盖 44.97 s source）。合成 speed MAE 0.210 → 0.0914，真实 speed MAE 0.0378。

## C．是否真正支持 piecewise / 多段 / 多集 / gap / source reuse，而非 benchmark 特例？

**是，这是真实能力。** 我用**自己构造的**时间轴（5 段、3 集、2 个 gap、4 种速度、含 source reuse）得到 **5/5 召回、0 FP、reuse 精确标记**；同一 source clip 重复三次全部正确提取；100 个真实短段全部提取。这些构造与 completion benchmark 的构造方式无关。

## D．precision 61.02% → 52.17% 为什么下降？

**TP 完全不变（36 → 36），predicted 从 59 增到 69，新增的 10 条全部错误。**

逐条判定：
- **H1（更多 segment，阈值未校准）：证伪**——predicted 69 < GT 80，12 个欠分割、仅 1 个过分割
- **H2（弱峰过于积极）：部分成立**——新增来自更强假设机制，但是"位置错"不是"多余"
- **H3（split/merge 重复 FP）：基本证伪**——33 个 FP 中仅 2 个 duplicate
- **H4（噪声拟合）：纯噪声下证伪**（全部配置 0 FP）；**结构化错误候选下部分成立**（5 个 wrong-episode FP 全部来自 mirror 组）
- **threshold calibration：不是主因**——阈值敏感性显示 recall 5/5、噪声 FP 0 在很宽的参数范围内保持
- **benchmark composition：是重要成分**——范围外组贡献 11/33 FP，且合成语料的 DCT 检索质量已被前四次审查证明病态

**结论：79% 的 FP 是"集数对、时间戳错"。这是检索受限语料下更强对齐能力的可解释代价，不是算法缺陷。** 真实素材上不存在对应的 precision collapse（89.17%，0 unsafe wrong match）。

**但这不等于无害**：v2 在候选结构化错误时会给出高 `match_score` 的段而不是拒绝，这是第一次审查 C-1 的同一血脉。

## E．Phase 2A 是否可以 PASS？

**CONDITIONAL PASS —— 需要小范围 Phase 2A.1，不需要重做核心算法。**

理由：核心时序机制（B、C 已答）在 reviewer 自建输入下全部通过；缺陷是两条后处理判据加若干标定问题，每一条都能在不触碰投票/拟合逻辑的前提下单独修复；真实素材表现（89.17% recall、94.17% episode correctness、0 unsafe wrong match、7 个安全拒绝均含 ground truth）支持继续。

---

# 22. Phase 2A Readiness（分维度）

| 维度 | 判定 | 依据 |
|---|---|---|
| **Temporal mechanics** | **PASS** | 变速 8/8；offset 负值到 2300 s 正常；piecewise 5/5；gap 正确保留；source reuse 三次复用全对且标记 |
| **Safety** | **CONDITIONAL** | 噪声拒绝完美（全配置 0 FP）；ambiguity 语义未被绕过；GREEN 未恢复。**但 C-2（长弱假设静默压掉短强正确段）与 M-5（GREEN residual 恒真）未闭合** |
| **Integration** | **PASS** | 真实两集 20 位置：16/20 六档全通过；13 个 miss 中 7 个是含 ground truth 的安全拒绝，0 个 unsafe wrong match |
| **Metrics** | **CONDITIONAL** | 真实 recall 89.17% / episode 94.17% 可接受；但 ideal benchmark 的候选物理不可实现（M-1），120 case 的统计单位是 20（M-3），合成 precision 需按 §12 口径解读 |

## Phase 2A.1 建议范围（全部为局部修复）

| | 项 | 对应 |
|---|---|---|
| 1 | merge 增加斜率兼容性判据 | C-1 |
| 2 | `_fit_quality` 不以时长主导，或对被压制的强候选保留 alternative | C-2 |
| 3 | `maximum_frame_gap_seconds` 1.1 → 1.5（实测修复 5/13 真实 miss，纯噪声 FP 仍为 0） | M-2 |
| 4 | GREEN residual 判据改用 `residual_rms` 或 `inlier_ratio` | M-5 |
| 5 | 超出 [0.75,1.50] 的斜率拒绝或标记，不 clamp 后静默输出 | M-7 |
| 6 | ideal benchmark 的 `_line()` 改为网格对齐；补负样本与 merge 否定用例 | M-1、§20 |
| 7 | temporal benchmark 持久化 alternatives 并区分四类结果 | M-4 |

**均不涉及投票、拟合、抽取的重写。**

---

# 23. Questions for Architecture Owner

**Q1（最高优先级）　合成 precision 下降的接受口径**

范围内组贡献 22/33 FP，按 Owner 在指令中给出的字面判据（"如果 direct/speed/multi-segment 本身 precision 也显著下降，则是 blocker"）指向 blocker。但 TP 完全未变、79% 的 FP 是时间戳偏差、纯噪声 FP 为 0、真实素材无对应退化。
请裁定：(a) 按字面判据判为 blocker，要求合成 precision 恢复到 ≥61% 才关闭 Phase 2A；(b) 接受"检索受限语料下的可解释代价"，以真实素材指标为准；(c) 引入分层判据——范围内组只看 TP 不看 precision，precision 待 backend 升级后重测。
**我倾向 (c)**，因为合成语料的检索质量已被四次审查证明不足以支撑 precision 结论。

**Q2　安全拒绝是否应与错误答案分别计分**

真实 benchmark 13 个 miss 中 **7 个是含 ground truth 的 AMBIGUOUS 安全拒绝**（我已逐例验证 alternatives 内含正确 episode 与时间轴）。当前它们与错误答案同样计为 recall miss。
请裁定是否采用四分类计分：`correct match emitted / safe ambiguity containing GT / unsafe wrong match / no match`，以及 readiness gate 应以哪一个为准。按该口径，当前真实结果是 **108 正确 + 7 安全拒绝 + 0 不安全错误 + 5 边界截断**。

**Q3　`AMBIGUOUS` 时是否仍返回 segments**

v2 改为在 `ambiguous or contradiction` 时仍返回 `segments`（Phase 1.5 返回 `[]`）。这是我在第三次审查 Q4 中建议的 (c) 方案。请确认这是**有意采纳**而非副作用，并确认下游（未来 Excel / rule engine）读取 `status` 而非 `segments` 是否为空来判定。

**Q4　`maximum_frame_gap_seconds` 的标定**

1.1 秒单独造成 5/13（38%）真实 miss；调到 1.5 完全修复，且在我的全部纯噪声探针中 FP 仍为 0。但放宽 gap 会使一段引用更容易跨越真实的内容空洞。
请裁定该参数由谁标定、依据哪个数据集（当前只有 20 个真实位置）。

**Q5　超出速度范围的行为**

0.70–0.74 与 1.51–1.55 的真实斜率目前被 clamp 到端点后**静默输出**。对法律交付物，一段真实 1.55× 的引用会被报成 1.50×。
请裁定：(a) 拒绝并返回 `MATCH_NOT_FOUND`；(b) 输出但 `speed_ratio=null` 并标 `speed_out_of_range`；(c) 扩大配置范围。

**Q6　Phase 1 Gate 是否仍应阻断 Phase 2A 关闭**

Completion report 明确 Phase 1 gate 仍为 FAIL（source-aware recall 68.57%、precision 66.67%）。Phase 1 gate 建立在合成语料上，而合成语料的检索质量问题已被证明是主因。
请裁定 Phase 1 gate 是否应在 backend 升级前继续作为 Phase 2A 的阻断条件，还是转为"待 backend 升级后重测"的挂起项。

**Q7　权威时间轴映射的时点**

`source boundary MAE 0.219 s` 是 matcher 内部误差；相对真实 media PTS 还需叠加约 0.23 秒系统性偏移。ADR-0002 已记录"Phase 2A 完成前必须解决"，但本 commit 未实现。
请确认该映射是 Phase 2A 关闭的必要条件，还是可移入 Phase 2B。
