# Phase 1.5 独立技术审查（第二次）

- **审查对象**：`2e8294f Implement Phase 1.5 measurement and safety hardening`
- **基线**：`71e8d93 Initial architecture baseline`
- **审查人身份**：独立 reviewer，非 Phase 2 开发者
- **审查日期**：2026-09-02
- **方法**：全量阅读 13 个源文件、8 个测试文件、5 份 ADR 与修订文档；**未修改任何生产代码**；运行 10 组只读探针实验
- **前置文档**：`docs/review/PHASE1-INDEPENDENT-REVIEW.md`（第一次审查，CONDITIONAL PASS）

---

# Executive Verdict

## **CONDITIONAL PASS**

Phase 1.5 的核心交付物——**可信 measurement**——**已经真正完成**，质量显著超出预期。Phase 1 Gate 的 FAIL 是这套 measurement 正确工作的证据，不是 Phase 1.5 的失败。

但**两项 safety 交付物存在已验证的漏洞**，且这两个漏洞恰好会被 Phase 2A 即将引入的能力（镜像、变速、crop）直接触发。因此：**Phase 1.5 可以在修复 C-1、C-2 后关闭并进入 Phase 2A。**

## 判定依据

**已验证真正完成的部分（无保留）：**

| 交付物 | 结论 |
|---|---|
| 五种一等 status | ✅ `MatchStatus` StrEnum 完整；运行中观察到 4 种，`ERROR` 有真实代码路径 |
| `confidence` → `match_score` | ✅ 全链路更名，schema/audit/CLI 一致；ADR-0003 的"不是正确概率"表述准确 |
| GREEN 不由单一分数决定 | ✅ 五条件同时满足才 GREEN；**且已证明在真实场景中拦截了错误**（见 M-1 的 CRF 实验） |
| incomplete index 阻断 | ✅ 三重防御：`index_status` 过滤 + 项目级 health + 运行时 `.npz` 帧数复核 |
| project-scoped schema | ✅ `UNIQUE(project_id, episode)`，migration 有测试覆盖 |
| `mirrored/cropped` → null | ✅ `bool \| None = None`，DB nullable，全链路无 `False` 伪事实 |
| audit 完整性 | ✅ 补齐原片 SHA、config 快照、encoder、index completeness、竞争候选、reason |
| benchmark 机器生成 + 标准 JSON | ✅ 三个 JSON 全部通过 RFC 8259 严格解析；**gate 在干净根目录 11 项指标逐位复现** |
| evaluator 指标定义 | ✅ 第一次审查的 C-2a–C-2e **全部修复**，我用 6 组对抗性输入独立验证（详见 Benchmark Audit） |

**未闭合的部分：**

- **C-1**：GREEN 仍然放行了一个错误答案。committed 的 diagnostic 里 2 个 GREEN segment 中有 1 个（CASE_B 镜像 → EP03）**集数和时间轴都错**。GREEN precision = 50%。ADR-0003 的五个条件全部是**内部自洽性**度量，没有任何一条是**外部有效性**检查，所以一个系统性变换过的输入可以产生内部完美自洽的错误匹配。
- **C-2**：`ambiguity_score_ratio = 0.99999` 只能捕获**逐位相同**的重复。把共享片头按不同 CRF 重新编码（真实剧集的常态），score ratio 降到 **0.9999**，恰好落在阈值之外 → 返回 `MATCH_FOUND` 并给出**错误集数**。ADR-0003 已声明该阈值需用 golden set 重新校准，但当前实现的裕度只有一个数量级。

**不给 PASS 的理由**：上述两个漏洞属于 Phase 1.5 自身的 charter（safety hardening），且 Phase 2A 加入镜像/变速/crop 后会被高频触发。
**不给 FAIL 的理由**：measurement 层的质量是实打实的。evaluator 经受住了我 6 组对抗性探针中的 4 组；gate 完全可复现；旧的六项指标缺陷全部关闭。这正是 Phase 1.5 的主要任务，它做到了。

---

# Critical

## C-1　GREEN 仍会放行确信的错误答案：五个条件全部是内部自洽性

**证据（来自 committed 的 `output/reports/benchmark_all_diagnostic.json`，不是我的探针）：**

```
GREEN-tier segments in the diagnostic:
   CASE_B  horizontal_mirror              pred EP03 [6.00,10.50]   correct=False
   CASE_I  off_grid_multi_episode_concat  pred EP02 [2.00, 6.50]   correct=True
```

一个从 **EP01** 剪出、水平镜像的片段，被判为 **EP03 的 6.00–10.50 秒**，`match_score 0.981`，**review_tier = GREEN**。真值是 EP01 的 1.238–6.479 秒。集数错、时间轴错、变换未识别，而它落在"自动写入 Excel、不进人工复核"的档位。

**根因（`matching.py:293-305`）：**

```python
green = (index_complete
    and margin >= 0.05
    and segment.temporal_residual_seconds <= 0.55
    and segment.coverage >= 0.90
    and segment.temporal_score >= 0.90)
```

五个条件问的都是"**这个被选中的提案自己是否融贯**"：
- `margin` —— 我比第二名高多少（第二名也可能同样错）；
- `temporal_residual` —— 我的点是否落在一条直线上；
- `coverage` / `temporal_score` —— 我的帧是否稠密且都是内点。

**没有一条问"这个答案对不对"。** 一个被镜像的输入在错误集数上找到一段自洽的线性映射，五个条件同时满足，GREEN 触发。

**探针 E10 进一步放大了这个问题**（同一段 EP01，两种子帧相位，八种变换）：

```
── 相位 0.238（= 现行 A–J 的抽样）      ── 相位 0.000
   A direct_cut  MATCH_NOT_FOUND           A direct_cut  MATCH_FOUND  EP01 ✓
   B hflip       MATCH_FOUND EP03  YELLOW  B hflip       MATCH_NOT_FOUND
   D speed0.8    MATCH_FOUND EP01  YELLOW  D speed0.8    MATCH_NOT_FOUND
   E center_crop MATCH_FOUND EP01  GREEN   E center_crop MATCH_NOT_FOUND
                 src[11.00,14.00] 真值 1.238   F/G/H       MATCH_NOT_FOUND / AMBIGUOUS
```

在**好相位**下，所有未实现的变换都正确地返回 `MATCH_NOT_FOUND`——这是 Phase 1 应有的行为。在**坏相位**下，同样的变换把安全拒绝变成了确信编造：center crop 拿到 **GREEN**，源时间偏离约 **10 秒**。

> **失败模式从"安全拒绝"翻转为"确信编造"，触发条件是 off-grid + 未实现的变换——而这正是 Phase 2A 要处理的输入分布。**

**这是第一次审查 C-1 的直系后代。** Phase 1.5 把它从"单一分数决定一切"收窄为"五条件"，确实拦下了一部分（见 M-1 的 CRF 案例），但没有闭合，因为**收窄的方向是增加更多内部条件，而缺的是外部验证**。

**可行的闭合方向（属于架构决策，见 Q1）：**
1. GREEN 额外要求**反向验证**：对判定的 source 区间按 8 FPS 重抽帧，与被诉帧逐帧比对，要求平均相似度高于一个绝对阈值——这是外部检查，不是自洽检查；
2. GREEN 额外要求**变换检测已运行且为阴性**（`mirrored is False` 而非 `None`）——即在 Phase 2 变换检测落地之前，**GREEN 不可用**；
3. 最保守：Phase 2 完成前禁用 GREEN，全部 YELLOW。

## C-2　Ambiguity 检测只能捕获逐位相同的重复，CRF 变化即失效

**探针 E8**（`ambiguity_score_ratio = 0.99999`，被诉片段取自 EP04 的共享片头）：

| 共享片头的构造方式 | score ratio | status | 主要候选 |
|---|---:|---|---|
| 两集用相同参数编码（= CASE_L） | 1.0 | `AMBIGUOUS` ✅ | — |
| **两集分别按 CRF 20 / CRF 26 编码** | **0.9999** | **`MATCH_FOUND`** ❌ | **EP05（查询来自 EP04）** |
| EP05 片头经 640→636→640 缩放 | 1.0 | `AMBIGUOUS` ✅ | — |
| 三集共享片头（相同编码） | 1.0 | `AMBIGUOUS` ✅ | — |

CASE_L 的构造是把**同一个 `shared.mp4`** 用**同一组参数**拼进两集，因此解码后逐帧近乎相同。真实剧集的每一集是**各自独立编码**的成片，码率、GOP、量化都可能不同。**只要引入一次 CRF 差异，score ratio 就从 1.0 掉到 0.9999，恰好越过 0.99999 的阈值，歧义检测静默失效。** 阈值裕度只有一个数量级。

**两点缓解，都需要记录在案：**
- `uniqueness_margin = 0.0001 < 0.05` → tier 降为 **YELLOW**，没有进入 GREEN。**这证明 ADR-0003 "GREEN 不由单一分数决定"的设计确实在做实事**，是本次 Phase 1.5 最有价值的一处设计。
- 竞争候选被写入 audit 的 `top_competing_candidates`。

**但仍然是 Critical，因为：**
- `status` 是 `MATCH_FOUND` 而不是 `AMBIGUOUS`，下游（Excel、rule engine）读的是 status；
- `alternatives` 只在 `is_ambiguous` 为真时才填充（`matching.py:264-266`），所以这条路径下 **`alternatives` 为空**，数据库里只有一条**错误的 primary segment**，`is_primary=0` 的竞争行不存在。审计链只在 JSON 里保留了竞争者，DB 查询看不到。

ADR-0003 已经声明"真实素材上的阈值必须由 golden set 重新校准"——这是诚实的。但**在校准之前，这个检测器对真实剧集的片头/片尾/前情提要是无效的**，而这正是它被创造出来要解决的场景。

**结构性建议（属于架构决策，见 Q2）**：把重复检测从**查询期的分数比值**改为**索引期的预计算**——建索引时直接检测跨集重复区间并标记，查询命中被标记区间时无条件返回 `AMBIGUOUS`。这不依赖任何浮点阈值。

---

# Major

## M-1　Phase 1 Gate FAIL 的真正根因不是"DCT 不够强"，而是"2 FPS 索引没有 off-grid 的对应帧"

这是本次审查最重要的技术发现，它**改变 Phase 2A 的排序建议**。

**探针 E1（决定性）**：同一集、同一时长（5.0 s）、纯直接剪切、无任何变换，只让切点的**子帧相位**在一个 0.5 秒采样周期内扫描：

```
 start   phase | status            pred src        srcErr
12.000   0.000 | MATCH_FOUND       [12.00,17.00]   0.000
12.050   0.050 | AMBIGUOUS         -
12.100   0.100 | AMBIGUOUS         -
12.150   0.150 | MATCH_NOT_FOUND   -
12.200   0.200 | MATCH_NOT_FOUND   -
12.250   0.250 | MATCH_NOT_FOUND   -
12.300   0.300 | MATCH_NOT_FOUND   -
12.350   0.350 | MATCH_FOUND       [12.50,17.50]   0.150
12.400   0.400 | MATCH_FOUND       [12.50,17.50]   0.100
12.450   0.450 | MATCH_FOUND       [12.50,17.50]   0.050

source-aware recall@1s = 4/10 = 40%，纯粹由子帧相位决定
```

**探针 E2b（定位到阶段）**：

```
── 相位 0.00（成功）  10 帧全部通过 0.82 阈值
   EP01 bucket 24: 10 帧  最长连续 5.0s  OK   meanVis=1.0000
── 相位 0.25（失败）  10 帧同样全部通过 0.82 阈值（0.9782–0.9907）
   EP01 bucket 23:  4 帧  最长连续 1.5s  低于 3.0s → 丢弃
   EP01 bucket 67/43/19/47/71: 各 3 帧  最长 0.5–1.0s → 全部丢弃
   -> 存活提案数 = 0
```

视觉相似度**完全正常**（0.978+），失败发生在 offset 聚类阶段。

**探针 E5（根因定位，决定性）**：直接在 `.npz` 上计算"正确源帧在全库中的排名"：

```
 phase   正确帧的中位排名（EP01 共 80 帧）   p90   最差   胜出别名的相似度
  0.00                                 1      1     1        1.0000
  0.10                                 1      2     2        0.9864
  0.15                                12     36    40        0.9794
  0.20                                44     68    69        0.9815
  0.25                                42     72    75        0.9814
  0.30                                14     41    48        0.9786
  0.35                                 1      2     2        0.9854
  0.45                                 1      1     1        0.9981
```

**在相位 0.20–0.25，正确的源帧排名中位数是 80 帧中的第 42–44 位——统计上与随机帧无异。** `top_k = 8` 完全取不到它。探针 E4 直接确认：相位 0.20 时，**10 个被诉帧中有 0 个的正确源帧出现在 top-8 里**。

**探针 E6（为什么）**：DCT 描述子在该语料上的动态范围：

```
EP01: 80 帧 | 全部非对角 cosine 0.9494..0.9999 | visual_score 0.9745..1.0000
      所有帧对中通过 0.82 阈值的比例：100%
```

整集任意两帧的相似度都挤在 0.026 的区间内。当被诉帧恰好落在两个索引采样点中间时，它与正确邻居的相似度下降幅度，与它和整集任意其它帧的相似度差异**同一量级**。信号被噪声淹没。

**顺带的结论**：`minimum_visual_similarity = 0.82` 在该语料上**从不触发**（100% 的帧对都在阈值之上）。一个永不触发的安全阈值等于没有。

**对 ADR-0002 的直接含义（见 ADR-0002 Review）：**

> 正确的对应关系**根本不在候选集里**。任何在候选集之上运行的对齐算法——2D voting、DTW、LIS、graph alignment——都无法恢复它。**ADR-0002 位于失败点的下游。**

**探针 E3（排除其它解释）**：

| 配置 | recall@1s |
|---|---:|
| 现行语料（plain `testsrc2`） | 40% |
| 更有辨识度的语料（`testsrc2` + 移动色块，Phase-1 生成器写法） | **20%**（更差） |
| `offset_bin_seconds` 0.5 → 1.0 | 40%（无变化） |
| `coarse_fps` 2 → 4 | 50%（小幅改善） |

- 增加内容辨识度**反而更差**——因为内容变化越快，0.25 秒的时间错配造成的视觉差异越大。这排除了"语料太自相似"这个解释。
- 加宽 offset bin **完全无效**——排除了"bucket 宽度不够"这个解释。
- 提高采样率**有效但有限**——指向正确方向。

**注意范围限定**：以上量级是在 `testsrc2` 上测得的。真实剧集的镜头间低频结构差异远大于合成图案，DCT 的可分性会好得多。**方向是真实的，幅度是语料相关的。** 这正是 Q3 需要先做真实素材测量的原因。

## M-2　Gate 失败的完整归因（3 个 episode-level miss + 2 个 false positive）

`source_aware_recall@1s = 18/23 = 78.3%`，5 个未召回：

| Case | GT | 实际输出 | 失败阶段 | 根因 |
|---|---|---|---|---|
| **P1_004** | EP01 src[2.131, 5.539]，时长 3.408 s | `MATCH_NOT_FOUND` | 候选检索 | 子帧相位 0.131 + 时长仅 3.408 s（≈7 帧），offset 分裂后无一段达 3.0 s |
| **P1_009** | EP03 src[6.252, 9.604]，时长 3.352 s | `MATCH_NOT_FOUND` | 候选检索 | 相位 **0.252**（最坏值）+ 时长 3.352 s |
| **P1_012** | EP03 src[1.189, 5.326] | `AMBIGUOUS`（"Segment contradiction detected"） | 对齐 + 矛盾处理 | 两个 EP03 提案：src[8.5,11.5] 与 src[0.0,3.0]，accused 重叠 2.5 s、源起点差 8.5 s → 判定矛盾 → **整案输出被清空** |
| **CASE_A** | EP01 src[1.238, 6.479] | `MATCH_FOUND` EP01 src[**0.500, 3.500**] | 对齐（截断） | 相位 0.238；段被截断到 3.0 s，source_end 误差 2.979 s > 1 s |
| **CASE_I** 第一段 | EP01 src[1.238, 5.411] | 同上，src[0.500, 3.500] | 同上 | 同上 |

**`segment_precision@1s = 90.0%` 的两个 false positive 就是 CASE_A 和 CASE_I 的第一段。** 它们**不是凭空捏造的区间**——集数正确（`episode_correctness = 100%` 印证），只是源时间轴偏差 > 1 秒。这一点 `PHASE1.5-RESULTS.md` 没有说清楚，容易被误读为"系统在编造侵权片段"。

**按失败阶段归类：**

| 阶段 | 命中 | 说明 |
|---|---|---|
| Visual feature / DCT | **3**（P1_004、P1_009 及 CASE_A/I 的间接成因） | 正确帧不在 top-8（M-1） |
| Candidate retrieval | **同上** | `top_k=8` 取不到排名 40+ 的正确帧 |
| Temporal alignment | **2**（CASE_A、CASE_I 第一段） | offset 分桶导致段被截断到 3.0 s |
| Segment merge / claiming | 0 | `_merge_adjacent` 未观察到误合并 |
| Threshold | 0（但 `minimum_visual_similarity` 实际失效，见 M-1） | |
| Ambiguity handling | **1**（P1_012） | 矛盾检测**按设计正确工作**，代价是丢失该案全部输出 |
| Evaluator 本身 | **0** | 我用 6 组对抗性输入验证，未发现错判（详见 Benchmark Audit） |

**关于 P1_012**：矛盾检测做出的是**正确的安全选择**（两个不可调和的主张 → 不猜）。但 `matching.py:285` 的处理是 `segments=[] if ambiguous or contradiction`——**一对矛盾清空整案**。在真实的多段混剪里，第 7 段和第 8 段矛盾会让前 6 段正确结果一并消失。见 Q4。

## M-3　A–J diagnostic 的 16.7% 主要由**一次不幸的抽样**造成，不能读作变换鲁棒性的估计

A–J 的 10 个 case 中有 **8 个**使用同一个源区间：EP01 的 `first_start = 1.238`（子帧相位 **0.238**，接近 0.25 的最坏值）。只有 CASE_I/J 额外使用了 EP02 的 `2.441`（相位 0.059，接近最好值）——而 CASE_I/J 的 EP02 段**全部成功**。

**探针 E10 的对照**证实了这一点：同样八种变换，只改切点相位——

| 相位 | 结果 |
|---|---|
| **0.238**（= 现行抽样） | A 失败；B→EP03、D→偏 8 秒、E→**GREEN** 偏 10 秒；C/F/G/H 无输出 |
| **0.000** | A 成功；B/C/D/E/F/H 全部 `MATCH_NOT_FOUND`；G `AMBIGUOUS`。**零假阳性** |

所以：
- **"变换未实现"是真的**——好相位下所有变换案例都正确拒绝；
- **"16.7% source-aware recall"不是变换能力的度量**——它主要度量了这一次抽到的相位有多差；
- 第一次审查的 M-1（A–J 源区间多样性 n=1）**在 gate 集合上已修复**（20 条独立抽样的 P1 片段），**但在 A–J 子集上仍未修复**。

这是本次 Phase 1.5 的一个真实缺口：`SyntheticBenchmarkGenerator.generate()` 里 A–H 共用 `first_start` 一个变量（`benchmarking.py:132, 143`）。见 Q5。

## M-4　Evaluator 的两处可被"看起来正确"的预测欺骗

我构造了 6 组对抗性输入（探针 E7）。**4 组行为正确**，2 组存在漏洞：

**漏洞 1——重复的正确预测不受惩罚：**

```
同一答案输出两次           recall@1s=1.0  precision=1.0  FPseg=0  FPsec=0.0
近似重复（相差 0.3 秒）     recall@1s=1.0  precision=1.0  FPseg=0  FPsec=0.0
```

`true_predictions` 是**预测下标**的集合，多个预测可以同时匹配同一条 GT 并各自被计为"真"。一个输出 N 份冗余正确片段的系统拿到满分 precision。运行期有 `_has_contradiction` 的同集源重叠检查作为部分防线（这是第一次审查 M-6 的修复），但**evaluator 层不度量它**，因此该防线一旦回归也不会被发现。对后续 §31「引用比例」（按 segment 时长求和）意味着重复计数。

**漏洞 2——物理上不可能的斜率通过判据：**

```
accused[0,60] → source[10,15]   recall@1s=1.0  precision=1.0  FPseg=0
```

60 秒被诉内容映射到 5 秒原片（隐含 12 倍慢放）。两个源端点都在 1 秒容差内 → 计为完全成功。**evaluator 从不检查 accused 跨度与 source 跨度是否自洽。** `speed_ratio_mae` 有计算但不参与判据（ADR-0005 明确说明 Phase 1 不 gate 变速，这是对的），所以这条路没有任何拦截。

修复方向：在 `_source_match` 中增加 `|accused_duration − source_duration / gt_speed| ≤ tolerance`，或在 precision 判据中要求每条 GT 至多认领一条预测（匈牙利匹配 / 贪心一对一）。

## M-5　`speed_ratio_mae` 被非变速样本稀释，读数误导

A–J 子集报告 `speed_ratio_mae = 0.05`。实际构成：CASE_C 误差 0.25、CASE_D 误差 0.20，其余 7 条速度真值为 1.0 的样本误差 0.0 → (0.25+0.20)/9 = 0.05。

**在真正的两条变速样本上，平均误差是 0.225**，即系统把 1.25× 报成 1.0、把 0.8× 报成 1.0，误差率 18–20%。报告的 0.05 让变速能力看起来比实际好 4.5 倍。

建议：`speed_ratio_mae` 只在 `gt_speed != 1.0` 的样本上计算，或按 transformation 分组报告。

## M-6　迁移过程不是原子的

`Database._migrate_legacy_to_v2`（`database.py:177-267`）内部调用了两次 `connection.executescript(SCHEMA)`。Python `sqlite3` 的 `executescript` **在执行前会隐式 COMMIT**。因此迁移序列：

```
rename v1 表 → executescript（隐式提交）→ INSERT...SELECT ×4 → DROP v1 → executescript（隐式提交）
```

不在单个事务内。进程在中途崩溃会留下半迁移状态：v1 表可能已被 DROP 而新表数据不完整，或新旧表并存。对以"可追溯"为核心的律所证据库，这是数据完整性风险。

另外 `PRAGMA foreign_keys = OFF/ON`（`database.py:179, 267`）在事务内是 no-op；当前调用点恰好不在写事务中所以生效，但这是脆弱的依赖。

建议：整个迁移包在一个显式 `BEGIN IMMEDIATE` 内，改用 `execute` 逐条执行 DDL 而非 `executescript`；或迁移前先复制一份 `.db` 备份。

---

# Minor

- **m-1　`frame_features` 仍然只写不读。** 全代码库唯一的 SELECT 在迁移路径里（`database.py:222`）。40 集 × 45 分钟 = 21.6 万行纯冗余，内容与 `.npz` 的 `timestamps` 完全重复。第一次审查 M-10 未修复。
- **m-2　索引缓存键不覆盖采样参数。** 键是 `{sha256}-{encoder}-{algorithm_version}`（`indexer.py:163-165`），不含 `coarse_fps` / `frame_width` / `frame_height`。实测：改 `frame_width` 160→320 后**缓存被静默复用且状态为 COMPLETE**；改 `coarse_fps` 2→4 会被完整性校验挡下（但报错信息说的是"媒体覆盖不足"，误导）。对当前 DCT 编码器（内部统一缩放到 32×32）实测跨分辨率描述子 cosine = 1.00000，**影响可忽略**；但换成直接消费预缩放帧的编码器（DINOv2 224×224）后会变成真实的静默污染。建议在 Phase 2A 换 backend 前把采样参数纳入缓存键。
- **m-3　`minimum_visual_similarity = 0.82` 在合成语料上从不触发**（100% 帧对高于阈值，M-1）。一个永不触发的安全阈值给人虚假的安全感。应在真实素材上重新标定，或改为相对阈值（相对于该查询帧的分数分布）。
- **m-4　`validate_index_completeness` 的 `monotonic` 检查是死代码。** 时间戳由 `frame_index / fps` 合成（`media.py:108`），恒为严格递增，该分支永不为假。
- **m-5　`index_all` 的项目完整性是全有或全无。** `project_complete = complete_count == len(files) == len(results)`（`indexer.py:143`）。`input/originals/` 里一个命名不规范的杂项视频就会让整个项目变成 `INCOMPLETE_INDEX`，阻断所有分析。安全姿态正确，但操作上偏严（见 Q6）。
- **m-6　ambiguity 分支的 FP 统计是死码。** `evaluate_segments` 的歧义分支会计算 `false_positive_segments/seconds`，但 `_aggregate` 用 `normal = [... if not ambiguity_expected]` 把这些 case 整体排除，这些数字永远不进聚合。CASE_L 的错误行为实际由 gate 的 status 检查捕获，不由 FP 指标捕获。
- **m-7　`audit.py` 的 `schema_version` 仍是 `"0.1"`**，尽管 payload 结构大幅变化（新增 `original_assets`、`index_completeness`、`top_competing_candidates`、`review_tier`、`reason`）。下游无法据此区分格式。
- **m-8　`VideoMatcher.__init__` 与 `OriginalIndexer.__init__` 调用 `database.initialize()`**，构造函数带 schema 迁移副作用。CLI 每个 case 构造一次 `VideoMatcher`，即每个 case 触发一次迁移检查。功能正确但是代码味道。
- **m-9　`CandidateRetriever` 仍无单元测试。** 8 个测试文件中没有一个直接测试检索层——而 M-1 证明当前的主要失败恰好发生在这一层。
- **m-10　中间候选列表的内存问题未处理。** `matching.py:58-84` 仍然在裁剪到 `top_k` 之前物化 `query_frames × n_episodes × top_k` 个 `CandidateMatch`（第一次审查 M-3 中测得季度规模下为 96 万对象 / 154 MB）。Phase 1.5 不要求性能工作，记录备查。

---

# Benchmark Audit

## 第一次审查的六项指标缺陷：逐条核验

| 第一次审查的发现 | 状态 | 核验方式 |
|---|---|---|
| C-2a `episode_accuracy ≡ segment_recall` | ✅ **已修复** | `episode_recall`（分母 = GT 数）与 `episode_correctness`（分母 = 重叠预测数）是两个不同的量；gate 报告中前者 0.8696、后者 1.0，**数值不同**，证明已解耦 |
| C-2b recall 不校验原片时间轴 | ✅ **已修复** | `_source_match` 要求两个源端点均在 1 秒内；探针 C 组：源偏 1.1 秒 → recall 0.0 |
| C-2c 无 precision / FP 指标 | ✅ **已修复** | 探针 A 组：1 正确 + 2 捏造 → precision 0.333、FPseg 2、FPsec 40.0 |
| C-2d 聚合 MAE 丢弃失败样本 | ✅ **已修复** | gate 条件为 `MAE ≤ 1.0 AND boundary_missed_segments == 0`（`benchmarking.py:661-665`）；实测 MAE 0.275 但因 3 个 miss 判 FAIL |
| C-2e `speed_ratio` 从不校验 | ⚠️ **部分** | `speed_ratio_mae` 已计算并报告，但被非变速样本稀释（M-5）；不参与 gate 是 ADR-0005 的正确决定 |
| M-2 GT 吸附 0.5 秒网格 | ✅ **已修复** | `round(uniform(4.137, 5.863), 3)` / `round(uniform(1.123, ...), 3)`，三位小数；实际 GT 值如 `1.238` / `5.241` / `4.173` 确认脱离网格 |
| M-9 `Infinity` 非法 JSON | ✅ **已修复** | 三个 JSON 全部通过 RFC 8259 严格解析；`allow_nan=False` 显式设置于 `cli.py:35`、`benchmarking.py:272` |
| M-19 聚合按 case 平均 | ✅ **已修复** | `_aggregate` 从 `raw_source_boundary_errors` 逐边界重算；我独立重算 0.274600，与报告逐位一致 |

## 对抗性探针：evaluator 能否被"看起来正确"的预测欺骗

| 探针 | 输入 | 结果 | 判定 |
|---|---|---|---|
| A | 1 正确 + 2 处捏造（40 s / 30 s） | precision 0.333、FPsec 40.0 | ✅ 正确惩罚 |
| B | 同一正确答案输出两次 | precision **1.0**、FPseg **0** | ❌ **漏洞（M-4）** |
| B' | 近似重复（错开 0.3 秒） | precision **1.0** | ❌ **漏洞（M-4）** |
| C | 源偏 0.9 s / 1.1 s / 子集 / 错集数 | 1.0 / 0.0 / 0.0 / 0.0；错集数使 `episode_correctness` 独立降为 0 | ✅ 边界正确、指标独立 |
| D | accused[0,60] 吞掉 GT，source[10,70] | recall 0.0、FPsec 60.0 | ✅ 正确拒绝 |
| D' | accused[0,60] → source[10,15]（斜率 0.083） | recall **1.0**、precision **1.0** | ❌ **漏洞（M-4）** |
| E | 歧义案上给出单一确信答案 | FPseg 1、FPsec 5.0（但被 `_aggregate` 排除，由 gate 的 status 检查捕获） | ⚠️ 覆盖但路径迂回（m-6） |
| F | 纯负样本上捏造 20 s | precision 0.0、FPsec 20.0 | ✅ 正确惩罚 |

**结论：6 组中 4 组完全正确，2 组存在可利用漏洞。** 这是一个**远比 Phase 1 可信**的 evaluator，但还不是防欺骗的。

## K / L / M 覆盖核验

| Case | 意图 | 构造 | 实际行为 | 评价 |
|---|---|---|---|---|
| **K** pure negative | 完全不含权利作品 | `life`（生命游戏）6.217 s | `MATCH_NOT_FOUND`，FP 秒数 0 | ✅ 真实覆盖 |
| **L** duplicate ambiguity | 跨集完全重复 | 同一 `shared.mp4`（mandelbrot 10 s）用**相同参数**拼进 EP04/EP05 | `AMBIGUOUS`，返回 2 个候选 | ⚠️ **覆盖了逐位重复，未覆盖真实的独立编码重复**（C-2） |
| **M** incomplete index | 索引不完整必须拒绝 | faststart MP4 截断至 45%，独立项目根目录 | `INCOMPLETE_INDEX`，无任何 segment；reason 精确到 "frame count 18 outside expected 40±2" | ✅ **构造和隔离都很扎实**（独立 DB、独立 workspace，不污染主项目） |

CASE_M 的设计特别值得肯定：`_run_incomplete_index_case` 建立完全隔离的项目环境，真实走一遍 index → analyze 流程，不是 mock。

## Gate 可复现性

在干净根目录 `python -m ipvm --root <tmp> benchmark --demo` 完整复现：

```
metric                              committed   reproduced  match
ground_truth_segments                      23           23   True
predicted_segments                         20           20   True
episode_recall                         0.8696       0.8696   True
episode_correctness                    1.0000       1.0000   True
source_aware_recall_at_1s              0.7826       0.7826   True
segment_precision_at_1s                0.9000       0.9000   True
false_positive_segments                     2            2   True
false_positive_seconds                 6.0000       6.0000   True
source_boundary_mae_seconds            0.2746       0.2746   True
boundary_missed_segments                    3            3   True
speed_ratio_mae                        0.0000       0.0000   True
gate.passed : False -> False | checks identical: True
```

**11/11 逐位一致，gate 判定与全部 6 项 check 完全相同。** 对比第一次审查中"唯一入库的 gate 证据是手工转写"的问题——`benchmark/results/phase1_5_gate.json` 现在是 `BenchmarkRunner.run()` 的直接产物，schema 与代码输出一致。**第一次审查的 M-4 完全闭合。**

## 这些数字代表什么、不代表什么

| 数字 | 代表 | 不代表 |
|---|---|---|
| `source-aware recall@1s 78.3%` | 在 23 条 off-grid 直接剪切/重编码上，集数正确且源端点均在 1 秒内的比例 | 真实案件准确率。语料是 3 段 11.5 秒的 `testsrc2`；失败高度集中在子帧相位 0.15–0.30 的样本 |
| `precision@1s 90.0%` | 20 条预测中 18 条满足 source-aware 判据 | 没有假阳性风险。2 条"FP"是**集数正确但源时间偏差 > 1 秒**，不是凭空捏造 |
| `episode correctness 100%` | 每一条与 GT 重叠的预测都命中了正确集数 | 集数识别可靠。A–J 子集同一指标是 81.8%（CASE_B 和 CASE_F 集数错） |
| `source boundary MAE 0.275s` | 在 20 条被召回样本上的源端点平均误差 | 整体精度。它**条件于集数正确**，且 gate 正确地拒绝以此单独放行（要求 miss = 0） |
| `A–J source-aware 16.7%` | 这一次抽样（相位 0.238）下的表现 | 变换鲁棒性。好相位下同样八种变换全部安全拒绝、零假阳性（M-3） |
| `speed-ratio MAE 0.05` | 全部 9 条集数正确样本的平均 | 变速能力。仅在两条真正变速的样本上误差是 0.225（M-5） |
| `peak RSS 133.3–182.8 MB` | 主进程与已回收 ffmpeg 子进程的 `getrusage` 生命周期峰值，分别记录并给出上下界 | 严格的同步瞬时峰值——文档已诚实声明此点 |

---

# Failure Taxonomy

## Phase 1 Gate（23 条 GT，5 条未召回）

```
                        ┌─ P1_004  相位 0.131 + 时长 3.408s ─┐
  Retrieval / 表示层  ──┤                                    ├─→ MATCH_NOT_FOUND
  （正确帧不在 top-8） └─ P1_009  相位 0.252 + 时长 3.352s ─┘

  Temporal alignment  ──┬─ CASE_A        ─┐  段被截断到 3.0s
  （offset 分桶截断）   └─ CASE_I 第一段 ─┘  → source_end 误差 2.98s
                                              → 既是 recall miss 也是 precision FP

  Ambiguity handling  ─── P1_012  两个 EP03 提案矛盾 → 整案清空 → AMBIGUOUS
  （按设计正确，代价是召回）
```

**三个 episode-level miss = P1_004 + P1_009 + P1_012。** 前两个是检索层（M-1），第三个是安全机制正确触发。

**两个 false positive = CASE_A + CASE_I 第一段**，同一根因（对齐截断），同一签名（`accused[0,3.0] → EP01 src[0.5,3.5]`，真值起点 1.238）。

**不能归因于"DCT 不够强"的证据**：失败样本的视觉相似度是 0.978–0.991，与成功样本无显著差异；`minimum_visual_similarity` 在整个语料上从不触发。真正的问题是**索引里没有 off-grid 的对应帧**，而不是描述子分不清两张不同的图。

## A–J Diagnostic（12 条 GT，2 条 source-aware 成功）

| Case | 预期能力 | 实际预测 | 失败阶段 | ADR-0002 能解决? | 需要 DINOv2? | 需要独立 crop/mirror 逻辑? | 阶段归属 |
|---|---|---|---|---|---|---|---|
| **A** off-grid 直接剪切 | Phase 1 应过 | EP01 src[0.5,3.5]，截断 | 检索 + 对齐 | 部分 | 部分（提高 off-grid 可分性） | 否 | **Phase 1，未达标** |
| **B** 水平镜像 | Phase 2 | **EP03 src[6.0,10.5]，GREEN** | 检索（错集数）+ tier | 否 | 否 | **是**（§13 候选期二次验证） | Phase 2 前期 |
| **C** 1.25× | Phase 2 | EP01 src[0,3]，slope 报 1.0 | 对齐（结构上限 ±0.167） | **是** | 否 | 否 | Phase 2 前期 |
| **D** 0.8× | Phase 2 | EP01 src[0,3.5]，slope 报 1.0 | 对齐（同上） | **是** | 否 | 否 | Phase 2 前期 |
| **E** 中心裁剪 | Phase 2 | EP01 src[0,3]（好相位下 GREEN 偏 10 秒） | 检索 + tier | 否 | **是**（patch token 抗裁剪） | 是（§14 空间验证） | Phase 2 中期 |
| **F** 竖屏裁剪 | Phase 2 | **EP05 src[14,18.5]**，错集数 | 检索 | 否 | **是** | **是**（宽高比重建） | Phase 2 中期 |
| **G** 字幕遮挡 | Phase 2 | EP01 src[0,3]，截断 | 检索 + 对齐 | 部分 | **是**（局部遮挡鲁棒性） | 否 | Phase 2 中期 |
| **H** 镜像+变速+字幕 | Phase 2 | `MATCH_NOT_FOUND` | 全链路 | 部分 | 是 | 是 | Phase 2 后期 |
| **I** 多集拼接 | Phase 1 应过 | EP01 段截断（FP），**EP02 段 GREEN 正确** | 对齐 | 部分 | 部分 | 否 | **Phase 1，部分达标** |
| **J** 插入无关画面 | Phase 2 | EP01 段截断，EP02 段正确，无关片段未误配 | 对齐 | 部分 | 部分 | 否 | Phase 2 前期，**piecewise 机制本身可用** |

**关键结论：**

1. **I/J 证明 piecewise 机制真实可用**——EP02 段在两个 case 中都被独立、正确地提取（CASE_I 的 EP02 段甚至是 GREEN 且正确）。第一次审查对 piecewise 的正面评价成立。
2. **C/D 的失败确认第一次审查 C-3 的结构性上限**（`|s−1| ≤ offset_bin/min_segment = 0.167`）。ADR-0002 直击此处。
3. **B/F 的失败是"错集数 + 高置信"**，不是"无输出"。这两条是 C-1 的直接证据。
4. **好相位对照（E10）显示所有变换 case 在正常抽样下都是安全拒绝**——所以 A–J 的 16.7% 更多是相位抽样问题（M-3），不是变换处理的稳定估计。

---

# ADR-0002 Review

## 该路线能否解决 constant-offset bucket 的结构缺陷

**能，但只解决其中一半，而且不是当前 Gate 失败的那一半。**

**能解决的部分（已用探针确认）**：变速。第一次审查证明常数 offset 分桶把速度容忍锁死在 0.81–1.19（要求 0.75–1.50）。slope-offset 二维投票让 slope 成为显式搜索维度，这个上限直接消失。CASE_C/D/H 的对齐层失败由此解决。

**不能解决的部分（本次审查的 M-1）**：off-grid 相位。探针 E4/E5 证明在失败相位下，**正确的源帧根本不在 top-8 候选里**（中位排名 42/80）。二维投票、DTW、LIS、graph alignment 都是在候选集上工作的算法：

> **投票需要选票。当正确的 (slope, offset) 组合在候选集中一张选票都没有时，任何投票方案都投不出它。**

因此：**ADR-0002 必要但不充分。** 它应该实施，但不应该排在第一位，也不应该被期待修复当前 78.3% 的 Gate 数字。

## 逐项回答

### slope / offset 离散化应如何设计

- **slope 轴**：不要在 slope 上均匀离散。人为的速度只有少数几个常用值（0.75/0.8/0.9/1.0/1.1/1.25/1.5/2.0），但也存在连续微调。建议 **对数均匀**：`slope ∈ exp(linspace(ln 0.75, ln 1.50, N))`，因为速度是乘性量，1.0→1.1 与 1.4→1.54 是同一"感知步长"。取 `N ≈ 33`（约 1.4% 步长），足以让 5 分钟片段的累计漂移小于半个采样周期。
- **offset 轴**：分辨率应等于**采样周期**（0.5 s），但**必须使用软投票**——每张选票同时投给相邻两个 bin，权重按到 bin 中心的距离线性分配。这直接消除本次发现的"相位落在 bin 边界导致票数对半劈"的失败模式，成本几乎为零。
- **不要**把 offset 范围设为整集时长的全量 bin（45 分钟 / 0.5 s = 5400 bin），见下条。

### 如何避免复杂度爆炸

投票空间 `N_slope × N_offset` 对每集是 33 × 5400 ≈ 18 万 bin，40 集就是 700 万 bin——如果对每个候选对都做完整累加，代价是 `n_candidates × N_slope`。

三条控制：
1. **不建稠密数组**。用 `dict[(episode, slope_idx, offset_bin)] → weight` 稀疏累加。实际非零 bin 数受 `n_candidates × N_slope` 上界约束（25 分钟被诉 × 2 FPS × top_k 8 × 40 集 × 33 slope ≈ 3200 万——仍然太大）。
2. **两阶段**：第一阶段只在 `slope = 1.0` 上投票（成本 = 候选数），取 top-M 峰值；第二阶段仅对这些峰值邻域展开完整 slope 搜索。绝大多数真实片段速度为 1.0，这让常见情形保持线性成本。
3. **成对投票代替单点投票**：不对单个 (accused, source) 候选投票，而是对**候选对**投票——两个候选 `(a1,s1)`、`(a2,s2)` 唯一确定一条 `slope = (s2−s1)/(a2−a1)`、`offset = s1 − slope·a1`。只取 `|a2−a1|` 落在 [1, 8] 秒窗口内的对，成本 `O(n × k²  × window)` 且天然自带 slope 精度。这是经典的 Hough 成对参数化，比在 slope 轴上扫描更省。

**建议采用方案 3 + 方案 2 的两阶段。**

### 如何从多条 motion line 中逐条提取 piecewise segment

标准做法是**迭代提取 + 支持移除**：
1. 取当前投票空间的全局峰值 → 该 (slope, offset) 的所有内点候选；
2. 按 accused 时间排序内点，用 `maximum_frame_gap` 切成连续 run；
3. 对每个 run 做局部 RANSAC 精修，得到 segment；
4. **把这些内点从候选池移除**（不是从投票空间减权——直接移除更简单也更稳），重建/增量更新投票；
5. 重复，直到峰值票数低于 `minimum_segment_seconds × coarse_fps × 阈值`。

停止条件建议用**绝对票数**（对应最短可接受片段的帧数），不要用相对于首峰的比例——否则一个很长的主片段会压制掉合法的短片段。

`peak suppression` 半径：offset 方向 ±1 bin，slope 方向 ±1 格，避免同一条线被当成多个峰。

### 如何处理多个 episode

**每集独立投票，最后统一竞争。** 不要把 episode 混进同一个投票空间——offset 的语义是集内时间，跨集没有可比性。

流程：每集独立跑迭代提取 → 汇总所有集的 segment 候选 → 按 accused 时间轴做**一次全局的区间竞争**（当前 `claimed_frames` 逻辑的位置）。这样 C-4a 类的跨集重复内容会自然表现为"多集给出票数相近的 segment"——**这正是歧义信号应该产生的地方**，比现在的 `match_score` 比值稳健得多（票数是整数计数，不是挤在 0.99 附近的浮点数）。这条与 C-2 的修复方向是同一件事。

### 如何处理同一 source 区间被多个 accused 区间重复认领

三层：
1. **提取期**：内点移除天然防止同一批候选被两次使用；
2. **精修期**：为每条 segment 记录其 source 区间；提取新 segment 时，若其 source 区间与已接受 segment 在同一集上重叠超过阈值（建议 50%），且两者的 accused 区间不重叠——则这是"同一段原片被声称使用了两次"。**合法情形确实存在**（UP 主重复播放同一片段），所以不应直接拒绝，而应标记 `source_reuse = true` 并降级 tier；
3. **报告期**：§31 的 `usage_ratio` 必须按 **source 区间的并集**计算，不是 segment 时长求和。这一点建议现在就写进 ADR-0002 的影响章节，否则 Phase 5 会重新踩坑。

当前 `_has_contradiction` 的第二条规则（同集、accused 不重叠、source 重叠 > 0.5 s → 矛盾）在做类似的事，但结论是"矛盾并清空整案"，过于粗暴（见 Q4）。

### temporal residual 应如何计算

当前是**内点残差的最大值**（`matching.py:193`，`np.max`）。建议改为**同时记录三个量**：
- `residual_rms` —— 拟合质量（连续量，适合做 tier 判据）；
- `residual_max` —— 最坏点（适合做矛盾检测）；
- `inlier_ratio` —— 内点数 / 该 run 的总候选帧数。

只用 max 的问题是它对单个离群点极度敏感，而 RANSAC 的 0.55 s 内点阈值又把 max 强制截断在 0.55 以内——所以现在的 `temporal_residual_seconds` 在几乎所有成功样本上都是 0.0（gate 报告中确实如此），**作为 GREEN 判据是无信息的**。这解释了为什么 C-1 里 CASE_B 能满足 `residual ≤ 0.55`：它恒等于 RANSAC 的内点阈值，不是独立信息。

**这一条应写进 ADR-0003 的修订，因为它是 C-1 的技术根因之一。**

### speed_ratio 应如何定义

建议明确定义为：

```
speed_ratio = d(source_time) / d(accused_time) = 拟合直线的斜率 a
```

即 **> 1.0 表示被诉视频加速播放**（同样长度的被诉时间覆盖更多原片时间）。当前实现的语义已经是这个，但**没有任何文档写明方向**，而"1.25 倍速"在中文语境里既可能指 a=1.25 也可能指 a=0.8。ADR-0002 应给出一个带具体数字的例子固定语义，并规定：

- `speed_ratio` 只在该 segment 的内点跨度 ≥ 某个最小值（建议 3 秒）时才可信，否则输出 `null`；
- 合并相邻 segment 时**不得对 speed_ratio 取算术平均**（当前 `_merge_adjacent:357` 这样做），应在合并后的完整内点集上重新拟合。

### 是否存在明显更合适的 DTW / LIS / graph alignment 方案

**没有。对本问题，Hough 式二维投票是正确的选择。** 逐一说明：

- **DTW** 假设两条序列**单调对应且完整覆盖**。被诉视频里权利作品只占一部分，中间夹杂 UP 主原创、表情包、其它作品——DTW 会被迫把这些也对齐到某处。需要大量改造（子序列 DTW + 多次运行 + 跳段惩罚）才能用，改造后本质上退化成一个更慢的分段搜索。**不推荐。**
- **LIS / 最长递增子序列**（视频指纹领域的常见做法）只能处理 `slope ≈ 1` 的单调匹配，无法给出速度估计，且对多段来自不同集的情形需要外层循环。**它是当前方案的变体，不是升级。**
- **Graph alignment / 最短路**（把候选建图、边权为时序一致性）表达力足够，但参数（边构造规则、权重）比 Hough 更难调，且失去了"票数"这个天然的、可解释的置信量。对律所可追溯场景，**"这条映射得到 47 票，第二名 12 票"比一个图算法的路径代价好解释得多**。
- **Hough 投票**的独有优势：多假设天然并行、票数可直接用作歧义信号（见上文"多个 episode"一节）、对离群点鲁棒、参数只有两个离散化步长。

**结论：ADR-0002 的路线是对的，不需要推倒重来。** 建议补充的只有：软投票（消除 bin 边界效应）、成对参数化（控制复杂度）、票数作为歧义与置信信号（与 ADR-0003 打通）、residual 三元组、speed_ratio 语义与合并规则。

---

# ADR-0003 / 0004 / 0005 Review

## ADR-0003：Match Score / Status / Ambiguity

**Status 语义**：✅ 合理。五种状态覆盖完整，`AMBIGUOUS` 与 `INCOMPLETE_INDEX` 的引入是正确的架构判断——它们表达的是"系统知道自己不知道"的两种不同原因，下游可以区别处理。`ERROR` 与 `MATCH_NOT_FOUND` 分离也正确。

**`match_score` 与 auto-release gate 是否真正解耦**：✅ **是，且已证明有效。** 探针 E8 的 CRF 案例中，`match_score = 0.9986`（远超 0.95），但 `uniqueness_margin = 0.0001 < 0.05` 使其停在 YELLOW。**如果沿用 Phase 1 的单分数逻辑，这会是一个 GREEN 的错误答案。** 这是 Phase 1.5 最有价值的一处设计。

**但解耦不完整**（C-1）：五个条件全是内部自洽性，其中 `temporal_residual ≤ 0.55` 恒等于 RANSAC 的内点阈值因而无信息（见 ADR-0002 Review），实际起作用的只有 `margin`、`coverage`、`temporal_score` 三条。对系统性变换（镜像）产生的自洽错误匹配，三条全部满足。

**Ambiguity 阈值**：❌ **裕度不足**（C-2）。`0.99999` 与真实观测到的 `0.9999` 只差一个数量级。ADR 已声明需要 golden set 校准，但建议**换掉机制而不是调参**：把跨集重复检测移到**索引期**（建索引时做一次集间自相似扫描，标记重复区间），查询命中标记区间时无条件 `AMBIGUOUS`。这不依赖任何浮点阈值，且成本一次性。

**未评估事实写 null**：✅ 正确且已全链路落实。

## ADR-0004：Project-scoped Original Assets

**决策**：✅ 正确，直接闭合第一次审查的 M-7。

**Episode 权威来源**：✅ 实现严格。`episode_from_path` 只接受 `S01E01` / `EP01` / `E01` / `第01集` 四种明确模式，多解或无解一律返回 `None` → `NEED_METADATA`。我验证了 `S01E01` 不会同时匹配出 `S01E01` 和 `E01`（负向后顾 `(?<![A-Z0-9])` 生效）。**不从任意数字猜集数**这一条真正做到了。

**Migration 安全性**：⚠️ **不原子**（M-6）。逻辑正确、有测试覆盖（`test_legacy_schema_migrates_match_score_and_project_scope`），但 `executescript` 的隐式提交使整个迁移分裂成多个事务。建议在 Phase 2A 之前改为显式事务，或至少在迁移前自动备份 `.db`。

**旧索引强制 INCOMPLETE_INDEX**：✅ 这是正确的保守选择，且 `SCHEMA-V2.md` 给出了明确的用户操作（`index-originals --force`）。

## ADR-0005：Benchmark Metric Definitions

**指标定义**：✅ 定义正确、实现与定义一致、四个指标互不复用同一表达式（我逐一核对了代码路径）。"禁止复用同一个表达式"这条约束写进 ADR 是好做法。

**False Positive**：✅ `_union_seconds` 正确处理重叠区间的并集；pure negative 的 0 秒要求已进 gate。

**Boundary Error**：✅ accused / source 分开报告；`boundary_missed_segments` 与 MAE 并列进 gate 条件（`MAE ≤ 1.0 AND missed == 0`），**明确堵死了"靠过滤失败样本改善 MAE"这条路**。

**Ground Truth off-grid**：✅ 已落实并验证。

**是否足以指导 Phase 2 优化**：⚠️ **基本够用，但有三处需要补：**
1. **重复预测不受惩罚**（M-4 漏洞 1）——Phase 2 引入更多候选后，这个漏洞会被更频繁地触发；
2. **accused/source 跨度自洽性未校验**（M-4 漏洞 2）——Phase 2 引入变速后，斜率就是核心输出，evaluator 必须能识别荒谬的斜率；
3. **缺少"tier 正确性"指标**——C-1 的问题（GREEN 放行错误答案）在当前所有指标里**完全不可见**。建议新增：
   - `green_precision` = GREEN 档预测中满足 source-aware 判据的比例；
   - `green_recall` = 满足判据的预测中被判 GREEN 的比例；
   - gate 条件：**`green_precision == 1.0`**（GREEN 意味着自动写入 Excel，一条错误就不可接受）。

   用现有 diagnostic 数据算：`green_precision = 1/2 = 50%`。**这个数字应该出现在报告里。**

**指标覆盖度小结**：召回、精度、集数、边界、假阳性、歧义、索引安全七个维度已覆盖；缺 **tier 校准**这一维，而它恰好是 C-1 所在的位置。

---

# Phase 2A Readiness

## 直接回答：**不应该**先按 ADR-0002 开始 Temporal Alignment v2

理由是探针 E4/E5 的实测结果：在当前 Gate 失败的样本上，**正确的源帧不在候选集里**（相位 0.20 时 top-8 命中率 0/10，正确帧中位排名 42/80）。ADR-0002 的全部机制都在候选集之上工作，因此它**无法改善 78.3% 这个数字**。先做它会得到一个令人困惑的结果：架构变更完成、复杂度大增、Gate 数字几乎不动。

同时必须说明范围：这个测量是在 `testsrc2` 上做的，该语料的描述子动态范围异常窄（整集帧对 cosine 挤在 0.9494–0.9999）。**真实剧集上这个结论可能不成立。** 所以第一步不是改代码，是测量。

## 建议的 Phase 2A 顺序

**2A-0　先测量（约 1 天，不改生产代码）**

在真实素材（哪怕 2 集 45 分钟的公开影视片段）上测一个数字：**retrieval recall@k**——把 off-grid 剪出的片段逐帧查询，统计"正确源帧出现在 top-k 中"的比例，k = 1/8/32/128。

这一个数字决定后面所有事：
- 若 recall@8 已经很高（真实内容的低频结构差异大）→ **瓶颈是对齐层，直接做 ADR-0002**；
- 若 recall@8 低而 recall@128 高 → **提高 `top_k` 即可**，几乎零成本；
- 若 recall@128 也低 → **瓶颈是表示层，必须先换 backend 或加密索引采样**。

**这是整个 Phase 2 中投入产出比最高的一个实验，且不需要任何架构决策。**

**2A-1　按 2A-0 的结果二选一（约 3–5 天）**

- 路线甲（若瓶颈在采样）：**非对称采样**——原片索引按 4–8 FPS 建，被诉视频仍按 2 FPS 查询。这保证任意被诉帧都能在索引中找到 0.0625–0.125 秒内的对应帧，直接消除 off-grid 空洞。成本：索引体积和建库时间 ×2–4（DCT 144 维，30 小时剧集在 4 FPS 下约 250 MB，可接受）。**这是对 §9 的修改，需要 ADR（见 Q3）。**
- 路线乙（若瓶颈在描述子）：提高 `top_k` 并加入 §9 已规定但未实现的 **8 FPS 候选区间精对齐**——先用粗索引定位候选区间，再对该区间重抽帧做精细匹配。这同时改善召回和边界精度。

**2A-2　ADR-0002 Temporal Alignment v2（约 1–2 周）**

在 2A-1 之后单独实施。ADR-0002 自己写了"不应与视觉 backend 切换同时实施，否则无法归因指标变化"——同样的纪律也适用于 2A-1：**一次只改一层。** 完成后 CASE_C/D/H 的对齐层障碍消除，速度容忍范围可覆盖 §16 的 0.75–1.50。

**2A-3　闭合 C-1：给 GREEN 加一条外部验证（约 2–3 天）**

最小可行方案：对每条候选 GREEN 的 segment，按 8 FPS 从**原片**重抽该 source 区间的帧，与被诉帧逐帧比对，要求平均相似度高于一个在 golden set 上标定的绝对阈值。这是外部检查（拿真实像素验证结论），不是内部自洽。同时按 §13 做镜像二次验证——对 DCT 描述子，水平翻转在频域有解析关系（奇数列系数变号），**几乎零成本**，这也是保留 DCT 层的一个额外理由。

**2A-4　之后才是 DINOv2 / crop / 字幕 / audio**，顺序沿用第一次审查的 R-5 ~ R-9。

## Phase 1.5 关闭条件

| | 条件 | 对应 |
|---|---|---|
| 1 | GREEN 增加外部有效性检查，或在 Phase 2 变换检测落地前禁用 GREEN | C-1 |
| 2 | Ambiguity 检测改为索引期跨集重复标记（或提供 golden-set 校准后的阈值 + 在非 AMBIGUOUS 路径上也填充 `alternatives`） | C-2 |
| 3 | evaluator 补 `green_precision` 指标并进 gate（要求 = 1.0） | M-4 / ADR-0005 |
| 4 | evaluator 修复重复预测与斜率自洽性两个漏洞 | M-4 |
| 5 | A–J 各 case 使用独立抽样的源区间 | M-3 |

**1 和 2 是硬条件**（safety charter 内）。**3、4、5 可以与 Phase 2A 并行**，但必须在 Phase 2A 的第一次 gate 运行前完成，否则 Phase 2A 的调优仍然看不见 tier 错误。

---

# Questions for Architecture Owner

**Q1（最高优先级）　GREEN 除内部自洽外还必须满足什么外部条件？**

committed 的 diagnostic 中 2 个 GREEN segment 有 1 个集数与时间轴都错（CASE_B 镜像 → EP03）。ADR-0003 的五个条件全部是内部自洽度量，其中 `temporal_residual ≤ 0.55` 因恒等于 RANSAC 内点阈值而无信息。请裁定：
(a) GREEN 增加外部验证（8 FPS 源区间反向比对 + 镜像二次验证）；
(b) GREEN 要求变换检测已运行且为阴性（即 Phase 2 之前 GREEN 不可用）；
(c) Phase 2 完成前全部降为 YELLOW。
**在裁定之前，`review_tier=GREEN` 不应被任何下游模块解释为"可自动写入 Excel"。**

**Q2　跨集重复检测：继续调阈值，还是改为索引期预计算？**

`ambiguity_score_ratio = 0.99999` 只能捕获逐位相同的重复；一次 CRF 变化（0.9999）即失效，而真实剧集每集都是独立编码。建议改为**索引期跨集自相似扫描 + 重复区间标记**，查询命中标记区间无条件 `AMBIGUOUS`，不依赖浮点阈值。这是对 §8 索引层的新增职责，需要架构决策。
附带问题：在非 `AMBIGUOUS` 但 `margin` 很低的路径上，是否也应填充 `alternatives` 并写入 `is_primary=0` 的数据库行？（当前只有 audit JSON 有竞争者，DB 查不到。）

**Q3　是否批准非对称采样（原片索引 4–8 FPS，被诉查询 2 FPS）？**

§9 规定"粗检索 2 FPS + 候选区间 8 FPS 精对齐"。本次测量显示，off-grid 失败发生在**粗检索阶段**——候选区间根本没被正确定位，精对齐无从谈起。非对称采样把 §9 的两级策略改为"索引密、查询疏"。成本：索引体积与建库时间 ×2–4。这是对 §9 的实质修改，需要 ADR。
**建议先做 2A-0 的真实素材测量再决定。**

**Q4　矛盾检测应该清空整案，还是只降级涉事片段？**

`matching.py:285`：任意一对矛盾 → `segments=[]` → 整案 `AMBIGUOUS`。P1_012 因此丢失了它唯一的 GT。在真实多段混剪中，第 7、8 段矛盾会让前 6 段正确结果一并消失。
请裁定：(a) 保持整案清空（最保守）；(b) 只把涉事的两条降级为 YELLOW/alternatives，保留其余；(c) 整案状态设为 `AMBIGUOUS` 但仍返回未涉事的 segment。
**我倾向 (c)**：状态诚实地表达"本案存在无法调和的证据"，同时不丢弃已确认的部分。但这改变 `AMBIGUOUS` 的语义（从"没有结论"变成"有部分结论"），属于 ADR-0003 的修订。

**Q5　A–J diagnostic 是否重新生成为各自独立抽样？**

当前 A–H 共用 `first_start = 1.238`（子帧相位 0.238，接近最坏值），导致 16.7% 这个数字主要反映一次抽样运气而非变换鲁棒性。对照实验显示好相位下同样八种变换全部安全拒绝、零假阳性。
建议：A–J 各自独立抽取源区间，并对每种变换重复 3–5 次不同抽样。这会让 A–J 成为可用于 Phase 2 追踪的时间序列。

**Q6　`index-originals` 的全有或全无策略是否符合实际操作？**

`input/originals/` 中任意一个文件命名不规范或解码失败，都会让整个项目变成 `INCOMPLETE_INDEX` 并阻断所有分析。安全姿态正确，但律所现场常有杂项文件混入。
请裁定：(a) 维持现状；(b) 允许"部分完整"状态——已完整的集数可参与匹配，但每条结果都必须携带"索引覆盖 N/M 集"的警告并禁止 GREEN；(c) 提供显式的 `--allow-partial` 开关。

**Q7　`speed_ratio` 的方向语义与最小可信跨度**

当前实现的语义是 `speed_ratio = d(source)/d(accused)`，即 > 1.0 表示被诉视频加速。文档中没有任何地方写明方向，而中文"1.25 倍速"存在歧义。请在 ADR-0002 中固定语义并规定：内点跨度小于多少秒时输出 `null` 而非一个不可信的斜率；合并相邻 segment 时是否禁止对 `speed_ratio` 取算术平均（当前 `_merge_adjacent:357` 在做算术平均，我建议改为在合并后的完整内点集上重新拟合）。

---

# 附录 A：工程质量与性能

## 代码量：`6370 行`是误导性数字

`git diff --stat HEAD~1` 显示 6370 行新增，但基线 commit `71e8d93` 中 `src/`、`tests/`、`benchmark/`、`config/` 全部未入库。**实际的 Phase 1.5 增量：**

| 文件 | Phase 1 | Phase 1.5 | 增量 |
|---|---:|---:|---:|
| `benchmarking.py` | 308 | 692 | **+384** |
| `database.py` | 228 | 472 | **+244** |
| `matching.py` | 265 | 492 | **+227** |
| `indexer.py` | 91 | 211 | **+120** |
| `cli.py` | 199 | 223 | +24 |
| `media.py` | 115 | 132 | +17 |
| 其余 7 个文件 | 382 | 407 | +25 |
| **src 合计** | **1588** | **2629** | **+1041** |
| tests | 219 | 379 | +160 |

其余 5100 行是：先前未入库的 Phase 0/1 代码（约 1588）、**机器生成**的 gate JSON（1981 行数据，非代码）、README（176）、docs（约 1100）。

**结论：约 1200 行的真实实现增量，换来 8 项 measurement 缺陷闭合 + 5 项 safety 机制 + schema v2 + 4 份 ADR。这不是过度工程化。**

## 是否存在复制粘贴 / 重复逻辑

- `evaluate_segments`（逐案）与 `_aggregate`（聚合）看似重复，但**实现方式正确**：聚合层从 `raw_*_boundary_errors` 数组**逐边界重算**，而不是对逐案均值再取均值。我独立重算 source MAE = 0.274600，与报告逐位一致。这正好修复了第一次审查的 M-19。
- `_run_incomplete_index_case` 中重建了一套隔离的 config/database/indexer/matcher——这是必要的隔离，不是复制。
- 未发现明显的复制粘贴。

## 新增的 Critical / Major regression

**无。** 逐项核对第一次审查的 20 项发现：

| 修复 | 未修复 |
|---|---|
| C-2a/b/c/d/e（evaluator 五项）、M-2（网格）、M-4（手工 baseline）、M-5（`mirrored/cropped`）、M-7（episode UNIQUE）、M-8（audit 完整性）、M-9（JSON）、M-11（死配置）、M-12（部分：fps 回退）、M-13（stderr 死锁）、M-14（generator 泄漏）、M-15（fps 回退）、M-17（duration 静默 0）、M-16（encoder 缓存键）、M-19（聚合口径）、M-18（报告路径） | M-10（`frame_features` 只写不读）、M-3 内存中间列表（性能，Phase 1.5 不要求）、C-1（**收窄但未闭合**）、M-6（重叠矛盾：**运行期已加检测**，evaluator 层仍不度量） |

新引入的问题只有 M-6（迁移非原子）和 m-2（缓存键不含采样参数），均为 Major/Minor 级，无 Critical regression。

## 性能测量

| 项 | 评价 |
|---|---|
| 主进程 + ffmpeg 子进程 | ✅ `resource_snapshot()` 同时读 `RUSAGE_SELF` 与 `RUSAGE_CHILDREN`，分别报告并给出 lower bound（max）与 conservative upper bound（sum）。**第一次审查 M-3 的核心问题（46.5 MB 不含 ffmpeg）已闭合。** 文档明确声明 getrusage 是生命周期峰值而非同步瞬时峰值——诚实。 |
| cold / warm 定义 | ✅ `cold_assets` / `warm_cache_assets` 分别计数；`--warm-index` 开关明确；cold 0.427 s vs warm 0.132 s 的对比有意义。 |
| 外推纪律 | ✅ `normalization_warning` 字段直接写进 JSON："must not be extrapolated to real 30-hour drama indexes"。**第一次审查 M-3 的"13.55 秒/小时"式外推已从报告中彻底移除**，改为报告原始 scope（"5 assets / 82.5 media seconds / 0.427 s"）。这是正确的做法。 |
| `rtf` 0.0149 | 分析实时率，定义清晰。但同样是在 144 帧级别的索引上测得，不可外推——文档已声明。 |

## 只读探针清单

全部在 scratchpad 运行，调用未修改的 `ipvm` 模块。

| 编号 | 目的 | 关键结论 |
|---|---|---|
| E1 | 子帧相位扫描（纯直接剪切） | ❌ recall 40%，完全由相位决定 |
| E2/E2b | 定位失败阶段 | ❌ 视觉分 0.978+ 正常，offset 分桶碎片化，0 个提案存活 |
| E3 | 排除替代解释（语料/bin 宽度/采样率） | 更有辨识度的语料**更差**；加宽 bin 无效；提高 FPS 有限改善 |
| E4 | 正确帧是否在 top-8 | ❌ 相位 0.20 时命中率 **0/10** |
| E5 | 正确帧的全库排名 | ❌ 中位排名 **42–44 / 80** |
| E6 | 描述子动态范围 | ❌ 整集帧对 cosine 挤在 0.9494–0.9999；0.82 阈值从不触发 |
| E7 | 6 组对抗性 evaluator 输入 | 4 组正确、2 组漏洞（重复预测、荒谬斜率） |
| E8 | ambiguity 阈值鲁棒性 | ❌ CRF 差异使 ratio 降至 0.9999，歧义检测失效、集数报错 |
| E9/E9b | 缓存键覆盖 | ⚠️ 采样参数不在键内；对当前编码器影响可忽略 |
| E10 | A–J 相位对照 | 好相位下变换案例全部安全拒绝、零假阳性；坏相位下出现 GREEN 级错误 |
| repro | 干净根目录复现 gate | ✅ 11/11 指标逐位一致，gate 与 6 项 check 完全相同 |
