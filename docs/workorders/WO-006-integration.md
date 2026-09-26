# WO-006 合并各分支，一条命令跑全流程，按案件配置

- 签发：Owner　执行：**Codex**　时间盒：**1 个工作日**
- 分支：新建 `pilot/integration`，从 `main` 切出，依次合并：
  - `pilot/wo-001-ledger`（R2）
  - `pilot/wo-002a-textindex`
  - `pilot/wo-003a-captions`（R1）
  - `pilot/wo-003b-asr`
  - `pilot/wo-004a-vlm`（R3）
  - `pilot/wo-004b-classify`
  - `pilot/wo-004c-opinion`：Claude Code 正在做 R1，R1 提交后再合一次
  - `pilot/wo-005-review`
- 交付说明：`docs/workorders/WO-006-RESULT.md`，不超过 1 页

## 0. 目的

所有者以后只需要做四件事：
1. 把材料放好；
2. 写一份案件配置；
3. 敲一条命令；
4. 打开核对页核验，导出 Excel。

新案子（比如逐玉）只加一份配置和必要的新模块，不重新开发（D-028）。

## 1. 案件配置 `cases/qsjx.yaml`

**代码里不能再写死七时吉祥的路径、列号或规则。** 配置至少包含：

| 项目 | 内容 |
|---|---|
| 比对表 | 路径、工作表名、行范围（1051–1219）、产出列与原表列的对应（U–AJ） |
| 证据 | 录像、截图、下载文件所在目录 |
| 原片 | 目录，以及开关（D-028：表里有集数列才打开） |
| 客户口径 | 规则文件（`rules_qsjx.yaml`）、意见格式、风格样例 |
| 模型 | 看画面用 `glm-5.3-flash` 和 `deepseek-flash` 交叉，本地 Qwen3-VL 备用；文字用 `deepseek-flash`；是否允许上传图片（true/false） |
| 输出 | 输出目录 |

另写一份 `cases/_template.yaml`，每项都注明含义和可选值，为逐玉做准备。

## 2. 一条命令

```bash
zsh -lic 'python -m pilot run cases/qsjx.yaml'
```

- **步骤顺序**：台账 → 字幕与集数 → 语音（可选）→ 画面 → 分类 → 意见 → 核对页数据。
- **每一步都有缓存**：输入没变就跳过。另外支持：
  - `--from <步骤>`：从某一步开始重跑；
  - `--rows 1051,1098`：只跑指定的几行。
- **key**：命令本身不读 `~/.zshrc`。README 里写明要用 `zsh -lic` 启动（D-023）。
- **结束时打印一页摘要**：各步耗时、失败行、旗标数、云端费用。
- **另外两条命令**：
  - `python -m pilot review cases/qsjx.yaml`：打开核对页，只监听 127.0.0.1；
  - `python -m pilot export cases/qsjx.yaml`：导出 Excel，规则同 WO-005。

## 3. 验收

- **用现有缓存从头跑一遍**，直到生成核对页数据。产出与各分支现有产出逐列比对，应当一致。云端模型有随机性，允许个别差异，差异行要列出来。
- **另选 3 行清掉缓存**，从头跑通。
- **所有测试通过**。`pilot/ledger` 有 3 个测试因为 `.venv` 缺 openpyxl 而失败，一并修好。
- **README 一页**，写给所有者看：怎么放材料、怎么写配置、怎么跑、怎么核对和导出。

## 4. 不做

- 不改各模块的算法。
- 除 D-032 授权的画布拼图外，不上传任何东西。
- 不 push。
