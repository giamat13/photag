# photag 1.7.0

## What is new
- **New import sources.** Import → digiKam reads a `digikam4.db` database, bringing in ratings, captions, GPS, tags and people (tags digiKam marks as a person become People). Import → Instagram / Facebook reads a "Download Your Information" export ZIP directly, with caption and date recovered from the export's own JSON when it's included.
- **Lightroom import, two improvements.** The Lightroom import screen now shows catalogs found in their usual default folders ("Found on this computer"), so you often don't need to browse for the file. And if there's no `.lrcat` at all -- a lapsed subscription, say -- a new "Recover ratings, labels and keywords from XMP" option on folder import reads an XMP sidecar next to each file, or an XMP packet embedded in it, and applies what it finds.
- **Export, three new formats.** Export now offers a dropdown: a plain folder (as before), a single ZIP file, or a self-contained HTML gallery (every photo embedded as a resized JPEG with a click-to-enlarge lightbox -- one file, nothing else needed to view it). A new "Include an XMP sidecar" option writes rating, color label, keywords, people and caption -- and now album/collection membership too -- next to each exported file, readable by Lightroom, Bridge, digiKam and most other photo software.
- **Burst stacking.** Rapid-fire sequences (continuous shooting) collapse into a single tile in the grid, showing the sharpest shot with a stack badge; click it to see every frame.
- **Face Timeline.** A clock icon on a named person's card in People view opens a year-by-year strip of their best photo each year.
- **Map Flythrough.** In Map view, an animated marker traces your geotagged photos in capture order, drawing a trail as it goes.
- **Storage breakdown** (1.6.2, included here for completeness): File → Storage... shows where your library's disk space goes -- by folder, year and file type.
- **photag × triplan, step 1.** An album can be marked as a trip; mark one and a "Connect to triplan" option appears to sign in, plus an "Open in triplan" button on the album.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
