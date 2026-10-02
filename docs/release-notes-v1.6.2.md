# photag 1.6.2

## What is new
- **Storage.** File → Storage... shows where your library's disk space goes -- by folder, by year and by file type -- so you can decide what to archive or delete. Trashed photos are reported separately (they still take up space until you empty the Trash).

## Fixed
- **A background backup could crash with "No module named 'concurrent'".** A lightweight code update (no installer) can only replace the app's Python source, not add a library an older install's program file never bundled -- which is what happened here, going unnoticed since nothing checked for it before. Backups, photo analysis and AI tagging now fall back to running one item at a time instead of crashing when that happens, and a new check catches this category of problem before it ships again.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
