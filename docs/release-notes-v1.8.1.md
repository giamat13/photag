# photag 1.8.1

## What is new
- **Tester mode: a big PRE-RELEASE banner.** When the update window (or the "What's new" window) is about a pre-release, it now shows a large orange **PRE-RELEASE** banner and says plainly that it is a test version that may still have bugs.
- **A manual update check now tells you about pre-releases even when Tester mode is off.** The automatic check stays quiet (as before), but *Help → Check for updates* will say that a newer pre-release exists, show its notes and let you open its page. It is never installed from there -- turn Tester mode on in Preferences to be offered pre-releases and install them. If a regular update is also available, its window mentions the newer pre-release too.
- **The installer is also published inside a ZIP** (`photagSetup-1.8.1.zip`), next to the usual `photagSetup.exe` (which is unchanged, and is still what in-app updates use). If Windows blocks the `.exe` ("An Application Control policy has blocked this file"), try the ZIP: extract it and run the installer from the extracted folder. Whether that helps depends on your Windows protection settings -- if it does not, see the README section on Smart App Control.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
