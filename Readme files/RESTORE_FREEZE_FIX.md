# Restore Freeze Fix

## Summary

Fixed a restore hang that happened while preparing `table_creation_sql/create_tables.sql` before PostgreSQL received any restore SQL.

The root cause was a large DOTALL regex used to remove legacy invalid `NOT NULL` constraint blocks. On larger DDL files, that regex could take a very long time and make the PyQt window show `Not Responding`.

## What Changed

- Replaced the expensive regex cleanup with a linear `DO $$ ... END $$;` block parser.
- Added a `Preparing restore SQL...` status step before applying DDL.
- Added restore-time injection for required UUID extensions such as `uuid-ossp`.
- Added restore-time enum type injection for older backup folders that contain enum columns but no `CREATE TYPE ... AS ENUM` statements.
- Added enum type and required extension generation for future backups.

## Verification

- `python -m compileall -q src main.py`
- Synthetic restore DDL check confirmed:
  - `CREATE EXTENSION IF NOT EXISTS "uuid-ossp";` is injected when `uuid_generate_v4()` is used.
  - Missing enum types are injected before `CREATE TABLE`.
  - Restore DDL preprocessing no longer depends on the slow regex path.

## Notes

The previously selected folder `C:\Users\sajith\Desktop\db\oclly_erp_backup_20260520_172521` was no longer present during the final local verification pass, so the exact folder could not be restored from this session after the fix. Restart the app and select an existing complete backup folder containing `manifest.json` and `table_creation_sql/create_tables.sql`.

## Risks

- For old backups, enum labels are inferred from DDL defaults and CSV values. If an enum column has no data and no default, the restored type may be created with no labels.
- PostgreSQL must allow `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"` when UUID defaults use `uuid_generate_v4()`.
