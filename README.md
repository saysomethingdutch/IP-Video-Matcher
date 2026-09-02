# IP Video Matcher

给知识产权律师使用的本地视频时间轴匹配工具。当前开发状态：

```text
Phase 1 = CONDITIONAL PASS
Phase 1.5 Measurement & Safety Hardening = implemented
Phase 2 = not started
```

当前 pipeline 输入原剧全集和被诉视频，输出：

```text
被诉时间轴 → 原剧集数 → 原剧时间轴
```

Phase 1.5 没有增加镜像、变速、crop、字幕、audio 或 AI 能力。它修正了 benchmark 指标、匹配状态、歧义处理、索引完整性、项目作用域、审计字段和性能测量。

## 安装

要求：Apple Silicon Mac、Python 3.11+、FFmpeg/FFprobe。

```bash
brew install ffmpeg python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-user -e '.[dev]'
python -m ipvm doctor
python -m ipvm init-project
```

## 输入

```text
input/
├── originals/
│   ├── EP01.mp4
│   ├── EP02.mp4
│   └── metadata.json        # 可选，但优先级最高
├── cases/
│   └── CASE_0001/
│       └── accused.mp4
└── golden/                  # 真实 golden set；禁止提交 Git
```

显式集数映射：

```json
{
  "episodes": {
    "第一集最终版.mp4": "EP01",
    "第二集最终版.mp4": "EP02"
  }
}
```

没有 metadata 时只解析明确的 `EP01`、`E01`、`S01E01`、`第01集` pattern。无法唯一确定时返回 `NEED_METADATA`。

`input/` 只读；缓存写入 `workspace/`；audit 与报告写入 `output/`。案件材料、模型、数据库和常见视频格式均被 `.gitignore` 排除。

## CLI

```bash
python -m ipvm init-project
python -m ipvm index-originals
python -m ipvm index-originals --force
python -m ipvm analyze CASE_0001
python -m ipvm analyze-all
python -m ipvm benchmark --demo
python -m ipvm benchmark --demo --all-diagnostic
python -m ipvm doctor
```

`export` 只保留 CLI 契约，返回 `PHASE_NOT_IMPLEMENTED`，不会读写 Excel。

## 状态与分数

匹配状态：

- `MATCH_FOUND`
- `MATCH_NOT_FOUND`
- `AMBIGUOUS`
- `INCOMPLETE_INDEX`
- `ERROR`

`match_score` 是内部证据强度，不是正确概率。GREEN 还要求完整索引、唯一性 margin、时间残差、coverage、temporal consistency 和 segment consistency 同时过关。镜像与裁剪尚未检测，因此 audit 中的 `mirrored`、`cropped` 为 `null`，不是 `false`。

## Phase 1.5 Benchmark

生成器包含：

- A–J 原 synthetic 变换诊断；
- 20 条 off-grid 直接剪切/重编码回归样本；
- K：pure negative；
- L：跨集 exact duplicate ambiguity；
- M：faststart 残缺原片索引。

新指标分别报告：

- `episode_recall`；
- `source_aware_recall_at_1s`；
- `segment_precision_at_1s`；
- false-positive segments/seconds；
- accused/source boundary error；
- unmatched ground truth/predictions；
- ambiguity 与 incomplete-index refusal；
- `speed_ratio_mae`。

当前机器生成的 Phase 1.5 gate 结果：

```text
source-aware recall@1s     78.3%  FAIL (<95%)
segment precision@1s      90.0%  FAIL (<98%)
source boundary MAE        0.275s, but 3 episode-level misses  FAIL
pure negative FP seconds   0      PASS
exact duplicate ambiguity         PASS
incomplete index refusal          PASS
```

因此新 Gate 总结果是 **FAIL**。这说明 Phase 1 的基础 pipeline 可保留，但 off-grid 定位仍不满足新门槛。按架构裁定，Phase 1.5 不实现 Temporal Alignment v2，也不通过放宽指标掩盖失败。

机器报告见 `benchmark/results/phase1_5_gate.json`。A–M 完整 diagnostic 写入 `output/reports/benchmark_all_diagnostic.json`。

## 性能测量口径

报告保留原始媒体秒数、原始 elapsed、cold/warm asset 数、逐资产计时和 RTF。内存同时读取主进程与已完成 FFmpeg 子进程的 peak RSS。

当前数据仅是低分辨率 synthetic regression measurement，不再换算或宣称真实剧集的每小时性能。项目最终目标仍是 M3 24GB、30 小时原片冷索引目标 ≤8h（硬上限 12h）、普通视频 RTF ≤0.7、峰值统一内存 ≤18GB；这些目标尚未验证。

本次原始测量：cold index 为 5 个资产/82.5 秒媒体、耗时 0.427 秒；warm cache 为 5/5 命中、耗时 0.132 秒；25 个 gate case 共 112.707 秒媒体，分析耗时 1.608 秒、RTF 0.01426。主进程/FFmpeg peak RSS 的观测 lower bound 为 133.3 MB，保守相加 upper bound 为 182.8 MB。

## 测试

```bash
pytest
```

当前结果：`18 passed`。测试覆盖 schema migration、project scope、episode metadata、index completeness、incomplete-index refusal、exact duplicate ambiguity、新 evaluator、audit 字段和 FFmpeg 端到端路径。

## Schema Migration

SQLite schema 已升级到 v2。旧索引迁移后会被标记为 `INCOMPLETE_INDEX`，必须重新建索引：

```bash
python -m ipvm index-originals --force
```

详见 `docs/migrations/SCHEMA-V2.md`。

## Repository tree

```text
IP-Video-Matcher/
├── benchmark/
│   ├── README.md
│   └── results/phase1_5_gate.json
├── config/default.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ARCHITECTURE-REVISION-0.1.1.md
│   ├── adr/ADR-0001...ADR-0005
│   ├── migrations/SCHEMA-V2.md
│   └── review/PHASE1-INDEPENDENT-REVIEW.md
├── src/ipvm/
├── tests/
├── pyproject.toml
└── README.md
```

## 仍未解决

- 新 Phase 1 Gate 未通过；off-grid 召回和假阳性仍需后续架构决策。
- Temporal Alignment v2 只起草 ADR-0002，没有实现。
- ambiguity/uniqueness 阈值只由 synthetic exact-duplicate regression 支持，真实 golden set 建立后必须重校。
- 没有 8 FPS 精对齐、DINOv2/SigLIP、FAISS、镜像、变速、crop、audio、ASR、Gemma、合理使用或 Excel。
- 没有真实案件 golden set；synthetic 数字不得表述为真实案件准确率。
