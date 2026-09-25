# WO-004c 交付说明：比对意见起草与校验

分支 `pilot/wo-004c-opinion`，从 f719758（WO-004b）切出。代码在 `pilot/opinion/`。另外改了 `pilot/semantic` 的两处：
- 模型适配层加 system 提示、temperature 和缓存计数；
- run 加 `--fields-only`，为 R3 准备。

worktree：`~/.claude/worktrees/wo-004c-opinion/IP-Video-Matcher`。

## 1. 命令与输出

```bash
zsh -lic '/opt/homebrew/bin/python3 -m pilot.opinion run workspace/pilot/qsjx_e18 --workers 4 --no-local'
/opt/homebrew/bin/python3 -m pilot.opinion run workspace/pilot/qsjx_e18 --revalidate --no-local   # 只重新校验，不调模型
/opt/homebrew/bin/python3 -m pilot.opinion run workspace/pilot/qsjx_e18 --dry out.json            # 只看提示词
.venv/bin/python -m pytest -q pilot/opinion/tests pilot/semantic/tests                           # 31 passed
```

- `semantic/<row>.json` 增加 `opinion` 字段：正文、分段、加粗、证据、比例依据、校验结果、模型、token、费用、输入指纹。AF 列也填入了草稿，核对页直接读。
- 另外输出：
  - `semantic/opinions/`：意见另存一份，WO-004b 重跑后不丢；
  - `opinions.csv`；
  - `fields.csv`："比对意见"列 169 行都已填；
  - `opinion_summary.json`；
  - `episode_seconds.json`：本地 32 集的 ffprobe 实测时长。

## 2. 结果（169 行，DeepSeek flash，只发文字）

- **分类**：
  - 起草 161 行，其中 21 行分类为不确定，照常起草、标黄；
  - 填"/" 4 行（原片搬运 3、剧情解说 1）；
  - 重复取证 4 行；
  - 故意跳播占位 0 行，因为没有红标候选。
- **校验通过 149/161（92.5%）**，77 行重试过一次。未通过的 12 行保留草稿并标红：
  - 8 行没有 UP 主文字，也没有画面事实，只有标题；
  - 另外 4 行（1102、1140、1199、1203）事实也很少；
  - 按规则写不出"这条视频特有的内容"。
- **长度**：250–378 字，中位数 323 字。只有 15 行落在"200–300 字为宜"，不过比本批以外人工意见的中位数（314–438 字）短。
- **比例段**：
  - 集数来源：台词证据 50 行，原告声称 111 行（标黄）；
  - 写出百分比 83 行；超过 10% 不写数值 74 行。
- **标黄**：比例段 145 条、外语转写请核对大意 61 条、分类不确定 21 条。
- **token 与费用**：
  - 现存意见共 238 次调用：输入 65.3 万（缓存命中 83%），输出 175.9 万，其中思考 169.2 万；
  - 缓存按未命中价计，约 $1.15；
  - 加上被替换的草稿，本单花费约 $1.4（上限）。

## 3. 做法要点

- **比例段**：代码按模板生成，每集时长用 ffprobe 实测；比例超过 10% 不写数值；模型不碰数字。
- **性质目的段、替代段**：由模型写。
- **风格样例**：
  - 客户示例 3 条；
  - 本批以外的人工意见 8 条：Reaction 851、272，混剪 153、1602，夹叙夹议 9、2，观点评析 1591、640；
  - 只给模型看这两段；
  - `corrections.jsonl` 的读取入口已预留。
- **画面说法**：只从 Z/AA/AB 列取值，逐项校验。
- **校验项**：§5 的七项之外，另加四项：类型须与分类一致；证据须逐字；画面说法须能在 Z/AA/AB 找到；引号最多 2 处。

## 4. 偏差与说明

1. **第一轮 20 行未通过**：其中 6 行是校验器的问题。模型原样引用了带标签的画面行，校验器却按不带标签的列值比对。修正后只重新校验，没有调模型。
2. **29 行引号超过 2 处**（对应客户常见错误 10）：加了这条规则后只重写这 29 行，全部通过。
3. **111 行没有台词证据**：按工单取原告声称的集数，多数是泰配 reaction 和没有字幕的混剪。像"1-38"这种全剧声称会让比例显得很小，这些行都标了黄。

## 5. R3 完成后重跑（按通知）

```bash
zsh -lic '/opt/homebrew/bin/python3 -m pilot.semantic run workspace/pilot/qsjx_e18 --backend deepseek --reuse --fields-only --no-local'
zsh -lic '/opt/homebrew/bin/python3 -m pilot.opinion run workspace/pilot/qsjx_e18 --reuse --workers 4 --no-local'
```

- 第一条只重算各列，沿用已存的性质。画面事实变了的行加黄标"事实已更新，性质未重判"。
- 第二条用输入指纹判断，只重写 Z/AA/AB 列变了的行。
- R3 的输出文件名要看 Codex 的交付；如果不是 `predictions_full.csv`，给 `--visual` 加一个选项即可。

## 6. 需要 Owner 决定

1. **12 行未通过的怎么办**：由所有者手写，还是放宽"具体性"，允许以标题为依据？
2. **111 行比例段的集数**：继续用原告声称，还是有标题/台标集数的行（5 行）改用它？
3. **长度**：要不要把目标压到 200–300 字？需要整批重写，约 $1.2、30 分钟。
