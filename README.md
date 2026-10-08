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
- **Undo and redo** (`Ctrl+Z` / `Ctrl+Y`) — moving to the trash, ratings, flags, color labels and the Quick Collection.
- **Add to Collection** from the right-click menu (a list of the collections and "New Collection...").
- **Advanced search** (Library → Advanced Search, `Ctrl+Shift+F`) — date range, near a place on the map, file type and size; searches can
  be saved (they live in the catalog and appear under "Saved Searches").
- **Automatic import from a folder** (Preferences) — new photos that appear in a chosen folder (for example where your phone syncs to) are
  imported in the background, once a minute, without duplicates; what was already there is skipped unless you ask for it.
- **Weekly backup check** — once a week the newest backup is checked (ZIP, catalog inside it, photo files; read-only) and a warning appears if
  something is broken. The trash is emptied after 60 days by default; the number of days is a setting (Catalog Settings).
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
- **Photo quality score, series and closed eyes** (Library → Analyse Photo Quality) — every photo gets a score from 1 to 100 (sharpness, exposure,
  contrast; lower when someone has closed eyes — checked with the face model once Face Detection has been run), shown on the thumbnail and in the
  metadata, and usable for sorting and in smart collections. Select several photos and click **Rank** to see which one is the best of a series
  (and, optionally, pick it and reject the rest). Everything is computed on your computer.
- **Duplicates and similar photos** (Library → Find Duplicates and Similar Photos) — groups of identical copies (a resized or re-saved file) and of
  similar shots (a burst or the same scene taken within minutes, or at the same place), with the best of each group suggested; keep what you want and
  move the rest to the Trash in one click.
- **Library cleanup** (Library → Library Cleanup) — a report of screenshots, receipts / documents, very dark and blurry photos, to review and move to the
  Trash in bulk (nothing is selected for you; the receipts check is a heuristic).
- **Search by meaning** (Library → Search by Meaning, or Library Filter → Text → *Meaning*) — find photos by describing them, e.g. "beach at sunset",
  with a local CLIP model (ONNX, on the same onnxruntime as face recognition): a one-time download (~600 MB, only after you agree) and a one-time pass over
  the photos; nothing is sent anywhere. CLIP understands English; a query in another language is translated by the AI provider you set up for AI tagging
  (only the query text is sent), otherwise write it in English.
- **On This Day** — photos taken on today's date in earlier years (Catalog → On This Day), an automatic slideshow of them (Library → On This Day:
  Slideshow) and a quiet reminder when you open photag.
- **Smart collections** (Collections → the smart-collection button, or Library → New Smart Collection) — rules that fill the collection by themselves:
  people (any / all), years, rating, quality score, flag, color label, keywords, Google favorites, with location, text. They update as you import and
  rate. One click creates a smart collection for each person tagged in Google Photos (from a Google Takeout import; Google's own "live album" rules are not
  part of a Takeout and cannot be imported).
- **Timeline** (View → Timeline, or the toolbar button) — your photos by month and year with a fast-scroll rail: drag it to jump to any month.
- **Backups of reduced copies for big libraries** (File → Backup and restore) — instead of the originals the backup can hold smaller copies of the photos:
  strong compression (JPEG quality 60) and HD size (1280 px on the long side) by default, adjustable; videos are copied as they are (or left out). The library
  is never touched, a file that would not get smaller is copied as is, later backups re-use unchanged copies, and restoring from such a backup only fills in
  missing files (never replaces one). Safety backups made before a restore, update or compression always hold the originals.
- **Works with OneDrive** — folders stored in OneDrive (photos-in-my-folder, automatic import, import from a folder): files that are only in the cloud ("Free up space") are never read by a scan, analysis or preview, so photag cannot silently download a whole library; they are not forgotten either (a photo whose file went online-only stays in the catalog, a new one is added once it is on the computer). Files that OneDrive briefly locks are retried instead of failing. photag warns when the catalog itself lives inside OneDrive (a synced database can get conflicts or be damaged) and says when the backup folder is inside OneDrive.
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

## Privacy
- Everything runs on your computer: the catalog, thumbnails, face recognition, search, backups and the map pins. photag has no account, no
  telemetry and no analytics.
- It only talks to the internet when you use these features: **AI tagging** (sends small thumbnails to the provider *you* choose, with your own
  key, and only when you start it), **map tiles** (OpenStreetMap, the tile requests reveal the area you look at), **update check** (asks GitHub
  for the latest release), the one-time download of the face model (~300 MB), and — only after you agree — the one-time download of the search-by-meaning model (~600 MB, from Hugging Face; searches in other languages send only the query words to your AI-tagging provider).
- The local server listens on `127.0.0.1` only and refuses requests that do not come from photag's own window (Host / Origin checks), so a web
  page open in your browser cannot reach your library.
- Your AI key is stored encrypted with Windows DPAPI (readable only by your Windows user).

## Windows says the installer is blocked (Smart App Control)
_Releases are not code-signed yet (see [docs/CODE_SIGNING_POLICY.md](docs/CODE_SIGNING_POLICY.md)); the signing steps are ready in the release workflow and will be switched on once a signing service is set up._

photag is not code-signed yet (a signing certificate costs money; the application to the free signing program for open-source projects was declined for now). Windows 11
**Smart App Control** (when it is on) blocks unsigned programs it does not know, with no "run anyway" button. SmartScreen only warns
("More info" → "Run anyway"). If the installer is blocked:
0. **Try the install script** from the release page: `photag-install.bat` (double-click it; Windows asks "Run / Cancel" instead of blocking it) or `photag-install.ps1`.
   It downloads the portable version straight from GitHub, checks its SHA-256, unpacks it into `%LOCALAPPDATA%\Programs\photag` and makes shortcuts. The files it
   unpacks are never marked "from the internet", which is what Smart App Control looks at. It is plain text -- read it first.
   **Tested limit:** on a PC where Smart App Control is *on* (tested in Windows Sandbox) the script installs everything, but Windows then refuses to *start* the unsigned `photag.exe`
   ("An Application Control policy has blocked this file"). Nothing a script can do changes that: a self-made certificate, even trusted on the PC, is ignored, and a signed Python
   cannot load the unsigned native modules (Pillow, numpy, pydantic) either. Only a signed build helps (planned: Microsoft Store), or turning Smart App Control off
   (Windows Security > App & browser control > Smart App Control; it cannot be turned back on without reinstalling Windows). Where Smart App Control is off, SmartScreen only warns, and the installer/script work.
   **If the downloaded `.bat` / `.ps1` is blocked too** (a file from the internet carries a "from the internet" mark; a file you make yourself does not):
   either open **Windows PowerShell** (not cmd), paste this one line and press Enter -- it runs the script straight from GitHub without saving any file --
   ```
[Net.ServicePointManager]::SecurityProtocol='Tls12'; $w=New-Object Net.WebClient; $w.Encoding=[Text.Encoding]::UTF8; & ([scriptblock]::Create($w.DownloadString('https://github.com/giamat13/photag/releases/latest/download/photag-install.ps1').TrimStart([char]0xFEFF)))
   ```
   or open the script on the release page, copy its whole text, paste it into Notepad, save it as `photag-install.bat` (Save as type: All files) and double-click that.
1. Right-click `photagSetup.exe` → Properties → tick **Unblock** if shown, OK, and run it again. Or run it from File Explorer instead of a terminal.
2. Try the **portable version** instead (see below) — same program, no installer.
3. Otherwise run photag from source (see below), or turn Smart App Control off in *Windows Security → App & browser control → Smart App Control*
   (this cannot be turned on again without reinstalling Windows, so only do it if you accept that).
Smart App Control can still block the portable EXE itself the first time it runs on a given PC; there is no installer-vs-portable difference
there, only signing fixes that for good.

## Background mode (Windows)
photag keeps an icon next to the clock. Closing the window does not end the program: automatic import and the backup keep working, and the icon's menu has **Open photag**, **Back up now** and **Exit**. photag tells you with a notification when the backup drive is not connected, when there was no backup for several days, or when the backup keeps failing (once every 12 hours at most, in your language). Preferences > Background switches the icon off, or starts photag in the background when you sign in.

## More daily tools
- **Ctrl+K** is one search box for every command, place (album, person, folder, keyword), year and photo name.
- **Library > Add places to photos without GPS...** suggests places from a GPX track or from pictures taken at the same time; you confirm the list.
- **File > Make a slideshow video...** turns pictures into an MP4 with fades and your own music (needs ffmpeg).
- In the viewer, **C** compares two pictures side by side with the same zoom.

## Use photag as your picture viewer
Right-click a picture > **Open with > photag** shows it in a fast viewer (zoom, pan, next/previous picture in the folder, rotate, full screen, slideshow, print, rename, copy and move to a folder, delete to the Recycle Bin, an editor that saves a copy (tone sliders, straighten, crop, one-button *Improve automatically*), videos, copy to the clipboard, set as desktop background, a strip of thumbnails, the location on a map and all the EXIF) **without adding it to your library**. A button in the viewer adds it if you want. The installer offers this as an option, and Preferences > *Picture viewer* switches it on or off for the portable version. To replace the Windows Photos app, press *Choose in Windows settings* there and pick photag for each picture type (Windows does not allow a program to make itself the default).

## Portable version
No installation, no admin rights, nothing written to this PC: download `photag-<version>-portable.zip` from the
[releases page](https://github.com/giamat13/photag/releases), extract it anywhere (a USB stick, a folder, a synced drive) and run `photag.exe`
from there. Everything photag needs — catalog, settings, backups, your photo library by default — lives in a `data` folder right next to
`photag.exe`; move or copy the whole extracted folder (photag must be closed first) and it keeps working, on any PC.
- Auto-update installs a new `photagSetup.exe` are not offered in portable mode (there is no installed location to replace); a **code update**
  (see Updates below) still applies normally, since it only touches files inside the portable folder.
- The background backup task (Scheduled Task + sign-in entry) is not registered in portable mode — there is no stable path to point it at on a
  drive that may be plugged into a different PC next time. Back up from inside the app, or enable it after installing normally instead.
- Delete `portable.txt` (next to `photag.exe`) to make a copy of photag behave like a normal per-PC install again.

## Updates
photag checks GitHub for a newer version at start-up and once a day (switch it off in Preferences) and offers the update in a window. Most
updates are a small **code update** (~1 MB): only photag's own Python code and web pages are replaced, never an executable, so Smart App
Control and similar protections have nothing new to block; the previous code is kept and put back automatically if the new code cannot start.
An update that changes the libraries inside photag comes as a normal installer instead (`codeboot.py` explains the rules; `RUNTIME` there is
bumped for such releases). Every download is verified with the SHA-256 that GitHub publishes for the file.

## Photos stay in my folder (optional)
For people who already have a folder structure that is not all photos and do not want to reorganise it: in *Preferences* switch on
**Photos stay in my folder** and choose the folder. photag then lists the image files of that folder and its subfolders where they are and
keeps the list up to date (a quiet scan every few minutes, or *Scan now*): new files appear, files you move or rename inside the folder keep
their ratings and keywords, files you delete disappear. It is **off by default**.
- The files in that folder are **never moved, copied, renamed or changed** by photag. Develop edits, rotate, compress and "save metadata to the
  file" are therefore not available for these photos (import a copy to edit it); ratings, flags, keywords, collections and descriptions work, they
  live in the catalog.
- The catalog, thumbnails, backups and settings stay in photag's own folder. Backups also hold the photos of your folder (reduced copies if you chose that, under `_external` in the backup); a restore only puts back files that are missing, at their own path, and never replaces one.
- The only time a file in your folder is touched: you delete a photo from the trash for good, and its file then goes to the **Recycle Bin**
  (never deleted outright; on drives without a Recycle Bin photag leaves the file alone and keeps the photo in the trash).
- A drive that is unplugged never empties the catalog: when the folder cannot be read, or an unusually large part of it vanishes at once, nothing is removed.

## Run from source
```bat
pip install -r requirements.txt
python photag.py            REM desktop window (WebView2)
```
Or in VS Code: F5 → "photag (desktop window)".

## Build the installer
```bat
pip install -r requirements-dev.txt
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

## Code signing
Not signed yet. Policy, status and privacy statement: [docs/CODE_SIGNING_POLICY.md](docs/CODE_SIGNING_POLICY.md).
