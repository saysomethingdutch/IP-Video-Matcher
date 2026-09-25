# WO-004b 交付说明：事实汇总、性质分类与四格

分支 `pilot/wo-004b-classify`，从 8c8746b（WO-003a-R1）切出，合并 `pilot/wo-001-ledger` R2（5a40dd7）。代码在 `pilot/semantic/`，未改 ledger、textindex、content。worktree：`~/.claude/worktrees/wo-004b-classify/IP-Video-Matcher`。

## 1. 命令与输出

```bash
zsh -lic '/opt/homebrew/bin/python3 -m pilot.semantic run workspace/pilot/qsjx_e18 --backend deepseek --visual full --workers 4 --no-local'
zsh -lic '/opt/homebrew/bin/python3 -m pilot.semantic compare workspace/pilot/qsjx_e18 --backends deepseek,zhipu --workers 4 --no-local'
/opt/homebrew/bin/python3 -m pilot.semantic run workspace/pilot/qsjx_e18 --backend local --visual full --out workspace/pilot/qsjx_e18/semantic_local
/opt/homebrew/bin/python3 -m pilot.semantic compare workspace/pilot/qsjx_e18 --backends local
.venv/bin/python -m pytest -q pilot/semantic/tests      # 17 passed
```

- **交付版本** `qsjx_e18/semantic/`：
  - 169 个 `<row_id>.json`：RowDigest（每个值带来源）、各列取值与依据、分类、证据、flags；
  - `fields.csv`：表头同比对表，比对意见留空；
  - `consistency.csv`、`run_summary.json`；
  - 8 个核对页副本，每行加了"分类与四格"块。
- **其他版本**：
  - `semantic_r1/`：只用 R1 19 行画面事实的版本；
  - `semantic_local/`：本地模型跑通；
  - `workspace/pilot/semantic/`：§3.4b 对比。
- **开关**：
  - `--reuse`：只沿用"提示词里的事实没变、调用都成功"的行；
  - `--no-local`：不调用本机 Ollama，也不退回本地（D-026）。

## 2. 全量结果（交付版本：DeepSeek flash 只发文字，画面事实来自 `predictions_full.csv`）

- **169 行都有输出**：165 行分类；4 行重复取证（1116 同 1114，1204、1218、1219 保留原表写法）。310 次调用有效 JSON 100%。
- **性质**：reaction 65、混剪 63、不确定 21、观点评析 9、原片搬运 3、夹叙夹议解说 3、剧情解说 1。
- **四格**：跳播+Reaction类 63、跳播+混剪类 46、不确定 22、混剪类 16、观点评析类 9（跳播 8）、否/否 3、夹叙夹议 3（跳播 2）、Reaction类 2、剧情解说 1。
- **不确定 21 行**：
  - 11 行模型判为解说类，但可判断的 UP 主文字不到 5 句；
  - 5 行评论占比在 0.2–0.5 之间；
  - 5 行模型判不确定。
  - 另有 1 行混剪的再创作很少，四格也不自动判定。
- **与只用 19 行画面事实的版本相比，性质变了 22 行**：
  - 9 行短片从"不确定"改为混剪；
  - 泰配各行按"UP 主是否出镜"改判：5 行改为 reaction，2 行改为原片搬运。
- **flags**：黄 389 条，红 0 条。其中：
  - 集数：未能用台词核实 115、比声称窄 29、与声称冲突 20、标题/台标与声称不一致 5；
  - 无 ASR 87；四格不自动判定 22；同 BV 在本批外另有取证 20；
  - 播放量未读到 13、不一致 11；
  - 缺逐字引用 13、一致性告警 10；
  - 画面模型判断不确定 7、无画面事实 4、跳播未知 4、原片搬运待确认 3。
- **疑似故意跳播：0 行。** 规则只在"reaction + 跳播 + 点评不超过 2 句"时触发。泰配 reaction 的点评句数数不出来，所以不触发。
- **用时与费用**：
  - 模型调用每行约 31 s，4 路并行，墙钟 21 min；
  - token：输入 15.2 万，输出 101.9 万，其中思考 97.5 万；
  - 本轮按高峰价约 $1.27。今天 DeepSeek 各轮合计约 $4.5（估算），以账单为准。

## 3. 模型对比（§3.4b）

| 模型·任务 | 行 | 有效 JSON | 每行秒（中位/平均） | 每行 token 入/出（其中思考） | 每行费用 |
|---|---|---|---|---|---|
| DeepSeek 画面事实 | 19 | 100% | 19 / 20 | 0.9k / 3.4k（3.3k）* | $0.004* |
| DeepSeek 分类·一次调用 | 30 | 100% | 39 / 48 | 2.1k / 10.4k（9.9k） | $0.013 |
| DeepSeek 分类·两步 | 30 | 100% | 55 / 68 | 2.5k / 14.2k（13.6k） | $0.018 |
| GLM 画面事实 | 19 | 100% | 33 / 44 | 1.5k / 1.4k（1.2k）* | 待账单 |
| GLM 分类·一次调用 | 30 | 100% | 84 / 225 | 2.7k / 8.1k（7.4k） | 待账单 |
| GLM 分类·两步 | 30 | 99% | 176 / 307 | 3.1k / 10.1k（9.2k） | 待账单 |
| 本地 | | | （本地对照跑完后补） | | |

- \* 画面事实调用的 token 没有进日志（见 §4-2），按同一任务"看图"调用的平均值估算。画面事实的评分见 D-027。
- **失败原因全是网络**：本机代理 503 共 9 次、读超时 3 次、IncompleteRead 1 次。按行重试后只剩 GLM 1124 两步 1 次超时。模型输出本身的 JSON 全部有效。
- **30 行的性质**：4 种云端配置几乎一样。
  - 配置之间性质相同 25–29 行；同一模型一次调用与两步：DeepSeek 26 行相同、GLM 29 行相同；
  - 与 §2 全量的一致率 27–29/30。
- **本地跑通**（qwen3:4b，169 行）：
  - 有效 JSON 87.7%：38 次标句调用失败，句子多时给不出 JSON；
  - 每行 27 s，墙钟 76 min；
  - 与 DeepSeek 相比，性质相同 107 行、四格相同 104 行；本地判"不确定"的有 40 行。
- **拼图**：`cloud_outbox/` 30 张，每张只含播放器画面。两家合计发送约 150 次：计划 120 次，另有第一轮 9 行 24 次，以及网络失败后重发的约 10 次。

## 4. 偏差与说明

1. **本机模型的调用时间**：
   - 16:14 以前，我按工单"先本地跑通"跑的是本地 qwen3:4b，不是 key 缺失后的退回。16:15 又调了 1 次做排查，当时还没读到 D-026。
   - 此后到 Codex 看图跑完（20:39）都没有调用本机；20:55 以后跑了本地跑通和本地对照。
2. **对比第一轮有问题**：
   - "一次调用"的提示词写着"画面事实：无"，没有提示模型看图。已修正，分类两种方式都重跑了。第一轮 9 行的结果留在 `run1_partial/`。
   - 第一轮进程是我中止的，所以 token 没有记录。
3. **Codex R2 的两点**：
   - 1115、1133、1194、1197 报 `TypeError`，这 4 行的 seeks 是"未知"，和我遇到的是同一个问题，所以没有画面事实；
   - R2 的模型比例列已经换成几何比例（D-016），两者一致，不会再出现比例不一致的标记。
4. **Y 列**（纯切条类使用权利作品画面的时长）工单没有给规则：原片搬运填视频时长，其余填"/"。
5. **`semantic_local/` 的 Z、AB 两列没有刷新**：它们是修两处小问题之前的版本（"画面缩小为不确定"、真人出镜不确定时写成了"否"）。刷新会再调用本机模型，所以没刷。交付版本已修。
6. **`pilot/ledger` 有 3 个测试失败**：`.venv` 里没有 openpyxl，与本单无关。

## 5. 需要 Owner 决定

1. 原片搬运的四格按"否/否"；
2. Y 列规则；
3. 全量用哪个模型、一次调用还是两步。交付版本是"本地看图 + DeepSeek 文字分类"：价格已知，速度约为 GLM 的 2–3 倍；
4. 智谱价格页没有读到，GLM 费用请按账单核对。
