# photag 12.1.0

## Fixes
- **Backup now includes photos that are outside the library.** With "photos stay in my folder" the backup used to copy only the catalog (a few MB for a library of hundreds of GB). It now also copies those photos and videos (reduced copies if you chose that), skips a drive that is unplugged or a file that is only in OneDrive's cloud, and never touches your own folder. A restore puts back only files that are missing, at their own path, and never replaces an existing file.

## Small fixes
- "What's new" is now grouped into Features, Fixes and Small fixes (in your language), and when you update across several versions the notes are merged by group.
