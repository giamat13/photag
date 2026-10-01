# photag 1.0.0

A local Windows photo manager in the spirit of Lightroom Classic: your photos stay on your computer, no account, free (GPL-3.0).

## Highlights
- **Library:** grid, filmstrip and viewer; ratings, flags, colour labels, keywords, albums, smart search, people (face recognition), duplicates.
- **Import** from folders, Google Takeout (multi-part ZIPs supported) and Lightroom catalogs, with live statistics, speed, time left and cancel.
- **Map:** photos with GPS on an interactive map; select photos and see their pins.
- **Compress** photos and videos (one or many at once) with a progress screen and a safe undo.
- **Backups that do not forget:** every backup holds the catalog *and* its own complete copy of your photos and videos (unchanged files are hard
  links, so no extra disk space). Runs in the background with a scheduled task, a startup entry and the app itself; asks before deleting; restore from inside the app.
- **Safe auto-update** from GitHub with rollback and a "what's new" screen.
- **17 languages**, English by default.

## Good to know
- The installer is **not code-signed yet**. Windows Smart App Control may block it: see "Windows says the installer is blocked" in the README.
- AI tagging is optional and needs your own provider key; face recognition downloads its model (~300 MB) on first use.
- The default data folder is `C:\Users\<you>\Photag`.

Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
