# photag 1.3.0

## What is new
- **Updates that Windows cannot block.** From this version on, most updates are a small "code update" (about 1 MB): photag downloads it, checks its SHA-256 signature, and replaces only its own Python code and web pages, never an executable file. Windows features that block unknown or unsigned programs (Smart App Control) have nothing new to block, so updating works even on PCs where installing a new `photagSetup.exe` is refused. The app restarts by itself afterwards, and the "What's new" window shows the notes.
- **Safe by design.** The previous code is kept (one step back), and if the new code cannot start three times in a row, photag puts the previous one back automatically. Updates that change the libraries inside photag are still delivered as a normal installer.
- **A clearer message when Windows blocks the installer.** photag says so, keeps running, and opens the folder with the downloaded file instead of a confusing "file not found".

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
