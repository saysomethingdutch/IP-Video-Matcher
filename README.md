# IP Video Matcher

给知识产权律师使用的本地视频时间轴匹配工具。当前状态：

```text
Phase 1 Gate                         FAIL
Phase 1.5                            CONDITIONAL PASS
Phase 1.5.1 Safety Closure           completed
Phase 2A-0 Retrieval Characterization harness completed
Phase 2A-0.1 Platform-like Transcode Characterization completed
Phase 2A-0.2 Statistical / Reporting Hardening completed
Temporal Alignment v2                not started
```

当前 pipeline 的目标仍是：

```text
被诉时间轴 → 原剧集数 → 原剧时间轴
```

本轮只闭合安全语义、benchmark/evaluator、数据库迁移、缓存身份和 retrieval 测量工具。没有实现 Temporal Alignment v2、8 FPS 外部验证、DINOv2、FAISS、镜像/crop/audio/Gemma、合理使用、网页抓取或 Excel。

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

## 安全语义

匹配状态为 `MATCH_FOUND`、`MATCH_NOT_FOUND`、`AMBIGUOUS`、`INCOMPLETE_INDEX` 或 `ERROR`。

系统仍计算 `candidate_tier`，但外部像素验证未实现，因此 candidate GREEN 不具有自动放行含义。所有原本可能成为 GREEN 的结果，对 CLI、audit、数据库和未来 exporter 的有效 `review_tier` 均为 `REVIEW_REQUIRED`。

同一 accused 区间存在跨 episode 主要竞争候选且 `uniqueness_margin < 0.05` 时，case 返回 `AMBIGUOUS`。Primary 只供展示，alternatives 写入 audit 和数据库，且 `is_primary=false`。

Segment contradiction 不再清空整案。Case status 为 `AMBIGUOUS`，冲突 segment 携带 conflict graph 信息；未涉冲突的独立 segment 保留。

`speed_ratio = d(source_time) / d(accused_time)`：大于 1 表示被诉视频加速，小于 1 表示减速。有效拟合跨度不足 3 秒时为 `null`；合并 segment 时用完整 inliers 重新拟合，不做算术平均。

## Synthetic benchmark

A–J 每种 transformation 现在包含 5 个独立 source intervals、独立 off-grid phase，并尽量覆盖不同 episode。报告按 transformation 输出 recall、precision、source boundary error、false positives 和 status distribution。

最新本地完整 diagnostic（73 cases、80 个非歧义 GT）：

```text
source-aware recall@1s       42.5%
segment precision@1s         57.6%
candidate green precision    66.7%   diagnostic only
A–J source-aware recall      31.7%
A–J segment precision        46.3%
A–J false positives          22 segments / 74.5s
CRF20/CRF26 shared intro     AMBIGUOUS
incomplete index             refused
```

Phase 1 gate subset 仍为 FAIL：source-aware recall 71.4%、precision 78.1%，且有 3 个 episode-level boundary misses。Pure negative、CRF20/CRF26 跨集歧义和 incomplete-index refusal 三项安全检查通过。以上均为低分辨率 synthetic regression，不是实际案件准确率。

本地机器报告位于 `output/reports/phase1_5_1_gate.json` 和 `output/reports/phase1_5_1_a_to_m_diagnostic.json`，不提交 Git。

## Phase 2A-0 状态

Retrieval harness 已可运行并带 JSON schema、匿名 aggregate template 与测试。Phase 2A-0.1 新增 `p1` / `p2` / `p3` platform-like transcode proxies；Phase 2A-0.2 新增 `alignment_usable_recall@k`、interval-level cluster bootstrap CI、ICC / effective sample size，以及 `ABOVE / STRADDLES / BELOW` 三态 threshold band。

已提交的匿名 aggregate 位于 `benchmark/retrieval/results/`，只含 `E001` / `E002`、`E001-I017` 别名、归一化 source position、benchmark config、aggregate statistics、CI 与 profile 名；不含真实文件名、路径、SHA-256 或案件标识。真实逐帧观测仍在 ignored output 中。

Phase 2A-0.3 Safety & Semantics Closure：readiness gate 指标定为 `alignment_usable_recall@8`（ADR-0005），strict recall 保留为 localization diagnostic；裸 `threshold_band` 移除，改为 `strict_threshold_band` 与 `alignment_usable_threshold_band` 两个具名四态 band（`ABOVE / STRADDLES / BELOW / UNKNOWN`）；ICC / design effect / effective sample size / CI 按 strict 与 alignment_usable 分别建模；`source_grid_period_seconds` 由 `1.0 / coarse_fps` 推导；platform transcode 使用确定性单线程编码参数。

平台档结果的正确解释：**compression / resolution / codec 的扰动低于当前低频 DCT descriptor 的主要灵敏度范围**（实测 `1 - cos ≈ 6e-6 ~ 1.3e-5`，比 median score margin `3.7e-3` 低 2–3 个数量级）。不得泛化为 crop / mirror / subtitle / picture-in-picture / 摄像头翻拍 的 robustness 结论。

Architecture Owner 收到 real recall@k 与平台档结果后按既定决策树裁定。当前代码不实现或预选 Temporal Alignment v2、扩大 top-k/VectorIndex、提高 source FPS 或 DINOv2 中的任何路线。

## 测试

```bash
pytest
```

当前结果：`39 passed`。在原有 coverage 之上新增 alignment-usable 边界、cluster bootstrap determinism、threshold 三态、profile isolation、interval count、真实 decode off-grid 与 anonymized aggregate 敏感性测试。

## Schema migration

SQLite schema version 为 3。迁移使用 `BEGIN IMMEDIATE`，transaction 内不调用 `executescript`；失败会 rollback。需要迁移时先创建 `<database>.pre-v3.bak`。详见 `docs/migrations/SCHEMA-V3.md`。

旧索引仍需强制重建：

```bash
python -m ipvm index-originals --force
```

## Repository tree

```text
IP-Video-Matcher/
├── benchmark/
│   ├── results/phase1_5_gate.json
│   └── retrieval/
│       ├── README.md
│       ├── schema-v0.1.json
│       └── anonymized-aggregate-template.json
├── config/default.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ARCHITECTURE-REVISION-0.1.1.md
│   ├── adr/ADR-0001...ADR-0005
│   ├── migrations/SCHEMA-V2.md, SCHEMA-V3.md
│   └── review/
├── src/ipvm/
│   └── retrieval_benchmark.py
├── tests/
├── pyproject.toml
└── README.md
```

## 已知问题

- Phase 1 Gate 仍未通过；off-grid retrieval 和当前 constant-offset alignment 都有测量到的失败。
- Candidate GREEN 在完整 diagnostic 中仍可能错误，所以自动放行保持禁用。
- Temporal Alignment v2 只有 ADR 草案，未实现。
- 没有真实 golden set 或真实规模性能数据；real retrieval 目前只有两集匿名 aggregate（`episode_n=2`，不能估计集间方差）。
- 没有跨集重复区间的索引期预计算；当前只按运行期竞争候选和 margin 降级。
- DINOv2、FAISS、4/8 FPS source index、8 FPS external verification、镜像、crop、audio、Gemma、合理使用、网页抓取和 Excel 都未实现。
