# ADR-0004：Project-scoped Original Assets

状态：已接受，schema v2 已实现

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
