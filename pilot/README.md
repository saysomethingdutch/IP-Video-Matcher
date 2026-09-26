# 按案件运行 Pilot

在检出 `pilot/integration` 的仓库根目录操作。保留原始 Excel、录像、截图和下载文件，不改名、不覆盖。外置盘案件先确认 `/Volumes/Element` 已挂载。

1. 复制 `cases/_template.yaml` 为 `cases/<案件>.yaml`，填写比对表路径、工作表、总编号范围、表头及 U–AJ 对应的实际列；填写证据目录和每个录像 ID。`source.enabled` 仅在比对表有原片集数列且需要精确集数或比例时设为 `true`，并填原片目录。客户规则、意见格式、样例、模型和输出目录均在同一文件中。新证据若不是播放器画布结构，先实现对应画面模块并修改 `models.visual.protocol`。
2. 已授权的七时吉祥配置是 `cases/qsjx.yaml`。完整运行：

   ```bash
   zsh -lic 'python -m pilot run cases/qsjx.yaml'
   ```

   从分类开始重跑：`zsh -lic 'python -m pilot run cases/qsjx.yaml --from classification'`。仅处理两行：`zsh -lic 'python -m pilot run cases/qsjx.yaml --rows 1051,1098'`。从仓库根目录执行后两条命令。可用步骤名：`ledger`、`captions`、`speech`、`visual`、`classification`、`opinion`、`review`。未变化的输入沿用缓存；`--from` 强制重跑该步及后续步骤。`--rows` 保留整批台账，仅重算指定行。
3. 运行结束看终端摘要及 `workspace/pilot/<案件>/integration_summary.json`。红黄旗标仍需人工核验；语音可在配置中关闭。云端图片上传仅受 `models.visual.allow_image_upload` 控制；未授权时设为 `false`。模型密钥从登录 shell 环境继承，配置文件不放密钥。
4. 打开核对页：`python -m pilot review cases/qsjx.yaml`，浏览器访问 `http://127.0.0.1:8765/`。逐行核对、修改并批准。核对页只监听本机回环地址。导出：`python -m pilot export cases/qsjx.yaml`。结果和修改记录位于配置指定输出目录的 `export/`；原表不变。
