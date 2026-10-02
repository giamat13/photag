"""Periodic backups of the catalog (and optionally the photo files), restorable from inside the app.

A snapshot is one ZIP in the backup folder (default: <library>/backups):
    catalog.db     a consistent copy made with SQLite's online-backup API (safe while the app is running),
                   checked with PRAGMA integrity_check before it is accepted
    settings.json  the app settings (AI provider / key, HandBrake path ...); the key stays DPAPI-encrypted
    manifest.json  when / why / how many photos
The ZIP is written under a temporary name and renamed only when it is complete and verified, so a power
cut never leaves a half-written snapshot that looks real.

"include photo files" gives every snapshot its OWN complete set of photo files (<backup folder>/media-<snapshot>):
files that did not change since the previous snapshot are hard links to it (no extra space, no copying), new or
changed files are copied. So each backup is a full point-in-time copy, a photo deleted or damaged later is still in
the older ones, and the second backup is fast. Where hard links are not possible (FAT, network drives) the files
are simply copied. Snapshots made before this change share one folder, media-mirror, and still restore from it.

"Reduced copies" (compress_media) makes that photo set smaller for big libraries: JPEG / PNG / WebP / BMP / TIFF photos are
re-encoded at a lower quality and capped to a size (default: strong compression, 1280 px = HD) under the same file names,
other files (videos, RAW, HEIC...) are copied as they are. The library itself is never touched. A file that would not get
smaller is copied as is. Which source version a reduced file came from is recorded (.photag-index.json), so later backups
re-use it instead of encoding again. Safety snapshots (before restore / update / compression) always hold the originals,
and restoring from a reduced set only fills in files that are missing, it never replaces a file.

Restoring first takes a safety snapshot of the current state ("before-restore"), then loads the chosen
catalog into the live database in one transaction. Photo files are only ever ADDED back (never deleted).
"""
import concurrent.futures as cf
import contextlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, db, images
from .config import PATHS
from .version import __version__

DEFAULTS = {"enabled": True, "interval_hours": 24, "keep": 10, "include_media": True, "folder": None,
            "compress_media": False, "compress_quality": 60, "compress_max_side": 1280, "include_videos": True}
INDEX = ".photag-index.json"      # inside a reduced media set: for each file, which version of the source it was made from
REASONS = ("auto", "manual", "before-restore", "before-update", "before-compress")
NAME_RE = re.compile(r"^photag-\d{8}-\d{6}-(auto|manual|before-restore|before-update|before-compress)(-\d+)?\.zip$")
MIRROR = "media-mirror"
KEEP_MIN, KEEP_MAX = 3, 200
MAX_SAFETY = 3                   # backups made automatically before a restore / update / compression: this many of each kind are kept


# ------------------------------------------------------------------ settings
def get_settings() -> dict:
    s = {**DEFAULTS, **(config.get_backup() or {})}
    s["interval_hours"] = max(1, min(24 * 90, int(DEFAULTS["interval_hours"] if s["interval_hours"] is None else s["interval_hours"])))
    s["keep"] = max(KEEP_MIN, min(KEEP_MAX, int(DEFAULTS["keep"] if s["keep"] is None else s["keep"])))
    s["enabled"] = bool(s["enabled"])
    s["include_media"] = bool(s["include_media"])
    s["compress_media"] = bool(s["compress_media"])
    s["include_videos"] = bool(s["include_videos"])
    s["compress_quality"] = int(max(40, min(98, int(DEFAULTS["compress_quality"] if s["compress_quality"] is None else s["compress_quality"]))))
    ms = int(DEFAULTS["compress_max_side"] if s["compress_max_side"] is None else s["compress_max_side"])
    s["compress_max_side"] = ms if 256 <= ms <= 16384 else 0          # 0 = keep the size
    s["folder"] = (s["folder"] or None)
    return s


def set_settings(raw: dict) -> dict:
    cur = get_settings()
    for k in DEFAULTS:
        if k in raw and (raw[k] is not None or k == "folder"):      # a folder of None/"" = back to the default location
            cur[k] = raw[k]
    if cur["folder"]:
        f = Path(str(cur["folder"]).strip().strip('"')).expanduser()
        f.mkdir(parents=True, exist_ok=True)           # fails early (and clearly) for an unusable location
        probe = f / ".photag-write-test"
        probe.write_text("ok"); probe.unlink()
        cur["folder"] = str(f)
    config.set_backup({k: cur[k] for k in DEFAULTS})
    return get_settings()


def backup_dir() -> Path:
    s = get_settings()
    d = Path(s["folder"]) if s["folder"] else PATHS.root / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ listing
def _manifest(path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as z:
            m = json.loads(z.read("manifest.json"))
        m["name"], m["bytes"] = path.name, path.stat().st_size
        return m
    except Exception:
        return None


@contextlib.contextmanager
def background_mode(process: bool = False):
    """Windows "background mode": low CPU and disk priority for this thread (or the whole process), so a backup
    never slows down what the user is doing. No-op elsewhere."""
    k = None
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            if process:
                k.SetPriorityClass(k.GetCurrentProcess(), 0x00100000)        # PROCESS_MODE_BACKGROUND_BEGIN
            else:
                k.SetThreadPriority(k.GetCurrentThread(), 0x00010000)        # THREAD_MODE_BACKGROUND_BEGIN
        except Exception:
            k = None
    try:
        yield
    finally:
        if k is not None:
            try:
                if process:
                    k.SetPriorityClass(k.GetCurrentProcess(), 0x00200000)    # ..._END
                else:
                    k.SetThreadPriority(k.GetCurrentThread(), 0x00020000)
            except Exception:
                pass


def peak_ram_mb() -> int | None:
    """Peak working set of this process in MB (Windows), for the log."""
    try:
        import ctypes
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        k, ps = ctypes.windll.kernel32, ctypes.windll.psapi
        k.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        pmc = PMC(); pmc.cb = ctypes.sizeof(pmc)
        if not ps.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return round(pmc.PeakWorkingSetSize / 1048576)
    except Exception:
        return None


def folder_error() -> str | None:
    """Why the backup folder cannot be used right now (e.g. the external drive is unplugged), else None."""
    try:
        backup_dir()
        return None
    except OSError as e:
        return str(e)[:200]


def list_snapshots() -> list[dict]:
    try:
        d = backup_dir()
    except OSError:
        return []
    out = [m for p in d.glob("photag-*.zip") if NAME_RE.match(p.name) and (m := _manifest(p))]
    for m in out:                                         # can this snapshot's photo files be restored?
        m["media_ok"] = bool(m.get("includes_media")) and (d / (m.get("media_dir") or MIRROR)).is_dir()
        # size of the whole backup as the user thinks of it: catalog + all its photo files (shared files are
        # hard links, so the disk is not used twice; see media_bytes() for the real space)
        mb = (m.get("media") or {}).get("total_bytes")
        if mb is None and m["media_ok"]:
            mb = sum(f.stat().st_size for f in (d / MIRROR).rglob("*") if f.is_file())
        m["total_bytes"] = m["bytes"] + (mb or 0)
    return sorted(out, key=lambda m: m["created"], reverse=True)


def last_backup_time() -> float | None:
    snaps = [m for m in list_snapshots() if m["reason"] in ("auto", "manual")]
    return snaps[0]["created"] if snaps else None


def _interval_seconds(s: dict) -> float:
    ov = os.environ.get("PHOTAG_BACKUP_INTERVAL_SECONDS")       # tests: shrink "a day" to a few seconds
    return float(ov) if ov else s["interval_hours"] * 3600


def next_due(now: float | None = None) -> float | None:
    s = get_settings()
    if not s["enabled"]:
        return None
    last = last_backup_time()
    return (last + _interval_seconds(s)) if last else (now or time.time())


def is_due(now: float | None = None) -> bool:
    nd = next_due(now)
    return nd is not None and (now or time.time()) >= nd


# ------------------------------------------------------------------ creating
def _counts(con) -> dict:
    one = lambda q: con.execute(q).fetchone()[0]
    return {"photos": one("SELECT COUNT(*) FROM photos WHERE trashed=0"), "albums": one("SELECT COUNT(*) FROM albums"),
            "tags": one("SELECT COUNT(*) FROM tags"), "people": one("SELECT COUNT(*) FROM people")}


def media_dir_name(snapshot_name: str) -> str:
    return "media-" + snapshot_name[:-4]                  # photag-<date>-<reason>.zip -> media-photag-<date>-<reason>


def _previous_media_dir(reduced: bool = False) -> Path | None:
    """The newest complete media set of the same kind (originals or reduced copies): the media folder of the newest snapshot
    that has one, else (originals only) the old shared mirror."""
    d = backup_dir()
    for m in list_snapshots():
        if m.get("media_dir") and bool(m.get("media_compressed")) == reduced and (d / m["media_dir"]).is_dir():
            return d / m["media_dir"]
    old = d / MIRROR
    return old if (old.is_dir() and not reduced) else None


def _media_files(root: Path):
    """The photo / video files of a media folder (not the temporary .part files, not our own index)."""
    return [p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".part") and p.name != INDEX]


def _same(a: Path, st) -> bool:
    try:
        o = a.stat()
        return o.st_size == st.st_size and int(o.st_mtime) >= int(st.st_mtime) - 2
    except OSError:
        return False


def _mirror_media(dst: Path, progress=None, reduce: dict | None = None) -> dict:
    """Build a complete copy of the photo files in dst (a new folder): hard-link what the previous set already has,
    copy the rest. With `reduce` ({quality, max_side, include_videos}) photos are re-encoded smaller instead of copied."""
    from . import compress
    reduced = reduce is not None
    prev = _previous_media_dir(reduced)
    dst.mkdir(parents=True, exist_ok=True)
    pidx = {}
    if reduced and prev is not None:
        try:
            pidx = json.loads((prev / INDEX).read_text("utf-8"))
        except (OSError, ValueError):
            pidx = {}
    q, ms = (reduce["quality"], reduce["max_side"]) if reduced else (0, 0)
    files = [p for p in PATHS.media.rglob("*") if p.is_file() and ".compress_tmp" not in p.parts]
    if reduced and not reduce.get("include_videos", True):
        files = [p for p in files if p.suffix.lower() not in images.VIDEO_EXT]
    plan, need, est = [], 0, 0           # plan item: (src, out, old file to link or None, "encode" | "copy", stat)
    for src in files:
        rel = src.relative_to(PATHS.media)
        st = src.stat()
        old = prev / rel if prev else None
        encode = reduced and src.suffix.lower() in compress.IMAGE_OK
        if encode:
            e = pidx.get(rel.as_posix())
            reuse = (old is not None and old.is_file() and e and e.get("s") == st.st_size and abs(e.get("m", 0) - int(st.st_mtime)) <= 2
                     and e.get("q") == q and e.get("x") == ms)
        else:
            reuse = old is not None and _same(old, st)
        plan.append((src, dst / rel, old if reuse else None, "encode" if encode else "copy", st))
        if not reuse:
            need += st.st_size
            est += st.st_size * (0.4 if encode else 1)
    free = shutil.disk_usage(dst).free
    if est + 200 * 1024 * 1024 > free:                   # never fill the backup disk to the brim
        raise NoSpaceError(int(est), free)
    todo = [x for x in plan if x[2] is None]
    stats = {"copied": 0, "encoded": 0, "linked": 0, "bytes_copied": 0, "done": 0}
    index: dict[str, dict] = {}
    if progress:
        progress.total = max(1, need); progress.done = 0

    def work(item):
        src, out, old, how, st = item
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        did = "copied"
        if how == "encode":
            try:
                compress._encode_image(src, tmp, {"quality": q, "max_side": ms})
                if tmp.stat().st_size < st.st_size:
                    did = "encoded"
                else:
                    tmp.unlink(missing_ok=True)          # not smaller: keep the original as it is
            except Exception:
                tmp.unlink(missing_ok=True)               # a photo we cannot re-encode (damaged, animated...): copy it
        if did == "copied":
            shutil.copy2(src, tmp)
        os.replace(tmp, out)
        os.utime(out, (st.st_atime, st.st_mtime))         # same time stamp as the source: later backups can tell it is up to date
        return item, did

    for src, out, old, how, st in [x for x in plan if x[2] is not None]:
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(old, out)
            stats["linked"] += 1
            if how == "encode":
                index[src.relative_to(PATHS.media).as_posix()] = {"s": st.st_size, "m": int(st.st_mtime), "q": q, "x": ms}
        except OSError:
            todo.append((src, out, None, how, st))        # no hard links here: copy / encode instead
    ex = cf.ThreadPoolExecutor(2 if reduced else 1)
    try:
        for item, did in ex.map(work, todo):
            src, out, _, how, st = item
            stats[did] += 1
            stats["bytes_copied"] += out.stat().st_size
            stats["done"] += st.st_size
            if how == "encode":
                index[src.relative_to(PATHS.media).as_posix()] = {"s": st.st_size, "m": int(st.st_mtime), "q": q, "x": ms}
            if progress:
                progress.done = stats["done"]
                progress.say("Copying photo and video files… {done} of {total} files", done=stats["copied"] + stats["encoded"], total=len(todo))
                if getattr(progress, "cancel", False):
                    raise RuntimeError("cancelled")
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    if reduced:
        (dst / INDEX).write_text(json.dumps(index), "utf-8")
    out_bytes = sum(o.stat().st_size for _, o, _, _, _ in plan if o.exists())
    res = {"files": len(plan), "copied": stats["copied"] + stats["encoded"], "linked": stats["linked"], "bytes_copied": stats["bytes_copied"],
           "total_bytes": out_bytes if reduced else sum(st.st_size for *_, st in plan)}
    if reduced:
        res.update(reduced=True, encoded=stats["encoded"], quality=q, max_side=ms, source_bytes=sum(st.st_size for *_, st in plan),
                   include_videos=bool(reduce.get("include_videos", True)))
    return res


class NoSpaceError(Exception):
    def __init__(self, need: int, free: int):
        super().__init__(f"need {need} bytes, {free} free")
        self.need, self.free = need, free


def folder_bytes(*paths: Path) -> int:
    """Real space used: a file that is hard-linked into several snapshots counts once."""
    total, seen = 0, set()
    for path in paths:
        for root, _, names in os.walk(path):
            for n in names:
                try:
                    st = os.stat(os.path.join(root, n))
                except OSError:
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                total += st.st_size
    return total


def media_bytes() -> int:
    """Space used by all photo copies in the backup folder (shared files counted once)."""
    d = backup_dir()
    return folder_bytes(*[p for p in d.iterdir() if p.is_dir() and (p.name == MIRROR or p.name.startswith("media-"))])


# ------------------------------------------------------------------ never silently forget: state, lock, health
STATE_FILE = config.settings_dir() / "backup_state.json"
RETRY_AFTER = 30 * 60           # after a failed automatic backup, wait this long before trying again
LOCK_STALE = 3 * 3600


class BusyError(Exception):
    """Another backup (the app's or the scheduled task's) is running right now."""


def get_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        return {}


def _record(ok: bool, by: str, error=None, now: float | None = None):
    st = get_state()
    now = now or time.time()
    st.update(last_attempt=now, last_by=by)
    if ok:
        st.update(last_success=now, last_error=None, fail_count=0)
    else:
        st.update(last_error=str(error)[:300], fail_count=int(st.get("fail_count", 0)) + 1)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), "utf-8")
    os.replace(tmp, STATE_FILE)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)                  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False                                       # no such process
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_is_stale(p: Path) -> bool:
    """A lock is left over when its backup died (the installer closed it, a crash, a power cut) or is absurdly old."""
    try:
        if time.time() - p.stat().st_mtime > LOCK_STALE:
            return True
        pid = int(p.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return not _pid_alive(pid)


def _lock() -> Path:
    p = backup_dir() / ".backup.lock"
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time()}".encode()); os.close(fd)
            return p
        except FileExistsError:
            if _lock_is_stale(p):
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            raise BusyError()
    raise BusyError()


def create_snapshot(reason: str = "manual", progress=None, force_media: bool = False) -> dict:
    lock = _lock()
    try:
        return _create_snapshot(reason, progress, force_media)
    finally:
        lock.unlink(missing_ok=True)


def create_tracked(reason: str, by: str, progress=None, now: float | None = None) -> dict:
    """create_snapshot + remember the outcome, so a failure can be shown instead of lurking in a log."""
    try:
        m = create_snapshot(reason, progress)
    except BusyError:
        raise
    except Exception as e:
        _record(False, by, e, now)
        raise
    _record(True, by, None, now)
    return m


def auto_due(now: float | None = None) -> str | None:
    """None = an automatic backup should run now; otherwise why not."""
    now = now or time.time()
    if not get_settings()["enabled"]:
        return "disabled"
    if not is_due(now):
        return "not due"
    st = get_state()
    if st.get("last_error") and now - st.get("last_attempt", 0) < RETRY_AFTER:
        return "retry later"
    return None


def run_if_due(by: str = "app", force: bool = False, now: float | None = None) -> dict:
    why = None if force else auto_due(now)
    if why:
        return {"ran": False, "reason": why}
    try:
        return {"ran": True, **create_tracked("manual" if force else "auto", by, now=now)}
    except BusyError:
        return {"ran": False, "reason": "another backup is running"}
    except Exception as e:
        return {"ran": False, "error": str(e)}


def health(now: float | None = None) -> dict:
    """Everything the UI needs to say whether backups are really happening."""
    from . import backup_task
    now = now or time.time()
    s, st, last = get_settings(), get_state(), last_backup_time()
    interval = _interval_seconds(s)
    overdue = bool(s["enabled"] and last is not None and now - last > max(2 * interval, interval + 6 * 3600))
    ferr = folder_error()
    if ferr and last is None:                       # the folder is unreachable: fall back to what we remember
        last = st.get("last_success")
        overdue = bool(s["enabled"] and last is not None and now - last > max(2 * interval, interval + 6 * 3600))
    return {"enabled": s["enabled"], "last_success": last, "never": last is None, "overdue": overdue, "folder_error": ferr,
            "last_attempt": st.get("last_attempt"), "last_by": st.get("last_by"),
            "last_error": st.get("last_error"), "fail_count": st.get("fail_count", 0), "task": backup_task.status(),
            "verify": st.get("last_verify")}


def _create_snapshot(reason: str = "manual", progress=None, force_media: bool = False) -> dict:
    if reason not in REASONS:
        raise ValueError(reason)
    s = get_settings()
    d = backup_dir()
    now = time.time()
    base = f"photag-{datetime.fromtimestamp(now):%Y%m%d-%H%M%S}-{reason}"
    final = d / f"{base}.zip"
    n = 1
    while final.exists():
        final = d / f"{base}-{n}.zip"; n += 1
    for old in list(d.glob("media-*.part")) + list(d.glob("photag-backup-*")):      # leftovers of a backup that died
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    work = Path(tempfile.mkdtemp(prefix="photag-backup-", dir=d))
    try:
        if progress: progress.say("Creating a copy of the catalog…")
        tmpdb = work / "catalog.db"
        src = db.connect()
        dst = sqlite3.connect(tmpdb)
        try:
            src.backup(dst)                                   # consistent even while the app is writing
        finally:
            dst.close(); src.close()
        chk = sqlite3.connect(tmpdb)
        try:
            ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
            counts = _counts(chk)
        finally:
            chk.close()
        if ok != "ok":
            raise RuntimeError(f"integrity_check: {ok}")
        settings = {k: v for k, v in config.read_all().items() if k not in ("library_root", "update_skipped", "backup")}
        media, mdir, mpart = None, None, None
        # a backup taken before compressing always holds the photo files (that is what makes the compression undoable)
        if (s["include_media"] and reason in ("auto", "manual")) or force_media or reason == "before-compress":
            if progress: progress.say("Copying photo files…")
            mdir = media_dir_name(final.name)
            mpart = d / (mdir + ".part")
            shutil.rmtree(mpart, ignore_errors=True)
            # reduced copies only for the regular backups: the safety snapshots made before a restore / update / compression
            # exist to undo it, so they must hold the real files
            reduce = ({"quality": s["compress_quality"], "max_side": s["compress_max_side"], "include_videos": s["include_videos"]}
                      if s["compress_media"] and reason in ("auto", "manual") and not force_media else None)
            media = _mirror_media(mpart, progress, reduce)
        manifest = {"created": now, "reason": reason, "app_version": __version__, "includes_media": bool(media),
                    "media": media, "media_dir": mdir, "media_compressed": bool(media and media.get("reduced")), **counts}
        part = final.with_name(final.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(tmpdb, "catalog.db")
            z.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=1))
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        with zipfile.ZipFile(part) as z:
            if z.testzip() is not None:
                raise RuntimeError("zip verification failed")
        if mpart is not None:
            os.replace(mpart, d / mdir)                       # the photo set appears only when complete
        os.replace(part, final)                               # appears only when complete and verified
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for p in d.glob("*.part"):
            p.unlink(missing_ok=True)
        for p in d.glob("media-*.part"):
            shutil.rmtree(p, ignore_errors=True)
    prune(s["keep"])
    return {**manifest, "name": final.name, "bytes": final.stat().st_size}


def prune(keep: int):
    """Keep the newest `keep` snapshots (of every kind) and delete the older ones."""
    seen: dict[str, int] = {}
    for m in list_snapshots():                            # newest first; each kind has its own allowance, so a
        kind = "regular" if m["reason"] in ("auto", "manual") else m["reason"]       # run of compressions never pushes out the daily backups
        seen[kind] = seen.get(kind, 0) + 1
        if seen[kind] > (keep if kind == "regular" else MAX_SAFETY):
            _remove(m)
    _drop_old_mirror()


def _remove(m: dict):
    d = backup_dir()
    (d / m["name"]).unlink(missing_ok=True)
    if m.get("media_dir"):
        shutil.rmtree(d / m["media_dir"], ignore_errors=True)   # hard-linked files stay for the snapshots that share them


def _drop_old_mirror():
    """The shared media-mirror of older versions goes when no snapshot needs it any more."""
    d = backup_dir()
    old = d / MIRROR
    if old.is_dir() and not any(m.get("includes_media") and not m.get("media_dir") for m in list_snapshots()):
        shutil.rmtree(old, ignore_errors=True)


def delete_snapshot(name: str):
    if not NAME_RE.match(name):
        raise ValueError("name")
    m = next((x for x in list_snapshots() if x["name"] == name), None)
    if m:
        _remove(m)
    else:
        (backup_dir() / name).unlink(missing_ok=True)
    _drop_old_mirror()


# ------------------------------------------------------------------ restoring
def restore_snapshot(name: str, restore_media: bool = False, restore_settings: bool = False, progress=None,
                     overwrite_changed: bool = False) -> dict:
    if not NAME_RE.match(name):
        raise ValueError("name")
    path = backup_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    work = Path(tempfile.mkdtemp(prefix="photag-restore-"))
    try:
        if progress: progress.say("Checking the backup…")
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise RuntimeError("zip is damaged")
            z.extract("catalog.db", work)
            settings = json.loads(z.read("settings.json")) if "settings.json" in z.namelist() else {}
            manifest = json.loads(z.read("manifest.json"))
        if manifest.get("media_compressed"):
            overwrite_changed = False                         # a reduced copy never replaces a file that exists: it only fills gaps
        snap = sqlite3.connect(work / "catalog.db")
        try:
            if snap.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("catalog in the backup is damaged")
            if progress: progress.say("Saving a safety copy of the current state…")
            # restoring is itself undoable; when files will be replaced the safety copy holds the current files too
            safety = create_snapshot("before-restore", force_media=bool(restore_media and overwrite_changed))
            if progress: progress.say("Restoring the catalog…")
            live = db.connect()
            try:
                snap.backup(live)                             # one transaction: all or nothing
                live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                live.close()
        finally:
            snap.close()
        db.init_db().close()                                  # an older snapshot may lack newer columns
        if restore_settings and settings:
            config.merge_settings({k: v for k, v in settings.items() if k in ("ai", "handbrake_path")})
        copied = 0
        mirror = backup_dir() / (manifest.get("media_dir") or MIRROR)
        if restore_media and mirror.is_dir():
            files = _media_files(mirror)
            if progress: progress.total = len(files); progress.done = 0
            for i, src in enumerate(files):
                out = PATHS.media / src.relative_to(mirror)
                if not out.exists() or (overwrite_changed and not _same(src, out.stat())):
                    out.parent.mkdir(parents=True, exist_ok=True)
                    tmp = out.with_name(out.name + ".part")
                    shutil.copy2(src, tmp); os.replace(tmp, out); copied += 1
                if progress and i % 20 == 0:
                    progress.done = i + 1
                    progress.say("Restoring photo files… {done} of {total}", done=i + 1, total=len(files))
        con = db.connect()
        try:
            rels = [r[0] for r in con.execute("SELECT rel_path FROM photos")]
        finally:
            con.close()
        missing = sum(1 for r in rels if not (PATHS.media / r).exists())
        return {"photos": manifest.get("photos"), "media_copied": copied, "missing_files": missing,
                "safety": safety["name"], "created": manifest["created"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)



# ------------------------------------------------------------------ checking the newest backup (read-only, never restores)
VERIFY_EVERY = 7 * 86400         # the newest backup is checked once a week
VERIFY_SAMPLE = 25               # photo files of the backup that are read in full (finds unreadable disk areas)


def _verify_every() -> float:
    ov = os.environ.get("PHOTAG_VERIFY_EVERY_SECONDS")       # tests
    return float(ov) if ov else VERIFY_EVERY


def verify_snapshot(m: dict) -> list[tuple[str, dict]]:
    """Check one backup without changing anything. Returns the problems found as (message, values): empty = all fine.
    ZIP checksums, the catalog inside it, and (when it has photo files) that the set is complete and readable."""
    import random
    name, d, bad = m["name"], backup_dir(), []
    try:
        with zipfile.ZipFile(d / name) as z:
            if z.testzip() is not None:
                return [("The backup {name} is damaged: its file does not pass the check", {"name": name})]
            work = Path(tempfile.mkdtemp(prefix="photag-verify-"))
            try:
                z.extract("catalog.db", work)
                con = sqlite3.connect(work / "catalog.db")
                try:
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    con.close()
            finally:
                shutil.rmtree(work, ignore_errors=True)
        if ok != "ok":
            return [("The catalog inside the backup {name} is damaged", {"name": name})]
    except Exception:                                  # bad ZIP, bad checksum, bad compressed data, unreadable disk...
        if not (d / name).exists():
            raise FileNotFoundError(name)            # deleted meanwhile (pruned): not a problem to report
        return [("The backup {name} is damaged: its file does not pass the check", {"name": name})]
    if m.get("includes_media"):
        mdir = d / (m.get("media_dir") or MIRROR)
        if not mdir.is_dir():
            return [("The photo files of the backup {name} are missing from the backup folder", {"name": name})]
        files = _media_files(mdir)
        want = (m.get("media") or {}).get("files")
        if want is not None and len(files) != want:
            bad.append(("The backup {name} should hold {want} photo files but {found} were found", {"name": name, "want": want, "found": len(files)}))
        unreadable = 0
        for p in random.sample(files, min(VERIFY_SAMPLE, len(files))):
            try:
                with open(p, "rb") as f:
                    while f.read(1 << 20):
                        pass
            except OSError:
                unreadable += 1
        if unreadable:
            bad.append(("{n} photo files of the backup {name} cannot be read", {"name": name, "n": unreadable}))
    return bad


def _newest_to_check() -> dict | None:
    """The newest backup, including one so damaged that its manifest cannot even be read (that is exactly what must be reported)."""
    d = backup_dir()
    good = {m["name"]: m for m in list_snapshots()}
    files = sorted((p for p in d.glob("photag-*.zip") if NAME_RE.match(p.name)), key=lambda p: p.name, reverse=True)
    regular = [p for p in files if "-auto" in p.name or "-manual" in p.name]
    for p in regular or files:
        return good.get(p.name) or {"name": p.name, "created": p.stat().st_mtime, "reason": "manual", "includes_media": False}
    return None


def verify_last(by: str = "app") -> dict | None:
    """Check the newest backup and remember the outcome (the app shows a warning when something is wrong). None = no backup yet."""
    m = _newest_to_check()
    if not m:
        return None
    try:
        with background_mode():
            problems = verify_snapshot(m)
    except FileNotFoundError:
        return None
    r = {"at": time.time(), "name": m["name"], "created": m["created"], "ok": not problems, "by": by,
         "problems": [{"key": k, "vars": v} for k, v in problems]}
    st = get_state()
    st["last_verify"] = r
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), "utf-8")
    os.replace(tmp, STATE_FILE)
    return r


def verify_due(now: float | None = None) -> bool:
    lv = get_state().get("last_verify")
    return not lv or (now or time.time()) - lv.get("at", 0) >= _verify_every()


def verify_if_due(by: str = "app") -> dict | None:
    """Once a week: check the newest backup. Skipped while a backup is being written (it would look half-finished)."""
    if not verify_due() or folder_error():
        return None
    lock = backup_dir() / ".backup.lock"
    if lock.exists() and not _lock_is_stale(lock):
        return None
    return verify_last(by)


# ------------------------------------------------------------------ jobs (run through the server's job runner)
def run_backup(reason: str, progress):
    try:
        progress.state = "backing_up"; progress.say("Creating a backup…")
        with background_mode():                       # never slow down the app while it backs up
            m = create_tracked(reason, "app", progress)
        progress.result = m
        progress.state = "done"
        progress.say_parts(("Backup saved", {}), ("{n} photos in the catalog", {"n": m["photos"]}))
    except BusyError:
        progress.fail("Another backup is running right now, try again in a few minutes")
    except NoSpaceError as e:
        progress.fail("Not enough free space on the backup disk: {need} MB needed, {free} MB free. Choose a folder on another disk",
                      need=round(e.need / 1048576), free=round(e.free / 1048576))
    except Exception as e:
        progress.fail("Backup failed: {error}", error=str(e)[:200])


def run_restore(name: str, restore_media: bool, restore_settings: bool, overwrite_changed: bool, progress):
    try:
        progress.state = "restoring"; progress.say("Restoring from backup…")
        r = restore_snapshot(name, restore_media, restore_settings, progress, overwrite_changed)
        progress.result = r
        progress.state = "done"
        parts = [("Catalog restored", {}), ("{n} photos", {"n": r["photos"]})]
        if r["missing_files"]:
            parts.append(("{n} photo files are missing from the folder", {"n": r["missing_files"]}))
        progress.say_parts(*parts)
    except Exception as e:
        progress.fail("Restore failed: {error}", error=str(e)[:200])


def run_verify(progress):
    """The "Check the backup now" button."""
    try:
        progress.state = "verifying"; progress.say("Checking the newest backup…")
        r = verify_last("manual")
        progress.result = r
        progress.state = "done"
        if r is None:
            progress.say("There is no backup to check yet")
        elif r["ok"]:
            progress.say("The backup check found no problems")
        else:
            progress.say_parts(*[(p["key"], p["vars"]) for p in r["problems"]])
    except Exception as e:
        progress.fail("Backup check failed: {error}", error=str(e)[:200])
