# photag 18.1.0

## Fixes
- **Problem reports carry more of what is needed to find the cause:** the last 120 lines of the program's messages, the end of the log of the previous run (useful after a crash), the versions of the main libraries, the number of photos, videos and edited photos, the free disk space of the library, and what the window knows (its size, theme, the errors it had and the requests that failed). All of it can be read in the dialog before sending; folders, user name, e-mail and IP addresses are removed.
- The program now keeps a log file of its messages (`photag.log` in its settings folder, 1.5 MB at most, the older part in `photag.1.log`).
