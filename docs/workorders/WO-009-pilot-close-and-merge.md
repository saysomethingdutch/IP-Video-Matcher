# WO-009 试点收尾：合并进 main

- 签发：Owner　执行：**Codex**（做完 WO-001-R3 之后）　时间盒：**2–3 小时**
- 交付说明：`docs/workorders/WO-009-RESULT.md`，不超过 1 页

## 0. 背景（D-051）

七时吉祥早已结案。这次试点的目的是验证工具，不是重新交付这个案子，所以：
- 不整批重写意见；
- 所有者不再逐行核验 169 行。

收尾只做两件事：
1. 把已验证的代码和记录固定下来；
2. 为逐玉准备一个干净的起点。

## 1. 合并

1. 以下提交依次合入 `pilot/integration`：
   - `pilot/wo-001-r3`（台账遗留问题）；
   - Claude Code 在 `pilot/wo-004c-r3` 上的**代码改动**：不引用原话的文风规则、标题自称 reaction 的类型规则、对应的校验器和测试。**不含整批重写的数据。**
2. 跑全仓测试，全部通过。
3. 用一条命令跑一遍试点（`--rows 1051,1098,1146,1175,1216`），沿用缓存，确认能跑通。

## 2. 合进 main（要格外小心）

1. **先盘点 main 工作区里的未跟踪文件。**尤其是 `pilot/` 目录：main 上它未被跟踪，里面可能是早期运行留下的副本。
   - 与 `pilot/integration` 中同路径的文件逐个比对。
   - 有差异的，**不得删除或覆盖**。整体移到 `_attic/main-untracked-pilot-<日期>/`，交付说明里列出清单。
2. `docs/workorders/`：main 上这个目录未被跟踪，里面是 Owner 的工单和决策记录，是项目的正式记录。**先原样复制一份备份**，再与分支里的同名文件比对：
   - 分支里只有各单的 RESULT 和运行日志；
   - main 上有工单和 `DECISIONS.md`。
   - 合并后两边的文件都要保留。**同名文件以 main 工作区的版本为准。**
3. **只动这两个目录。**不碰：
   - `experiments/phase3v_*`、`papers/`、`claude_architecture_battle_pack/`、`IP-Video-Matcher-Architecture-Battle-Pack.zip`；
   - `.DS_Store`；
   - `workspace/`（产物目录，不进 git）。
4. 把 `pilot/integration` 合并进 `main`（`--no-ff`），打标签 `pilot-qsjx-v1`。
5. **不 push**，由所有者决定。

## 3. 交付说明要写

- 合并了哪些分支、提交号、测试结果；
- 移到 `_attic/` 的文件清单；
- 本地还有哪些 worktree 可以删，由所有者确认后再删；
- 下一步：逐玉摸底，用 `cases/_template.yaml` 开新配置。
