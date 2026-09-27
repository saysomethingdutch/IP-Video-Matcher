# WO-003b 会话语音转写（ASR）

- 签发：Owner　执行：**Codex**（做完 WO-004a-R1 之后）　时间盒：**1 个工作日**
- 分支：`pilot/wo-003b-asr`，从 `pilot/wo-001-ledger` 最新提交切出
- 代码目录：`pilot/asr/`
- 交付说明：`docs/workorders/WO-003b-RESULT.md`，不超过 1 页

## 1. 目标

把每一行在录像中实际播放的片段转成带时间的文字，并区分：
- **原剧对白**：能在原剧台词索引里匹配上的；
- **非原剧语音**：UP 主的解说、reaction 评论、配音等。

这是分类和写意见最重要的原料之一。

## 2. 输入（只读）

- 台账：`workspace/pilot/qsjx_e18/ledger.jsonl`，用其中的 `played_intervals` 和 `playback_map`。
- 录像：`/Volumes/Element/.../MVI_0001–0008.MP4` 的音轨（AAC 48k 双声道）。需要外置盘挂载。
- 模型：`models/whisper/ggml-large-v3.bin`，whisper.cpp 已安装。
- 台词索引：`pilot/textindex`（在 `pilot/wo-002a-textindex` 分支，合并进来即可），用 `Index.load` 和 `search` 或 `match_sequence`。

## 3. 要求

1. **沿用 3A-0 已经验证过的 ASR 配置**：
   - 按 40 秒分块，块间重叠 2 秒，每块独立调用；
   - 语言设为 zh；
   - **不用 initial prompt，不用 `-mc`、`-et`，不做 atempo 变速还原**。
   - 背景：录像音轨是用相机从手机外放录下来的，只有约 8.6 dB 余量。其他配置在真实音轨上会静默地产生幻觉或复读。
2. **时间轴**：每段转写先带录像秒，再用 `playback_map` 换算成被诉视频秒。倍速播放下语速是快的，这属于正常现象。
3. **分类**：每句去台词索引做模糊匹配，相似度 ≥0.6 即可，因为 ASR 文本和字幕文本的写法会有出入。
   - 能匹配上：`ORIGINAL`，附集数和原剧秒；
   - 匹配不上：`NON_ORIGINAL`。**不要默认它就是 UP 主本人说的**，只标 NON_ORIGINAL。
4. **幻觉检查**：同一句连续出现 3 次及以上，或出现"请不吝点赞订阅转发打赏"这类已知的训练语料残留，标记 `SUSPECT_HALLUCINATION`，不删除。
5. **语言**：jxd290 那批"泰配"视频是泰语配音。检测到非中文时，标出语言，保留原文，不翻译。
6. **自动估计准确率**：解说类视频里，UP 主的话通常也烧录成了字幕。用 WO-001 的 OCR 缓存（底部字幕区）作参照，计算 ASR 文本与字幕文本在对齐时间窗里的字级相似度，给出中位数和分布。这个指标不需要人工标注。

## 4. 输出（`workspace/pilot/qsjx_e18/asr/`）

- `<row_id>.json`：ASR 分段，每段含录像秒、被诉秒、文本、ORIGINAL/NON_ORIGINAL、匹配到的集数和原剧秒、可疑标记。
- `asr_summary.csv`：每行一条，列出转写秒数、NON_ORIGINAL 句数、ORIGINAL 句数、语言、可疑标记数。
- 总耗时，以及每小时音频的耗时。

## 5. 验收

- 169 行中，有播放片段的行全部产出转写，失败的行列出原因。
- 解说类视频"ASR 对字幕"的字级相似度中位数，由 Owner 看数字后判断是否可用。
- Owner 抽 10 行核对 ORIGINAL/NON_ORIGINAL 的划分。

## 6. 不做

不下载新模型（SenseVoice 等以后再比）；不调用云端；不做分类或意见；不改 `pilot/ledger/`、`pilot/textindex/`；不 push。
