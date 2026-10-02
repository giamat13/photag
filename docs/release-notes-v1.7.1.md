# photag 1.7.1

## What is new
- **Collections now have a right-click menu.** Open in triplan (when marked as a trip), Mark/Remove Trip status, Best moments slideshow and Delete Collection -- previously only available as hover-only icons on the row.

## Fixed
- **"Open in triplan" pointed at a site that was never deployed.** triplan's Firebase Hosting (`triplan-giamat13.web.app`) was never actually published -- the real live site is on GitHub Pages. Every click since the feature shipped in 1.7.0 hit "Site Not Found"; it now opens the correct address.

Your photos, catalog, backups and settings are not touched by an update. Full source and licenses: see LICENSE and THIRD_PARTY_NOTICES.md.
