# photag — a local photo manager

A photo and video manager for Windows in the spirit of Lightroom Classic: catalog, collections, flags, ratings,
color labels, keywords, face recognition and non-destructive editing (the original is always kept).
The interface is available in 17 languages (English by default). A Hebrew version of this file is in [README.he.md](README.he.md).

## What's inside
- **Lightroom Classic–style interface** — Library / Develop / Slideshow modules; grid, loupe, compare, survey and people views;
  filmstrip, library filter and the Lightroom keyboard shortcuts.
- **AI tagging (optional, with your own key)** — an "AI tagging" button next to the keywords. Providers: OpenAI, Claude (Anthropic),
  Gemini (Google), OpenRouter (one key for dozens of providers) and any OpenAI-compatible server (Groq, Together, local Ollama…).
  The model is **automatic** (a cheap, fast vision model is picked from the provider's live list) or chosen by hand. Only a small
  thumbnail (512 px) of each photo is sent, and only while tagging runs. You can choose the keyword language, tag the selection or all
  photos without keywords, and stop at any time. The API key is stored encrypted (Windows DPAPI) and is never sent back to the UI.
- **Video compression with HandBrake** — a button in the player. If HandBrakeCLI is not installed, its download page opens; otherwise you
  choose compression strength and speed with two sliders (with a plain-words description of what was chosen), and in "Advanced" mode the
  encoder (H.264 / H.265 / AV1), quality, resolution, frame rate and audio. The default keeps every frame and the resolution. After
  compressing, photag **verifies** the result (frame count, length within ±50 ms, resolution, audio, size and SSIM) and only then replaces
  the file; the previous version is kept in backups and can be restored.
- **Photo compression and several files at once** — the same dialog handles one photo, one video or any mix (Photo menu →
  "Compress selected files…"). Photos: JPEG/WebP by quality, PNG/BMP/TIFF losslessly, optional longest-side limit; EXIF, orientation and
  color profile are kept. Every file is verified (size, metadata, SSIM) and backed up before it is replaced; a file that did not get smaller
  stays as it was. A progress screen shows percent, elapsed and estimated remaining time and the finished files; a report follows.
- **Automatic backup and restore** (File → Backup and restore) — by default a verified backup of the catalog, settings **and your photos and
  videos** (incremental copy) is made once a day, keeping the latest 10. It cannot quietly "forget": several things wake it up and any one is
  enough — a Windows scheduled task (hourly and at every sign-in, catching up on runs missed while the PC was off), a Run entry at sign-in,
  and the app itself (at start-up and every 5 minutes). The background run is a small separate program, `photag-backup.exe`, with no window,
  low priority and Windows background mode; it exits as soon as it is done. Failures are recorded, shown in the backup dialog and in a
  notice at start-up, and retried after 30 minutes. You can change the interval, how many to keep and the folder (a different disk is best).
  Restoring first makes a safety copy of the current state, so a restore can be undone; photo files are never deleted by a restore.
- **Map** — select photos and click the map button in the toolbar (next to "People"): every place where they were taken gets a pin (photos
  from the same place share one pin with a count); click a pin to see the photos. With nothing selected, all photos in view are shown. The
  metadata panel has a small map for the selected photo. The map is Leaflet, bundled with the app; **tiles are loaded from OpenStreetMap, so
  an internet connection is needed** (offline you see the pins on an empty background), and the tiles you view are requested from their servers.
- **Asks before deleting** — moving to the trash (with a "don't ask again" option; it is always restorable), going back to the original
  file, deleting a collection, a backup or an API key.
- **Automatic updates** — at start-up photag checks the GitHub releases (`giamat13/photag`); if a newer one exists, a window shows "What's
  new" with "Update now" / "Later" / "Skip this version". The download is verified with SHA-256 before it runs. Manual check: Help → Check for
  updates. The update is installed *over* the existing app (never uninstall-then-install); your photos and data live outside the program folder
  and are not touched. Before installing, a verified copy of the program is saved: if the PC shuts down or the installer is closed halfway, the
  previous version is restored automatically (on the next start, or through a Windows RunOnce script at the next sign-in if the program itself
  is damaged). After an update the release notes of the new version are shown, fetched from GitHub.
- **Video player** — our own controls over the browser player: progress bar with buffered range and frame preview on hover, ±10 s, volume,
  speed (0.25×–2×), loop, frame step (`,` and `.`), picture-in-picture, full screen and shortcuts (Space, `Shift+←/→`, `↑/↓`, `M`, `F`).
  For formats it cannot play there is an "Open in external player" button — VLC if installed, otherwise the Windows default player.
- **Import from a folder / memory card** — copies into the library by year taken, detects duplicates, and can add keywords and a
  collection during import. Includes RAW files (CR2/CR3/NEF/ARW/DNG/…) through the preview the camera stores in the file.
- **Import from a Lightroom Classic catalog (`.lrcat`)** — ratings, flags, color labels, captions, dates, GPS, keywords ("person" keywords
  become people), collections and the quick collection. Develop edits are not transferred (a Lightroom format) — the original file is imported.
- **Import from Google Takeout** — reads the ZIP files directly (no need to unpack 19 GB); a large export comes as several ZIPs (`…-001.zip`, `…-002.zip`): choose them all, or just one and the other parts in the folder are added automatically (a missing part is reported, and importing it later never duplicates), keeps each photo once (dedup by SHA-256) and restores
  albums, descriptions, dates taken, GPS, favorites, Google's people tags, memory titles and comments on shared albums.
  All of these imports are in **one Import screen** (File → Import…) where you choose the source.
- **Face recognition** — InsightFace `buffalo_l` (ONNX Runtime) → cosine distance → SciPy average-linkage clustering at a threshold of
  **0.38**. Cluster names are seeded from Google's people tags (majority vote); the rest can be named by hand.
- **Simple photo editing** — rotate, crop, brightness / contrast / saturation, black and white. The original is always kept and restorable.
- **Metadata editing** — description, date, GPS, favorite, rating, tags, with optional write-back to EXIF (JPG).
- **Storage transparency** — the settings screen shows exactly where everything is kept, and the location can be changed.

## Languages
English (default), עברית, العربية, Русский, Español, Français, Deutsch, Italiano, Português, Nederlands, Polski, Українська, Türkçe, 中文,
日本語, 한국어, हिन्दी — View → Language. English is the base language: every UI string is an English key, and the translations (Hebrew included) are in `app/ui/locales/<code>.json`.
`py -3.12 tools/ui_smoke.py` checks the language handling in a real browser.
```bat
python tools\i18n.py extract   REM after changing UI text: rebuilds locales\_keys.json
python tools\i18n.py check     REM checks that every language file is complete and the {placeholders} are intact
```

## Where your photos are kept
Default: `%USERPROFILE%\Photag\` (an older library in `%USERPROFILE%\PhotoManager\` keeps working; photag offers to move it to `Photag` with a simple rename, nothing is copied)
- `media\` — the photo / video files
- `thumbs\` — thumbnails
- `catalog.db` — the database (SQLite)
- `backups\` — backups (unless you chose another folder)

The location pointer is stored in `%APPDATA%\photag\config.json` and can be changed in the settings screen.

## Run from source
```bat
pip install -r requirements.txt
python photag.py            REM desktop window (WebView2)
```
Or in VS Code: F5 → "photag (desktop window)".

## Build the installer
```bat
pip install -r requirements.txt pyinstaller
build.bat                   REM builds dist\photag.exe, dist\photag-backup.exe and installer_output\photagSetup.exe
```
`build.bat` needs Python 3.12 and [Inno Setup 6](https://jrsoftware.org/isdl.php). The `buffalo_l` model (~300 MB) is downloaded on the first
face detection. To test a build on a clean Windows, `python tools/sandbox/make_wsb.py` creates a Windows Sandbox configuration that installs,
exercises and uninstalls it automatically (see `tools/sandbox`).

## License

photag is free software under the **GNU General Public License version 3** (GPL-3.0), see [LICENSE](LICENSE). You may use, study, change and
redistribute it, including with changes, as long as distributed versions stay under the same license and their source code is available.
There is no warranty.

The license was chosen partly because of components bundled with the app: **ffmpeg** (the GPL build from imageio-ffmpeg) is used to verify
compression and to make video thumbnails. FFmpeg's source code is available at <https://ffmpeg.org/download.html#get-sources>.

Not bundled, and therefore not covered by this license:
- **HandBrake** (GPL) — installed separately by the user; photag only runs `HandBrakeCLI`.
- **Face-recognition model weights** (InsightFace `buffalo_l`) — downloaded on first use, **for non-commercial / research use only** under the
  terms of their authors. A commercial version would need a different model.
- **Map tiles** — from OpenStreetMap (© OpenStreetMap contributors, ODbL); an internet connection is required.
- Other Python libraries (FastAPI, Pillow, NumPy, …), Leaflet and the fonts keep their own licenses. The full list, including the written offer
  of FFmpeg source code, is in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) (generated with `python tools/gen_notices.py`).

## Typical workflow
1. Settings → check the library location.
2. File → Import… → choose the source (folder / memory card, Lightroom catalog, or Google Takeout ZIP) → start.
3. Library → "Face recognition" to group people. Add keywords in the "Keywording" panel.
4. People → name or correct the names. Catalog / collections / keywords → browse and search.
