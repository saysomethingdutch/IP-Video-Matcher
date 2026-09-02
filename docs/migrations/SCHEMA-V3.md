# Schema v3 migration

Schema v3 separates the internal diagnostic `candidate_tier` from the effective downstream `review_tier`, adds segment conflict fields, and makes `speed_ratio` nullable for short fits.

Migration behavior:

1. Create `<database>.pre-v3.bak` with the SQLite backup API if a migration is required and no backup exists.
2. Disable foreign-key enforcement before the transaction boundary.
3. Start `BEGIN IMMEDIATE`.
4. Execute each DDL statement individually; `executescript` is not used in the migration transaction.
5. Copy and validate rows, set `PRAGMA user_version=3`, then commit.
6. On any exception, issue `ROLLBACK`. The original schema and data remain intact.

Old GREEN and YELLOW rows retain their former value as a diagnostic candidate tier where possible, but their effective `review_tier` becomes `REVIEW_REQUIRED`. This prevents migrated data from silently acquiring automatic-release semantics.
