# photag 1.5.1

## What is new
- **The app now restarts by itself after an update.** After a code update photag closed, but the restart failed with the Windows message «Windows cannot find '\\'» and the app stayed closed until it was opened by hand (the update itself was fine). The restart command was quoted in a way `cmd.exe` does not understand; it is fixed, and a test now checks it on Windows. Because the restart is carried out by the version that is being replaced, updating *to* 1.5.1 may still show the message once; every update after that restarts normally.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
