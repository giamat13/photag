# photag 1.2.0

## What is new
- **Add to Collection from the right-click menu.** Right-click photos, choose *Add to Collection*, and pick a collection from the list (or *New Collection...*). Dragging still works.
- **Undo (Ctrl+Z) and Redo (Ctrl+Y).** Moving to the trash, ratings, flags, color labels and the Quick Collection can be undone, so a single wrong key press is no longer a problem. Also in the *Edit* menu.
- **Automatic import from a folder.** In *Preferences*, choose a folder (for example the one your phone syncs to). New photos that appear there are imported by themselves in the background, without duplicates. Photos that were already in the folder stay where they are unless you tick "Also import the photos already in the folder".
- **Advanced search** (*Library → Advanced Search*, Ctrl+Shift+F): date range, near a place on the map, file type and size. A search can be saved and appears under *Saved Searches*; it is stored in the catalog, so backups include it.
- **Weekly backup check.** Once a week photag checks that your newest backup is intact (the ZIP, the catalog inside it and the photo files; read-only, nothing is restored) and warns you if something is broken. There is also a *Check the backup* button in the backup window.
- **The trash period is a setting.** Items are still deleted from the trash after 60 days by default; change it in *Catalog Settings*. If you shorten it, photag asks first when that would delete items right now.

Your photos, catalog, backups and settings are not touched by the update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
