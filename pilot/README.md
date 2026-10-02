# 给所有者：按案件运行

在 `pilot/integration` 仓库根目录操作。七时吉祥的配置已写在 `cases/qsjx.yaml`；外置盘案件先确认 `/Volumes/Element` 已挂载。原始 Excel、录像、截图和下载文件保留原状，程序不会覆盖原表。

1. **放材料、写配置。** 新案件复制 `cases/_template.yaml` 为 `cases/<案件>.yaml`，填比对表路径、工作表、行号、产出列映射、证据目录和录像清单。按客户要求填规则、意见格式与样例，并设置模型和输出目录。只有表里有集数列且需要核对原片时才打开 `source.enabled`。证据画面结构不同于当前手机播放器时，先补对应的画面模块。图片是否可上传由 `models.visual.allow_image_upload` 明确控制；密钥只放登录 shell 环境，不写入配置。
2. **一条命令生成待核对结果。** 本机登录 shell 提供 `python3`：

   ```bash
   zsh -lic 'python3 -m pilot run cases/qsjx.yaml'
   ```

   按台账、字幕与集数、可选语音、画面、分类、意见、核对页数据的顺序执行。未变化的输入使用缓存。只处理指定行可加 `--rows 1051,1098`；从某步及后续步骤强制重跑可加 `--from classification`。步骤名为 `ledger`、`captions`、`speech`、`visual`、`classification`、`opinion`、`review`。结束时看终端摘要；逐行结果、失败原因、旗标及费用明细在配置的输出目录下的 `integration_summary.json`。
3. **核对。** 运行 `python3 -m pilot review cases/qsjx.yaml`，打开 `http://127.0.0.1:8765/`。逐行查看证据、红黄旗标和机器填写的列，修改或批准；页面只监听本机。
4. **导出。** 运行 `python3 -m pilot export cases/qsjx.yaml`。Excel 与修改记录写入配置输出目录的 `export/`，原表不变。

### WO-108：可复用的核验控件与代办清单

先读 `docs/LESSONS-tsxk.md`。新案子的默认做法在 `cases/_template.yaml` 和
`pilot/semantic/rules_template.yaml`：只写观察到的事实，程序事实和模型文字分开保存，
模型分不准的类别准备两份版本，由所有者选择。模板声明默认策略；新案件适配器需把 `review.fixed_values` 等配置转换成下面的控件参数。
吞噬星空页面已接入，旧案件页面尚未全部迁移；客户的判断标准仍需单独填写。

`pilot/review/static/components.js` 是不带案名、列号和类别的控件库：

| 控件 | 适配器提供什么 | 生效方式 |
|---|---|---|
| `fixedSelect` | 可选值、当前值和输入属性 | 下拉选值，原核验接口保存 |
| `removeItem` | 条目文本和索引 | 重新编号，用户点删才保存 |
| `alternative` | `eligible/ready/values` 和按钮文字 | 先预览；用户点击后记录每个替换格 |
| `syncText` | API、同步接口、字段映射 | 先显示关联文字，保存两个格 |
| `confirmation` / `batchPage` | 清单旧值、新值、理由 | 查看不修改；勾选确认并填代办人后生效 |
| `evidence` | `support`（录屏秒、原话、截图） | 时间按钮使用既有 `data-seek` 播放器跳转 |

Python 的 `pilot/review/evidence.py` 负责依据卡片：吞噬星空的规则文件可用
`review_defaults: {列号: 默认值}` 覆盖默认值；缺少依据的非默认值标红。吞噬星空适配器至少提供 T、X、W、偷渡、口播、配乐。原始双模型描述保留在
缓存，只有交叉一致或本机确认的事实能进入写作提示词。旧缓存缺少水印位置时，只按
UP 主名 / bilibili 的相近文字过滤；新缓存另外保留 OCR 坐标。

**Owner 代办**清单支持 UTF-8 JSON 或 CSV，字段为 `row,column,old,new,reason`
（也支持“行、列、旧值、新值、理由”）。JSON 例子：

```json
[{"row":"1051","column":"U","old":"否","new":"是","reason":"所有者确认有讲述"}]
```

预览：`python -m pilot.review.batch cases/<案件>.yaml 修改清单.json`。
确认：同一命令加 `--delegate 姓名 --confirm`。测试可加 `--decisions /临时目录/decisions.jsonl`。
核对页也有“Owner 代办 · 导入修改清单”入口；旧值不一致会跳过，不覆盖后来修改。
写入采用文件锁并重新读取记录，事件注明“Owner 代办”、代办人姓名及理由。
不应把核验过的全文交给自动过滤器。自动【其他】清理只建议处理未被所有者修改过的 AC；
所有者写过的句子只能通过逐格核验或一份明确的代办清单修改。

**离线回归**：`python -m pilot.tsxk.regress` 固定 20261002 的 300/100 分组，
报 v1 和 R1 两个基线；任意机器版本用 `--machine-dir 路径 --label 名称`。
`--development-errors` 只输出 300 行的错例，100 行永远只报数；标准答案快照留在
`workspace/tsxk/wo108/regression/`，记录或分组改动后拒绝继续沿用旧快照。
条目删除数按“机器编号条目在最终文本中缺失”计算，改写也算缺失，不声称是语义删除。

`python -m pilot.tsxk.lessons` 的缓存修复用于暂存重放，不安装到正式 `rows/`。
`pilot/timeline/danmaku_trial.py` 是独立半秒移动实验，未接入抽帧流水线。
