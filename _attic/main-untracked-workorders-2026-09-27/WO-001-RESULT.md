# WO-001 交付说明

1. **运行命令（一条）**：`/opt/homebrew/bin/python3 -m pilot.ledger run pilot/cases/qsjx_e18.yaml && /opt/homebrew/bin/python3 -m pilot.ledger run pilot/cases/zhuyu_mah00002.yaml`。输出在 `workspace/pilot/`；第二个案例自动运行全帧和裁剪两种条件。
2. **自动指标**：七时吉祥 **161/169** 行关联到会话，检出 **165** 次跳转；8 条录像共 24,949 帧。首次 MVI_0006 加 421 张截图耗时 555 秒，其余录像全量跑批（复用该缓存）耗时 1,551 秒，合计约 **35 分 6 秒**，低于 60 分钟目标。逐玉 MAH00002：全帧 **12/65（18.5%）**，裁剪放大 **12/65（18.5%）**。这是自动结果；30 行正确率、取证时长误差和跳转漏检仍待 Owner 人工抽检。
3. **已知失败例**：1058 行与 1057 行同 BV、分 P 不同，缺少 1058 的可靠播放器读数，未复用 1057 会话（`workspace/pilot/qsjx_e18/evidence/failure-1058.jpg`）；1092 行的 EP.17 画面可见但未读到对应总长（`workspace/pilot/qsjx_e18/evidence/failure-1092.jpg`）；1135 行为竖屏小窗详情页，播放器读数过小，未关联会话（`workspace/pilot/qsjx_e18/evidence/failure-1135.jpg`）。另有 5 行未关联，均列于 `rows_without_session.csv`。
4. **需要 Owner 决定的问题**：截图能识别 BV、但录像没有可靠播放器读数时，是否可把截图附近的区间计为“已关联会话”？工单未给出计数口径；本次保守记为未关联，未凭截图推算取证时长。
5. **实际用时**：约 70 分钟（含归档、开发、OCR 运行与校验）。第 0 步归档已完成：外置盘压缩包校验 2,928 个文件；`archive/phase3` 提交 `037b2d1`，仅含 335 个符合限制的文本文件；未 push。
