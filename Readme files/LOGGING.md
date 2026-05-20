# Logging Summary

## Summary

The application now uses Python's standard `logging` library for runtime observability. Logs are written to `logs/postgres_backup_app.log` and also emitted to the console.

## Format

Messages follow the requested format:

```text
[MODULE] ACTION — key=value
```

Examples:

```text
[BackupService] backup_database — database=mydb, destination_parent=D:\Backups
[Database] connect_to_database — success database=mydb
[MainWindow] _operation_failed — message=<error>
```

## Areas Covered

- Application startup and shutdown.
- Main window initialization and UI construction.
- Backup and restore button actions.
- Sanitized connection config validation.
- PostgreSQL connection attempts and failures.
- Backup folder creation, table discovery, DDL generation, CSV export, SQL export, and manifest writing.
- Restore folder validation, manifest loading, DDL execution, CSV restore, transaction commit, rollback on error.
- Worker-thread success and failure paths.

## Sensitive Data Policy

Passwords are never logged. The logs only record `password_set=True` or `password_set=False` so a developer can diagnose missing credential input without exposing the actual secret.

## Trade-offs and Risks

- Debug logging is intentionally detailed and may grow quickly for databases with many tables.
- Logs include filesystem paths, database name, host, port, user, schema names, and table names. These are useful for diagnosis but may still be considered sensitive in some environments.
- SQL data values are not logged.

## Next Steps

- Add a UI setting for log level selection.
- Add log rotation so long-running usage does not create oversized log files.
- Add a "Open Logs Folder" button for support workflows.

## Deferred Items

- No structured JSON logging was added because the current app is a desktop utility and plain text logs are easier to read manually.
- No external logging dependency was added to keep the project small and dependency risk low.
