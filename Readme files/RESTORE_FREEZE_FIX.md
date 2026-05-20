# Restore Freeze Fix

## Summary

Fixed a restore hang that happened while preparing `table_creation_sql/create_tables.sql` before PostgreSQL received any restore SQL.

The root cause was a large DOTALL regex used to remove legacy invalid `NOT NULL` constraint blocks. On larger DDL files, that regex could take a very long time and make the PyQt window show `Not Responding`.

## What Changed

- Replaced the expensive regex cleanup with a linear `DO $$ ... END $$;` block parser.
- Added a `Preparing restore SQL...` status step before applying DDL.
- Added restore-time injection for required UUID extensions such as `uuid-ossp`.
- Added restore-time enum type injection for older backup folders that contain enum columns but no `CREATE TYPE ... AS ENUM` statements.
- Added source-database enum lookup for old backups. If the source database from `manifest.json` is reachable on the same server, restore reads the real enum labels from that source before creating the target schema.
- Added enum type and required extension generation for future backups.
- Reordered generated constraints so primary keys, unique constraints, and check constraints are created before foreign keys.
- Split restore execution so schemas/tables/indexes are created first, CSV data is loaded next, and foreign keys are created only after all referenced rows exist.

## Verification

- `python -m compileall -q src main.py`
- Synthetic restore DDL check confirmed:
  - `CREATE EXTENSION IF NOT EXISTS "uuid-ossp";` is injected when `uuid_generate_v4()` is used.
  - Missing enum types are injected before `CREATE TABLE`.
  - Restore DDL preprocessing no longer depends on the slow regex path.
- `C:\Users\sajith\Desktop\db\oclly_erp_backup_20260520_173538` restore preparation was checked locally:
  - DDL preprocessing completed in about `0.063s`.
  - 79 enum type blocks were generated.
  - 1 UUID extension statement was generated.
  - 0 enum placeholder labels were needed because source enum labels were read from `oclly_erp`.
- `C:\Users\sajith\Desktop\db\oclly_erp_backup_20260520_175024` was restored into a temporary throwaway database named `codex_restore_verify_175024`:
  - Restore completed successfully in about `3.657s`.
  - 105 tables were restored.
  - 154 foreign keys were created after CSV data load.
  - 16 sequences were reset.
  - The temporary verification database was dropped afterward.

## Notes

Restart the app after this fix. A PyQt window opened before the code change will keep running the old restore logic.

## Risks

- For old backups, enum labels are read from the source database when it is reachable. If the source database is unavailable, labels are inferred from DDL defaults and CSV values. If an enum column has no data and no default, a placeholder value is used so schema creation can continue.
- PostgreSQL must allow `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"` when UUID defaults use `uuid_generate_v4()`.
