# IP-Video-Matcher

律所用的著作权比对工具。原告给一张比对表和一批取证录像，工具替律师把表里的产出列填出初稿、起草比对意见；律师在本地核对页逐行核验，最后导出 Excel 副本。原表和客户材料不会被修改。

机器负责写，人负责批。不存在"全自动免审"。

## 做过的项目

| 项目 | 证据形式 | 规模 | 状态 |
|---|---|---|---|
| 七时吉祥 | 公证处用相机拍手机屏的录像 | 169 行 | 已收尾，标签 `pilot-qsjx-v1` |
| 吞噬星空 | B 站网页录屏（易存云取证） | 400 行 | 已收尾，标签 `tsxk-v1` |

吞噬星空 400 行由律师核完：6,400 格里改了 3.9%，按意见修正后平均每行核验 40 秒。错在哪里、为什么、下个项目怎么做，写在 [docs/LESSONS-tsxk.md](docs/LESSONS-tsxk.md)。

## 怎么跑

```bash
zsh -lic 'python3 -m pilot run cases/<案件>.yaml'      # 跑流水线
python3 -m pilot review cases/<案件>.yaml               # 本地核对页，只监听 127.0.0.1:8765
python3 -m pilot export cases/<案件>.yaml               # 导出 Excel 副本，原表不动
```

运行前提和详细步骤见 [pilot/README.md](pilot/README.md)。要点：

- macOS，OCR 用系统自带的 Apple Vision；
- 取证录像和原表在外置盘上，不在这个仓库里；
- 云端模型的 key 写在 `~/.zshrc`，不进仓库。

## 流程

1. **台账**：读录像里的播放页面，定下标题、地址、取证时间、是否播完。
2. **画面和声音**：本机 OCR 读字幕；两家云端模型交叉看画面，一致才采用；声音在本机转写，不上传。
3. **分类和说明**：按该客户的口径判类别，起草视频说明或比对意见。
4. **核验**：律师在核对页逐行批准或修改，页面上会显示判断依据的时间点和原话。
5. **导出**：只改目标表的目标格，导出后与原表逐格比对。

## 目录

| 路径 | 内容 |
|---|---|
| `pilot/` | 全部代码 |
| `cases/` | 案件配置；新案子从 `_template.yaml` 复制 |
| `docs/workorders/` | 工单、交付说明和决策记录 `DECISIONS.md` |
| `docs/LESSONS-tsxk.md` | 吞噬星空复盘 |
| `tests/`、`pilot/**/test_*.py` | 测试，`pytest` 一条命令全跑 |
| `tools/publish_github.sh` | 同步到 GitHub 私有仓库 |

运行产物（`workspace/`）、本地模型（`models/`）、原剧文件（`real_test/`）不进 git。

## 接手先读

1. [AGENTS.md](AGENTS.md)：给接手 agent 的说明；
2. [docs/workorders/DECISIONS.md](docs/workorders/DECISIONS.md)：全部决策，D-001 起；
3. [pilot/README.md](pilot/README.md)：运行步骤；
4. [docs/LESSONS-tsxk.md](docs/LESSONS-tsxk.md)：开新案子前必读。

## 约定

- 仓库是私有的。客户文件、取证录像、运行产物都不提交。
- 主仓库不设远程，同步到 GitHub 只用 `tools/publish_github.sh`。
- 不上传音频；画面只在案件授权的范围内发给云端模型。

## 历史

这个项目最初走的是"视频时间轴匹配"路线（`src/ipvm`、`benchmark/` 和一批阶段评审文档），2026-09-23 审计后停掉，改做现在的比对表工具。这些代码和文档已于 2026-10-02 从主干移除（D-084）。要看的话，检出标签 `early-route-final`。
