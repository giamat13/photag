# Import from Google Photos

photag can bring photos in from Google Photos through Google's official **Picker API**: you choose photos in Google's own window, and photag
receives exactly those. It cannot read your whole library by itself (Google closed that in March 2025), so there is no background sync;
instead photag can remind you every few days to run an import (Preferences > Google Photos, off by default).

Your Google Photos library is only **read**. Everything that comes in is a new copy in the photag library.

## What is never imported twice
- a photo picked before (its Google id is remembered), while it is still in the catalog;
- a file with the same content (SHA-256);
- the same shot **edited in Google Photos** or already imported from Takeout / a folder: it is recognised by file name (ignoring what Google adds, such
  as `-edited`) and capture time, before anything is downloaded.

A photo you deleted from photag comes back if you pick it again.

## One-time setup (about 10 minutes): a Google Cloud OAuth client
Google requires every app that talks to Google Photos to be registered. photag does not ship with a registration; you create your own
(free) and type its Client ID and Client secret into the import window, once.

1. Open <https://console.cloud.google.com/> and create a project, for example `photag`.
2. **APIs & Services > Library**: enable the **Photos Picker API**.
3. **APIs & Services > OAuth consent screen**: user type **External**; add the scope `.../auth/photospicker.mediaitems.readonly`.
   Then press **Publish app** (status "In production"). In "Testing" status Google expires the permission every 7 days.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**, type **Desktop app**. Copy the Client ID and the Client secret.
5. In photag: **Import... > Google Photos**, paste both values, **Save**, **Connect to Google Photos** and allow access in the browser.

After that: **Import... > Google Photos > Choose photos...**, mark the photos in Google's window, press **Done** there. photag brings in the new ones.

## Reminder
Preferences > Google Photos > "Remind me to import from Google Photos" (every N days). It is off by default and only shows when it is switched on.

## For developers
`app/gphotos.py`; tests: `tools/test_gphotos.py` (a fake Google on 127.0.0.1). A release build can carry a built-in client: the release
workflow may write `app/_google_client.py` (`CLIENT_ID`, `CLIENT_SECRET`, git-ignored); values typed in Preferences win over it.
