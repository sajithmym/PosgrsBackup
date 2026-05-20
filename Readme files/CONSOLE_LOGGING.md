# Console Logging Update

## Summary

Added console logging across the PostgreSQL Backup and Restore application so backup, restore, connection, UI, worker, folder, DDL, CSV, SQL insert, truncate, and sequence-reset flows can be traced from the terminal.

Logs follow the format:

```text
[MODULE] ACTION — key=value
```

The source uses `\u2014` for the separator so the console displays the requested dash while keeping the Python files stable.

## What Changed

- Added `src/pg_backup_app/logging_config.py` to configure DEBUG-level console output.
- Enabled logging from both `main.py` and `python -m pg_backup_app`.
- Added sanitized connection logs in `db.py`.
- Added operation, success, failure, and UI-state logs in `main_window.py`.
- Added backup and restore service logs in `backup_service.py`.
- Added restore-specific logs around DDL cleanup, missing sequence injection, table truncation, CSV restore, and sequence reset.

## Security

Passwords are never logged. Connection logs only show `password_set=True/False`.

No row data, CSV contents, SQL values, tokens, or secrets are printed.

## Verification

Run:

```powershell
python -m compileall -q src main.py
python main.py
```

Then perform:

1. Test connection.
2. Backup database.
3. Restore from a backup folder.
4. Check the terminal output for module/action logs.

## Trade-Offs And Risks

- DEBUG logging is intentionally verbose because this is a desktop troubleshooting workflow.
- Large databases will produce many table-level logs, but not row-level logs.
- Console logs are not written to a file. If persistent logs are needed later, add a file handler with rotation.

## Next Steps

- Add a UI toggle for log level if normal users find DEBUG logs too detailed.
- Add a copy/export console log option if support workflows need a packaged diagnostic bundle.
- Add timing metrics for long-running table exports and restores.
