# photag 20.0.0

## Features
- **Your original files are never changed.** photag now keeps one rule everywhere: it never renames, moves, rewrites or deletes the files you import from, the files of "photos stay in my folder" mode, or the pictures you open in the viewer. The only thing that writes is Export, and it writes a copy somewhere else. For that, the viewer no longer has Rename, Move to folder or Delete (to the Recycle Bin), and "Delete permanently" in the trash of reference mode now removes the photo from the catalog only, never the file. Everything new below works on copies.
- **Find a photo by place**: type a city, an address, a country or coordinates, or draw an area on the map, in the Library filter ("Location" tab) and in Advanced Search.
- **Fix dates from file names** (Library menu): photos that have no date at all get one from their name (for example IMG-20240501-WA0003). A date that is already there is never replaced.
- **Copy a picture to the clipboard**: right-click a photo, **Copy picture**, then paste it into a message, a document or an editor.
- **Drag and drop, and paste**: drop pictures and videos on the photag window, or paste a picture with Ctrl+V, and they are added to the library (copies; the originals stay where they are).
- **Save a frame of a video as a photo** and **trim a video** (right-click a video): both make a new file; the video itself is not changed. Trimming copies the video as it is (instant) or, if you choose, cuts exactly.
- **Merge two people**: in the People view, drag a person (or an unnamed group) onto the same person to merge them into one.
- **New in library**: after a big import a window shows what came in and what is still to do, with a button for each (add places, fix dates, analyze, find faces). It is also in the Library menu.
- **Search by panorama and by dominant color** in Advanced Search.
- **Make a collage** from the selected photos (right-click, or the Merge menu): a new photo.
- **Notification when your report is handled**: photag tells you once when an issue you reported or a suggestion you made was closed on GitHub.
- **A job per row**: when several things run at once (AI tagging, backup...) each has its own row and bar.

## Small features
- Reports and suggestions can be sent with a title only; the limit message says how long to wait.
- "Open on GitHub" buttons in the update and "What's new" windows.
- The viewer and the grid show animated GIFs as pictures that play; thumbnails keep the real shape of the picture.

## Fixes
- The toolbar no longer squeezes its buttons on a narrow window.
- **Delete** moves to the trash; Backspace no longer does (so a mistyped key doesn't delete a photo).
- Several reports in a row are no longer refused too early.
