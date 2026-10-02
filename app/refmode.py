"""Keep photos where they are ("photos stay in my folder"), for people who already have a folder structure they want to keep.

Off by default. When it is on, photag shows the image files of one folder (and its subfolders) and keeps that list up
to date, but never moves, copies, renames or changes those files. Everything photag makes itself (catalog, thumbnails,
backups, settings) stays in the normal library folder. The only time a file in that folder is touched is when the user
deletes a photo from the trash for good: the file then goes to the Windows Recycle Bin (never deleted outright).

How it fits the rest of the program: a photo of such a folder has an ABSOLUTE path in photos.rel_path. Because
`PATHS.media / "D:\\Photos\\a.jpg"` is simply that path, every place that opens a photo keeps working. Places that would
change a file (develop edits, rotate, compress, writing metadata into the file) refuse these photos.
"""
import os
import time
from pathlib import Path

from . import cloud, db, images
from .config import PATHS

ENABLED_KEY, FOLDER_KEY = "ref_enabled", "ref_folder"
SAFE_REMOVE_FRACTION = 0.5          # more than half of the known files vanishing at once looks like an unplugged drive, not a clean-up


def is_external(rel_path: str | None) -> bool:
    """True for a photo that lives in the user's own folder (its stored path is absolute)."""
    return bool(rel_path) and Path(rel_path).is_absolute()


def get(con) -> dict:
    return {"enabled": str(db.get_setting(con, ENABLED_KEY, "0")) == "1", "folder": db.get_setting(con, FOLDER_KEY, "") or ""}


def set_state(con, enabled: bool | None = None, folder: str | None = None) -> dict:
    if folder is not None:
        db.set_setting(con, FOLDER_KEY, folder)
    if enabled is not None:
        db.set_setting(con, ENABLED_KEY, "1" if enabled else "0")
    return get(con)


def folder_problem(folder: str) -> str | None:
    """Why this folder cannot be used (an error key for the UI), or None."""
    p = Path(folder)
    if not p.is_dir():
        return "Folder not found"
    try:
        f, r = p.resolve(), PATHS.root.resolve()
    except OSError:
        return "Folder not found"
    if f == r or r in f.parents or f in r.parents:
        return "Choose a folder outside the photag library"
    return None


def walk(root: str):
    """(absolute path, size, mtime_ns) of every image file under the folder; hidden files and folders are skipped."""
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("$")]
        for n in names:
            if n.startswith(".") or Path(n).suffix.lower() not in images.IMAGE_EXT:
                continue
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size > 0:
                yield os.path.normpath(p), st.st_size, st.st_mtime_ns


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _under(path: str, root: str) -> bool:
    k, r = _key(path), _key(root)
    return k == r or k.startswith(r.rstrip("\\/") + os.sep)


def forget(con, photo_ids: list[int]) -> int:
    """Take photos out of the catalog WITHOUT touching their files (the files are gone or were moved away)."""
    n = 0
    for pid in photo_ids:
        row = con.execute("SELECT sha256 FROM photos WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        images.thumb_path(row["sha256"]).unlink(missing_ok=True)
        for table in ("photo_albums", "photo_people", "photo_tags", "faces", "video_backups", "ref_files"):
            con.execute(f"DELETE FROM {table} WHERE photo_id=?", (pid,))
        con.execute("DELETE FROM photos WHERE id=?", (pid,))
        n += 1
    return n


def _register(con, path: str, size: int, mtime_ns: int, sha: str) -> int:
    taken, lat, lng = images.exif_info(Path(path))
    w, h = images.dimensions(Path(path))
    now = int(time.time())
    pid = con.execute(
        "INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,taken_at,created_at,lat,lng,imported_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sha, Path(path).name, path, Path(path).suffix.lower().lstrip("."), 0, w, h, size,
         taken or int(mtime_ns // 1_000_000_000), now, lat, lng, now)).lastrowid
    try:
        images.make_thumb(Path(path), sha)
    except Exception:
        pass                                              # a damaged file still shows up (as a placeholder); no reason to skip it
    return pid


def scan(con, root: str, progress=None) -> dict:
    """Bring the catalog in line with the folder: new image files are added, files that were moved inside the folder keep
    their ratings and keywords, files that are gone are removed from the catalog. Nothing in the folder is written."""
    stats = {"added": 0, "moved": 0, "changed": 0, "removed": 0, "duplicates": 0, "failed": 0, "skipped_removals": 0, "seen": 0, "cloud": 0}
    if progress:
        progress.state = "scanning"; progress.say("Looking at the photo folder…")
    disk = {_key(p): (p, s, m) for p, s, m in walk(root)}
    stats["seen"] = len(disk)
    known = {}
    for r in con.execute("SELECT path,size,mtime_ns,photo_id FROM ref_files"):
        if _under(r["path"], root):
            known[_key(r["path"])] = (r["path"], r["size"], r["mtime_ns"], r["photo_id"])
    root_ok = Path(root).is_dir()

    # files that are on disk and unchanged need nothing; changed or new ones are looked at
    changed = [k for k, (p, s, m) in disk.items() if k in known and (known[k][1], known[k][2]) != (s, m)]
    new = sorted(k for k in disk if k not in known)           # a fixed order: of identical copies, the first path is the one that is shown
    gone = [k for k in known if k not in disk]
    todo = len(changed) + len(new)
    if progress:
        progress.state = "importing"; progress.total = max(1, todo); progress.done = 0
        progress.extra = {"source": "refscan", "seen": len(disk), "t0": time.time()}
    n = 0

    def tick():
        nonlocal n
        n += 1
        if progress:
            progress.done = n
            progress.say("Adding photos from the folder… {done} of {total}", done=n, total=todo)
        if n % 50 == 0:
            con.commit()

    for k in changed:                                    # edited outside photag: refresh what depends on the content
        p, s, m = disk[k]
        pid = known[k][3]
        tick()
        if cloud.is_online_only(p):                      # in the cloud only: reading it would download it; looked at when it is on this computer
            stats["cloud"] += 1
            continue
        try:
            sha = images.sha256_file(Path(p))
            old = con.execute("SELECT sha256 FROM photos WHERE id=?", (pid,)).fetchone()
            if old and old["sha256"] != sha:
                if con.execute("SELECT 1 FROM photos WHERE sha256=? AND id!=?", (sha, pid)).fetchone():
                    stats["duplicates"] += 1
                else:
                    w, h = images.dimensions(Path(p))
                    con.execute("UPDATE photos SET sha256=?, bytes=?, width=?, height=? WHERE id=?", (sha, s, w, h, pid))
                    images.thumb_path(old["sha256"]).unlink(missing_ok=True)
                    images.make_thumb(Path(p), sha)
                    stats["changed"] += 1
            con.execute("UPDATE ref_files SET size=?, mtime_ns=? WHERE path=?", (s, m, known[k][0]))
        except Exception:
            stats["failed"] += 1

    gone_ids = {known[k][3]: k for k in gone}
    for k in new:
        p, s, m = disk[k]
        tick()
        if cloud.is_online_only(p):
            stats["cloud"] += 1                          # not added yet (and not forgotten): the next scan looks again
            continue
        try:
            sha = images.sha256_file(Path(p))
            row = con.execute("SELECT id, rel_path FROM photos WHERE sha256=?", (sha,)).fetchone()
            if row and row["id"] in gone_ids:             # the same photo, moved or renamed inside the folder: keep its metadata
                con.execute("UPDATE photos SET rel_path=?, filename=? WHERE id=?", (p, Path(p).name, row["id"]))
                con.execute("DELETE FROM ref_files WHERE photo_id=?", (row["id"],))
                con.execute("INSERT OR REPLACE INTO ref_files(path,size,mtime_ns,photo_id) VALUES(?,?,?,?)", (p, s, m, row["id"]))
                gone_ids.pop(row["id"])
                stats["moved"] += 1
            elif row:                                    # identical content already in the catalog (a copy elsewhere)
                stats["duplicates"] += 1
            else:
                pid = _register(con, p, s, m, sha)
                con.execute("INSERT OR REPLACE INTO ref_files(path,size,mtime_ns,photo_id) VALUES(?,?,?,?)", (p, s, m, pid))
                stats["added"] += 1
        except Exception:
            stats["failed"] += 1

    # what is really gone: only when the folder is readable and the loss is not suspiciously large
    if gone_ids:
        if not root_ok or (len(gone_ids) > 20 and len(gone_ids) > SAFE_REMOVE_FRACTION * max(1, len(known))):
            stats["skipped_removals"] = len(gone_ids)
        else:
            stats["removed"] = forget(con, list(gone_ids))
    con.commit()
    if progress:
        progress.state = "done"; progress.result = stats
    return stats


def run_scan(progress):
    """Job body (also used by the periodic check): scan the folder chosen in the settings."""
    con = db.connect()
    try:
        st = get(con)
        if not st["enabled"] or not st["folder"]:
            progress.state = "done"; progress.result = {}
            return
        problem = folder_problem(st["folder"])
        if problem:
            progress.fail(problem)
            return
        r = scan(con, st["folder"], progress)
        db.set_setting(con, "ref_last_scan", int(time.time()))
        db.set_setting(con, "ref_last_result", __import__("json").dumps(r))
    except Exception as e:
        progress.fail("Could not read the photo folder: {error}", error=str(e)[:200])
    finally:
        con.close()


# ---- deleting for good: the file goes to the Recycle Bin ------------------------------
def recycle(path: str) -> bool:
    """Send one file to the Windows Recycle Bin. False if it could not be done (the file is then left alone)."""
    p = Path(path)
    if not p.exists():
        return True
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    if ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:          # only fixed disks have a Recycle Bin; never delete outright
        return False

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc == 0 and not op.fAnyOperationsAborted and not p.exists()
