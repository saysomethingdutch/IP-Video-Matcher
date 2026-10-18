# WO-111：扩量前Claude独立审计材料

准备于2026-10-18，**尚未派发、未完成审计，也不是80条启动许可**。依据交接第3节，由所有者说“让Claude审一下”启动。当前有D-100定向返修，先取得新提交和结果再作最终扩量建议；可先审现有规则分歧。

## 索引

- 授权与决定：主仓库 `docs/workorders/DECISIONS.md` D-094至D-100。所有者已授权100条推断及认可样稿口径，人工最终核验、安装和Gate扩量许可分别处理。
- 原要求和新指导：`WO-111-taipingnian-pilot.md`、`WO-111-GUIDANCE-RESULT.md`、`WO-111-R2-guidance-and-notes.md`；客户Excel、PPT、培训及群聊源材料只在本案移动硬盘。
- 开发工作树：`/Users/lestersun/.codex/worktrees/wo-111-taipingnian-pilot/IP-Video-Matcher`，分支 `codex/wo-111-taipingnian-pilot`。本阶段交付 `4f6c8e23be14a4f3bc7f025f08600e77bc5f2954`，返修后提交另补；不要把Gate文档主干当成已安装开发代码。
- 输出与Gate结论：`WO-111-R2-R1-RESULT.md`、`WO-111-R2-R1-GATE.md`、`WO-111-D099-S-REGRESSION-RESULT.md`、`WO-111-D099-S-GATE.md`、`WO-111-D100-S-corrections.md`。
- 外置盘根：`/Volumes/Element/ip-video-matcher-1015-round3-taipingnian/workspace/`。读取 `guidance/20261018/GUIDANCE-REVIEW.md`、`r2-r1/`、`r2-r1-s-regression/`；Gate独立证据在 `gate/20261018-WO111-R2-R1/` 与 `gate/20261018-WO111-D099-S/`。
- 只读入口：`http://127.0.0.1:8769/?row=1519`；标准对照 `/file/r2-r1-s-regression/comparison.html`。正式8767，不得保存或修改。

## 请独立回答

1. 作者加入防删风景/黑屏是否仍按群聊一般拼接处理；资源例外有无扩大，1512一致错判能否被拦。不要以Gate意见代替原要求核查。
2. 1514保留人工切条与通用规则诊断是否分开；1519及无形象reaction是否真正按说话来源区分，未靠关键词或长短强判。人工保护不计模型命中。
3. R/T正面定义、U边框事实与面积/时间分母、真实PTS和核准窗口保护是否继续有效。抽读完整可读输出，不只检查测试数。
4. 11条人工保存/10条已核、Q、客户原件和旧成功缓存是否受保护；6次新请求、新音频0及未知费用是否如实记录。余80清单中的5条人工行如何保护。
5. 给出“可进入80条只读暂存 / 定向修改后再审”及具体理由；不代所有者作最终核验，不安装、合并或调用模型。先提交独立报告供Gate处理。

可粘贴给Claude：

> 请独立审计WO-111扩量前版本。先读 /Users/lestersun/IP-Video-Matcher/docs/workorders/WO-111-PRE80-INDEPENDENT-AUDIT.md，按索引核实最新返修提交和完整输出，重点审一般拼接/资源例外、实时声音来源、人工保护及R/U来源限制。不要从头读对话，不改代码、数据或人工记录，不调用模型。交一页审计结论和明确阻塞项；本次不启动80条或安装。
