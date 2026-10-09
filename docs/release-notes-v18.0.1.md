# photag 18.0.1

## Fixes
- **AI tagging no longer fails with "can't register atexit after shutdown"** when it is started after the window was closed and photag keeps running next to the clock. Photo analysis and backups had the same problem and are fixed too.
- **AI tags now appear while the tagging runs**, photo by photo, not only when it ends. A stop keeps everything that was already tagged.
