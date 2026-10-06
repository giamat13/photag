# photag on Linux and macOS

photag is built for Windows (the installer and the portable ZIP are Windows programs). On **Linux and macOS you run it from the source code**.
Everything except the Windows-only parts works: import, catalog, search, edit, export, backup, the web UI.

## Run it
```
git clone https://github.com/giamat13/photag.git && cd photag
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python photag.py
```
* **Window:** photag opens its own window with `pywebview`. On macOS that just works. On Linux it needs a toolkit (`pip install "pywebview[gtk]"`
  plus the system packages `python3-gi gir1.2-webkit2-4.1`, or `pywebview[qt]`). **Without one photag opens in your default browser** and keeps running
  in the terminal until you press Ctrl+C -- the same app, no window frame.
* **Data:** settings in `~/.config/photag` (Linux, `$XDG_CONFIG_HOME`) or `~/Library/Application Support/photag` (macOS); your library in `~/Photag`.
* **Background backup:** a systemd *user* timer (Linux) or a launchd agent (macOS) runs the backup every hour even when photag is closed. It is set up
  by the app itself when backups are on (Linux needs `systemctl --user`).
* **Deleting for good** moves the file to the system trash (`~/.local/share/Trash`, or `~/.Trash`).
* **API keys** (AI tagging, triplan) go into the system keychain when `pip install keyring` is done and a keychain exists; otherwise they are only
  base64-wrapped in the settings file, like a development build on Windows.
* **Updates:** git pull. The in-app update check tells you about a new release but does not offer the Windows installer.

## What is not there
* No packaged app (AppImage, `.deb`, `.dmg`) and no Apple signing: a signed macOS app needs a paid Apple developer account. Tracked in a separate issue.
* Casting to a TV (Win+K), the Windows installer/portable ZIP and Smart App Control notes are Windows only.

## What is tested
The *tests* workflow runs the tests that do not need Windows on **Ubuntu and macOS** (GitHub's runners), and the browser UI tests on Ubuntu with Chromium.
It has not been used on a real Mac or on many Linux desktops: reports are welcome.
