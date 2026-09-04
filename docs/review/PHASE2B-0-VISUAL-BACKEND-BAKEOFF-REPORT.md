# Phase 2B-0 — Visual Backend Bake-off 报告

日期：2026-09-04

角色：Experimental engineer

实验状态：**PILOT STOPPED BEFORE FULL**

停止原因：`NO_MEANINGFUL_VDF_QUERY_TO_SOURCE_EVIDENCE`

## 1. Executive Summary

Phase 2B-0 已完成当前上游发现、隔离安装、synthetic smoke、接口审查、预注册和真实素材 pilot。按照预注册的 Stage 0 / Pilot Gate，实验在 full matrix 前停止：当前 VDF 4.1 CLI 虽能执行 DINOv2 whole-file AI 比较，也实现了视觉 partial matching，但其 dense partial sampler 对 8 秒 query 仅产生 2 个约 5 秒间隔的 embedding slot，而匹配器硬性要求至少 4 个时间偏移一致的 hit。真实 pilot 因而在 default、机械 eligibility 修正以及 84%/89%/94% hit 阈值下均没有产生任何 query-to-source group、offset 或局部对应证据。

这不是“VDF 算法不如 DCT”的结论。它是一个更窄且可复核的接口/尺度结论：**当前 VDF runtime 的公开 CLI 与内部 partial 策略不适合本项目预注册的 8 秒侵权 query 任务，无法作为现有 Temporal Alignment 的候选证据源完成公平 full bake-off。** 因停止条件已满足，未运行 20 positions × 11 transforms、20 个 negatives 或 speed groups；这些项目在本报告中均标为 `NOT RUN`，不能按 0 分解释。

有效 pilot 仍显示 frozen DCT 的已知边界：DIRECT 为 4/4 source-correct 且 4/4 temporally usable；SUBTITLE_OVERLAY 为 4/4 source-correct、2/4 temporally usable；MIRROR、CENTER_CROP、VERTICAL_9_16 均为 0/4 final source match；40% viewport PIP 仅 1/4 source-correct、0/4 temporally usable，并出现 2/4 false-source match。VDF 所有已运行模式在 24 个真实 pilot query 上均为 0/24 detection，因此“0 false positives”没有判别意义。

基于本阶段证据，下一架构实验建议为 **E — 自建可审计的 DINO/embedding candidate backend，不直接依赖 VDF runtime**。理由不是抽象偏好，而是：VDF 的 DINOv2 whole-file mirror smoke 给出了正向视觉信号；其 8 秒 partial runtime 又在采样/最小 hit 与输出接口上结构性失配；frozen DCT 对几何变化确有缺口。该建议仅是下一项实验方向，不是 production 集成批准，也不代表 Phase 2B PASS。

## 2. Phase 2A frozen baseline

- 基线 commit：`93bf13bfac3b94c599ec0aa5800a43e3415e93f1`（`Close Phase 2A.2 final gate issues`）。
- `coarse_fps = 2.0`。
- `top_k = 8`。
- visual descriptor：`dct-low-frequency@1`。
- 本阶段未修改 `src/ipvm/`、production config、Temporal Alignment、DCT descriptor、FPS、top-k、DB schema 或既有 evaluator/GT。
- 接管时已有用户项 `.DS_Store` 修改，以及两个未跟踪 Phase 2A review 文档；均未修改、未暂存、未提交。

## 3. Current VDF upstream 与版本

发现时的 canonical repository 是 [0x90d/videoduplicatefinder](https://github.com/0x90d/videoduplicatefinder)；GitHub 元数据标记其为非 fork，默认分支为 `master`。选用官方 rolling release [`4.1.x`](https://github.com/0x90d/videoduplicatefinder/releases/tag/4.1.x) 的 macOS arm64 CLI asset，release/commit 均指向 [`51fbf0512002309e852e0e2e0a9eea0aba5bfba8`](https://github.com/0x90d/videoduplicatefinder/commit/51fbf0512002309e852e0e2e0a9eea0aba5bfba8)，发现时 release 更新日期为 2026-08-22。

- CLI version：`4.1.0+51fbf0512002309e852e0e2e0a9eea0aba5bfba8`。
- License：GNU AGPL v3-or-later。
- 官方 arm64 archive SHA-256：`5e06d70547892b3becaad92c352e858c4432b3959d2b56f4f59e158ae76a85de`；本地下载值与上游 asset digest 一致。该值是公开发行物 provenance，不是私有媒体 hash。
- binary：Mach-O 64-bit arm64，自包含 CLI；未使用 Rosetta，未使用 sudo。

## 4. 安装、Apple Silicon 与 runtime

安装采用官方 `CLI-osx-arm64.tar.gz`，解压到被 Git 忽略的实验 workspace；没有把 binary/source/model/runtime 放进 `src/` 或 Git。机器预检：Apple arm64、macOS 26.6.2、24 GB unified memory；Python 3.13.7；FFmpeg 9.0.1 arm64，包含 libx264/libx265；Homebrew 6.0.21 与 .NET 10.0.400 已存在，但官方自包含 binary 不需要另行构建或系统级安装。预检剩余磁盘约 52 GiB。

第一次启用 AI 时，VDF 在自身 state/output 目录下载模型与 ONNX Runtime。本阶段未启用云端媒体处理；只有公开 binary/model/runtime 下载需要网络。模型初始化后均为本机 CPU 推理。

实际 AI backend：

- `dinov2-small-int8.onnx`，来自 Xenova `dinov2-small` 的 int8 ONNX export。
- 上游源码公开的 model SHA-256：`3afdc8bc63b50558d6e5770f5b799bb82455c2311183a2de43803f343a29d917`。
- ONNX Runtime：1.23.2 arm64。
- 输入：224×224 RGB24，ImageNet normalization；输出 384 维 L2-normalized embedding，dense cache 使用量化向量。

## 5. 已验证 VDF feature matrix

下表中的结论来自 installed CLI `--help`、实际 invocation、JSON 输出和必要的同 commit 源码核查；“支持”不等于适合 8 秒 query。

| 能力 | 结论 | 验证结果/限制 |
|---|---|---|
| ordinary duplicate matching | SUPPORTED | 12 秒 synthetic re-encode 成组，similarity 99.94 |
| AI / embedding matching | SUPPORTED | `--ai-matching` 可运行；等长 mirror synthetic 成组，97.09，`AiMatched` |
| actual embedding backend | SUPPORTED | DINOv2-small int8 + ONNX Runtime 1.23.2 |
| partial-video visual matching | SUPPORTED_WITH_LIMITATIONS | `--ai-partial` 存在；12/40 秒 synthetic 可成组，但 8 秒真实 query 的 2-slot/4-hit 约束使其结构性失败 |
| edited-video matching | SUPPORTED_WITH_LIMITATIONS | 等长 mirror smoke 成功；短 query pilot 无输出 |
| crop handling | SUPPORTED_WITH_LIMITATIONS | 上游将 AI 用于 crop；实测 70% center crop synthetic 在 94% 与 92% 均未成组，真实短 query 亦无输出 |
| mirror handling | SUPPORTED_WITH_LIMITATIONS | 等长 whole-file smoke 成功；8 秒 partial pilot 为 0/4 |
| zoom handling | UNKNOWN | full matrix 未进入，未以独立真实组验证 |
| clip/source offset | SUPPORTED_WITH_LIMITATIONS | 仅 partial group item 暴露整数秒 `PartialClipOffset`；不暴露局部 frame pairs |
| similarity/confidence | SUPPORTED_WITH_LIMITATIONS | 只对已输出 group 提供 similarity；无未命中候选分数 |
| CLI/headless | SUPPORTED | `scan-and-compare` / `scan` / `compare` / `db` |
| machine-readable output | SUPPORTED | JSON、CSV、text；JSON 是 duplicate groups，不是 ranked candidates |
| reusable index/cache | SUPPORTED | `ScannedFiles.db`、`UnionEmbeddings.db`、`DenseEmbeddings.db` sidecars |
| audio-free visual comparison | SUPPORTED | 本实验未启用 audio partial；queries 删除音轨 |

Stage 0 preflight：installed=YES、native arm64=YES、reliable execution=YES、CLI=YES、JSON=YES、AI=YES、backend identified=YES、partial matching=LIMITED、offset exposed=LIMITED、similarity exposed=YES、model initialized=YES、cache usable=YES。

## 6. Synthetic smoke 结果

1. Classic：同长原始/re-encode pair 检出，99.94。
2. AI union：同长原始/mirror pair 检出，97.09，带 `AiMatched`。
3. AI partial：40 秒 source 与其中 12 秒无音轨 clip 检出，95.78，带 `PartialClip,AiMatched`；但 source 为周期性 synthetic 内容，GT offset 为 11 秒，VDF 报 20 秒，误差 9 秒。该结果证明 code path 可工作，不证明 offset 可交付。
4. 70% center crop whole-file：AI 94% 和预登记较松 92% 均未检出。
5. 真实 30 秒 DIRECT 补充诊断：eligibility 已放开、hit=89%，仍无 group。此项不是 primary metric，只用于确认失败不只来自 8 秒 ratio gate。

## 7. 预注册 protocol 与 amendment

完整结果前已在 ignored workspace 保存 `phase2b0-v0.1`：

- seed：`20260904`。
- 计划 full：2 sources × 10 independent positions；pilot：2/source，共 4 independent positions。
- clean source span：8.0 秒。
- 自动抽样：每集在避开首尾约 30 秒的有效区间等分 strata，每层固定 seeded uniform start；相邻样本至少 20 秒；不按画面难度剔除。
- DCT 固定 2 FPS / top-k 8 / DCT@1；final offset primary tolerance ±1 秒。预注册 JSON 将 DCT `source_position_tolerance_seconds` 写为 1.0 秒，但 harness 实际按一个 2 FPS grid step，即 0.5 秒，计算 `source-position usable frames`。这是最终审计发现的 protocol/implementation 偏差；表中明确采用实际 0.5 秒结果。该偏差对 DCT 更严格，且不改变 VDF 0 group 所触发的停止判定，但正式重跑前必须统一字段。
- VDF default：Classic 96%；AI union 94%；AI partial hit 89%；groups 而非排名。
- VDF 成功：group 必须含 expected source 且不含另一 source；temporally usable 还必须有 `PartialClip` 与 offset；primary offset ±1 秒，另设与 15 秒 source dense grid 对应的 ±15 秒 diagnostic，不能替代 primary。
- 冷查询：从 source-only template 恢复，query 未缓存；warm：紧随其后的同 query 重跑。
- audio evidence 关闭。

初次 pilot 后发现上游 `partial_clip_min_ratio=0.10` 会在 AI frame matching 前排除 8 秒/~40 分钟组合（ratio 约 0.0034）。这是 protocol applicability defect，而不是 label-driven threshold tuning。于 full 前登记 v0.1.1 amendment：新增 `AI_PARTIAL_8S_ELIGIBLE`，仅将 `--partial-clip-min-ratio` 设为 0.0，hit threshold 保持 89%，metric 和 tolerance 不变；原 default 0/24 结果保留。随后按预注册敏感度运行 hit 84%/94%，也没有输出。

## 8. Corpus construction 与 transformations

真实素材为 2 个 ignored local source，约 38–40 分钟；报告仅使用 `SOURCE_A`、`SOURCE_B`。原片未重命名、未修改，query 全部在 ignored workspace 自动生成。pilot 为 4 independent positions × 6 transforms = 24 positive queries；每个 transform 的 `n=4`，不能把 24 当作 24 个独立内容样本。

Pilot exact parameters：

| Transform | 参数 |
|---|---|
| DIRECT | 8 秒，H.264/libx264 CRF18，原几何，移除音轨 |
| MIRROR | `hflip` 后 1280×720，CRF23 |
| CENTER_CROP | 保留中心 70% 宽高，再缩放 1280×720，CRF23 |
| VERTICAL_9_16 | 中心 9:16 crop，再缩放 720×1280，CRF23 |
| SUBTITLE_OVERLAY | 1280×720；底部固定半透明黑带和确定性白色 glyph-like rectangles，CRF23 |
| PIP_REDUCED_VIEWPORT | 原画面宽度固定为 output 的 40%（512×288），置于 1280×720 固色背景中央，CRF23 |

本机 FFmpeg build 没有 `drawtext/subtitles` filter，故 subtitle proxy 不含真实文字 glyph；这是已知 realism 限制。预注册的 H264_REENCODE、ZOOM、LETTERBOX、SPEED_0_8X、SPEED_1_25X 只属于计划 full matrix，因 Gate 停止未生成/运行。speed GT 在 harness 单测中固定为 `source_time = speed_ratio × accused_time + source_start`，但没有真实 speed measurement。

## 9. Frozen DCT pilot 结果

### 9.1 Retrieval-level

| Transform | n | correct-source top-1 frames | correct-source top-8 frames | source-position usable frames |
|---|---:|---:|---:|---:|
| DIRECT | 4 | 62/64 (96.9%) | 64/64 (100%) | 63/64 (98.4%) |
| MIRROR | 4 | 37/64 (57.8%) | 61/64 (95.3%) | 7/64 (10.9%) |
| CENTER_CROP | 4 | 41/64 (64.1%) | 59/64 (92.2%) | 20/64 (31.3%) |
| VERTICAL_9_16 | 4 | 31/64 (48.4%) | 56/64 (87.5%) | 1/64 (1.6%) |
| SUBTITLE_OVERLAY | 4 | 63/64 (98.4%) | 64/64 (100%) | 63/64 (98.4%) |
| PIP_REDUCED_VIEWPORT | 4 | 21/64 (32.8%) | 57/64 (89.1%) | 0/64 (0%) |
| **合计** | **24** | **255/384 (66.4%)** | **361/384 (94.0%)** | **154/384 (40.1%)** |

“correct-source top-k”只代表 episode 在 candidate set 中；MIRROR/CROP/PIP 的 source-position usable 明显更低，不能将 94.0% 的 episode-level top-8 当成可供 Temporal Alignment 使用的 94.0%。

### 9.2 Product-level

| Transform | n | source-correct | temporally usable | offset-correct | false-source | final status |
|---|---:|---:|---:|---:|---:|---|
| DIRECT | 4 | 4/4 | 4/4 | 4/4 | 0/4 | MATCH_FOUND 1；AMBIGUOUS 3 |
| MIRROR | 4 | 0/4 | 0/4 | 0/4 | 0/4 | MATCH_NOT_FOUND 4 |
| CENTER_CROP | 4 | 0/4 | 0/4 | 0/4 | 0/4 | MATCH_NOT_FOUND 4 |
| VERTICAL_9_16 | 4 | 0/4 | 0/4 | 0/4 | 0/4 | MATCH_NOT_FOUND 4 |
| SUBTITLE_OVERLAY | 4 | 4/4 | 2/4 | 2/4 | 0/4 | MATCH_FOUND 3；AMBIGUOUS 1 |
| PIP_REDUCED_VIEWPORT | 4 | 1/4 | 0/4 | 0/4 | 2/4 | MATCH_FOUND 1；AMBIGUOUS 2；MATCH_NOT_FOUND 1 |
| **合计** | **24** | **9/24 (37.5%)** | **6/24 (25.0%)** | **6/24 (25.0%)** | **2/24 (8.3%)** | MATCH_FOUND 5；AMBIGUOUS 6；MATCH_NOT_FOUND 13 |

DIRECT source-start absolute error 为 0.165–0.295 秒，中位约 0.231 秒。SUBTITLE_OVERLAY 的 4 个正确 episode 候选 source-start error 为 0.295–1.191 秒，其中 2/4 达到 ±1 秒；其余不能用“episode correct”掩盖 offset 不合格。PIP 唯一 same-episode 输出偏离真实位置约 283 秒，不是可用 temporal match。

## 10. VDF pilot 结果

每个模式均运行相同 24 queries；每个 case 有 cold 与 immediate warm。以下计数按 cold 结果，warm 结果同样为 0 group。

| Mode | 参数 | detection | source-correct | temporally usable | offset-correct | false-source |
|---|---|---:|---:|---:|---:|---:|
| CLASSIC_DEFAULT | percent 96 | 0/24 | 0/24 | 0/24 | 0/24 | 0/24 |
| AI_UNION_DEFAULT | AI percent 94 | 0/24 | 0/24 | 0/24 | 0/24 | 0/24 |
| AI_PARTIAL_DEFAULT | hit 89；min ratio 0.10 | 0/24 | 0/24 | 0/24 | 0/24 | 0/24 |
| AI_PARTIAL_8S_ELIGIBLE | hit 89；min ratio 0.0 | 0/24 | 0/24 | 0/24 | 0/24 | 0/24 |
| AI_PARTIAL_LOOSER | hit 84；min ratio 0.0 | 0/24 | 0/24 | 0/24 | 0/24 | 0/24 |
| AI_PARTIAL_STRICTER | hit 94；min ratio 0.0 | 0/24 | 0/24 | 0/24 | 0/24 | 0/24 |

由于没有任何 group，VDF similarity、offset error、ranking、agreeing keyframes 和 matched interval 均为 **unavailable**，不是 0。当前 CLI 本来也不暴露 ranked candidates、frame timestamps、matched frame pairs 或 interval；只在成功 group 中给 similarity、flags 与整数秒 offset。

### 10.1 结构性原因

当前源码 `VDF.Core/ScanEngine_AiPartial.cs`：

- `AiPartialMinConsistentHits = 4`。
- `AiPartialOffsetToleranceSeconds = 30`。
- dense interval 为 `max(clamp(duration/60, 5, 15), duration/400)`。
- 约 40 分钟 source 的 interval 为 15 秒；8 秒 query 的 interval 为 5 秒。
- `TryMatchDenseFrames` 少于 4 hits 立即失败，offset 取 consistent hits 的 median 后四舍五入为整数。

对 pilot 的 `DenseEmbeddings.db` 作只读格式核查，代表性 8 秒 query 记录为 interval 5.0 秒、2 slots、2 usable slots；两 source 分别约 153/160 slots。故 8 秒 query 在进入 cosine threshold 判断前就不可能满足 4-hit quorum。84%/89%/94% 都是 0 并非阈值曲线，而是这个结构约束的必然结果。

## 11. Transformation matrix

VDF 三类默认接口（classic / AI union / AI partial）及 eligibility-corrected partial 在每个 transform 都是 0/4 source-correct、0/4 temporal；敏感度同样为 0，因此表内合并显示，避免把重复的零伪装成独立证据。

| Transform | n | DCT source / temporal | VDF classic source / temporal | VDF AI union source / temporal | VDF AI partial source / temporal | false matches DCT / VDF | 结论 |
|---|---:|---:|---:|---:|---:|---:|---|
| DIRECT | 4 | 4/4 / 4/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | VDF 短 query 接口失败，不能归因 DINO 表示 |
| MIRROR | 4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 两者均无 final evidence；VDF whole-file mirror smoke 单独成功 |
| CENTER_CROP | 4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 无优胜者；VDF whole-file crop smoke 也失败 |
| VERTICAL_9_16 | 4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 无优胜者 |
| SUBTITLE_OVERLAY | 4 | 4/4 / 2/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 此 pilot 中 DCT 有用，但 n=4 |
| PIP_REDUCED_VIEWPORT | 4 | 1/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 0/4 / 0/4 | 2/4 / 0/4 | 40% viewport 对两者均不可靠；VDF 0 FP 无判别力 |

## 12. Position-level independence

| Position | transforms | DCT source-correct | DCT temporally usable | VDF source-correct / temporal（每模式） |
|---|---:|---:|---:|---:|
| SOURCE_A-POSITION_001 | 6 | 3/6 | 1/6 | 0/6 / 0/6 |
| SOURCE_A-POSITION_002 | 6 | 2/6 | 2/6 | 0/6 / 0/6 |
| SOURCE_B-POSITION_001 | 6 | 2/6 | 1/6 | 0/6 / 0/6 |
| SOURCE_B-POSITION_002 | 6 | 2/6 | 2/6 | 0/6 / 0/6 |

- all six transforms temporally succeed：0/4 positions。
- DCT source-correct / VDF wrong：9/24 queries；其中只有 6/24 是 DCT temporally usable。
- VDF correct / DCT wrong：0/24。
- both source-correct：0/24。
- both not source-correct：15/24。

这些是 pilot 描述值，不是 20-position population estimate，也未计算 CI。

## 13. Deterministic disagreement analysis

按 case ID 排序取最前的类别样本，不看画面挑选：

- DCT correct / VDF wrong：`SOURCE_A-POSITION_001-DIRECT`、`...-PIP_REDUCED_VIEWPORT`、`...-SUBTITLE_OVERLAY`。DIRECT 的 VDF failure 可由 2-slot/4-hit 解释；SUBTITLE 同样受此门槛约束；PIP 的 DCT 只命中同 episode，却偏离 GT 数百秒，故应归“repeated/self-similar evidence + small viewport”，不能视作 DCT 胜利。
- both wrong：同 position 的 CENTER_CROP、MIRROR、VERTICAL_9_16。DCT 有不少 correct-episode top-8 frame，但真实 source-position evidence 不够；VDF 没进入可输出 partial group 的条件。
- VDF correct / DCT wrong、both correct：不存在可检查样本。

没有把任何真实 frame、exact source timestamp 或 identifiable content 写入报告。

## 14. False-positive controls

计划的 ≥20 synthetic negative queries **NOT RUN**。原因是 pilot 已证明 VDF 8 秒正样本 detection=0；此时运行 negatives 只能轻易得到缺乏意义的“低 FP”，不能形成阈值 tradeoff。真实 pilot 自带 cross-source opportunity：VDF 0/24 false-source，但同时 0/24 detection；DCT 在 PIP 为 2/4 false-source（全 pilot 2/24）。

因此当前 observed false-positive tradeoff 是 **不可识别**，不能将 VDF 的 0 false match 宣称为安全优势。

## 15. Source-offset accuracy

- DCT：见第 9 节；DIRECT 4/4 在 ±1 秒，SUBTITLE 2/4，其他几何组 0/4。
- VDF：真实 pilot 没有输出 offset，故 offset distribution 为 unavailable。
- Synthetic partial：输出粒度为整数秒；在周期性 12/40 秒 smoke 中误差 9 秒，说明 ±15 秒 coarse diagnostic 不能替代 ±1 秒 primary。

## 16. Speed analysis distinction

`SPEED_0_8X` 与 `SPEED_1_25X` **NOT RUN**。Stage Gate 在 full 前停止，因此不能回答视觉 source recognition 是否保留，也不能把任何 failure 分解为 embedding failure 或 VDF constant-offset logic failure。Harness 已独立测试 query duration 和 GT slope 公式，但那只是生成器正确性，不是 backend 结果。

## 17. Index、cold/warm runtime 与资源

Source indexing 是两个约 38–40 分钟 source 的 source-only template：

| Mode | source indexing | source-only cache |
|---|---:|---:|
| Classic | 0.555 s | `ScannedFiles.db` 3.2 KB |
| AI union | 0.621 s | `ScannedFiles.db` 3.2 KB + `UnionEmbeddings.db` 1.0 KB |
| AI partial | 11.577 s | `ScannedFiles.db` 3.2 KB + `DenseEmbeddings.db` 119.6 KB |

Per-query wall time（24 queries 中位数）：

| Mode | cold median | warm median |
|---|---:|---:|
| Frozen DCT `analyze_file`（已有 source index） | 0.135 s | 不适用 |
| VDF Classic | 0.279 s | 0.203 s |
| VDF AI union | 0.371 s | 0.275 s |
| VDF AI partial default | 0.421 s | 0.276 s |
| VDF AI partial eligible 89 | 0.423 s | 0.276 s |
| VDF AI partial eligible 84 | 0.520 s | 0.360 s |
| VDF AI partial eligible 94 | 0.522 s | 0.370 s |

DCT 时间只包 production `analyze_file`；VDF cold/warm 是完整 CLI process wall time，二者边界已标明，不能解读为微基准同构比较。首次公开模型/runtime 网络下载未混入 query timing。

Disk：解压后的 VDF output/runtime/model 约 146 MiB，其中 `ai/` 约 91 MiB；另保留约 12 MiB 官方 archive 于 ignored workspace。六份 mode-specific source template 共约 508 KiB，包含重复 template。

Peak RSS 未得到可信值：sandbox 阻止进程树 `ps` 采样，macOS `/usr/bin/time -l` 在该环境也未返回 RSS 字段。报告不以源码中的 bounded batch 设计冒充实测内存；唯一可核查事实是当前实现以最多 16-frame batch streaming，而不是整片 frame buffer。

## 18. Architectural usefulness

### Product-level

VDF installed build 对 whole-file re-encode/mirror 有识别能力，但对本项目 8 秒 query pilot 没有任何公开结果，无法提供 source episode、similarity 或 offset。Frozen DCT 在 direct/subtitle 有用，对 mirror/crop/vertical/PIP 不可靠。

### Temporal Alignment input-level

当前 VDF CLI：

- 不输出 ranked source-frame candidates；
- 不输出 matching keyframes、frame timestamps 或 local correspondences；
- 不输出 matched interval；
- 成功 AI partial 时仅输出 group、similarity、flags 和整数秒 offset；
- 真实 8 秒 query 又无法满足 4-hit quorum。

因此对当前 task 判定为 **UNSUITABLE**。即使未来得到 partial offset，它也最多是 coarse source-window hint，不能直接替代现有 Temporal Alignment 所需的 frame-level candidates。

源码内部确有 `DenseRecord(interval, Frames)`、per-frame embedding timeline 与 `TryMatchDenseFrames` 的 source/clip loop；从纯工程角度提取是可行的，但不是现成 public API。实现需要 fork/adapter 或独立重写、改变 5/15 秒采样与 4-hit 设计、定义稳定 candidate schema，并处理 AGPL 合规。Phase 2B-0 未复制、port 或集成这部分代码。

## 19. Architecture Questions

### Q1 — MIRROR 是否 materially outperform DCT？

**不能建立。** 真实 8 秒 pilot 两者 final source match 都是 0/4；VDF 等长 synthetic mirror 成功只证明 embedding whole-file path 有信号，不能外推为短 clip 优势。

### Q2 — CENTER_CROP 是否 materially outperform DCT？

**不能建立。** 真实 pilot 两者均 0/4；VDF 70% whole-file synthetic crop 在 94% 与 92% 也未检出。

### Q3 — VERTICAL_9_16 是否 materially outperform DCT？

**不能建立。** 两者均 0/4，VDF failure 被短 query gate 混杂。

### Q4 — SUBTITLE/OVERLAY 是否 materially outperform DCT？

**否，在本 pilot 中没有证据。** DCT 为 4/4 source-correct、2/4 temporal；VDF 为 0/4。但 subtitle 是 glyph-like proxy，且 n=4，不能做广泛稳定性结论。

### Q5 — 能否识别 40% PIP？可靠性在哪个 viewport 开始下降？

VDF 在固定 40% viewport 为 0/4；DCT 为 1/4 source-correct、0/4 temporal，并有 2/4 wrong-source。因为 initial fixed test 已失败且 Stage Gate 停止，没有做 viewport sweep，故 degradation threshold **UNKNOWN**。不能把“40% 失败”改写成“低于 40% 才失败”。

### Q6 — SPEED_0_8X / 1.25X 的 visual recognition 与 offset logic？

**UNKNOWN / NOT RUN。** 不从 constant-offset 源码推断 embedding 实测强弱。

### Q7 — VDF 是否暴露可供现有 Temporal Alignment 使用的证据？

对当前 8 秒任务：**否。** CLI 没有 frame correspondences/ranked candidates；真实 pilot 也没有 coarse offset 输出。

### Q8 — 若证据只在内部，提取/移植是否可行？

**技术上可行但不是小 adapter。** 内部 dense timeline 与 matching loop 存在；需要改变短 clip sampling/quorum、暴露 candidates、建立 cache identity/schema，并进行 AGPL 法律评估。当前不应直接 port。

### Q9 — observed false-positive tradeoff？

**不可识别。** VDF detection=0 导致 FP=0 没有信息量；negative suite 按停止规则未运行。DCT 的主要安全信号是 40% PIP 2/4 false-source。

### Q10 — M3 24GB runtime/index/memory tradeoff？

AI partial source indexing 约 11.6 秒，source cache 约 123 KB；query cold/warm 中位约 0.42/0.28 秒；安装与 AI runtime/model 约 146 MiB（另有 archive）。DCT 已索引 query matcher 中位 0.135 秒。可信 peak RSS 未取得，不能报告估计数字。

### Q11 — 下一架构建议

选择 **E — BUILD OUR OWN DINO/EMBEDDING BACKEND INSPIRED BY FINDINGS, WITHOUT DIRECT VDF RUNTIME DEPENDENCY**。

证据边界：VDF DINOv2 whole-file mirror smoke 给出正向 signal；VDF runtime 的短 query sampling/quorum 和 group-only output 又不满足 IPVM；DCT 在 geometry/PIP 上存在实测缺口。下一步若获 Architecture Owner 批准，应先做独立、measurement-only frame embedding retriever：保持 source/query 2 FPS 与 top-k 8 的可比配置，直接输出 `(accused_time, source_time, episode, score)` candidates 给关闭的 Temporal Alignment 作离线评估。触发/晋级条件应是：在预注册的 20-position geometric transforms 与 negatives 上，较 frozen DCT 提升 source-position usable evidence，且不引入不可接受的 false-source/ambiguity；未达到 Gate 前不接 production。此建议不授权当前进入 backend 实现。

## 20. Limitations

- 仅 2 sources、4 independent pilot positions；不是跨剧集稳定性估计。
- full 220 positives、20 negatives、speed、zoom、letterbox 均未运行；缺失项不是失败数。
- VDF 的 8 秒失败是 runtime design/interface 与 task length 的交互，不能单独评价 DINOv2 representation。
- Synthetic partial source 有周期性，offset error 不代表一般分布。
- Subtitle 是规则矩形 proxy，不是语言文字或平台真实 burn-in。
- VDF group similarity 不是 DCT retrieval score；没有强行统一内部指标。
- 没有 peak-memory 实测。
- 上游 rolling release 可能变化；本结论严格绑定 exact commit。

## 21. Tests、artifact 与 reproducibility

- 新增 focused harness tests：7 passed。
- final full repository pytest：**126 passed in 6.90s**。
- 匿名 aggregate：`benchmark/visual/results/phase2b0-visual-backend-pilot-v0.1.json`。
- aggregate 状态显式为 `PILOT_STOPPED_BEFORE_FULL`，包含 4-position/24-query DCT 与六个 VDF mode 的 numerator/denominator、runtime、position-level counts，不含 exact GT。
- Harness 支持 deterministic corpus、negative generator、frozen DCT、VDF source template、cold/warm run、group parser、重复 VDF report 合并和 sanitized aggregate；重复 case/mode 或不一致 offset tolerance 会 fail loudly。

## 22. Privacy / scope audit

- `real_test/` 与 `workspace/` 均由 `.gitignore` 排除；原片、queries、raw JSON、exact timestamps、VDF binary/source/model/cache/database 都未纳入 Git。
- committed aggregate/report 不含 home-directory absolute path、原媒体文件名、私有媒体 hash、exact source timestamps、frames 或 thumbnails；上一个条目中的目录名只用于说明 ignore policy。
- 只使用 `SOURCE_A/SOURCE_B` 和 anonymized position ID；没有可识别逐帧信息。
- 未修改 production matcher、Temporal Alignment、DCT、FPS、top-k、schema 或 Phase 2A artifacts。
- 没有进入 DINOv2 production backend、FAISS、audio、ASR、Gemma、Evidence Analysis、screen/reaction normalization、Excel 或 Phase 3。

## 23. 最终状态

本阶段形成了一个有效但受限的 sanitized pilot characterization。按照预注册规则，**在 full bake-off 前 STOP**，等待 Architecture Owner 决定是否批准新的、直接输出 frame candidates 的 embedding retrieval experiment。本文不宣布 Phase 2B PASS，也不重新打开已关闭的 Phase 2A。
