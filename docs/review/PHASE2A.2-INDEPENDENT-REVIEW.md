# Phase 2A.2 — Integrity & Boundary Semantics 独立审查

- 审查对象：`80036d8`（`Complete Phase 2A.2 integrity and boundary closure`）
- 上游基线：`c512a80`（`Complete Phase 2A.1 calibration and safety closure`）
- 上一轮裁定：`docs/review/PHASE2A.1-INDEPENDENT-REVIEW.md` = **FAIL**
- 审查日期：2026-09-04
- 总体裁定：**CONDITIONAL PASS**

## 1. 结论先行

上一轮的四项阻断项**全部关闭**，并且每一项都由本审查独立复现，而不是采信 completion
report：

1. **Critical（v4→v5 假升级）已关闭。** 我用 `2e8294f`/`a24ee39`/`3f5f4d5`/`c512a80` 各自
   的代码构造了真实的 v2/v3/v4/v5 数据库，并复现了真实的 poisoned v5（用 `c512a80` 升级
   真实 v4：`user_version=5`、36 列、两个 v5 列不存在）。当前 `initialize()` 对全部五种
   输入都能升到 v6、保留旧 row、写入新字段。三处 failure injection 全部干净 rollback。
2. **Blocker（系统性错误时间线）已关闭为安全语义。** E001-I008 四例仍在 functional
   taxonomy 中记 `UNSAFE_WRONG_MATCH`，但生产输出已不再断言完整时间线：它输出可确认的
   `2.5–6.0`、`boundary_completeness=INCOMPLETE`、附原因、candidate YELLOW、
   `REVIEW_REQUIRED`。
3. **Major（rival compatibility 只比 `source_start`）已关闭。** 上一轮记录的两个端到端
   反例现在结论完全反转。全部 44 对 primary–alternative（按 completion report 的
   predictions×alternatives 口径则是 54 对）中，**0 对**是同线近等价。
4. **Major（benchmark safety assertions 不足）基本关闭。** 我自行枚举 1824 个 ideal
   candidate timestamp，**0 个**不在 0.5 秒 source grid 上；tracked artifact 与 fresh run
   除 runtime 外完全一致；real 120 case 的四分类由我独立重算，逐案与 artifact 一致。

Change-point refinement 是本轮真正的新能力，并且通过 ablation 证明它是**一般能力而不是
只修好已知 testcase**：关闭该开关后，`1.0→1.25` 的边界跑到 17.5（GT 12.0）、
`0.8→1.25→1.0` 中段变成 `a=1.1883` 的混合段、四段用例出现两个混合段；打开后 2/3/4 段、
双向、加噪声全部正确。

未给 PASS 的原因只有两条，两条都窄、都属于 Temporal Alignment 本阶段可修范围，且都不需要
碰任何被禁止的区域：

- **M-1（Major）**：`_merge_adjacent()` 的 source 连续性阈值是硬编码 1.5 秒且**不随
  slope 缩放**，所以速度 ≥ ~1.2 时，一条真实单线被 1.5 秒 retrieval hole 打断后会输出两个
  片段；而 `_classify_boundary_completeness()` 的"相邻已输出 segment 不算 orphan"排除项
  **没有做同线判断**，于是这两个片段互相屏蔽对方的 orphan 证据，双双得到
  `candidate GREEN + boundary_completeness=UNKNOWN`。这正是本阶段目标句
  "不能把 partial match 伪装成完整时间线"要防的形态。
- **M-2（Major）**：ideal benchmark 的 `passed` **只**由 gating aggregate 的
  recall/precision 计算，`behavioural_assertions` 完全不参与——包括
  `speed_discontinuity_source_aware`，即关闭上一轮 change-point blocker 的那一条。断言被
  诚实报告了，但没有接进 Gate。

其余全部剩余问题（短 regime 吞并、多 regime 退化、synthetic ambiguity、coherent wrong
line、`FULL` 无证据规则、out-of-range ±0.025 量化带）经独立测量后，**均不属于本阶段必修**，
理由逐条列在 §17。

---

## 2. 审查范围与方法

完整阅读：Architecture baseline 与 `ARCHITECTURE-REVISION-0.1.1.md`、ADR-0002/0003/0005、
`SCHEMA-V5.md`/`SCHEMA-V6.md`、`PHASE2A.1-INDEPENDENT-REVIEW.md`、
`PHASE2A.2-INTEGRITY-BOUNDARY-CLOSURE-REPORT.md`，以及 `c512a80..80036d8` 全部 diff
（23 文件，+6686/−366）。

本审查**未修改 production code、未修改 tests、未修 bug、未 commit、未 push**。审查结束时
工作树仅有用户既有的 `.DS_Store` 修改与未跟踪的
`PHASE2A.1-DS-AUXILIARY-VERIFICATION.md`；`git diff HEAD -- src/ tests/ config/ docs/` 为空。
所有 probe、fixture 与 scratch DB 均在 `/private/tmp` 会话目录中。

独立执行与结果：

| 检查 | 独立结果 |
|---|---|
| `pytest -q` | **107 passed in 6.58s** |
| migration focused（`test_database.py`） | 13 passed，2 deselected |
| genuine v2 / v3 / v4 / v5 fixture 升级 | 4/4 成功，旧 row 保留，新字段可写 |
| 真实 poisoned v5 复现 + 修复 | 复现成功，修复成功 |
| 真实 legacy `user_version=0`（用户 workspace 副本） | 升级成功 |
| migration failure injection | 3/3 干净 rollback |
| temporal-line compatibility A–F | 6 组、24 个断言全部符合预期 |
| change-point adversarial | 7 组、40+ 构造 |
| boundary completeness A–D + false-INCOMPLETE C1–C7 | 全部符合预期，1 处例外（M-1）|
| retrieval attribution | 12 帧全部 4095 个子集穷举验证 |
| outcome taxonomy | 独立重算 120 case，与 artifact 完全一致 |
| ideal benchmark | fresh run；1824 candidates 独立枚举 |
| real benchmark | 2 集 × 10 位置 × 6 profile = 120 case，runtime 32.16 s |
| synthetic A–M | 73 case（`80036d8`）+ 73 case（`c512a80` 对照）|
| pure noise | 12/12 配置 0 segment |
| 性能 scaling | change-point 开/关对照，5 组规模 |

真实媒体、文件名、hash、绝对路径未写入本报告。

---

## 3. Scope / baseline audit

| 项目 | 结论 | 证据 |
|---|---|---|
| sparse voting 主体 | **未重写** | AST 级函数对比：`_sparse_peaks`、`_support_for_peak` 与 `c512a80` 逐字节相同 |
| robust fitting 主体 | **未重写** | `_robust_fit`、`_proposal_to_segment`、`_continuous_runs`、`_fit_quality` 逐字节相同 |
| hypothesis extraction | **未重写** | `_extract_episode_segments` 唯一改动是 `return` 行加了 refinement 调用 |
| piecewise architecture | 未改 | 无结构性 diff |
| DCT backend | **未变** | `features.py`、`media.py`、`indexing.py`、`benchmarking.py`、`retrieval_benchmark.py` diff 为空 |
| source / query FPS | 仍 2.0 | `config/default.yaml` `coarse_fps: 2.0` 未改 |
| retrieval top-k | 仍 8 | `top_k: 8` 未改 |
| GREEN auto-release | 仍禁用 | 三份 artifact 共 120+17+73 case，`review_tier` 集合 = `{REVIEW_REQUIRED}` |
| VDF / DINOv2 / SigLIP / FAISS | 未引入 | 全树 grep：唯一 `faiss` 出现在 `doctor.py` 的可选模块探测（既有） |
| crop / mirror / subtitle / PiP / audio / ASR / Gemma | 未引入 | 全部命中都在 `benchmarking.py` 的 synthetic 变换*生成器*、v1 起就有的 DB 列、`audit.py` 的 `not_implemented` 列表、`doctor.py` 探测 |
| Excel / 网页抓取 | 未引入 | `openpyxl`/`xlsx`/`requests`/`selenium` 全树 0 命中 |
| 真实媒体 / hash / 绝对路径 | 未进入 commit | commit 只含 23 个源码/文档/config/artifact 文件 |
| `.DS_Store` | **未被误提交** | 仍是工作树中的未暂存修改 |
| `PHASE2A.1-DS-AUXILIARY-VERIFICATION.md` | **未被误提交** | 仍未跟踪 |

新增的 6 个函数全部集中在 change-point、boundary completeness 与 compatibility：
`_ordinary_line_cost`、`_candidate_path_for_change_point`、`_refine_change_point_pair`、
`_refine_adjacent_change_points`、`_classify_boundary_completeness`、`_same_temporal_line`。
改动的 6 个函数：`_candidate_tier`、`_conflict_graph`、`_extract_episode_segments`、
`_lines_stay_consistent`、`align_with_diagnostics`、`analyze_file`。**无 scope creep。**

config 只删除了两个上一轮指出的死键（`explanation_span_saturation_seconds`、
`maximum_temporal_residual_seconds`），新增 4 个 change-point 键。

---

## 4. Schema migration — 独立复现

### 4.1 fixture 构造

用各自 commit 的代码创建真实数据库并写入一条真实 case row，再用当前代码升级：

| fixture 来源 | 构造版本 | segment 列数 | row |
|---|---:|---:|---:|
| `2e8294f` | v2 | 23 | 1 |
| `a24ee39` | v3 | 28 | 1 |
| `3f5f4d5` | v4 | 36 | 1 |
| `c512a80`（fresh） | v5 | 38 | 1 |
| `c512a80` 升级真实 v4 | **poisoned v5** | **36（缺两个 v5 列）** | 1 |
| 用户 workspace 中的真实 legacy 副本 | `user_version=0` | 16 | 0 |

poisoned v5 的复现结果为 `{"user_version": 5, "n_cols": 36, "has_v5": [], "rows": 1}`——
**上一轮的 Critical 在 `c512a80` 上确实存在，且我复现了它。**

### 4.2 升级结果

| 输入 | `initialize()` | 升级后 | 旧 row | backup | 写新 row |
|---|---|---|---|---|---|
| genuine v2 | ok | uv=6，41 列 | 保留 | `.pre-v6.bak` uv=2 | ok |
| genuine v3 | ok | uv=6，41 列 | 保留 | `.pre-v6.bak` uv=3 | ok |
| genuine v4 | ok | uv=6，41 列 | 保留 | `.pre-v6.bak` uv=4 | ok |
| genuine v5 | ok | uv=6，41 列 | 保留 | `.pre-v6.bak` uv=5 | ok |
| **poisoned v5** | ok | uv=6，41 列 | 保留 | `.pre-v6.bak` uv=5/36 列 | ok |
| real legacy uv=0 | ok | uv=6，41 列 | 无 row | `.pre-v6.bak` uv=0 | ok |

写回验证（每一例）：
`speed_out_of_range=1`、`unconstrained_speed_ratio=1.62`、
`boundary_completeness='INCOMPLETE'`、`segment_cap_truncated=1`、
`accused_videos.truncated_episodes_json='["EP01"]'`、
`analysis_warnings_json=[{"code":"ALIGNMENT_SEGMENT_CAP_REACHED"}]`。
`integrity_check=ok`。旧的 `.pre-v4.bak` 命名不再产生。

结构等价性：v2 与 v4 升级后的 table 集合与 index 集合与 fresh v6 **完全相同**（缺失集合为
空），segment 列数同为 41。

### 4.3 Failure injection

在各 migration step 的 checkpoint 注入异常：

| 注入点 | 输入 | `initialize()` | 升级后 uv | 列 | row | backup |
|---|---|---|---:|---|---:|---|
| `v5_speed_range_added` | genuine v4 | 抛出 | **4**（未虚假前进） | 仍 36，无 v5/v6 | 保留 | 存在 uv=4 |
| `v6_boundary_completeness_added` | genuine v5 | 抛出 | **5** | 仍 38，v5 完整、无 v6 | 保留 | 存在 uv=5 |
| `schema_repaired` | poisoned v5 | 抛出 | **5** | 仍 36，仍缺 v5 | 保留 | 存在 uv=5 |

三例均**无半升级、无虚假版本前进、无数据丢失**。

### 4.4 对抗输入

| 输入 | 行为 |
|---|---|
| `user_version=7`（高于代码） | `SchemaIntegrityError: Database user_version 7 is newer than supported version 6`，库原样不动 |
| uv=6 但 `segment_matches` 表缺失 | 先写 backup，再抛 `SchemaIntegrityError: ... required tables are missing`，**不猜测重建** |
| uv=6 但缺 v6 列（poisoned v6） | 检出 → backup → 原子 repair → 41 列，uv 保持 6 |
| uv=4 但 `original_assets` 表缺失 | 重建表与 index，升到 v6，41 列，旧 row 保留 |

### 4.5 残留缺口（Minor，非 Critical）

`REQUIRED_COLUMNS` / `_required_columns_for_version()` 只定义了 **v4 及以后**的列不变量。
因此把一个 v2 形状的库人为盖上 `user_version=3/4/5/6` 时，当前代码会补齐 v4/v5/v6 列并盖到
v6，但 v3 引入的列（`candidate_tier` 等）既不被 repair 也不被响亮拒绝，最终在写入时才失败：

```text
stamped uv=3 -> final uv=6, 36 cols, candidate_tier missing,
   write_new_row = OperationalError: table segment_matches has no column named candidate_tier
```

这与上一轮 Critical 的失败签名同类，但**低一级**。我确认这不是任何已发布代码能产生的状态：
`a24ee39` 的 `initialize()` 在同一个事务里先做 v2→v3 重建再盖 `PRAGMA user_version = 3`，
不存在只盖版本不重建的路径。因此这是一个**防御性缺口而不是现存回归**，与
`database.py:283` 注释"a database can claim a version it does not structurally satisfy"的
承诺范围不符（承诺是全量，实现是 v4+）。

### 4.6 一处与 completion report 不符的事实

closure report §3 写："真实 benchmark 使用源 DB 的 scratch copy。源 DB fresh 检查前后均为
v4、2 assets；没有对真实 DB 执行 initialize 或 migration。"

对 `workspace/real-retrieval/db/ipvm.db` 这一句**准确**：只读检查显示它仍是 `uv=4`、36 列、
2 assets，mtime `2026-09-03 14:03`，早于 Phase 2A.2 的工作。

但被 `config/default.yaml` 指向的默认库 `workspace/db/ipvm.db` **确实被当前代码迁移过**：
它现在是 `uv=6`/41 列，且存在 `ipvm.db.pre-v6.bak`（mtime `2026-09-03 23:48`），该 backup 的
状态是 `uv=5`/36 列——即 **poisoned v5**。`workspace/benchmark_demo/*.db` 同样在 `23:52`
被从 v4 迁到 v6。

我核对了数据风险：`workspace/db/ipvm.db` 及其全部 backup 都是 0 assets / 0 accused /
0 segments / 1 project，不含任何案件数据。所以**没有真实数据风险**，而且这反而构成
poisoned-v5 修复路径在真实数据库上成功执行的额外证据。但 report 那句话的措辞比证据支持的
范围更宽，应更正为"未对 `real-retrieval` 源索引库执行 initialize"。

**§4 结论：v4/v5/v6 migration Critical 对任何已发布代码能产生的旧库都已彻底关闭。**

---

## 5. Temporal-line compatibility

### 5.1 直接判据（reviewer 自建 proposal，绕开现有 tests）

grid step = 0.5 s，budget = `merge_line_divergence_grid_steps × frame_interval` = 0.5 s。

| 组 | 构造 | divergence | compatible | 期望 |
|---|---|---:|---|---|
| A | `source=100+accused`，acc[0,10] vs acc[2,12] | 0.000 | True | ✔ |
| A | 同映射，acc[0,10] vs acc[20,30] | 0.000 | True | ✔ |
| A | 同映射，acc[0,10] vs acc[100,110] | 0.000 | True | ✔ |
| B | 同起点，slope 1.0 vs 1.25 | 2.500 | False | ✔ |
| B | 起点差 0.5 s，slope 1.0 vs 1.25 | 2.375 | False | ✔ |
| C | 同线 + intercept 抖 0.1/0.25/0.4/0.49 s | ≤0.49 | True ×4 | ✔ 不制造歧义 |
| C | 同线 + intercept 抖 0.51/0.6/1.0 s | ≥0.51 | False ×3 | ✔ |
| D | 完全相同映射，EP01 vs EP02 | inf | False | ✔ 永不相容 |
| F | 中点相交、两端分叉（1.0 vs 0.1） | 4.500 | False | ✔ |

E（随 span 变化）——slope 1.00 vs 1.05：

| union span | 2 s | 4 s | 6 s | 8 s | 10 s | 20 s | 40 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| divergence | .100 | .200 | .300 | .400 | .500 | 1.000 | 2.000 |
| compatible | T | T | T | T | T | **F** | **F** |

判据在 10 s 处正好落在一个 grid step，行为与"仿射差的最大值在闭区间端点取得"的数学一致。
`_lines_stay_consistent()` 还支持显式 span 覆盖，同一对线在 `(0,4)` 相容、`(0,20)`/`(0,100)`
不相容。

### 5.2 端到端（注入合法 fitted proposals，走完整后半段）

上一轮记录的两个反例现在结论**完全反转**：

| 构造 | `c512a80` 行为（上轮记录） | `80036d8` 独立复现 |
|---|---|---|
| 同集、`source_start` 差 0.5 s、slope 1.0 vs 1.25 | 判为 compatible，**静默删除 rival**，`ambiguous=false` | `ambiguous=True`，rival 保留为 alternative（`a=1.2474`），competing=1 |
| 同集、同映射 `source=100+accused`、边界差 2 s | 放入 alternatives，`ambiguous=true` | `ambiguous=False`，alternatives=[]，primary GREEN |

补充构造：

| 构造 | 结果 |
|---|---|
| 跨集完全相同映射 | `ambiguous=True`，EP02 进 alternatives ✔ |
| 同线 + ±1 grid jitter rival | `ambiguous=False`，无 alternative ✔ |
| 4 s span、slope 1.00 vs 1.05 | `ambiguous=False`（该 span 下不可分） ✔ |
| 24 s span、slope 1.00 vs 1.05 | `ambiguous=True`，rival `a=1.0514` 保留 ✔ |

### 5.3 同一 helper 是否真的用在五处

`grep` + 代码阅读确认 `_same_temporal_line()` 用于：
suppression（`matching.py:762`）、rival 选取（`:803`）、conflict graph（`:1046`），
`_merge_adjacent()` 通过 `_lines_stay_consistent()`（`:1149`）。alternatives 由 rival 列表
派生，因而共用同一判据。

`matching.py` 中残留的 `source_start` 只有三处，**都不是线身份判据**：
`:556`（change-point 的 source 连续性）、`:1066`（`_mark_source_reuse` 的 source 区间重叠）、
`:1147`（`_merge_adjacent` 的 source 连续性）。**不存在"新 helper + 某处仍偷用旧判据"。**

**§5 结论：上一轮的 rival compatibility Major 已真正关闭。**

---

## 6. Change-point refinement（本轮最高优先级）

### 6.1 Ablation：是一般能力，还是只修好已知 testcase？

同一输入，唯一变量是 `change_point_split_enabled`：

| 构造 | 关闭（segments / accused starts / slopes） | 打开 |
|---|---|---|
| 0.8→1.25，12+12 s | 2；`[0,11.5] a=.8024` `[11.5,24.5] a=1.2509` | 2；`[0,12] a=.7978` `[12,24.5] a=1.2523` |
| 1.0→1.25，12+12 s | 2；`[0,17.5] **a=1.0513**` `[17.5,24.5] a=1.2505` | 2；`[0,12] a=1.0` `[12,24.5] a=1.2569` |
| 0.75→1.50，12+12 s | 2；`[0,11] a=.75` `[11,24.5] a=1.4847` | 2；`[0,12] a=.75` `[12,24.5] a=1.4954` |
| 0.8→1.25→1.0，8×3 | 3；`[0,6.5]` `[6.5,20.5] **a=1.1883**` `[20.5,24.5]` | 3；`[0,7.5] a=.7893` `[7.5,16] a=1.2451` `[16,24.5] a=1.0` |
| 1.0→1.4→0.75→1.1，8×4 | 4；`[0,11.5] **a=1.0792**` `[11.5,15] a=1.3571` `[15,29] **a=.8634**` `[29,32.5] a=1.1786` | 4；`[0,8] a=1.0` `[8,15] a=1.4` `[15,24] a=.7916` `[24,32.5] a=1.0931` |

关闭时出现的 `a=1.0513`、`a=1.1883`、`a=1.0792`、`a=.8634` 就是上一轮所说的**跨 change point
混合段**。打开后全部消失。这个改动**不是针对单个 testcase 的补丁**。

### 6.2 A. 两段速度（每段 12 s，on-grid）

| GT | 预测 segments | accused 边界 | 恢复的 slope |
|---|---:|---|---|
| 0.75→1.50 | 2 | 0 / 12.0 | .7500 / 1.4954 |
| 1.50→0.75 | 2 | 0 / 12.0 | 1.4948 / .7500 |
| 0.90→1.40 | 2 | 0 / 12.0 | .9030 / 1.3962 |
| 1.40→0.90 | 2 | 0 / 12.0 | 1.4000 / .8985 |
| 1.00→1.25 | 2 | 0 / 12.0 | 1.0000 / 1.2569 |
| 0.80→1.25 | 2 | 0 / 12.0 | .7978 / 1.2523 |
| 1.25→0.80 | 2 | 0 / 12.0 | 1.2578 / .7962 |

7/7 段数正确，边界误差 0，slope 误差 ≤ .006。

### 6.3 B. 小变化 × short/long span

| 变化 | 4 s + 4 s | 20 s + 20 s |
|---|---|---|
| 1.00→1.03 | 1 段，`a=1.0`（**应当不切**） | 1 段，`a=1.0154`，IR=1.0 |
| 1.00→1.05 | 1 段，`a=1.0`（**应当不切**） | 2 段，边界 25.5（GT 20.0） |
| 1.00→1.10 | 1 段，`a=1.0637` | 2 段，边界 22.5（GT 20.0） |

`1.00→1.03` 即使 20+20 s 也不切是**正确的**：整段 source 位移只有 0.6 s，snap 到 0.5 s grid
后单线解释的最大偏差约 0.2 s < 0.5 s 容差，两个 regime 在该采样率下不可分。

`1.00→1.05` / `1.00→1.10` 的**边界定位误差 2.5–5.5 s**：Δslope=0.05 时两条线要 10 s 才分开
一个 grid step，所以 change point 位置本身就有 ±10 s 的不确定性。当前输出**不携带任何
不确定性标记**（candidate GREEN、`boundary_completeness=UNKNOWN`）。这是一个应记录的
特性，不是本阶段必修项。

### 6.4 C. 三段 / 四段 / 多段

| 构造 | 段数 | 预测 accused 边界 | GT 边界 |
|---|---:|---|---|
| 0.8→1.25→1.0（8 s 各） | 3 | 0 / 7.5 / 16.0 | 0 / 8 / 16 |
| 0.8→1.25→1.0（12 s 各） | 3 | 0 / 12.0 / 23.5 | 0 / 12 / 24 |
| 1.0→1.4→0.75→1.1（8 s 各） | 4 | 0 / 8.0 / 15.0 / 24.0 | 0 / 8 / 16 / 24 |

交替 regime 段数扫描（每段 8 s）：

| regimes | 2 | 3 | 4 | 5 | 6 | 8 | 10 | 12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 预测段数 | 2 ✔ | 3 ✔ | 4 ✔ | 5 ✔ | 6 ✔ | **7** | **7** | **9** |

把 `change_point_maximum_splits` 从 4 提到 32 后 8→7、12→9 **不变**，所以 ≥8 段的退化来自
extraction 而不是 refinement 的 cap（extraction 是 Owner 明确禁止重写的部分）。

### 6.5 D. 不应该切的情形

| 构造 | 段数 | 结论 |
|---|---:|---|
| 同 slope 1.0 + 1 s retrieval hole | 1 | ✔ |
| 同 slope 1.0 + 稀疏支持（隔帧丢失） | 1（YELLOW，coverage 降级） | ✔ |
| 同线 + 3 个 ±0.5 s outlier | 1（IR=.9796） | ✔ |
| 同 slope 1.0 长 40 s | 1 | ✔ |
| 同 slope score 高低交替（0.90/0.99） | 1 | ✔（有对应回归测试） |
| **同线 speed 1.25 + 1.5 s hole** | **2** | **✘ → M-1，见 §7.4** |

同线 + 1.5 s hole 的速度扫描：

| 真 speed | .75 | .80 | 1.00 | 1.25 | 1.50 |
|---|---:|---:|---:|---:|---:|
| 段数 | 1 | 1 | 1 | **2** | **2** |

根因是 `_merge_adjacent()` 的 `abs(current.source_start - previous.source_end) <= 1.5`
是**绝对秒数、不随 slope 缩放**：accused 侧 1.5 s 的 hole 在 a=1.25 时对应 1.875 s source，
超过硬编码 1.5。

### 6.6 E. source reuse + speed change

`1.0(8s)→1.25(8s)` 再加一段复用同一 source 区间的 `1.0`：输出 3 段，两段 `source_reuse=True`
且降级 YELLOW，中段 `a=1.2676` 保持 GREEN，`ambiguous=False`。reuse 标记与 change-point
互不破坏。

### 6.7 F. 噪声 change point

| 注入 | 段数 | accused 边界 | slope |
|---|---:|---|---|
| change point 附近 4 帧被 wrong-episode 候选占据 | 2 | 0–11.0 / 13.0–24.5 | .7979 / 1.2470 |
| change point 附近 5 帧 ±1 grid jitter | 2 | 0–12.0 / 12.0–24.5 | .8050 / 1.2231 |
| change point 附近 3 帧缺失 | 2 | 0–11.5 / 13.0–24.5 | .8024 / 1.2470 |

三种噪声下都保持 2 段、两个 slope 都恢复。

### 6.8 剩余系统性问题：短 regime 被吞并

长首段 + 短尾段扫描（预测段数）：

| 首段 | 3 s | 4 s | 5 s | 6 s | 8 s | 10 s | 12 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1.0(24 s)→1.5 | **1** | **1** | 2 | 2 | 2 | 2 | 2 |
| 0.8(20 s)→1.25 | **1** | 2 | 2 | 2 | 2 | 2 | 2 |
| 1.25(24 s)→0.8 | **1** | 2 | 2 | 2 | 2 | 2 | 2 |
| 1.0(24 s)→1.25 | **1** | **1** | **1** | 2 | 2 | 2 | 2 |

被吞并时的输出（`1.0(24s)→1.5(4s)`）：

```text
acc[0.0, 26.0]  src[99.929, 126.093]  a=1.0063
tier=GREEN  review=REVIEW_REQUIRED  bc=UNKNOWN  reason=None
ir=1.0  rms=0.1084  coverage=1.0  inl=52/52
ambiguous=False  conflict=False  reason=None
57 个候选中 52 个被解释，5 个（accused 26–28）无任何输出
```

即：真实边界是 acc[0,24] + acc[24,28]，系统输出一个 acc[0,26] 的混合段，`source_end` 比
GT1 偏 2.09 s，并给出 candidate GREEN、`UNKNOWN`、无 warning。

严重度评估：
- 触发条件是**第二个 regime 短于 4–6 s**，正好落在 `minimum_segment_seconds=3.0` /
  `change_point_minimum_side_seconds=3.0` 声明的下界附近；配置声明的 3.0 s 下界比实测下界
  乐观（实测 4–6 s，随 Δslope 变化）。这是**文档诚实度问题**。
- 结构原因：`_refine_adjacent_change_points()` 只重新划分**已存在的相邻 pair**，
  段数恒定不变，因此当 extraction 已经吐出一个混合段时，refinement 在结构上无法拆开它。
  修复需要改 extraction —— Owner 明确禁止。
- 真实 benchmark 不触发：每个 profile 全片恒速，没有 change point。

因此这条**记录为已知边界，不作为本阶段必修**。

**§6 结论：change-point refinement 是真正的一般能力；上一轮的 change-point blocker 已关闭。
剩余的短 regime 吞并是 extraction 层的能力边界，且当前配置注释对它的下界描述过于乐观。**

---

## 7. Boundary completeness

### 7.1 A. E001-I008 形态（`##...#######`）

```text
输入 mask:  # # . . . # # # # # # #      （12 个 0.5 s query 帧）
输出:       acc[2.50, 6.00]  src[1870.000, 1873.500]  a=1.0
            tier=YELLOW   bc=INCOMPLETE
            reason: "compatible orphan support outside the emitted span: 2 before start"
```

完全符合预期：保留可确认的 partial span、**不外推**到 0–2.5 s、显式声明边界不完整、
非 GREEN。

### 7.2 B. 干净完整 span

24 帧连续、两端没有 orphan：输出 `acc[0,12]`，`bc=UNKNOWN`，GREEN。**没有默认 FULL。**
实现与 ADR-0002/ADR-0003 的表述一致。

`BoundaryCompleteness.FULL` 在整个 `src/` 中**没有任何写入点**（唯一出现是
`temporal_benchmark.py:319` 的读取）。所以生产语义当前实际是二值（INCOMPLETE / UNKNOWN），
benchmark 的 `INCORRECT_COMPLETE_ASSERTION` 分支不可达。closure report §9.1 已诚实说明这点，
不视为 bug。

### 7.3 C. false INCOMPLETE（必须不误标）

| 构造 | 结果 | 期望 |
|---|---|---|
| 相邻 piecewise 段（0.8→1.25 两条真实线） | 两段均 UNKNOWN | ✔ |
| source reuse（两个 disjoint accused 复用同一 source 区间） | 两段均 UNKNOWN | ✔ |
| 相邻但无关的 EP02 线 | 两段均 UNKNOWN | ✔ |
| 段前 6 个 wrong-episode orphan | UNKNOWN | ✔ |
| 段前 6 个同集但 slope 不相容的 orphan | UNKNOWN | ✔ |
| 段前 10 s 处 3 个**同线** orphan（真 orphan） | **INCOMPLETE**，reason "3 before start" | ✔ |

五种应当不触发的构造全部正确保持 UNKNOWN；唯一应触发的构造正确触发。

### 7.4 D. orphan 阈值与 M-1

阈值扫描（`mask` 前/后 orphan 数）：

| orphan 数 | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| 段前 | UNKNOWN / GREEN | UNKNOWN / GREEN | **INCOMPLETE** / YELLOW | INCOMPLETE | INCOMPLETE |
| 段后 | UNKNOWN / GREEN | UNKNOWN / GREEN | **INCOMPLETE** / YELLOW | INCOMPLETE | INCOMPLETE |
| 两端 2+2 | — | — | INCOMPLETE，reason 同时列出两端 | — | — |

行为与代码注释声明的"两个不同 accused frame"完全一致。1 个 orphan（0.5 s 截断）不触发，
仍是 GREEN——这是文档化的取舍（"One top-k collision is not stable boundary evidence"），
量级 0.5 s，可接受。

**M-1（Major）**：当 orphan 证据落在**另一个已输出且同线**的 segment 内时，
`_classify_boundary_completeness()` 会把它排除掉：

```python
# matching.py:986-993
if any(
    other.source_episode == segment.source_episode
    and other.accused_start - 1e-9 <= candidate.accused_time < other.accused_end - 1e-9
    for other in other_emitted_segments
):
    continue
```

这个排除**没有做同线判断**。对真正的 piecewise（不同线）它是对的（§7.3 第一行验证），
但对**同一条线被切成两段**的情形，它恰好屏蔽掉唯一能说明"边界不是最终边界"的证据：

```text
一条真实线 acc[0,24.5] @ a=1.25，中间 1.5 s hole：
  acc[ 0.0,10.0]  a=1.2534  tier=GREEN  bc=UNKNOWN
  acc[11.5,24.5]  a=1.2540  tier=GREEN  bc=UNKNOWN
```

两段各自都是真实线的真子区间，但都被报成 GREEN + UNKNOWN，没有任何 partial 标记。
按本阶段目标句"不能把 partial match 伪装成完整时间线"，这属于必修。

触发前提是 `_merge_adjacent()` 拒绝合并同线两段，而当前拒绝的原因正是 §6.5 的
speed-blind 1.5 s source-gap。所以 **M-1 有两个独立的最小修法**：
(a) 把 `_merge_adjacent()` 的 source 连续性阈值按 slope 缩放；或
(b) 在上面的排除条件里加一个 `self._same_temporal_line(segment, other)` 的取反判断。
两者都不触碰 sparse voting / robust fitting / extraction。

**§7 结论：`INCOMPLETE` 在"孤立 orphan 支持"这一类上可靠表达了"确认了部分、不能声称完整"；
在"同线被切成两段"这一类上会静默降为 UNKNOWN 且保留 GREEN（M-1）。**

---

## 8. E001-I008 的真正 Gate 含义

四个 paired case（direct/P1/P2/P3）独立复现，逐字段一致：

```text
GT accused                      0.0 – 6.0
GT source                       1867.603 – 1873.603
confirmed accused span          2.5 – 6.0
confirmed source span           1870.000 – 1873.500
actual supported frames         9 / 12
longest actual-hit run          7 frames
longest run temporal span       3.5 s
tolerated holes in that run     0
supported accused start/end     2.5 / 6.0
sufficient_for_any_match        true
sufficient_for_full_boundary    false
boundary_completeness           INCOMPLETE  ("2 before start")
candidate / production tier     YELLOW / REVIEW_REQUIRED
failure_attribution             RETRIEVAL_INSUFFICIENT_FOR_FULL_BOUNDARY
boundary_assertion_outcome      CONTAINED_PARTIAL_EVIDENCE
functional outcome              UNSAFE_WRONG_MATCH
```

被声称的 partial 部分本身是**正确**的：accused 2.5 在 GT 映射下对应 source 1870.103，
系统给出 1870.000，误差 0.103 s。

### Q1　生产系统是否仍在声称"完整使用范围就是 2.5–6"？

**没有。** 我逐个检查了每个输出面：

| 输出面 | 是否携带完整性语义 |
|---|---|
| `SegmentMatch` | `boundary_completeness='INCOMPLETE'` + `boundary_completeness_reason` |
| SQLite `segment_matches` | v6 列 `boundary_completeness` / `boundary_completeness_reason` |
| audit JSON（schema 0.5） | `matching.boundary_completeness` 映射，含 status 与 reason |
| CLI `analyze` payload | `segments[].boundary_completeness` |
| real benchmark artifact | 正交 `boundary_assertion_outcome` |
| 所有面 | `review_tier=REVIEW_REQUIRED` |

不存在只给 span 不给完整性标记的输出面。因此从 **production semantics** 看，这四例
**不再属于 unsafe assertion**，而是"已确认 2.5–6.0；完整边界未知/不完整；必须人工复核"。

### Q2　functional evaluator 继续记 UNSAFE 是否只为保持不可作弊的 accuracy metric？

**是，而且这是正确的设计。** 我独立验证：`CONTAINED_PARTIAL_EVIDENCE` 完全不进入
TP/recall（独立重算 TP=109 与 artifact 一致，四例仍计 FN+FP）。如果让 partial 计入
recall，就会用新 taxonomy 把 recall 从 0.9083 抬到 0.9417 而没有恢复任何真实边界。
保留严格口径是必要的反作弊约束。

### Q3　是否应该同时保留两个正交指标？

**应该，而且已经保留。** ADR-0005 已把两套指标写成正交定义，artifact 里也是两套分布并存
（`outcome_taxonomy.counts` 与 `boundary_assertion_outcome_distribution`）。这正是"能确认多少
就报告多少"与"不能把 partial 伪装成完整"两个要求同时成立的唯一表达方式。建议 Owner
在 Gate 文本中把两者并列写死，避免后续用任一单一数字代表系统能力。

### Q4　这 4 个 paired case 是否还应阻断 Phase 2A CLOSED？

**不应该。** 理由：

1. 它们是**同一个内容位置**的 4 个配对 profile，不是 4 份独立证据（position-level：
   19/20 位置无 unsafe）。
2. 上一轮它们阻断的具体理由是"以 `MATCH_FOUND` 返回错误时间线并被标为 candidate GREEN"。
   现在状态是 `MATCH_FOUND` + **YELLOW** + **INCOMPLETE** + `REVIEW_REQUIRED`，
   这三项改变了性质。
3. 根因已归到 retrieval：`sufficient_for_any_match=true` 而
   `sufficient_for_full_boundary=false`，头部 3 帧连续缺失，2.0 s candidate gap 超过
   1.5 s 桥接阈值。恢复完整边界需要更强的视觉候选，属于 visual backend Gate。
4. 强行恢复 0–2.5 s 只能靠外推，而外推恰恰是本阶段明令禁止的。

---

## 9. Retrieval attribution

### 9.1 上一轮反例

| 输入（12 帧，gap=1.5 s，fps=2） | 旧 `_longest_supported_run` | 新 `_supported_run_statistics` |
|---|---:|---|
| 实际命中 `{0,3,6}` | **7** | hits=**3**，span=3.5 s，holes=4，any=**False** |
| 实际命中 `{0,3,6,9}` | — | hits=4，span=5.0 s，holes=6，any=False |

### 9.2 穷举证明

对 12 帧的**全部 4095 个非空子集**计算
`longest_run_actual_hit_count − |actual hits|`，最大值 = **0**。
即：**tolerated hole 在任何输入下都不可能抬高 actual hit count**，只延长 span。

### 9.3 定义是否非同义反复

```text
sufficient_for_any_match      := longest_run_actual_hit_count >= minimum_support
                                 （= minimum_segment_seconds × fps = 6 帧）
sufficient_for_full_boundary  := sufficient_for_any_match
                                 AND supported_accused_start == 0.0
                                 AND supported_accused_end   >= query_duration
```

前者把 retrieval 侧的实际连续支持量与 aligner 自己的最小支持门槛比较——两个独立量，
非同义反复。后者被代码注释显式标为 benchmark-only 且允许使用 GT
（`temporal_benchmark.py:874-875`），生产 `boundary_completeness` 从不依赖 GT，我已在
§7 独立确认。

### 9.4 边界行为抽查

| 输入 | hits | span | holes | start/end | any | full |
|---|---:|---:|---:|---|---|---|
| 全 12 帧 | 12 | 6.0 | 0 | 0.0 / 6.0 | T | T |
| `0..5` | 6 | 3.0 | 0 | 0.0 / 3.0 | T | F |
| `6..11` | 6 | 3.0 | 0 | 3.0 / 6.0 | T | F |
| `##...#######`（E001-I008） | 7 | 3.5 | 0 | 2.5 / 6.0 | **T** | **F** |
| `{0..10}`（尾帧缺） | 11 | 5.5 | 0 | 0.0 / 5.5 | T | F |
| `{0,2,4,6,8,10}` | 6 | 5.5 | 5 | 0.0 / 5.5 | T | F |
| `{0,1,2}` | 3 | 1.5 | 0 | 0.0 / 1.5 | F | F |
| 空 | 0 | 0.0 | 0 | None | F | F |

E001-I008 的 `any=true / full=false` 是一般规则的自然结果，**没有 hard-code**。

**§9 结论：retrieval attribution 已正确拆分并可证明。**

---

## 10. Outcome taxonomy — 独立重算

### 10.1 方法

我**没有**读取 case 的 `outcome` 字段，**没有**调用 `classify_case_outcome()`、
`_alternatives_contain_ground_truth()`、`_mapping_matches_truth()` 或 `evaluate_segments()`。
GT 由只读读取 scratch DB 中的 episode duration，配合 artifact 的
`normalized_source_start` 与 profile speed 独立重建
（`accused=[0, 6/speed]`，`source=[start, start+6]`），比较逻辑自写。

### 10.2 结果

| 类别 | 独立重算 | artifact |
|---|---:|---:|
| CORRECT_UNAMBIGUOUS_MATCH | 96 | 96 |
| SAFE_AMBIGUITY_CONTAINS_GT | 20 | 20 |
| UNSAFE_WRONG_MATCH | 4 | 4 |
| NO_MATCH | 0 | 0 |
| **合计** | **120** | 120 |
| TP / FP / FN | 109 / 11 / 11 | 109 / 11 / 11 |
| precision / recall | .9083 / .9083 | .9083 / .9083 |

**逐案零分歧。** 四类互斥（每 case 单一赋值）且求和等于 case 数。

### 10.3 对抗 fixture（A–G）

| 检查 | 结果 |
|---|---|
| C：`AMBIGUOUS` + primary 完全正确 | → `SAFE_AMBIGUITY_CONTAINS_GT`（**不是** automatic correct）✔ |
| C：`MATCH_FOUND` + primary 完全正确 | → `CORRECT_UNAMBIGUOUS_MATCH` ✔ |
| D：alternative 仅 source endpoints 对、accused[100,160] 错 | `mapping_matches_truth=False` → `UNSAFE_WRONG_MATCH` ✔（上一轮反例已修）|
| D：alternative 完整映射正确 | → `SAFE_AMBIGUITY_CONTAINS_GT` ✔ |
| E：正确集数 / 错误 source 时间 | → `UNSAFE_WRONG_MATCH` ✔ |
| F：`MATCH_NOT_FOUND` 且 0 prediction | → `NO_MATCH` ✔ |
| F：`MATCH_NOT_FOUND` 但有 prediction | → `UNSAFE_WRONG_MATCH` ✔（不能靠 status 绕过）|
| G：GT 全中但多出 1 个 FP | → `UNSAFE_WRONG_MATCH` ✔ |

`_mapping_matches_truth()` 拒绝的变体：wrong episode、source 平移 3 s、duration 减半、
speed 2×、`speed_ratio=None`、`speed_ratio=1.4`；接受 1 s 容差内的变体。行为正确。

在 status × recall × FP × prediction × alternatives 的穷举网格上，可达标签集合
**恰好等于** `CASE_OUTCOMES`，无逸出。

正交 boundary 标签：`INCOMPLETE` 部分证据 → `CONTAINED_PARTIAL_EVIDENCE`；
`FULL` 但 span 错 → `INCORRECT_COMPLETE_ASSERTION`；完整命中 → `FULL_MAPPING_CORRECT`；
无输出 → `NO_OUTPUT`；`UNKNOWN` 且 span 错 → `UNRESOLVED_OR_INCORRECT_MAPPING`。

**§10 结论：taxonomy 已可信，上一轮两项 taxonomy 缺陷均已关闭。**

---

## 11. Synthetic ambiguity 37/73 — 根因审查

### 11.1 独立复跑与对照

我在 scratch 环境分别用 `80036d8` 与 `c512a80` 的代码跑了完整 A–M（`--all-diagnostic`）：

| | `c512a80` | `80036d8` |
|---|---:|---:|
| cases | 73 | 73 |
| GT / predicted | 80 / 70 | 80 / 70 |
| TP / FP / FN | 36 / 34 / 44 | 36 / 34 / 44 |
| AMBIGUOUS | **33 / 73** | **37 / 73** |
| MATCH_FOUND | 26 | 22 |
| MATCH_NOT_FOUND / INCOMPLETE_INDEX | 13 / 1 | 13 / 1 |

TP/FP/FN 逐位相同，确认 completion report"没有改 sampling、GT 或 evaluator 来换分"属实。

### 11.2 全部 37 个 AMBIGUOUS case 的根因分类

我对每个 primary–alternative 对独立计算 `source = slope×accused + intercept` 在 union
accused 域上的最大分叉：

| 类别 | case 数 | pair 数 |
|---|---:|---:|
| A. near-equivalent same-line artifact | **0** | **0** |
| B. genuine same-episode different-line competition | 31 | 36 |
| C. genuine cross-episode competition | 6 | 8 |
| D. evaluator / bookkeeping artifact | **0** | 0 |
| E. unclear | 0 | 0 |

同集 pair 的 divergence 分布（grid step = 0.5 s）：

```text
min 1.00  median 6.00  max 9.88
[1.00, 1.25, 1.41, 1.41, 3.11, 3.44, 4.00 ×6, 4.09, 4.10, 4.25, 4.83, 5.56,
 6.00 ×10, 6.08, 6.38, 6.87, 7.88, 8.00, 8.07, 8.25, 8.95, 9.88]
≤1 grid step（即 A 类）: 0
≤2 grid steps        : 1
```

primary 与 rival 的 visual_score 差：min −0.0144，median **0.0027**，max 0.0178 —— 全部远小于
0.05 的 uniqueness margin。即：**DCT 描述子确实分不开这些候选。**

我最初把 `P1_015` 归为 D 类（无 alternative），复查后更正为 B：它的 AMBIGUOUS 来自
`contradiction` 路径而非 rival 列表——两段 EP03 的 `acc[0,3]→src[0,2.06]` 与
`acc[1.5,5.5]→src[0,3.01]` 互相冲突，`conflict_reason="overlapping accused intervals map to
incompatible sources"`。这是真实不相容，不是 bookkeeping 假象。

### 11.3 为什么 B 类占绝对多数：benchmark 内容本身近周期

demo originals 由 `create_demo_originals()` 生成，内容是 `testsrc2`、`testsrc`、
`testsrc2+negate`、以及 `mandelbrot`+`smptebars` 拼接。`testsrc`/`testsrc2` 是**时间上高度
自相似**的测试图样；在 144 维 DCT 低频描述子下，相隔 4–8 秒的两个位置几乎不可区分。
这与上面 median divergence = 6.0 s、visual margin median = 0.0027 的观测完全吻合。

### 11.4 33 → 37 的增量是什么

| case | 变化 | rival | divergence | 之前 recall | 现在 recall |
|---|---|---|---:|---:|---:|
| `CASE_A_01` | MATCH_FOUND → AMBIGUOUS | 同集 | 1.41 s | **0/1** | 0/1 |
| `P1_006` | MATCH_FOUND → AMBIGUOUS | 同集 | 1.41 s | **0/1** | 0/1 |
| `P1_015` | MATCH_FOUND → AMBIGUOUS | 同集 contradiction | — | **0/1** | 0/1 |
| `P1_017` | MATCH_FOUND → AMBIGUOUS | 同集 | 1.00 s | 1/1 | 1/1 |

**4 个增量里有 3 个原本是静默返回的错误答案**（recall 0/1 却报 `MATCH_FOUND`），现在带上了
歧义信号。只有 `P1_017` 是正确答案被标歧义，而它的 rival 分叉 1.0 s（2 grid steps）、
visual margin 0.0165，属于真实近平局。**净安全性为正。**

### 11.5 关于 completion report 的一处措辞

report §7 写"Phase 2A.2 关闭的是 near-equivalent same-line proposal 人工制造 ambiguity 的
机制"。当前状态确实 0 对同线 pair（我复现），但 **`c512a80` 同样是 0 对同线 pair**
（31 B + 8 C = 39 对），所以那 33 个歧义并非由同线机制产生。真实机制是：
`3f5f4d5` 只有跨集 rival 能触发歧义（1/73）→ `c512a80` 的 suppressed-rival 路径把同集不相容
rival 纳入（33/73）→ `80036d8` 把 `major_rivals` 的跨集限制也去掉（37/73）。
这正是本 commit 修订 ADR-0003 时写死的语义（"同 episode 但 mapping 在 union domain 上超过
source-grid uncertainty 的候选也不相容"）。**这是一次有意的、已文档化的语义扩大，不是回归**，
report 反而低估了自己的安全性收益。

`54` 与我数的 `44` 的差别是口径：`predictions × alternatives` = 54，
`AMBIGUOUS case 上的 alternatives` = 44。两种口径下同线 pair 都是 0。

**§11 结论：剩余 37/73 的根因已确定落在 DCT visual retrieval（B/C 共 44 对，A/D 为 0），
不是 temporal bookkeeping。这不构成 Phase 2A 的 temporal 层阻断项。**

---

## 12. Candidate GREEN

### 12.1 四条排除不变量（aligner 层直接构造）

| 构造 | candidate_tier | review_tier |
|---|---|---|
| 干净单线（基线） | GREEN | REVIEW_REQUIRED |
| `boundary_completeness=INCOMPLETE` | **YELLOW** | REVIEW_REQUIRED |
| out-of-range speed 1.70 (×3 段) | **YELLOW** | REVIEW_REQUIRED |
| source reuse (×2 段) | **YELLOW** | REVIEW_REQUIRED |
| 命中 segment cap (×2 段) | **YELLOW** | REVIEW_REQUIRED |
| `index_complete=False` | **YELLOW** | REVIEW_REQUIRED |

### 12.2 全 artifact 扫描

| artifact | review_tier 取值集合 | candidate GREEN | 违反不变量的 GREEN |
|---|---|---:|---:|
| real 120 case | `{REVIEW_REQUIRED}` | 93 | **0** |
| ideal 17 case | `{REVIEW_REQUIRED}` | 14 | **0** |
| synthetic 73 case | `{REVIEW_REQUIRED}` | 20 | **0** |

127 个 GREEN segment 中，没有任何一个同时带 `INCOMPLETE`、`segment_cap_truncated` 或
`speed_out_of_range`。生产 `review_tier` 在 210 个 case 的全部输出上恒为 `REVIEW_REQUIRED`。

### 12.3 GREEN 精度

| 集合 | GREEN | 正确 | precision |
|---|---:|---:|---:|
| real | 93 | 93 | **1.00** |
| synthetic | 20 | 7 | **0.35** |

### 12.4 GREEN 是否已被文档限制为内部诊断？

**是。** ADR-0003 已写入："Candidate GREEN 只表示已输出 segment 内部 alignment quality，
不表示 segment 边界完整，更不表示生产自动放行"；README 同步；real artifact 的
`metric_semantics.candidate_green` 直接写 `"Internal alignment-quality diagnostic only"`。
代码侧我确认 `candidate_tier` 的**唯一非写入读取点**是两处 benchmark 统计
（`benchmarking.py:417/538`）与一处 ideal 断言，没有任何生产放行分支读它。

因此 synthetic GREEN precision 0.35 **不应阻断 Phase 2A**：它度量的是"temporal 层认为自洽的
线里有多少视觉上是对的"，而在近周期的 `testsrc` 内容 + DCT 低频描述子下，一条结构自洽的
错误线本来就可能被 temporal 层接受。要求 Temporal Alignment 单独证明视觉正确性是范畴错误。
但结论的另一半必须保留：**在 GREEN 加入 completeness/视觉证据之前，不得恢复任何自动放行。**

---

## 13. Segment cap 全链路

低 cap（`maximum_alignment_segments_per_episode=2`）+ 5 段输入：

```text
aligner            segments=2  truncated_episodes=('EP01',)
                   reason="Alignment segment cap reached for: EP01"
                   warnings=[{"code":"ALIGNMENT_SEGMENT_CAP_REACHED",
                              "message":"Per-episode alignment segment cap reached; output is truncated",
                              "episodes":["EP01"],"limit_per_episode":2}]
                   每段 segment_cap_truncated=True，tier 全部降级 YELLOW
AnalysisResult     truncated_episodes / warnings 是真实 dataclass 字段（非 getattr）
matcher            replace_case_result(..., truncated_episodes, warnings) 显式传参
DB accused_videos  truncated_episodes_json='["EP01"]'
                   analysis_warnings_json=[{"code":"ALIGNMENT_SEGMENT_CAP_REACHED",...}]
DB segment_matches segment_cap_truncated=[1,1]  candidate_tier=['YELLOW','YELLOW']
audit JSON v0.5    truncated_episodes=['EP01']  segment_cap_reached=True
                   warnings=['ALIGNMENT_SEGMENT_CAP_REACHED']
                   matching.boundary_completeness={'S001':{...},'S002':{...}}
CLI analyze        truncated_episodes / warnings 出现在 payload
benchmark          case['truncated_episodes'] / case['warnings']
```

`grep -rn "getattr(result" src/` → **无命中**。上一轮的静默丢失已彻底关闭。

---

## 14. Ideal benchmark integrity

Fresh run（当前代码，写入 scratch）与 tracked artifact 逐字段比较：

| 项目 | 独立结果 |
|---|---|
| cases | 17（gating 12 / diagnostic 5，互斥且覆盖全部） |
| **candidate timestamps 自行枚举** | **1824** |
| **不在 0.5 s source grid 上的** | **0**（offenders = {}） |
| gating GT / prediction | 16 / 16 |
| gating TP / FP / FN | 16 / 0 / 0 |
| precision / recall | 1.0 / 1.0 |
| speed MAE | 0.00693 |
| source boundary MAE | 0.02875 s |
| tracked vs fresh | 除 `runtime_seconds`（0.2193 vs 0.2119）外**完全一致**；predictions 逐案相同；behavioural assertions 相同；configuration 相同 |

上一轮的"18 个非 grid candidate 却硬写 `source_grid_realistic=true`"已关闭：该字段现在由
`_grid_invariant()` 计算，`IDEAL_GRID_JITTER` 的生成器改为先 snap 再加 ±1 grid 抖动。
上一轮的"tracked artifact 仍是旧 10-case v0.1"也已关闭。

行为断言（我逐条复核为 True）：

```text
pure_noise_produces_no_segments        True
no_clamped_speed_reported              True
out_of_range_semantics_complete        True
speed_discontinuity_source_aware       True
competing_lines_are_ambiguous          True
short_source_span_speed_is_null        True
```

`IDEAL_SPEED_DISCONTINUITY` 现在 source-aware 2/2：预测 `[0,12] src[100.014,109.587]` 与
`[12,24] src[109.513,124.482]`，slopes `0.7978 / 1.2474`。上一轮"只检查 `predicted_segments >= 2`
而实际 0/2 命中"的假阳性已关闭。

out-of-range 诊断完整：`speed_ratio=null`、`speed_out_of_range=true`、
`unconstrained_speed_ratio` 存在（0.6692–0.6711 / 1.6593–1.6710）、`candidate_tier=YELLOW`、
`review_tier=REVIEW_REQUIRED`。pure noise = 0 segment。

### M-2（Major）：`passed` 没有接入行为断言

```python
# temporal_benchmark.py:799-802
"passed": (
    aggregate["source_aware_segment_recall_at_1s"] == 1.0
    and aggregate["segment_precision_at_1s"] == 1.0
),
```

`aggregate` = `_metric_summary(gating_cases)`，而
`diagnostic_transformations = {pure_noise_negative, speed_out_of_range,
speed_discontinuity_merge_negative, grid_jitter_outlier_wrong_episode}` 被排除在 gating 之外。

因此：**`speed_discontinuity_source_aware`（关闭上一轮 change-point blocker 的那一条）、
`pure_noise_produces_no_segments`、`out_of_range_semantics_complete` 全都不影响 `passed`。**
它们被诚实计算并写入 artifact（我能读到），但 Gate 不看。这与上一轮批评的
"merge-negative 只检查输出至少两段"属于同一类缺陷——断言存在但不约束 Gate。

修法极小：把 `behavioural_assertions` 的 `all(...)` 与 `source_grid_invariant
.all_candidates_on_grid` 一起 AND 进 `passed`。

### 关于 ideal 1.0/1.0 的解读

必须保留 closure report 已写明的限定：这只证明**受控可恢复 case** 的 nominal-grid 内部指标，
不代表 production accuracy，也不是 media PTS 精度。

---

## 15. Real benchmark 独立复跑

配置未变：source 2 FPS / query 2 FPS / top-k 8 / 20 content positions × 6 profiles。
**`episode_n=2`，120 case 不是 120 个独立样本。**

### 15.1 Case level

| 指标 | 独立复跑 | closure report |
|---|---:|---:|
| cases / content positions | 120 / 20 | 120 / 20 |
| TP / FP / FN | 109 / 11 / 11 | 109 / 11 / 11 |
| precision / recall | .9083 / .9083 | .9083 / .9083 |
| episode correctness | .9417 | .9417 |
| source boundary MAE | 0.1993 s | 0.1993 s |
| accused boundary MAE | 0.0692 s | 0.0692 s |
| candidate GREEN / precision | 93 / 1.0 | 93 / 1.0 |
| matcher runtime sum | **32.16 s** | 32.67 s |
| status 分布 | 100 MATCH_FOUND / 20 AMBIGUOUS | — |
| boundary completeness | 116 UNKNOWN / 4 INCOMPLETE / 0 FULL | 同 |
| boundary assertion | 109 FULL_MAPPING_CORRECT / 4 CONTAINED_PARTIAL / 7 UNRESOLVED | 同 |
| retrieval sufficiency | 120 any / **110** full | 同 |
| failure attribution | 0 ANY_MATCH / 4 FULL_BOUNDARY / 7 ALIGNMENT_LIMIT | 同 |

runtime 对照：`c512a80` completion 31.27 s，Phase 2A.1 独立复跑 32.39 s，本次 32.16 s。
**无数量级或显著性能回归。**

### 15.2 Profile level

| profile | TP/FP/FN | precision/recall | ambiguity | GREEN | boundary |
|---|---:|---:|---:|---:|---|
| direct | 18/2/2 | .90/.90 | 2/20 | 17 | 1 INCOMPLETE |
| p1 | 18/2/2 | .90/.90 | 2/20 | 17 | 1 INCOMPLETE |
| p2 | 18/2/2 | .90/.90 | 2/20 | 17 | 1 INCOMPLETE |
| p3 | 18/2/2 | .90/.90 | 2/20 | 17 | 1 INCOMPLETE |
| **0.8x** | 19/1/1 | .95/.95 | **10/20** | 7 | 0 |
| 1.25x | 18/2/2 | .90/.90 | 2/20 | 18 | 0 |

### 15.3 Position level（独立重建，非读取 artifact）

`C`=CORRECT_UNAMBIGUOUS，`A`=SAFE_AMBIGUITY，`W`=UNSAFE_WRONG。

| position | direct | p1 | p2 | p3 | 0.8x | 1.25x |
|---|---|---|---|---|---|---|
| E001-I001 | C | C | C | C | C | C |
| E001-I002 | C | C | C | C | A | C |
| E001-I003 | C | C | C | C | C | C |
| E001-I004 | C | C | C | C | C | C |
| E001-I005 | C | C | C | C | C | C |
| E001-I006 | C | C | C | C | A | C |
| E001-I007 | C | C | C | C | A | C |
| **E001-I008** | **W** | **W** | **W** | **W** | C | C |
| E001-I009 | C | C | C | C | A | C |
| E001-I010 | A | A | A | A | A | A |
| E002-I001 | A | A | A | A | A | A |
| E002-I002 | C | C | C | C | A | C |
| E002-I003 | C | C | C | C | C | C |
| E002-I004 | C | C | C | C | A | C |
| E002-I005 | C | C | C | C | C | C |
| E002-I006 | C | C | C | C | A | C |
| E002-I007 | C | C | C | C | C | C |
| E002-I008 | C | C | C | C | A | C |
| E002-I009 | C | C | C | C | C | C |
| E002-I010 | C | C | C | C | C | C |

- 9/20 位置六档全部非歧义完整命中；
- **19/20 位置没有 unsafe wrong**；
- 唯一 unsafe position 仍是 **E001-I008**，且仅在 direct/P1/P2/P3 四档（0.8x/1.25x 反而正确）；
- direct/P1/P2/P3 在全部 20 个位置**逐格相同**，不能当四份独立内容证据。

### 15.4 为什么 0.8x 的 ambiguity 高达 10/20

0.8x 的 TP/FP/FN 反而是六档中最好的（19/1/1），而 ambiguity 是 10/20，GREEN 只有 7。
即 0.8x 的歧义**不是错误，是保守**——8 个 0.8x 的 A 出现在 direct 档为 C 的位置上
（I002/I006/I007/I009、E002-I002/I004/I006/I008）。机制：0.8x 把 6 秒源内容压成 4.8 秒
accused，query 帧从 12 降到 10，可用支持变少、visual margin 变窄，于是同集/跨集竞争线更容易
落进 0.05 的 uniqueness margin。这与 §11 的观察同源（共享内容 + DCT 描述子分辨力），
属于 retrieval competition，不是 temporal bookkeeping。

---

## 16. Pure noise / coherent wrong / 性能

### 16.1 Pure noise（reviewer 自建，12 格全跑）

| episodes / duration | k=8 | k=16 | k=32 |
|---|---|---|---|
| 1 / 30 s | 0 段（480 cand，0.013 s） | 0 段（960，0.025 s） | 0 段（1920，0.055 s） |
| 1 / 300 s | 0 段（4800，0.161 s） | 0 段（9600，0.327 s） | 0 段（19200，0.691 s） |
| 5 / 30 s | 0 段（480，0.013 s） | 0 段（960，0.026 s） | 0 段（1920，0.052 s） |
| 5 / 300 s | 0 段（4800，0.134 s） | 0 段（9600，0.267 s） | 0 段（19200，0.644 s） |

12/12 全部 0 segment；运行时间随候选数线性。

### 16.2 Coherent but wrong line

```text
一条完美的错误线 EP07 acc[0,20] → src[900,920]，a=1.0
   rms=0.0  ir=1.0  coverage=1.0  candidate_tier=GREEN  review_tier=REVIEW_REQUIRED
```

**这是 Temporal Alignment 的设计边界，不是当前 bug。** temporal coherence 只能判断候选是否
构成一条线，无法判断视觉候选本身是否指向正确内容。这条负例必须保留给下一个视觉 backend Gate。

### 16.3 复杂度与性能

单线（无 change point）时 refinement 几乎零成本：

| 输入 | candidates | ON | OFF | overhead |
|---|---:|---:|---:|---:|
| 单线 30 s | 61 | 69.3 ms | 69.4 ms | −0% |
| 单线 60 s | 121 | 139.1 ms | 139.4 ms | −0% |
| 单线 120 s | 241 | 261.5 ms | 260.6 ms | 0% |
| 单线 240 s | 481 | 422.1 ms | 408.8 ms | +3% |

但**一对相邻不相容 segment 的 refinement 成本是超线性的**：

| 输入 | candidates | ON | OFF | overhead | refinement 单独成本 |
|---|---:|---:|---:|---:|---:|
| 0.8→1.25，6+6 s | 25 | 25.7 ms | 22.4 ms | +15% | 3 ms |
| 0.8→1.25，12+12 s | 49 | 333.0 ms | 59.5 ms | +460% | 274 ms |
| 0.8→1.25，24+24 s | 97 | 1125.6 ms | 190.5 ms | +491% | 935 ms |
| 0.8→1.25，48+48 s | 193 | 2646.8 ms | 297.3 ms | +790% | 2350 ms |
| 0.8→1.25，72+72 s | 289 | 4282.9 ms | 423.9 ms | +910% | 3859 ms |

即 144 秒的两段式输入要 **4.3 秒**。经验标度约 `O(n^1.4)`（理论上界是每对
`O(n²)`：`n` 个 split 位置 × 每个位置两次 robust refit，实际被早退检查压平）。

`change_point_maximum_splits` 确实起到界的作用（cap 0 → 1058 ms，cap 4 → 1701 ms，
cap 8/16 → 1705 ms 持平），但它**同时是一个语义 cap**：接受 4 次 refinement 后不再尝试，
后续相邻对保持 extraction 的原始边界，且**没有任何 warning**。在 6 段构造上，cap=4 与
cap=32 的最后一个边界不同（38.0 vs 40.0）。这一点在 `config/default.yaml` 的注释和
ADR-0002 中都没有说明。

ADR-0002 写的"没有新增对全量候选或全量 proposal 的无界 `O(N²)`"字面成立（没有全候选
二次扫描），但没有反映上面这条每对超线性成本。real benchmark 不受影响（每个 6 秒 query
恒速，无 change point），所以 32.16 s 的实测无回归是可信的，但结论不能外推到长 query。

---

## 17. Final Gate questions

### Q1　v4/v5/v6 migration Critical 是否彻底关闭？

**是。** 对任何已发布代码能够产生的旧库（v2 / v3 / v4 / fresh v5 / poisoned v5 / legacy uv=0）
都能正确升级、保留旧 row、写入新字段；三处 failure injection 干净 rollback；
newer-version 与缺表两种输入响亮拒绝。残留的 v3 列不变量缺口（§4.5）不是任何已发布路径能
产生的状态，列为 Minor。

### Q2　temporal-line rival compatibility 是否真正关闭上一轮 Major？

**是。** 判据统一为完整映射比较，A–F 六组直接构造与两个端到端反例全部反转，五个调用点
共用同一 helper，无残留 `source_start` 线身份判据。

### Q3　change-point refinement 是否足够 general？

**是，上一轮的 change-point blocker 已关闭。** ablation 证明它不是 testcase 补丁：关闭后
出现 `a=1.0513`/`a=1.1883`/`a=1.0792`/`a=.8634` 四个混合段，打开后全部消失。
2–6 个 regime、双向、三段/四段、加噪声（wrong-episode / jitter / missing）全部正确。
剩余边界：第二个 regime 短于 4–6 s 会被吞并（§6.8）、≥8 个交替 regime 会丢段（§6.4），
两者都源自被禁止改动的 extraction，列为已知限制而非本阶段必修。

### Q4　boundary completeness 是否把 E001-I008 变成安全的 partial evidence assertion？

**是。** 输出为"已确认 2.5–6.0（source 误差 0.103 s）+ `INCOMPLETE` + 原因 + YELLOW +
`REVIEW_REQUIRED`"，且完整性语义出现在 model / DB / audit / CLI / benchmark 全部输出面，
不存在只给 span 不给标记的面。partial 不进 TP/recall。

### Q5　那 4 个 functional UNSAFE 是否仍应阻断 Phase 2A CLOSED？

**不应该。** 详见 §8 Q4：它们是一个内容位置的 4 个配对 profile；上一轮的阻断理由
（`MATCH_FOUND` + candidate GREEN + 完整时间线断言）三项都已消除；根因经拆分归到 retrieval
的 full-boundary 不足；恢复完整边界只能靠本阶段明令禁止的外推。

### Q6　synthetic ambiguity 37/73 的根因是否已主要落到 DCT visual retrieval？

**是。** 44 对 rival pair 中 A 类 0、D 类 0，B 类 36、C 类 8；同集 pair 的 divergence 中位数
6.0 s、最小 1.0 s（2 grid steps）；visual margin 中位数 0.0027。demo 内容是近周期的
`testsrc`/`testsrc2`，在 144 维 DCT 低频描述子下本就不可分。33→37 的增量中 3 个是原本静默的
错误答案现在带上了歧义信号，净安全性为正。

### Q7　candidate GREEN 是否已只是 internal diagnostic，因而不应作为 Phase 2A production safety blocker？

**是。** ADR-0003 / README / artifact `metric_semantics` 三处一致声明；代码中没有任何生产
放行分支读取 `candidate_tier`；210 个 case 的全部输出 `review_tier` 恒为 `REVIEW_REQUIRED`；
四条排除不变量在 127 个 GREEN segment 上零违反。因此 synthetic GREEN precision 0.35
不应阻断 Phase 2A。但必须保留约束：**在 GREEN 加入 completeness 与视觉证据之前，不得恢复
任何自动放行**。

### Q8　benchmark integrity 是否恢复可信？

**基本恢复，但有一条必修。** grid 标志由机器不变量计算且我独立枚举 1824/1824 全部在 grid 上；
tracked artifact 与 fresh run 一致；SAFE helper 已检查完整映射；merge-negative 断言已 source-aware；
taxonomy 逐案独立重算零分歧。**M-2 必修**：`passed` 只看 gating aggregate，
`behavioural_assertions`（含 `speed_discontinuity_source_aware`）与
`source_grid_invariant` 都不参与 Gate。另有一处既有的小口径不一致：synthetic artifact 的
`aggregate.predicted_segments`=69 与逐 case 求和 70 差 1（`c512a80` 与 `80036d8` 完全相同，
非本轮引入），导致 precision 报 0.5217 而逐 case 重算为 0.5143。

### Q9　是否还存在任何必须在 VDF/DINOv2 bake-off 之前修的 Temporal Alignment blocker？

**有两项，都很窄，都不触碰被禁止的区域：**

- **M-1**：同线两段互相屏蔽 orphan 证据 → 两个 GREEN + UNKNOWN 片段。必须在 bake-off 之前修，
  因为更强的视觉 backend 会改变候选密度，若不修，这个 temporal 层伪影会被误归因给 backend。
  最小修法二选一：`_merge_adjacent()` 的 source 连续性阈值按 slope 缩放；或
  `_classify_boundary_completeness()` 的邻段排除加同线判断。
- **M-2**：把 `behavioural_assertions` 与 `source_grid_invariant` 接进 ideal `passed`。
  必须在 bake-off 之前修，因为 bake-off 会用这个 Gate 判断两个 backend 的对比是否有效。

其余全部剩余问题**不属于 Temporal Alignment 本阶段**：

| 问题 | 归属 |
|---|---|
| 短 regime（<4–6 s）被吞并、≥8 交替 regime 丢段 | extraction 能力边界；Owner 禁止重写；future phase |
| change-point 在 Δslope≤0.10 时边界定位误差 2.5–5.5 s | source-grid 量化的物理下限 |
| synthetic ambiguity 37/73、GREEN precision 0.35 | **visual backend**（DCT 描述子分辨力） |
| coherent wrong line 被接受 | **visual backend**；temporal coherence 无法自证视觉正确 |
| `BoundaryCompleteness.FULL` 无证据规则 | 新能力（negative-boundary evidence），future phase |
| out-of-range ±0.025 量化带（真 0.74 报 0.75 且 GREEN；真 1.51 报 1.50 且 GREEN） | grid 量化不可分区；测试未覆盖该带（上一轮测试缺口 #4 仍未关闭），Minor |
| endpoints 仍是 `nominal_grid_time` | Phase 3 Evidence Analysis 边界 |
| change-point 每对超线性成本 | 性能，非正确性；real benchmark 无回归；随长 query 再评估 |
| v3 列不变量缺口 | 防御性，无已发布路径可达，Minor |

### Q10　Phase 2A 最终应为？

```text
CONDITIONAL PASS
```

上一轮的 1 项 Critical + 1 项 Blocker + 2 项 Major **全部关闭并经独立复现**。
剩余的两项必修（M-1、M-2）都窄、都在 Temporal Alignment 本阶段范围内、都不需要碰
sparse voting / robust fitting / piecewise architecture / DCT / FPS / top-k。
**这两项落地并复跑 ideal + real 后，我认为 Phase 2A 可以标记 PASS / CLOSED**，
其余剩余问题应作为 visual backend Gate 与 future phase 的显式输入，而不是 Phase 2A 的债务。

---

## 18. 分项 verdict

| 维度 | Verdict | 理由 |
|---|---|---|
| **core temporal mechanics** | **PASS** | 主体逐字节未改；compatibility 判据在 A–F 全组正确并统一用于五处；两个上轮端到端反例反转 |
| **change-point handling** | **CONDITIONAL PASS** | ablation 证明是一般能力，2–6 regime / 双向 / 加噪声全部正确；残留短 regime 吞并与多 regime 退化属 extraction 边界；每对超线性成本未在 ADR 中反映 |
| **rival / ambiguity safety** | **PASS** | 44 对 rival pair 中 A/D 类为 0；33→37 的增量含 3 个原本静默的错误答案 |
| **boundary completeness semantics** | **CONDITIONAL PASS** | E001-I008 语义正确；五种 false-INCOMPLETE 构造全部不误标；**M-1**：同线两段互相屏蔽 orphan 证据 → 双 GREEN + UNKNOWN |
| **retrieval attribution** | **PASS** | 上轮反例修复；4095 子集穷举证明 tolerated hole 不抬高 hit count；定义非同义反复且显式标注 benchmark-only |
| **schema migration** | **PASS** | 6 类真实 fixture 全部升级成功；poisoned v5 复现并修复；3 处 rollback 干净；对抗输入响亮拒绝 |
| **benchmark integrity** | **CONDITIONAL PASS** | 1824/1824 on-grid 独立枚举；tracked=fresh；taxonomy 逐案零分歧；**M-2**：`passed` 不含行为断言与 grid 不变量 |
| **production-release safety** | **PASS** | 210 个 case 输出 `review_tier` 恒为 `REVIEW_REQUIRED`；127 个 GREEN 零违反四条排除不变量；cap 全链路贯通且无 `getattr` 回退 |

---

## 19. 与 completion report 的事实性出入

按严重度排列，全部已在正文给出证据：

1. **§3 "没有对真实 DB 执行 initialize 或 migration"** —— 对 `real-retrieval` 源索引库准确；
   但默认库 `workspace/db/ipvm.db` 确实被从 poisoned v5 迁到 v6（`.pre-v6.bak` 为 uv=5/36 列），
   `benchmark_demo/*` 同样被迁移。这些库均无案件数据，**无数据风险**，但措辞应收窄。（§4.6）
2. **§7 归因** —— "Phase 2A.2 关闭的是 near-equivalent same-line 机制"在当前状态成立，
   但 `c512a80` 同样是 0 对同线 pair，所以那 33 个歧义不是该机制产生的。真实机制是
   ambiguity 语义的有意扩大，report 反而低估了自己的安全性收益。（§11.5）
3. **§8 复杂度** —— "没有新增全候选无界 `O(N²)`"字面成立，但未提及每对相邻不相容 segment
   的超线性成本（144 s 两段输入 4.3 s，约 10× OFF）。（§16.3）
4. **§2.2 / config 注释** —— `change_point_minimum_side_seconds: 3.0` 暗示 3 s 下界，
   实测可恢复的最短第二 regime 是 4–6 s（随 Δslope 变化）。（§6.8）
5. **§4 jitter** —— "旧的未 snap raw probe（a=1.2946, 19/24）不是当前 grid-realistic
   benchmark 输入"对 ideal benchmark 成立；但 `tests/test_alignment.py` 的 jitter 单元测试
   **仍使用未 snap 的生成器**，实测输出正是 `a=1.2946 / 19 of 24 / IR=.7917 / RMS=.2587 /
   YELLOW`（测试内注释准确）。snapped 变体（ideal 用）实测
   `a=1.2581 / 21 of 21 / IR=1.0 / RMS=.2776`，与 report 一致。
   `inlier_count >= 18` 的推导（每 4 帧周期至少 3 个观测支持一条线）成立、非快照；
   但 `speed_ratio ± 0.05`（实测误差 .0446，余量 .0054）与 `inlier_ratio >= 0.75`
   （实测 .7917）余量很小，属脆弱标定，Minor。（§13 相关）
6. **§7 "54 个 rival pair"** —— 口径为 `predictions × alternatives`；按
   "AMBIGUOUS case 的 alternatives"计为 44。两种口径下同线 pair 均为 0，结论不受影响。

---

## 20. 建议给 Architecture Owner 的最小闭环

只有两项，均不进入任何被禁止的区域：

1. **M-1**：二选一——
   (a) `_merge_adjacent()` 的 source 连续性阈值改为随 slope 缩放；或
   (b) `_classify_boundary_completeness()` 的邻段排除加 `_same_temporal_line()` 判断。
   回归：同线 speed 1.25/1.50 + 1.5 s hole 必须输出 1 段，或两段均标 `INCOMPLETE` 且非 GREEN。
2. **M-2**：`passed` 改为同时要求 gating recall/precision、`behavioural_assertions` 全 True、
   `source_grid_invariant.all_candidates_on_grid`。

建议同时（非阻断）：把 `change_point_minimum_side_seconds` 的注释改成实测下界，
在 ADR-0002 复杂度段落补一句每对 refinement 的超线性成本与 `change_point_maximum_splits`
的语义副作用，并补上 out-of-range 0.72/0.74/1.51/1.53 的参数化测试。

---

## Final Verdict

```text
CONDITIONAL PASS
```

停止于独立审查。未修改 production code、未修改 tests、未 commit、未 push，
未进入 VDF / DINOv2 / SigLIP / FAISS / crop / mirror / subtitle / PiP / audio / Phase 3。
