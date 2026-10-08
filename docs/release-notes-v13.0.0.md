# photag 13.0.0

## Features
- **Background mode (Windows).** photag now has an icon next to the clock. Closing the window no longer ends the program: automatic import and the backup keep working. The icon's menu: **Open photag**, **Back up now** and **Exit** (Exit really ends it). Switch it off in Preferences > Background. There is also "Start photag in the background when I sign in to Windows" (no window, just the icon).
- **Notifications about the backup.** photag tells you when the backup drive is not connected, when there was no backup for several days, or when the backup keeps failing -- also when the window is closed, and also from the scheduled background backup. The same problem is announced at most every 12 hours, in the language you use in photag.
- **"Make photag your picture viewer?"** The first time you open photag after installing, one simple question; "Yes" opens Windows' Default apps so you can choose photag for your pictures.
- **The picture viewer is now a complete replacement for the Windows Photos app.** New in the viewer ("Open with photag"): **rename** (`F2`), **copy to a folder**, **move to a folder** (never over a file that exists; a copy gets "name (2)"), **print** (`Ctrl+P`, exactly what you see: turned and edited) and a **slideshow** (`S`: full screen, a new picture every 2, 4, 8 or 15 seconds, round and round; `Esc` ends it). Rename, copy, move and print are in the **...** menu.
- **A real editor in the viewer.** Besides brightness, contrast and saturation: exposure, highlights, shadows, temperature, tint, vibrance, sharpness and vignette (drawn live), **straighten** with a free slider (the empty corners are cropped for you), **crop** with a frame you drag and ready shapes (1:1, 4:3, 3:2, 16:9, 10×15, 13×18, passport photo...) and **Improve automatically** -- one button that sets everything from the picture itself. "Save a copy" keeps your original.
- **Videos in the viewer.** "Open with photag" now also plays mp4, mov, m4v, webm, mkv, avi and more, with the player's own controls, and you can move between pictures and videos of the folder.
- **Copy and desktop background.** `Ctrl+C` puts the picture on the clipboard (as you see it, with your edits); the **...** menu has "Copy picture" and "Set as desktop background" (Windows).
- **A strip of thumbnails** at the bottom of the viewer (`T` or its button): click a picture to go to it.
- **Kind to the battery.** On a laptop that runs on battery, the heavy background work (the automatic backup, filling in EXIF, scanning your own folder, reading the edits) waits until the charger is connected. A backup never waits more than 3 days; "Back up now" and anything you start yourself always run. Preferences > Background has a switch.
- **Develop:** after straightening a photo, the empty corners are cropped automatically (as long as you did not set the crop yourself).

## Fixes
- The backup could fail with "[WinError 5] Access is denied" on the `media-....part` folder when an antivirus, the search indexer or OneDrive was holding a file in it open. photag now builds the photo folder under its final name in that case, and the backup succeeds.
