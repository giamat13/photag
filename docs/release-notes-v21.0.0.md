# photag 21.0.0

## Features
- **All the camera data in the info panel (#32)**: the shutter speed, aperture, ISO, exposure compensation and flash now appear right under the camera and lens, and the full list of EXIF tags is always open below it (no more clicking to expand it).
- **Your reports and suggestions (#34)**: Help > "Your reports and suggestions" lists everything you sent through "Report a problem or suggest a feature", with its state on GitHub (open / closed). You can edit the title and the text, withdraw a report, reopen it or open it on GitHub.
- **A better progress screen (#35)**: the speed graph now has a filled curve, the peak value and the time span; a slow job such as AI tagging is shown per minute (photos/min) with a readable curve instead of "0.0 photos/s". Every job shows the average time per item and the peak speed, and AI tagging also shows how many photos were tagged, how many keywords were added, how many failed and which model was used.
- **Less memory (#33)**: the AI models (face detection and the search index, hundreds of MB) are unloaded after 5 minutes without use and the freed memory is given back to Windows every couple of minutes; the AI runtime no longer keeps its large working buffers.

## Fixes
- The **estimated time left** of AI tagging was always "…" until 1% was done (about 40 minutes for a big library); it now appears after the first photo.
