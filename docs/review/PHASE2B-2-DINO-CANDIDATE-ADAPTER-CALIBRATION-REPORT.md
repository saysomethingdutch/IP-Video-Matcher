# Phase 2B-2 DINO candidate adapter calibration report

日期：2026-09-05。实验状态：CHARACTERIZATION COMPLETE。
建议 Architecture Owner 选择 **C：独立、窄范围的 score / ambiguity remediation**。
本报告不宣布 Phase 2B PASS，也不授权生产集成。

## 1. Executive Summary

候选能够接入冻结的 Temporal Alignment，但本轮选出的全局策略未达到生产集成的推荐条件。
预注册的 safety-first 字典序选中了 `FLOOR_0.70`：校准集 unsafe 从 DCT 的 28/110
降至 11/110，同时安全成功从 44/110 降至 27/110。held-out 的安全成功为 31/110，
DCT 为 44/110；unsafe 为 8/110，对照为 30/110。三项核心几何变换的安全成功均为
0/10，没有实现所要求的至少两项改善。

总 unsafe 的下降不能证明安全问题已解决。held-out 中有 6/110 个原本由 DCT 安全覆盖的
案例变成 unsafe，另有 1/110 个从 NO_MATCH 变成 unsafe。一个 DIRECT 案例把正确候选
支持从原始 top-8 的 15/16 帧压到有效门槛后的 1/16 帧，最终输出错误剧集、candidate GREEN。
所有 emitted segments 的 production review tier 仍为 REVIEW_REQUIRED。

RAW 诊断仅在校准集运行，安全成功为 69/110，unsafe 为 20/110，表现出更好的覆盖。
它没有赢得预注册的第一优先级，因此没有用 held-out 重新选择 RAW，也没有运行未选策略的
held-out 匹配。允许的 floor 家族只能抬高有效门槛；当前结果指向分数适配和竞争证据保留
问题，不能据此否定已验证的 DINO 检索能力。

## 2. Architecture scope

所有新代码位于 `experiments/phase2b2/`，配套测试位于 `tests/`。
没有修改 `src/ipvm/`、`config/`、DB schema、视觉 DCT、时序投票、RANSAC、提取、merge、
change point、rival、boundary completeness 或 evaluator。没有加入 ROI/tile、FAISS、
新模型、audio、ASR、Gemma、Excel 或 Phase 3。

## 3. Phase 2A frozen baseline

冻结生产基线为 `93bf13b`。开工 HEAD 为 `dccec2820750126b46baabcacb14ce0caf828c7b`，
其相对生产基线的 `src/ config/` diff 为空。运行入口再次验证该 diff；最终 scope audit
仍为空。全量初始测试为 137 passed；本轮最终为 157 passed。

## 4. Phase 2B-1 validated input

复用已审查的 corpus：2 个匿名 source，每个 10 个位置，每位置 11 种变换，220 个正样本；
另有 20 个确定性 synthetic negatives。source index 为 9,379 帧，DCT/DINO source alias
和 nominal timestamps 逐元素一致，全部落在 0.5 秒 grid。原媒体、采样位置和变换参数不变。

本轮重新解码已有 query 媒体并生成候选，以满足固定批处理要求。没有重采样位置，没有
重编码 query，没有更换 source index 或 DINO preprocessing。Phase 2B-1 的独立审查文档
只读使用，仍为用户拥有的未跟踪文件，未随本轮提交。

## 5. Protocol pre-registration commit

协议先行 commit：`20884b1e9ca5d81f35660bc84a7e1294897084a0`，只有
`experiments/phase2b2/PROTOCOL.md` 一个文件，没有结果。第一轮 calibration 在该 commit
存在之后才启动。上次未完成工作留下 adapter.py；benchmark.py 的 patch 曾失败，接管时
并不存在可运行 harness 或实验结果。

## 6. Candidate contract audit

输入 identity 为 `(accused_time, source_time, source_id, raw_cosine, rank)`。
adapter 无 GT 参数，仅做全局分数映射和 below-floor 删除。时间、episode 和 rank identity
保留在 ignored sidecar；生产 `CandidateMatch` 消费四个字段：被诉时间、source episode、
source 时间、visual score。rank 不参与冻结的 Temporal Alignment。

每 frame 原始 top-k=8。floor 删除后保留原 rank，不重新编号。输入按 accused time / 原 rank
规范化排序；候选源是 query embedding 与全 source embedding 矩阵的 exact NumPy cosine。
GT 仅在 alignment 完成后进入评价和 attribution。

## 7. Temporal Alignment score-use audit

分数同时用于绝对筛选和相对竞争。以下链路直接来自冻结 production code。

| 位置 | 分数作用 |
|---|---|
| CandidateRetriever / DCT | raw cosine 映射为 clip((cosine+1)/2,0,1) |
| align_with_diagnostics | visual_score < 0.82 的候选排除 |
| sparse peaks | visual_score × offset soft weight；一帧对一个 cell 至多一次投票 |
| support_for_peak | residual 优先，visual_score 作为极小 tie-break |
| robust fit | 几何 inlier、span、RMS 排序，不以视觉分数作 OLS 权重 |
| segment | inlier mean visual score；match_score 乘 coverage / temporal 因子 |
| explanation quality | visual_score × min(1, inlier_count/12)，约 6 秒支持饱和 |
| suppression / rivals | 以上 quality 排序；uniqueness margin=0.05 |
| candidate GREEN | 不直接阈值化 visual score；受竞争 margin 间接影响，另检查 RMS、IR、支持、coverage 与安全 flags |

floor 不是概率校准。它与固定 0.82 门槛组合后的 raw cosine cutoff 为：
RAW=0.820；f=.45/.50/.55/.60/.65/.70 分别为 .901/.910/.919/.928/.937/.946。
这个结构事实在看结果前已记录于 EXECUTION-NOTES.md。

## 8. Adapter implementation and fairness

DINO 调用现成 `TemporalAligner.align_with_diagnostics(..., index_complete=True)`；没有复制
时序算法。DCT 使用实际 `VideoMatcher.analyze_file(persist=False)`，其 source arrays 写成
生产格式 NPZ 并注册到独立 scratch DB。生产 probe、query decode、DCT encoder、
CandidateRetriever、score mapping、aligner 全部执行；capture wrapper 仅记录 retriever
原样返回。用户的 production DB 未被打开。

双方使用相同 query 媒体、2 FPS、top-k 8、GT、TemporalAligner 配置和 evaluator。
必要差别为 encoder/preprocessing/视觉分数：DCT 灰度 160×90 后采用原 descriptor；DINO
采用已验证 RGB 224×224、ImageNet normalization、CLS/384/L2。DCT 计时为完整
analyze_file，DINO 分开记录 decode/embed、retrieval 和 aligner；不能直接比较两者的
alignment-only 数字作为端到端性能。

## 9. Fixed DINO batch size

本轮配置 `DINO_BATCH_SIZE=16`；自然尾批不 padding。实际调用为 220 次 16 帧、20 次
4 帧、20 次 13 帧。0.8× query 为 16+4；1.25× 为 13。source index 原本采用同样的
16 帧上限与自然尾批，直接复用。

上一阶段 query helper 曾一次编码整条 clip，0.8× 用 20 帧。这与本轮固定批量要求冲突，
因此在任何校准结果出现前决定重新生成 query 候选。fresh 与旧候选比较：仅 SPEED_0_8X
改变，3,200 个按 rank 对齐的 candidate slots 中有 1,212 个 identity 不同，3,188 个
cosine 差超过 2e-5；其他组 identity 差异为零，cosine 差超过 2e-5 的数量为零。
这是已知 INT8 batch sensitivity，不能把本轮 speed 候选称为旧产物逐字节复用。

## 10. Calibration / evaluation split and metrics

每 source 的奇数 ordinal 1/3/5/7/9 进入校准，偶数 2/4/6/8/10 进入 held-out。
每侧独立 content position n=10、positive query n=110、negative n=10。一个位置的所有
变换不跨 split。两侧各 1,930 query frames，包含正样本 1,770 帧和 negatives 160 帧。
2 个 episodes 与 20 个内容位置不足以估计跨剧集总体稳定性；本报告不作 population CI。

四类互斥 taxonomy 沿用 Phase 2A：C=CORRECT_UNAMBIGUOUS_MATCH，
S=SAFE_AMBIGUITY_CONTAINS_GT，U=UNSAFE_WRONG_MATCH，N=NO_MATCH。
安全成功=C+S。AMBIGUOUS 即使 primary 正确也计 S。SAFE alternatives 使用原完整
mapping 检查；额外检测所有 SAFE 输出都存在满足该严格 helper 的 explanation，违规为零。

段 TP/FP/FN 使用冻结 evaluator 的一对一 assignment；alternatives 可使案件计 S，但不
冒充 primary TP。contained partial evidence 仅作为正交安全描述，不提高 recall。
所有时间均是 nominal grid time；不具备 authoritative media PTS 法律交付精修。

## 11. Calibration floor grid

七种策略为 RAW、0.45、0.50、0.55、0.60、0.65、0.70。没有增加 floor、连续优化或
transform/source 特例。对“materially increases”的运行前保守解释为：unsafe-positive
count 任何增加都不 eligible；negative 任何 emitted segment 都不 eligible，比仅拒绝
confident negatives 更严格。全部七策略满足这项相对 DCT 的 eligibility。

## 12. Calibration results by floor

下表 C/S/U/N 的总和逐行均为 110；negative 输出全部为 0/10。
retain/effective 分母均为 15,440 candidates，包含该 split 的正、负样本。
effective 表示最终通过 aligner 的 0.82 过滤。

| Policy | C / S / U / N | safe /110 | retained /15440 | effective /15440 | source MAE s | accused MAE s | AMBIGUOUS /110 |
|---|---:|---:|---:|---:|---:|---:|---:|
| DCT | 26 /18 /28 /38 | 44 | 15440 | 8746 | 55.3832 | .5109 | 32 |
| RAW | 46 /23 /20 /21 | 69 | 15440 | 8939 | .4038 | .2275 | 33 |
| .45 | 38 /6 /17 /49 | 44 | 15241 | 4457 | .5389 | .3803 | 13 |
| .50 | 39 /2 /15 /54 | 41 | 14360 | 3771 | .4834 | .3648 | 9 |
| .55 | 35 /2 /19 /54 | 37 | 13765 | 3139 | .5180 | .4269 | 9 |
| .60 | 31 /2 /16 /61 | 33 | 13199 | 2507 | .4570 | .3378 | 9 |
| .65 | 31 /0 /13 /66 | 31 | 12337 | 1971 | .4531 | .3641 | 8 |
| .70 | 27 /0 /11 /72 | 27 | 11606 | 1517 | .3834 | .3147 | 6 |

RAW 的覆盖明显较强；floor 越高，丢弃的有效证据越多。误配数并非严格单调，说明候选
提取与 rival 竞争也受影响。各 floor 的 retained/dropped cosine min/p05/median/p90/
p95/max、NOT_FOUND、segment-count distribution、完整逐变换结果均在匿名 aggregate
的 `calibration` 节点，未只保留赢家。

## 13. Selected adapter policy

选中 `FLOOR_0.70`，floor=0.70。它的 unsafe-positive count=11，是七种 eligible 策略中
最少；第一优先级已决定结果，不需要用覆盖或 MAE 破平局。它的 safe 27/110 低于 RAW 的
69/110，不是被遗漏，而是预注册字典序带来的已观察代价。没有为了更好的最终数字改选 RAW。

## 14. Selection-before-held-out evidence

| 事件 | UTC 时间 |
|---|---|
| Calibration completed | 2026-09-05 02:06:07.361503 |
| Selected policy serialized | 2026-09-05 02:06:57.752496 |
| Held-out completed | 2026-09-05 02:08:21.088308 |

ignored `selected-adapter.json` 由单独 freeze 命令以 exclusive create 写入，含 protocol
commit、校准摘要 digest、策略和执行代码/evaluator/config fingerprints。
held-out 启动核验这些 identity，并将 selection digest 写入结果。聚合时重新核验。
calibration 在该文件存在后拒绝重跑；已有 run result 拒绝覆盖。冻结后没有修改 adapter、
benchmark、reporting 或 production/evaluation code。新增的 verify/diagnostics 仅检查
现存输出，不执行未选择策略的 held-out matcher，也不参与选择。

## 15. Held-out DCT results

DCT：C/S/U/N=25/19/30/36，总计 110；safe=44/110。primary 段 TP/FP/FN=38/45/72，
precision=38/83，recall=38/110。案件安全成功多于 primary TP，是由于部分 S 的正确
explanation 在 alternatives 中。status 为 MATCH_FOUND 45/110、AMBIGUOUS 29/110、
MATCH_NOT_FOUND 36/110；INCOMPLETE_INDEX=0/110，ERROR=0/110。

## 16. Held-out selected DINO results

FLOOR_0.70：C/S/U/N=30/1/8/71，总计 110；safe=31/110。
primary 段 TP/FP/FN=31/10/79，precision=31/41，recall=31/110。
status 为 MATCH_FOUND 37/110、AMBIGUOUS 2/110、MATCH_NOT_FOUND 71/110；
INCOMPLETE_INDEX=0/110，ERROR=0/110。canonical status 沿用 MATCH_NOT_FOUND，
报告中的 NOT_FOUND/NO_MATCH 指相应空输出状态或 taxonomy，不改生产枚举。

## 17. Per-transform end-to-end matrix

每行每 backend 都是同一组 10 个 held-out positions；四元组为 C/S/U/N。
MAE 为 source / accused，单位秒；“—”表示无可评价 assignment，不表示零误差。

| Transform | DCT C/S/U/N | DINO .70 C/S/U/N | safe DCT→DINO /10 | DCT MAE | DINO MAE |
|---|---:|---:|---:|---:|---:|
| DIRECT | 6/3/1/0 | 8/0/1/1 | 9→8 | .2689/.1250 | .1161/.0278 |
| H264_REENCODE | 5/4/1/0 | 5/0/4/1 | 9→5 | .3020/.1500 | .3985/.4722 |
| MIRROR | 0/0/2/8 | 0/0/1/9 | 0→0 | —/— | 2.4755/2.0000 |
| CENTER_CROP | 0/0/2/8 | 0/0/0/10 | 0→0 | 28.1481/.7500 | —/— |
| VERTICAL_9_16 | 0/0/1/9 | 0/0/0/10 | 0→0 | —/— | —/— |
| ZOOM | 0/2/6/2 | 0/0/0/10 | 2→0 | 10.2603/.9643 | —/— |
| LETTERBOX | 0/0/6/4 | 0/0/0/10 | 0→0 | 225.4091/1.8750 | —/— |
| SUBTITLE_OVERLAY | 5/0/4/1 | 1/0/1/8 | 5→1 | .4615/.3438 | .8417/.8750 |
| PIP_REDUCED_VIEWPORT | 0/0/6/4 | 0/0/0/10 | 0→0 | 971.7611/.6875 | —/— |
| SPEED_0_8X | 1/8/1/0 | 8/0/1/1 | 9→8 | .2188/.1000 | .1186/.3056 |
| SPEED_1_25X | 8/2/0/0 | 8/1/0/1 | 10→9 | .1841/.0750 | .2054/.0833 |

H264 的 safe 从 9/10 降至 5/10，unsafe 从 1/10 升至 4/10。几何三项没有 segment-level
收益。尽管总体 unsafe 更少，这些分组事实足以阻止推荐当前策略生产集成。

校准侧同样保留逐变换结果；以下为各组 safe（分母均为 10）。

| Transform | DCT | RAW diagnostic | selected .70 |
|---|---:|---:|---:|
| DIRECT | 9 | 9 | 8 |
| H264_REENCODE | 9 | 9 | 7 |
| MIRROR | 0 | 9 | 0 |
| CENTER_CROP | 0 | 4 | 0 |
| VERTICAL_9_16 | 0 | 1 | 0 |
| ZOOM | 2 | 4 | 0 |
| LETTERBOX | 0 | 7 | 0 |
| SUBTITLE_OVERLAY | 7 | 6 | 0 |
| PIP_REDUCED_VIEWPORT | 0 | 0 | 0 |
| SPEED_0_8X | 8 | 10 | 5 |
| SPEED_1_25X | 9 | 10 | 7 |

RAW 的几何收益只说明校准侧 feed-through 可行，不能当作未运行的 RAW held-out 结论。

## 18. Taxonomy and position-level comparison

held-out 配对结果：DCT 的 30 个 U 中，27 个变 N、2 个变 C、1 个仍 U；原有 C/S 中有
6 个变 U，另有 1 个原 N 变 U。因此总数下降主要来自 abstention，不能解释成全部错误
都被纠正。下表按 position 统计 safe / unsafe，每个格的分母为 11 个配对变换。

| Held-out position | DCT safe / unsafe | selected safe / unsafe |
|---|---:|---:|
| SOURCE_A POSITION_002 | 5/11 ; 4/11 | 4/11 ; 0/11 |
| SOURCE_A POSITION_004 | 2/11 ; 4/11 | 0/11 ; 0/11 |
| SOURCE_A POSITION_006 | 5/11 ; 2/11 | 3/11 ; 1/11 |
| SOURCE_A POSITION_008 | 4/11 ; 4/11 | 3/11 ; 2/11 |
| SOURCE_A POSITION_010 | 5/11 ; 3/11 | 1/11 ; 3/11 |
| SOURCE_B POSITION_002 | 5/11 ; 0/11 | 3/11 ; 1/11 |
| SOURCE_B POSITION_004 | 4/11 ; 4/11 | 5/11 ; 1/11 |
| SOURCE_B POSITION_006 | 5/11 ; 2/11 | 4/11 ; 0/11 |
| SOURCE_B POSITION_008 | 3/11 ; 4/11 | 4/11 ; 0/11 |
| SOURCE_B POSITION_010 | 6/11 ; 3/11 | 4/11 ; 0/11 |

双方 all-11-transforms-safe 均为 0/10。完整 calibration/held-out position×transform
matrix 和 normalized positions 已入匿名 aggregate；110 个 cells 不能当作 110 个独立位置。

## 19. Ambiguity comparison

calibration DCT / RAW / selected 的 AMBIGUOUS 为 32/110、33/110、6/110；held-out
DCT / selected 为 29/110、2/110。歧义减少有两种不同后果：一些正确候选变成清晰的 C，
另一些正确 rival 被门槛削弱后产生错误确定输出。后者在 DIRECT 和 H264 上实际发生，
所以不能把低 ambiguity rate 直接当成优点。

## 20. Unsafe-wrong inspection

以下 8 个 held-out U 全部逐项检查。A/B 指 SOURCE_A/B，编号是匿名 position ordinal；
只记录误差和支持计数，未提交任何真实 source timestamp。

| Position / transform | DCT outcome | 主要问题 | raw GT hits→effective | selected tier |
|---|---|---|---:|---|
| A006 H264 | C | source end error 1.442s；被诉末尾少 1.5s | 16→9 /16 | YELLOW |
| A008 H264 | S | source start error 1.023s；拟合 slope 1.2184 偏离 GT 1 | 15→12 /16 | YELLOW |
| A008 SUBTITLE | U | 前边界 source error 2.085s；内部高一致但截断 | 13→10 /16 | GREEN / UNKNOWN |
| A010 DIRECT | S | 错误 episode；正确 rival 的有效支持不足 | 15→1 /16 | GREEN / UNKNOWN |
| A010 H264 | S | 错误 episode；正确候选全部被有效门槛排除 | 13→0 /16 | YELLOW / INCOMPLETE |
| A010 SPEED_0_8X | S | 错误 episode，2 个片段，AMBIGUOUS 但不含完整 GT | 18→11 /20 | YELLOW / INCOMPLETE |
| B002 H264 | C | contained partial；前端 source error 2.125s，明确 INCOMPLETE | 16→15 /16 | YELLOW |
| B004 MIRROR | N | 仅后部短段；source start error 4.412s，speed null | 16→10 /16 | YELLOW |

B002 H264 的 effective support 满足现有 full-boundary sufficiency 诊断，但提取仍只给
partial：这属于 frozen alignment 行为边界，不能全部归罪于检索。其余多例原始 top-8
支持较好、经过门槛后明显减少，说明 score adaptation 本身丢失了时间线证据。
U taxonomy 保留 B002 的功能性失败，同时正交标为 CONTAINED_PARTIAL_EVIDENCE，不伪造 recall。

## 21. Boundary MAE and tier semantics

| Split/backend | source MAE s | accused MAE s | GREEN / YELLOW | INCOMPLETE / UNKNOWN |
|---|---:|---:|---:|---:|
| Calibration DCT | 55.3832 | .5109 | 31 /50 | 1 /80 |
| Calibration RAW | .4038 | .2275 | 39 /52 | 2 /89 |
| Calibration .70 | .3834 | .3147 | 17 /23 | 2 /38 |
| Held-out DCT | 45.0831 | .3962 | 26 /57 | 1 /82 |
| Held-out .70 | .3076 | .3013 | 25 /16 | 4 /37 |

表中 tier/completeness 分母是 emitted primary segment 数，分别为 81/91/40/83/41。
FULL 一律为零；UNKNOWN 不代表完整。所有 primary review tier 均为 REVIEW_REQUIRED。

source MAE 由冻结 evaluator 只对已 assignment 的正确 episode 预测计算，包括错时间段，
排除错 episode，也没有为 NO_MATCH 插入虚构误差。selected calibration 的 source/accused
error sample n 为 66/68，held-out 为 72/78。因此 source MAE 的大幅下降不等价于整体
准确率上升：错误 episode 与弃答不在该 source MAE 分母中。端到端必须同时看 taxonomy。

低 RMS/高 IR 不能保障完整性：A008 SUBTITLE 的错误截断仍为 GREEN/UNKNOWN；A010
DIRECT 的错误 episode 也为 GREEN。candidate GREEN 只用于内部质量诊断。

## 22. Speed results

held-out 0.8× safe 为 8/10，DCT 为 9/10；1.25× 为 9/10，对照为 10/10。
selected speed MAE 分别为 .01730（n=8）、.03366（n=9），DCT 为 .04198（n=9）、
.02850（n=9）。calibration RAW 两组 safe 均 10/10，但 selected 只有 5/10、7/10。
这说明 speed fitting 在有支持时仍可工作，门槛导致的漏检/竞争损失使整体行为没有完全保住。
不扩大速度范围，不修改 out-of-range 规则。

## 23. Shared-opening and cross-episode competition

校准 SOURCE_A POSITION_001 出现跨 episode、同 source-time 邻域候选，符合上一轮已确认
的共享片头现象。RAW 在 DIRECT、两个 speed profiles 可保留含 GT 的 ambiguity；其余
profile 不一定包含完整 GT。selected 在这组 DIRECT/H264/两个 speed profiles 均为 U，
没有用 GT 排除另一 episode。

held-out A010 也存在强跨 episode 相干竞争。DIRECT 的 GT 支持由 15/16 降至 1/16 后，
错误 episode 获得 GREEN。这里不能仅凭候选推断它必定是共享片尾；可核验事实是分数筛选
删除了正确 rival 的支持并留下错误相干线。scratch 双强线探针会 AMBIGUOUS；真实候选
强度不对称、门槛之后的歧义保留仍有风险。共享内容本身不属于数据泄漏。

## 24. PIP analysis

held-out PIP 的 raw top-8 在 5/10 clips 有至少 6 个实际 GT-supporting frames 的连续支持，
只有 1/10 满足现有 full-boundary support 诊断。对未经 floor/0.82 过滤的候选运行冻结
sparse-peak/support 检查，5/10 clips 也存在偏离 GT 的有最低支持量的竞争 peak。
这里仅检查候选连续性，没有输出另一种 held-out matcher 或选择替代策略。

selected adapter 在 1,280 个 held-out PIP candidates 中保留 83 个，但有效门槛后为 0/1280。
因此 10/10 NO_MATCH、0/10 emitted、0 GREEN、没有 segment completeness 值；案件 review
tier 为原有空输出 RED。calibration selected 同样 10/10 NO_MATCH。PIP 在本策略下没有
虚假自信，但明显未解决。ROI/tile 仅可由未来单独授权实验研究，本轮未实施。

## 25. Negative end-to-end analysis

所有 calibration policies 的 synthetic negatives 均 0/10 emitted；held-out DCT 和
selected 同样 0/10 emitted，0 GREEN，全部 MATCH_NOT_FOUND。DCT held-out best
visual score max=.8152，低于 .82；selected 的负样本全部低于 floor，无 retained candidate，
其 best adapted score 为 null，不是零。support=0、emitted temporal coherence=0，
warnings 为空，source concentration 在无候选时定义为 0（不代表均匀分布）。

匿名 aggregate 的 negative_cases 逐 negative alias 记录 status、segment count、tiers、
best score、support、temporal score、source concentration 和 warnings。原始 audit 仍含
全部 nearest-neighbor 信息。negative 是有 source bias 的合成素材，这个结果不能估计
真实平台负样本误报率，也不能单独验证校准安全。

## 26. Runtime

calibration 120 clips、DCT+七种 DINO policy 共 960 个 backend/case 执行，用时 84.86s；
held-out 120 clips、DCT+selected 共 240 个执行，用时 71.18s。各 split 每条 query 只做
一次 DINO encoding/retrieval，calibration policy 重用其候选。

held-out DINO decode/embed median=.3899s、exact retrieval median=.0101s；DCT
完整 analyze median=.1468s。selected aligner median=.00013s，多数几何 query 在门槛处
即无有效候选，所以这个低值不能被称为更高效的完整匹配能力。setup（模型加载、scratch DB
注册、source hashing）不在上述主循环 runtime 中；source index construction 未重跑。

## 27. Invariants, anti-leak probes and tests

最终 full pytest：157 passed in 7.62s。20 项新测试覆盖：identity/time/rank、RAW/floor/clipping、
below-floor removal、固定 batch metadata、source-balanced grouped split、held-out rejection、
deterministic selection、taxonomy、negative semantics、sanitization、protocol-only commit、
重复/置换、band removal、coherent rivals、out-of-range、boundary incomplete 和 cap。

`verify.py` 不调用被测 taxonomy 函数，独立按冻结 single-GT eligibility 和完整 alternative
规则重算 1,100 个 positive backend/case/policy records，差异为零；这些不是 1,100 个
独立内容位置。审计含 policy replay 的 154,400 个 candidates，non-grid=0。
提交前重新从既有 raw output 计算三个匿名 JSON，序列化后的内容全部一致；再次核验
calibration、selection、held-out 与当前执行代码 fingerprints 一致。没有重跑校准或评估。

真实 held-out candidate probe：重复输入一致，直接倒序 CandidateMatch 后输出一致；
修改 frame 附带 GT 字段并置换 frames/ranks 后 adapter stream 与 alignment 一致。
删除前部 6 个 query frames 的候选，confirmed span 从 8s 缩到 5s。
scratch 两条强跨 source 线产生 ambiguity、1 alternative、0 GREEN；单独一条相干错误
source 线仍产生 1 segment、1 GREEN、REVIEW_REQUIRED。这是 temporal coherence 不能
独立证明视觉真实性的既有限制。测试没有为通过探针修改 production。

## 28. Known limitations and architecture questions

仅两个 episodes、每侧 10 个独立 positions；全部变换高度 paired。negative 合成且有偏。
2 FPS nominal-grid timing 未精修到 media PTS。source 索引与模型复用已验证产物；运行
identity 绑定了执行代码与策略，但没有在 selected 文件中单独绑定所有输入媒体/index/model
的内容 hash，因此复跑仍需保持这些输入固定。这是 harness provenance 的剩余增强项。

选择规则在有限网格上优先最小化 unsafe 数，会奖励大量 abstention；这已真实发生。
RAW 不是未校准概率；floor 家族不能降低 RAW 的 .82 有效门槛，因此不能证明所有全局
adapter 都失败，也不能证明需要 transformation-specific calibration。当前不改目标、
阈值或安全定义，任何后续 remediation 必须另行预注册。

### Required Q1–Q14

| 问题 | 回答 |
|---|---|
| Q1 能否接入冻结 Temporal Alignment？ | 能，直接构造 CandidateMatch 调用已有 entry point；production diff 为空。 |
| Q2 RAW 是否足够，是否需要非平凡 floor？ | RAW 校准侧 69/110 safe、20/110 U，比 selected 覆盖好；无法据此称其安全充分，也没有证据证明较高 floor 能兼顾安全和覆盖。 |
| Q3 选中哪个 floor，为什么？ | .70；eligible 中 unsafe count=11 最少，按预注册第一优先级胜出。 |
| Q4 held-out DIRECT/H264 是否退化？ | 是，safe 9→8/10 与 9→5/10；H264 U 1→4/10，DIRECT 出现新的错源 GREEN。 |
| Q5 held-out MIRROR 是否改善？ | 否，safe 均 0/10；selected 另有一个错误短段。 |
| Q6 held-out CENTER_CROP 是否改善？ | 否，safe 均 0/10，selected 全 NO_MATCH。 |
| Q7 held-out VERTICAL_9_16 是否改善？ | 否，safe 均 0/10，selected 全 NO_MATCH。 |
| Q8 两档 speed 是否仍强？ | selected safe 8/10、9/10，较 DCT 各少一例；有支持时 slope MAE 较低，但端到端未完全保住，不能无条件答强。 |
| Q9 shared opening ambiguity 怎样？ | 校准 RAW 部分保留含 GT alternatives；selected 仍有不含 GT 的 ambiguity。held-out 跨集竞争出现正确 rival 被门槛削弱、错源 GREEN。 |
| Q10 selected 是否增加 U？ | 总数从 30/110 降至 8/110；但新增 7 个配对 U，其中 6 个原安全案例。必须同时报告。 |
| Q11 held-out negatives 是否产生相干误配？ | 否，DCT/selected 均 0/10 输出；只对这组合成负样本成立。 |
| Q12 PIP 是 safely weak 还是 dangerously confident？ | 本策略下 safely weak：10/10 NO_MATCH、0 GREEN；原因是有效候选为零，不代表 PIP 已解决。 |
| Q13 一个全局 adapter 能否工作，是否必须分变换？ | 当前选中策略不满足要求；校准 RAW 的跨多组收益说明未必必须分变换。本阶段未测试或授权分变换校准。 |
| Q14 建议 A/B/C/D？ | C。缩小后续问题到全局 score 与固定 .82 门槛的交互、竞争证据保留和 abstention/unsafe 的选择口径。B 不满足；A 过度否定已有 RAW 校准证据；D 可保留为以后 PIP 分支但不是本轮全局成功声明。 |

## 29. Privacy, artifacts and stopping point

Git 仅提交通用实验代码、测试、Markdown 文档和三个匿名 JSON：主 aggregate、verification、
descriptive diagnostics。JSON 只含匿名 aliases、normalized positions、指标/误差统计、
配置和流程时间，不含原片名称、路径、私有 hash、exact source/query timelines、frames、
embeddings 或 raw candidate stream。运行结果、scratch DB 和 selected 文件均在 ignored
workspace。保留用户原有 .DS_Store 修改和三份未跟踪 review 文档，未暂存。

本轮匿名主结果：`benchmark/visual/results/phase2b2-dino-adapter-v0.1.json`；
独立复算：`benchmark/visual/results/phase2b2-verification-v0.1.json`；
失败诊断：`benchmark/visual/results/phase2b2-diagnostics-v0.1.json`。
运行方法见 `experiments/phase2b2/README.md`。

预注册 commit 已明确列于本报告；最终 experiment commit 由包含本文件的 Git commit 与
交付消息标识，避免自包含 hash 循环。STAGE 7 完成后进入 STAGE 8 STOP，等待独立审查
与 Architecture Owner 裁定。没有生产集成，没有进入下一阶段。
