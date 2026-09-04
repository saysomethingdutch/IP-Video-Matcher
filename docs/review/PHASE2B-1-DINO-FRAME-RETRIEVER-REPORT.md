# Phase 2B-1 — DINO Frame Embedding Retriever Characterization

日期：2026-09-04

角色：Experimental engineer

实验状态：**CHARACTERIZATION COMPLETE**

表示层事实分类：**DINO RETRIEVAL = STRONG CANDIDATE**

架构路线建议：**B — DINO RETRIEVAL PROMISING**。该建议只支持由 Architecture Owner 考虑是否进入候选 adapter / score calibration 实验；不代表 Phase 2B PASS，不授权生产集成。

## 1. Executive Summary

本阶段完成了独立于 VDF runtime 的 DINOv2-small frame retriever。DINO 与冻结的 DCT 对照使用完全相同的真实 query、2 FPS source/query nominal grid、top-k 8、source/time ground truth 和 exact NumPy cosine retrieval；没有调用 Temporal Alignment，也没有修改 production matcher。

全量样本为 2 个匿名 source、20 个独立 content positions、11 种 transformation，共 220 个正样本；另有 20 个确定性无关负样本。20 个位置才是主要独立单位，220 个 position×transformation cells 与 3,540 个 query frames 都是配对/相关观测，不能当作 220 或 3,540 个独立样本。

DINO_RAW 保住了 DIRECT/H264 的预注册 top-8/position 指标：主口径 source-aware recall@8（±1 秒）均为 99.7%，DCT 均为 98.8%；20/20 positions 都达到“至少 50% frame 可用”。H264 的 strict SA@1 从 DCT 89.1% 降到 DINO 85.0%，因此不能概括成所有 direct/re-encode 指标都无回落。三项预注册几何对比均有大幅改善：

- MIRROR：7.2% → 99.4%，可用位置 0/20 → 20/20；
- CENTER_CROP：26.9% → 89.4%，6/20 → 20/20；
- VERTICAL_9_16：8.1% → 77.8%，0/20 → 17/20。

实际栅格文字字幕为 95.9%、20/20；0.8× 与 1.25× 分别为 100.0% 与 99.6%、均 20/20。40% PIP 从 DCT 的 0.3%、0/20 提升到 60.6%、12/20，但仍不足以称为可靠；其 true-minus-best-wrong median margin 为 -0.026，且 score 与 negatives 有重叠，可能需要单独的 region/ROI 路线。

DINO_FLIP_TTA 把 MIRROR 的 source-aware recall@1 从 82.2% 提高到 91.9%，但 top-8 仅从 99.4% 到 99.7%；全体非 mirror cases 的 top-8 没有净收益，个别组有 0.6–1.6 个百分点回落。因此它是可选的镜像定向诊断，不应默认取代 DINO_RAW。

## 2. Frozen baseline

- 输入基线 commit：`0b5dc6bea30b828c4dd7eb35cddaff82937c1b78`。
- DCT：`dct-low-frequency@1`，实现与 production descriptor 相同。
- source FPS = 2；query FPS = 2；top-k = 8。
- DCT 与 DINO source index 均为 9,379 frames，source alias/time 数组逐元素相同；所有 source timestamps 都在 0.5 秒 grid 上。
- 相似度为各 backend 自己的 raw cosine；没有跨 backend 比较绝对阈值，也没有拟合 detection threshold。
- 本报告所在最终 commit 由 Git 历史与完成输出标识；Git commit 无法在自身内容中自包含其最终 hash。

## 3. Model / provenance

- 模型：`dinov2-small-int8.onnx`，与 Phase 2B-0 验证的 VDF 4.1.0 bundle 所用模型相同；公开来源与公开模型校验值见 Phase 2B-0 报告。
- 运行时：Python ONNX Runtime 1.23.2，`CPUExecutionProvider`；不依赖 VDF executable、VDF DB 或 VDF sampling/runtime。
- 实际 ONNX metadata：输入 `pixel_values[batch, channels, height, width]` float；输出 `last_hidden_state[batch,257,384]` float。
- preprocessing：FFmpeg RGB24、224×224 bicubic、`uint8/255`、ImageNet mean `(0.485,0.456,0.406)` / std `(0.229,0.224,0.225)`、NCHW。
- embedding：取 `last_hidden_state[:,0,:]` CLS token，384 维 float32，逐向量 L2 normalization。
- 独立 smoke：重复输入 bitwise 一致；实测 norm 范围 `0.99999988–1.00000012`。

模型、运行时 binary、source paths 和 source hashes 均未提交。

## 4. Implementation

实验 harness 位于 `experiments/phase2b1/benchmark.py`，职责限于：

1. 确定性抽样与 query/negative 生成；
2. 直接 ONNX frame embedding；
3. 实验 DCT/DINO source index；
4. exact cosine top-k；
5. source-aware evaluator 与匿名 aggregate。

每条 raw candidate 明确包含：

```text
(accused_time, source_time, source_id, score, rank)
```

全量 raw 记录共 92,640 个 top-8 candidate tuples。独立 invariant probe 验证：每 frame 恰有 8 个不同 source/time candidates；rank 连续为 1–8；score 非增；DCT/DINO accused-time grid 完全相同。原始逐帧记录只在 ignored workspace。

`DINO_FLIP_TTA` 只翻转 query：对原 query 与水平翻转 query 分别 embedding，对每个相同 source/time 取两者 cosine 最大值，再取 unique top-8；source index 从不翻转。

## 5. Corpus

- source aliases：`SOURCE_A`、`SOURCE_B`；`episode_n = 2`。
- 每 source 10 positions，共 20；seed = `20260904`。
- 抽样沿各 source 的有效时长等分 strata，每层随机一个 start；位置覆盖 normalized source range 约 0.026–0.958。
- 独立对照 Phase 2B-0 selector 后，20 个 position aliases 与 starts 逐项一致。
- 每个 source interval 为 8 秒；每个 position 生成全部 11 transforms。
- 每 transform 20 queries；总正样本 220。
- DIRECT 等普通组每 backend 为 320 query frames；0.8× 为 400；1.25× 为 260；合计每 backend 3,540。

本阶段的 2-source 结果是工程 characterization，不是跨剧集稳定性估计。

## 6. Transformation definitions

| Transform | 固定定义 |
|---|---|
| DIRECT | H.264/libx264 CRF18，原几何，移除音频 |
| H264_REENCODE | 854×480，CRF32 |
| MIRROR | hflip，1280×720，CRF23 |
| CENTER_CROP | 中心保留 70% 宽高，再缩放 1280×720，CRF23 |
| VERTICAL_9_16 | 中心 9:16 crop，再缩放 720×1280，CRF23 |
| ZOOM | 中心保留 80% 宽高，再缩放 1280×720，CRF23 |
| LETTERBOX | 960×540 内容置入 1280×720 黑底，CRF23 |
| SUBTITLE_OVERLAY | 1280×720，固定真实栅格文字 + 半透明底带，CRF23 |
| PIP_REDUCED_VIEWPORT | 内容固定为输出宽度 40%（512×288），1280×720 固色背景，CRF23 |
| SPEED_0_8X | `setpts=PTS/0.8`，约 10 秒 query，CRF23 |
| SPEED_1_25X | `setpts=PTS/1.25`，约 6.4 秒 query，CRF23 |

没有参数 sweep。所有 queries 均来自真实 source 的 off-grid interval，并重新编码到 ignored workspace。

## 7. Real subtitle-overlay correction

Phase 2B-0 的字幕仅是 glyph-like rectangles。本阶段改为 Pillow 内置 bitmap font 实际 rasterize 两行固定文本，叠加在固定半透明黑带上，再由 FFmpeg 编码；不提交外部 font file。生成后的 query frame 已做解码层视觉检查，确认是可读文字，不是矩形代理。

## 8. Positive sample counts

| 单位 | 数量 | 独立性说明 |
|---|---:|---|
| source | 2 | 匿名真实剧集 |
| content position | 20 | 主要独立单位 |
| transform per position | 11 | 同位置配对观测 |
| positive query | 220 | 不是 220 个独立内容位置 |
| query frame per backend | 3,540 | interval 内高度相关 |
| evaluated backend modes | 3 | DCT、DINO_RAW、DINO_FLIP_TTA |

## 9. Negative sample counts

20 个确定性无关 synthetic clips，每条 8 秒、2 FPS，共 320 frames/backend。由于 nearest-neighbor 检索总会返回结果，本阶段只报告 score、source concentration 和粗 temporal coherence，不定义 FP、不拟合阈值。

## 10. DCT full retrieval metrics

`Ep` 为只看 source identity；`SA` 同时要求正确 source 与正确 source time。`usable ≥50/75/100` 是 20 个位置中达到相应 frame-level SA@8 ±1 覆盖率的数量。

| Transform | Ep@1 | Ep@8 | SA@1 ±1 | SA@8 ±1 | SA@1 ±.5 | SA@8 ±.5 | usable ≥50/75/100 |
|---|---:|---:|---:|---:|---:|---:|---:|
| DIRECT | 91.6% | 100.0% | 90.0% | 98.8% | 89.7% | 98.8% | 20/20/18 |
| H264_REENCODE | 91.2% | 100.0% | 89.7% | 98.8% | 89.1% | 98.4% | 20/20/18 |
| MIRROR | 57.2% | 90.6% | 4.1% | 7.2% | 2.8% | 7.2% | 0/0/0 |
| CENTER_CROP | 65.9% | 92.5% | 15.6% | 26.9% | 10.6% | 25.6% | 6/1/0 |
| VERTICAL_9_16 | 52.8% | 86.6% | 3.1% | 8.1% | 2.2% | 6.6% | 0/0/0 |
| ZOOM | 90.0% | 99.1% | 43.1% | 71.9% | 33.1% | 65.0% | 18/10/4 |
| LETTERBOX | 55.3% | 90.3% | 0.0% | 0.3% | 0.0% | 0.0% | 0/0/0 |
| SUBTITLE_OVERLAY | 90.6% | 98.8% | 67.5% | 81.9% | 60.9% | 81.2% | 18/15/10 |
| PIP_REDUCED_VIEWPORT | 52.2% | 88.4% | 0.3% | 0.3% | 0.3% | 0.3% | 0/0/0 |
| SPEED_0_8X | 95.0% | 100.0% | 92.5% | 98.0% | 91.2% | 98.0% | 20/20/16 |
| SPEED_1_25X | 93.8% | 100.0% | 92.7% | 98.5% | 91.9% | 98.5% | 20/20/18 |

## 11. DINO_RAW full retrieval metrics

| Transform | Ep@1 | Ep@8 | SA@1 ±1 | SA@8 ±1 | SA@1 ±.5 | SA@8 ±.5 | usable ≥50/75/100 |
|---|---:|---:|---:|---:|---:|---:|---:|
| DIRECT | 92.5% | 100.0% | 91.6% | 99.7% | 89.7% | 99.4% | 20/20/19 |
| H264_REENCODE | 91.9% | 100.0% | 89.1% | 99.7% | 85.0% | 98.4% | 20/20/19 |
| MIRROR | 91.9% | 100.0% | 82.2% | 99.4% | 73.4% | 98.8% | 20/20/18 |
| CENTER_CROP | 91.9% | 100.0% | 53.4% | 89.4% | 39.7% | 83.1% | 20/17/7 |
| VERTICAL_9_16 | 89.7% | 100.0% | 45.6% | 77.8% | 32.2% | 75.0% | 17/14/4 |
| ZOOM | 93.4% | 100.0% | 62.8% | 94.4% | 47.2% | 92.2% | 20/20/10 |
| LETTERBOX | 91.9% | 100.0% | 65.9% | 95.9% | 50.0% | 93.1% | 20/19/14 |
| SUBTITLE_OVERLAY | 93.8% | 100.0% | 75.0% | 95.9% | 65.0% | 92.2% | 20/19/14 |
| PIP_REDUCED_VIEWPORT | 79.7% | 99.7% | 26.2% | 60.6% | 17.2% | 52.5% | 12/9/1 |
| SPEED_0_8X | 94.8% | 100.0% | 94.0% | 100.0% | 90.5% | 99.8% | 20/20/20 |
| SPEED_1_25X | 94.2% | 100.0% | 91.9% | 99.6% | 89.6% | 99.6% | 20/20/19 |

## 12. DINO_FLIP_TTA metrics

| Transform | Ep@1 | Ep@8 | SA@1 ±1 | SA@8 ±1 | SA@1 ±.5 | SA@8 ±.5 | usable ≥50/75/100 |
|---|---:|---:|---:|---:|---:|---:|---:|
| DIRECT | 92.8% | 100.0% | 91.9% | 99.7% | 89.7% | 99.4% | 20/20/19 |
| H264_REENCODE | 91.9% | 100.0% | 89.1% | 99.7% | 85.0% | 98.4% | 20/20/19 |
| MIRROR | 93.4% | 100.0% | 91.9% | 99.7% | 89.4% | 99.1% | 20/20/19 |
| CENTER_CROP | 90.9% | 100.0% | 53.4% | 89.4% | 39.1% | 82.8% | 20/17/7 |
| VERTICAL_9_16 | 88.1% | 100.0% | 43.8% | 76.2% | 30.6% | 72.2% | 17/13/3 |
| ZOOM | 93.4% | 100.0% | 62.8% | 94.7% | 46.9% | 92.2% | 20/20/11 |
| LETTERBOX | 91.2% | 100.0% | 65.0% | 95.9% | 50.0% | 93.4% | 20/20/13 |
| SUBTITLE_OVERLAY | 93.8% | 100.0% | 75.0% | 95.3% | 64.4% | 92.2% | 20/19/14 |
| PIP_REDUCED_VIEWPORT | 77.2% | 99.4% | 24.7% | 59.7% | 16.6% | 53.1% | 13/7/2 |
| SPEED_0_8X | 94.5% | 100.0% | 93.8% | 100.0% | 90.5% | 99.8% | 20/20/20 |
| SPEED_1_25X | 94.2% | 100.0% | 91.9% | 99.6% | 89.6% | 99.6% | 20/20/19 |

## 13. Per-transformation comparison

主口径只看 SA@8 ±1 与 usable≥50。括号内为可用位置数。

| Transform | DCT | DINO_RAW | DINO_FLIP_TTA |
|---|---:|---:|---:|
| DIRECT | 98.8% (20/20) | 99.7% (20/20) | 99.7% (20/20) |
| H264_REENCODE | 98.8% (20/20) | 99.7% (20/20) | 99.7% (20/20) |
| MIRROR | 7.2% (0/20) | 99.4% (20/20) | 99.7% (20/20) |
| CENTER_CROP | 26.9% (6/20) | 89.4% (20/20) | 89.4% (20/20) |
| VERTICAL_9_16 | 8.1% (0/20) | 77.8% (17/20) | 76.2% (17/20) |
| ZOOM | 71.9% (18/20) | 94.4% (20/20) | 94.7% (20/20) |
| LETTERBOX | 0.3% (0/20) | 95.9% (20/20) | 95.9% (20/20) |
| SUBTITLE_OVERLAY | 81.9% (18/20) | 95.9% (20/20) | 95.3% (20/20) |
| PIP_REDUCED_VIEWPORT | 0.3% (0/20) | 60.6% (12/20) | 59.7% (13/20) |
| SPEED_0_8X | 98.0% (20/20) | 100.0% (20/20) | 100.0% (20/20) |
| SPEED_1_25X | 98.5% (20/20) | 99.6% (20/20) | 99.6% (20/20) |

## 14. ±1.0 s versus ±0.5 s

严格口径没有被删除。DINO_RAW 全部 transforms 合计：SA@8 从 ±1 的 92.1% 降至 ±0.5 的 89.5%；DCT 为 53.9% → 52.9%。下降主要集中在中心裁剪、竖屏、PIP 和 subtitle；DIRECT/H264/speed 的 strict top-8 仍为 98.4%–99.8%。这说明 DINO 的一部分几何收益落在 GT 邻近 source grid，而不是精确 grid point，Temporal Alignment 可用与 strict 定位应继续分开报告。

## 15. Position-level results

跨全部 11 transforms 的 position×transformation cells：

| Backend | usable ≥50 | usable ≥75 | usable =100 |
|---|---:|---:|---:|
| DCT | 122/220 | 106/220 | 84/220 |
| DINO_RAW | 209/220 | 198/220 | 145/220 |
| DINO_FLIP_TTA | 210/220 | 196/220 | 146/220 |

这些分母是 20 positions × 11 paired transforms，不是 220 个独立位置。按 source 汇总，DINO_RAW 的主口径 SA@8 为 SOURCE_A 91.3%、SOURCE_B 92.9%；usable≥50 cells 分别 102/110 与 107/110。DCT 两边都是 61/110 cells，SA@8 为 53.2% 与 54.6%。

DINO_RAW 未达到 usable≥50 的只有：PIP 8 个位置、VERTICAL_9_16 3 个位置。其余 9 个 transformation 全部 20/20。匿名 position matrix 已写入 aggregate。

## 16. Correct-rank and score distributions

全部 3,540 positive frames 的 primary (±1 秒) first-correct-rank：

| Backend | rank 1 | rank 2–8 | rank 9–32 | rank 33–128 | rank ≥129 | median / p90 |
|---|---:|---:|---:|---:|---:|---:|
| DCT | 1,614 | 295 | 163 | 225 | 1,243 | 3 / 3,698.5 |
| DINO_RAW | 2,509 | 751 | 167 | 65 | 48 | 1 / 6 |
| DINO_FLIP_TTA | 2,526 | 726 | 160 | 82 | 46 | 1 / 6 |

DINO_RAW raw cosine 分布（p05 / median / p95）：

| Transform | true neighborhood | best wrong | true − wrong margin |
|---|---:|---:|---:|
| DIRECT | .867 / .979 / .991 | .679 / .935 / .976 | -.004 / .041 / .190 |
| H264_REENCODE | .830 / .962 / .980 | .678 / .924 / .967 | -.017 / .036 / .184 |
| MIRROR | .808 / .915 / .958 | .670 / .879 / .946 | -.011 / .024 / .164 |
| CENTER_CROP | .738 / .868 / .918 | .648 / .863 / .925 | -.050 / .003 / .109 |
| VERTICAL_9_16 | .615 / .770 / .869 | .591 / .783 / .868 | -.113 / -.003 / .080 |
| ZOOM | .781 / .904 / .944 | .675 / .891 / .940 | -.038 / .006 / .157 |
| LETTERBOX | .740 / .860 / .913 | .639 / .850 / .902 | -.033 / .007 / .128 |
| SUBTITLE_OVERLAY | .784 / .911 / .963 | .655 / .888 / .943 | -.036 / .018 / .153 |
| PIP_REDUCED_VIEWPORT | .451 / .610 / .703 | .532 / .633 / .710 | -.128 / -.026 / .047 |
| SPEED_0_8X | .876 / .974 / .990 | .686 / .933 / .975 | -.002 / .037 / .199 |
| SPEED_1_25X | .887 / .976 / .989 | .686 / .932 / .975 | -.005 / .038 / .196 |

这些 raw cosines 只用于分布刻画。CENTER_CROP/ZOOM/LETTERBOX 的 median margin 虽为正但很小；VERTICAL 与 PIP 为负。不能把 DINO 的某个固定分数直接当作安全阈值。

## 17. Disagreement analysis

以 usable≥50 为单位，DINO_RAW 相对 DCT 的新增可用位置：MIRROR 20、CENTER_CROP 14、VERTICAL_9_16 17、LETTERBOX 20、ZOOM 2、SUBTITLE 2、PIP 12；没有任何 transformation 出现 DCT-only usable position。DIRECT/H264/两档 speed 均为双方 20/20。

仍然 neither usable 的位置为 PIP 8 个、VERTICAL 3 个。DINO_FLIP_TTA 在 PIP 的 ≥50 cutoff 净增加 1 个位置，但在 ≥75 cutoff 反而少 2 个；非 mirror frame-level SA@8 没有一致改善。其作用应视为 mirror top-1/rank 改善，不是全局 recall 增益。

## 18. Speed results

GT 始终由 `source_time = source_start + speed_ratio × accused_time` 计算。实际 query grid 为：0.8× 每 query 20 frames、1.25× 每 query 13 frames；生成媒体时长分别约 9.96–10.00 秒与 6.44–6.48 秒，均在预注册编码容差内。

DINO_RAW：

- 0.8×：SA@1/8 ±1 = 94.0% / 100.0%；strict SA@8 = 99.8%；usable 20/20/20。
- 1.25×：SA@1/8 ±1 = 91.9% / 99.6%；strict SA@8 = 99.6%；usable 20/20/19。

frame-local embedding 没有因速度变化本身明显退化。本结论不测试 temporal slope fitting。

## 19. PIP result

global DINO 能从 40% viewport 中找回一部分正确区域：episode top-8 99.7%，但 source-aware top-8 只有 60.6%，20 个位置中 12 个达到 ≥50% frame 可用、仅 1 个达到 100%。first-correct rank 的 320 frames 中，84 个为 rank1、110 个为 rank2–8、62 个为 rank9–32、31 个为 rank33–128、33 个低于 128。

PIP median true cosine .610，median best-wrong .633，median margin -.026；true score lower tail 与 negatives 明显重叠。因此“episode 出现在 top-8”不能替代正确 source-time evidence。PIP 是 global embedding 的独立弱项，后续若获批准，region/tile/ROI 应单独实验，不应在本阶段实现。

## 20. Negative characterization

| Backend | top1 cosine median / p95 / max | top8 cosine median / p95 | dominant-source concentration median | top1 temporal coherence median |
|---|---:|---:|---:|---:|
| DCT | .621 / .629 / .630 | .580 / .621 | 1.000 | .3125 |
| DINO_RAW | .539 / .566 / .583 | .491 / .544 | 1.000 | .3125 |
| DINO_FLIP_TTA | .548 / .573 / .594 | .501 / .553 | 1.000 | .3125 |

DINO_RAW 全 positive true-neighborhood cosine 为 p05 .606、median .907、p95 .987；整体 p05 高于 negative top1 max .583，但 PIP 的 p05 .451 低于 negative 区间，不能声称全 transform 可由单一阈值分开。

全部 synthetic negatives 的 top1 都偏向同一个匿名 source，说明此负样本生成器和当前两集存在 source-level bias；它不是“误报”，但必须保留为限制。offset coherence median 只有 5/16 frames（.3125），没有形成强连续时间线。本阶段没有据此选阈值。

## 21. Runtime / resources

| 项目 | 结果 |
|---|---:|
| DINO source index（9,379 frames） | 259.44 s |
| DCT source decode + index | 68.48 s |
| DINO index array RAM / disk | 15.64 MB / 15.64 MB |
| DCT index array RAM / disk | 6.64 MB / 6.64 MB |
| warm index prepare | 0.070 s |
| 240 queries、3 backend modes warm full run | 189.81 s |
| DINO raw+flip decode/embed per query median / p90 | 0.648 / 0.775 s |
| DCT decode/embed per query median / p90 | 0.095 / 0.137 s |
| exact retrieval per positive query median | DCT 0.0112 s；DINO_RAW 0.0117 s；TTA 0.0122 s |
| observed process peak RSS | 518.8 MB |

在当前 2-source / 9,379-frame scale，exact NumPy retrieval 约 12 ms/query，明显不是瓶颈；decode/embedding 主导时间。该结论不能外推到大规模 source library，也不等于批准 FAISS。

## 22. Tests and integrity checks

- Full pytest：`137 passed in 8.16s`。
- Phase 2B-1 tests：preprocessing determinism、CLS/384-d/L2、exact top-k tie order、source/time identity、两档 tolerance、speed GT、position usability、rank buckets、真实 raster-text overlay、negative semantics、sanitization。
- 模型 smoke：真实 ONNX Runtime 推理、重复性、shape、norm。
- Full-run invariants：660 positive backend-case rows、60 negative backend-case rows、92,640 raw tuples；top-8 rank/order/uniqueness 全通过。
- DCT/DINO source/query grids 完全相同；9,379/9,379 source timestamps grid-aligned。
- 完整 retrieval 多次 warm-index 复跑，汇总指标一致。

## 23. Known limitations

1. 只有 2 个真实 episodes，不能描述成跨集稳定性；20 positions 也不是客户总体抽样。
2. 所有 transformation 共享同一批 positions，frame 与 transformation 之间相关；未计算 population CI。
3. negatives 是确定性 synthetic test-pattern，不代表真实平台无关视频；且观察到 source bias。
4. 只测试 DINOv2-small INT8、CPU provider、单一 preprocessing；没有模型族或量化消融。
5. global DINO 在 VERTICAL 与 PIP 仍弱，PIP scores 与 negative 分布重叠。
6. exact cosine 只验证当前 9,379-frame scale，未做大库扩展。
7. 未做 score calibration、decision threshold、Temporal Alignment feed-through 或 end-to-end matcher outcome。
8. 当前时间仍是 nominal 2 FPS grid，不是法律交付所需的 authoritative media PTS 精修。
9. 每个 crop/PIP/subtitle/codec profile 只有一个固定配置；不构成平台参数覆盖。

## 24. Privacy / scope audit

Committed aggregate 只含 `SOURCE_A/SOURCE_B`、position aliases、normalized positions、配置和聚合统计；不含原媒体文件名、本机绝对路径、私有媒体 hash、exact GT timestamp、frame、embedding 或逐帧 candidate。

本阶段没有修改：

- `src/ipvm/` production code；
- DB schema / migration；
- Temporal Alignment；
- DCT descriptor；
- source/query FPS 或 top-k；
- FAISS、SigLIP、audio、ASR、Gemma、Excel 或 Phase 3。

## 25. Required architecture questions

### Q1. DINO_RAW 是否在 MIRROR 上实质优于 DCT？

是。SA@8 ±1 从 7.2% 提高到 99.4%，usable≥50 从 0/20 到 20/20；远超预注册“实质改善”标准。

### Q2. 是否在 CENTER_CROP 上实质优于 DCT？

是。26.9% → 89.4%，6/20 → 20/20。不过 strict SA@8 为 83.1%，median margin 只有 .003，仍非完美定位。

### Q3. 是否在 VERTICAL_9_16 上实质优于 DCT？

是，但绝对水平仍弱。8.1% → 77.8%，0/20 → 17/20；3 个位置未达到 primary usability，median margin 为 -.003。

### Q4. DINO_FLIP_TTA 是否额外改善 mirror 且不伤非 mirror？

它明显改善 mirror top-1（82.2% → 91.9%）和 rank1，top-8 只增加 0.3 个百分点。非 mirror 没有大幅伤害，但全体 top-8 略降 92.1% → 91.9%，VERTICAL、SUBTITLE、PIP 有小幅回落，因此不能说“零代价”。建议只作为可选 mirror-specific augmentation，不作默认 backend。

### Q5. DINO 对真实栅格字幕/文字 overlay 多稳健？

在本固定样式上 SA@8 ±1 为 95.9%，strict 为 92.2%，20/20 positions 达到 ≥50%，19/20 达到 ≥75%。结论仅适用于这一固定 overlay，不外推到任意字体、大小、位置或滚动弹幕。

### Q6. global DINO 能否从 40% PIP 找回正确时间区域？

部分可以，但不可靠：SA@8 ±1 60.6%，12/20 positions usable，median margin 为负。不能作为已关闭能力。

### Q7. frame-local DINO 在 0.8× / 1.25× 是否保持准确？

是。SA@8 ±1 为 100.0% / 99.6%，两组均 20/20 usable；本阶段只评价 frame retrieval，不评价 temporal slope fitting。

### Q8. positive / wrong / negative cosine 分布如何？

DINO_RAW 全 positive true score p05/median/p95 = .606/.907/.987；best-wrong = .608/.879/.964；negative top1 = .517/.539/.566（p05/median/p95），max .583。整体 true 与 negative 有间隔，但 PIP 与部分 VERTICAL score/margin 重叠；不可据此直接定一个全局阈值。

### Q9. 当前规模 exact NumPy retrieval 是否够快？

是。9,379 source frames 上每 8–10 秒 query 的 median 为约 11–12 ms；embedding 耗时远高于检索。不能外推到大库。

### Q10. DINO 是否提供 Temporal Alignment 真正需要的对象？

是。raw top-8 每条候选显式输出 `(accused_time, source_time, source_id, score, rank)`，并保持 source/time identity。是否接入 Temporal Alignment 仍需下一阶段批准。

### Q11. 推荐 A/B/C/D 中哪一个？

推荐 **B — DINO RETRIEVAL PROMISING**。理由：保住 DIRECT/H264 的 top-8/position 指标，预注册三项几何均实质优于 DCT，209/220 配对 cells 达到 primary usability；negative top1 分布未显示与整体 positive true-score 分布不可分，但两者尾部及 PIP 确有重叠，尚未定阈值。候选接口已经满足时间对齐输入。PIP 应作为单独 region/ROI 实验问题保留；按预注册规则，PIP 单项失败不否定 global DINO，也不足以要求在任何 candidate-adapter characterization 之前先做 C。

本报告到此停止，等待独立 reviewer 与 Architecture Owner 决策。
