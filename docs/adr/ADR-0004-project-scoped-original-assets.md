# ADR-0004：Project-scoped Original Assets

状态：已接受，schema v3 原子迁移修订

## 决策

V1 一个 project 对应一部权利作品。`original_assets` 增加 `project_id` 外键，episode 唯一约束由全局 `UNIQUE(episode)` 改为：

```sql
UNIQUE(project_id, episode)
```

`accused_videos` 和 `segment_matches` 同样保存 `project_id`，避免多个项目之间的 case/episode 冲突。

## Episode 权威来源

1. `input/originals/metadata.json` 的 `episodes` 映射；
2. `EP01`、`E01`、`S01E01`、`第01集` 等明确 pattern；
3. 无法唯一解析时返回 `NEED_METADATA`。

不会从文件名中的任意数字推断集数。

## Migration

SQLite `PRAGMA user_version` 升至 2。迁移会重建受影响表、保留旧资产和匹配记录，把旧 `overall_confidence` 迁移到 `match_score`，并将旧 `mirrored/cropped` 改为 `NULL`。

旧索引没有 completeness 元数据，迁移后项目状态设为 `INCOMPLETE_INDEX`，必须重新运行 `index-originals` 验证，不能直接参与匹配。

Phase 1.5.1 将 SQLite `PRAGMA user_version` 升至 3，并规定：

- migration 使用显式 `BEGIN IMMEDIATE`；
- transaction 内逐条 `execute` DDL，不使用会隐式 COMMIT 的 `executescript`；
- 任一 checkpoint 失败必须 `ROLLBACK`，不得留下新旧表并存或数据只复制一半；
- migration 前使用 SQLite backup API 创建同目录 `.pre-v3.bak`；
- v2 的 candidate tier 与下游 review tier 拆分，旧 GREEN/YELLOW 对外迁为 `REVIEW_REQUIRED`。
