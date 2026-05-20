# UI Update Summary

## Summary

The app now has a more complete desktop workflow: persistent theme selection, remembered non-secret settings, larger readable status output, explicit folder selectors, and advanced action buttons for test connection, opening the last backup, and copying status text.

## What Changed

- Removed the previous file-based debug trace layer.
- Added System, Light, and Dark theme modes.
- On Windows, System theme now reads the Windows app-theme registry value because Qt can report a light palette even when Windows is in dark app mode.
- Saved the selected theme with `QSettings`; default mode is System.
- Remembered host, port, user, database, backup save folder, restore folder, and last backup folder.
- Kept password out of saved settings.
- Rebuilt the UI with card panels instead of group boxes, so the connection form and buttons remain visible.
- Increased window size and status console height.
- Moved status actions into the Status card header so the log console is not hidden behind bottom buttons.
- Added `Test Connection`, `Open Last Backup`, and `Copy Status` buttons.
- Kept backup and restore service behavior unchanged.

## Trade-offs and Risks

- System theme is detected when the app opens. If the OS theme changes while the app is already open, the user can choose System again or restart the app.
- Saved settings are local user settings through Qt, not a project config file.
- The on-screen status panel remains the main observability surface after removing debug/file logs.

## Next Steps

- Add schema/table selection before backup.
- Add row-count progress percentages.
- Add optional `pg_dump` mode for full PostgreSQL object fidelity.
