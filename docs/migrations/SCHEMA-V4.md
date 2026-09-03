# Schema V4 — Temporal Alignment diagnostics

状态：已实现

## 变更

`segment_matches` 新增：

- `fitted_offset_seconds`
- `residual_rms_seconds`
- `residual_max_seconds`
- `inlier_count`
- `candidate_count`
- `inlier_ratio`
- `source_reuse`
- `timestamp_basis`

这些字段保存 Temporal Alignment v2 的拟合诊断和时间戳语义。`source_reuse`
表示相隔的 accused segment 合法复用同一 source interval，不表示矛盾。
`timestamp_basis` 当前固定为 `nominal_grid_time`；它不是 authoritative media PTS。

## 原子性

初始化时读取 `PRAGMA user_version`。需要迁移且数据库非空时，先通过 SQLite backup
API 创建 `<database>.pre-v4.bak`（若该 backup 尚不存在），再显式执行
`BEGIN IMMEDIATE`。每个 `ALTER TABLE ... ADD COLUMN` 使用单独的
`connection.execute`，事务内不调用会产生隐式 commit 风险的 `executescript`。

任一步失败时执行 `ROLLBACK`；只有所有 schema、数据与索引操作成功后才把
`user_version` 写为 4 并 `COMMIT`。Regression test 在最后一个新增列之后注入失败，
验证所有新增列和同一事务内创建的表均不残留，`user_version` 仍为 3。

## 回滚与恢复

自动 rollback 处理单次迁移失败。如数据库本身损坏或需要人工恢复，关闭所有写入者
后使用 `.pre-v4.bak` 作为恢复来源。旧 source feature index 的身份和有效性由既有
cache/index 检查决定；schema v4 本身不更换 DCT encoder 或采样 FPS。
