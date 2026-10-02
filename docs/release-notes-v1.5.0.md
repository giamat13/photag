# photag 1.5.0

## What is new
- **A quality score for every photo (1-100)** — sharpness, exposure and contrast, lower when someone has closed eyes. Shown on the thumbnail and in the metadata, available as a sort order and as a smart-collection rule. Select several photos and click **Rank** to find the best of a series, and optionally pick it and reject the rest.
- **Duplicates and similar photos** — finds identical copies and series of similar shots (same time, same place), suggests the best of each group, and moves the rest to the Trash in one click (restorable).
- **Library cleanup** — a report of screenshots, receipts and documents, very dark and blurry photos, for review and bulk deletion. Nothing is selected for you.
- **Search by meaning** — describe a photo ("beach at sunset", "dog in the snow") and find it, with a local CLIP model: nothing is sent anywhere. One-time download of about 600 MB after you agree. Other languages are translated by your AI-tagging provider if you set one up (only the search words are sent).
- **On This Day** — photos from this date in earlier years, an automatic slideshow, and a reminder at start-up.
- **Smart collections** — collections that fill themselves by rules (people, years, rating, quality, keywords...). One click creates one for each person tagged in Google Photos (Google Takeout import).
- **Timeline** — your photos by month and year, with a fast-scroll rail.
- **Backups of reduced copies** (File → Backup and restore) — for people with very large libraries: the backup can hold smaller, compressed copies of the photos (strong compression and HD size by default) instead of the originals. Your library is never touched; restoring from such a backup only fills in missing files and never replaces one.
- **OneDrive support** — folders stored in OneDrive work without surprises: files that are only in the cloud are never downloaded by scans, analysis or previews (and are picked up once they are on the computer), files OneDrive briefly locks are retried, and photag warns when the catalog or the backup folder is inside OneDrive.
- Translations of all new texts in the 16 languages.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
