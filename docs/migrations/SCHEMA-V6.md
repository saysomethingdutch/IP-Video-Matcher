# Schema v6 migration

Schema v6 is the Phase 2A.2 boundary-completeness and segment-cap propagation migration.

## Change

`segment_matches` adds:

- `boundary_completeness TEXT NOT NULL DEFAULT 'UNKNOWN'`
- `boundary_completeness_reason TEXT NULL`
- `segment_cap_truncated INTEGER NOT NULL DEFAULT 0`

`accused_videos` adds:

- `truncated_episodes_json TEXT NOT NULL DEFAULT '[]'`
- `analysis_warnings_json TEXT NOT NULL DEFAULT '[]'`

Old rows remain valid and conservatively receive `UNKNOWN`, not `FULL`.

## Migration and repair matrix

| Input | Required path | Success state | Failure state |
|---|---|---|---|
| genuine v3 | v3 → v4 → v5 → v6 | v6; rows preserved | failing step rolls back; prior completed step remains |
| genuine v4 | v4 → v5 → v6 | v6; rows preserved | v5 failure remains v4; v6 failure remains valid v5 |
| genuine v5 | v5 → v6 | v6; rows preserved | remains valid v5 |
| poisoned v5 | repair v5 → validate → v6 | v6; rows preserved | repair rollback; remains poisoned v5 and initialization fails |
| poisoned v6 columns | repair v6 → validate | valid v6 | repair rollback and loud failure |
| required table missing | no table reconstruction | no silent success | backup plus `SchemaIntegrityError` |
| user_version > 6 | none | none | immediate `SchemaIntegrityError` |

Every migration step has a separate function and transaction. The implementation avoids
`executescript` inside migration transactions because it may cause an implicit commit.
Version stamping occurs only in the same transaction as the successful step. Repair is
idempotent and followed by schema introspection; an absent required table is not guessed or
silently recreated.
