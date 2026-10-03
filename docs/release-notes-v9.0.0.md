# photag 9.0.0

Version numbers changed: they are now **feature.fix** -- "1.8.1" is followed by **9.0.0** (a new feature release); a release with only fixes raises the second number ("9.1"). Updating from 1.8.1 works as usual.

## What is new
- **Install with a script, when Windows blocks the installer.** The release page now has `photag-install.bat` (double-click it) and `photag-install.ps1`. They download the portable version from GitHub, check its SHA-256, unpack it into `%LOCALAPPDATA%\Programs\photag`, make shortcuts and keep your `data` folder when you run them again to update. The files they unpack are never marked "from the internet". Windows asks "Run / Cancel" for a downloaded `.bat` instead of blocking it outright. It is plain text -- read it first -- and it is not guaranteed to get past Smart App Control on every PC.
- **Export can update the EXIF and carry the edits.** A new option in the Export window: *Update the EXIF of the exported JPEG files and write the edit settings with the originals*.
  - Edited or resized copies used to lose all EXIF. Now they get every tag of the original plus the caption, capture time, location, star rating and keywords as they are in photag now, with the right orientation and size.
  - A photo exported as its **original** gets an XMP sidecar with its edit settings: exactly (in a `photag:` attribute) and, as far as there is a counterpart, in Lightroom's own vocabulary (exposure, contrast, highlights, shadows, saturation, vibrance, clarity, sharpness, vignette, black & white, crop). The Lightroom values are approximate; other programs may draw them slightly differently.
  - Your library files are never changed by an export.
- **Code updates accept the new numbering**, and the installer ZIP of 1.8.1 stays available as an extra download.

## Good to know
- If a release page offers a pre-release, the update window shows a big PRE-RELEASE banner (since 1.8.1).

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
