# WO-003a 交付说明：录像字幕轨与集数证据

分支 `pilot/wo-003a-captions`：从 `pilot/wo-001-ledger`（9ad7d17）切出，并合并了 `pilot/wo-002a-textindex`。代码在 `pilot/content/`，只用标准库，未改 `pilot/ledger/` 和 `pilot/textindex/`。主工作区有 ledger 文件的未跟踪副本，无法切到这个分支，所以我在独立 worktree 里开发，主工作区一直停在 `main`。worktree 路径是 `~/.claude/worktrees/wo-003a-captions/IP-Video-Matcher`，其中的 `workspace/pilot` 链接到主仓库的共享数据，下面的命令在这里直接运行。

## 1. 命令

```bash
/opt/homebrew/bin/python3 -m pilot.content run workspace/pilot/qsjx_e18 --ledger workspace/pilot/qsjx_e18/ledger_r1_baseline.jsonl
.venv/bin/python -m pytest -q pilot/content/tests pilot/textindex/tests      # 36 passed
```

输出在 `workspace/pilot/qsjx_e18/content/`，169 行共用时 9.9 s：
- 169 个 `<row_id>.json`；
- `content_summary.csv` 和 `run_summary.json`；
- 8 个核对页副本 `MVI_000x.html`，每行追加了"字幕与集数"区块。原剧台词用蓝色、非原剧文字用橙色、待定用灰色，每句点开可看录像秒、被诉视频秒和原剧秒。副本放在 `content/` 下，原页面没有改动。

## 2. 验收行

| 行 | 结果 | 与期望 |
|---|---|---|
| 1152 | EP11（24 句），NARROWER | 符合 |
| 1160 | EP03（19）、EP04（9），CLAIM_UNPARSEABLE | 符合 |
| 1161 | EP05（14）、EP03（1 句，7 字），CONTRADICTED | 结论符合，但**缺 EP07**：EP07 只有一句孤立的"从教场回来"（原剧为"从校场回来"，相似度 0.8），另一句"听见没有"在 EP06、EP07 里都有，无法区分。按门槛只算弱线索，已写入说明 |
| 1163 | EP07（8）、EP08（10），CONTRADICTED | 符合 |
| 1144 / 1145 | EP15、EP16 / EP17，均为 NARROWER | 符合。EP16 靠"该不是要告自吧"一句，这句在原剧里是相邻两句 |
| 1154 / 1155 | EP12 为主（16 / 11 句），CONTRADICTED | 符合。1154 另有 EP09（7）、EP07（2）、EP08（1），原剧 EP12 的对应位置没有这些台词，应是 UP 主剪入了其他集 |

## 3. 分布与比例

- **结论分布**：UNVERIFIED 120，NARROWER 29，CONTRADICTED 19，CLAIM_UNPARSEABLE 1，SUPPORTED 0。UNVERIFIED 的原因逐行写在说明里，主要有：MVI_0001–0004 的泰配 reaction 只有英文字幕；BGM 混剪只有歌词；解说视频只有旁白；声称的集数本地缺失。
- **原剧台词占比**：字幕事件共 3,779 条，其中原剧台词 678 条，占 **17.9%**。其余为非原剧文字 3,008 条（底部解说字幕 1,747、其他 1,107、竖排或花字 154）和待定 93 条。
- **误判为台词的比例**：随机抽 40 条 LINKED，1 条是 UP 主改写的台词，约 2.5%。按可疑特征另找出 10 条误判样例：
  - 1130"你给我闭嘴"→EP18 565 s：reaction 里观众说的话；
  - 1129"无论对方变成什么样子"→EP27 1306 s：UP 主旁白；
  - 1129"有摩罗、麒麟、帝休三大神族鼎立于世"→EP01 459.5 s：UP 主改写的开篇旁白；
  - 1118"没了就是没了"→EP09 521 s、"此生尽就是永生尽"→EP09 526 s：UP 主改写；
  - 1177"鲜有神灵敢靠近呐"→EP15 708.5 s：解说中的改写；
  - 1151"你便是本座预定的历劫仙侣了"→EP02 195 s：引用时加了字；
  - 1165"我立刻转身就走绝不说一句废话"→EP12 989 s：MV 字幕改写，1168 行也有；
  - 1167"缘起缘天"→EP09 266 s、"红生一梦去无还"→EP09 280 s：这是插曲歌词，原剧字幕里也有同一段歌词。

## 4. 需要 Owner 决定的问题

1. **台账版本**：运行期间，WO-001 在共享的 workspace 里重新生成了 `ledger.jsonl` 和核对页（09-25 02:17）。工单指定用 R1，所以本次读的是 R1 基线副本；核对页副本是在新版 HTML 上追加的区块，区块里注明了所用台账。要改用新台账，去掉 `--ledger` 重跑即可，需要吗？
2. **1161 的 EP07**：满足"只有一个候选、相似度 ≥0.8、≥5 字"的待定句，要不要也计为证据？
3. **插曲歌词**：原剧字幕里含插曲歌词，UP 主用这首歌做 BGM 时，歌词会被当成台词；1167 行 EP09 的 6 句证据里有 4 句是歌词。要不要在 textindex 里给歌词段打标记？这需要改 textindex。
4. **两处 textindex 改动建议**：
   - 繁简转换：例如"你不應該來的"因为是繁体，相似度被压低了。
   - "相邻两句拼接"候选：目前放在 `pilot/content/evidence.py`，建议移入 textindex。
5. **与工单字面要求的细节差异，请确认**：
   - 另外排除了分享面板、UP 主空间页等非播放页，B 站的音乐标签，以及纯数字或纯字母的碎片；
   - 外文字幕保留为非原剧文字，并标 `foreign`；
   - 带引号的台词、字幕带以外的台词匹配，归为非原剧文字；前后没有连贯上下文的 4 字以内短句，归为待定；
   - 前情窗口取文件开头起 100–160 s。在有前一集的剧集里，实测前情落在 109–156 s。

## 5. 实际用时

约 35 分钟（09-25 01:57–02:30），没有新增 OCR。
