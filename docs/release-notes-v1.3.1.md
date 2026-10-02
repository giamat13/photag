# photag 1.3.1

## What is new
- **Updates that Windows cannot block.** From this version on, most updates are a small "code update" (about 1 MB): photag downloads it, checks its SHA-256 signature, and replaces only its own Python code and web pages, never an executable file. Windows features that block unknown or unsigned programs (Smart App Control) have nothing new to block, so updating works even on PCs where installing a new `photagSetup.exe` is refused. The app restarts by itself afterwards, and the "What's new" window shows the notes.
- **Safe by design.** The previous code is kept (one step back), and if the new code cannot start three times in a row, photag puts the previous one back automatically. Updates that change the libraries inside photag are still delivered as a normal installer.
- **Photos stay in my folder (optional, off by default).** For people who already have a folder structure that is not all photos: in *Preferences* choose a folder and photag lists its image files where they are, keeping the list up to date (new files appear, moved files keep their ratings and keywords, deleted files disappear). photag never moves, copies, renames or changes those files; the catalog, thumbnails and backups stay in its own folder. Only when you delete a photo from the trash for good, its file goes to the Recycle Bin.
- **A clearer message when Windows blocks the installer.** photag says so, keeps running, and opens the folder with the downloaded file instead of a confusing "file not found".
- The EXE now carries correct version info (Explorer's "Details" tab showed 0.0.0.0 before; this fixes v1.3.0, whose release failed to publish because of a bug in the build pipeline).

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
