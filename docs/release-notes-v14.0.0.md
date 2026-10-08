# photag 14.0.0

## Features
- **Send (small copy):** right-click a picture (or several) and choose "Send (small copy)". photag makes a copy with the long edge at 1600 px and without the location or other EXIF data, and puts it on the clipboard. Press Ctrl+V in WhatsApp, mail or any folder to paste it.
- **Copy path:** right-click a picture and choose "Copy path" to copy the full path of the file ("Show in Explorer" opens its folder).
- **Date from the file name:** a picture with no date inside it (WhatsApp, screenshots, scans) now gets its date from the file name, for example IMG-20240501-WA0003.jpg, instead of the day it was imported. A date written inside the picture is always used first.
- **An MSI package** next to the installer (photag-14.0.0.msi): for installing with msiexec, Group Policy or Intune. Per user, no administrator rights.

## Fixes
- **"Open with photag" opened photag but not the picture** (since 13.0.0, when photag was already running in the background with the icon next to the clock). The picture is now handed to the photag that is running and shown in the viewer.
- **After an update, Windows could show "Windows cannot find '\\'"** and photag did not come back by itself. photag now restarts itself directly, without the Windows command prompt.
