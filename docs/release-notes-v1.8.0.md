# photag 1.8.0

## What is new
- **Edits no longer touch your photos.** Every edit is now saved as *settings in the catalog database*; the photo file itself is never rewritten. photag draws the edited look from the original plus those settings whenever it is needed, so the original is always the exact file you imported (same bytes, same date, same hash), backups no longer re-copy a photo after you edit it, and "Reset" / "Revert" can never lose anything. Photos you edited with an older version are moved to the new model quietly in the background -- the original goes back in place, and nothing is deleted until the catalog has been updated safely.
- **All EXIF is kept in the catalog.** Every EXIF tag of every photo (camera, lens, exposure, GPS, ...) is stored in the database -- at import for new photos, in the background for the ones you already have. The info panel has a new **All EXIF tags** section that shows them *from the catalog*, not from the file.
- **A switch for it.** Preferences has a new option, *Keep edits and EXIF in the catalog database, never in the photo file* (on by default). Switch it off and photag works like before: edits are written into the photo file (a copy of the original is kept) and the EXIF is read from the file. Switch it back on and the photos edited in the meantime are moved into the catalog again.
- **Many more edits.** Develop now has Exposure (in stops), Highlights, Shadows, Temperature, Tint, Vibrance, Clarity, Sharpness, Blur, Vignette and Sepia, plus Flip horizontally / vertically and three new presets (Warm Sepia, Warm Glow, Cool Tones). Sliders that CSS cannot draw are previewed live by the server on a shrunk copy of the original.
- **Auto.** One click on the new **Auto** button improves the photo: photag looks at the picture and sets exposure, contrast, highlights, shadows, white balance (temperature and tint), vibrance and sharpness to what it needs -- gently, never past sensible limits -- and applies it. It is an ordinary edit: it appears in History, and Reset / Revert take it back. Rotation, crop and flips you chose are kept.

## Good to know
- Edited looks are cached in a `renders` folder inside the library; it is rebuilt on demand and is not part of backups.
- Exports of "the photo as it looks" use the edited look; exporting "originals" gives the untouched original.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
