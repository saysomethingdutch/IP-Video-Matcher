# Architecture Revision 0.1.1

状态：生效

日期：2026-09-02

本修订补充 Architecture Baseline v0.1，不改写历史文档。若与 v0.1 冲突，以本修订为准。

## 1. 阶段裁定

Architecture v0.1 §16 的镜像、0.75–1.5× 变速、字幕和裁剪指标，是 Phase 2 结束时的 Gate，不是 Phase 1 Gate。

Phase 1 只验证直接剪切、常规重编码、基础多段拼接，以及：

```text
被诉时间轴 → 集数 → 原片时间轴
```

Phase 1 独立 Review 的裁定为 `CONDITIONAL PASS`。Phase 2 开始前新增 Phase 1.5：Measurement & Safety Hardening。本阶段只修 benchmark、状态语义、索引完整性、数据作用域和审计链，不增加匹配能力。

## 2. 匹配状态

匹配结果必须使用以下一等状态：

- `MATCH_FOUND`
- `MATCH_NOT_FOUND`
- `AMBIGUOUS`
- `INCOMPLETE_INDEX`
- `ERROR`

等价竞争候选不能唯一消歧时返回 `AMBIGUOUS` 和主要候选。项目索引不完整时返回 `INCOMPLETE_INDEX`，且不得生成 `MATCH_FOUND`。

## 3. Match Score 与放行条件

原 `overall_confidence` 更名为 `match_score`。它只表示内部匹配证据强度，不是结果正确概率。Phase 1.5 不训练概率校准器。

GREEN 不能由单一分数阈值触发。它至少要求索引完整、无等价竞争者、唯一性 margin 达标、时间残差达标、coverage 与 temporal consistency 达标、无 segment contradiction。阈值定义见 ADR-0003，均属于 synthetic regression threshold，不得解释为概率。

## 4. 项目作用域

V1 中一个 project 对应一部权利作品。`original_assets` 必须引用 `project_id`；episode 唯一性是 `UNIQUE(project_id, episode)`。

集数来源顺序：

1. `input/originals/metadata.json` 显式映射；
2. 明确且唯一的 filename pattern；
3. 无法唯一解析时 `NEED_METADATA`，不得使用任意数字猜测。

## 5. Golden Set

真实 golden set 放在 `input/golden/`，与 synthetic benchmark 共用 ground-truth schema。真实视频、案件名称、文件 hash 和详细案件结果不得提交 Git。

## 6. Phase 1.5 Gate

在 ADR-0005 指标下：

- off-grid direct cut/re-encode：source-aware recall@1s ≥ 95%；
- segment precision@1s ≥ 98%；
- source boundary MAE ≤ 1.0 秒，且漏检必须单独报告；
- pure negative：false-positive matched seconds = 0；
- exact duplicate cross-episode：`AMBIGUOUS` 或合法上下文消歧；
- incomplete index：必须拒绝，不能产生 `MATCH_FOUND`。

这些是 synthetic regression gates，不是对真实案件准确率的声明。

## 7. 暂停项

Phase 1.5 完成后停止，等待第二次独立 Review 和架构负责人 Gate 决策。DINOv2、SigLIP、FAISS、Temporal Alignment v2、镜像、变速、crop、audio、ASR、Gemma、合理使用和 Excel 均不得在本阶段实现。
