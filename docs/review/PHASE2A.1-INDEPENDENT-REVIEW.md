# Phase 2A.1 — Calibration & Safety Closure 独立审查

- 审查对象：`c512a80`（`Complete Phase 2A.1 calibration and safety closure`）
- 上游基线：`3f5f4d5`（`Implement Phase 2A temporal alignment v2`）
- 审查日期：2026-09-03
- 总体裁定：**FAIL**

## 1. 结论先行

`c512a80` 修复了两个窄义问题：`_merge_adjacent()` 不再把已经抽取出的
0.8→1.25 两段直接合并；新的 `_fit_quality()` 也不再用无界的
`match_score × duration` 压掉较短但明显更强的解释。生产
`review_tier` 仍恒为 `REVIEW_REQUIRED`，所以本 commit 没有重新开启 GREEN
自动放行。

但 Phase 2A.1 不能通过 Gate，主要原因有四项：

1. **Critical — v4→v5 migration 是假升级。** 数据库版本会从 4 写成 5，
   但两个 v5 列没有创建；随后持久化任何新结果都会报
   `no column named speed_out_of_range`。
2. **Blocker — 仍有系统性的错误时间线。** 独立重跑复现 4 个
   `UNSAFE_WRONG_MATCH`，全部是同一内容位置的 direct/P1/P2/P3；它们均以
   `MATCH_FOUND` 返回并被标为 candidate GREEN。
3. **Major — rival compatibility 仍只比较 `source_start`。** 它既会漏掉
   “起点接近、末端明显分叉”的不同 temporal line，也会把同一条 line 的不同
   boundary 当成 rival。隔离 synthetic A–M 重跑中，TP/FP/FN 虽仍为
   36/33/44，但 ambiguity 从上游 1/73 上升到 **33/73**。
4. **Major — benchmark safety assertions 不足。** ideal benchmark 仍有 18 个
   非 source-grid 候选，却硬写 `source_grid_realistic=true`；merge-negative
   只检查“输出至少两段”，实际两段均未命中 GT；SAFE ambiguity helper 只检查
   source endpoints，可把 accused timeline 完全错误的 alternative 判为 SAFE。

因此：**Phase 2A 不可标记为 PASS / CLOSED。** Core temporal mechanics 可保留，
但 safety、boundary completeness、benchmark integrity 和数据库升级仍需关闭。

## 2. 审查范围与方法

我完整阅读了：

- Architecture baseline 与当前 revision；
- ADR-0002、ADR-0003、ADR-0005；
- 上一轮 `PHASE2A-TEMPORAL-ALIGNMENT-V2-INDEPENDENT-REVIEW.md`；
- 本 commit completion report；
- `3f5f4d5..c512a80` 的全部 production、test、schema、config diff；
- `PHASE2A.1-DS-AUXILIARY-VERIFICATION.md`。该文件仅作线索；本报告采用的
  migration、grid、real taxonomy 等结论均另行复现。

执行与结果：

| 检查 | 独立结果 |
|---|---:|
| `pytest -q` | **78 passed in 4.08s** |
| fresh ideal benchmark | gate recall/precision 1.0/1.0 |
| real benchmark | 2 episodes × 10 positions × 6 profiles = 120 cases |
| real matcher runtime sum | 32.39 s |
| synthetic A–M | 73 cases；TP/FP/FN = 36/33/44 |
| pure-noise reviewer matrix | 12/12 配置均为 0 segment |
| DB migration | 用 `3f5f4d5` 的原始 SCHEMA 构造真实 v4 fixture 后升级 |

真实媒体、hash、绝对路径和逐帧可识别内容没有写入本报告。原始 reviewer 输出只在
ignored `/tmp` 中。

## 3. Baseline 与 scope 核对

| 项目 | 结论 |
|---|---|
| sparse voting | 主体未改写 |
| robust fitting | 主体未改写；只增加 unclipped slope 留存 |
| hypothesis extraction | 主循环未改写；改了排序、support floor、cap 信号 |
| piecewise architecture | 未改写 |
| visual backend | 仍为 DCT |
| source/query FPS | 仍为 2 FPS |
| retrieval top-k | 仍为 8 |
| GREEN auto-release | 仍禁用；生产结果均 `REVIEW_REQUIRED` |
| DINOv2 / SigLIP / FAISS | 未引入 |
| crop/mirror/subtitle/PiP 新能力 | 未引入 |
| 真实媒体 blob | 未进 Git |
| 两集真实媒体 hash / 绝对路径 | 在 `c512a80` tree 中均为 0 命中 |
| `real_test/` | 被 `.gitignore` 排除 |
| `.DS_Store` | **仍在 repository tree**；由初始 commit `71e8d93` 引入，非本 commit 新增 |

工作树审查开始时只有用户既有的 `.DS_Store` 修改；后续出现的 auxiliary report
也是用户提供的未跟踪文件。本审查未修改 production code，未 commit。

## 4. Finding 1 — v4→v5 migration 失效（Critical）

`SCHEMA_VERSION` 从 4 升到 5，但 `initialize()` 只有在
`fitted_offset_seconds` 不存在时才调用 `_migrate_v3_to_v4()`。真实 v4 数据库已经
有该列，因此不会执行新增 `speed_out_of_range` 与
`unconstrained_speed_ratio` 的 ALTER；之后代码仍无条件写入
`PRAGMA user_version = 5`。

关键位置：

- `src/ipvm/database.py:184-210`：读取旧版本、按旧列判断、无条件写 v5；
- `src/ipvm/database.py:362-381`：两个 v5 列被塞进名为 v3→v4 的迁移函数；
- `src/ipvm/database.py:560-572`：新写入语句强制引用两个 v5 列。

独立 fixture 结果：

| 项目 | 结果 |
|---|---|
| 升级前 | user_version=4；35 列；旧 row 1 条 |
| `Database.initialize()` 后 | user_version=5；仍为原 35 列 |
| 两个 v5 列 | 均不存在 |
| 旧 row | 保留 |
| 写入新 SegmentMatch | `OperationalError: ... no column named speed_out_of_range` |
| migration 前 backup | 有，但后缀仍叫 `.pre-v4.bak` |
| `before_commit` 注入失败 | rollback 正常；version 保持 4，row 保留 |

原子事务本身仍有效；问题是**迁移内容根本没有执行**。Fresh v5 DB 对照组可以正常
保存并读回：

```text
speed_ratio = null
speed_out_of_range = 1
unconstrained_speed_ratio = 1.6
review_tier = REVIEW_REQUIRED
```

同一对象经 `SegmentMatch.to_dict()` 和 audit JSON 也能保留上述字段。因此 fresh DB
round-trip 为 PASS，direct-parent v4 upgrade 为 FAIL。现有测试只有 legacy、v3→v4
rollback 和 fresh schema，没有 v4→v5 success/failure 测试；也没有
`SCHEMA-V5.md`。

## 5. Merge safety 独立复核

### 5.1 Reviewer-owned A–G

2 FPS 下一个 source-grid step 为 0.5 秒。

| Case | 观察 | 结论 |
|---|---|---|
| A：同一 line，中间 retrieval hole | 合为 1 段；0–12.5 s；a=1.25；23/23 inliers | 应合并，PASS |
| B：0.8→1.25，source 连续 | endpoint divergence=5.40 s；保留 2 段 | 不合并，PASS |
| C：1.00→1.05，短 union | divergence=0.15 s；合为 1 段 | 小于 0.5 s 网格不确定性，合理 |
| D：1.00→1.05，长 union | divergence=1.00 s；保留 2 段 | slope 差累积后可辨，合理 |
| E：同 slope，intercept 差 0.2 grid | 0.10 s；允许 | PASS |
| E：同 slope，intercept 差 0.9 grid | 0.45 s；允许 | PASS |
| E：同 slope，intercept 差 1.1 grid | 0.55 s；拒绝 | PASS |
| F：受控 union refit IR=0.89 | 输出 2 段 | 必须拒绝，PASS |
| G：受控 union refit IR=0.91 | 输出 1 段 | 按阈值接受，PASS；无 hysteresis |

两条 affine line 的差仍是 affine function，绝对差在闭区间的最大值出现在端点。
因此“检查 union 两端”能够覆盖 crossing lines：我构造的中点相交、两端相差
1.0 秒案例被正确拒绝。

### 5.2 这个判据不能保证“不制造虚构时间线”

它只能保证：两条**已经抽取出的** line 在观测 union 上相差不超过一个 grid，且
refit 能解释至少 90% 的两段既有 inliers。它不能证明 gap 内的 accused content
连续，也不检查被 proposal 排除的证据。

更直接的反例就在新增 ideal case：`IDEAL_SPEED_DISCONTINUITY` 没有被 merge 成一段，
但 extraction 先输出了：

```text
GT1  acc[0,12]   src[100,109.6]   a=.8
GT2  acc[12,24]  src[109.5,124.5] a=1.25

P1   acc[0,15.5]   src[99.73,112.943]   a=.8525
P2   acc[15.5,24]  src[113.919,124.461] a=1.2402
```

两条 prediction 对 GT 的 source-aware recall 是 **0/2**。也就是说，窄义 merge bug
已经关闭，但 temporal extraction 仍可在 change point 上先制造混合段；当前
behavioral assertion 只检查 `predicted_segments >= 2`，没有检查 boundaries 或 GT。

## 6. `_fit_quality` 与 C-2

当前首要质量实际在 12 个 inlier 时饱和，即 2 × 3 秒 × 2 FPS = 12 frames。

| 对比 | 左 quality | 右 quality | 胜者 | 评价 |
|---|---:|---:|---|---|
| 30s/.90 wrong vs 10s/.99 correct | .90 | .99 | 10s correct | 符合要求 |
| 3s/.995/6 support vs 20s/.94 | .4975 | .94 | 20s | 没有短高分碎片偏置 |
| 6s/.99/仅6 support vs 20s/.97/丰富 support | .495 | .97 | 20s | 符合整体证据目标 |

两个 cross-episode 强解释（.99 与 .97）进入 alternatives 并触发 ambiguity；同 episode、
同 line、0.5 秒平移的近等价解释没有制造 ambiguity。30s wrong + 10s correct 的端到端
case 中两段均被保留并形成 contradiction，正确解释不再被静默压掉。

因此 **C-2 的“long weak 静默压掉 short strong”已关闭**。上述 probes 没有发现新的
普遍短段偏置。不过有两个次要实现问题：

- `explanation_span_saturation_seconds=12.0` 配置完全未被读取；实现硬编码的是
  “2 × minimum duration × FPS”。该字段名称给人的含义是 12 秒，实际饱和点为 6 秒。
- `maximum_temporal_residual_seconds` 也已成为未使用的旧配置。

## 7. Rival / ambiguity compatibility（Major）

当前不兼容判断在 suppression、rival audit 和 conflict graph 中都使用：

```text
cross episode
OR abs(left.source_start - right.source_start) > 1s
```

`source_start` 属于各自 segment 的左端点；当 accused boundaries 不同，它们不是同一
横坐标上的函数值。这个量不能代表 temporal-line compatibility。

我绕开 extraction 的偶然边界，向后半段逻辑注入两对合法 fitted proposals：

1. 同 episode，`source_start` 只差 0.5 秒，但一条 slope=1.0、另一条 slope=1.25，
   到 segment end 已差 5.5 秒：系统判为 compatible，静默删除 rival，最终
   `ambiguous=false`。
2. 两段实际是同一映射 `source=100+accused`，只是 boundaries 分别从 accused 0 和
   accused 2 开始，故 `source_start` 相差 2 秒：系统把第二段放入 alternatives，
   `ambiguous=true`。

端到端同起点不同 slope 的构造有时会“碰巧”进入 ambiguity，因为 extraction 会先删掉
两条线靠近的早期部分，使 rival 的新左端点移到 2.5 秒后；这反而证明结果依赖 proposal
boundary，而不是 line 是否兼容。

source reuse 的独立构造正常：两个 disjoint accused segments 复用同一 source interval，
两段均 `source_reuse=true`、candidate YELLOW、production REVIEW_REQUIRED。跨集共享
片头/片尾也能进入 alternatives。

最明显的回归出现在 synthetic A–M：

| 指标 | `3f5f4d5` run | `c512a80` fresh run |
|---|---:|---:|
| TP / FP / FN | 36 / 33 / 44 | 36 / 33 / 44 |
| AMBIGUOUS cases | 1 / 73 | **33 / 73** |
| ambiguity rate | 1.37% | **45.21%** |

所以 completion report 所述“曾出现约 0.54 ambiguity、最终已避免类似隐患”不成立。
正确修复方向应复用 temporal-line 比较（在共同 accused span 或 union 上比较映射），
而不是 source 起点阈值。

## 8. Gap 1.5 calibration

新增 `##..########` 回归测试实际删除两个 0.5 秒采样点，前后候选时间差为 1.5 秒；
该 case 现在保持一段，PASS。

Reviewer-owned 连续 source line、不同 candidate-to-candidate gap：

| candidate gap | 输出 |
|---:|---:|
| 1.0 s | 1 segment |
| 1.5 s | 1 segment |
| 2.0 s | 1 segment |
| 3.0 s | 2 segments |

2.0 秒仍被连接，是因为 `_continuous_runs()` 先 split，但两个独立长段随后满足
segment-boundary gap ≤1.5 秒而被 `_merge_adjacent()` 重新合并。故配置名 1.5 并不等于
端到端最大 candidate gap 1.5。

只有 candidate 缺失时，系统无法区分“检索 hole”与“accused 中真实未引用内容”。当前
continuity 只看候选时间差和拟合 line，缺少正/负 evidence boundary semantics。1.5 秒
可以作为 provisional calibration，但不能解释成内容连续性的证明。

## 9. Out-of-range speed 全扫描

构造为 40 个 2 FPS、source-grid-snapped candidates。结果如下；多段表示 endpoint
模型只能把真实线分成若干局部片段。

| 真 speed | segments | 报告 speed | out_of_range | candidate tier |
|---:|---:|---|---|---|
| .60 | 3 | 全部 null | 全部 true | 全部 YELLOW |
| .65 | 3 | 全部 null | 全部 true | 全部 YELLOW |
| .70 | 1 | null | true | YELLOW |
| .72 | 1 | null | true | YELLOW |
| .74 | 1 | **.75** | false | **GREEN** |
| .75 | 1 | .75 | false | GREEN |
| .76 | 1 | .7597 | false | GREEN |
| 1.49 | 1 | 1.4991 | false | GREEN |
| 1.50 | 1 | 1.4981 | false | GREEN |
| 1.51 | 1 | **1.50** | false | **GREEN** |
| 1.53 | 1 | null | true | YELLOW |
| 1.55 | 1 | null | true | YELLOW |
| 1.60 | 2 | 全部 null | 全部 true | 全部 YELLOW |
| 1.65 | 3 | 全部 null | 全部 true | 全部 YELLOW |

在本构造中，.60–.72 和 1.53–1.65 能稳定被标记；.74 与 1.51 落在显式的
±0.025 bin tolerance 内，仍会报告端点值。它们属于当前规则承认的量化不可分区，
但也意味着“绝无静默 endpoint”必须加上这个限定。新增参数化测试没有覆盖 .72/.74
或 1.51/1.53；测试中“0.70 无法区分”的注释也不是本次 40-frame sweep 的实际结果。

凡 `speed_out_of_range=true` 的段都满足：`speed_ratio=null`、candidate 非 GREEN、
production REVIEW_REQUIRED。`unconstrained_speed_ratio` 在代码中只被保存和序列化，
没有作为对外速度结论使用。Fresh DB / JSON / audit round-trip 正确；v4 升级库因
Finding 1 无法写入。

## 10. Outcome taxonomy 独立复算

我没有读取 case 的 `outcome` 字段，也没有调用 `classify_case_outcome()`。GT 由固定
seed 的 interval sampling、匿名 normalized position 和 profile speed 独立重建；
每个 primary/alternative 都重新检查 accused overlap、episode、source 两端、duration
与 slope consistency。

| 类别 | 独立计数 |
|---|---:|
| CORRECT_MATCH | 109 |
| SAFE_AMBIGUITY_CONTAINS_GT | 7 |
| UNSAFE_WRONG_MATCH | 4 |
| NO_MATCH | 0 |
| 合计 | 120 |

四类互斥且求和为 120；4 个 right-episode/wrong-timeline 均落入 UNSAFE；所有
NO_MATCH（本 run 为 0）都要求没有输出，非空 segments 没有绕过 status。

但有两项 taxonomy 语义缺陷：

1. 109 个 `CORRECT_MATCH` 中有 **8 个 status=AMBIGUOUS**。真正非歧义且 primary
   正确的是 101 个。因此字段 `exact_automatic_success_rate=109/120` 命名错误；
   AMBIGUOUS 在架构上不能自动成功。
2. `_alternatives_contain_ground_truth()` 只查 episode 与 source endpoints，不查
   accused interval、duration 或 slope。Reviewer probe 中，一个 accused[100,160]
   但 source endpoints 恰好等于 GT 的 alternative 被错误分类为
   `SAFE_AMBIGUITY_CONTAINS_GT`。

当前 7 个 SAFE case 经更严格的完整映射检查仍全部真的包含 GT，所以本次 7 的计数
有效。它们全部集中于两个极端位置：E001-I010（normalized position 约 .990）5 个，
E002-I001（约 .015）2 个；primary/alternative 互换 episode，与共享 OP/ED 的模式一致。

## 11. Real benchmark：case 与 content-position 两种口径

状态分布为 105 MATCH_FOUND、15 AMBIGUOUS。direct/P1/P2/P3 在全部 20 个位置的
outcome 完全配对，不能当成四份独立内容证据。

缩写：`C`=非歧义 primary 正确，`CA`=primary 正确但 AMBIGUOUS，`S`=primary 错但
alternative 含 GT，`U`=unsafe wrong。

| position | direct | P1 | P2 | P3 | .8x | 1.25x |
|---|---|---|---|---|---|---|
| E001-I001 | C | C | C | C | C | C |
| E001-I002 | C | C | C | C | C | C |
| E001-I003 | C | C | C | C | C | C |
| E001-I004 | C | C | C | C | C | C |
| E001-I005 | C | C | C | C | C | C |
| E001-I006 | C | C | C | C | CA | C |
| E001-I007 | C | C | C | C | C | C |
| E001-I008 | **U** | **U** | **U** | **U** | C | C |
| E001-I009 | C | C | C | C | C | C |
| E001-I010 | S | S | S | S | CA | S |
| E002-I001 | CA | CA | CA | CA | S | S |
| E002-I002 | C | C | C | C | C | C |
| E002-I003 | C | C | C | C | C | C |
| E002-I004 | C | C | C | C | CA | C |
| E002-I005 | C | C | C | C | C | C |
| E002-I006 | C | C | C | C | CA | C |
| E002-I007 | C | C | C | C | C | C |
| E002-I008 | C | C | C | C | C | C |
| E002-I009 | C | C | C | C | C | C |
| E002-I010 | C | C | C | C | C | C |

位置级事实：

- 17/20 个位置六档 primary 全部准确；
- 19/20 个位置没有 unsafe 输出；
- 唯一 unsafe position 是 E001-I008；
- 109/120 是 case-level 功能计数，不是 109 个独立样本；
- episode_n 仍为 2，不构成跨集稳定性估计。

## 12. 剩余 4 个 UNSAFE_WRONG_MATCH

四个 profile 的候选命中位置相同：

```text
query grid:  0 .5 1 1.5 2 2.5 3 3.5 4 4.5 5 5.5
GT support:  #  #  .  .   . #   # #   # #   # #
pattern:     ##...#######
```

即 usable hits=9/12；前段只有 2 个支持点，随后连续缺 3 个 query frames；后段有
7 个连续支持点。四档的 best usable candidate 也一致：多数命中相对 expected 约
-0.103 秒，q=3.0 的 best candidate 为 +0.397 秒，均在 ±0.5 秒 usable tolerance 内。

`maximum_frame_gap=1.5` 没有恢复前 2.5 秒，因为 q=.5 到 q=2.5 的 candidate gap
是 **2.0 秒**。前 2-frame run 又小于 6-frame minimum support，被丢弃；后 7-frame
run 单独形成：

```text
prediction accused [2.5, 6.0]
prediction source  [1870.0, 1873.5]
inliers 7/7, RMS 0, coverage 1, slope 1
candidate GREEN, production REVIEW_REQUIRED
```

根因分层：

- retrieval：完整 GT boundary 的证据不足，头部存在 3-frame hole；
- run extraction：按当前 1.5 秒规则正确 split；
- support threshold：正确拒绝仅 2-frame 的头部碎片；
- boundary recovery：没有跨 hole 外推或“完整边界不足”状态；
- source-grid quantization：不是主因。

把它只叫 `ALIGNMENT_LIMIT` 过于粗糙。当前 retrieval 足以找到**某个 partial match**，
但不足以直接证明**完整 GT boundary**；temporal layer 又把 partial match 当完整结论返回。
这 4 个 case 是同一内容位置的配对失败，不是四份独立证据，但它仍是稳定、跨四种非变速
profile 的错误模式，应阻断 Phase 2A Final PASS。

## 13. Retrieval attribution 需要拆分

当前 `_longest_supported_run()` 还有一个独立计算问题：允许的 missing frames 也被计入
`current`。例如 2 FPS、gap=1.5、仅支持 frame `{0,3,6}` 时，该函数返回 run=7，
虽然实际只有 3 个 supporting frames，达不到 aligner 的 6-frame peak minimum。

因此 `run >= minimum` 既不能证明 full-boundary recovery，也不一定证明 aligner 有足够的
真实 support。建议至少区分并分别计数：

```text
RETRIEVAL_SUFFICIENT_FOR_ANY_MATCH
RETRIEVAL_SUFFICIENT_FOR_FULL_BOUNDARY_RECOVERY
```

每个 run 还应同时报告 actual supported-frame count、temporal span 和 tolerated-hole
count。E001-I008 应是前者 true、后者 false，而不是笼统归为 ALIGNMENT_LIMIT。

## 14. Candidate GREEN

residual tautology 已移除：GREEN 现在真正检查 RMS、inlier ratio、absolute support、
coverage/temporal、out-of-range 与 source reuse。独立 real run 为：

```text
candidate GREEN count       98
correct candidate GREEN     94
candidate GREEN precision   94/98 = 0.95918
wrong GREEN                 E001-I008 direct/P1/P2/P3
```

四个截断错误段内部完全自洽，所以 RMS、IR 和 slope 都无法发现“漏掉真实头部”。
Candidate GREEN 明确缺少 segment completeness / boundary evidence。

当前处理应分两层：

- 生产 containment：维持 diagnostic-only 与 `REVIEW_REQUIRED`，这是必要条件；
- GREEN 语义：未来恢复任何 GREEN 信赖之前，必须增加 boundary/completeness evidence。

仅选 Option A 不能关闭 Phase 2A，因为 candidate GREEN 已被 benchmark 当质量指标使用；
Option B 是必需的架构要求。对能够识别为 boundary-incomplete 的 case 应执行 Option C，
但可靠识别本身不是简单阈值修补，而是新的 boundary-evidence 能力。

另外，命中 per-episode segment cap 时，aligner 虽写入 reason 和
`AlignmentOutcome.truncated_episodes`，已有 segment 仍可 candidate GREEN。更严重的是
`AnalysisResult` 没有 `truncated_episodes` 字段，`VideoMatcher` 没有传出它，而 real
benchmark 用 `getattr(result, "truncated_episodes", [])`，所以该结构化字段永远为空。
现有 cap test 只直接测 aligner，未覆盖 matcher/CLI/audit/benchmark 传播。

## 15. Ideal benchmark 真实性

Fresh run 的 gate aggregate 可复现：

```text
source-aware recall     1.0
precision               1.0
speed MAE               0.00693
source boundary MAE     0.02875 s
```

这些数值是对明确列入 gating 的 recoverable cases 的 nominal-grid 内部指标；就这个受限
口径而言计算诚实，但不是 media PTS 精度，也不能代表被排除的 diagnostics。

逐一枚举 17 cases、1824 个 candidate timestamp：1806 个位于 0.5 秒 source grid，
18 个不在 grid；全部 18 个来自 `IDEAL_GRID_JITTER` 的真线。原因是 `_line()` 会先 snap，
但该 case 没有使用 `_line()`，而是直接计算
`25 + 1.25 × accused + jitter`。因此顶层 `source_grid_realistic=true` 为假。

覆盖情况：8 档 speed sweep、negative offset、pure noise、coherent competing lines、
short source span、merge-negative 和 out-of-range cases 都存在。问题在 assertion 强度：

- merge-negative 实际 0/2 source-aware correct，却只因输出段数≥2而显示 true；
- out-of-range assertion 只检查结果不等于精确端点，未验证 flag/null/diagnostic 完整性；
- tracked `benchmark/temporal/results/phase2a-temporal-ideal-v0.1.json` 仍是旧 10-case
  artifact，没有本次 17-case taxonomy/behavioral assertions。

## 16. Existing test edits

### 16.1 `merge_refits_all_inliers`

把旧的 slope-discontinuity 正例改成“同一 line 被 hole 切开”，同时新增 0.8→1.25
negative test，是 legitimate expectation update。新正例仍对照独立 union refit，保留了
“不能算术平均 speed”的原意。

### 16.2 jitter 断言

该修改不是必要的兼容更新。当前 deterministic 输出为：

```text
speed_ratio=1.2946（距1.25为.0446）
accused_end=12.0
inlier_count=19
inlier_ratio=.7917
candidate_tier=YELLOW
```

也就是说，旧的 speed ±.05、end≥11.5、count≥18 在当前实现上仍全部通过；把它们放宽为
±.10、end≥10、count≥16 没有独立必要性。新增“非 GREEN”断言是合理的。

上游测试中没有 `inlier_ratio == 1.0` 这一旧断言；本 commit 新增的是
`inlier_ratio >= .75`。`.75` 与 `16` 都没有由误差模型或采样分布推导，属于按当前输出
附近设置的 brittle calibration。特别是注释声称“当前正好选 16”，而 fresh run 实际是
19。

## 17. Pure-noise 与 structured-wrong safety

纯噪声使用 1/5 episodes × 30/300 秒 × top-k 8/16/32 的完整 12 格矩阵：

| episodes / duration | k=8 | k=16 | k=32 |
|---|---:|---:|---:|
| 1 / 30s | 0 | 0 | 0 |
| 1 / 300s | 0 | 0 | 0 |
| 5 / 30s | 0 | 0 | 0 |
| 5 / 300s | 0 | 0 | 0 |

相干但错误的单条 line 则被 temporal layer 接受为 1 segment，RMS=0、IR=1、coverage=1，
并成为 candidate GREEN；production 仍为 REVIEW_REQUIRED。这是 temporal coherence 的
能力边界：它只能判断候选是否构成线，不能判断视觉候选本身是否指向正确内容。后续视觉
backend Gate 必须保留这条负例，但本审查不据此选择 DINOv2。

## 18. Synthetic A–M 完整性

隔离生成并重跑 A–M 后：

```text
GT=80, predicted=69
TP=36, FP=33, FN=44
precision=36/69=0.521739
```

TP/FP/FN 与 `3f5f4d5` 完全一致。`src/ipvm/benchmarking.py`、generator、evaluator、
sampling 和 GT 在本 commit 均无 diff；13 个 transformation group 仍全部存在，没有
隐藏分组。故 TP/FP/FN 可继续称为 backend-conditioned diagnostic。

但“完全一致”不能扩展到 status：新 rival preservation 把 ambiguity 从 1 提到 33。
Completion report 只列 TP/FP/FN，遗漏了这项明显功能回归。

## 19. Performance 与复杂度

Completion report 给出的 31.28→31.27 秒没有显示回归；本次独立 real rerun 为
32.39 秒，约高 3.6%，仍处于一次本机转码/调度波动能够解释的范围。Synthetic analysis
反而由既有 run 的 6.39 秒变为 6.25 秒。当前数据不足以认定显著性能退化。

但静态复杂度不能写成“没有新的无界 O(N²)”：

- suppression 现在对每个 fitted rival 扫描 `accepted`，并在循环内重建 winner frame set；
- 每个 accepted winner 又扫描全部 `scored_segments` 生成 rivals；
- 以总 fitted segment 数 S 和每段 frame 数 F 计，新增路径最坏为 O(S²·F)；
- 128 的 cap 是 per episode，不是全局 cap，episode 数增长时 S 仍无全局界。

既有 conflict/source-reuse 已有 O(S²)，所以当前规模未表现回归；但新逻辑确实增加了
二次扫描，不能宣称 asymptotic 不变且有全局界。

## 20. Schema / DB / audit 字段核对

| 字段/结构 | 结论 |
|---|---|
| `speed_out_of_range` | model、fresh schema、DB write、audit JSON 有；v4 upgrade 缺失 |
| `unconstrained_speed_ratio` | 同上；代码中仅 diagnostic |
| alternatives | DB 以 `is_primary=false` 保存；audit 有 `alternative_candidates` |
| `ambiguity_contains_ground_truth` | benchmark-only，合理；但 helper 判定不完整 |
| longest supported run | benchmark-only；会把 tolerated holes 计入 run length |
| `truncated_episodes` | aligner 有；AnalysisResult/CLI/audit/real benchmark 结构化传播丢失 |
| speed nullability | fresh DB / JSON / audit 正确；v4 upgrade 无法写 |
| timing basis | 仍为 `nominal_grid_time`；未硬编码 +0.23 秒 |

## 21. 测试缺口

除现有 78 tests 外，至少还缺：

1. v4→v5 成功迁移、失败 rollback、新 row 写入；
2. boundary completeness / truncated candidate GREEN；
3. rival 以完整 temporal line 而非 raw source_start 判断；
4. out-of-range 的 .72/.74 与 1.51/1.53 endpoint 边界；
5. segment cap 从 aligner 到 matcher/CLI/audit/benchmark 的传播与降级；
6. taxonomy 四类的独立 fixture，而不只是 `sum == case_count`；
7. SAFE ambiguity：GT present 与 source-only-present-but-mapping-wrong；
8. NO_MATCH 真正无输出；
9. correct episode / wrong source time 必须 UNSAFE；
10. merge-negative 的 boundary 正确性，而非只数 segment；
11. `_longest_supported_run` 同时约束 actual hit count；
12. ideal 所有 candidate timestamp 的 source-grid 枚举断言。

## 22. Architecture Owner 13 项裁定核对

按 completion report 的 13 个标题逐项判断：

| # | 项目 | 裁定 |
|---:|---|---|
| 1 | Merge safety | **CONDITIONAL PASS**：窄义 merge 关闭；change-point extraction 仍错 |
| 2 | Competing hypothesis ranking | **PASS**：C-2 关闭，未见短碎片反偏置 |
| 3 | Frame-gap 1.5 | **CONDITIONAL PASS**：目标 hole 修复；边界语义和有效 2.0s bridge 未说明 |
| 4 | Out-of-range speed | **CONDITIONAL PASS**：明显越界安全；tolerance 内仍报端点且 tests 不完整 |
| 5 | Outcome taxonomy | **FAIL**：SAFE helper 不查完整映射；automatic 命名含 AMBIGUOUS |
| 6 | Ambiguous cases | **CONDITIONAL PASS**：本次 7 个真含 GT；rival 语义产生大量额外 ambiguity |
| 7 | Retrieval attribution | **FAIL**：partial/full 未拆；holes 被计入 run length |
| 8 | GREEN diagnostic | **CONDITIONAL PASS**：residual 修复；completeness 缺失，4 wrong GREEN |
| 9 | Ideal realism | **FAIL**：18 个非 grid candidates；merge-negative assertion 假阳性 |
| 10 | Extreme jitter safety | **CONDITIONAL PASS**：非 GREEN；测试阈值缺少独立依据 |
| 11 | Segment cap | **FAIL**：aligner 有信号，但下游结构化传播丢失且 segment 可 GREEN |
| 12 | Phase 1 synthetic gate | **PASS（文档口径）**：仍 FAIL/deferred；但应补报 ambiguity 回归 |
| 13 | Timestamp policy | **PASS**：nominal 与 media PTS 区分，未引入常数修正 |

结论：Q1 的答案是**否**，13 项并未全部正确完成。

## 23. Final Gate questions

### Q1　是否正确实现 13 项裁定？

**否。** 2、12、13 可通过；其余多项仅部分完成，taxonomy、attribution、ideal
realism 和 cap propagation 明确失败，另有 Critical v4→v5 migration 缺陷。

### Q2　C-1 merge bug 是否真正关闭？

**窄义关闭，广义未关闭。** `_merge_adjacent()` 会拒绝 0.8→1.25；但 end-to-end
extraction 仍能输出跨 change point 的虚构段，现有 ideal assertion 没有捕获。

### Q3　C-2 是否关闭，是否换成短段偏置？

**C-2 已关闭。** 指定 A/B/C probes 的排序都符合整体证据目标；没有观察到短高分碎片
压掉长强证据。新的主要问题是 rival compatibility 和 ambiguity 膨胀，不是短段偏置。

### Q4　4 个 UNSAFE 是已知 limitation 还是 blocker？

**是 Phase 2A Final PASS blocker。** 它们是一个位置的系统性 boundary failure，不能按
4 个独立失败夸大，但也不能用 `REVIEW_REQUIRED` 掩盖 `MATCH_FOUND` 输出了错误时间线。

### Q5　截断错误 candidate GREEN 现在修还是 diagnostic-only？

自动放行继续禁用，作为暂时 containment 可以保留 diagnostic-only；但这不足以关闭
Phase 2A。未来 GREEN 必须加入 completeness（Option B），可识别的 incomplete case
应立即降级（Option C）。可靠识别完整边界属于新的 evidence capability。

### Q6　是否拆分 RETRIEVAL_LIMIT？

**是。** 至少拆成“足以形成任一 partial temporal match”和“足以恢复完整 boundary”，
并修正 longest-run 对 holes 与 actual hit count 的混算。

### Q7　Phase 2A 能否 PASS / CLOSED？

**不能。** 当前 commit Gate 为 FAIL，Phase 2A 仍应保持 CONDITIONAL。

若 Architecture Owner 设 Phase 2A.2，应明确分成两类 scope：

- **小修 / safety closure**：v4→v5 migration、temporal-line rival 判据、taxonomy helper、
  cap propagation、ideal grid/assertions、测试与报告修正；
- **新能力**：segment boundary completeness / full-boundary recovery。若目标是把
  E001-I008 的完整 0–6 秒边界恢复出来，而不只是保守降级，就不是阈值小修。

本报告不授权或启动 Phase 2A.2，只说明关闭本 Gate 所需的性质。

## 24. 分项 verdict

| 维度 | Verdict | 理由 |
|---|---|---|
| core temporal mechanics | **CONDITIONAL PASS** | 核心 v2 可保留；C-1/C-2 窄义修复有效，但 change-point 仍错 |
| safety | **FAIL** | 4 unsafe wrong、rival 误判、cap 信号未贯通 |
| boundary completeness | **FAIL** | partial self-consistency 被当作完整时间线，且可 GREEN |
| benchmark integrity | **FAIL** | grid 标志错误、SAFE helper 不完整、merge-negative 假阳性、tracked artifact 过期 |
| production-release safety | **CONDITIONAL PASS** | GREEN 自动放行仍禁用；但 v4 升级后持久化失败，不能视为 production-ready |

## Final Verdict

```text
FAIL
```

停止于独立审查，不进入 DINOv2、几何变换、Phase 3 或任何下一阶段实现。
