"""OneDrive and other "files on demand" cloud folders.

Windows can keep a file in the cloud only ("Free up space" / online-only): the file is listed with its size and date, but
reading it makes OneDrive download it first. A scan that reads every file (hashing, thumbnails, analysis) would silently
download a whole library, and a catalog or backup written inside a synced folder meets files that OneDrive has locked
for a moment. This module is the one place that knows about that:

  * is_online_only(path)   a cloud-only placeholder: read it only when the user asked for that very file
  * in_onedrive(path)      the path is inside a OneDrive folder
  * replace(src, dst)      os.replace that waits and retries while OneDrive / an antivirus holds the file
"""
import os
import re
import time
from pathlib import Path

RECALL_ON_OPEN = 0x00040000
OFFLINE = 0x00001000
RECALL_ON_DATA_ACCESS = 0x00400000
ONLINE_ONLY = RECALL_ON_OPEN | OFFLINE | RECALL_ON_DATA_ACCESS
_ONEDRIVE_NAME = re.compile(r"^onedrive( - .+|\s*\(.+\))?$", re.I)      # "OneDrive", "OneDrive - Company", "OneDrive (Personal)"


def is_online_only(p) -> bool:
    """True for a cloud-only placeholder (not on this computer yet). Always False where the file system has no such notion."""
    marker = os.environ.get("PHOTAG_TEST_ONLINE_ONLY")                    # tests: any file whose name contains this text
    if marker and any(m and m in os.path.basename(str(p)) for m in marker.split(",")):
        return True
    try:
        st = p if hasattr(p, "st_mode") else os.stat(p)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & ONLINE_ONLY)


def onedrive_roots() -> list[str]:
    out = []
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(k)
        if v:
            out.append(v)
    out += [x for x in os.environ.get("PHOTAG_TEST_ONEDRIVE", "").split(os.pathsep) if x]
    return out


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def in_onedrive(path) -> bool:
    """The path is inside the user's OneDrive (by the OneDrive environment variables, or a folder named like one)."""
    try:
        p = _norm(Path(str(path)).expanduser().resolve())
    except OSError:
        p = _norm(path)
    for r in onedrive_roots():
        n = _norm(r)
        if p == n or p.startswith(n.rstrip("\\/") + os.sep):
            return True
    return any(_ONEDRIVE_NAME.match(part) for part in Path(p).parts[1:])


def replace(src, dst, tries: int = 12):
    """os.replace that rides out a short lock (OneDrive uploading the file, an antivirus scanning it): WinError 5 / 32."""
    for i in range(tries):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(min(0.15 * 2 ** i, 3.0))
        except OSError as e:
            if getattr(e, "winerror", None) in (5, 32, 33) and i < tries - 1:
                time.sleep(min(0.15 * 2 ** i, 3.0))
                continue
            raise
