# Phase 2A.2 Final Closure Report

日期：2026-09-04

基线：`80036d8 Complete Phase 2A.2 integrity and boundary closure`

审查输入：`docs/review/PHASE2A.2-INDEPENDENT-REVIEW.md`，裁定为
`CONDITIONAL PASS`，仅要求关闭 M-1 与 M-2。

本提交不自行宣布 Phase 2A PASS。最终状态由独立 reviewer 与 Architecture Owner 决定。

## 1. Scope

本次只修改：

- M-1：同一 temporal line 被 1.5 秒 retrieval hole 切开时的 merge 与 boundary safety；
- M-2：Ideal benchmark `passed` 的完整 Gate 语义；
- 对应 regression、tracked ideal artifact 和紧邻文档事实修正。

未修改 visual backend、FPS、top-k、sparse voting、robust fitting、hypothesis extraction、
piecewise architecture、evaluator 或 ground truth。未进入 VDF、DINOv2、SigLIP、FAISS、
Phase 3、audio、Gemma 或 Excel。

## 2. M-1 exact fixes

### 2.1 Slope-aware merge continuity

删除 `_merge_adjacent()` 中固定的：

```text
abs(current.source_start - previous.source_end) <= 1.5
```

替换为：

```text
accused_gap = current.accused_start - previous.accused_end
representative_slope = mean(previous_slope, current_slope)
expected_source_gap = representative_slope * accused_gap
observed_source_gap = current.source_start - previous.source_end

abs(observed_source_gap - expected_source_gap)
    <= candidate_timestamp_tolerance_seconds
```

容差复用现有 source-grid uncertainty，没有新增 speed-specific 常数。Merge 仍同时要求：

- 同 episode；
- accused gap 不超过 `maximum_frame_gap_seconds`；
- `_same_temporal_line()` 为 true；
- union refit `inlier_ratio >= merge_minimum_inlier_ratio`。

因此 slope scaling 不能单独触发 merge。真实 source pause、不同 temporal line 与
0.8→1.25 discontinuity 仍被拒绝。

### 2.2 Boundary-completeness neighbour exclusion

原实现会排除落在任一同 episode sibling segment 内的 orphan candidate。现在只有 sibling
属于不同 temporal line 时才排除：

```text
same episode AND NOT same temporal line AND candidate inside sibling span
```

若同线 fragments 因其他合法原因保持分离，它们不再互相屏蔽 orphan evidence。Fallback
regression 强制跳过 merge 后，两段均为：

```text
boundary_completeness = INCOMPLETE
candidate_tier        = YELLOW
review_tier           = REVIEW_REQUIRED
```

没有增加 boundary extrapolation。

## 3. Same-line hole speed sweep

输入为一条 24.5 秒真实 temporal line，删除 accused grid 上连续三个 frame，形成 1.5 秒
accused-side retrieval hole。

| speed | before segments | after segments | after accused span |
|---:|---:|---:|---|
| 0.75 | 1 | 1 | 0.0–24.5 |
| 0.80 | 1 | 1 | 0.0–24.5 |
| 1.00 | 1 | 1 | 0.0–24.5 |
| 1.25 | 2 | 1 | 0.0–24.5 |
| 1.50 | 2 | 1 | 0.0–24.5 |

五档最终均为单段。负例验证：

- 0.8→1.25 speed discontinuity 保持两段；
- 同 speed 但 accused gap 内 source timeline pause 的两段不 merge；
- 不同 temporal line 不 merge；
- merge 被测试替身阻止时，同线 fragments 按 fallback 标为 INCOMPLETE / non-GREEN。

## 4. M-2 Ideal Gate integrity

新增明确的 required assertion 集合：

```text
pure_noise_produces_no_segments
no_clamped_speed_reported
out_of_range_semantics_complete
speed_discontinuity_source_aware
competing_lines_are_ambiguous
short_source_span_speed_is_null
```

`passed` 现在必须同时满足：

1. gating aggregate recall 与 precision 均为 1.0；
2. 上述每个 required behavioural assertion 都严格为 `true`；
3. `source_grid_invariant.all_candidates_on_grid` 严格为 `true`。

缺少 required assertion 也会失败，不会由 `.get(..., True)` 静默通过。Regression 分别验证：

- perfect aggregate + failed speed-discontinuity assertion → `passed=false`；
- perfect aggregate + failed source-grid invariant → `passed=false`；
- perfect aggregate + all assertions + grid valid → `passed=true`；
- perfect aggregate + missing required assertion → `passed=false`。

Fresh ideal result：

| 项目 | 结果 |
|---|---:|
| gating cases / GT segments | 12 / 16 |
| TP / FP / FN | 16 / 0 / 0 |
| precision / recall | 1.0 / 1.0 |
| candidate timestamps on grid | 1824 / 1824 |
| required assertions true | 6 / 6 |
| `passed` | true |

Tracked artifact 已由当前代码重新生成：
`benchmark/temporal/results/phase2a-temporal-ideal-v0.1.json`。

## 5. Tests

| 验证 | 结果 |
|---|---:|
| M-1 + out-of-range focused | 12 passed |
| Ideal Gate focused | 2 passed |
| full pytest | **119 passed in 6.94s** |

新增 regression 包括五档 same-line hole、slope-aware negative content gap、boundary fallback、
Ideal Gate 三项组合与 missing-assertion fail-closed。另增加 0.72 / 0.74 / 1.51 / 1.53
near-endpoint diagnostics：当前 2 FPS 下 0.72 与 1.53 可识别为 out-of-range 并降级；0.74
与 1.51 落入端点量化带，但 production tier 始终为 `REVIEW_REQUIRED`。

## 6. Real 120-case before / after

配置保持：2 episodes × 10 independent content positions × 6 paired profiles，source/query
2 FPS，top-k 8。120 cases 不是 120 个独立内容样本。

| 指标 | `80036d8` | final closure |
|---|---:|---:|
| TP / FP / FN | 109 / 11 / 11 | 109 / 11 / 11 |
| precision / recall | 0.9083 / 0.9083 | 0.9083 / 0.9083 |
| episode correctness | 0.9417 | 0.9417 |
| source boundary MAE | 0.1993 s | 0.1993 s |
| CORRECT / SAFE / UNSAFE / NO_MATCH | 96 / 20 / 4 / 0 | 96 / 20 / 4 / 0 |
| boundary UNKNOWN / INCOMPLETE | 116 / 4 | 116 / 4 |
| any-match / full-boundary sufficient | 120 / 110 | 120 / 110 |
| content positions without unsafe | 19 / 20 | 19 / 20 |
| matcher runtime sum | 32.67 s | 31.94 s |

没有功能或性能回归。Evaluator、GT 与 metric definitions 未改。

### E001-I008

Direct、P1、P2、P3 四档均保持：

```text
confirmed accused span       2.5–6.0
confirmed source span        1870.0–1873.5
boundary_completeness        INCOMPLETE
candidate_tier               YELLOW
review_tier                  REVIEW_REQUIRED
retrieval any-match          true
retrieval full-boundary      false
boundary assertion           CONTAINED_PARTIAL_EVIDENCE
functional outcome           UNSAFE_WRONG_MATCH
```

M-1 没有外推或掩盖该头部缺失，也没有用 boundary taxonomy 提高 recall。

## 7. Documentation corrections

- `change_point_minimum_side_seconds=3.0` 明确为 fit-side support threshold，不是 3 秒 regime
  必然可恢复；当前 2 FPS 下观察到的 recoverability 约为 4–6 秒，取决于 slope separation。
- ADR-0002 记录 change-point refinement 对单个不相容 segment pair 的超线性成本；当前 cap
  使规模有界，但 real 6 秒 query 的性能不能外推到长 query。
- 上一份 closure report 的 DB 表述收窄：未迁移的是 `real-retrieval` source-index DB；默认
  空 workspace DB 与 benchmark demo DB 曾被测试流程迁移，但不含案件数据。

## 8. Remaining known limitations

本 patch 没有处理下列已知限制：

1. 约 4–6 秒以下的短 speed regime 可能被 extraction 吞并；修复需要超出本次 scope。
2. 多个交替 regime 与长 query 的 change-point 成本仍需后续独立 characterization。
3. `BoundaryCompleteness.FULL` 仍没有 production negative-boundary evidence 规则；无法证明时
   保持 `UNKNOWN`。
4. 0.74 / 1.51 一类 near-endpoint speed 在当前 2 FPS grid 上可能量化为配置端点。
5. Coherent wrong visual line 仍可能成为 candidate GREEN；production 自动放行继续禁用。
6. Synthetic ambiguity 与 DCT visual discrimination 限制未在本 patch 修改。
7. Endpoints 仍为 `nominal_grid_time`，未映射到 authoritative media PTS，不能直接法律交付。

## 9. Stop condition

M-1、M-2、focused regression、fresh ideal 与 fresh real 120-case 已完成。此处停止，等待
focused independent verification 与 Architecture Owner 最终裁定。
