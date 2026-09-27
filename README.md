# IP Video Matcher

> **2026-09-27 起，当前可用的工具是 `pilot/`（比对表机器填写 + 比对意见起草 + 本地核对页）。**
> 接手请先读 [`AGENTS.md`](AGENTS.md)；运行步骤见 [`pilot/README.md`](pilot/README.md)；决策记录见 [`docs/workorders/DECISIONS.md`](docs/workorders/DECISIONS.md)。
> 下面是早期"视频时间轴匹配"路线（`src/ipvm`）的历史说明；该路线的实验目录已按所有者授权删除（D-053）。

给知识产权律师使用的本地视频时间轴匹配工具。当前状态：

```text
Phase 1 Gate                         FAIL
Phase 1.5                            CONDITIONAL PASS
Phase 1.5.1 Safety Closure           completed
Phase 2A-0 Retrieval Characterization harness completed
Phase 2A-0.1 Platform-like Transcode Characterization completed
Phase 2A-0.2 Statistical / Reporting Hardening completed
Phase 2A Temporal Alignment v2        implementation complete; review pending
```

当前 pipeline 的目标仍是：

```text
被诉时间轴 → 原剧集数 → 原剧时间轴
```

Temporal Alignment v2 已用 sparse slope-offset voting、robust local fit 和 iterative
piecewise extraction 替换 constant-offset proposal。DCT、source/query 2 FPS、top-k=8
保持不变。没有实现 8 FPS 外部验证、DINOv2、FAISS、镜像/crop/audio/Gemma、
合理使用、网页抓取或 Excel。

## 安装

要求：Python 3.11+、FFmpeg/FFprobe。

```bash
brew install ffmpeg python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-user -e '.[dev]'
python -m ipvm doctor
python -m ipvm init-project
```

## 输入目录

```text
input/
├── originals/
│   ├── EP01.mp4
│   ├── EP02.mp4
│   └── metadata.json
├── cases/
│   └── CASE_0001/
│       └── accused.mp4
└── golden/
```

`metadata.json` 是可选的显式集数映射，优先级高于文件名。没有 metadata 时只解析明确的 `EP01`、`E01`、`S01E01`、`第01集` pattern；无法唯一解析时返回 `NEED_METADATA`。

真实媒体、案件名称、文件 hash、数据库、索引和输出报告均在 Git ignore 范围内。

## CLI

```bash
python -m ipvm init-project
python -m ipvm index-originals
python -m ipvm index-originals --force
python -m ipvm analyze CASE_0001
python -m ipvm analyze-all
python -m ipvm benchmark --demo
python -m ipvm benchmark --demo --all-diagnostic
python -m ipvm retrieval-benchmark
python -m ipvm temporal-benchmark
python -m ipvm doctor
```

`retrieval-benchmark` 要求现有原片索引状态为 `COMPLETE`，按全片时间位置分层，并在每集内随机打散、均衡覆盖 10 个 source-grid phase 桶，以避免 phase 固定绑定 source 时间位置。Direct query 在 seek 后重置 PTS，再按本地 2 FPS 网格取帧；platform-like transcode proxies 使用相同区间，只改变 compression / resolution / codec。所有 profile 均测量对 2 FPS source index 的 strict `recall@1/8/32/128` 与 `alignment_usable_recall@1/8/32/128`（±1 source grid step）：

```bash
python -m ipvm retrieval-benchmark \
  --samples-per-episode 3 \
  --query-duration 6 \
  --mode direct \
  --mode p1 \
  --mode p2 \
  --mode p3
```

默认输出：

```text
output/retrieval/retrieval_characterization.json
output/retrieval/retrieval_characterization_aggregate.json
output/retrieval/retrieval_characterization.md
```

报告使用 `E001` 形式的匿名 episode alias，不写原文件名、路径、SHA-256 或案件标识。完整 JSON 的逐帧观测只保存在 ignored output；另一个 aggregate JSON、CLI 和 Markdown 输出匿名聚合。每个 profile 包含 interval count、distinct content position count、effective sample size、ICC、interval-level cluster bootstrap 95% CI，以及 `ABOVE / STRADDLES / BELOW` 三态 threshold band。Harness 只输出测量，不选择后续路线。

`temporal-benchmark` 默认运行不经过视觉 retrieval 的 deterministic ideal candidate
benchmark；可选择在已配置且完成索引的 ignored real originals 上运行完整 matcher：

```bash
python -m ipvm temporal-benchmark
python -m ipvm temporal-benchmark \
  --real-media \
  --samples-per-episode 10 \
  --query-duration 6 \
  --include-speed-profiles
```

Real profile 为 direct、P1/P2/P3 platform-like proxies，以及显式选择的 0.8× / 1.25×
speed-only。Harness 不加入 crop、mirror、subtitle 等变量，并把 source-aware miss 粗分
为 `RETRIEVAL_LIMIT` 或 `ALIGNMENT_LIMIT`。

## 安全语义

匹配状态为 `MATCH_FOUND`、`MATCH_NOT_FOUND`、`AMBIGUOUS`、`INCOMPLETE_INDEX` 或 `ERROR`。

系统仍计算 `candidate_tier`，但外部像素验证未实现，因此 candidate GREEN 不具有自动放行含义。所有原本可能成为 GREEN 的结果，对 CLI、audit、数据库和未来 exporter 的有效 `review_tier` 均为 `REVIEW_REQUIRED`。

同一 accused 区间存在跨 episode 主要竞争候选且 `uniqueness_margin < 0.05` 时，case 返回 `AMBIGUOUS`。Primary 只供展示，alternatives 写入 audit 和数据库，且 `is_primary=false`。

Segment contradiction 不再清空整案。Case status 为 `AMBIGUOUS`，冲突 segment 携带 conflict graph 信息；未涉冲突的独立 segment 保留。

`speed_ratio = d(source_time) / d(accused_time)`：大于 1 表示被诉视频加速，小于 1 表示减速。有效拟合跨度不足 3 秒时为 `null`；合并 segment 时用完整 inliers 重新拟合，不做算术平均。

## Synthetic benchmark

A–J 每种 transformation 现在包含 5 个独立 source intervals、独立 off-grid phase，并尽量覆盖不同 episode。报告按 transformation 输出 recall、precision、source boundary error、false positives 和 status distribution。

最新本地 Temporal v2 完整 diagnostic（73 cases、80 个非歧义 GT）：

```text
source-aware recall@1s       45.0%
segment precision@1s         52.2%
candidate green precision    21.1%   diagnostic only
A–J source-aware recall      35.0%
A–J segment precision        43.8%
A–J false positives          27 segments / 104.5s
CRF20/CRF26 shared intro     AMBIGUOUS
incomplete index             refused
```

Phase 1 gate subset 仍为 FAIL：source-aware recall 68.6%、precision 66.7%、source
boundary MAE 1.319 秒。Pure negative、CRF20/CRF26 跨集歧义和 incomplete-index
refusal 三项安全检查通过。以上均为低分辨率 synthetic regression，不是实际案件
准确率。Temporal v2 没有为 benchmark 放宽阈值。

本地机器报告位于 `output/reports/phase2a-temporal-phase1-gate.json` 和
`output/reports/phase2a-temporal-after.json`，不提交 Git。

## Phase 2A-0 状态

Retrieval harness 已可运行并带 JSON schema、匿名 aggregate template 与测试。Phase 2A-0.1 新增 `p1` / `p2` / `p3` platform-like transcode proxies；Phase 2A-0.2 新增 `alignment_usable_recall@k`、interval-level cluster bootstrap CI、ICC / effective sample size，以及 `ABOVE / STRADDLES / BELOW` 三态 threshold band。

已提交的匿名 aggregate 位于 `benchmark/retrieval/results/`，只含 `E001` / `E002`、`E001-I017` 别名、归一化 source position、benchmark config、aggregate statistics、CI 与 profile 名；不含真实文件名、路径、SHA-256 或案件标识。真实逐帧观测仍在 ignored output 中。

Phase 2A-0.3 Safety & Semantics Closure：readiness gate 指标定为 `alignment_usable_recall@8`（ADR-0005），strict recall 保留为 localization diagnostic；裸 `threshold_band` 移除，改为 `strict_threshold_band` 与 `alignment_usable_threshold_band` 两个具名四态 band（`ABOVE / STRADDLES / BELOW / UNKNOWN`）；ICC / design effect / effective sample size / CI 按 strict 与 alignment_usable 分别建模；`source_grid_period_seconds` 由 `1.0 / coarse_fps` 推导；platform transcode 使用确定性单线程编码参数。

平台档结果的正确解释：**compression / resolution / codec 的扰动低于当前低频 DCT descriptor 的主要灵敏度范围**（实测 `1 - cos ≈ 6e-6 ~ 1.3e-5`，比 median score margin `3.7e-3` 低 2–3 个数量级）。不得泛化为 crop / mirror / subtitle / picture-in-picture / 摄像头翻拍 的 robustness 结论。

Architecture Owner 已将 Phase 2A-0 判定为 `PASS / CLOSED`，并授权在上述锁定输入下
实现 Temporal Alignment v2。扩大 top-k/VectorIndex、提高 source FPS 和 DINOv2
仍未实现或预选。

## Phase 2A Temporal Alignment v2

生产 temporal mapping 使用：

```text
source_time = speed_ratio * accused_time + fitted_offset
speed_ratio ∈ [0.75, 1.50]
```

实现包含 episode-local sparse slope/offset soft voting、从 FPS 推导的 ±1 source-grid
容差、bounded deterministic robust refit、continuity split、iterative evidence claiming、
piecewise multi-episode extraction、cross-episode ambiguity，以及 source reuse 语义。
Phase 2A.2 增加统一 temporal-line compatibility、evidence-backed change-point refinement、
`BoundaryCompleteness`、segment-cap structured warning，以及贯通 matcher / DB / audit / CLI /
benchmark 的 truncation diagnostics。诊断字段包括 fitted offset、residual RMS/max、
inlier/candidate count 和 inlier ratio。

当前 fresh ideal candidate benchmark 的 12 个 gating cases 含 16 个 GT segments，
TP/FP/FN 为 16/0/0，precision/recall 均 100%，speed MAE 0.00693；另有 5 个非 gating
safety diagnostics。1824 个 candidate timestamps 全部位于 2 FPS source grid。机器结果位于
`benchmark/temporal/results/phase2a-temporal-ideal-v0.1.json`。

两集 real segment characterization 使用 20 个独立 content positions、6 个共享位置的
profiles（120 cases）：总体 TP/FP/FN 为 109/11/11，source-aware recall/precision
90.83%，episode correctness 94.17%，source boundary MAE 0.199 秒。四类 outcome 为
96 `CORRECT_UNAMBIGUOUS_MATCH`、20 `SAFE_AMBIGUITY_CONTAINS_GT`、4
`UNSAFE_WRONG_MATCH`、0 `NO_MATCH`。4 个 unsafe case 均同时以正交语义标为
`CONTAINED_PARTIAL_EVIDENCE`，不提高 TP/recall。`episode_n=2`，不能解释为跨集稳定性
估计。详见 `docs/review/PHASE2A.2-INTEGRITY-BOUNDARY-CLOSURE-REPORT.md`。

当前 endpoints 仍是 `nominal_grid_time`。Audit 明确标记 authoritative `media_pts`
尚未精修且不能法律交付；没有硬编码 PTS correction。

## 测试

```bash
pytest
```

当前结果：见 Phase 2A.2 completion report。新增 temporal-line compatibility、双向及三段
speed change、boundary completeness、segment-cap 贯通、taxonomy adversarial、source-grid
invariant、genuine v4 / poisoned-v5 / rollback migration 与 ideal benchmark 测试。

## Schema migration

SQLite schema version 为 6，显式阶梯为 `v3 → v4 → v5 → v6`。每一步使用
`BEGIN IMMEDIATE`，transaction 内不调用 `executescript`；失败会 rollback，成功后才更新
version。需要迁移或 repair 时先创建 `<database>.pre-v6.bak`。Poisoned v5 会先 introspect、
原子 repair、validate，再进入 v6；缺 required table 则备份后响亮失败。详见
`docs/migrations/SCHEMA-V4.md`、`SCHEMA-V5.md`、`SCHEMA-V6.md`。

Schema v4 本身不更换 DCT cache identity，因此已通过现有 identity/completeness 检查的
v3 source index 无需仅因数据库迁移而重建。未知 legacy index 仍会被拒绝；需要时执行：

```bash
python -m ipvm index-originals --force
```

## Repository tree

```text
IP-Video-Matcher/
├── benchmark/
│   ├── results/phase1_5_gate.json
│   ├── retrieval/
│   │   ├── README.md
│   │   ├── schema-v0.1.json
│   │   └── anonymized-aggregate-template.json
│   └── temporal/results/phase2a-temporal-ideal-v0.1.json
├── config/default.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ARCHITECTURE-REVISION-0.1.1.md
│   ├── adr/ADR-0001...ADR-0005
│   ├── migrations/SCHEMA-V2.md ... SCHEMA-V6.md
│   └── review/
├── src/ipvm/
│   ├── retrieval_benchmark.py
│   └── temporal_benchmark.py
├── tests/
├── pyproject.toml
└── README.md
```

## 已知问题

- Phase 1 Gate 仍未通过；Temporal v2 的端到端 synthetic precision/FP/source MAE 比
  constant-offset before 更差，虽然 episode recall 和 variable-speed slope error 改善。
- Candidate GREEN 仅是内部 alignment-quality diagnostic；它不证明 boundary complete。
  自动放行保持禁用，所有生产结果仍为 `REVIEW_REQUIRED`。
- Real segment benchmark 只有两集、20 个独立 content positions；不能估计跨集方差或
  当作案件准确率。
- Real attribution 已区分“足以产生任一 temporal match”与“足以恢复完整边界”，但仍是
  GT-aware benchmark 诊断，不能替代生产边界证明。
- `source_start/source_end` 仍是 nominal grid time，尚未精修到 authoritative media PTS。
- 没有跨集重复区间的索引期预计算；当前只按运行期竞争候选和 margin 降级。
- DINOv2、FAISS、4/8 FPS source index、8 FPS external verification、镜像、crop、audio、Gemma、合理使用、网页抓取和 Excel 都未实现。
