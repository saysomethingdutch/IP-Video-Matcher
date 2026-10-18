# WO-111：扩量前Claude独立审计材料

2026-10-18更新。**D-101定向返修已由Gate通过（D-102），可作扩量前独立审计；Claude尚未派发/审完，80条不启动、不安装。** 按交接第3节，由所有者说“让Claude审一下”启动。审计报告交Gate处理，不自动开始下一批。

## 冻结版本与索引

- 开发工作树：`/Users/lestersun/.codex/worktrees/wo-111-taipingnian-pilot/IP-Video-Matcher`，分支 `codex/wo-111-taipingnian-pilot`，交付 **`4b291fbb7f9ef807e431ae0e2647d36f7ba4221f`**。规则 `D101-S-source-v3` / `D101-resource-context-v1`。主干只有Gate文档，未安装开发代码。
- 授权与决定：主仓库 `docs/workorders/DECISIONS.md` D-094至D-102。所有者已授权100条推断、认可样稿口径；人工最终核验、扩量暂存、安装分别处理。本次审计不调用模型。
- 要求与来源：`WO-111-taipingnian-pilot.md`、`WO-111-GUIDANCE-RESULT.md`、`WO-111-R2-guidance-and-notes.md`。客户Excel、PPT、培训和群聊源材料只在本案移动硬盘，不当作操作授权。
- 最新交付与Gate结论：`WO-111-D101-RESULT.md`、`WO-111-D101-GATE.md`。前轮修复及限制参照 `WO-111-R2-R1-GATE.md`、`WO-111-D100-S-GATE.md`、`WO-111-D101-resource-gate.md`；不要从头读对话。
- 外置盘根：`/Volumes/Element/ip-video-matcher-1015-round3-taipingnian/workspace/`。最新交付 `d101-resource-gate/`：`delivery.json`、`PRE80-AUDIT-HANDOFF.md`、`artifact-manifest.json`、`source-stage-replays.json`、`actual-stage-replays.json`、`genuine-positive-replay.json`、`legacy-purpose-disagreement.json`、`source-supplement/`、`inputs/`、`current-resource-audits/`、测试和用量。历史保留于 `r2-r1/`、`r2-r1-s-regression/`、`d100-s-corrections/`。
- Gate独立证据：`gate/20261018-WO111-D101/verification.json` 及同目录复验脚本；前轮在 `gate/20261018-WO111-R2-R1/`、`gate/20261018-WO111-D099-S/`、`gate/20261018-WO111-D100/`。指导原文及培训转写见 `guidance/20261018/GUIDANCE-REVIEW.md`。
- 页面：`http://127.0.0.1:8769/?row=1512`；当前审计 `/file/d101-resource-gate/resource-audit.html`。正式8767，旧R2为8768；不得保存或修改。

## 独立审计问题

1. 对照原群聊图2、培训与D-096，核查作者后接防删风景/黑屏仍按一般拼接，平台界面与解说比较引用另辨。资源例外是否依据本行实际推广、目标剧及片段招揽关系，而非OCR词命中。
2. 重查5个反例和1544真实推广正例，尤其实际S/T采用分支。正例借1606 R/存储骨架验证，不是1544整行稿。GLM根对象包裹是否只改结构、原回复/初次audit是否保留；两家用途或原创表达冲突如何保留通常类别候选。Gate合成反例不是客户视频新观测。
3. 核实1514人工切条与通用规则诊断分开、1519人工reaction保留且说话来源仍未解；旧成功缺新字段不当作当前通过，人工保留不算模型命中。不要要求所有者重核已保存行。
4. 抽读完整R/T/U输出：正面类别定义、边框事实与面积分母、阶段时钟、真实PTS及核准整窗口是否继续有效。1602 R、1576面积分母、部分覆盖/分P和原视频阶段起止等D-099限制不得被验收措辞抹去。
5. 核对11条人工保存/10条已核、Q、原件、旧缓存和14条样稿保护。用量按轮次分开：D-099来源诊断6次、D-100零新增、D-101补问2次2610 tokens；D-101新音频0，实付未知。459项测试在seed0下通过，旧灰度碰撞问题的失败/复现日志应保留。
6. 裁定“可进入80条只读暂存”或给具体阻塞项。80清单含5条人工行（1513、1515–1518）须保留，75条才可形成替换候选；S/T有分歧不静默选一家，R/U无可靠来源不造数。暂存不等于安装、最终导出或所有者通过。

请输出不超过1页审计报告：所审提交、结论、具体问题/位置、证据和适用限制。只读，不改代码或人工记录，不调用模型、不启动80条、不安装/合并。无需重做Gate已完成的全部机械校验，重点独立读来源和输出。

可粘贴给Claude：

> 请独立审计WO-111扩量前版本4b291fb。先读 /Users/lestersun/IP-Video-Matcher/docs/workorders/WO-111-PRE80-INDEPENDENT-AUDIT.md，按索引核查来源、实际采用路径和完整输出，给一页结论及具体阻塞项。只读，不改代码/数据或人工记录，不调用模型；不启动80条或安装。报告交Gate处理。
