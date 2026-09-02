# Phase 2A-0.1 / 2A-0.2 独立技术审查（第四次）

- **审查对象**：`8caf2d7 Complete Phase 2A-0 platform characterization hardening`
- **上游基线**：`1dd337d Complete Phase 2A-0 retrieval characterization`
- **实现方**：临时 implementation agent DeepSeek V4 Pro
- **审查人身份**：独立 reviewer，非本阶段开发者
- **审查日期**：2026-09-03
- **方法**：全量阅读 diff（32322 insertions）、schema v0.2、ADR-0002/0005 修订、新增测试；**未修改任何生产代码**；用 `real_test/` 两集真实素材**完整重跑了 4-profile benchmark**；运行 5 组只读探针
- **前置**：`docs/review/PHASE2A-0-INDEPENDENT-REVIEW.md`（第三次审查）

---

# 1. Executive Verdict

## **CONDITIONAL PASS**

Phase 2A-0.1 / 2A-0.2 的**技术实现是正确的**，而且在几个我预期会出问题的地方做得比上一轮更好：

- **transcode 是真的**（ffprobe 验证：p1 = h264 1280×720 @2131 kbps，p2 = h264 854×480 @1059 kbps，p3 = **hevc** 1280×720 @1714 kbps），管线顺序正确（真实视频 → 转码 → 解码 → 160×90 → DCT），descriptor 不是逐位相同；
- **`alignment_usable` 定义正确**：±1 grid = 0.5 s，对称，只接纳两个 bracketing frame，不误纳 ±1.5/±2 grid，要求 episode 正确，strict ⊂ usable 完备互斥；
- **统计实现正确**：cluster bootstrap 的 resample 单元确实是 interval、整段一起重采样、percentile 法、point estimate 来自原始样本而非 bootstrap 均值——**我独立复算的 CI 与 harness 逐位一致**；
- **threshold band 的绑定在代码里是明确的**（`ci["strict"]["8"]`），harness 自己生成的 markdown 也**正确地**把该列标为 `Strict R@8 95% CI | Band`；
- **scope 干净**：production matcher / descriptor / indexer / database / models / config **全部未改**，FPS 仍为 2.0 且有守卫，sampling population / ground truth / top-k / strict tolerance 全部未动，`.DS_Store` 未入库，`real_test/` 与 `output/` 仍 ignored。

**三个否决 PASS 的理由：**

**1（Critical）**　`tests/test_retrieval_benchmark.py` 把 **真实案件素材 `real_test/EP01.mp4` 的 SHA-256** 和 **含用户名的绝对路径** 明文提交进了 git，并且已经进入不可变历史（`8caf2d7`）。讽刺的是，这正是那个"验证匿名化有效"的测试。Architecture §54 明确禁止案件材料进入 git repo 与 test fixtures，§41 又把 SHA-256 定义为文件的权威身份。

**2（Critical）**　Completion report 的 Summary table 把 **`Usable 95% CI`** 与 **由 strict CI 推出的 `Band`** 并排放在同一行，而 strict CI 根本没有出现在那张表里。读者按 Owner 的判定规则去读那张表，会算出 `ABOVE` 却看到 `STRADDLES`。这是本次 review 最高优先级问题，答案在 §3。

**3（Major）**　**复现不是逐位一致**。libx264/libx265 在本机是**非确定性**的（同一命令三次运行产生三份不同字节的文件），因此 p1/p2/p3 每次跑都会有 1–5 帧/1200 的浮动。Completion report §7/§11 把 `0.9942 vs 0.9933`、`0.58% vs 0.67%` 当作 profile 之间的差异陈述——**这些差异全部落在编码器噪声底之内**。我的复现里 p1 = 0.9942，与 direct 完全相同。

**不给 FAIL 的理由**：以上三点没有一条推翻测量本身。核心结论（platform-like compression/resolution 不显著挤压 DCT）在我的独立复现和 paired 分析下**成立且更强**：k=8 上四个 profile 的失败集合 Jaccard 为 **1.000 / 0.982 / 1.000**，是**同一批帧**在失败，不是"总数碰巧一样"。

---

# 2. Reproduction Summary

在独立项目根目录（`real_test/` 两集，2400.6 s + 2289.3 s，1080p h264 25 fps）完整重跑：

```
schema=retrieval-characterization-v0.2  intervals=100  variants=400
profile_status = {direct: COMPLETED, p1: COMPLETED, p2: COMPLETED, p3: COMPLETED}

profile  frames  ivl  strictR@8  usableR@8       strict CI        usable CI       band     ICC   n_eff  trueLoss
direct     1200  100     0.9533     0.9942  [0.9383,0.9675]  [0.9892,0.9983] STRADDLES  0.0408  828.5    0.0058
p1         1200  100     0.9533     0.9942  [0.9383,0.9675]  [0.9892,0.9983] STRADDLES  0.0408  828.5    0.0058
p2         1200  100     0.9525     0.9933  [0.9367,0.9667]  [0.9883,0.9975] STRADDLES  0.0383  844.0    0.0067
p3         1200  100     0.9533     0.9942  [0.9383,0.9675]  [0.9892,0.9983] STRADDLES  0.0408  828.5    0.0058
```

**128 个字段逐一比对 committed aggregate，19 处不同**，全部集中在 p1/p2/p3（direct 逐位一致）：

| profile | 字段 | 复现 | committed | 差异规模 |
|---|---|---:|---:|---|
| p1 | usableR@8 | 0.9942 | 0.9933 | **1 帧 / 1200** |
| p1 | true_loss@8 | 0.0058 | 0.0067 | 1 帧 |
| p2 | strictR@1 | 0.8167 | 0.8125 | 5 帧 |
| p3 | strictR@1 | 0.8150 | 0.8108 | 5 帧 |
| p3 | usableR@1 | 0.9142 | 0.9117 | 3 帧 |

根因见 Major M-2：编码器非确定性。**`direct` 完全可复现**，这本身是个好信号——说明差异确实来自转码而非抽样或统计。

`pytest -q`：`39 passed`。

---

# 3. Critical

## C-1　真实案件素材的 SHA-256 与绝对路径被提交进 git 历史

`tests/test_retrieval_benchmark.py:278-298`：

```python
def test_anonymized_aggregate_excludes_sensitive_paths_and_hash() -> None:
    report = {"observations": [{
        ...
        "file_path": "/Users/<用户名>/IP-Video-Matcher/real_test/EP01.mp4",
        "sha256": "<EP01 的真实 SHA-256，此处已脱敏>",
        ...
```

我本地核验：

```
shasum -a 256 real_test/EP01.mp4  ->  <EP01 的真实 SHA-256，此处已脱敏>
                                       *** 与测试文件中的常量完全一致 ***
git log --all -S"<该 hash>" -- tests/  ->  8caf2d7
```

泄露的内容：
1. **真实权利作品第 1 集的 SHA-256**——这是该文件的密码学指纹，任何持有同一份文件的人（权利人、对方当事人、平台）都能验证"该仓库的作者持有这个确切文件"；
2. **含 macOS 用户名的绝对路径**；
3. **文件名 `EP01.mp4` 与目录名 `real_test`**。

违反的条款：
- Architecture §54：「绝不能把案件文件放进 model cache / prompt examples / **git repo** / **test fixtures**」；
- Architecture §41：SHA-256 是系统内绑定案件媒体身份的权威键；
- 本 commit 自己写的 README：「不写原文件名、路径、SHA-256 或案件标识」。

**严重性**：不是内容泄露，但它是这套架构自己定义的案件身份标识，而且**已进入不可变的 git 历史**——普通 follow-up commit 删不掉，需要 history rewrite（`filter-repo` / `filter-branch`）＋强推。若该仓库曾推送到任何远端，还需要评估远端历史。

**最刺眼的地方**：这段代码的目的是证明匿名化管线有效。它用把真值明文写进 git 的方式，来断言输出里没有真值。合理写法是用一个明显合成的假值（`/tmp/example/CLIP.mp4` + 64 个 `a`）——断言强度完全相同。

## C-2　Completion report 的 Summary table 在最高风险字段上构成语义误导

Completion report 末尾：

```text
Profile | Strict R@8 | Usable R@8 | Usable 95% CI    | Band       | True Loss
DIRECT  | 0.9533     | 0.9942     | [0.9892, 0.9983] | STRADDLES  | 0.58%
```

`Band` 紧邻 `Usable 95% CI`，而 **strict CI 在这张表里根本没出现**。按 Owner 的判定规则读这一行：`lower = 0.9892 >= 0.95 → ABOVE`，但表里写着 `STRADDLES`。读者只有翻到 §10 才能发现 band 其实来自另一组、表中未显示的区间。

**这不是实现缺陷**——代码和 harness 自己生成的 markdown 都是对的：

```
harness markdown 第 20 行：
| Profile | Frames | Intervals | Strict R@1 | Strict R@8 | Usable R@1 | Usable R@8 | Strict R@8 95% CI | Band | True loss@8 |
                                                                                       ^^^^^^^^^^^^^^^^^^^^^^ 标注正确
```

**是人写的交接文档缺陷，而这份文档正是 Architecture Owner 用来做 gate 裁定的输入。** 对一个把「不得用 point estimate 直接宣布通过」写进 ADR-0005 的项目，让 Owner 在 CI 与 band 的对应关系上产生歧义，是同一类风险的另一种形态。

详细回答见 §5 A–E。

---

# 4. Major

## M-1　platform-like transcode 对 DCT 的扰动比判定裕度低 2–3 个数量级，测试几乎没有统计功效

转码是真的（见 §7），descriptor 也确实变了（不是逐位相同）。但**变化的量级**：

```
profile                      realized kbps   1 - mean cos    mean L2
p1  720p 2000k                       1875       9.30e-06    0.00421
p2  480p 1000k                        920       1.33e-05    0.00508
p3  720p 1600k (hevc)                1613       6.44e-06    0.00352
--- 我额外加的更狠的档 ---
    360p  400k                         356       2.58e-05    0.00705
    360p  200k                         172       8.48e-05    0.01263
    240p  120k                         113       9.78e-05    0.01341
    180p   60k                          60       2.08e-04    0.01979

参照量级：
  correct 与 best-wrong 帧之间的 median score margin = 3.68e-03
  p10 score margin                                    = -4.19e-03
```

**p1/p2/p3 注入的扰动是 6e-6 ~ 1.3e-5，比决定排序的 margin 小 280–600 倍。** 即使我把档位压到 **180p @ 60 kbps**（远超任何真实平台的劣化），扰动也只有 2.08e-4，仍比 margin 小 **18 倍**。

**根因**：DCT 描述子先把画面降到 160×90，再降到 32×32，只保留 12×12 低频系数。1080p→720p→160×90 与 1080p→160×90 丢掉的几乎是同一批信息。压缩与分辨率降低在进入描述子之前就被描述子自己的降采样抹掉了。

**因此本测试在设计上就无法失败。** 这个结论本身有价值——它确证了**码率与分辨率不是这个描述子的威胁向量**。但它必须这样表述，而不能表述成"DCT 挺过了 platform-like transcode"，因为后者会被读成对 crop / mirror / subtitle / PiP / letterbox 的robustness暗示，而那些恰恰是会改变低频结构的变换。

Completion report §15 说了「只改变 compression / resolution / codec 一类变量」——这是诚实的。**缺的那句是**：这类变量已被实测证明位于描述子灵敏度下限之下。

## M-2　编码器非确定性使 profile 结果不可逐位复现，而报告把噪声级差异当作 profile 效应

同一条命令、同一输入、同一参数，连跑三次：

```
profile  run  file sha256(前16)   与 run1 相同   descriptor 相同
p1        1   cfe433429455010e          True            True
p1        2   ac9c32568174fe8f         False           False
p1        3   b1914169d754c66f         False           False
p2        1/2/3  三份不同字节            False           False
p3        1/2/3  run2 与 run3 相同，均 != run1          False
```

`libx264/libx265 -preset veryfast` 默认多线程、帧级并行，输出不是位确定的。descriptor 因此每次都略有不同（cos ≈ 0.99999）。

**后果**：

| Completion report 的陈述 | 我的复现 | 判定 |
|---|---|---|
| §7「P1 usable R@8 = 0.9933」 | 0.9942 | 1 帧差，噪声 |
| §11「DIRECT/P3 true loss 0.58%，P1/P2 0.67%」 | direct/p1/p3 全为 0.58%，p2 为 0.67% | **profile 排序不可复现** |
| §6「P1 strict R@1 = 0.8167」 | 0.8167 ✓ | 一致 |
| §8「P3 strict R@1 = 0.8108」 | 0.8150 | 5 帧差 |

**§11 的 true-loss 分档表在下一次运行就会变。** 它被写成了一张像是有信息量的表，实际是抛硬币。

修复方向（不需改测量逻辑）：给 `_make_platform_transcode_clip` 加 `-threads 1` 与编码器确定性选项（x264 `-x264-params threads=1:sliced-threads=0`，x265 `-x265-params pools=none:frame-threads=1`），或在报告中显式标注 profile 指标的 run-to-run 噪声带（±5 帧 / 1200 ≈ ±0.42%），并停止陈述小于该带宽的差异。

## M-3　没有 `alignment_usable` 的 threshold band 字段，schema 里的 `threshold_band` 也不带指标限定

`_profile_block` 只产出一个字段：

```python
strict_ci_8 = ci["strict"]["8"]
band = _threshold_band(strict_ci_8[0], strict_ci_8[1])
...
"threshold_band": band,
```

schema v0.2 里也只有一个不带限定的 `threshold_band`（`enum: [ABOVE, STRADDLES, BELOW]`）。

**风险**：ADR-0005 与 Completion report §16 都把「是否采用 `alignment_usable_recall@k` 作为官方口径」留给 Owner 裁定。**如果 Owner 裁定采用 usable 口径，当前没有任何字段可供 Gate 消费**——消费方读 `threshold_band` 会静默拿到 strict 的结果。这正是我上一轮 review 提出的 D 问题，实现方保留了歧义。

## M-4　ADR-0005 要求 grid period 从配置推导，实现是模块级硬编码

ADR-0005 本次新增的原话：

> `±1` grid step 的秒数从 sampling configuration 推导（当前 2 FPS → 0.5 秒），**不得在统计层硬编码**。

实现：

```python
retrieval_benchmark.py:28   SOURCE_GRID_PERIOD_SECONDS = 0.5          # 模块级常量
retrieval_benchmark.py:529  if config.matching.coarse_fps != 2.0: raise ...
schema-v0.2.json            "source_grid_period_seconds": {"const": 0.5}
                            "alignment_usable_tolerance_seconds": {"const": 0.5}
```

有守卫（FPS 不是 2.0 直接抛错）所以**当前不会产出错误结果**，但 grid period 确实没有从 `coarse_fps` 推导，schema 也把它 const-lock 死了。这是 ADR 与实现的直接冲突，且会在未来改 source FPS 时同时需要改三处。

## M-5　committed 的 `phase2a-0-direct-baseline-v0.1.json` 不是任何命令的产物

该文件的顶层键与 `1dd337d` harness 的输出不一致：

| | 有 | 无 |
|---|---|---|
| committed v0.1 | `result_kind`、`metric_semantics`、`episode_scope_note`、`independent_content_position_count`、`nominal_query_frame_count` | `scope`、`query_variant_count`、`by_query_mode`、`by_source_position_bucket`、`by_source_phase_bucket` |
| 1dd337d 代码输出 | 上述后五项 | 上述前五项 |

而且 `observed_threshold_band` 被置为 `null`。这些字段在 `1dd337d` 的 `retrieval_benchmark.py` 里一次都没出现。

**结论：这是手工编纂的文件。** 它的内容是好的（`metric_semantics` 明确写了 "legacy strict... no cluster-bootstrap confidence interval"，`episode_scope_note` 写了 "episode_n=2; not a cross-episode stability estimate"——正是我上一轮要求的上下文），但它**不能由任何命令复现**。这与第一次审查里 `benchmark/results/phase1_baseline.json` 手工转写的问题同源。

对比之下，`phase2a-0-platform-characterization-v0.2.json` **是**真正的 harness 产物（我逐字段比对确认结构一致），这是进步。

---

# 5. 最高优先级：Threshold Band 语义审计（A–E 直答）

## A.　`threshold_band` 实际绑定的是哪个指标？

**绑定 `strict_retrieval_recall@8`。** 三重证据：

1. **代码**（`retrieval_benchmark.py`，`_profile_block`）：
   ```python
   strict_ci_8 = ci["strict"]["8"]
   band = _threshold_band(strict_ci_8[0] if strict_ci_8 else None,
                          strict_ci_8[1] if strict_ci_8 else None)
   ```
   `ci["alignment_usable"]` 被计算并存储，但**从未参与 band 计算**。

2. **committed JSON**：`direct` 的 strict CI = `[0.9383, 0.9675]`（跨越 0.95），usable CI = `[0.9892, 0.9983]`（完全高于 0.95），而 `threshold_band = "STRADDLES"` —— 只有 strict CI 能产生这个结果。

3. **harness 自己的 markdown**：列头写作 `Strict R@8 95% CI | Band`，标注正确。

## B.　如果绑定 strict，当前四档 `STRADDLES` 数学上是否正确？

**正确。** 逐档核验（我用 `_threshold_band` 直接跑）：

```
DIRECT [0.9383, 0.9675]  lower<0.95 且 upper>=0.95  -> STRADDLES  ✓
P1     [0.9383, 0.9675]                              -> STRADDLES  ✓
P2     [0.9367, 0.9667]                              -> STRADDLES  ✓
P3     [0.9383, 0.9675]                              -> STRADDLES  ✓
```

边界行为也正确：`[0.95, 0.99] -> ABOVE`、`[0.90, 0.9499] -> BELOW`、`[0.949999, 0.95] -> STRADDLES`。

## C.　Summary table 把 `Usable 95% CI` 与 strict `Band` 放在同一列组，是否构成严重语义误导？

**是，构成严重误导。** 见 Critical C-2。

判据很直接：**一个只读那张表的读者，按 Owner 明确写下的判定规则去核对，会得到与表中 Band 相反的结论**，且表里没有任何线索指向真正的依据。对一份用于 gate 裁定的交接文档，这是必须修的缺陷。

强调：**harness 输出没有这个问题**，只有人写的 completion report 有。修复只需把该列改成 `Strict R@8 95% CI`，或加一列同时列出两组 CI 与两个 band。

## D.　是否应该同时输出 `strict_threshold_band` 和 `alignment_usable_threshold_band`？

**应该，而且这是本轮最值得做的一处架构收口。**

理由：
1. ADR-0005 定义了双口径但**没有说 band 绑定哪个**；Completion report §16 明确把"是否采用 usable 为官方口径"留给 Owner。**在裁定发生之前，一个不带限定的 `threshold_band` 必然被某一方误读。**
2. Gate 消费方读的是 JSON 字段名。`threshold_band` 这个名字不携带任何关于指标的信息。
3. 两个 band 都已具备计算所需的 CI（`ci["strict"]` 与 `ci["alignment_usable"]` 都已存在），**成本是两行代码**。

建议：
```
strict_threshold_band              : 由 ci.strict["8"] 推出
alignment_usable_threshold_band    : 由 ci.alignment_usable["8"] 推出
threshold_band                     : 标记 deprecated，或直接移除以强制消费方明确选择
```
并在 schema v0.3 里把两者都设为 required，把裸 `threshold_band` 移出 required。

## E.　按当前 usable CI，若 band 应用于 usable 指标，是否应全部为 `ABOVE`？

**是，四档全部 `ABOVE`。** 我用 `_threshold_band` 实际跑过：

```
DIRECT [0.9892, 0.9983]  lower=0.9892 >= 0.95  -> ABOVE
P1     [0.9883, 0.9975]  lower=0.9883 >= 0.95  -> ABOVE
P2     [0.9883, 0.9975]  lower=0.9883 >= 0.95  -> ABOVE
P3     [0.9892, 0.9983]  lower=0.9892 >= 0.95  -> ABOVE
```

而且余量很大：usable CI 的下界比阈值高约 3.8–4.9 个百分点。**这是本次测量最强的一个结果，而它目前没有任何字段承载。**

---

# 6. Scope Audit

## 逐项核实

| # | 检查项 | 结论 |
|---:|---|---|
| 1 | 是否只实现 2A-0.1 / 2A-0.2 | ✅ 是。改动集中在 `retrieval_benchmark.py`（+455）、`benchmarking.py`（+45，两个新 helper）、`cli.py`（+3，mode 选项）。无 Temporal Alignment v2、无 DINOv2/SigLIP/FAISS |
| 2 | production matcher / visual descriptor 是否未变 | ✅ **完全未变**。`matching.py`、`features.py`、`indexer.py`、`database.py`、`models.py`、`config.py`、`audit.py`、`media.py`、`project.py`、`doctor.py`、`config/default.yaml` 全部 0 改动 |
| 3 | source/query FPS 是否仍为 2 FPS | ✅ `source_fps: 2.0` / `query_fps: 2.0`，schema `const` 锁死；`RetrievalBenchmark.__init__` 在 `coarse_fps != 2.0` 时抛错 |
| 4 | 是否偷改 sampling population / ground truth / top-k / hit tolerance | ✅ 无。`_sample_off_grid_start`、`_balanced_phase_assignments`、bin 划分逻辑**一行未动**；`TOP_K_VALUES = (1,8,32,128)` 未变；strict `tolerance_seconds` 默认仍为 0.26；ground truth 仍是"query 取自源片 start + k·0.5"这一构造事实 |
| 5 | `.DS_Store` 是否未纳入 | ✅ 未纳入（工作区里 `M .DS_Store` 是先前就存在的未提交改动，不在本 commit 内） |
| 6 | `real_test/` 与 full-detail outputs 是否仍 ignored | ✅ `.gitignore:2 real_test/`、`.gitignore:4 output/` 均生效，`git check-ignore` 确认 |
| 7 | anonymized aggregate 是否不含敏感信息 | ✅ **两份 committed aggregate 全部干净**（详见 §11） |

## Scope creep

**未发现 scope creep。** 两处需要备案但属合理范围内的变更：

- **CLI 默认 mode 从 `("direct","reencode")` 改为 `("direct","p1","p2","p3")`**。`reencode` 保留可选。合理——旧变体已被上一轮证明零信息量。
- **`by_query_mode` 与 `profiles` 内容重叠**。前者是旧结构，后者带完整统计块。冗余但无害；建议 v0.3 合并。

**唯一的实质越界是 C-1**：把真实案件的 SHA-256 与路径写进 test fixture，这违反 §54 对 test fixtures 的明确禁令。

---

# 7. Platform Transcode Audit

## 用 ffprobe 实测生成产物（不是只看命令串）

```
profile  codec   WxH        pix_fmt   dur     size KB   realized kbps   target
direct   （无转码，直接从 1080p 源解码）
p1       h264    1280x720   yuv420p   6.00     1560.8            2131   2000k
p2       h264     854x480   yuv420p   6.00      775.4            1059   1000k
p3       hevc    1280x720   yuv420p   6.00     1255.6            1714   1600k
```

**三档全部符合 Architecture Owner 的规格：**

| | 规格要求 | 实测 | 判定 |
|---|---|---|---|
| P1 | H.264 / 1280×720 / ~2 Mbps | h264 / 1280×720 / 2131 kbps | ✅ |
| P2 | H.264 / 854×480 或等价 480p / ~1 Mbps | h264 / 854×480 / 1059 kbps | ✅ |
| P3 | HEVC/H.265 / 1280×720 / 1.5–2 Mbps | **hevc** / 1280×720 / 1714 kbps | ✅ |

`profile_status` 三档均为 `COMPLETED`（本机 libx265 可用，`_encoder_available("libx265")` 返回 True）。

## 管线顺序正确

`_make_platform_transcode_clip`：

```
-ss {start} -i {真实 1080p 源}
-vf trim=duration=6.0, setpts=PTS-STARTPTS, scale={720p/480p}:flags=area
-c:v {libx264|libx265} -b:v -maxrate -bufsize -pix_fmt yuv420p
  → 写出真实的转码视频文件
```

然后 harness 对**该文件**调用 `extract_grayscale_frames(query_path, fps=2.0, width=160, height=90)` → 正常预处理 → DCT。

**不存在"先降到 160×90 / 先生成 descriptor 再模拟转码"的情况。** 转码作用于真实视频像素，descriptor 在其之后计算。管线是：

```
真实 clip → platform-like video transcode（真实编码器，真实码率控制）
        → decode → 正常 retrieval 预处理（fps=2, 160×90, gray）→ DCT   ✅
```

## descriptor 确实不同

```
profile   bit-identical vs direct   mean cos    min cos    mean L2   max L2
p1                        False     0.999990   0.999978   0.004306  0.006588
p2                        False     0.999985   0.999959   0.005212  0.009027
p3                        False     0.999993   0.999978   0.003563  0.006706

profile 互比：p1/p2、p1/p3、p2/p3 均 bit-identical=False
```

**不是逐位相同，管线没有错误复用 direct 的 descriptor 或 cache。** 补充证据：`query_path` 命名已包含 profile（`f"{alias}-{sample_index:03d}-{mode}.mp4"`），上一版的 `f"{alias}-{sample_index:03d}.mp4"` 会在 profile 间互相覆盖——**这个覆盖 bug 在本 commit 中被修掉了**，值得记一笔。

## 但见 Major M-1

转码真实、descriptor 确实变了、变化量却比判定裕度低 2–3 个数量级。**"P1/P2/P3 不掉点"是可信的实测结果，但它的信息量是"这类劣化在描述子灵敏度下限之下"，不是"DCT 对平台转码鲁棒"。**

---

# 8. Statistical Audit

## Cluster bootstrap（问题 8 逐项）

| # | 检查项 | 结论 |
|---:|---|---|
| 1 | bootstrap 单元是否确实是 interval | ✅ `rng.integers(0, len(clusters), size=len(clusters))`，clusters 由 `_clusters_for_metric` 按 `interval_id` 分组 |
| 2 | 整个 interval 内的相关 frame 是否一起 resample | ✅ `pool = [value for index in sample_indexes for value in clusters[int(index)]]`——被抽中的 cluster 整体入池 |
| 3 | profile 内是否没有把 direct/P1/P2/P3 当独立 cluster | ✅ `_profile_block` 在**已按 profile 切分**的观测上工作，每个 profile 独立 100 个 cluster × 12 帧。不存在跨 profile 混入 |
| 4 | seed 是否 deterministic | ✅ `CLUSTER_BOOTSTRAP_SEED = 20260902`，同 seed 两次调用结果完全相同 |
| 5 | iteration 数是否足够 | ✅ 2000。对 95% percentile CI 足够（尾部各 50 个样本） |
| 6 | percentile 方法是否与文档一致 | ✅ `np.percentile(..., 2.5/97.5)`，Completion report §4 写的就是 percentile method |
| 7 | point estimate 是否来自原始样本 | ✅ 来自 `aggregate_observations(observations)`，与 bootstrap 完全解耦。我构造对照：原始点估计 0.700000 vs bootstrap 均值 0.699075，**确实不同** |
| 8 | 能否独立复现 | ✅ 我用 numpy 从零重写同一算法，得到 `[0.450000, 0.900000]`，与 `_cluster_bootstrap_ci` **逐位一致** |

**`DIRECT usable CI [0.9892, 0.9983]` 独立可复现**：我的完整重跑得到完全相同的区间。

一处需要备案：**所有 metric、所有 profile 共用同一个 bootstrap seed**，因此重采样索引在 strict/usable、direct/p1/p2/p3 之间完全相同。这对配对比较其实是好事（消除了比较噪声），但它意味着这些 CI 不是相互独立的抽样，schema/文档应写明。

## ICC / effective sample size（问题 9 逐项）

| # | 检查项 | 结论 |
|---:|---|---|
| 1 | ICC 公式 | ✅ one-way ANOVA：`(MSB - MSW) / (MSB + (n₀-1)·MSW)`，其中 `n₀` 用了**不等 cluster 的标准修正** `(N - Σnᵢ²/N)/(k-1)` |
| 2 | cluster size 假设 | 实际全部相等（每 profile 每 interval 12 帧），故修正不起作用 |
| 3 | 是否存在不等 cluster | ❌ 不存在。100 个 interval × 12 帧，完全均衡 |
| 4 | effective sample size 是否只作诊断 | ✅ 是。CI 由 cluster bootstrap 独立产生，**没有**用 `n_eff` 去套普通 binomial 公式。Completion report §4 明确写了「不使用普通 frame-level binomial CI 替代 cluster bootstrap」 |
| 5 | 四个 profile 是否分别计算 | ✅ `_profile_reports` 逐 profile 调用 `_profile_block`。实测 ICC：direct/p1/p3 = 0.0408，p2 = 0.0383 |
| 6 | 「ICC 只对 strict recall@8 估计」是否与实现一致 | ✅ 一致。`icc_metrics = _intra_cluster_correlation(_clusters_for_metric(observations, "hits", 8))`——只用 strict、只用 k=8 |

**一处不一致（Minor）**：ICC 分母用 `adjusted_cluster_size`，而 `design_effect = 1 + (mean_cluster_size - 1) · icc` 用 `mean_cluster_size`。cluster 等大时两者相同，故当前无数值影响，但公式不自洽。

**关于"usable recall 是否也应该有独立 ICC/CI"——我的判断：**

- **CI：已经有了，而且做法正确。** `ci["alignment_usable"]` 是对 `alignment_usable_hits` 自己的 cluster 做 bootstrap，**不是**从 strict CI 平移或换算得来的。这一点我逐行确认过。
- **ICC：应该补。** 当前 `intra_cluster_correlation` / `design_effect` / `effective_sample_size` 三个字段只反映 strict@8，但它们在 JSON 里是 profile 级的裸字段，读者会自然地把它们套到 usable 上。usable 的 hit 率是 0.994，方差结构与 strict（0.953）不同，ICC 必然不同。**建议改为 `intra_cluster_correlation: {strict: ..., alignment_usable: ...}` 的结构，或至少改名为 `strict_intra_cluster_correlation`。**

## 与上一轮的 ICC 差异（备案，非缺陷）

我上一轮独立算出 ICC = 0.0825（2400 观测、cluster size 24，direct+reencode 混算）；本实现给出 0.0408（1200 观测、cluster size 12，逐 profile）。**本实现的做法更正确**——不该把不同 profile 混进同一个 cluster。有趣的是两者的 `n_eff` 殊途同归：

```
我：   2400 / (1 + 23×0.0825) = 2400 / 2.90 = 828
实现： 1200 / (1 + 11×0.0408) = 1200 / 1.449 = 828.5
```

---

# 9. Strict vs Alignment-Usable Audit

## 定义审计（问题 4 逐项）

| # | 检查项 | 结论 |
|---:|---|---|
| 1 | grid period 是否从 FPS/config 推导 | ❌ **硬编码**。`SOURCE_GRID_PERIOD_SECONDS = 0.5` 是模块级常量，schema 也 `const` 锁死。有 `coarse_fps != 2.0` 守卫，故当前不产错，但与 ADR-0005 本次新写的条款直接冲突（见 M-4） |
| 2 | ±1 grid 是否真正对称 | ✅ `abs(timestamp - expected) <= 0.5`，绝对值，天然对称。探针：expected=10.0 时 9.5 与 10.5 均被接纳 |
| 3 | endpoint / 浮点边界 | ✅ 闭区间 `<=`。距离恰为 0.500 时接纳，1.000 时拒绝 |
| 4 | 是否误纳 ±1.5 / ±2 grid | ✅ **未误纳**。expected=10.3 时 tol=0.5 只接纳 {10.0, 10.5}（距离 0.3 / 0.2）；9.5（0.8）与 11.0（0.7）均被拒。对照：tol=0.75 会误纳 1.4 grid，tol=1.0 会误纳 1.6 grid——实现选的是**两个可辩护读法中更严的那个** |
| 5 | 是否仍要求正确 episode | ✅ `episode == expected_episode_alias` 在 usable 谓词里 |
| 6 | 跨 episode 同时间戳候选是否绝不算 usable | ✅ 探针验证：E002@10.5（时间距离仅 0.2，但 episode 错）→ `usable=False` |
| 7 | top-k semantics 是否与 strict 完全一致 | ✅ 两者都对**同一个全局排序列表** `ranked` 取 `min(ranks)` 再比 `<= k`。唯一差别是入选谓词的 tolerance |
| 8 | strict miss / usable hit 的分类是否互斥且完备 | ✅ 由于 `strict_tol (0.26) ⊂ usable_tol (0.5)`，合格帧集合是子集关系，故 `usable_rank <= strict_rank` 恒成立 ⇒ **strict hit ⟹ usable hit**。三类 `{strict hit, adjacent-only, true loss}` 互斥且和为 1 |
| 9 | `true_loss = 1 - usable_recall@8` 是否成立 | ✅ **恒等成立**。`true_loss = ¬strict ∧ ¬usable`，而 `¬usable ⟹ ¬strict`，故 `true_loss = ¬usable = 1 - usable_recall`。实测：direct usable 0.9942，true_loss 0.0058，和为 1.0000 |
| 10 | 上一轮 direct baseline ≈99.5% 是否复现 | ✅ 见下 |

## 上一轮 99.5% vs 本轮 99.42% 的差异（问题 11）

**完全解释，差异合理。**

上一轮我用的是 **tolerance 0.76 s** 的 probe（"±1 格"的宽松读法）；本实现用的是 **0.5 s**（严格读法：只接纳两个 bracketing frame）。我上一轮的 tolerance 扫描表里两个值都在：

```
tolerance    R@1      R@8      R@32     R@128
0.2501    81.00%   95.08%   95.33%   95.83%
0.26      81.83%   95.33%   95.58%   96.08%   <- strict 口径
0.50      91.67%   99.42%   99.50%   99.67%   <- 本实现的 usable 口径
0.76      92.08%   99.50%   99.67%   99.83%   <- 我上一轮引用的数
```

**`0.50 → 99.42%` 与本实现的 `direct usable R@8 = 0.9942` 逐位一致。** 差异来源是 tolerance 定义，不是浮点、不是 observation population、不是抽样。

**并且实现选的是更保守的那个。** 我上一轮引用 0.76 是探针方便，本实现用 0.5 在语义上更干净（"±1 grid step"应当指相邻的那一格，而不是"一格半"）。true loss 相应从 0.50% 变成 0.58%——**保守方向，可接受。**

## 是否存在"usable = strict + 事后常数"（问题 10）

**不存在。** 两者是两个独立重新聚合的二元变量：

```python
"hits":                 {str(k): correct_rank is not None and correct_rank <= k ...}
"alignment_usable_hits": {str(k): usable_rank  is not None and usable_rank  <= k ...}
```

`correct_rank` 与 `usable_rank` 分别由两次独立的列表推导在同一个 `ranked` 上以不同 tolerance 计算得出。聚合层 `aggregate_observations` 对两个 key 分别求和。**没有任何加法修正、没有任何常数偏移。**

---

# 10. Failure-Pair Analysis

## 配对结果（1200 个内容位置，每个含 4 个 profile）

**alignment-usable @8：**

```
DIRECT usable-fail 集合大小 = 7
profile  fails  D-fail→P-fail  D-pass→P-fail  D-fail→P-pass  Jaccard
p1           7              7              0              0    1.000
p2           8              7              1              0    0.875
p3           7              7              0              0    1.000
```

**strict @8：**

```
DIRECT strict-fail 集合大小 = 56
profile  fails  D-fail→P-fail  D-pass→P-fail  D-fail→P-pass  Jaccard
p1          56             56              0              0    1.000
p2          57             56              1              0    0.982
p3          56             56              0              0    1.000
```

**profile 两两 strict-fail Jaccard 矩阵：**

```
          direct       p1       p2       p3
direct     1.000    1.000    0.982    1.000
p1         1.000    1.000    0.982    1.000
p2         0.982    0.982    1.000    0.982
p3         1.000    1.000    0.982    1.000
```

## 判定

这**不是**"总 recall 恰好一样但失败样本发生了替换"。这是**字面意义上的同一批帧在失败**：

- p1 与 p3 的失败集合与 direct **完全相同**（Jaccard = 1.000，零新增失败、零修复）；
- p2 只多出 **1 个**新失败位置（Jaccard = 0.982）；
- **四个 profile 之间没有任何一个 "D-fail → P-pass" 的转换**——转码从未把一个失败的帧变成成功。

结合 §7 的 descriptor 扰动量级（1e-5 vs 3.7e-3 margin），整幅图像完全自洽：转码是真的、descriptor 确实动了、但动的幅度不足以改变任何排序决策。

**k=1 上确实有分化**（direct 0.8183 / p1 0.8167 / p2 0.8167 / p3 0.8150），因为 rank-1 是近似平局最密集的地方，1e-5 的扰动偶尔能翻转。这也正是 M-2 编码器噪声最容易显形的指标。

---

# 11. Anonymization Audit

## 两份 committed aggregate：干净

对 `phase2a-0-direct-baseline-v0.1.json` 与 `phase2a-0-platform-characterization-v0.2.json` 逐模式 grep：

```
/Users/    real_test    EP01    EP02    .mp4    sha256    sha-256
file_path  index_path   media_metadata      /private/    /tmp/    input/
                        -> 全部 0 命中
```

两份文件均**不含 `observations`**（逐帧观测留在 ignored output），只含 `E001`/`E002`、`E001-I017` 形式的别名、归一化 source position、benchmark config、聚合统计、CI 与 profile 名。

## 上下文是否足够（不能匿名到只剩数字）

**足够，而且这一点做得比上一轮好。** v0.1 baseline 文件专门补了：

```json
"metric_semantics": {
  "recall": "legacy strict source-grid recall with tolerance_seconds=0.26",
  "timestamp": "nominal_grid_time; authoritative media PTS was not captured",
  "inference": "legacy point estimates only; no cluster-bootstrap confidence interval"
},
"episode_scope_note": "episode_n=2; not a cross-episode stability estimate"
```

未来 reviewer 能从这两份文件里读出：口径定义、tolerance、时间戳语义、统计方法的局限、样本量与集数限制、每个 profile 的完整配置与 CI。**这足以理解 Owner 当时是在什么证据上做 DCT / DINOv2 裁定的。**

## 但 test 文件泄露了 aggregate 保护住的东西

**Critical C-1**：`tests/test_retrieval_benchmark.py` 明文包含 `real_test/EP01.mp4` 的真实 SHA-256 与含用户名的绝对路径，已进入 git 历史。

**匿名化管线本身是有效的；泄露发生在验证它有效的那个测试里。**

---

# 12. Test Quality Audit

`39 passed`。逐项对照要求的覆盖清单：

| 要求覆盖 | 是否覆盖 | 质量评价 |
|---|---|---|
| ±1 grid hit | ✅ | **真实**。用 one-hot 正交特征构造 3 帧索引，检索结果确定，断言 0.5 被接纳 |
| ±1 grid miss | ✅ | **真实**。同上，1.0 被拒 |
| wrong episode 不算 usable | ❌ **未覆盖** | 我用探针验证行为正确，但**没有测试保住它**。这是 usable 定义里最容易被后续改动破坏的一条 |
| bootstrap deterministic | ⚠️ 浅 | 只断言"同 seed 两次相等"。没有验证 resample 单元是 interval，也没有对已知分布验证覆盖率 |
| ABOVE / STRADDLES / BELOW | ⚠️ **近乎同义反复** | `assert _threshold_band(0.96,0.98)=="ABOVE"` 等三行。被测函数本身就是三个 if。**且完全没有测试 band 绑定的是哪个指标**——正是本次的实际缺陷 |
| profile isolation | ⚠️ 部分 | `test_profile_reports_do_not_double_count_independent_samples` 验证了 interval/position 计数不翻倍。但**没有测试 profile 的 query 文件路径不互相覆盖，也没有测试 descriptor 实际不同** |
| interval counts | ✅ | 结构性，合格 |
| independent sample semantics | ✅ | 合格 |
| actual decode off-grid | ✅ **本轮最有价值的测试** | 真的跑 ffmpeg，用 `geq=lum='mod(N*10,256)'` 给每帧唯一亮度签名，再反查解码帧对应的源时间。这直接补上了我上一轮点名的 m-3（此前只有 mock 测试）。**但断言窗口 `start-0.01 <= best_time <= start+0.5` 宽达 0.51 秒**——我实测过系统性内容偏移约 +0.23 s，这个窗口宽到即使偏移 0.4 秒也能通过。是真实测试，但不是紧的测试 |
| anonymization | ⚠️ 有效但方式错误 | 断言逻辑正确，**代价是把真值写进 git**（C-1）。用合成假值可获得完全相同的断言强度 |

## 低价值测试

- `test_threshold_band_three_states`：断言自己刚写的 3 分支函数在三个显然的输入上返回三个显然的值。**典型的"assert 自己的常量"**。
- `test_profile_block_reports_interval_count_and_effective_sample_size`：所有观测都构造为 `True`，方差为 0 ⇒ ICC = 0 ⇒ design effect = 1.0 ⇒ `effective_sample_size == query_frame_count`。断言写的是 `<=`，**以等号平凡通过，完全没有测到聚类修正**。

## 缺失的高价值覆盖

1. **band 与指标的绑定关系**（本次的实际缺陷，零测试）；
2. **wrong episode 不算 usable**；
3. **非退化 cluster 下的 ICC / design effect 数值**（构造已知 ICC 的数据，验证估计值）；
4. **transcode profile 实际产出规定的 codec / 分辨率 / 码率**（需要 ffprobe，可标 `skipif` 依赖编码器）；
5. **profile 间 query 文件不互相覆盖**（本 commit 刚修掉的覆盖 bug，没有回归测试保护）。

---

# 13. DCT / DINOv2 Recommendation

## 本轮新增的证据

1. **压缩与分辨率不是这个描述子的威胁向量**——而且余量极大。从 720p@2Mbps 一路压到 **180p@60kbps**，descriptor 扰动仍比判定裕度小 18 倍。
2. **失败集合在四个 profile 间基本恒等**（Jaccard 1.000 / 0.982 / 1.000），说明这些 profile 引入的变化没有改变任何一个排序决策。
3. **alignment-usable CI 下界 0.9883–0.9892**，比 0.95 阈值高约 4 个百分点，四档全部会判 `ABOVE`。
4. **true loss 0.58%–0.67%**（比上一轮 0.50% 略高，纯因为 usable tolerance 从 0.76 收紧到 0.5）。

## 结论：**没有任何新证据要求现在换 DINOv2**

而且本轮的证据把"现在不换"的理由变强了：先前我担心「真实平台转码噪声可能淹没 0.0037 的裕度」——**这个担心被实测证伪了**，扰动量级差 2–3 个数量级。

## 但边界必须说清楚

本轮**只测了** compression / resolution / codec。**完全未测**：crop、mirror、subtitle overlay、picture-in-picture、letterbox / 竖屏改版、摄像头翻拍、Reaction 画中画。

而这些恰恰是会**改变低频结构**的变换——也就是 12×12 低频 DCT 唯一保留的那部分信息。**它们才是这个描述子的真实威胁向量，而本轮的实验设计在原理上无法触及它们。**

因此 DINOv2 的决策点应当保持在原位：**Temporal Alignment v2 完成并单独测量之后**，依据的是几何/遮挡类变换的实测，而不是本轮的压缩类实测。ADR-0002 自己写的「不应与视觉 backend 切换同时实施，否则无法归因指标变化」在此依然适用。

**DCT 保留为 Layer 1 的理由再加一条**：本轮实测真实 1080p 冷索引 **49 秒/小时**（两集 78 分钟共 64 秒），且对压缩/分辨率近乎完全不变。这是一个成本极低、行为极稳定的 fast path。

---

# 14. Phase 2A Readiness

## 关闭 Phase 2A-0.1 / 0.2 的条件

**必须先做（Critical）：**

1. **C-1 的补救**：从 test 中移除真实 SHA-256 与绝对路径（换合成假值），并由 Architecture Owner 决定是否对 `8caf2d7` 做 history rewrite。若该仓库从未推送到任何远端，rewrite 成本很低；若推送过，需要评估远端历史与所有克隆。
2. **C-2 的修正**：Completion report 的 Summary table 把 `Usable 95% CI` 改为 `Strict R@8 95% CI`，或同时列出两组 CI 与两个 band。

**建议在进入 Temporal Alignment v2 之前做（Major，都不改测量逻辑）：**

3. **M-3**：加 `strict_threshold_band` + `alignment_usable_threshold_band`，弃用裸 `threshold_band`（两行代码 + schema v0.3）。
4. **M-2**：给转码加确定性编码参数，或在报告中标注 profile 指标的 run-to-run 噪声带（±0.42%）并停止陈述小于该带宽的差异。
5. **M-1**：在报告中补一句量化陈述——"p1/p2/p3 注入的 descriptor 扰动为 6e-6~1.3e-5，比 median score margin 小 280–600 倍"——让读者知道这个测试的功效边界在哪。

**可延后：** M-4（grid period 推导）、M-5（v0.1 baseline 的可复现性）、§12 的测试缺口。

## Phase 2A-0 整体是否可以关闭

**在 C-1 / C-2 处理后可以关闭。** 测量工具已经达到设计目标：口径双轨、聚类推断正确、平台档真实、匿名化有效、direct 完全可复现。剩下的都是文档表述与一次安全补救，不需要重做任何测量。

---

# 15. Q1–Q6 直答

## Q1　Phase 2A-0.1 / 0.2 是否实现正确，可以正式关闭？

**实现正确，但不能在当前状态下关闭。**

技术实现我逐项核验通过：transcode 真实且符合规格、alignment-usable 定义正确且选了更严的读法、cluster bootstrap 与 ICC 实现正确且我独立复算一致、scope 完全干净、匿名化管线有效。

阻断关闭的是两条与测量正确性无关的问题：**C-1（真实案件 SHA-256 进入 git 历史）**必须先补救，**C-2（交接文档的 band/CI 并列误导）**必须先修正——因为后者正是 Owner 用来做裁定的那张表。

## Q2　当前数据是否支持"在 clean + platform-like compression/resolution 条件下，DCT + 2 FPS retrieval 不是 Temporal Alignment v2 之前的首要 blocker"？

**支持，且证据比上一轮更强。**

依据：
- alignment-usable recall@8 的 cluster-bootstrap CI 下界 **0.9883–0.9892**，四档均高于 0.95 约 4 个百分点；
- true loss 仅 **0.58%–0.67%**；
- 四个 profile 的失败集合 **Jaccard 1.000 / 0.982 / 1.000** —— 压缩与分辨率没有改变任何排序决策；
- descriptor 扰动比判定裕度小 **280–600 倍**，即使压到 180p@60kbps 仍小 18 倍。

**严格限定在被测条件内**：仅 compression / resolution / codec。**不外推到** crop、mirror、subtitle、picture-in-picture、摄像头翻拍、Reaction——这些未测，且原理上属于会改变低频结构的另一类威胁。

同时保留上一轮的限定：strict recall@8 的 CI 仍跨越 0.95（`STRADDLES`），所以"retrieval 不是 blocker"的结论**必须建立在 usable 口径上**，不能建立在 strict 点估计上。

## Q3　是否存在任何证据要求在 Temporal Alignment v2 之前先切换 DINOv2？

**没有。** 而且本轮把"暂不切换"的理由变强了——我上一轮担心真实平台噪声会淹没 DCT 的 0.0037 裕度，这个担心被实测证伪（扰动量级差 2–3 个数量级）。

ADR-0002 的归因纪律依然适用：alignment 层有一个已定位的结构缺陷（constant-offset bucket），必须先单独修掉，才能看清 backend 的边际价值。

## Q4　P2（480p / ~1 Mbps）仍保持 usable R@8 ≈ 99.33% 的可信度如何？

**作为测量结果：高度可信。** ffprobe 实测该档确实是 h264 / 854×480 / 1059 kbps；descriptor 与 direct 不是逐位相同；失败集合与 direct 的 Jaccard 为 0.982（只多 1 个失败位置）；我的独立复现给出同一档 usable R@8 = 0.9933，与 committed 一致。

**作为robustness声明：必须窄化。** 它证明的是"480p / 1 Mbps 这一档的劣化，在进入 12×12 低频 DCT 之前就被 160×90 → 32×32 的降采样抹掉了"，而不是"DCT 对 480p 平台视频鲁棒"。我的加压实验（180p@60kbps 扰动仍比 margin 小 18 倍）说明：**这个测试对该描述子几乎没有统计功效——它在设计上就不可能失败。**

一个可信但低信息量的正面结果。

## Q5　Threshold band 应如何正式建模？

**建议：两套 band，并弃用裸字段。**

```
strict_threshold_band            <- 由 ci.strict["8"] 推出（定位精度口径）
alignment_usable_threshold_band  <- 由 ci.alignment_usable["8"] 推出（下游可用口径）
threshold_band                   <- 标记 deprecated 或直接移除
```

理由：
1. ADR-0005 已确立双口径但**未规定 band 绑定哪个**；Completion report §16 又把官方口径的裁定权留给 Owner。**在裁定前，单一无限定的 band 必然被某一方误读**——C-2 就是这个误读已经发生的证据。
2. 两个 CI 都已存在，成本是两行代码。
3. Gate 消费方读的是字段名。`threshold_band` 不携带指标信息；`alignment_usable_threshold_band` 携带。

配套建议：schema v0.3 把两个具名 band 设为 required，把裸 `threshold_band` 移出 required；并给 `_threshold_band(None, None)` 一个独立的 `UNKNOWN` 返回值，避免 bootstrap 失败时静默显示为 `STRADDLES`。

**不建议只保留 usable band**——strict 是定位精度的唯一指标，Temporal Alignment v2 的 8 FPS 精对齐能否改善边界精度，需要它作基线。

## Q6　只有两集真实原片时，是否可以开始 Temporal Alignment v2，把"扩到 ≥5 集"留到 DINOv2 backend Gate 前完成？

**可以。**

理由：
1. **TAv2 的验收不依赖集数**。它要修的是 constant-offset bucket 的结构缺陷，验收靠 synthetic A–M gate（速度容忍范围、piecewise 提取、相位鲁棒性）与 Phase 1.5 gate 的回归——这些都在合成语料上，与真实集数无关。
2. **集数真正影响的是 backend 决策**。跨集混淆度（片头/片尾/前情提要/同场景复用）是 episode 数量的函数，而那正是 DINOv2 gate 要回答的问题。两集根本无法估计集间方差——Completion report §15 与 ADR-0005 都已明确写下这一限制。
3. **ADR-0002 的归因纪律要求一次只改一层**。同时扩语料和改算法会让指标变化无法归因。

建议顺序保持：**TAv2（合成语料验收）→ 扩到 ≥5 集 → 几何/遮挡类变换的 retrieval 测量 → DINOv2 backend gate**。

---

# 16. Questions for Architecture Owner

**Q1（最高优先级，安全）　`8caf2d7` 是否需要 history rewrite？**

`tests/test_retrieval_benchmark.py` 已把 `real_test/EP01.mp4` 的真实 SHA-256 与含用户名的绝对路径写入不可变 git 历史。这违反 §54（案件材料不得进入 git repo / test fixtures）与 §41（SHA-256 是案件媒体的权威身份）。
请裁定：(a) 立即 history rewrite 并强推；(b) 仅在后续 commit 中替换为合成假值，接受历史中的残留；(c) 先确认该仓库是否曾推送到任何远端，再决定。
**在裁定前建议不要把该仓库推送到任何新的远端。**

**Q2　Threshold band 的正式建模（对应 Q5 的建议）**

是否采纳 `strict_threshold_band` + `alignment_usable_threshold_band` 双字段并弃用裸 `threshold_band`？若采纳，请同时裁定 ADR-0005 §16 悬置的问题：**retrieval 的官方口径是 strict 还是 alignment-usable？**——这决定 Gate 消费哪个字段。

**Q3　profile 指标的噪声带是否需要在报告中显式化？**

libx264/libx265 在本机非确定性，profile 指标有 ±5 帧/1200 ≈ ±0.42% 的 run-to-run 浮动。Completion report §7/§11 陈述了小于该带宽的 profile 差异。
请裁定：(a) 给转码加确定性编码参数（`-threads 1` + 编码器专用参数）以恢复逐位可复现；(b) 保留非确定性但在报告中标注噪声带并禁止陈述带内差异；(c) 每个 profile 跑 N 次取分布。
**我倾向 (a)**——它同时恢复了 committed aggregate 的可验证性。

**Q4　`phase2a-0-direct-baseline-v0.1.json` 的地位**

该文件是手工编纂的（顶层键与 `1dd337d` 代码输出不一致，含五个代码从不产出的字段）。它的内容质量很好，但不能由任何命令复现。
请裁定：(a) 标注为 editorial summary，明确它不是机器产物；(b) 用 `1dd337d` 的代码重新生成一份真产物并替换；(c) 直接删除，只保留 v0.2。

**Q5　ICC / effective sample size 的指标归属**

当前 profile 块里的 `intra_cluster_correlation` / `design_effect` / `effective_sample_size` 是裸字段，但只对 strict@8 估计。读者会自然地把它们套到 usable 上，而 usable 的方差结构不同（hit 率 0.994 vs 0.953）。
请裁定：(a) 改为 `{strict: ..., alignment_usable: ...}` 结构；(b) 重命名为 `strict_*`；(c) 维持现状但在 schema 描述中写明。

**Q6　下一轮 retrieval 测量的变换清单**

本轮已确证 compression / resolution / codec 在描述子灵敏度下限之下。真正的威胁向量（crop、mirror、subtitle overlay、picture-in-picture、letterbox / 竖屏改版）尚未测量，且它们是决定 DINOv2 的关键证据。
请裁定这份清单以及测量时点：**Temporal Alignment v2 之前**（可能拖慢 TAv2），还是**之后、DINOv2 gate 之前**（我倾向此项，与 Q6 的排序一致）。
