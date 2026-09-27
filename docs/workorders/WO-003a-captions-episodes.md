# WO-003a 录像字幕轨与集数证据

- 签发：Owner　执行：**Claude Code**（你做过 WO-002a，熟悉 `pilot/textindex` 的接口）　时间盒：**1 个工作日**
- 分支：`pilot/wo-003a-captions`，从 `pilot/wo-001-ledger` 最新提交切出，再把 `pilot/wo-002a-textindex` 合并进来
- 代码目录：`pilot/content/`。**不要改 `pilot/ledger/` 和 `pilot/textindex/`**；确实需要改接口的，写进交付说明，由 Owner 决定
- 交付说明：`docs/workorders/WO-003a-RESULT.md`，不超过 1 页
- 注意 token：只读本工单、`pilot/ledger/README.md` 和你自己写的 `pilot/textindex`，**不要读审计报告和其他工单**

## 1. 目标

对原告表格的每一行，从公证录像里抽出屏幕上的字幕，分成"原剧台词"和"非原剧文字"两类（后者包括 UP 主的解说字幕、歌词、花字等），再用原剧台词给出集数证据，并与原告声称的集数对比。

## 2. 输入（只读）

- 台账：`workspace/pilot/qsjx_e18/ledger.jsonl`（WO-001-R1）。每行有 `video`、`video_start/end`（录像秒）、`played_intervals`、`playback_map`（录像秒 → 被诉视频秒）、`plaintiff.part_claimed`（原告声称的集数）。
- 录像 OCR 缓存：`workspace/pilot/qsjx_e18/ocr_cache/MVI_000x-*.jsonl`，每秒一条，items 里有文本、置信度、归一化 bbox。**不需要重新跑 OCR。**
- 台词索引：`workspace/pilot/textindex/qsjx/index.sqlite`。接口用 `Index.load` 加 `match_sequence`。

## 3. 要求

**3.1 抽字幕事件**
- 只取会话内、横屏播放器画面里的文字。
- 排除三类：
  - **UI**：标题栏、时间读数、倍速、"发个友善的弹幕"、按钮等，按位置和关键词排除；
  - **弹幕**：画面上部、并且相邻几秒横向移动的文字；
  - **水印**：固定在角落的 UP 主名或 bilibili 字样。
- 相邻几秒相同或几乎相同的文字合并成一个事件：`(录像起止秒, 被诉视频秒, 文本, bbox)`。
- 被诉视频秒用 `playback_map` 换算。

**3.2 分轨**
- 每一行的事件按被诉视频时间排序后，送进 `match_sequence`：
  - `LINKED` → **原剧台词轨**，附集数和原剧秒；
  - `NO_MATCH` → **非原剧文字**；
  - `AMBIGUOUS` → 单独列出。
- 非原剧文字再按位置和行高粗分为：底部解说字幕、竖排或特效花字、其他。只需粗分。

**3.3 集数证据**
- 某一集有 ≥2 句 `LINKED` 台词，或有 1 句 ≥6 字、置信度 ≥0.8 的台词，才算"有证据的集数"。
- 前情提要按 Owner 裁定（D-011）处理：
  - 同一句同时出现在第 N 集正片和第 N+1 集开头的前情里，**记为第 N 集**；
  - 如果第 N 集本地缺失（缺第 20、21、22、26、29、34 集），而命中落在第 N+1 集的前情窗口里，记为**"疑似第 N 集（缺集，经第 N+1 集前情命中）"**。
- 与原告声称比较，声称值要能解析"1-38""12-14""13"这类写法；"25-22"这种无法解析的写法记为 `CLAIM_UNPARSEABLE`。结论分四类：

| 结论 | 含义 |
|---|---|
| `SUPPORTED` | 有证据的集数都在声称范围内，且与声称一致或声称只有一集 |
| `NARROWER` | 有证据的集数都在声称范围内，但声称范围更宽（例如声称 1-38，证据只有 EP11） |
| `CONTRADICTED` | 有证据的集数中，至少一集不在声称范围内 |
| `UNVERIFIED` | 没有台词证据，例如 BGM 混剪，或台词被 UP 主字幕盖住 |

- **措辞一律写"已取证部分显示……"**，因为录像里有跳播，只能说明看到的部分。

**3.4 输出**（`workspace/pilot/qsjx_e18/content/`）
- `<row_id>.json`：字幕事件（带分轨）、有证据的集数、声称比较结论、每条证据的录像秒和原剧秒。
- `content_summary.csv`：每行一条，列出结论、有证据的集数、原剧台词句数、非原剧文字句数。
- 如果能在不改 `pilot/ledger/` 的前提下做到，给现有核对页追加一个"字幕与集数"区块：原剧台词、UP 主文字分色显示，每句可以点开看录像秒和原剧秒。做不到就只输出 JSON 和 CSV，写进交付说明。

## 4. 验收（Owner 用独立诊断核对）

下面几行的结论必须符合，这些是 Owner 在审计时独立得出的：

| 行 | 期望 |
|---|---|
| 1152 | EP11，`NARROWER`（声称 1-38） |
| 1160 | EP03、EP04，`CLAIM_UNPARSEABLE`（声称"25-22"） |
| 1161 | 以 EP05 为主，另有 EP03、EP07，`CONTRADICTED`（声称 9-14） |
| 1163 | EP07、EP08，`CONTRADICTED`（声称 9-14） |
| 1144 | EP15、EP16 |
| 1145 | EP17（二者声称均为 15-22） |
| 1154、1155 | EP12 为主，`CONTRADICTED`（声称 13-14） |

其余行报告四类结论的分布即可。另报告两个比例：原剧台词句数占全部字幕事件的比例，以及非原剧文字里被误判成台词的样例（抽 10 条列出来）。

## 5. 不做

不做语音转写（ASR 另开 WO-003b）；不调用任何大模型；不做分类或意见；不改 `pilot/ledger/`、`pilot/textindex/`；不 push。
