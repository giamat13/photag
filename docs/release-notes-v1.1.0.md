# photag 1.1.0

## What is new
- **Automatic update check.** photag now checks GitHub for a newer version at start-up and then once a day while it is open, quietly in the background. A window appears only when a newer version exists; "Later" reminds you the next day and "Skip this version" silences that version. It can be switched off in the Preferences window ("Check for updates automatically once a day"). A failed check (no internet) is simply retried later.
- The update itself is unchanged: downloaded, verified (SHA-256), installed over the old version with automatic rollback if anything goes wrong. Your photos, catalog, backups and settings are not touched.

Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
