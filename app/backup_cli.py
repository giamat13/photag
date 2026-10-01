"""Headless backup: `photag.exe --backup [--force]` (run by the Windows Scheduled Task, see backup_task.py).

Backs up only when a backup is due (or with --force), writes a line to <backup folder>\\backup.log, and exits
without ever opening a window. Exit code 0 = done or nothing due, 1 = the backup failed (the failure is also
recorded so the app can tell the user).
"""
import sys
import time

from . import backup


def _log(msg: str):
    try:
        with open(backup.backup_dir() / "backup.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [task] {msg}\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    force = "--force" in argv
    try:
        with backup.background_mode(process=True):       # lowest CPU / disk priority: never gets in the way
            r = backup.run_if_due(by="task", force=force)
    except Exception as e:                      # never crash silently
        _log(f"error: {e}")
        return 1
    try:                                         # once a week the newest backup is also checked here (read-only)
        v = backup.verify_if_due("task")
        if v:
            _log("backup check: " + ("no problems" if v["ok"] else "PROBLEMS: " + "; ".join(p["key"].format(**p["vars"]) for p in v["problems"])))
    except Exception as e:
        _log(f"backup check error: {e}")
    if r.get("ran"):
        _log(f"backup done: {r['name']} ({r['photos']} photos, media copied: {(r.get('media') or {}).get('copied', 0)}, peak RAM {backup.peak_ram_mb()} MB)")
        return 0
    if r.get("error"):
        _log(f"backup FAILED: {r['error']}")
        return 1
    _log(f"nothing to do ({r.get('reason')})")
    return 0
