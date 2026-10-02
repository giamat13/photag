"""photag-backup.exe: the small headless program the Windows scheduled task runs (see app/backup_task.py).

It contains only the backup code (standard library), so it starts instantly, unpacks almost nothing and
uses very little memory; the big photag.exe is not involved. Usage: photag-backup.exe --backup [--force]
"""
import os
import sys

os.environ["PHOTAG_BACKGROUND"] = "1"      # before app.config is imported (see _apply_pending_move)

import codeboot                       # a newer copy of the `app` package next to the exe (code update) wins over the built-in one
codeboot.activate(count=False)

from app import backup_cli

_rc = backup_cli.main(sys.argv[1:] or ["--backup"])
if sys.stdout is not None:
    sys.stdout.flush()
if sys.stderr is not None:
    sys.stderr.flush()
os._exit(_rc)          # leave at once: nothing stays in memory
