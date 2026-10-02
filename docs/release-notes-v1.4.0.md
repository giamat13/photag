# photag 1.4.0

## What is new
- **Portable version.** Download `photag-1.4.0-portable.zip` from this release instead of `photagSetup.exe`: no installer, no admin rights,
  nothing written to the PC it runs on. Extract it anywhere — a USB stick, a folder, a synced drive — and run `photag.exe` from there. The
  catalog, settings and backups live in a `data` folder right next to the EXE; move or copy the whole extracted folder (with photag closed) and
  it keeps working, on any PC. It is a good option if `photagSetup.exe` is blocked by Smart App Control on a given machine, though the portable
  EXE itself can still be blocked the first time it runs there too — only code signing removes that for good.
- The portable version does not register the background backup scheduled task (there is no stable path to point it at) and does not offer
  installer-based auto-update (there is no installed location to replace); a code update (small, no installer) still applies normally.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
