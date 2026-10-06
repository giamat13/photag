# photag 10.0.0

## What is new
- **photag for Linux and macOS.** The release page now has `photag-10.0.0-linux-x64.tar.gz` and `photag-10.0.0-macos-arm64.zip` (Apple Silicon): unpack and run, no installer.
  * Not signed. On macOS, Gatekeeper asks once: right-click `photag.app` > Open. On Linux nothing asks; run `./photag/photag`.
  * Settings and data go to the system's usual folders, the background backup uses a systemd user timer (Linux) or a launchd agent (macOS), deleting for good sends files to the system trash, and API keys can use the system keychain.
  * Without a window toolkit (GTK / Qt on Linux) photag opens in your default browser and keeps running until you press Ctrl+C.
  * You can also run it from the source code: see `docs/LINUX_MACOS.md`.
  * Tested on GitHub's Ubuntu and macOS machines (the whole test-suite that does not need Windows, the browser UI tests on Ubuntu, and starting the packaged program). Not yet used on many real desktops: reports are welcome.
- **The install script shows progress.** `photag-install.ps1` / `.bat` now announce four steps and show a live bar for the download and the unpacking, instead of sitting silent for minutes. If Windows blocks the downloaded `.bat`, the README has a one-line PowerShell command and a copy-paste alternative.
- **Version numbers** are always written with three parts (`10.0.0`, next fixes `10.1.0`).

## Good to know
- Windows Smart App Control blocks every program that is not signed (including photag.exe, even when installed by the script). Tested in Windows Sandbox: a script, a self-made certificate or a signed Python do not help. A signed build is the only fix; see the README.
- The in-app update on Linux and macOS tells you about a new release and opens its page (no installer to run).

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
