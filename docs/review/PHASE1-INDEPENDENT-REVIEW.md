# Phase 1 独立技术审查

- **审查对象**：Architecture Baseline v0.1 的 Phase 0 + Phase 1 + Synthetic Benchmark 基础框架
- **审查人身份**：Phase 1 独立技术审查者（非功能开发者）
- **审查日期**：2026-09-02
- **代码状态**：`71e8d93` + 未提交工作树（`src/`、`tests/`、`benchmark/`、`config/`、`docs/adr/`、`README.md` 均未入库）
- **审查方法**：全量阅读 13 个源文件与 7 个测试文件；对生产代码**未做任何修改**；在 scratchpad 中运行 9 组只读探针实验验证具体缺陷

---

# Executive Verdict

## **CONDITIONAL PASS**

Phase 1 的**工程骨架可以保留并在其上继续开发**，但**现在不能进入 Phase 2**。

理由分三层：

**第一层——实现是真实的。** 匹配路径（`features.py` / `matching.py` / `indexer.py` / `media.py` / `database.py` / `models.py`）中不存在任何 benchmark 标识符、case ID、测试路径或 hard-code 分支（已全量 grep 验证）。ground truth 由 `SyntheticBenchmarkGenerator` 依据 FFmpeg 参数独立生成，不反向依赖 matcher 内部状态。`MATCH_NOT_FOUND` 是真实代码路径，对 5 类完全无关内容（彩条 / 渐变 / 纯色 / 噪声 / 分形）全部正确拒绝，未出现虚构匹配。输入 `input/` 只读约束成立。`.gitignore` 符合 §55。整个 gate 在干净根目录下可完整复现（recall 1.0 / MAE 0.0 / 13.40 s per hour / 46.3 MB，与提交值一致）。**这不是造假，是一个诚实但被严重高估的基线。**

**第二层——benchmark 数字不可信。** `3/3`、`Segment Recall 100%`、`Episode Accuracy 100%`、`Boundary MAE 0.00s` 四个数字中，只有第一个字面为真。`Episode Accuracy` 在代码上恒等于 `Segment Recall`（同一分支同一行自增，聚合层同一表达式），不是独立指标；`Boundary MAE 0.00s` 是 ground truth 边界被 `round(x*2)/2` 对齐到 0.5 秒抽帧网格的产物——把切点移出网格后，同一算法、同一集、同一「直接剪切」变换下 MAE 变为 **2.13 秒**，最大单点误差 **12.09 秒**，8 个样本中 2 个直接漏检。整套 benchmark 没有任何 precision / false-positive 指标，凭空捏造的侵权区间不扣分。

**第三层——存在 Phase 1 范围内（非 Phase 2）的 Critical 缺陷。** 集数识别是 Phase 1 唯一交付物，而当剧集间存在重复内容（片头 / 片尾 / 前情提要——中国电视剧的普遍结构）时，系统以 **confidence 1.0000** 输出任意一集；原片索引不完整时，系统以 **confidence 0.9939** 输出偏离 12 秒的错误时间轴。按 §37 分档这两者都是 **GREEN → 自动写入 Excel、不进人工复核**。置信度公式中不含任何唯一性 / 竞争者间隔项，因此它度量的是「内部自洽程度」，与「是否正确」无关。

**进入 Phase 2 的前置条件（全部满足前不要开始 Phase 2）：**

| | 条件 | 对应 Critical |
|---|---|---|
| C1 | 修复 benchmark 指标定义（独立 episode accuracy、precision/FP、source 侧时间校验、off-grid 采样、Inf 处理） | C-2 |
| C2 | 置信度加入唯一性 / margin 项，或在唯一性不足时强制降级为 `AMBIGUOUS`，禁止输出 GREEN | C-1、C-4 |
| C3 | 索引完整性校验（`len(timestamps)` vs `duration × coarse_fps`），不足即 FAIL 而非静默 | C-4 |
| C4 | 架构负责人书面裁定 §16「P1 最低验收目标」与 §48「Phase 2」的范围冲突 | C-3 |

**不给 FAIL 的理由**：模块边界、`ImageEncoder` 抽象、缓存版本化、SQLite schema、audit JSON、结构化日志、CLI 契约、只读约束、确定性复现——这些 Phase 0 基础设施质量良好，Phase 2 应在其上继续，而非重写。
**不给 PASS 的理由**：现有 gate 证据无法支撑「被诉视频 → 原剧集数 + 原剧时间轴」已经做准这一结论；若带着当前指标进入 Phase 2，所有 Phase 2 调优都会朝一个看不见假阳性、看不见错集数、看不见错时间轴的目标函数收敛。

---

# Critical

> 会导致结果不可信、benchmark 无效或架构错误的问题。

## C-1　置信度与正确性无关，错误答案被判为 GREEN 自动放行

`matching.py:169-172`

```python
visual   = mean(inlier visual_score)
coverage = min(1.0, len(inliers) / ((end-start) * coarse_fps))
temporal = min(1.0, len(inliers) / len(proposal.matches))
confidence = visual * (0.7 + 0.3*coverage) * temporal
```

三项全部度量**被接受的那一个提案自身的内部自洽程度**，没有任何一项度量：

- 第二名候选集与第一名的分差（唯一性 margin）；
- 命中区间在原片中是否唯一；
- 索引本身是否完整。

**实测（探针 E2b，纯直接剪切，无任何变换）：**

| 场景 | 真值 source | 预测 source | 误差 | confidence | §37 分档 |
|---|---|---|---|---|---|
| 网格对齐切点 | 11.50 | 11.50 | 0.00 s | 1.0000 | GREEN |
| 非网格切点 | 13.37 | 13.50 | 0.13 s | 0.9654 | GREEN |
| 非网格切点 | **15.91** | **28.00** | **12.09 s** | **0.9939** | **GREEN** |
| 非网格切点 | 17.13 | 21.50 | 4.37 s | 0.9119 | YELLOW |

一个偏差 12 秒的错误时间轴拿到 0.9939，比一个偏差 0.13 秒的正确答案（0.9654）**更高**。§37 的 GREEN 阈值 0.95 在这个分数上完全失效——它把最坏的错误优先送进「无需人工复核」通道。

对律所场景的直接后果：Excel 的「对应集数」「原片时间轴」「引用秒数」三个字段会被一个自称高置信的错误答案填满，且 review queue 不会收到任何信号。

**这不是「Phase 2 再优化」的问题**，因为 §37 的分档语义与 §38 的 review queue 都建立在这个分数之上。

## C-2　Benchmark 指标在数学上无法发现错误

### C-2a　`episode_accuracy` 恒等于 `segment_recall`

`benchmarking.py:198-213`

```python
compatible = [p for p in predictions
              if p.source_episode == expected["source_episode"]      # ← 集数相等是入选前提
              and overlap(...) / expected_duration >= 0.5]
if not compatible: continue
recalled += 1
correct_episodes += 1                                                # ← 与 recalled 同一行同一分支
```

`benchmarking.py:283-284`

```python
"segment_recall":   total_recalled / total_truth,
"episode_accuracy": total_recalled / total_truth,      # ← 字面同一表达式
```

集数相等已经是 `compatible` 的**筛选条件**，因此 `correct_episodes ≡ recalled` 恒成立。实测四种输入（对集数对时间 / 错集数 / 对集数错时间 / 无输出）两个指标始终相等。

**「Episode Accuracy 100%」相对「Segment Recall 100%」提供的信息量为零。** 错集数不被记为「集数错误」，只是让 recall 下降；报告里永远不会出现 `episode_accuracy < segment_recall` 的情形。

### C-2b　`segment_recall` 不校验原片侧时间轴

同一段代码中，`_overlap` 只计算**被诉侧**区间重叠。一条「accused 0–4s 来自 EP02 的 40:00」在真值为「EP02 的 10:00」时，仍然计入 `recalled += 1`，recall = 1.00。实测确认。

README 宣称的交付是「被诉视频时间段 → 原剧集数 → 原剧时间段」三段式，而名为 `Segment Recall` 的指标只验证了前两段。原片时间轴的正确性仅通过 `boundary_mae_seconds` 以**平均值**形式体现，不构成任何门槛。

### C-2c　完全没有 precision / false-positive 指标

实测：1 条真值 + 2 条凭空捏造的 40 秒 / 30 秒侵权区间 → `{'segment_recall': 1.0, 'episode_accuracy': 1.0, 'boundary_mae_seconds': 0.0}`。**捏造代价为零。**

对侵权比对系统而言，假阳性（把 UP 主原创画面判为权利作品）在法律后果上比假阴性更严重，而它当前完全不可观测。A–J 中也没有任何一条「全部为非权利作品」的负样本 case。

### C-2d　聚合 MAE 丢弃失败样本，失败越多越好看

`benchmarking.py:268-271`

```python
boundary_values = [m["boundary_mae_seconds"] for m in case_results
                   if math.isfinite(m["boundary_mae_seconds"])]     # ← 漏检记为 inf，被过滤掉
```

漏检样本的 MAE 是 `math.inf`，被排除在平均之外。诊断报告的 `0.2s` 只是 A/E/G/I/J 五个成功样本的均值 `(0+1.0+0+0+0)/5`。**多失败一个难样本，报告 MAE 会变好。** 这是一个条件于成功的有偏估计量，不能作为门槛。

### C-2e　ground truth 的 `speed_ratio` 从不被校验

`GroundTruthSegment.speed_ratio` 写入 manifest（`benchmarking.py:29`），`evaluate_segments` 从不读取。CASE_D 真值 0.8×，系统输出 `speed_ratio: 1.0`，指标层完全静默。§56.B「识别主要处理方式：镜像 / 加速 / 裁剪 / 缩放」在 benchmark 中无任何覆盖。

## C-3　Temporal Alignment 的结构性速度上限远低于架构要求，RANSAC 实质失效

`matching.py:85-87` 先按**常数 offset** 分桶，`matching.py:111` 再要求单桶内连续段 ≥ `minimum_segment_seconds`：

```python
offset = candidate.source_time - candidate.accused_time
bucket = round(offset / self.config.offset_bin_seconds)      # 0.5 s
...
if duration + 1e-6 < self.config.minimum_segment_seconds:    # 3.0 s
    continue
```

`_robust_fit` 的 RANSAC（`matching.py:117-149`）确实允许 slope ∈ [0.5, 2.0]，**但它只在单个 offset 桶内部运行**。速度为 s 时，offset 在时长 T 上漂移 `|s-1|·T`；要让 3.0 秒内容留在同一个 0.5 秒桶里，必须满足：

```
|s − 1| ≤ offset_bin_seconds / minimum_segment_seconds = 0.5 / 3.0 = 0.167
```

**实测扫描（无噪声完美候选，speed 0.60→1.60 步进 0.01）：能产出任何 segment 的区间为 `0.81 – 1.19`。**

架构 §16 要求 **0.75 – 1.50**。且在 1.10 这种「勉强能过」的速度下，前 2.5 秒被丢弃（`accused_start` 从 0.00 变成 2.50）——即使输入完全无噪声。

**结论**：§11 称为「V1 的技术核心」的 `source_time = a × accused_time + b` 线性拟合，其斜率估计能力被前置的常数 offset 聚类结构性地阉割了。CASE_C（1.25×）/ CASE_D（0.8×）/ CASE_H 的失败**不是特征质量问题，换成 DINOv2 / SigLIP 也不会好转**。这是 Phase 1 的算法结构缺陷，必须在 Phase 2 换 backend **之前**通过 ADR 决定新的对齐策略（见 Phase 2 Recommendation R-2）。

## C-4　重复内容与残缺索引下的静默错误（违反 §46）

### C-4a　跨集重复内容 → 任选一集，confidence 1.0000

探针 E3：构造 3 集，各集**共享一段逐帧完全相同的 10 秒片头**，其余部分互不相同。取一段完全来自共享片头的 6 秒被诉片段：

```
### 来自共享片头（EP01/EP02/EP03 三者皆为合法答案）
    accused[0.00, 6.00] -> EP01 [1.00, 7.00]  visual=1.0  temporal=1.0  confidence=1.0000
```

系统输出单一集数 EP01（`list_original_indexes` 的 `ORDER BY episode` 决定），**置信度满分，没有任何歧义信号**。作为对照，取自 EP02 独有正片的片段被正确判为 EP02——说明算法本身工作正常，问题在于**歧义不可观测**。

这不是构造出来的边角情形：中国电视剧每一集都有相同片头，多数有相同片尾，且**前情提要会逐帧重放前一集画面**。一条引用片头或前情提要的 B 站解说视频，会拿到一个满分的错误集数。§46 明确禁止的「随机选最相似的一集」正在发生，只是形式是「按字母序选第一集」。

### C-4b　残缺原片被静默索引，并产出高置信错误答案

探针 E8b：faststart MP4（moov 在文件头，所有流媒体下载件的格式）只保留 45% 字节：

```
EP01: ffprobe duration=60.00s -> DB 记录 60.00s | 实际索引 54 帧 = 27.0s 内容 (45% 覆盖)
```

`indexer.py:65-78` 从不校验 `len(timestamps)` 与 `duration × coarse_fps` 是否一致。索引、数据库、日志、audit JSON 中**没有任何一处记录这个索引是残缺的**。

后果不止于漏检：被诉片段的真实来源（42 秒处）根本不在索引里，系统仍然输出

```
accused[0.00, 6.00] -> EP01 [18.00, 24.00]  confidence=0.9999   → GREEN
```

**一个不完整的取证素材库会安静地产出满分的错误结论。** 对比之下，硬截断（moov 缺失）会被 ffprobe 正确报错——问题恰恰出在最常见的那种残缺形态上。

---

# Major

> 进入 Phase 2 前应该解决的问题。

## M-1　Phase 1 gate 的样本量不构成证据

| 项 | 实际值 |
|---|---|
| gate 内视频数 | **2**（CASE_A、CASE_I） |
| gate 内 ground truth 区间数 | **3** |
| gate 内**唯一**「集数 + 区间」事实数 | **2**（EP01@11.5 出现两次、EP02@3.5 一次） |
| 原片语料 | 3 段 lavfi 合成图案，共 **72 秒** |
| A–J 全部 10 个 case 覆盖的唯一源区间数 | **2**（9/10 个 case 使用同一段 EP01 11.5–16.5） |
| 覆盖的变换类型（gate 内） | 1 种（直接剪切 + 拼接） |

`3/3 = 100%` 的 Wilson 95% 置信下界约为 44%。以此宣称达到 §16 的「Segment Recall ≥ 95%」在统计上没有意义。

更关键的是语料性质：`create_demo_originals`（`benchmarking.py:77-81`）使用 `testsrc2` / `testsrc` / `testsrc2+negate` 三种图案。它们**每一帧全局唯一、三集之间最大互异**（EP03 是 EP01 的 negate，DCT 描述子余弦相似度为 −1）。这恰好规避了真实电视剧最核心的难点：帧间近似重复、跨集共享片头片尾、同一场景反复出现、大量低信息量暗场。`visual_score = 1.0` 正是这种「对抗性简单」语料的直接产物。

## M-2　`boundary_mae_seconds = 0.00s` 是网格重合，不是精度

`benchmarking.py:110-111`

```python
first_start  = round(randomizer.uniform(3.0, dur - 8.0) * 2) / 2     # ← 吸附到 0.5 秒
second_start = round(randomizer.uniform(3.0, dur - 8.0) * 2) / 2
```

`0.5` 秒恰好等于 `1 / coarse_fps`（`config.py:12`），也恰好等于 `media.py:100` 中 `yield frame_index / fps` 生成的时间戳网格。片段时长同样全部取 5.0 / 4.0 / 3.5 秒（均为 0.5 的整数倍），24 fps 源的 0.5 秒也恰好是 12 帧的整数边界。

**三级网格全部对齐 → 误差恒为 0。这不是 leakage（生成器不读 matcher 内部状态），但它是一个让测试与实现按构造相符的共享常数。**

**探针 E2 实测（唯一改动：切点不吸附网格）：**

```
 gt_start  gt_dur | pred_src_start err_start | pred_src_end  err_end
    11.50    5.00 |          11.50      0.00 |        16.50     0.00   ← 生成器的写法
    11.60    5.00 |          11.50      0.10 |        16.50     0.10
    11.72    5.13 |  NO MATCH
    12.03    4.87 |          12.00      0.03 |        17.00     0.10
    13.37    6.41 |          13.50      0.13 |        20.00     0.22
    14.24    5.55 |  NO MATCH
    15.91    4.09 |          28.00     12.09 |        32.00    12.00
    17.13    7.77 |          21.50      4.37 |        25.50     0.60
    18.48    3.62 |          18.50      0.02 |        22.00     0.10

boundary MAE = 2.133s        最大单点误差 = 12.090s        漏检 2/8
```

真实案件中被诉视频的剪辑点是任意的，不会落在 0.5 秒网格上。**2 FPS 的系统性误差量级是 ±0.25 秒（内容-时间戳错配）+ 0.5 秒（区间端点量化），报告值 0.00 秒不代表任何真实精度。** 现在也确实没有 §9 要求的 8 FPS 候选区间精对齐。

## M-3　性能数字的计时范围诚实，外推不诚实

**`index_seconds_per_hour_video = 13.55`**

计时范围（`indexer.py:54-90`）**是完整的**：ffprobe + 整文件 SHA-256 + ffmpeg 解码/缩放/灰度 + DCT 编码 + `savez_compressed` + SQLite 写入。这一点值得肯定。

问题在分母：测量样本是 **3 × 24 秒 = 72 秒（0.02 小时）的 640×360 lavfi 合成视频**，然后乘以 180 外推到「每小时」。

```
合成 EP01：2,044,352 字节 / 24 秒 = 5.11 MB/分钟
真实 1080p 剧集：约 4000 kbps ≈ 30 MB/分钟
```

SHA-256 与容器读取成本 ×6；解码像素量 1920×1080 vs 640×360 = ×9；真实 H.264 的 B 帧 / CABAC 解码复杂度远高于合成图案。**保守估计真实值是报告值的 30–100 倍，即每小时原片 7–20 分钟；40 集 × 45 分钟（30 小时）的冷索引约 3.5–10 小时。** 这是一个需要向用户预告的量级，不是 13.55 秒。

另外 `benchmarking.py:275` 的分母 `index_video_hours` 取自 `list_original_indexes()` 的**全部**行（数据库里所有历史剧集），而分子 `index_elapsed` 只包含本次运行的 `IndexingResult`。在 demo 场景两者一致，但在真实项目中只重建一集时，这个比值会严重低估。

**`analysis_seconds_per_minute_accused_video = 0.6435`**

计时范围同样完整（`analyze_file` 全程），但它是在**索引只有 144 帧**的条件下测得的。检索复杂度是 `O(query_frames × total_source_frames)`：

| 场景 | 源帧数 | 相似度矩阵 | numpy 时间 |
|---|---|---|---|
| 现 benchmark（3×24s / 8s 被诉） | 144 | 0.0 MB | ~0.00 s |
| 40 集 × 45 分钟 / 10 分钟被诉 | 216,000 | 25.9 MB | 1.76 s |
| 40 集 × 45 分钟 / 25 分钟被诉 | 216,000 | 64.8 MB | 4.45 s |

`0.64 s/min` 完全不可外推——它测的是一个几乎为空的索引。

**`peak_memory_bytes = 46,465,024`（46.5 MB）**

`benchmarking.py:253-255` 读取 `resource.getrusage(RUSAGE_SELF).ru_maxrss`。**`RUSAGE_SELF` 结构上不包含子进程，而 ffmpeg 是子进程，且是本流水线最大的内存消耗者。** 实测：

```
跑一遍 1080p ffmpeg 后
  ru_maxrss RUSAGE_SELF     : 117.9 MB → 117.9 MB   (delta 0.0 MB)
  ru_maxrss RUSAGE_CHILDREN : 445.3 MB              ← benchmarking.py 从不读取
```

同时 Python 侧本身也被低估。`matching.py:37-58` 的中间候选列表在裁剪到 top_k 之前，会同时持有 `query_frames × n_episodes × top_k` 个 `CandidateMatch`：

| 场景 | 中间对象数 | 堆开销（实测 160 B/个） |
|---|---|---|
| 现 benchmark | 360 | 0 MB |
| 40 集 / 25 分钟被诉 | **960,000** | **154 MB** |

加上相似度矩阵峰值 194 MB 与 ffmpeg 的数百 MB，**真实峰值在 400 MB – 1 GB 量级，报告值低 10–20 倍**。§50 要求记录 `peak_memory`，当前记录的是一个不含主要消耗者的数字。

## M-4　`benchmark/results/phase1_baseline.json` 是手工编写的，不可由代码复现

它是**唯一被 git 跟踪**的 Phase 1 gate 证据（`output/reports/*` 全部被 `.gitignore` 排除）。但它的 schema 与 `BenchmarkRunner.run()` 的输出不一致：

| | 顶层键 | case 键 |
|---|---|---|
| 手工文件 | + `environment`、`warning`，− `created_at` | `predicted` |
| 代码输出 | `created_at` | `predictions`、`metrics`、`analysis_seconds`、`video_duration` |

`environment` / `warning` / `predicted` 这三个键在 `benchmarking.py` 中一次都没有出现。聚合层的 `ground_truth_segments` / `recalled_segments` 也不是 runner 的聚合字段。

数值本身与代码输出一致（我在干净根目录复现：recall 1.0 / MAE 0.0 / 13.40 s per hour / 46.3 MB），所以**不存在伪造**，但对一个以「可追溯」为核心卖点的系统而言，**gate 证据应当是机器产物而非人工转写**。

## M-5　`mirrored` / `cropped` 被当作已确认事实写入审计记录

`models.py:47-48`

```python
mirrored: bool = False
cropped:  bool = False
```

全代码库中没有任何地方给这两个字段赋非默认值。但它们被写入 `segment_matches` 表（`database.py:222`）和 audit JSON（`audit.py:28`），形式是**肯定性事实陈述**：

```json
"mirrored": false, "cropped": false
```

诊断报告里 CASE_E（真值为中心裁剪）的预测就带着 `"cropped": false` ——这是一条**写入审计记录的错误事实**。

「未评估」与「评估结果为否」在律所证据链中是两件不同的事。Phase 2 才实现检测是正确的排期，但当前应输出 `null` / `"NOT_EVALUATED"`，而不是 `false`。

## M-6　重叠且互相矛盾的 segment 被同时输出

探针 E9a（单个被诉视频）：

```
accused[0.5, 4.5] -> EP01[21.5, 25.5]  conf=0.9119
accused[4.5, 8.0] -> EP01[21.5, 25.0]  conf=0.9049
```

两个不同的被诉区间被映射到**几乎相同的原片区间**——物理上不可能同时成立。`_merge_adjacent`（`matching.py:196-200`）的合并条件是 `|seg.source_start − prev.source_end| ≤ 1.5`，此处 `|21.5 − 25.5| = 4.0`，于是两条矛盾结论并列输出。

对 §31「引用比例」的直接影响：按 segment 时长求和会**重复计算同一段原片秒数**，`usage_ratio` 被高估。§34 的 QA validator 尚未实现，无法拦截。

## M-7　`original_assets.episode` 全局 UNIQUE，跨剧集会互相覆盖

`database.py:18`

```sql
episode TEXT NOT NULL UNIQUE
```

集数由文件名解析（`indexer.py:29-33`），`projects` 表存在但**没有任何表引用它**。后果：

- 甲剧的 `EP01.mp4` 与乙剧的 `EP01.mp4` 映射到同一个 `episode = "EP01"`；
- `upsert_original` 的 `ON CONFLICT(episode) DO UPDATE` 会**静默覆盖**前一部剧的资产记录，并 `DELETE FROM frame_features`；
- 但两者的 `.npz` 索引文件都留在 `workspace/indexes/`（按 SHA-256 命名），成为孤儿。

律所同时办多部剧的案件是常态。当前 schema 需要为每部剧单独建一个项目根目录才安全，而 §35 的 `projects` 表本来就是为此设计的。

## M-8　audit JSON 不足以复现结论（§40 / §41 未满足）

`audit.py:15-35` 记录了被诉文件 hash、媒体元数据、匹配区间、程序版本。**但没有记录：**

- **原片索引的 SHA-256 / 集数清单**——事后无法证明当时比对的是哪一版 EP07；
- **matching config 快照**（`coarse_fps`、`top_k`、`minimum_visual_similarity`、`offset_bin_seconds`、`minimum_segment_seconds`）——阈值是「为什么这条匹配被接受」的一半答案；
- **被拒绝的竞争候选**——§38 要求 review 时能看到「为什么系统不确定」，当前没有任何竞争者信息落盘。

§41 要求「所有原始视频 SHA-256」，当前只有被诉侧。另外 `audit.py:24` 对被诉文件**重复计算了一次 SHA-256**（`analyze_file` 已算过一次），既浪费 I/O，又使 DB 与 audit 中的 hash 来自两个不同时刻。

---

# Minor

> 可以后续处理的问题。

- **M-9　报告不是合法 JSON。** `math.inf` 经 `json.dumps` 序列化为裸 `Infinity`（`benchmarking.py:224/285`，写出于 `cli.py:170`）。Python 能读，但 RFC 8259 严格模式与 JavaScript `JSON.parse` 均拒绝（实测 `output/reports/benchmark_all_diagnostic.json` 在两者下均报错）。用 `null` 或 `"NOT_RECALLED"` 替代。
- **M-10　`frame_features` 只写不读。** `database.py:35-39/149-167` 每帧写一行，全代码库无任何读取（已 grep 确认）。40 集 × 45 分钟 = **21.6 万行**纯冗余，内容完全重复 `.npz` 的 `timestamps` 数组。属于 §0.5 意义上的过早抽象。
- **M-11　`config/default.yaml` 的 `confidence:` 段从不被加载。** `config.py:74-78` 只读 `matching` / `paths` / `project`，`green: 0.95` / `yellow: 0.75`（§37 分档）被静默丢弃。`matching` 下的错误键会抛 `TypeError`，但整个顶层未知段落被无声忽略——用户改错位置得不到任何提示。
- **M-12　时间戳由计数器合成，不取真实 PTS。** `media.py:100` `yield frame_index / fps`。对起始 PTS 非零、含 edit list、或 VFR 的素材（B 站 / 小红书下载件常见）会整体偏移。`fps` 滤镜本身也带来 ±1/(2·fps) = ±0.25 秒的内容-时间戳错配。
- **M-13　`extract_grayscale_frames` 的 stderr 管道有死锁风险。** `media.py:87` 用 `subprocess.PIPE` 接 stderr，但在 stdout 读完之前从不读取它。解码错误密集的文件（正是最需要诊断的那类）写满 64 KB 管道缓冲后会挂死。改用 `stderr=subprocess.DEVNULL` + 单独线程，或写临时文件。
- **M-14　生成器提前 return 会静默吞掉 ffmpeg 失败。** 同一函数是 generator，`process.wait()` 与返回码检查在末尾（`media.py:103-105`）。调用方若提前 `break`，返回码永不检查，子进程泄漏。当前调用方都用 `list(...)` 全量消费，所以尚未触发。
- **M-15　`_ratio` 不会回退到 `r_frame_rate`。** `media.py:65` 的 `video.get("avg_frame_rate") or video.get("r_frame_rate")`——`"0/0"` 是真值，于是返回 0.0 而不回退。VFR 素材的 fps 会记为 0。
- **M-16　索引缓存键不含 encoder 名。** `indexer.py:59` 用 `{sha256}-{algorithm_version}.npz`，不含 `encoder.name`。Phase 2 切换 DCT → DINOv2 时若忘记同步改 `algorithm_version`，会静默复用旧特征。
- **M-17　`probe_media` 的 duration 静默回退到 0。** `media.py:59` `... or 0`。duration=0 会让 `index_seconds_per_hour_video` 的保护分支返回 0.0，是一个安静的错误值而非报错。
- **M-18　`--all-diagnostic` 默认写入 `benchmark_phase1.json`。** `cli.py:168` 的默认报告路径不区分 scope，未显式传 `--report` 会用诊断结果覆盖 gate 结果。
- **M-19　聚合 MAE 按 case 平均而非按边界平均。** `benchmarking.py:285`。CASE_I / CASE_J 各含 2 段（4 个边界），与单段 case 权重相同。
- **M-20　CASE_G 的「字幕」是纯黑色矩形，不是文字。** `benchmarking.py:136` 用 `drawbox`。本机 ffmpeg 无 `drawtext`（缺 freetype），所以这是可理解的规避，但它测的是「遮挡一块区域」而非「叠加高频文字纹理」——后者对 DCT 低频描述子的影响完全不同。

---

# Benchmark Audit

## 逐项：这些数字代表什么，不代表什么

### `ground_truth_segments: 3` / `recalled_segments: 3`

| | |
|---|---|
| **可信** | ✅ 是。可在干净根目录完整复现。 |
| **代表** | CASE_A（1 段）+ CASE_I（2 段）全部被召回。 |
| **不代表** | 任何统计意义上的准确率。3 个样本、**2 个唯一「集数+区间」事实**（EP01@11.5 重复出现两次）、1 种变换类型、72 秒合成语料。`3/3` 的 Wilson 95% 下界 ≈ 44%。 |

### `segment_recall: 1.0`

| | |
|---|---|
| **可信** | ⚠️ 数值为真，**定义偏弱**。 |
| **代表** | 「预测的集数正确 **且** 被诉侧区间重叠 ≥ 50%」。 |
| **不代表** | 原片时间轴正确（C-2b：错 30 秒仍算召回成功）；也不代表没有额外的虚假区间（C-2c）。 |

### `episode_accuracy: 1.0`

| | |
|---|---|
| **可信** | ❌ **不是独立指标。** |
| **代表** | 与 `segment_recall` 完全相同的数字（C-2a，代码上同一表达式）。 |
| **不代表** | 集数识别能力。集数正确性从未被单独度量；错集数只表现为 recall 下降，永远不会表现为 `episode_accuracy` 下降。**从报告中删除此字段，或重新定义为「在已召回的 segment 中集数正确的比例」。** |

### `boundary_mae_seconds: 0.00`

| | |
|---|---|
| **可信** | ❌ **是网格重合的产物。** |
| **代表** | 「当被诉片段的切点恰好落在 0.5 秒抽帧网格上时，误差为零」。 |
| **不代表** | 时间精度。同一算法在 off-grid 切点下 **MAE = 2.13 秒，最大误差 12.09 秒，漏检率 2/8**（探针 E2）。真实系统性下限是 ±0.25 s（内容-时间戳错配）+ 0.5 s（端点量化）。§16 的 ≤1 秒目标**当前未验证**。 |

### 诊断 `58.3%` 的失败分布

`7/12` 个 ground truth 区间被召回。逐 case：

| Case | 变换 | 结果 | 真实原因 |
|---|---|---|---|
| A | 直接剪切 | ✅ 1/1，MAE 0.00 | 网格对齐 |
| B | 水平镜像 | ❌ 0/1，无输出 | 未实现（Phase 2，符合预期） |
| C | 1.25× | ❌ 0/1，无输出 | **C-3 结构性上限 1.19×**，非特征质量问题 |
| D | 0.8× | ❌ 0/1，**但输出了一条 conf 0.9422 的预测** | 同 C-3。输出 `speed_ratio: 1.0`（真值 0.8），仅覆盖 6.25 秒中的 3.0 秒。**这是一条假阳性，指标层只记为「未召回」**（C-2c） |
| E | 中心裁剪 | ✅ 1/1，**但 MAE 1.0 秒** | 召回判据宽松（重叠 ≥50% 即可）；`cropped: false` 是错误事实（M-5） |
| F | 竖屏裁剪 | ❌ 0/1，无输出 | 未实现（Phase 2） |
| G | 字幕遮挡 | ✅ 1/1，MAE 0.00 | 黑色矩形对低频 DCT 影响小（M-20） |
| H | 镜像+变速+字幕 | ❌ 0/1，无输出 | 镜像 + C-3 双重原因 |
| I | 多集拼接 | ✅ 2/2，MAE 0.00 | **piecewise 真实工作**（见下） |
| J | 插入无关画面 | ✅ 2/2，MAE 0.00 | **真实工作**，无关片段未被误配 |

**分布解读**：5 个失败中，B / F / H 属于「Phase 2 未实现」（合理）；**C / D 属于「Phase 1 对齐结构缺陷」（不合理，换 backend 不解决）**。D 还额外贡献了一条 0.94 置信度的假阳性。

### 当前 benchmark 是否足以作为 Phase 1 gate

**不足以。** 三个层面的问题：

1. **样本量**：3 段 / 2 个唯一事实，不支持任何 ≥95% 的声明；
2. **指标定义**：无 precision、episode accuracy 退化、recall 不校验原片时间、MAE 丢弃失败样本、speed_ratio 不校验；
3. **语料性质**：`testsrc` 系列每帧全局唯一、跨集最大互异，恰好规避了真实剧集的核心难点（重复片头 / 前情提要 / 静态镜头 / 暗场）。

### 还需要补哪些测试，才能避免「代码通过自己设计的测试」

按优先级：

**P0——必须在 Phase 2 之前补齐**

1. **Off-grid ground truth**：切点从 `round(x*2)/2` 改为任意浮点（如 `uniform` 后保留 3 位小数），时长同样任意。这一项改动即可暴露 M-2 全部问题。
2. **负样本 case（CASE_K）**：完全不含权利作品的被诉视频，断言 `MATCH_NOT_FOUND`。当前 A–J 中不存在。
3. **假阳性指标**：新增 `false_positive_seconds`（预测区间中未与任何真值重叠的秒数）与 `precision`。
4. **原片侧时间校验进入召回判据**：`recall@1s` = 集数正确 **且** 被诉侧重叠 ≥50% **且** `|source_start − gt_source_start| ≤ 1s`。当前的 recall 应改名为 `episode_recall` 以免误导。
5. **重复内容 case（CASE_L）**：至少 2 集共享一段逐帧相同的片头/前情提要，取自共享区的片段必须被标记为歧义，不得输出满分单一集数。这是 C-4a 的回归测试。
6. **残缺索引 case（CASE_M）**：faststart MP4 截断至 45%，断言索引阶段报错或标记 `INCOMPLETE_INDEX`。这是 C-4b 的回归测试。
7. **置信度校准曲线**：把所有预测按 confidence 分箱，画出每箱的实际正确率。当前 GREEN 箱内含 12 秒误差的样本，说明校准完全失效。

**P1——Phase 2 期间补齐**

8. `speed_ratio` 纳入 `evaluate_segments`（误差 ≤ 5%）。
9. 真实剧集语料（哪怕 2 集 × 45 分钟公开素材）替代 `testsrc`，专门覆盖片头 / 片尾 / 暗场 / 静态对话镜头。
10. 规模测试：≥10 集 × 45 分钟，测真实的 index 时间 / 检索时间 / 峰值内存（用 `RUSAGE_SELF + RUSAGE_CHILDREN`）。
11. 长被诉视频（≥10 分钟、含 ≥5 段来自 ≥3 集），验证 piecewise 在真实密度下的表现。
12. 重叠 segment 一致性断言（M-6）。
13. §49 的 5–10 条真实人工 golden set。**synthetic 永远不能替代它。**

**单元测试层面的缺口**

现有 10 个测试中，`test_alignment.py` 喂给 aligner 的是**间隔恰好 0.5 秒、offset 恒定、无噪声**的手工候选——正是 aligner 被设计来处理的输入形态。它从未测试：非 1.0 斜率、含离群点的 RANSAC 拒绝、`_merge_adjacent` 路径、frame-claiming 重叠逻辑、`CandidateRetriever`（完全无单测）、audit JSON、缓存失效、`MATCH_NOT_FOUND` 路径。`test_temporal_alignment_maps_episode_and_timeline` 里的干扰项 `visual_score=0.2` 在任何逻辑运行前就被 `minimum_visual_similarity=0.82` 滤掉了——那是阈值测试，不是假阳性测试。

---

# ADR-0001 Review

## 对 Codex 方案的评价

ADR-0001 的**判断是正确的，但问题定义找错了地方**。

**正确的部分：**

- 在 Phase 1 不引入 SigLIP/DINOv2 + FAISS 是对的。§48 把 Phase 1 定义为「打通 originals → 集数 + 时间轴」，先用低成本确定性描述子验证数据流，符合 §51「正确性 > 可追溯性 > 稳定性 > 速度」。
- `ImageEncoder` Protocol（`features.py:10-13`）抽象干净，`encode_images(images) -> ndarray[N, D]` 与 §8 Layer 2 要求的接口一致，替换 encoder 不触及 `matching.py` 与 `indexer.py`。
- 索引缓存按 `algorithm_version` 分文件、`segment_matches` 保存 `algorithm_version`，数据兼容分析准确。
- 「未经架构负责人确认，不在 Phase 1 引入任何一个」——纪律正确。

**缺失的部分（这是我不同意的地方）：**

ADR-0001 通篇把 A–J 的失败归因于**特征表达能力**，因此把决策框定为「选哪个 embedding」。但实测表明：

> **CASE_C / CASE_D / CASE_H 的失败与视觉特征无关。** 在完全无噪声、余弦相似度精确为 1.0 的理想候选下，`TemporalAligner` 对 1.25× 和 0.8× 仍然输出零个 segment（探针 E4/E4b）。瓶颈是 `matching.py:85-87` 的常数 offset 分桶结构，速度容忍上限被锁死在 `offset_bin / min_segment = ±0.167`。

也就是说：**即使明天换上 DINOv2，CASE_C / D / H 依然全败。** ADR-0001 把一个时序对齐的结构问题，误诊为一个特征表达的问题。

同样，C-4a（跨集重复内容 → 满分错集数）也不是 backend 能解决的——更强的特征只会让三集的片头**更加**相似，从而**加剧**这个问题。它需要的是检索层的唯一性度量与置信度重构。

## 我的推荐

### 1. DINOv2 vs SigLIP：**DINOv2**，且这个决定应当推迟

**推荐 DINOv2**，理由与 ADR 的初步倾向一致但更明确：

- 本任务是**实例级检索**（找同一个镜头的同一帧），不是语义检索（找「相似的场景」）。DINOv2 的自监督目标函数直接优化 patch 级视觉对应，其 `[CLS]` + patch token 在 copy-detection / instance-retrieval 基准上是当前的强基线；
- SigLIP 是图文对比学习，其表示刻意抹平了实例差异以对齐文本语义。**这恰好是本项目最危险的失效模式**：同一部剧的两个不同场景（同一套景、同一组演员、同一种光线）在 SigLIP 空间里会非常接近，直接放大 C-4a 的集数混淆；
- DINOv2 的 patch token 可直接复用于 §14 的 crop/homography 空间验证，SigLIP 无此结构；
- Apple Silicon 本地推理成熟度：DINOv2 ViT-S/14（21M 参数）在 M3 上可实用；SigLIP 需要更大模型才达到可比的实例区分度。

**但这个决定应当推迟到两件事之后：**

1. C-3 的对齐结构修好（否则无法区分「特征不够好」和「对齐拦住了」）；
2. §49 的 5–10 条真实 golden set 建立（否则在 `testsrc` 上比较两个 backend 毫无意义）。

**如果现在就必须定，选 DINOv2 ViT-S/14。** 建议同时保留一个 evaluation harness，使换 backend 只需改 `MatchingConfig.encoder` 一个字段。

### 2. 是否现在就需要 FAISS：**不需要，但需要一个检索接口抽象**

实测（探针 E5）：40 集 × 45 分钟（21.6 万帧 × 144 维）对 25 分钟被诉视频的**纯 numpy 精确检索约 4.45 秒**。DCT 的 144 维下，精确检索完全够用。

真正的成本不在矩阵乘法，而在 `matching.py:37-58` 的 **96 万个 `CandidateMatch` Python 对象**（154 MB，构造开销远超 numpy 部分）。**先把这个循环向量化，收益比引入 FAISS 大得多，且不增加依赖。**

FAISS 变成必需是在换到 DINOv2 之后：768 维（ViT-B）会让相似度矩阵从 64.8 MB 涨到 345 MB，索引本身从 124 MB 涨到 663 MB。届时需要 IVF-PQ 或 HNSW。

**建议**：现在只引入一个 `VectorIndex` Protocol（`add(vectors, meta)` / `search(query, k) -> (scores, ids)`），用 numpy 实现；FAISS 在 Phase 2 换 backend 时作为第二个实现接入。这是接口投资，不是依赖投资。

### 3. DCT 是否保留为 fast path：**保留，但要重新定位**

保留，理由：

- §8 Layer 1 明确要求 perceptual hash 层，DCT 描述子正是这一层的合理实现；
- 它是**确定性的**——无模型权重、无版本漂移、无随机性。对律所审计场景，一个可以在任何机器上逐位复现的特征有独立价值（§4.2）；
- 它可以作为 DINOv2 结果的**独立交叉验证**：两个原理完全不同的特征同时给出同一集数，是比单一高维特征更强的证据；
- 成本极低（144 维，索引仅 124 MB / 30 小时视频）。

但要**重新定位**：当前它是唯一的匹配依据，应改为「Layer 1 快速召回 + 交叉验证」，主判据交给 Layer 2。同时把 `algorithm_version` 从 `phase1-dct-v1` 改为能同时表达两层的复合版本号。

### 4. 属于架构变更、需要 ADR 的事项

以下**不应直接实现**，需要架构负责人裁定：

| 事项 | 为什么是架构变更 |
|---|---|
| **ADR-0002 时序对齐策略** | C-3 要求替换 `_proposals` 的常数 offset 分桶。候选方案：(a) 二维 Hough（offset × slope）；(b) 直接对全体候选跑 RANSAC 后再分段；(c) DTW / 最长递增子序列。这改变 §11 的核心算法，且影响 `speed_ratio` 语义。**必须先立 ADR。** |
| **ADR-0003 置信度语义与歧义状态** | C-1/C-4a 要求在 confidence 中引入唯一性项，并新增 `AMBIGUOUS` / `INCOMPLETE_INDEX` 状态。这改变 §37 分档与 §38 review queue 的触发条件，影响后续所有模块。 |
| **ADR-0004 项目/剧集作用域** | M-7 要求给 `original_assets` 加 `project_id` 并去掉 `episode` 的全局 UNIQUE。这是 §35 schema 变更，影响数据兼容。 |
| **ADR-0005 benchmark 指标定义** | C-2 的全部修复（新召回判据、precision、off-grid、negative case）会让历史数字不可比。需要架构负责人确认「§16 的 P1 目标按新定义重新解释」。 |
| **§16 与 §48 的范围冲突裁定** | 见下方 Questions Q1。这是文档级冲突，只有架构负责人能裁。 |

**不需要 ADR、可直接修的**：M-9 ~ M-20 全部（JSON 合法性、死配置、死表、stderr 死锁、fps 回退、缓存键、报告路径等），以及 M-5（`mirrored`/`cropped` 改为 `null`）。

---

# Phase 2 Recommendation

> 仅为建议，不含任何代码修改。

## 阶段划分建议

**Phase 1.5（新增，进入 Phase 2 之前）——「让尺子可信」**

不加任何新能力，只做四件事：

1. 按 P0 清单重建 benchmark（off-grid、负样本、precision、原片侧时间校验、重复内容 case、残缺索引 case、置信度校准曲线）；
2. 置信度重构：加入唯一性 margin 项 + 索引完整性校验，歧义时返回 `AMBIGUOUS` 而非满分单值；
3. 在**新的**指标下重跑 A–J，得到一组诚实的基线数字（预期会显著低于当前，这是好事）；
4. 建立 §49 的 5–10 条真实 golden set。

**理由**：如果先做 Phase 2，所有调优都会朝一个看不见假阳性、看不见错集数、看不见错时间轴的目标函数收敛。**修尺子必须先于修零件。**

## Phase 2 能力加入顺序

推荐顺序与理由（与「先易后难」不同，是「先解锁后续、后处理孤立」）：

**R-1　时序对齐重构（最优先，且必须第一）**
换 backend 之前必须完成。当前 ±0.167 的速度容忍上限会让所有变速类评测无法区分「特征不行」与「对齐拦住了」。推荐二维 Hough（offset × slope 联合投票）后接分段 RANSAC——它同时解决变速与 piecewise，且天然给出 `speed_ratio`。

**R-2　变速（1.25× / 0.8× / 0.75–1.5×）**
R-1 完成后基本自动解决，无需新特征。这是投入产出比最高的一项，也是当前诊断中最大的一块失分。

**R-3　镜像**
按 §13 在**候选验证阶段**做二次比对，不建两套索引。对 DCT 描述子而言，水平翻转在频域有解析关系（奇数列系数变号），可以零成本得到镜像描述子——这是保留 DCT 层的一个额外理由。对 DINOv2 则需要实际推理翻转图。

**R-4　8 FPS 候选区间精对齐**
§9 已要求但未实现。这是把 MAE 从 ~2 秒降到 <0.5 秒的直接手段，且只对已确定的候选区间做，成本可控。**应排在换 backend 之前**——它改善的是「时间精度」，与「召回率」正交，能让后续 backend 比较更干净。

**R-5　视觉 backend 切换（DINOv2）+ VectorIndex 抽象**
在 R-1~R-4 之后。此时 benchmark 可信、对齐不再是瓶颈，才能真正测出 backend 的边际收益。

**R-6　crop / resize / 竖屏**
§14 的空间验证。依赖 R-5 的 patch token（DINOv2 提供，DCT 不提供）。这也是为什么 crop 排在 backend 之后。

**R-7　字幕 / 遮挡鲁棒性**
真实字幕是高频文字纹理，与当前 benchmark 的黑色矩形性质完全不同。需要先有真实语料（P1-9）才能评测。DINOv2 对局部遮挡的鲁棒性显著优于全局 DCT。

**R-8　拼接 / piecewise 加密度**
当前 CASE_I/J 已证明基本机制可用，但只测了 2 段。真实混剪可能有 20+ 段、单段 3–5 秒。需要在 R-1 的新对齐器上重新验证，并加入重叠一致性约束（M-6）。

**R-9　audio 指纹（最后）**
§8 Layer 3。放最后的理由：(a) 它是**交叉验证**手段，需要先有一个值得验证的视觉结论；(b) 它对「原声保留但画面重度改造」的场景价值最大，而那是 crop/遮挡都处理完之后才凸显的残余失败；(c) 它引入新的依赖与新的失败模式，不应与视觉层的重构并行。

**明确不建议现在做**：FAISS（见上）、ASR、任何 Gemma 相关、Excel。

## 一句话总结

> **Phase 2 的第一行代码不应该是 `import torch`，而应该是 benchmark 里的一个负样本。**

---

# Questions for Architecture Owner

> 需要架构负责人决定，执行 Agent 不应自行裁定。

**Q1（最高优先级）　§16 与 §48 的范围冲突如何裁定？**

§16 标题为「**P1** 最低验收目标」，要求在「重编码 / **镜像** / **0.75–1.5× 速度** / 常规字幕 / **常规裁剪**」条件下达到 Recall ≥95%、Episode Accuracy ≥98%、MAE ≤1s。
§48 则把「镜像 / 变速 / crop / 拼接 / subtitles / audio」全部划入 **Phase 2**。

两者对「Phase 1 gate 包含什么」给出了相反的答案。Codex 自行采用了最宽松的解释（gate = CASE_A + CASE_I），并在 README「需要架构负责人决策」第 2 条中如实标注。**在裁定之前，「Phase 1 是否通过」这个问题字面上无法回答。**

请明确二选一：
- (a) §16 是 **Phase 2 结束**时的验收目标，Phase 1 gate 仅覆盖直接剪切 + 拼接 → 则需要为 Phase 1 单独写一组门槛（我建议：off-grid 直接剪切 recall ≥95%、`recall@1s` ≥90%、precision ≥95%、负样本假阳性率 0）；
- (b) §16 确实是 Phase 1 门槛 → 则当前 58.3% 判定为 **FAIL**，须完成 R-1~R-3 后重测。

**Q2　置信度的语义由谁定义，`AMBIGUOUS` 是否新增为一等状态？**

当前 `overall_confidence` 度量的是提案内部自洽度，与正确性无关（C-1）。§37 的 GREEN/YELLOW/RED 分档建立在它之上。
请裁定：(a) confidence 是否必须校准为「正确概率」的估计？(b) 是否在 `MATCH_FOUND` / `MATCH_NOT_FOUND` 之外新增 `AMBIGUOUS`（多集等价命中）与 `INCOMPLETE_INDEX`？(c) 跨集重复内容（片头 / 片尾 / 前情提要）的**期望行为**是什么——报所有候选集？报 `AMBIGUOUS` 并强制人工？还是按某种业务规则（如「优先归属首次出现的那一集」）？**这是业务口径问题，不是技术问题。**

**Q3　多剧集 / 多项目的数据隔离边界在哪里？**

`original_assets.episode` 当前全局 UNIQUE（M-7），甲剧 EP01 会覆盖乙剧 EP01。
请裁定：(a) 一个 workspace 只服务一部剧（靠目录隔离），还是 (b) schema 增加 `project_id` / `series_id` 并放开 UNIQUE 约束？后者是 §35 schema 变更，需要 ADR。同时请确认集数的权威来源：文件名解析（当前）、`metadata.json`、还是人工映射表？非标准命名（`第01集.mp4`、`S01E01.mkv`、`七时吉祥-01.mp4`）的处理规则是什么？

**Q4　真实 golden set 的清单、真值格式与数据保留策略？**

§49 要求 5–10 条人工已知答案的视频，覆盖普通引用 / 镜像 / 加速 / Reaction / 混剪 / 跳播 / 严重裁剪。
需要决定：(a) 具体视频清单由谁提供；(b) 人工真值的记录格式（建议直接复用 `ground_truth.json` 的 schema，便于同一套 `evaluate_segments` 复用）；(c) §54 明确禁止真实案件材料进入 git / test fixtures，那么 golden set 存放在哪、如何在不同机器间复现评测、评测结果能否入库？

**Q5　性能目标是什么？**

真实规模下的诚实估计是：冷索引约 **7–20 分钟/小时原片**（40 集 × 45 分钟 ≈ 3.5–10 小时），峰值内存 **400 MB – 1 GB**（当前报告 46.5 MB 不含 ffmpeg 子进程）。
请给出可接受的上限，因为它直接决定 Phase 2 的 backend 选择：DINOv2 ViT-S/14 在 M3 上对 21.6 万帧的推理是数小时量级，若「一部剧的索引必须在一夜内完成」，则模型尺寸、是否用 Neural Engine、是否降到 1 FPS 建索引都需要一并决定。

**Q6　`mirrored` / `cropped` 未评估时输出什么？**

当前输出 `false`（M-5），在审计记录中构成「已确认未镜像 / 未裁剪」的事实陈述。建议改为 `null` 或 `"NOT_EVALUATED"`，但这会改变 audit JSON 与 `segment_matches` 的 schema 语义。请确认。

**Q7　是否接受新增 Phase 1.5？**

即「不加新能力、只重建 benchmark 与置信度」的一个独立阶段。若不接受，则上述 C1–C4 前置条件需要合并进 Phase 2 的第一个迭代，且 Phase 2 的验收指标必须在开工前就按新定义写死。

---

# 附录：审查方法与可复现性

## 只读探针实验清单

所有实验在 scratchpad 中运行，**未修改任何生产代码**，全部调用未经修改的 `ipvm` 模块。

| 编号 | 目的 | 关键结论 |
|---|---|---|
| E1 | 假阳性（5 类无关合成内容） | ✅ 全部正确返回 `MATCH_NOT_FOUND` |
| E2 | off-grid 切点的真实边界误差 | ❌ MAE 2.13 s，最大 12.09 s，漏检 2/8 |
| E2b | 错误答案的置信度分档 | ❌ 12.09 s 误差 → conf 0.9939 → GREEN |
| E3 | 跨集共享片头的集数归属 | ❌ 任选 EP01，conf 1.0000，无歧义信号 |
| E4/E4b | 速度容忍上限扫描（无噪声） | ❌ 实测 0.81–1.19，要求 0.75–1.50 |
| E5 | 季度规模检索成本 | 21.6 万帧 / 25 分钟被诉 → 4.45 s，194 MB |
| E6 | 内存计量范围 | ❌ `RUSAGE_SELF` delta 0.0 MB vs `CHILDREN` 445 MB |
| E7 | benchmark 指标退化性 | ❌ episode_accuracy ≡ recall；捏造区间零代价 |
| E8/E8b | 残缺原片索引 | ❌ 45% 覆盖无告警，产出 conf 0.9999 错误答案 |
| E9 | 重叠矛盾 segment / 计时范围 | ❌ 同一原片区间被两个被诉区间同时认领 |
| repro | 干净根目录复现 gate | ✅ recall 1.0 / MAE 0.0 / 13.40 s per hour / 46.3 MB |

## 已验证为「真实、无特化」的部分

- 匹配路径（`features` / `matching` / `indexer` / `media` / `database` / `models`）中无任何 benchmark 标识符、case ID、测试路径或 hard-code 分支（全量 grep）；
- ground truth 由 FFmpeg 参数独立生成，不反向依赖 matcher 内部状态。`benchmarking.py` 从 matcher 侧只导入 `episode_from_path`（文件名解析）与 `VideoMatcher`（被测对象本身），不存在真值泄漏；
- `input/` 只读约束成立（无任何写入路径）；
- `.gitignore` 符合 §55，无媒体文件入库；
- `pytest` 10/10 通过，其中 `test_media_integration.py` 确为真实 FFmpeg 端到端（0.26 s）；
- gate 在干净根目录可完整复现，数值与提交值一致。

**唯一的「构造性相符」是 M-2 的 0.5 秒网格**：生成器的 `round(x*2)/2` 与 `1/coarse_fps` 是同一个常数。这不是有意的 leakage，但它使测试与实现按构造相符，是 `MAE 0.00s` 的完整解释。
