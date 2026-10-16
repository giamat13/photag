# photag 25.0.0

## Features

- **Import from Google Photos**: a new source in the import window. You choose photos in Google's own window (Google's official Picker; Google no longer lets any program read a whole library), and photag brings in the ones it does not have yet. Your Google Photos library is only read, never changed; the photos come in as new copies. Setup is a one-time Google Cloud OAuth client (about 10 minutes, steps in `docs/GOOGLE_PHOTOS.md`).
- **No duplicates, even for edited photos**: a photo picked before, a file with the same content, and the same shot you edited in Google Photos (or imported earlier from Google Takeout or a folder) are recognised before anything is downloaded and are not added twice. A photo you deleted from photag comes back if you pick it again.
- **A reminder to import (off by default)**: Preferences > Google Photos > "Remind me to import from Google Photos", every 1-365 days. The notice appears only when it is switched on; it offers Import now, Not now and Stop reminding me.
