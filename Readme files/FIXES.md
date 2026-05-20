# Fixes Summary

## Summary

This update fixes two user-facing issues: clipped text inside connection input fields and SQL export failure on PostgreSQL JSON/JSONB values. A generated database-style app icon was also added to make the desktop window easier to identify.

## Root Causes

### Input Text Clipping

The `QLineEdit` stylesheet used large padding without reserving enough input height. On Windows, values such as host, port, user, and database could render partially hidden inside the text boxes.

### `can't adapt type 'dict'`

The backup failed in `BackupService._export_table_inserts()` at the `cur.mogrify(...)` call. PostgreSQL `json/jsonb` columns can be returned by psycopg2 as Python `dict` values, and raw dictionaries cannot be adapted into SQL literals without wrapping them as JSON.

## What Changed

- Added a generated database-style window icon in `MainWindow`.
- Increased `QLineEdit` height and adjusted padding so typed text is visible.
- Added JSON/JSONB column detection before SQL insert export.
- Wrapped values from JSON/JSONB columns with `psycopg2.extras.Json` before `mogrify()`.
- Added missing semicolons to generated `CREATE INDEX` statements.
- Skipped PostgreSQL `NOT NULL` pseudo-constraint blocks during DDL generation because `NOT NULL` is already carried on the column definition.
- Added restore-time DDL cleanup so older backups generated before this fix can still be restored.
- Kept CSV export and restore behavior unchanged.

### Restore DDL Syntax Error

Restore failed at:

```text
syntax error at or near "CREATE"
```

The generated `create_tables.sql` had consecutive `CREATE INDEX` statements without `;` terminators. Older generated files could also include invalid standalone `ALTER TABLE ... ADD CONSTRAINT ... NOT NULL column` blocks. Restore now normalizes those older DDL files before executing them.

## Verification

Run:

```bash
python -m compileall -q src main.py
python main.py
```

Manual check:

1. Confirm text is fully visible in Host, Port, User, Password, and Database fields.
2. Run backup again against `oclly_erp`.
3. Confirm the export passes the table that previously failed, `public.employee_types`.
4. Confirm backup completes and writes `manifest.json`, `csv`, `sql`, and `table_creation_sql`.

## Trade-offs and Risks

- JSON wrapping is intentionally limited to columns PostgreSQL reports as `json` or `jsonb`, so normal array/list values keep their existing behavior.
- The icon is generated in code to avoid adding image dependencies.

## Next Steps

- Add an integration test database containing JSON, JSONB, array, and plain text columns.
- Add optional log rotation because debug logs can become large during full database backup.
