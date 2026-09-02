# Phase 1.5.1 Safety Closure 完成报告

日期：2026-09-02

结论：Phase 1.5.1 代码与回归已完成；Phase 2A-0 measurement harness 已完成。仓库没有真实原片，因此没有真实 retrieval recall@k 结论。实现停止于 Phase 2A-0，没有进入 Temporal Alignment v2。

## 1. Safety Closure

### GREEN

系统保留 `candidate_tier=GREEN` 用于诊断，但有效 `review_tier` 统一为 `REVIEW_REQUIRED`。CLI、audit 和 schema v3 数据库不会输出可被解释为自动放行的 GREEN。8 FPS external verification 未实现。

### 跨集歧义

主要竞争候选来自不同 episode 且 `uniqueness_margin < 0.05` 时，case status 为 `AMBIGUOUS`。Primary 保留用于展示，主要 alternatives 写入 audit 和数据库，且 `is_primary=false`。CRF20/CRF26 独立编码共享片头的 synthetic regression 返回 `AMBIGUOUS`。

### Contradiction

Case status 为 `AMBIGUOUS`，但不再清空整案。冲突 segment 写入 `segment_id`、`conflict`、`conflict_with`、`conflict_reason`；audit 写入 conflict graph。未参与冲突的独立 segment 保留。

### Evaluator

- prediction/GT 使用确定性一对一 assignment；重复 prediction 计 FP；
- accused/source duration 和 speed ratio 需要物理自洽；
- 新增 `green_precision`、`green_recall`、`review_required_precision`；
- `speed_ratio_mae` 只统计非 1.0× GT；
- 报告固定注明 candidate GREEN 不具有自动放行语义。

### A–J

每种 transformation 生成 5 个独立 source intervals，使用独立 off-grid phase，并轮换 episode。报告按 transformation 输出 recall、precision、source boundary error、false-positive segments/seconds 和 status distribution。

### speed_ratio

ADR-0002 已固定：

```text
speed_ratio = d(source_time) / d(accused_time)
```

大于 1 表示被诉视频加速，小于 1 表示减速。拟合有效跨度不足 3 秒时输出 `null`。相邻 segment 合并使用完整 inliers 重拟合 slope，不做算术平均。

### Migration 和 cache

Schema version 为 3。Migration 使用 `BEGIN IMMEDIATE`、逐条 DDL、异常 rollback 和 SQLite backup API；中途失败回归确认原 schema 与数据不发生半迁移。

Cache identity 包含 source SHA-256、encoder name/version/config、algorithm version、coarse FPS、frame dimensions、grayscale/scaling/timestamp preprocessing。旧 DCT 或旧采样参数不会与新索引共用 cache 文件名。

## 2. Phase 2A-0 harness

命令：

```bash
python -m ipvm retrieval-benchmark
```

Harness 对 source/query 2 FPS 的 direct 与 ordinary re-encode off-grid queries 报告：

- recall@1、@8、@32、@128；
- correct-source rank distribution、median rank、p90 rank；
- score margin；
- source/query temporal phase、匿名 episode、query timestamp。

输出为标准 JSON 和 Markdown。JSON schema 与匿名 aggregate template 位于 `benchmark/retrieval/`。实际输出默认进入 ignored 的 `output/retrieval/`；报告不包含原文件名、路径、SHA-256 或案件名称。`decision` 固定为 `null`，由 Architecture Owner 应用决策树。

当前 `input/originals/` 为空，因此未运行真实素材 2A-0。Synthetic benchmark 数字没有被写成真实 retrieval 结论。

## 3. 测试与 diagnostic

```text
pytest                         26 passed
CRF20/CRF26 ambiguity          PASS
duplicate prediction FP        PASS
duration/slope adversarial     PASS
partial conflict retention     PASS
migration rollback             PASS
retrieval rank/recall schema   PASS
```

重建后的完整 synthetic diagnostic：

```text
cases                          73
non-ambiguity GT               80
source-aware recall@1s         42.5%
segment precision@1s           57.6%
candidate green precision      66.7% (diagnostic only)
A–J source-aware recall        31.7%
A–J segment precision          46.3%
A–J false positives            22 segments / 74.5s
```

Phase 1 gate subset：

```text
source-aware recall@1s         71.4%   FAIL
segment precision@1s           78.1%   FAIL
source boundary                0.628s, 3 episode-level misses   FAIL
pure negative                  PASS
CRF20/CRF26 ambiguity          PASS
incomplete-index refusal       PASS
```

Phase 1 Gate 维持 FAIL，没有通过改阈值或替换测量口径掩盖。

本地机器报告：

- `output/reports/phase1_5_1_gate.json`
- `output/reports/phase1_5_1_a_to_m_diagnostic.json`

两份报告属于 ignored output，不提交 Git。

## 4. 性能

完整 synthetic diagnostic：5 个 source assets、82.5 秒 source media、73 cases、约 349.9 秒 query media。Cold index 约 0.44 秒，analysis 约 4.47 秒，RTF 约 0.0128。观测 peak RSS lower bound 约 133.5 MB，保守相加 upper bound 约 183.6 MB。

这些是低分辨率 synthetic regression 的原始数据，不外推真实 30 小时剧集。真实性能和真实 retrieval recall 均未测。

## 5. 已知问题

- Phase 1 Gate 仍失败；
- Candidate GREEN 在完整 diagnostic 中 precision 不是 1.0，自动放行必须继续禁用；
- 当前 constant-offset matcher 仍有 off-grid 和变速结构限制；
- 当前跨集歧义只处理运行期候选竞争，没有索引期重复区间预计算；
- 没有真实 golden set、真实 retrieval recall@k 或真实规模性能；
- Temporal Alignment v2、DINOv2、FAISS、source 4/8 FPS、8 FPS verification 及其它 Phase 2 能力均未实现。

## 6. 需要 Architecture Owner 决策

1. 提供或指定可用于 2A-0 的真实原片范围后，是否批准在本地运行匿名 retrieval measurement；
2. 收到真实 recall@1/8/32/128 后，按既定决策树选择 Temporal Alignment v2、top-k/VectorIndex 研究，或后续采样/backend 比较；
3. 外部像素验证完成后，何时恢复 GREEN 自动放行；恢复前 Gate 必须满足 `green_precision == 1.0`；
4. 后续是否新增跨 episode 重复区间的索引期预计算。

在上述决策前，项目停在 Phase 2A-0。
