# Schema v5 migration

Schema v5 is the Phase 2A.1 speed-range diagnostic migration.

## Change

`segment_matches` adds:

- `speed_out_of_range INTEGER NOT NULL DEFAULT 0`
- `unconstrained_speed_ratio REAL NULL`

`speed_ratio` remains nullable. When an observed line is reliably outside the configured
range, `speed_ratio` is null, `speed_out_of_range` is true, and the unconstrained value is
diagnostic only. Such a segment cannot be candidate GREEN.

## Migration contract

The explicit `v4 → v5` step runs inside `BEGIN IMMEDIATE`, adds missing columns
idempotently, and stamps `PRAGMA user_version=5` only immediately before the successful
commit. A failure rolls the step back and preserves all v4 rows. Before any migration or
repair, a SQLite backup named `<database>.pre-v6.bak` is created.

A database that claims v5 but lacks either column is treated as poisoned: initialization
inspects the actual table shape, repairs the missing columns atomically, validates them,
then proceeds to the next migration. It is never stamped v6 before the v5 invariant is
satisfied.
