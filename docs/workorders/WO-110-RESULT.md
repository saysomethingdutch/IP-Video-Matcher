# WO-110 结果（2026-10-03，Gate PASS：D-091）

D-089 摸底材料保留于 `workspace/audio-exp/`。A 仅用百炼 `qwen-audio-3.1-asr-flash`，B 仅用 `qwen3.8-omni-flash`，两模型实调成功。样本种子 20261003；A 全属开发组，B 开发 38 段、工单指定的保留组 1 段。每段 24 秒，均来自吞噬星空 B 站台账内容区间。先冻结清单与预算，再上传；B 仅收音频及固定问题，A 仅收音频，无标题、字幕或热词。

| A（最终各 20 段） | 字符相似度中位数：SenseVoice / 百炼 | <0.6：SenseVoice / 百炼 | 词表命中：SenseVoice / 百炼 |
|---|---|---|---|
| 解说 | 0.887 / 0.927 | 0/20（0%） / 0/20（0%） | 59/81 / 70/81 |
| 混剪对白 | 0.646 / 0.833 | 6/20（30%） / 2/20（10%） | 3/18 / 8/18 |
| 合计 | 0.839 / 0.894 | 6/40（15%） / 2/40（5%） | 62/99（62.6%） / 78/99（78.8%） |

相似度为去标点后的字符序列比；词表按工单逐字计出现次数。旧 OCR 有方向误判，统一重新做本机 OCR；4 段未达原定字幕门槛，保留调用记录、按原种子补 4 段，最终仍为 20+20。最终 5 段由 Codex 执行者逐秒目视核查参照（非所有者真人复核）。抽读：7816、7920 仍把“坐山客”写成“座山客”；8008、8109 的参照含无声注释，低分和词表漏记不能全归因于 ASR；切片首尾也有字幕与音频错位。

| B：正确数/总数 | 旧流水线 | WO-108 修正规则 | 百炼 |
|---|---|---|---|
| 手改解说，开发 | 0/4 | 4/4 | 4/4 |
| D-081 补配音，开发 | 0/15 | 14/15 | 13/15 |
| 无 UP 主口播，开发 | 0/4 | 4/4 | 4/4 |
| 角色对白，开发 | 15/15 | 13/15 | 15/15 |
| 保留组，仅汇总 | 0/1 | 1/1 | 1/1 |

B 基线为整行 `uploader_speech` 是否非空，云端为 24 秒切片；角色组另要求判为角色对白。7815 抽中原片对白，与整行“有解说”答案口径不同；7840 被云端判为角色对白；修正规则在 8105、8204 多报口播。人数及合成判断留存原答，未设人工真值；仅开发组抽读，无保留组调参。

成本：ASR 44 次、Omni 39 次，共 **83 次音频提交，失败/重试 0**，含被排除的 4 次；请求耗时合计 152.9 秒。输入/输出 Token 分别为 ASR 15,708/3,383、Omni 11,544/3,367。[官方价目](https://help.aliyun.com/zh/model-studio/model-pricing)与账号页面均为输入 0.8、输出 2.7 元/百万 Token。首轮保守预留 35.90 元，补测前另预留 0.08 元；全部调用原价估算 **0.0400266 元**。账单补验：**待出账**。15:29 查询（时间窗 09:29–15:29、账期 2026-10）：平台 ASR 40 次、17,358 Token，Omni 39 次、14,911 Token，合计 79 次、32,269 Token，与本地前 79 次逐项吻合；后补 4 次、1,733 Token 未显示。费用概览 ¥0 无本实验逐项明细，实付及免费额度抵扣均未核齐；本轮已停止查询。所有者决定不以账单出齐阻塞实验交付（D-091）。[服务条款](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)约定按指令处理、最小必要保存及未经授权不训练，未列固定保存天数。

产物均在 `workspace/audio-exp/`：[汇总](/Users/lestersun/IP-Video-Matcher/workspace/audio-exp/results-summary.json)、[全部上传清单](/Users/lestersun/IP-Video-Matcher/workspace/audio-exp/final-upload-manifest.csv)、[逐次调用](/Users/lestersun/IP-Video-Matcher/workspace/audio-exp/calls.jsonl)、[参照核查](/Users/lestersun/IP-Video-Matcher/workspace/audio-exp/final-ocr-review.json)、[账单查询出处](/Users/lestersun/IP-Video-Matcher/workspace/audio-exp/billing-followup-20261003-1529.json)、[pytest](/Users/lestersun/IP-Video-Matcher/workspace/audio-exp/pytest-final.log)。合并后主仓库 347 项测试通过；正式行文件及核验存储哈希未变，后台任务和子进程已退出。已并入 main（D-091）；未改生产、未 push，生产接入另行决定。
