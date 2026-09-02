# Phase 2A-0 独立技术审查（第三次）

- **审查对象**：`1dd337d Complete Phase 2A-0 retrieval characterization`
- **上游 commit**：`a24ee39 Complete Phase 1.5.1 safety closure and retrieval harness`（引入 harness 本体）
- **审查人身份**：独立 reviewer，非 Phase 2 开发者
- **审查日期**：2026-09-03
- **方法**：全量阅读 harness（596 行）、schema、CLI/media diff、新增测试；**未修改任何生产代码**；用仓库中的 `real_test/EP01.mp4` + `EP02.mp4` **完整重跑了整个测量**，并运行 8 组只读探针
- **前置**：`docs/review/PHASE1-INDEPENDENT-REVIEW.md`、`docs/review/PHASE1.5-INDEPENDENT-REVIEW.md`

---

# Executive Verdict

## **CONDITIONAL PASS**

Harness 本身是方法论上扎实的测量工具。我逐条核验了 15 个审查点，**13 个通过**，包括几个我原本预期会出问题的关键点（全局 rank、off-grid 真实性、source/query 时间戳一致性、tolerance 非注水）。测量**完全可复现**——我在独立项目根目录重跑，所有数字逐位一致。

**但结论的表述强度超出了抽样设计能支撑的范围**，且**最具决策价值的一项分析缺失**。

## 复现结果（独立项目根目录，`real_test/` 两集 1080p / 78 分钟）

```
intervals=100  variants=200  frames=2400
recall@1   = 81.5000%      median rank=1   p90 rank=2
recall@8   = 95.3333%      rank dist: {1:1956, 2-8:332, 9-32:6, 33-128:12, >128:94, not_found:0}
recall@32  = 95.5833%      margin min=-1.446 p10=-0.004188 med=+0.003683 p90=+0.090502
recall@128 = 96.0833%      band = RECALL_AT_8_GTE_95_PERCENT
E001 R@8=94.8333%   E002 R@8=95.8333%   direct=95.3333%  reencode=95.3333%
P2_20-40% R@8=91.6667%   （其余四桶 95.83%–96.67%）
```

**与报告数字逐位一致。**

## 两个否决 PASS 的理由

**1. 头条结论没有通过聚类推断。** 2400 个 query frame 来自 100 个 interval、2 集。同一 interval 内 12 帧高度相关（ICC = 0.0825，design effect = 2.90），**有效样本量是 829，不是 2400**。

```
R@8 = 95.33%
  朴素 95% CI（假设独立，错误）: [94.49%, 96.18%]
  聚类修正 95% CI（诚实）      : [93.90%, 96.77%]   <- 下界低于 95%
```

**"recall@8 ≥ 95%" 这一命题在诚实推断下不成立。** 这与第一次审查里 "3/3 = 100%" 的错误属于同一类：把相关样本当独立样本计数。报告的 `observed_threshold_band = RECALL_AT_8_GTE_95_PERCENT` 是一个**点估计与阈值的比较**，没有区间。

**2. 最具决策价值的一项分析缺失：miss 是"差一格"还是"完全丢失"。** 我实测（direct 模式 56 个 recall@8 miss）：

```
top-8 中包含相邻 grid 源帧（<=0.76s）: 50 / 56 = 89%   <- 时序对齐完全可用
top-8 落在完全无关处                 :  6 / 56 = 11%   <- 真正丢失
```

即：**真正丢失的只有 0.5% 的 query frame（6/1200），而不是 4.7%。** 报告把这两类混在一个 `recall@8` 里，恰好抹掉了"retrieval 是否是 blocker"这个问题唯一需要的区分。

## 不给 FAIL 的理由

Harness 的方法论质量是实打实的，而且**我原本准备挑的几个刺都不成立**：

| 我预期的问题 | 实测结论 |
|---|---|
| rank 只在正确 episode 内排序？ | ❌ 不成立。`characterize_query_frame` 累加**全部** episode 的 (score, ep, ts) 后全局排序 |
| ±0.26s 抬高了 recall？ | ❌ 不成立。0.26 = `grid/2 + ε`，是**理论最小可辩护值**；0.2501 给 95.08%，差 0.25 个百分点 |
| off-grid 只是 metadata 层面的？ | ❌ 不成立。我用真实解码器 PTS 验证，query 内容确实携带请求的相位 |
| source/query 时间戳定义不一致？ | ❌ 不成立。两侧携带**相同**的 +0.23s 内容偏移，精确抵消 |
| 存在 query 自匹配泄漏？ | ❌ 不成立。on-grid 会给 cos=1.000000 的自匹配，但 harness 显式排除 phase < 0.021 |

**测量工具本身可以关闭。需要修的是结论的表述方式和两处抽样设计。**

---

# Critical

## C-1　头条结论未做聚类推断，`observed_threshold_band` 会把噪声读成通过

**证据（我从复现出的完整 observations 直接计算）：**

```
nominal query frames                : 2400
distinct (interval, frame) positions: 1200   <- reencode 与 direct 完全重复
distinct sampled intervals          :  100
distinct episodes                   :    2

ICC of recall@8 hits, clustered by interval = 0.0825
design effect = 1 + (m-1)*ICC = 1 + 23*0.0825 = 2.90
effective n = 2400 / 2.90 = 829
```

**并且 reencode 变体贡献零独立信息**——我逐帧配对核验：

```
失败的 (interval, frame) 位置数 : 56
其中 direct 与 reencode 同时失败 : 56 (100.0%)
仅 direct 失败 : 0     仅 reencode 失败 : 0
```

**100% 配对。** 所以 2400 这个数字里，有 1200 是同一内容的第二次计数。真实的独立单元数在 100（interval）到 1200（frame position）之间，绝不是 2400。

`_observed_threshold_band()`（`retrieval_benchmark.py:220-227`）用点估计 `recall["8"] >= 0.95` 直接产出一个枚举 band，这个 band 会被 Architecture Owner 的决策树直接消费。**在 [93.90%, 96.77%] 的区间下，这个 band 是一枚硬币。**

**修复方向**：`aggregate` 增加 `effective_sample_size`、`intra_cluster_correlation`、`recall_at_k_confidence_interval`（按 interval 做 cluster bootstrap）；`observed_threshold_band` 改为三态：`ABOVE`（CI 下界 ≥ 95%）/ `STRADDLES`（当前情形）/ `BELOW`。

## C-2　报告不区分"相邻格 miss"与"真正丢失"，掩盖了 Phase 2A-0 唯一要回答的问题

Phase 2A-0 的存在目的是回答"retrieval 还是不是首要 blocker"。但对下游 Temporal Alignment 而言，**"检索到相邻的源帧"和"检索到正确的源帧"几乎等价**——0.5 秒的偏移会被 offset 聚类 / ADR-0002 的软投票吸收，也在 Phase 1.5 gate 的 ≤1 秒 source tolerance 之内。

我用 tolerance 扫描量化了这一点（direct 模式，n=1200）：

| tolerance | 语义 | R@1 | R@8 | R@128 |
|---:|---|---:|---:|---:|
| 0.2501 | 只认最近的那一个源帧（理论最小值） | 81.00% | **95.08%** | 95.83% |
| 0.26 | 报告采用值 | 81.83% | **95.33%** | 96.08% |
| 0.50 | 允许任一相邻源帧 | 91.67% | **99.42%** | 99.67% |
| 0.76 | 允许 ±1 格 | 92.08% | **99.50%** | 99.83% |

**从 95.33% 到 99.42% 的 4 个百分点，全部是"找到了隔壁那一帧"。** 逐帧核验确认 89% 的 miss 属于此类。

因此报告应当同时给出两个数字：
- **`retrieval_recall@8 (exact grid) = 95.33%`** —— 严格定位精度；
- **`alignment_usable_recall@8 (±1 grid) = 99.50%`** —— 时序对齐实际可用的召回。

**只报第一个，会让 Architecture Owner 在决策树上看到一个"勉强及格"的数字，而真实情况是"远超需要"。** 这不是把结论往好里说——它是这份测量唯一真正的产出，现在被平均掉了。

---

# Major

## M-1　`reencode` 变体是近无损的，测不到真实被诉视频的分布

`_make_clip`（`benchmarking.py`）使用 `-c:v libx264 -preset veryfast -crf 18`，输入是 1080p 原片，输出同分辨率。CRF 18 在 1080p 上视觉近无损，而描述子还要把画面降到 160×90 再降到 32×32 做 DCT。**这个变体测的是"再压一次几乎无损的 x264 会不会影响检索"——答案是完全不会**（direct 与 reencode 的 R@8 到小数点后四位完全相同：95.3333% vs 95.3333%，56 个失败位置 100% 配对）。

真实的被诉视频来自 B 站 / 小红书：

| 维度 | 当前 reencode 变体 | 真实被诉视频 |
|---|---|---|
| 码率 | CRF 18（≈ 原片） | 平台转码，常见 1–3 Mbps |
| 分辨率 | 1920×1080 不变 | 常降至 720p / 480p，或竖屏裁切 |
| 编码器 | 同一 libx264 | 平台自有转码链，可能 HEVC/AV1 |
| 色度/色域 | 不变 | 常有 BT.601/709 处理差异 |

**结论：当前 2400 帧全部是"原片对原片"的自比对，这些数字是真实场景的上界，不是估计。** 报告的 README 说明了"direct 与 ordinary re-encode"，但 "ordinary" 一词会让读者以为覆盖了平台转码。

这是 **C-3 的答案所在**：这是唯一一项我认为必须在进 Temporal Alignment v2 之前补的 retrieval 测量，成本很低（换几个 ffmpeg 参数重跑一次）。

## M-2　DCT 在真实素材上的判别裕度同样razor-thin

复现数据：

```
score margin  min=-1.446407  p10=-0.004188  median=+0.003683  p90=+0.090502
|margin| < 0.01 的帧占比: 57.2%
```

**中位数裕度是 0.0037（余弦单位）。** 即使在 rank-1 命中的 81.5% 里，赢面也在小数点后第三位。我另测了单帧的绝对分数：

```
on-grid  query: top1 cos=1.000000  top2=0.990737  margin=0.009263
off-grid query: top1 cos=0.998288  top2=0.996407  margin=0.001881
```

这与第二次审查在合成语料上的发现同构（整集帧对余弦挤在 0.026 区间内）。**真实剧集把绝对分数抬高了，但没有把裕度拉开。**

含义：一旦叠加 M-1 的真实平台转码噪声，这 0.0037 的裕度极可能被淹没。**M-1 和 M-2 是同一件事的两面**，也是我对 D 问题（是否现在换 DINOv2）保持"暂不"但要求复测的原因。

## M-3　`by_source_phase_bucket` 的 n=240 同样是聚类膨胀，且相位是按 interval 赋值的

相位分层本身做得对——10 个桶各 10 个 interval，完美均衡（`interval_count_by_source_phase_bucket` 全为 10）。但：

- **相位是按 interval 赋值的**（`_balanced_phase_assignments` 每个 interval 一个 phase bucket），interval 内 12 帧共享同一相位；
- 因此 `by_source_phase_bucket` 表里的 `n=240` 实际只有 **10 个独立观测单元**；
- 表中呈现的相位—召回曲线本身是**真实且重要的**：

```
B01 (0.000-0.050): R@1=98.33%  R@8=99.17%
B04 (0.150-0.200): R@1=73.75%  R@8=91.67%
B05 (0.200-0.250): R@1=50.42%  R@8=91.67%   <- R@1 最低点
B06 (0.250-0.300): R@1=69.58%  R@8=94.17%
B10 (0.450-0.500): R@1=95.83%  R@8=99.17%
```

**R@1 呈现以相位 0.25 为中心的完美 V 形，从 98% 掉到 50%——这在真实剧集上确认了第二次审查在合成语料上发现的相位效应。** 但 R@8 的同一曲线只从 99.17% 掉到 91.67%，说明正确帧仍在 top-8 内。这是本次测量最有价值的正面发现之一，**应当在结论里被明确陈述**，而不只是留在一张分组表里。

需要修的是：给这些分桶数字标注独立单元数（10），不要让 n=240 造成精度错觉。

## M-4　索引与查询的绝对时间戳系统性早于真实媒体时间约 0.23 秒

我用 `-copyts` + `showinfo` 读真实解码器 PTS 验证：

```
SOURCE INDEX
  index label 600.000  ->  真实内容 pts 600.240   delta +0.240
  index label 600.500  ->  真实内容 pts 600.720   delta +0.220
  index label 1200.000 ->  真实内容 pts 1200.240  delta +0.240
  index label 1800.500 ->  真实内容 pts 1800.720  delta +0.220
                                        平均 delta_index = +0.230s

DIRECT QUERY（三个不同 start 各测 4 帧）
                                        平均 delta_query = +0.240s

残差失配 = 0.010s
```

**对本次测量无害**：两侧 delta 几乎相同，精确抵消，所以命中判定和相位标签都是自洽的（见 Metric Audit 中 Q2/Q3/Q15 的判定）。

**但对生产输出有害**：`segment_matches.source_start` / `source_end`、audit JSON、以及未来 Excel 里写的"EP07 00:17:42.11"，都携带这个约 0.23 秒的系统性提前量。这是 ffmpeg `fps` 滤镜的取帧约定（约等于半个输出周期），不是 bug，但**目前没有任何文档记录它**。在律所交付物里，一个恒定 0.23 秒的偏差应当被知晓并决定是否补偿。

## M-5　被审查的产物不在仓库里，无法从 repo 独立复核

`output/retrieval/` 被 `.gitignore` 排除（符合 §54，正确），`real_test/` 也被排除（正确）。但结果是：

- 仓库里只有 harness、schema 和**全 null 的空模板**；
- 我被要求审查的那些数字（94.83% / 95.83% / 95.33% / 91.67% / 94 frames）**在仓库里一份都没有**；
- 我能复核它们，唯一原因是 `real_test/` 恰好还在这台机器上。

`benchmark/retrieval/anonymized-aggregate-template.json` 的设计意图显然是"提交匿名聚合"，但提交的是模板而不是**实测的匿名聚合**。而 harness 已经实现了 `anonymized_aggregate_report()`，输出物里**不含任何文件名、路径、SHA-256 或案件标识**（我核对了 `by_episode` / `by_interval` 只有 `E001` / `E001-I017` 这类别名和归一化位置）。

**建议**：把实测的 `retrieval_characterization_aggregate.json` 提交到 `benchmark/retrieval/results/`。它已经满足 §54 的匿名要求，而没有它，Phase 2A-0 的结论就是一个无法被第三方复核的口头数字。

---

# Minor

- **m-1　`sampling_audit.query_subframe_phase_values_seconds` 恒为 `[0.0]`。** 复现确认。这是平凡真值（query 用本地网格），字段名容易被误读成"查询相位覆盖不足"。真正有意义的是 `source_subframe_phase_seconds`。建议改名或删除。
- **m-2　相位桶不等宽。** `_sample_off_grid_start` 把相位钳制到 `[0.021, 0.479]`（`retrieval_benchmark.py:158-159`），所以 B01 实际是 [0.021, 0.050]、B10 是 [0.450, 0.479]，宽度只有中间桶的 58%。方向是保守的（排除了最容易的近零相位），但两个边缘桶与其余八个不可直接比较。
- **m-3　`test_input_seek_resets_pts_before_query_fps_filter` 只验证命令行形状。** 它 monkeypatch 掉 `Popen`，断言 `-ss` 在 `-i` 前、filter 顺序为 `setpts,fps`。README 声称的行为是"decoded query frames, rather than only their labels, retain the requested off-grid phase"——**这个行为断言没有任何测试覆盖**。我用真实解码验证了它成立，但它是靠一次人工验证，不是靠回归测试保住的。建议加一个用短 lavfi 素材的真实解码断言。
- **m-4　`characterize_query_frame` 对每个 query frame 物化并排序全部源帧。** 9379 帧 × 2400 查询 = 2250 万次 tuple 构造 + 2400 次全量排序，整轮 2 分钟里绝大部分耗在这里。用 `np.argpartition` 取 top-128 再排序即可，但当前只是测量工具的性能问题，不影响正确性。
- **m-5　margin 用原始余弦，生产 `CandidateRetriever` 用 `(cos+1)/2`。** 两处的 "margin" 数值不可直接比较；ADR-0003 的 `minimum_uniqueness_margin = 0.05` 是在 `(cos+1)/2` 尺度上的。报告里的 `score_margin` 若被拿去校准那个阈值会差 2 倍。建议在 schema 里标注尺度。
- **m-6　`p90_rank` 用 `method="higher"`，`p10` 用 `method="lower"`。** 保守方向一致，但两者不对称且未在 schema 说明。
- **m-7　`_position_bucket` / `_phase_bucket` 的字符串键（`P2_20-40%`）同时承担分组键和展示标签。** 下游解析需要正则拆分。建议 JSON 里额外给结构化的 `{index, lower, upper}`。

---

# Metric Audit

## 逐条核验 15 个审查点

| # | 审查点 | 判定 | 依据 |
|---:|---|---|---|
| 1 | ground truth 真实独立 | ✅ | ground truth 是"query 帧取自源片 `start + k*0.5`"这一构造事实，不依赖被测表示。抽样用 `random.Random(seed)` 做时间分层 + 相位分层，**不使用 DCT 分数选样本**（README 明示，代码核对无违反） |
| 2 | query 真正 off-grid（非仅 metadata） | ✅ | 用 `-copyts`+`showinfo` 读真实 PTS 验证：三个不同 start，query 内容相对索引内容的相对相位 = 请求相位。`minimum_distance_from_source_grid_seconds = 0.0228`，`all_query_frames_off_source_grid = True` |
| 3 | source 2 FPS / query 2 FPS 时间戳定义一致 | ✅ | `delta_index = +0.230s`，`delta_query = +0.240s`，残差 0.010s。两侧同一提取路径、同一 `fps` 滤镜，偏移抵消。**但绝对值都早于真实媒体时间 0.23s（M-4）** |
| 4 | `recall@1/8/32/128` 定义正确 | ✅ | `hits[k] = (correct_rank is not None and correct_rank <= k)`；分母是全部 query frame（含 rank 为 None 的）。`aggregate_observations` 对 `count == 0` 返回 `None` 而非 0。rank 分布与 recall 互相自洽（1956+332 = 2288 = 2400×95.33%） |
| 5 | ±0.26s 是否人为抬高 recall | ✅ **否** | 0.26 = `grid_period/2 + 0.01`，是保证每个 query 至少有一个合格源帧的**最小可辩护值**。实测：0.2501 → R@8 95.08%，0.26 → 95.33%，**差 0.25 个百分点**。低于 0.25 时 20% 的 query 无合格帧（`mean #correct = 0.80, min = 0`），指标失去定义。只有 2.0% 的 query 拿到两个合格帧（需要相位落在 0.24–0.26 的 0.02s 窗口内） |
| 6 | rank 是否含全部两集源帧 | ✅ | `characterize_query_frame` 对 `source_indexes` 全部累加后 `ranked.sort()` 全局排序。我实测最差 rank = 9205，源池总量 9379 —— **只有全局排序才可能出现大于单集帧数（4801/4578）的 rank** |
| 7 | 数据泄漏（query 自身 / 缓存 / 文件派生） | ✅ **无泄漏，但是上界** | on-grid query 会给 `cos = 1.000000` 的自匹配；harness 显式排除 `phase < 0.021`，**从不抽到这个平凡情形**。索引由 `--force` 之外的正常路径构建，encoder identity 在 harness 启动时校验。**但 query 与 index 共用同一解码+缩放链，所以数字是真实被诉视频的上界（M-1）** |
| 8 | 抽样的 cluster dependence 与诚实处理 | ❌ **未处理** | ICC = 0.0825，design effect = 2.90，effective n = 829。报告全程按 n=2400 呈现，无 CI，无 ICC，无 effective n。见 C-1 |
| 9 | phase bucket 是否均衡 | ✅ 均衡，⚠️ 不等宽 | 10 桶各 10 个 interval，完美均衡；`_balanced_phase_assignments` 打散后分配，相位与时间顺序解耦（有测试覆盖）。边缘两桶因 0.021 钳制而窄 42%（m-2） |
| 10 | E001 94.83% vs E002 95.83% | ✅ **噪声，不值得关注** | 差 12 帧 / 1200。朴素两比例 z = 1.16（p = 0.246），**在计入聚类之前就已不显著**；每集只有 50 个独立 interval，计入 ICC 后更不显著 |
| 11 | P2_20-40% 的 91.67% 是否可定位 | ✅ **可定位** | 40 个 miss 中 22 个（55%）来自 3 个 interval：E001-I017 / I019 / I020，归一化位置 0.329 / 0.368 / 0.385 ≈ EP01 的 **13.2 / 14.7 / 15.4 分钟**——同一集的一个约 2 分钟连续区域。剔除这 3 个后该桶 R@8 = 96.25%，与其余四桶持平。**不是"20-40% 这个位置"的性质，是一集里的一处局部区域** |
| 12 | 94 个 rank>128 是否集中 | ⚠️ **不集中，且是灾难性失败** | 分布在 **36 个 interval**（共 100 个）；recall@8 的 112 个 miss 分布在 40 个 interval，top-5 只占 30%。**并非"少数坏镜头"**。且这些 miss 不是"刚好掉出 128"：rank 中位数 **3040 / 9379（第 32 百分位）**，最差 **9205（第 98 百分位）**。**提高 top_k 救不了它们** —— 这也解释了为何 R@8 → R@128 只涨 0.75 个百分点 |
| 13 | 负 score margin 是否预期 | ✅ **定义使然** | `margin = max(correct) − max(incorrect)`，负值 ⟺ 最好的错误帧分数更高 ⟺ rank > 1。实测：负 margin 帧数 444，rank>1 帧数 444，**两个集合完全相同**。不是 ranking / margin 定义问题 |
| 14 | direct 与 reencode 几乎一致是否合理 | ✅ **合理，但说明变体无信息量** | 两者到小数点后四位完全相同（95.3333%）。56 个失败位置 **100% 在两种模式下配对失败**。原因：两者用同一 `-ss` 输入定位语义 + 同一 `fps` 滤镜，唯一差别是一次 CRF 18 的近无损 x264。**合理，但因此该变体贡献零独立信息并使 n 虚增一倍（C-1、M-1）** |
| 15 | PTS reset 是否正确 | ✅ **达成了声称的目的** | `setpts=PTS-STARTPTS` 置于 `fps` 之前（测试覆盖命令形状），使本地网格从 0 起算，从而 `start + k*0.5` 携带请求相位。真实解码验证通过。**但该行为断言只有 mock 测试，无真实解码回归（m-3）** |

## 指标定义的正面评价

- `recall@k` 分母正确（全部 query frame，不排除未找到的）；
- `not_found = 0` 与 `>128 = 94` 分开计数——这是有意义的区分，说明正确帧永远在索引里，只是排名靠后；
- `correct_source_rank_distribution` 的分箱（1 / 2-8 / 9-32 / 33-128 / >128 / not_found）与 top_k 值对齐，可直接读出边际收益；
- `decision: null` + `decision_note` 明确 harness 不做路线选择——**纪律正确**，与 ADR-0002 "不应与视觉 backend 切换同时实施"的归因原则一致；
- schema 用 `const` 锁死 `source_fps: 2.0` / `query_fps: 2.0` / `top_k_values`，防止事后改口径；
- `RetrievalBenchmark.__init__` 在 `coarse_fps != 2.0` 时直接抛错，`run()` 校验 index encoder identity 与 `target_fps == 2.0`——**拒绝在错配的索引上出数**，这是好设计。

---

# Sampling Audit

## 抽样设计的实测审计

```
episodes                    : 2            <- 顶层 cluster，n=2，无法估计集间方差
intervals                   : 100（每集 50）
query frames (nominal)      : 2400
distinct content positions  : 1200         <- reencode 完全重复 direct
effective n (cluster-adj)   : 829

intervals per phase bucket  : 全部 = 10    ✅ 完美均衡
intervals per episode       : E001=50, E002=50   ✅
intervals per position      : 每桶 = 20    ✅
all_query_frames_off_source_grid : True
minimum_distance_from_grid  : 0.0228s
```

## 三层聚类，报告只承认第零层

| 层级 | 独立单元数 | 报告中的呈现 |
|---|---:|---|
| episode | **2** | `by_episode` 直接比较 94.83% vs 95.83%，未提 n=2 |
| interval | **100** | `by_interval` 有 100 行，但聚合按 frame 计数 |
| (interval, frame) position | **1200** | —— |
| query frame（含 mode 重复） | 2400 | **所有 headline 数字用这一层** |

ICC = 0.0825 是**在 interval 层**测得的。**episode 层的 ICC 无法估计**（只有 2 个），所以真实的 design effect 只会比 2.90 更大。上面给出的 [93.90%, 96.77%] 是一个**乐观的**区间。

## 分层做得好的地方

- **时间分层是无监督的**：`bin_start = 0.25 + usable * i / N` 把全片等分，不使用被测表示选样本。README 明确说 "does not claim semantic scene labels; broad time stratification supplies scene diversity without selecting samples using the retrieval representation under test"——**这个纪律是对的**，避免了用 DCT 分数挑样本这种自证循环。
- **相位与时间顺序解耦**：`_balanced_phase_assignments` 先构造均衡序列再 shuffle，有专门测试（`test_phase_assignments_are_balanced_and_decoupled_from_time_order`）。如果相位随 interval 序号单调递增，相位效应会与时间位置效应混淆——这一点被正确防住了。
- **每个 interval 落在自己的时间 bin 内**：`_sample_off_grid_start` 在 `[bin_start, bin_end]` 内随机取网格点，保证 100 个 interval 不重叠、覆盖全片。

## 建议

1. `aggregate` 增加 `effective_sample_size`、`intra_cluster_correlation`、按 interval 的 **cluster bootstrap CI**；
2. `by_episode` / `by_source_phase_bucket` / `by_source_position_bucket` 每行标注**独立 interval 数**，不只标 frame 数；
3. reencode 若保留，应改为真实平台档（M-1）；若不改，应在计数时明确它不增加独立样本；
4. 集数从 2 提到 ≥ 5，才谈得上估计集间方差。

---

# Failure Concentration Analysis

## 失败不集中，且是灾难性的

```
recall@8 misses          : 112 / 2400
受影响的 interval        : 40 / 100
top-1 interval 占比      : 10/112 = 8.9%
top-3 interval 占比      : 22/112 = 19.6%
top-5 interval 占比      : 34/112 = 30.4%

rank > 128 的 94 帧
  分布 interval          : 36 个
  rank 中位数            : 3040 / 9379  (源池第 32 百分位)
  rank 最差              : 9205 / 9379  (第 98 百分位)
```

**两个独立的结论：**

1. **没有"少数坏镜头"可以摘除。** 36 个 interval 各贡献一点，绝大多数受影响 interval 只有 2/24 帧失败（= 1 个内容位置 × 2 个 mode）。这是一个**弥散的、内容驱动的**失败模式。
2. **失败是灾难性的而非边缘的。** rank 中位数落在源池的第 32 百分位，最差落在第 98 百分位——正确帧与随机帧统计上不可区分。这在 recall 曲线上表现为 **R@8 → R@128 只涨 0.75 个百分点**（95.33% → 96.08%）。**提高 `top_k` 无效。**

## 但 89% 的 miss 只是"差一格"

```
direct 模式 recall@8 miss = 56
  top-8 含相邻源帧（<=0.76s）: 50 (89%)
  top-8 完全落空              :  6 (11%)
```

把这两件事放在一起，真实图景是：

| 类别 | 占全部 query frame | 对 Temporal Alignment 的影响 |
|---|---:|---|
| rank 1 命中 | 81.5% | 理想 |
| rank 2–8 命中 | 13.8% | 完全可用 |
| miss 但 top-8 含相邻格 | 约 4.2% | **可用**——0.5s 偏移在 ADR-0002 软投票和 ≤1s source tolerance 之内 |
| 真正丢失 | **约 0.5%** | 不可用，需要更强表示或更密采样 |

**只有约 0.5% 的 query frame 是真正丢失的。**

## 位置分桶的 P2 凹陷是局部现象

```
E001-I017  pos 0.329 -> 约 13.2 min   R@8 = 58.3%
E001-I019  pos 0.368 -> 约 14.7 min   R@8 = 75.0%
E001-I020  pos 0.385 -> 约 15.4 min   R@8 = 75.0%
E001-I013  pos 0.257 -> 约 10.3 min   R@8 = 83.3%
```

EP01 的 10–15 分钟区域集中了 P2 桶 40 个 miss 中的多数（E001 贡献 30，E002 只有 10）。剔除最差 3 个 interval 后该桶 R@8 = 96.25%。

**这是一个内容区域的性质，不是"影片 20-40% 位置"的性质。** 由于 harness 刻意不做语义场景标注（这个纪律是对的），无法在不引入新方法的前提下断言那里是什么。若要定位，正确做法是对该区域做**索引内自相似分析**（源帧之间的余弦分布），这不使用任何被测之外的表示，也不需要人工标注。

## 相位效应在真实素材上确认

```
source phase   R@1      R@8
0.000-0.050   98.33%   99.17%
0.150-0.200   73.75%   91.67%
0.200-0.250   50.42%   91.67%   <- R@1 最低
0.250-0.300   69.58%   94.17%
0.450-0.500   95.83%   99.17%
```

R@1 呈以 0.25 为中心的对称 V 形，**从 98% 掉到 50%**。这在真实剧集上确认了第二次审查在合成语料上发现的机制：off-grid 的 query 帧在 2 FPS 索引里没有精确对应物。

**但 R@8 的同一曲线只掉到 91.67%。** 这是本次测量最重要的正面结果：**相位问题在 rank-1 上是灾难，在 top-8 上基本被吸收。** Phase 1.5 gate 失败的那些样本，其正确帧确实在 top-8 里——瓶颈不在检索，在于 alignment 阶段如何使用这 8 个候选。

---

# DCT / DINOv2 Recommendation

## 直接回答四个问题

### A.　`recall@8 = 95.33%` 是否足以支持"retrieval 不再是首要 blocker"？

**按报告现在的表述：不足以。** 95.33% 的聚类修正 95% CI 是 **[93.90%, 96.77%]**，下界低于 95%。用一个跨越阈值的点估计去触发决策树，是第一次审查里 "3/3 = 100%" 那类错误的重演。

**但这个结论本身是成立的——只是需要换一组更强的证据来支撑：**

1. **只有约 0.5% 的 query frame 真正丢失**（89% 的 miss 仍在 top-8 里带回相邻格源帧）；
2. **允许 ±1 格时 R@8 = 99.50%**，而 ±1 格（0.5 秒）完全在 ADR-0002 软投票和 Phase 1.5 gate 的 ≤1 秒 source tolerance 之内；
3. **相位效应在 top-8 层面已被吸收**（R@8 最差桶 91.67% vs 最好桶 99.17%，而 R@1 是 50% vs 98%）；
4. Phase 1.5 gate 失败的直接原因（第二次审查 M-1/M-2）是 offset 分桶把连续匹配切碎，而不是候选集里没有正确帧——**本次真实素材数据与那个诊断一致**。

**所以：结论正确，论证需要重写。** 应当基于"真正丢失率 0.5%"和"±1 格可用召回 99.5%"，而不是基于"95.33% ≥ 95%"。

### B.　是否可以保持 DCT + source/query 2 FPS，进入 Temporal Alignment v2？

**可以，附两个条件。**

保持的理由：真正丢失率 0.5%，且 ADR-0002 的 slope-offset 投票天生能吸收 ±1 格误差（我在第二次审查里建议的**软投票**——每票同时投给相邻两个 bin——正好是为这个设计的，本次数据证明它是必需的而非可选的）。

**条件 1**：ADR-0002 的实现必须**显式设计为容忍 ±1 个 source grid step**。这不再是可选优化——89% 的检索 miss 都是这种形态。建议写进 ADR-0002 的"尚待决定"章节：offset 投票分辨率 0.5s + 软投票权重分配。

**条件 2**：先补 M-1 的真实平台转码测量（见 C）。当前数字是原片对原片的上界。

### C.　是否存在必须先做的 retrieval 修复？

**有一项，且只有一项：把 `reencode` 变体改成真实平台档。**

不是"修复"，是"补测"。当前变体（CRF 18、同分辨率、同编码器）与 direct 的结果到小数点后四位相同、56 个失败位置 100% 配对——**它测不到任何东西**。

建议的最小变更（只改 ffmpeg 参数，不动 matcher）：
- 转 720p 与 480p 各一档；
- 码率 1.5–2 Mbps（CRF 28–32 或直接限码率）；
- 至少一档非 x264（如 libx265）；
- 一档竖屏裁切（模拟小红书）。

这一步的成本是重跑一次（约 2 分钟索引已在、几分钟查询），**但它决定了 A/B/D 三个问题的答案是不是建立在真实分布上**。以 M-2 的裕度数据（中位 margin 0.0037，57% 的帧 |margin| < 0.01）判断，我预期真实平台档会显著低于 95%——这个预期必须被测量证实或证伪，而不是被假设。

**除此之外没有必须先做的 retrieval 修复。** 特别地：**不需要**提高 `top_k`（rank 中位数 3040，无效），**不需要**非对称采样（真正丢失率已经只有 0.5%），**不需要** FAISS（9379 帧的精确检索 2 分钟跑完 2400 查询，且瓶颈在 Python 侧的全量排序，见 m-4）。

### D.　是否有证据现在就应该换 DINOv2？

**没有。**

反对现在换的证据：
- 真正丢失率 0.5%，换 backend 的边际收益上限就是这 0.5%；
- ADR-0002 明确写了"不应与视觉 backend 切换同时实施，否则无法归因指标变化"——**这条纪律在本次数据下更重要了**，因为 alignment 层有一个已知的、结构性的、已定位的缺陷（offset 分桶），必须先单独修掉才能看清 backend 的边际价值；
- 换 DINOv2 会把索引从 144 维涨到 384/768 维，索引体积 ×3–5，建库时间从本次实测的 **49 秒/小时**（真实 1080p，两集 78 分钟耗时 64 秒）涨到数小时级——这是一个需要 Q5 类性能裁定的量级变化。

**但有一个明确的预警信号必须记录**：M-2 的裕度数据。中位 margin 0.0037、57% 的帧 |margin| < 0.01。DCT 在真实素材上**赢得很险**。一旦叠加真实平台转码噪声（M-1），这个裕度很可能不够。

**所以我的建议是：不是"不换"，而是"换的决策点在 C 之后，不在现在"。** 具体：
1. 先补真实平台档测量（C）；
2. 若平台档 R@8（±1 格口径）仍 ≥ 98%，则 DCT 足够，直接做 ADR-0002；
3. 若平台档显著下降，则 DINOv2 的决策点提前到 Temporal Alignment v2 **之后**、Phase 2B 之前，且仍应单独实施以保归因。

**保留 DCT 作为 Layer 1 fast path 的理由不变**（第一次审查 ADR-0001 Review）：确定性、可逐位复现、镜像在频域有解析关系、索引仅 144 维。本次实测又加了一条：**真实 1080p 剧集索引只要 49 秒/小时**，这个成本低到可以一直保留它做交叉验证。

---

# Phase 2A Readiness

## 建议顺序

**2A-0.1　补真实平台转码档（约半天，只改 ffmpeg 参数）**

见 C。这是唯一的硬前置。产出：一张"query 变换档 × R@8（严格 / ±1 格）"的表。

**2A-0.2　把 measurement 的结论重述为可决策的形式（约半天，只改 harness 报告层）**

三项，都不改测量逻辑：
1. `aggregate` 增加 `effective_sample_size` / `intra_cluster_correlation` / cluster bootstrap CI；
2. 新增 `alignment_usable_recall_at_k`（±1 grid step 口径），与严格口径并列；
3. `observed_threshold_band` 改三态（`ABOVE` / `STRADDLES` / `BELOW`），依据 CI 下界而非点估计。

做完这两步，Architecture Owner 的决策树才有一个不会把噪声读成通过的输入。

**2A-1　ADR-0002 Temporal Alignment v2**

本次数据支持直接进入，条件是软投票（±1 格容忍）写进设计。第二次审查给出的其余建议（成对参数化控复杂度、票数当歧义信号、residual 三元组、speed_ratio 方向语义与合并规则）不变。

**2A-2　DINOv2 决策点**，依据 2A-0.1 的结果，且必须在 2A-1 完成并单独测量之后。

## 关于"是否可以关闭 Phase 2A-0"

**可以关闭测量工具，但不能关闭结论。** Harness 本体已经达到它的设计目标，15 个审查点通过 13 个。剩下的是报告层的表述问题和一次补测——都不需要改测量逻辑，也不触碰 matcher。

---

# Questions for Architecture Owner

**Q1（最高优先级）　决策树消费点估计还是区间？**

`observed_threshold_band` 目前由 `recall["8"] >= 0.95` 的点估计产生。聚类修正后 R@8 的 95% CI 是 [93.90%, 96.77%]，跨越阈值。
请裁定：(a) 决策树改为消费 CI 下界；(b) 维持点估计但要求样本量提升到 CI 下界能明确落在阈值一侧；(c) 引入 `STRADDLES` 三态并规定该状态下的默认路线。
**在裁定之前，`RECALL_AT_8_GTE_95_PERCENT` 这个 band 不应被解释为"阈值已达成"。**

**Q2　"retrieval 召回"的官方口径是严格网格还是 ±1 格？**

严格口径 R@8 = 95.33%；±1 格口径 R@8 = 99.50%。两者相差 4 个百分点，且决定了 A/B/D 三个问题的答案。
±1 格（0.5 秒）在 ADR-0002 的软投票和 Phase 1.5 gate 的 ≤1 秒 source tolerance 之内，因此我认为**它才是与下游相关的口径**；严格口径应保留为定位精度指标。
但这是一个口径定义，需要 Owner 裁定，并写进 ADR-0005。

**Q3　是否批准补测真实平台转码档？**

见 C 与 M-1。当前 `reencode` 变体是近无损的，与 direct 结果完全相同、失败位置 100% 配对，零信息量。补测只改 ffmpeg 参数，不动 matcher，不违反 Phase 2A-0 的暂停项。
若不批准，则必须在所有对外表述中把当前数字标注为**原片对原片的上界**，不得用于估计真实案件召回。

**Q4　是否把实测的匿名聚合 JSON 提交入库？**

`anonymized_aggregate_report()` 的输出已不含文件名、路径、SHA-256、案件标识（我核对过），符合 §54。但当前仓库只有全 null 模板，导致 Phase 2A-0 的结论无法被第三方复核——我能复核它，唯一原因是 `real_test/` 恰好还在这台机器上。
建议提交到 `benchmark/retrieval/results/`。

**Q5　索引时间戳相对真实媒体时间早约 0.23 秒，是否需要补偿？**

实测 `delta_index = +0.230s`、`delta_query = +0.240s`（ffmpeg `fps` 滤镜取帧约定）。对匹配无害（两侧抵消），但 `segment_matches.source_start/source_end`、audit JSON 和未来 Excel 里的"EP07 00:17:42.11"都携带这个恒定提前量。
请裁定：(a) 不补偿，但在 schema 与交付文档中明确记录该约定；(b) 在索引期补偿到真实 PTS；(c) 改用真实 PTS 而非 `frame_index / fps` 作为时间戳来源（这会同时解决第一次审查的 m-12）。

**Q6　episode 数量：2 是否足以支撑任何跨集结论？**

顶层 cluster n=2，集间方差不可估计。`by_episode` 表把 94.83% 与 95.83% 并排呈现，容易被读成一个可比较的差异（实测为噪声，p=0.246）。
请裁定后续测量的最小集数，以及在达到该数量之前 `by_episode` 是否应当只报告而不做比较。
