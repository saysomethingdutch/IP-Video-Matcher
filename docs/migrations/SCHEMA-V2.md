# SQLite Schema Migration v2

执行入口：`Database.initialize(project_root)`。

主要变更：

- `PRAGMA user_version = 2`；
- `original_assets.project_id`；
- `UNIQUE(project_id, episode)`；
- project/index completeness 字段；
- `overall_confidence` → `match_score`；
- `mirrored/cropped` 改为 nullable；
- segment candidate rank、primary/alternative、residual、coverage、uniqueness margin、review tier；
- accused video status/reason 与 project scope。

旧索引迁移后标记 `INCOMPLETE_INDEX`。用户必须重新执行：

```bash
python -m ipvm index-originals --force
```

只有重新验证为 `COMPLETE` 的索引才能参与分析。
