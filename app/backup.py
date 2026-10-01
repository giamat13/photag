"""Periodic backups of the catalog (and optionally the photo files), restorable from inside the app.

A snapshot is one ZIP in the backup folder (default: <library>/backups):
    catalog.db     a consistent copy made with SQLite's online-backup API (safe while the app is running),
                   checked with PRAGMA integrity_check before it is accepted
    settings.json  the app settings (AI provider / key, HandBrake path ...); the key stays DPAPI-encrypted
    manifest.json  when / why / how many photos
The ZIP is written under a temporary name and renamed only when it is complete and verified, so a power
cut never leaves a half-written snapshot that looks real.

"include photo files" adds an incremental mirror (<backup folder>/media-mirror): only new or changed files
are copied each time, so the second backup is fast.

Restoring first takes a safety snapshot of the current state ("before-restore"), then loads the chosen
catalog into the live database in one transaction. Photo files are only ever ADDED back (never deleted).
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, db
from .config import PATHS
from .version import __version__

DEFAULTS = {"enabled": True, "interval_hours": 24, "keep": 10, "include_media": True, "folder": None}
REASONS = ("auto", "manual", "before-restore", "before-update")
NAME_RE = re.compile(r"^photag-\d{8}-\d{6}-(auto|manual|before-restore|before-update)(-\d+)?\.zip$")
MIRROR = "media-mirror"
KEEP_MIN, KEEP_MAX = 3, 200


# ------------------------------------------------------------------ settings
def get_settings() -> dict:
    s = {**DEFAULTS, **(config.get_backup() or {})}
    s["interval_hours"] = max(1, min(24 * 90, int(DEFAULTS["interval_hours"] if s["interval_hours"] is None else s["interval_hours"])))
    s["keep"] = max(KEEP_MIN, min(KEEP_MAX, int(DEFAULTS["keep"] if s["keep"] is None else s["keep"])))
    s["enabled"] = bool(s["enabled"])
    s["include_media"] = bool(s["include_media"])
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


def list_snapshots() -> list[dict]:
    d = backup_dir()
    out = [m for p in d.glob("photag-*.zip") if NAME_RE.match(p.name) and (m := _manifest(p))]
    return sorted(out, key=lambda m: m["created"], reverse=True)


def last_backup_time() -> float | None:
    snaps = [m for m in list_snapshots() if m["reason"] in ("auto", "manual")]
    return snaps[0]["created"] if snaps else None


def next_due(now: float | None = None) -> float | None:
    s = get_settings()
    if not s["enabled"]:
        return None
    last = last_backup_time()
    return (last + s["interval_hours"] * 3600) if last else (now or time.time())


def is_due(now: float | None = None) -> bool:
    nd = next_due(now)
    return nd is not None and (now or time.time()) >= nd


# ------------------------------------------------------------------ creating
def _counts(con) -> dict:
    one = lambda q: con.execute(q).fetchone()[0]
    return {"photos": one("SELECT COUNT(*) FROM photos WHERE trashed=0"), "albums": one("SELECT COUNT(*) FROM albums"),
            "tags": one("SELECT COUNT(*) FROM tags"), "people": one("SELECT COUNT(*) FROM people")}


def _mirror_media(progress=None) -> dict:
    """Copy new / changed photo files into <backup folder>/media-mirror (incremental)."""
    dst = backup_dir() / MIRROR
    dst.mkdir(parents=True, exist_ok=True)
    files = [p for p in PATHS.media.rglob("*") if p.is_file() and ".compress_tmp" not in p.parts]
    todo, need = [], 0
    for src in files:                                   # what is new or changed since the last backup
        out = dst / src.relative_to(PATHS.media)
        st = src.stat()
        try:
            same = out.exists() and out.stat().st_size == st.st_size and int(out.stat().st_mtime) >= int(st.st_mtime) - 2
        except OSError:
            same = False
        if not same:
            todo.append((src, out)); need += st.st_size
    free = shutil.disk_usage(dst).free
    if need + 200 * 1024 * 1024 > free:                 # never fill the backup disk to the brim
        raise NoSpaceError(need, free)
    copied = nbytes = 0
    if progress:
        progress.total = max(1, need); progress.done = 0
    for src, out in todo:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        shutil.copy2(src, tmp); os.replace(tmp, out)
        n = out.stat().st_size
        copied += 1; nbytes += n
        if progress:
            progress.done = nbytes
            progress.say("מעתיק קבצי תמונות וסרטונים… {done} מתוך {total} קבצים", done=copied, total=len(todo))
            if getattr(progress, "cancel", False):
                break
    return {"files": len(files), "copied": copied, "bytes_copied": nbytes}


class NoSpaceError(Exception):
    def __init__(self, need: int, free: int):
        super().__init__(f"need {need} bytes, {free} free")
        self.need, self.free = need, free


def folder_bytes(path: Path) -> int:
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def create_snapshot(reason: str = "manual", progress=None) -> dict:
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
    work = Path(tempfile.mkdtemp(prefix="photag-backup-", dir=d))
    try:
        if progress: progress.say("יוצר עותק של הקטלוג…")
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
        media = None
        if s["include_media"] and reason in ("auto", "manual"):
            if progress: progress.say("מעתיק קבצי תמונות…")
            media = _mirror_media(progress)
        manifest = {"created": now, "reason": reason, "app_version": __version__, "includes_media": bool(media),
                    "media": media, **counts}
        part = final.with_name(final.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(tmpdb, "catalog.db")
            z.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=1))
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        with zipfile.ZipFile(part) as z:
            if z.testzip() is not None:
                raise RuntimeError("zip verification failed")
        os.replace(part, final)                               # appears only when complete and verified
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for p in d.glob("*.part"):
            p.unlink(missing_ok=True)
    prune(s["keep"])
    return {**manifest, "name": final.name, "bytes": final.stat().st_size}


def prune(keep: int):
    """Keep the newest `keep` snapshots (of every kind) and delete the older ones."""
    snaps = list_snapshots()
    extra = snaps[keep:]
    for m in extra:
        (backup_dir() / m["name"]).unlink(missing_ok=True)


def delete_snapshot(name: str):
    if not NAME_RE.match(name):
        raise ValueError("name")
    (backup_dir() / name).unlink(missing_ok=True)


# ------------------------------------------------------------------ restoring
def restore_snapshot(name: str, restore_media: bool = False, restore_settings: bool = False, progress=None) -> dict:
    if not NAME_RE.match(name):
        raise ValueError("name")
    path = backup_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    work = Path(tempfile.mkdtemp(prefix="photag-restore-"))
    try:
        if progress: progress.say("בודק את הגיבוי…")
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise RuntimeError("zip is damaged")
            z.extract("catalog.db", work)
            settings = json.loads(z.read("settings.json")) if "settings.json" in z.namelist() else {}
            manifest = json.loads(z.read("manifest.json"))
        snap = sqlite3.connect(work / "catalog.db")
        try:
            if snap.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("catalog in the backup is damaged")
            if progress: progress.say("שומר עותק ביטחון של המצב הנוכחי…")
            safety = create_snapshot("before-restore")        # restoring is itself undoable
            if progress: progress.say("משחזר את הקטלוג…")
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
        mirror = backup_dir() / MIRROR
        if restore_media and mirror.is_dir():
            files = [p for p in mirror.rglob("*") if p.is_file() and not p.name.endswith(".part")]
            if progress: progress.total = len(files); progress.done = 0
            for i, src in enumerate(files):
                out = PATHS.media / src.relative_to(mirror)
                if not out.exists():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    tmp = out.with_name(out.name + ".part")
                    shutil.copy2(src, tmp); os.replace(tmp, out); copied += 1
                if progress and i % 20 == 0:
                    progress.done = i + 1
                    progress.say("מחזיר קבצי תמונות… {done} מתוך {total}", done=i + 1, total=len(files))
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


# ------------------------------------------------------------------ jobs (run through the server's job runner)
def run_backup(reason: str, progress):
    try:
        progress.state = "backing_up"; progress.say("יוצר גיבוי…")
        m = create_snapshot(reason, progress)
        progress.result = m
        progress.state = "done"
        progress.say_parts(("הגיבוי נשמר", {}), ("{n} תמונות בקטלוג", {"n": m["photos"]}))
    except NoSpaceError as e:
        progress.fail("אין מספיק מקום פנוי בדיסק של הגיבויים: צריך {need} MB, פנויים {free} MB. בחרו תיקייה בדיסק אחר",
                      need=round(e.need / 1048576), free=round(e.free / 1048576))
    except Exception as e:
        progress.fail("הגיבוי נכשל: {error}", error=str(e)[:200])


def run_restore(name: str, restore_media: bool, restore_settings: bool, progress):
    try:
        progress.state = "restoring"; progress.say("משחזר מגיבוי…")
        r = restore_snapshot(name, restore_media, restore_settings, progress)
        progress.result = r
        progress.state = "done"
        parts = [("הקטלוג שוחזר", {}), ("{n} תמונות", {"n": r["photos"]})]
        if r["missing_files"]:
            parts.append(("{n} קבצי תמונות חסרים בתיקייה", {"n": r["missing_files"]}))
        progress.say_parts(*parts)
    except Exception as e:
        progress.fail("השחזור נכשל: {error}", error=str(e)[:200])
