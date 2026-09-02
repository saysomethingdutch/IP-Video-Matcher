# 影视作品侵权视频自动比对系统
## 技术交接与实施规范（Architecture Baseline v0.1）

**项目代号：IP-Video-Matcher**

**目标平台：Apple Silicon Mac（当前目标设备：Mac M3）**

**系统性质：本地优先、批量处理、可审计的视频证据辅助比对系统**

**架构负责人：ChatGPT / GPT-5.6 Sol**

**执行 Agent：Claude、Codex**

---

# 0. 执行纪律

本文件是当前版本的架构基线。

Claude / Codex 在执行过程中：

1. 可以自行解决具体代码实现问题；
2. 可以修复 bug、优化性能、增加测试；
3. 不得未经确认修改核心数据结构、模块职责、流水线顺序和判定规则；
4. 如发现架构存在明显问题，应记录：
   - 当前问题；
   - 复现方式；
   - 建议方案；
   - 对其他模块的影响；
   
   然后停止对相关架构部分的自行扩展，提交给架构负责人重新决策。
5. 不得为了“先跑起来”把多个模块写成一个巨型脚本；
6. 不得在 V1 阶段引入不必要的云服务、数据库服务器、Docker 集群、前端框架或微服务。
7. 首要原则是：
   
   **先证明视频时间轴匹配可靠，再开发 AI 法律语义层。**

---

# 1. 项目背景

项目用于律所知识产权业务中的影视作品视频比对。

典型场景：

- 权利作品为电视剧、综艺等长视频；
- 被诉视频来自 B站、小红书等平台；
- 被诉视频可能对权利作品进行：
  - 截取；
  - 多段拼接；
  - 镜像；
  - 加速/减速；
  - 裁剪；
  - 横屏转竖屏；
  - 缩放；
  - 字幕覆盖；
  - 表情包/图片遮挡；
  - Reaction；
  - 解说；
  - 混剪；
  - 插入其他影视作品；
  - 加入 UP 主原创内容。

现有人工工作包括：

- 判断取证是否完整；
- 确认被诉视频实际时长；
- 确认取证时长；
- 查找引用原作的具体集数；
- 判断使用原作画面的时间长度；
- 判断视频发生了什么改变；
- 分类 Reaction / 观点评析 / 夹叙夹议 / 剧情解说 / 混剪等；
- 按项目内部规则判断合理使用可能性；
- 撰写比对意见；
- 回填 Excel。

项目要求明确区分视频实际时长与取证时长，并处理倍速、拖曳跳播以及存在完整下载件时优先使用下载件等情况。

项目还要求识别合集/分P、非权利作品、遮挡物、画面缩放或虚化、对应剧集等信息。

---

# 2. V1 总目标

实现：

> **输入：原剧全集 + 被诉视频 + 取证视频 + 原始比对表 Excel**

> **输出：自动填写完成的 Excel + 每条视频可追溯的机器分析结果 + 低置信度人工复核列表**

目标不是简单生成一段 AI 意见，而是建立：

**Evidence → Matching → Facts → Classification → Rules → Report**

完整证据链。

---

# 3. V1 非目标

以下内容暂不开发：

1. 百度网盘自动登录/下载；
2. 区块链取证网站自动登录；
3. 验证码处理；
4. B站/小红书自动账号登录；
5. 浏览器自动取证；
6. 自动提交律所共享表格；
7. Gemma 微调；
8. 云端推理；
9. 多用户服务器；
10. 手机 App；
11. 正式法律结论自动替代律师判断。

V1 默认：

**所有需要分析的视频已经下载到本地。**

网页取证自动化属于后续独立模块。

---

# 4. 核心设计原则

## 4.1 Deterministic First, LLM Last

能够用确定性算法计算的数据，不允许交给大模型猜。

例如：

- 时长；
- 集数；
- 时间轴；
- 引用秒数；
- 速度倍率；
- 镜像；
- 重复片段；
- 引用比例；

必须由程序计算。

Gemma 负责：

- 内容理解；
- UP主观点提取；
- Reaction / 解说 / 混剪分类；
- 转换性事实提取；
- 根据已经确认的事实生成文字意见。

---

## 4.2 所有结论必须可追溯

禁止只保存：

```json
{
  "episode": 17
}
```

必须保存证据：

```json
{
  "accused_start": 31.2,
  "accused_end": 47.8,
  "source_episode": 17,
  "source_start": 1102.7,
  "source_end": 1122.4,
  "visual_score": 0.97,
  "audio_score": 0.99,
  "alignment_score": 0.98,
  "speed_ratio": 1.25,
  "mirrored": true
}
```

任何 Excel 字段原则上都应可以反查来源。

---

## 4.3 原始媒体只读

系统不得修改：

```text
input/
```

中的任何原始文件。

所有中间结果放：

```text
workspace/
```

所有最终结果放：

```text
output/
```

---

## 4.4 本地优先

案件视频原则上不离开用户 Mac。

Gemma 使用本地模型。

Google 当前将 Gemma 4 12B 定位为可在约 16GB 统一内存/VRAM 级设备本地运行的多模态模型，并提供 macOS / 本地推理支持。

但模型调用必须封装 Adapter，不允许业务代码直接依赖具体运行时。

---

# 5. 项目目录规范

```text
IP-Video-Matcher/
│
├── input/
│   ├── originals/
│   │   ├── EP01.mp4
│   │   ├── EP02.mp4
│   │   └── ...
│   │
│   ├── cases/
│   │   ├── CASE_0001/
│   │   │   ├── accused.mp4
│   │   │   ├── evidence.mp4
│   │   │   └── metadata.json
│   │   ├── CASE_0002/
│   │   └── ...
│   │
│   └── comparison.xlsx
│
├── workspace/
│   ├── media/
│   ├── frames/
│   ├── audio/
│   ├── embeddings/
│   ├── transcripts/
│   ├── indexes/
│   └── db/
│
├── output/
│   ├── comparison_result.xlsx
│   ├── results/
│   ├── review/
│   ├── reports/
│   └── logs/
│
├── src/
├── tests/
├── benchmark/
├── config/
└── README.md
```

---

# 6. 总体流水线

```text
                    原剧全集
                        │
                        ▼
                [A] Original Indexer
                        │
                        ▼
                   原作索引库
                        │
                        │
被诉视频 ─→ [B] Media Analyzer
                        │
                        ▼
                [C] Candidate Retrieval
                        │
                        ▼
                [D] Temporal Alignment
                        │
                        ▼
                 原作使用时间轴
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
[E] Transformation [F] Audio/ASR   [G] Content Segmentation
        │               │               │
        └───────────────┼───────────────┘
                        ▼
                [H] Semantic Analyzer
                    Gemma 4
                        │
                        ▼
                 [I] Rule Engine
                        │
                        ▼
                  [J] QA Validator
                        │
             ┌──────────┴───────────┐
             ▼                      ▼
         高置信度                低置信度
             │                      │
             ▼                      ▼
       自动写入 Excel          Review Queue
```

---

# 7. 模块 A：原作索引 Original Indexer

这是整个项目最重要的基础设施之一。

电视剧全集只建立一次索引。

## 7.1 Media Probe

使用：

```text
ffprobe
```

提取：

- duration；
- FPS；
- resolution；
- codec；
- audio codec；
- sample rate；
- rotation；
- creation metadata。

输出：

```text
media_metadata.json
```

---

# 8. 视频索引策略

不得只依赖一种匹配算法。

使用多层索引：

## Layer 1：Perceptual Hash

建议：

- pHash；
- dHash 可选。

用途：

- 快速候选召回；
- 重编码；
- 分辨率变化；
- 轻微字幕。

---

## Layer 2：Visual Embedding

建议初版：

**SigLIP 或 DINOv2**

选择一种先实现，不要同时维护多个 embedding backend。

接口统一：

```python
encode_images(images) -> ndarray[N, D]
```

存储：

```text
episode
timestamp
embedding
```

索引使用：

**FAISS**

---

## Layer 3：Audio Fingerprint

对原剧建立音频特征。

目的：

- 画面变化严重但原声保留时提供强匹配；
- 校正视觉时间轴；
- 提高秒级定位精度。

具体实现方案允许 Codex 做技术评估。

要求 API：

```python
search_audio(query_audio) -> list[AudioMatch]
```

不要让业务层依赖具体 fingerprint 实现。

---

## Layer 4：ASR Transcript

使用 Apple Silicon 本地 ASR。

优先考虑：

```text
MLX Whisper
或
whisper.cpp
```

输出：

```json
{
  "episode": 17,
  "segments": [
    {
      "start": 1421.10,
      "end": 1424.43,
      "text": "……"
    }
  ]
}
```

ASR 用于辅助召回，不作为唯一证据。

---

# 9. 抽帧策略

不要固定“一秒一帧”。

采用两级策略。

## 粗检索

默认：

```text
2 FPS
```

即：

```text
每 0.5 秒一帧
```

用于全集索引和候选召回。

---

## 精对齐

找到候选区域后，对候选区间重新按：

```text
8 FPS
```

抽帧。

必要时：

```text
12 FPS
```

只允许发生在较短候选区间。

禁止整季电视剧全部按 8–12 FPS 建立高维索引。

---

# 10. 模块 C：Candidate Retrieval

给定被诉视频：

```text
accused.mp4
```

先按 2 FPS 提取视觉 embedding。

对每帧在 FAISS 中搜索：

```text
Top K = 5~10
```

取得：

```text
episode
source_timestamp
distance
```

单帧命中不代表成立。

必须继续进行时间聚类。

---

# 11. 模块 D：Temporal Alignment

这是 V1 的技术核心。

目标不是：

> 找相似图片。

而是：

> 找到连续视频片段的时间映射关系。

正常映射近似为：

```text
source_time = a × accused_time + b
```

其中：

```text
a ≈ 视频速度倍率
b ≈ 原作起点偏移
```

例如：

```text
a = 1.25
```

意味着被诉视频可能进行了约：

```text
1.25×
```

速度处理。

推荐：

**RANSAC**

先处理大量 noisy frame matches。

然后把连续映射拆成：

```text
SegmentMatch[]
```

例如：

```json
[
  {
    "accused_start": 13.1,
    "accused_end": 31.4,
    "episode": 12,
    "source_start": 1122.4,
    "source_end": 1144.9,
    "speed_ratio": 1.23
  },
  {
    "accused_start": 38.2,
    "accused_end": 54.7,
    "episode": 7,
    "source_start": 411.0,
    "source_end": 429.3,
    "speed_ratio": 1.11
  }
]
```

每个 segment 独立拟合。

---

# 12. Piecewise Alignment

必须允许：

```text
电视剧片段
→ UP主讲话
→ 电视剧片段
→ 表情包
→ 另一集电视剧
```

因此绝不允许假设：

> 一个被诉视频只对应原剧一个连续区间。

算法必须进行 piecewise segmentation。

---

# 13. 镜像检测

对于候选帧：

同时比较：

```text
original
horizontal_flip(original)
```

若翻转匹配显著优于原始匹配：

```json
{
  "mirrored": true
}
```

不要为整个索引永久保存两套高维 embedding，除非基准测试证明有明显性能优势。

优先考虑候选阶段进行二次验证。

---

# 14. Crop / Resize 检测

在候选原图已经确定之后，再进行空间验证。

可使用：

- local feature matching；
- homography；
- multi-crop similarity。

输出至少：

```json
{
  "cropped": true,
  "estimated_visible_ratio": 0.54,
  "resized": true
}
```

第一版不要求像素级重建。

只需满足 Excel 需要：

```text
否
是，画面缩小
是，画面放大并裁剪
```

等事实描述。

---

# 15. Synthetic Benchmark

**在真实项目开发之前必须建立。**

从原剧中随机抽取已知区间。

例如：

```text
EP03
10:00–10:30
```

自动用 FFmpeg 制作测试视频：

### Case A
直接剪切。

### Case B
水平镜像。

### Case C
1.25× 加速。

### Case D
0.8× 减速。

### Case E
中心裁剪。

### Case F
竖屏裁剪。

### Case G
加字幕。

### Case H
镜像 + 加速 + 字幕。

### Case I
多个不同集数拼接。

### Case J
电视剧片段之间插入无关画面。

ground truth 已知。

因此可以自动计算准确率。

---

# 16. P1 最低验收目标

对长度不少于 **3 秒** 的原作片段：

在常见：

- 重编码；
- 镜像；
- 0.75–1.5×速度变化；
- 常规字幕；
- 常规裁剪；

条件下，阶段目标：

```text
Segment Recall ≥ 95%
Episode Accuracy ≥ 98%
```

精对齐后：

```text
时间边界 MAE ≤ 1 秒
```

这些是工程目标，不是先验保证值。

如果达不到：

**不得继续开发 Gemma 合理使用分析模块。**

先解决匹配引擎。

---

# 17. 模块 B：取证完整性分析

需要区分：

```text
video_duration
evidence_duration
captured_content_duration
```

三者不能混淆。

项目规则要求，在存在倍速播放时按实际被播放的视频内容长度还原；如发生拖曳，则按实际播放范围计算。若另有完整下载件并且完播，应优先采用下载件。

---

# 18. Evidence Timeline

对：

```text
evidence.mp4
```

建立：

```json
[
  {
    "content_start": 0,
    "content_end": 82
  },
  {
    "content_start": 234,
    "content_end": 381
  }
]
```

进而得到：

```text
完整播放：否
```

和：

```text
取证时长
```

V1 首先允许使用视频内容自身匹配重建播放区间。

播放器 OCR 只作为辅助。

不要把 OCR 作为核心依赖。

---

# 19. 分P / 合集

项目要求：

- 分P：各 P BV号相同；
- 合集：视频 BV号不同；
- 应写明总数量以及实际取证序号。

V1：

如果：

```text
metadata.json
```

提供了：

```json
{
  "platform": "bilibili",
  "bvid": "...",
  "collection": {...},
  "parts": [...]
}
```

系统自动处理。

如果 metadata 不存在：

```text
status = NEED_METADATA
```

不要为了这一步引入网页爬虫。

---

# 20. Content Segmentation

把被诉视频切分成：

```text
RIGHTS_WORK
CREATOR_FACE
CREATOR_VOICE
OTHER_VIDEO
IMAGE_MEME
TEXT_ONLY
ADVERTISEMENT
UNKNOWN
```

注意：

**ADVERTISEMENT 可以识别，但按本项目要求不得计入“非权利作品”字段。**

项目原始要求明确：“商单广告不要写”。

---

# 21. 遮挡物

需要识别：

- 字幕；
- 表情包；
- 网络图片；
- 其他明显覆盖物。

但项目要求不要填写：

- UP 主水印；
- bilibili 平台水印。

因此：

模型负责识别。

Rule Engine 负责过滤。

---

# 22. Gemma 模块定位

V1 默认：

**Gemma 4 12B**

前提：

设备内存满足实际测试需要。

低内存 fallback：

**Gemma 4 E4B**

模型层必须定义：

```python
class MultimodalLLM:
    def analyze(
        self,
        images,
        transcript,
        structured_facts,
        task
    ) -> dict:
        ...
```

不得在业务层调用：

```text
mlx
LiteRT
llama.cpp
transformers
```

具体 API。

---

# 23. Gemma 输入原则

不要：

```text
把完整20分钟视频交给Gemma
```

而是先由程序生成：

```text
视频统计
+
抽样关键帧
+
匹配原片关键帧
+
ASR
+
时间轴
```

再调用 Gemma。

例如：

```json
{
  "video_duration": 193,
  "rights_work_duration": 67,
  "creator_speech_duration": 103,
  "matched_episodes": [3, 7],
  "longest_continuous_source_clip": 11.3
}
```

同时提供代表性关键帧和 transcript。

---

# 24. 语义分类 Schema

Gemma 必须只输出 JSON。

禁止自由文本作为系统事实来源。

Schema：

```json
{
  "primary_type": "",
  "secondary_type": "",
  "creator_viewpoints": [],
  "transformative_elements": [],
  "plot_reproduction_level": "",
  "commentary_level": "",
  "replacement_risk": "",
  "confidence": 0.0,
  "evidence": []
}
```

`primary_type` 枚举：

```text
REACTION
COMMENTARY_ANALYSIS
NARRATION_COMMENTARY
PLOT_NARRATION
MASHUP
OTHER
UNKNOWN
```

---

# 25. 项目类型映射

项目现有分类包含：

- 观点评析；
- 深度解读；
- 拉片解读；
- 细节分析；
- 人物小传；
- 服化道盘点；
- 文化科普；
- 恶搞讽刺；
- 混剪；
- 人物向；
- CP向；
- 恶搞向；
- 夹叙夹议；
- 剧情解说；
- Reaction。

内部英文 enum 与最终 Excel 中文字段必须分离。

---

# 26. Reasoning 与 Rule Engine 分离

Gemma 不直接输出：

```text
构成合理使用
```

Gemma输出事实。

例如：

```json
{
  "primary_type": "NARRATION_COMMENTARY",
  "commentary_level": "MEDIUM",
  "plot_reproduction_level": "MEDIUM",
  "transformative_elements": [
    "评价角色行为",
    "分析剧情逻辑"
  ]
}
```

然后：

**Rule Engine**

根据本项目既定工作口径映射。

---

# 27. 当前项目规则

现有要求明确：

```text
观点评析
    ↓
夹叙夹议
    ↓
剧情解说
```

转换性程度依次降低。

其中：

- 剧情类解说：项目口径认为不构成合理使用；
- 夹叙夹议：项目口径认为达到合理使用最低门槛；
- 观点评析：进一步细分类型。

该规则必须写入：

```text
config/project_rules.yaml
```

不要 hard-code 在 Python if/else 深处。

---

# 28. 推荐 project_rules.yaml

```yaml
classification:
  PLOT_NARRATION:
    fair_use: false
    derivative: true
    fair_use_type: "/"
    derivative_type: "剧情解说"

  NARRATION_COMMENTARY:
    fair_use: true
    derivative: true
    fair_use_type: "夹叙夹议类解说"
    derivative_type: "夹叙夹议解说"

  REACTION:
    evaluate_with_reaction_rules: true

  MASHUP:
    evaluate_transformative_degree: true
```

规则应允许案件之间替换。

---

# 29. Reaction 特殊规则

项目存在“故意跳播”的特殊判断。

现有规则涉及：

- 较长点评是否系统性被跳过；
- 是否仅保留一两句结论；
- 同一视频长短点评的取证情况；
- 同一 UP 主其他视频的跳播模式。

系统应计算：

```text
commentary_total_duration
commentary_captured_duration

rights_work_total_duration
rights_work_captured_duration
```

进一步：

```text
commentary_capture_ratio
rights_work_capture_ratio
```

如果：

```text
commentary_capture_ratio << rights_work_capture_ratio
```

则标：

```text
SUSPECTED_SELECTIVE_SKIP
```

注意：

“故意”包含较强评价色彩。

V1 不宜仅凭数学比例直接形成最终法律事实。

因此默认：

```text
mandatory_review = true
```

但系统可以自动生成建议结果和项目要求的固定模板。

---

# 30. 同一 UP 主一致性

项目明确要求相同 UP 主的判断标准保持基本一致。

SQLite 保存：

```text
uploader_id
case_id
classification
fair_use_result
confidence
```

分析新视频时查询：

```text
UploaderHistory
```

如果同一 UP：

```text
过去10条均为REACTION
```

新结果：

```text
PLOT_NARRATION
```

则：

```text
consistency_warning = true
```

但不得为了保持一致而强行覆盖新结果。

---

# 31. 引用比例

系统必须使用匹配出来的原作区间自动计算。

不要让 Gemma 计算。

保存：

```text
rights_work_seconds
relevant_original_seconds
usage_ratio
```

项目规则规定：

如计算比例超过10%，比对意见不再填写具体数值，而采用项目约定的概括性表述。

注意：

内部数据库仍应保留真实比例。

仅最终文字展示遵循项目格式。

---

# 32. 比对意见生成

仅当项目规则判定需要生成意见时调用 Gemma。

输入必须包含已经确定的事实。

例如：

```json
{
  "type": "COMMENTARY_ANALYSIS",
  "subtype": "细节分析",
  "video_duration": 193,
  "rights_work_duration": 67,
  "episodes": [3, 7],
  "usage_ratio": 0.025,
  "creator_viewpoints": [
    "分析人物行为动机",
    "评论剧情逻辑"
  ],
  "plot_reproduction_level": "LOW",
  "replacement_risk": "LOW"
}
```

模型不得自行补充视频中没有出现的观点。

---

# 33. 比对意见结构

固定三段：

```text
【使用的性质和目的】

【使用的比例】

【对原作品的替代可能性】
```

目标长度：

```text
200–300字
```

项目要求意见简明、突出转换性事实、避免套话，且转换性的重点部分需要加粗。

---

# 34. LLM Anti-Hallucination

生成之后再次进行机器验证。

至少检查：

### 集数一致

正文提到：

```text
第3、7集
```

必须与数据库一致。

### 数值一致

正文：

```text
2.5%
```

必须与数据库相符。

### 类型一致

若：

```text
fair_use = false
```

不得出现：

```text
构成合理使用
```

### 禁止词/高风险套话

维护：

```text
config/language_rules.yaml
```

对项目指出的典型错误表达进行检查。

原始要求特别反对：

- 把“二创/独创性”直接等同于转换性；
- 只罗列原作、不写作者观点；
- 大量套话；
- 不准确的著作权法动词；
- 意义不明的自造词。

---

# 35. SQLite 核心表

至少包含：

```text
projects
original_assets
accused_videos
evidence_videos
uploaders
frame_features
transcripts
segment_matches
transformations
content_segments
semantic_assessments
rule_results
review_items
exports
```

---

# 36. segment_matches 最低字段

```text
id
case_id

accused_start
accused_end

source_episode
source_start
source_end

speed_ratio

visual_score
audio_score
temporal_score
overall_confidence

mirrored
cropped

created_at
algorithm_version
```

`algorithm_version` 必须保存。

否则算法更新后无法追溯历史结果。

---

# 37. Confidence

统一：

```text
0.00–1.00
```

最终划分：

```text
GREEN   >= 0.95
YELLOW  >= 0.75 and < 0.95
RED     < 0.75
```

具体阈值后续根据 benchmark 调整。

---

# 38. Review Queue

每个需要人工查看的项目生成：

```text
output/review/CASE_0017/
```

包含：

```text
summary.json
accused_clip.mp4
source_clip.mp4
contact_sheet.jpg
analysis.html 或 markdown
```

人工不应该重新找原片。

点击 review 项时应直接看到：

```text
左边：被诉片段
右边：候选原片
```

及：

```text
为什么系统不确定
```

---

# 39. Excel Export

禁止直接覆盖原文件。

输出：

```text
comparison_result.xlsx
```

保留：

- 原 workbook 结构；
- 原格式；
- 原 sheet；
- 既有内容。

只填写指定单元格。

Excel 层不得重新实现业务逻辑。

业务结果先进入数据库：

```text
SQLite
```

最后：

```text
SQLite → Excel
```

---

# 40. Audit JSON

每条案件同时输出：

```text
output/results/CASE_0001.json
```

完整记录：

```text
输入文件hash
媒体metadata
匹配区间
算法分数
内容分类
Gemma输入摘要
Gemma结构化输出
规则引擎结果
最终Excel字段
人工override
程序版本
```

这是律所场景非常重要的可追溯设计。

---

# 41. 文件 Hash

所有原始视频：

```text
SHA-256
```

第一次 ingest 时计算。

数据库保存：

```text
file_path
file_size
sha256
```

缓存键优先依赖 SHA-256。

同一个文件重复出现不得重新进行高成本分析。

---

# 42. Cache

必须缓存：

- FFprobe；
- 抽帧；
- embeddings；
- audio fingerprint；
- ASR；
- Gemma分析。

避免重新运行程序以后从头计算。

---

# 43. CLI

V1 只开发 CLI。

至少：

```bash
python -m ipvm init-project
```

```bash
python -m ipvm index-originals
```

```bash
python -m ipvm analyze CASE_0001
```

```bash
python -m ipvm analyze-all
```

```bash
python -m ipvm export
```

```bash
python -m ipvm benchmark
```

```bash
python -m ipvm doctor
```

暂不开发 Streamlit。

---

# 44. doctor 命令

检测：

```text
macOS版本
Apple Silicon型号
统一内存
Python版本
FFmpeg
磁盘空间
Gemma runtime
模型路径
FAISS
MLX
```

输出：

```text
PASS / WARN / FAIL
```

根据机器内存推荐模型。

---

# 45. 日志

使用结构化 logging。

禁止大量：

```python
print()
```

至少记录：

```text
timestamp
case_id
module
level
message
duration
```

---

# 46. 错误不得静默吞掉

例如：

```text
找不到可信原片
```

正确行为：

```text
MATCH_NOT_FOUND
```

错误行为：

```text
随机选最相似的一集
```

同理：

Gemma 不确定：

```text
UNKNOWN
```

优于猜测。

---

# 47. Claude / Codex 分工

建议两者不要同时修改相同核心文件。

## Codex 负责

主要工程与算法：

```text
项目 scaffold
FFmpeg
ffprobe
视频抽帧
视觉 embedding
FAISS
candidate retrieval
RANSAC
temporal alignment
镜像/速度检测
benchmark
SQLite
cache
CLI
测试
性能优化
```

核心目标：

**把“被诉视频 → 原剧时间轴”做准。**

---

## Claude 负责

在 P1 匹配通过验收之后主要负责：

```text
项目规则结构化
Gemma prompt
JSON schema
内容分类
Reaction规则
reasonable-use rule engine
QA validator
比对意见生成
Excel字段映射
文档
```

Claude 不得在未经确认情况下重新设计视频检索引擎。

---

# 48. 推荐开发顺序

## Phase 0 — Bootstrap

完成：

- repository；
- Python package；
- config；
- SQLite；
- CLI；
- doctor；
- logging；
- tests。

验收：

```text
pytest
```

全部通过。

---

## Phase 1 — Video Matching MVP

只实现：

```text
输入：
originals/
accused.mp4

输出：
原片集数 + 时间轴
```

暂时不做：

- Gemma；
- 合理使用；
- Excel；
- Reaction。

这是整个项目 Gate 1。

---

## Phase 2 — Matching Robustness

加入：

- 镜像；
- 变速；
- crop；
- 拼接；
- subtitles；
- audio。

运行 synthetic benchmark。

达到验收指标。

---

## Phase 3 — Evidence Analysis

加入：

- evidence.mp4；
- 完整播放判断；
- 跳播；
- 倍速；
- 取证时长。

---

## Phase 4 — Semantic Layer

加入：

- ASR；
- content segmentation；
- Gemma adapter；
- Reaction；
- Commentary；
- Plot narration；
- Mashup。

---

## Phase 5 — Rule Engine

将《七时吉祥》项目规则编码为：

```text
project_rules.yaml
```

禁止把案件规则与通用算法绑死。

---

## Phase 6 — Opinion Generator + QA

生成：

```text
合理使用比对意见
```

并做：

```text
事实一致性校验。
```

---

## Phase 7 — Excel

最后才对接真实 Excel。

---

# 49. 第一轮真实测试

不要一开始跑几百条。

从项目中选：

```text
5–10条
```

人工已经知道答案的视频。

最好覆盖：

1. 普通引用；
2. 镜像；
3. 加速；
4. Reaction；
5. 混剪；
6. 跳播；
7. 较严重裁剪。

人工结果作为 golden set。

---

# 50. 性能记录

每阶段记录：

```text
index_seconds_per_hour_video
analysis_seconds_per_minute_accused_video
peak_memory
disk_usage
segment_recall
boundary_error
```

不要只说：

```text
“感觉挺快。”
```

---

# 51. 优化顺序

必须：

```text
正确性
>
可追溯性
>
稳定性
>
速度
>
UI
```

不得为了速度牺牲匹配正确率。

---

# 52. Gemma Runtime

Gemma 当前运行环境变化较快。

Google 官方支持本地部署 Gemma 4 12B，并提供 LiteRT-LM 等本地方式；社区 Apple Silicon / MLX 实现也已陆续支持 Gemma 4，但不同实现对 unified multimodal、视频和音频能力支持状态存在差异。

因此 V1：

**不要依赖 Gemma 原生“视频文件输入”。**

统一输入：

```text
images[]
transcript
structured_facts
```

这样切换：

```text
LiteRT-LM
MLX
llama.cpp
```

不会影响业务代码。

---

# 53. 安全与保密

默认：

```text
LOCAL_ONLY=true
```

程序不得：

- 自动上传案件文件；
- 自动调用云端 Gemini；
- 自动调用 OpenAI；
- 自动调用 Anthropic；
- 自动上传 Hugging Face inference endpoint。

下载安装模型本身可以联网。

案件视频推理阶段默认断网也必须正常工作。

---

# 54. 模型与案件材料解耦

模型：

```text
models/
```

案件：

```text
input/
```

绝不能把案件文件放进：

```text
model cache
prompt examples
git repo
test fixtures
```

真实案件材料不得进入 Git。

---

# 55. Gitignore

至少：

```text
input/
workspace/
output/
models/
*.mp4
*.mkv
*.mov
*.wav
*.db
.env
```

benchmark 中只允许 synthetic / 明确可用于测试的数据。

---

# 56. Definition of Done — V1

V1 完成必须满足：

### A. 原片定位

给被诉视频能够自动得到：

```text
被诉时间轴
→
对应集数
→
原片时间轴
```

### B. 处理方式

识别主要：

```text
镜像
加速
裁剪
缩放
```

### C. 使用统计

自动得到：

```text
原剧使用秒数
对应集数
引用比例
```

### D. 取证

能够判断：

```text
是否完整播放
取证时长
跳播
```

### E. 语义

能够分类：

```text
Reaction
观点评析
夹叙夹议
剧情解说
混剪
```

### F. 规则

能够按照当前项目规则产生：

```text
合理使用可能性
演绎可能性
合理使用类型
演绎类型
```

### G. 文本

需要时生成：

```text
200–300字
```

比对意见。

### H. QA

能够自动发现：

- 集数冲突；
- 比例冲突；
- 分类冲突；
- 比对意见与结构化事实冲突。

### I. 输出

能够生成：

```text
comparison_result.xlsx
```

并保留原始 Excel。

### J. 审计

每条结果均有：

```text
CASE_xxxx.json
```

可以解释系统为何作出结论。

---

# 57. 当前最重要的 Gate

在任何人开始调 Gemma prompt 之前，必须完成以下演示：

给程序：

```text
电视剧全集
+
一个经过镜像、1.25倍速、加字幕、多片段拼接的测试视频
```

程序输出：

```text
被诉 00:03.20–00:16.74
→ EP07 00:17:42.11–00:17:58.93

被诉 00:21.31–00:39.82
→ EP12 00:08:13.41–00:08:36.52
```

且与 ground truth 基本一致。

**这个 Gate 不通过，后续 AI 层暂停。**

---

# 58. 第一条给 Codex 的执行指令

读取本交接文档。

只执行：

```text
Phase 0
+
Phase 1
+
Synthetic Benchmark基础框架
```

不要实现：

```text
Gemma
合理使用
Excel
网页抓取
```

完成后提供：

1. repository tree；
2. README；
3. 安装步骤；
4. CLI；
5. 单元测试；
6. benchmark 生成器；
7. 第一版视频匹配结果；
8. 已知问题；
9. 性能数据；
10. 需要架构负责人决策的问题。

不要自行进入 Phase 2。

---

# 59. 第一条给 Claude 的执行指令

在 Codex 的 Phase 0 / Phase 1 可运行版本完成之后：

阅读：

1. 本交接文档；
2. 当前 repository；
3. 《七时吉祥》比对要求。

任务：

先设计但暂不接入生产代码：

```text
semantic schema
project_rules.yaml
Gemma structured-output schema
QA rules
```

要求：

不要重新设计 Phase 1 视频匹配模块。

不要直接编写最终合理使用 opinion prompt。

先提交 schema 和规则映射给架构负责人审核。

---

# 60. 架构变更流程

如果执行 Agent 认为需要修改本文件中的核心决策：

创建：

```text
docs/adr/ADR-xxxx.md
```

格式：

```text
# 问题

# 当前方案

# 为什么当前方案存在问题

# 方案A

# 方案B

# 推荐方案

# 性能影响

# 数据兼容影响

# 对后续模块影响
```

然后交由架构负责人决定。

---

# 61. 最终原则

这个项目不是：

> 让一个多模态大模型把所有视频从头看到尾，然后相信它给出的答案。

真正的系统应该是：

```text
计算机视觉确定“用了什么”
+
时序算法确定“用了哪里”
+
音频和ASR提供交叉验证
+
Gemma确定“作者在表达什么”
+
规则引擎确定“本项目表格怎么填”
+
QA系统保证前后事实一致
```

最终目标不是“AI替代人工看视频”。

而是：

> **让机器处理全部确定性、机械性和可结构化的比对工作，只把真正存在判断不确定性的少数视频交给人。**

—— Architecture Baseline v0.1