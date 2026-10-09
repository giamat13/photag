# photag 18.0.0-beta.1

## Features
- **Report a problem or suggest a feature** (Help menu): write a title and a description and press send; nothing opens in the browser and no GitHub account is needed. The report appears publicly on GitHub as an issue. You see exactly what is sent (version, system, the last messages of the program's log, what the window knows; folders, your user name, e-mail and IP addresses are removed) and can leave the technical details out. At most 3 reports an hour and 10 a day.
- The program keeps a log file of its messages (`photag.log` in its settings folder, 1.5 MB at most).

## Small fixes
- The Windows downloads are now named `windows-photag-…` (portable ZIP, MSI, installer ZIP and install scripts). The installer `photagSetup.exe` and the code update keep their names so that installed programs can still update.
- Running from the source folder with F5 now starts its own window on its own port, so it no longer attaches to an installed photag that is already open.
