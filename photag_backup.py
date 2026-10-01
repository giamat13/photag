"""photag-backup.exe: the small headless program the Windows scheduled task runs (see app/backup_task.py).

It contains only the backup code (standard library), so it starts instantly, unpacks almost nothing and
uses very little memory; the big photag.exe is not involved. Usage: photag-backup.exe --backup [--force]
"""
import os
import sys

from app import backup_cli

_rc = backup_cli.main(sys.argv[1:] or ["--backup"])
if sys.stdout is not None:
    sys.stdout.flush()
if sys.stderr is not None:
    sys.stderr.flush()
os._exit(_rc)          # leave at once: nothing stays in memory
