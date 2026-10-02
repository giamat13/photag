# photag 1.7.2

## What is new
- **photag x triplan, step 2.** Marking a collection as a trip now prompts you to pick which of your actual triplan trips it is; "Open in triplan" sends the browser straight to that trip. Change the link any time from the collection's right-click menu ("Choose triplan trip...").
- **Tester mode.** A new "Tester mode: also offer pre-release versions" option in Preferences opts the update check into pre-releases too, for anyone who wants to try things early. Off by default -- nothing changes unless you turn it on.

## Fixed
- **Background backup crashed with "No module named 'PIL'" on every run.** `photag-backup.exe` (the tiny headless program the scheduled task runs, standard-library only by design) pulled in PIL indirectly even for a plain, non-compressed backup, crashing before any backup code could run. Regular backups work again; "Reduced copies" compression is unaffected.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
