# photag 24.0.0

## Features

- **Tag a person by hand**: right-click a photo > "Tag a person in this photo…", drag a rectangle around the face and type the name. For faces the detector missed or got wrong; the person appears in People and in the photo's details. Works on photos that were not edited (#67).
- **AI suggests develop settings**: in the Edit module, the new "AI" button sends the photo (with what photag knows about it) to the AI of AI tagging, and puts its suggested settings on the sliders as a preview. Nothing is saved until you press Apply (#46).
- **A desktop wallpaper that changes by itself** (Windows): Library > "Desktop wallpaper…" (or right-click a collection > "Use as the desktop wallpaper…"). Choose all photos, favourites or a collection, how often it changes, and the order. photag puts a scaled copy on the desktop; your photos are not changed (#55).
- **photag in the right-click menu of a folder in Explorer** (Windows): "photag" > "Import this folder into photag…" / "Import this folder as a collection…" opens photag's Import window on that folder (#60).
- **A series by hand**: select two or more photos, right-click > "Make a series (N×)". They get the series badge, can be compared, and share their keywords like an automatic series; "Remove from the series" takes photos out (also out of an automatic one) (#59).
- **Export in full quality, with the edits and all the metadata written into the file**: a new choice in Export. The copy gets the edits, the EXIF, caption, rating and keywords; your library files are never changed (#62).
- **Pictures in a problem report or suggestion**: "Add pictures…" (or paste with Ctrl+V) attaches up to 3 pictures. They are scaled down, stripped of their metadata, and shown with the report on GitHub (#51).

## Small features

- **The AI sees more when it picks the best photo or tags a photo**: the capture time, camera and exposure, caption, people, and the keywords of the photos taken close in time (about two hours around it) go with the photo (#63, #65).

## Fixes

- **The language went back to English after an update**: the language you chose is now also kept by photag itself, not only by the window, which forgets it when photag has to use another port (#66).
- **The Timeline became the Grid after viewing a photo**: leaving the loupe (double click or Esc) now returns to the view it was opened from (#61).
- **No right-click menu in the Timeline**: the same menu as in the Grid now opens on a Timeline picture (#64).
