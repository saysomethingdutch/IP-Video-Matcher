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
