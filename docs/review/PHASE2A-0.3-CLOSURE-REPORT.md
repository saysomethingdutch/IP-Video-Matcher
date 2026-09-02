# Phase 2A-0.3 Safety & Semantics Closure — Completion Report

日期：2026-09-03　执行范围：remediation only。未修改 production matcher / descriptor / indexer，未实现 Temporal Alignment v2、DINOv2、SigLIP、FAISS。

## 1. Remote 状态（rewrite 前置确认）

```text
git remote -v            -> 无输出，未配置任何远端
git branch -r            -> 空
.git/refs/remotes/       -> 不存在
.git/FETCH_HEAD          -> 不存在
git config remote.*      -> 无
git reflog               -> 仅 5 条本地 commit，无 clone / fetch / push 记录
```

**该仓库从未有过远端，也从未 push。** 因此 history rewrite 成本最低且无需协调；本次未向任何远端 push。

## 2. 泄露范围与处置

泄露仅存在于 **tip commit `8caf2d7` 的单个文件** `tests/test_retrieval_benchmark.py`：真实 `EP01.mp4` 的 SHA-256、含用户名的绝对路径。全历史扫描确认：EP02 的 hash 从未出现；`real_test/*.mp4` 从未作为 blob 被跟踪。

处置：改为明显合成的假值（`/Users/example-user/.../SAMPLE_CLIP.mp4`、`"0"*64`），断言强度不变。因泄露 commit 仍是无子节点、无远端的本地 tip，采用**最小 history rewrite：`git commit --amend`**。

reviewer 报告 `PHASE2A-0.1-0.2-INDEPENDENT-REVIEW.md` 中引用的同一 hash 也已脱敏。

## 3. Threshold schema

裸 `threshold_band` **已移除**（不再产出）。每个 profile 输出两个具名 band，四态枚举 `ABOVE / STRADDLES / BELOW / UNKNOWN`：

```text
strict_threshold_band             <- 由 strict recall@8 自己的 CI 推出
alignment_usable_threshold_band   <- 由 alignment-usable recall@8 自己的 CI 推出（= readiness gate）
readiness_gate_metric / readiness_gate_band  <- 显式命名 gate 指标与其 band
```

`_threshold_band(None, None)` 返回 `UNKNOWN`（此前静默返回 `STRADDLES`）。

## 4. Readiness Gate 指标

正式指标：**`alignment_usable_recall@8`**。strict recall 保留为 localization diagnostic，不作为进入 Temporal Alignment v2 的 gate。已写入 ADR-0005。

## 5. Summary table（strict 与 usable 各自的 CI 与 band 并列）

| Profile | Strict R@8 | Strict CI | Strict band | Usable R@8 | Usable CI | Usable band (gate) | True loss@8 |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.9533 | [0.9383, 0.9675] | STRADDLES | 0.9942 | [0.9892, 0.9983] | **ABOVE** | 0.58% |
| p1 | 0.9525 | [0.9367, 0.9667] | STRADDLES | 0.9933 | [0.9883, 0.9975] | **ABOVE** | 0.67% |
| p2 | 0.9525 | [0.9367, 0.9667] | STRADDLES | 0.9942 | [0.9892, 0.9983] | **ABOVE** | 0.58% |
| p3 | 0.9533 | [0.9383, 0.9675] | STRADDLES | 0.9942 | [0.9892, 0.9983] | **ABOVE** | 0.58% |

置信区间与由它推出的 band 相邻呈现。**不再出现 usable CI 与 strict band 并列。**

## 6. 分指标聚类统计（无裸字段）

| Profile | Strict ICC | Strict n_eff | Usable ICC | Usable n_eff |
|---|---:|---:|---:|---:|
| direct | 0.0408 | 828.5 | 0.0213 | 972.5 |
| p1 | 0.0383 | 844.0 | 0.0171 | 1009.6 |
| p2 | 0.0383 | 844.0 | 0.0213 | 972.5 |
| p3 | 0.0408 | 828.5 | 0.0213 | 972.5 |

两个口径的 ICC 确实不同（strict ≈0.04，usable ≈0.02），证实此前共用裸字段确有归属歧义。`intra_cluster_correlation` / `design_effect` / `effective_sample_size` / `confidence_interval` 已全部移入 `profiles.<p>.strict` 与 `profiles.<p>.alignment_usable`；profile 块中不再有裸统计字段。

## 7. Grid period 推导

`source_grid_period_seconds = 1.0 / matching.coarse_fps`（报告中记录 `source_grid_period_derivation`）。`alignment_usable_tolerance_seconds = grid_steps × grid_period`。统计实现中已无裸 `0.5` 字面量；`coarse_fps == 2.0` 守卫保留。

## 8. 编码确定性与重跑

x264：`threads=1:sliced-threads=0:lookahead-threads=1:deterministic=1`；x265：`pools=none:frame-threads=1:wpp=0`；并加 `-threads 1`。

**两次独立完整重跑（direct/P1/P2/P3 × 100 intervals × 2400 frames）：148 个字段逐一比对，0 处差异。** 此前 p1/p2/p3 有 1–5 帧 / 1200 的 run-to-run 漂移。

## 9. Committed artifacts

手工编纂的 `phase2a-0-direct-baseline-v0.1.json` **已删除**，替换为 harness 生成的 `phase2a-0-direct-baseline-v0.2.json`。平台档更新为 `phase2a-0-platform-characterization-v0.3.json`。两份均通过 `/Users/` / `real_test` / `EP0x.mp4` / `sha256` / `file_path` / `index_path` 的断言检查。schema 升级为 `schema-v0.3.json`。

## 10. 新增测试

`wrong episode 不算 usable`、`band 与各自指标绑定`（构造 strict 全失败 / usable 全命中，band 必须分别为 BELOW / ABOVE）、`non-degenerate ICC`（ICC>0.9 与 ICC=0 两组对照）、`strict 与 usable 统计独立`、`profile 输出路径不互撞`、`实际 codec/分辨率验证`（ffprobe 校验 h264 720p / h264 480p / hevc 720p）、`转码字节确定性`、`grid period 由 fps 推导`、`UNKNOWN band`、`匿名化测试只用合成值`。

`pytest -q`：**49 passed**（此前 39）。

## 11. Platform characterization 的正确解释

> **compression / resolution / codec 的扰动低于当前低频 DCT descriptor 的主要灵敏度范围。**

实测：p1/p2/p3 的 `1 - cos ≈ 6e-6 ~ 1.3e-5`，比 correct 与 best-wrong 帧之间的 median score margin（≈`3.7e-3`）低 2–3 个数量级；即使压到 180p / 60 kbps 仍低约一个数量级。

**不得泛化为** crop / mirror / subtitle overlay / picture-in-picture / 摄像头翻拍 / Reaction 画中画 的 robustness 结论——这些变换改变低频结构，属未测量的另一类威胁向量。已写入 ADR-0005、`benchmark/retrieval/README.md`、`README.md`、schema `x-interpretation-limit`，并出现在 harness 生成的 markdown 报告中。

## 12. 未触碰

`matching.py` / `features.py` / `indexer.py` / `database.py` / `models.py` / `config.py` / `audit.py` / `media.py` / `project.py` / `doctor.py` / `config/default.yaml` 全部 0 改动。source/query FPS 仍为 2.0。sampling population、ground truth、top-k、strict tolerance 未变。未实现任何 Phase 2 能力。

## 13. 等待 Architecture Owner 裁定

Phase 2A-0 是否正式关闭。
