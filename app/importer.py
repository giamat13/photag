"""Import a Google Takeout (Google Photos) ZIP into the library.

Nothing from the ZIP is dropped: media, albums, per-photo metadata, people
name-tags, GPS, favorites, memory titles and shared-album comments all land in
the catalog. Media is streamed out of the ZIP (the 19 GB archive is never fully
extracted) and de-duplicated by SHA-256 so a photo that appears in several
album folders is stored once but belongs to every album.
"""
import json
import os
import re
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from . import cloud, db, images
from .config import PATHS, TRASH_RETENTION_DAYS

ROOT_PREFIX = "Takeout/Google Photos/"
YEAR_RE = re.compile(r"(?:תמונות משנת|Photos from)\s*(\d{4})")
LATIN_PAIR_RE = re.compile(r"^[A-Za-z].*,")  # e.g. "Itamar, dafna" -> shared/people album
SUPP_RE = re.compile(r"\.supplemental[\w-]*\.json$", re.I)


def delete_forever(con, rows) -> int:
    """Permanently delete these photos (rows with id, sha256, rel_path, orig_backup): their media / thumb / backup
    files and every DB row referencing them. Only used on photos that are already in the trash.
    A photo that lives in the user's own folder (photag only references it) has its file sent to the Windows Recycle Bin,
    never deleted outright; if that is not possible the photo stays in the trash (nothing is lost) and is not counted."""
    from . import refmode
    rows = list(rows)
    done = 0
    for r in rows:
        if refmode.is_external(r["rel_path"]):
            if not refmode.recycle(r["rel_path"]):
                continue
            images.thumb_path(r["sha256"]).unlink(missing_ok=True)
        else:
            for p in (PATHS.media / r["rel_path"], images.thumb_path(r["sha256"])):
                p.unlink(missing_ok=True)
        if r["orig_backup"]:
            (PATHS.media / r["orig_backup"]).unlink(missing_ok=True)
        for b in con.execute("SELECT backup_rel FROM video_backups WHERE photo_id=?", (r["id"],)).fetchall():
            (PATHS.media / b["backup_rel"]).unlink(missing_ok=True)
        pid = r["id"]
        for table in ("photo_albums", "photo_people", "photo_tags", "faces", "video_backups", "ref_files", "photo_analysis", "photo_clip"):
            con.execute(f"DELETE FROM {table} WHERE photo_id=?", (pid,))
        con.execute("DELETE FROM photos WHERE id=?", (pid,))
        done += 1
    if rows:
        con.commit()
    return done


def delete_trashed_by_id(con, ids) -> int:
    """Delete for good the given photos, but only those that are in the trash (never a photo that is not)."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        return 0
    q = ",".join("?" * len(ids))
    rows = con.execute(f"SELECT id, sha256, rel_path, orig_backup FROM photos WHERE trashed=1 AND id IN ({q})", ids).fetchall()
    return delete_forever(con, rows)


TRASH_DAYS_MIN, TRASH_DAYS_MAX = 1, 3650


def trash_days(con) -> int:
    """How many days a photo stays in the trash before it is deleted for good (a setting of the catalog, default 60)."""
    try:
        d = int(db.get_setting(con, "trash_days", TRASH_RETENTION_DAYS))
    except (TypeError, ValueError):
        d = TRASH_RETENTION_DAYS
    return max(TRASH_DAYS_MIN, min(TRASH_DAYS_MAX, d))


def set_trash_days(con, days: int) -> int:
    days = max(TRASH_DAYS_MIN, min(TRASH_DAYS_MAX, int(days)))
    db.set_setting(con, "trash_days", days)
    return days


def expired_trash(con, days: int):
    cutoff = int(time.time()) - days * 86400
    return con.execute("SELECT id, sha256, rel_path, orig_backup FROM photos "
                       "WHERE trashed=1 AND trashed_at IS NOT NULL AND trashed_at < ?", (cutoff,)).fetchall()


def purge_expired_trash(con, days: int | None = None):
    """Permanently delete photos that have sat in the trash for over `days` (the catalog's setting by default)."""
    return delete_forever(con, expired_trash(con, trash_days(con) if days is None else days))


def _album_kind(name: str) -> str:
    if YEAR_RE.search(name):
        return "year"
    if LATIN_PAIR_RE.match(name):
        return "people-share"
    return "album"


def _json_target(jname: str) -> str:
    """Intended media filename for a sidecar json basename."""
    if SUPP_RE.search(jname):
        return SUPP_RE.sub("", jname)
    if jname.lower().endswith(".json"):
        return jname[:-5]
    return jname


def match_sidecars(media: list[str], jsons: list[str]) -> dict[str, str]:
    """Map media basename -> json basename within one folder.

    Google truncates long sidecar names and shuffles the ``(n)`` duplicate
    counter, so exact matching misses. Strategy: exact, then truncation-prefix,
    then counter-normalised.
    ponytail: heuristic matcher, ceiling = pathological truncations may miss a
    few sidecars (photo still imports, just without Google metadata).
    """
    targets = {j: _json_target(j) for j in jsons}
    out: dict[str, str] = {}
    used = set()

    def stem(s):  # drop last extension
        return s.rpartition(".")[0] or s

    def norm(s):  # drop "(n)" counters and spaces for fuzzy compare
        return re.sub(r"\(\d+\)|\s+", "", s).lower()

    for m in media:
        ms = stem(m)
        # 1) exact target
        hit = next((j for j, t in targets.items() if j not in used and t == m), None)
        # 2) truncated: Google truncates the sidecar's filename tail, so the
        #    json's target stem is a PREFIX of the media stem. Pick the longest
        #    (most specific) prefix so IMG_1 doesn't steal IMG_10's sidecar.
        if not hit:
            cands = [(len(stem(t)), j) for j, t in targets.items()
                     if j not in used and t and len(stem(t)) >= 6 and ms.startswith(stem(t))]
            if cands:
                hit = max(cands)[1]
        # 3) counter-normalised ( "IMG(1).jpg" <-> "IMG.jpg(1).json" )
        if not hit:
            nm = norm(m)
            hit = next((j for j, t in targets.items()
                        if j not in used and norm(t) == nm), None)
        if hit:
            out[m] = hit
            used.add(hit)
    return out


def _ts(node) -> int | None:
    try:
        return int(node["timestamp"])
    except Exception:
        return None


def _safe_component(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "_"


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suf, i = dest.stem, dest.suffix, 1
    while (c := dest.with_name(f"{stem} ({i}){suf}")).exists():
        i += 1
    return c


class Progress:
    """Job state for the UI. Messages are Hebrew source strings with {name}
    placeholders (key + vars), translated by the frontend's t()."""
    def __init__(self):
        self.state = "idle"; self.done = 0; self.total = 0; self.msg = ""; self.error = None
        self.key = None; self.vars = {}; self.parts = None; self.error_key = None
        self.cancel = False   # set by the UI to stop a long job (AI tagging, compression)
        self.result = None    # structured outcome for the UI (e.g. the compression report)
        self.extra = {}       # live details for the progress screen (encoder fps / ETA, which file of a batch...)

    def say(self, key, **vars):
        self.key, self.vars, self.parts = key, vars, None
        self.msg = key.format(**vars)

    def say_parts(self, *parts):
        """Several short messages shown joined by " · " (only the ones that apply)."""
        self.key, self.parts = None, [{"key": k, "vars": v} for k, v in parts]
        self.msg = " · ".join(k.format(**v) for k, v in parts)

    def fail(self, key, **vars):
        self.state, self.error_key, self.vars = "error", key, vars
        self.error = key.format(**vars)

    def as_dict(self):
        return {"state": self.state, "done": self.done, "total": self.total, "msg": self.msg, "error": self.error,
                "key": self.key, "vars": self.vars, "parts": self.parts, "error_key": self.error_key,
                "result": self.result, "extra": self.extra}


class ImportStats:
    """Live numbers for the import screen: what was added, skipped, failed or missing, how much data, the current file."""

    def __init__(self, progress, source: str, bytes_total: int = 0):
        self.p = progress
        self.d = {"source": source, "t0": time.time(), "added": 0, "duplicates": 0, "failed": 0, "missing": 0,
                  "bytes": 0, "bytes_total": bytes_total, "album": "", "current": "", "failures": []}
        self.publish()

    def publish(self):
        self.p.extra = {**self.d, "failures": list(self.d["failures"])}

    def item(self, status: str, name: str = "", nbytes: int = 0, album: str | None = None):
        key = {"added": "added", "duplicate": "duplicates", "failed": "failed", "missing": "missing"}[status]
        self.d[key] += 1
        self.d["bytes"] += nbytes
        self.d["current"] = name
        if album is not None:
            self.d["album"] = album
        if status in ("failed", "missing") and len(self.d["failures"]) < 30:
            self.d["failures"].append(name)
        self.publish()


_PART_RE = re.compile(r"^(?P<stem>.+)-(?P<n>\d{3,})\.zip$", re.I)


def takeout_parts(path: str) -> dict:
    """A Takeout export can come as several files: takeout-<stamp>-001.zip, -002.zip, ... Given any one of them,
    find the others in the same folder, and report part numbers that are missing in the sequence."""
    p = Path(path)
    m = _PART_RE.match(p.name)
    parts = [p]
    missing = []
    if m:
        stem = m.group("stem").lower()
        sibs = {}
        for q in p.parent.glob("*.zip"):
            mm = _PART_RE.match(q.name)
            if mm and mm.group("stem").lower() == stem:
                sibs[int(mm.group("n"))] = q
        if sibs:
            parts = [sibs[k] for k in sorted(sibs)]
            missing = [k for k in range(1, max(sibs) + 1) if k not in sibs]
    return {"parts": [{"path": str(q), "name": q.name, "bytes": q.stat().st_size} for q in parts if q.exists()], "missing": missing}


def run_import(zip_paths, progress: Progress):
    """Import one Google Takeout ZIP or all the parts of an export (a path or a list of paths)."""
    zip_paths = [zip_paths] if isinstance(zip_paths, (str, Path)) else list(zip_paths)
    con = db.init_db()
    mark_import_start(con)
    progress.state = "scanning"; progress.say("Reading the ZIP structure…")
    try:
        _do_import(zip_paths, con, progress)
        progress.state = "done"
        if progress.cancel:
            progress.say_parts(("Import cancelled", {}), ("{n} items imported", {"n": progress.extra.get("added", 0)}))
        else:
            progress.say("Import complete")
    except Exception as e:  # surface to UI instead of dying silently
        progress.fail("Import failed: {error}", error=str(e))
        raise
    finally:
        con.commit()


def _do_import(zip_paths, con, progress):
    zfs = [zipfile.ZipFile(p) for p in sorted(zip_paths, key=lambda x: str(x).lower())]
    try:
        _import_zips(zfs, con, progress)
    finally:
        for z in zfs:
            z.close()


def _import_zips(zfs, con, progress):
    # Merge all the parts into one view: an album can span several ZIPs, and a photo's .json can sit in another part.
    folders: dict[str, dict] = {}
    specials = {"memory": None, "comments": None}
    for zf in zfs:
        for n in zf.namelist():
            if not n.startswith(ROOT_PREFIX) or n.endswith("/"):
                continue
            rest = n[len(ROOT_PREFIX):]
            parts = rest.split("/")
            if len(parts) == 1:  # top-level special json (memory titles / comments)
                b = parts[0]
                if "זיכרונות" in b:
                    specials["memory"] = specials["memory"] or (zf, n)
                elif "תגובות" in b:
                    specials["comments"] = specials["comments"] or (zf, n)
                continue
            album = parts[0]
            f = folders.setdefault(album, {"media": {}, "json": {}, "album_meta": None})
            base = parts[-1]
            if base in ("מטא נתונים.json", "metadata.json"):
                f["album_meta"] = f["album_meta"] or (zf, n)
            elif base.lower().endswith(".json"):
                f["json"].setdefault(base, (zf, n))
            else:
                f["media"].setdefault(base, []).append((zf, n))

    # special artifacts
    if specials["memory"]:
        try:
            titles = json.loads(specials["memory"][0].read(specials["memory"][1]).decode("utf-8", "replace")).get("title", [])
            con.execute("DELETE FROM memory_titles")
            con.executemany("INSERT INTO memory_titles(title) VALUES(?)", [(t,) for t in titles])
        except Exception:
            pass
    if specials["comments"]:
        try:
            cm = json.loads(specials["comments"][0].read(specials["comments"][1]).decode("utf-8", "replace")).get("sharedAlbumComments", [])
            con.execute("DELETE FROM shared_comments")
            con.executemany("INSERT INTO shared_comments(text,liked,created_at,content_url) VALUES(?,?,?,?)",
                            [(c.get("text"), 1 if c.get("liked") else 0,
                              _ts(c.get("creationTime", {})), c.get("contentUrl")) for c in cm])
        except Exception:
            pass

    total_media = sum(len(v) for f in folders.values() for v in f["media"].values())
    total_bytes = sum(zf.getinfo(n).file_size for f in folders.values() for v in f["media"].values() for zf, n in v)
    progress.total = total_media
    progress.state = "importing"
    stats = ImportStats(progress, "takeout", total_bytes)

    for album, f in folders.items():
        if progress.cancel:
            break
        album_id = _upsert_album(con, f["album_meta"], album)
        pairs = match_sidecars(list(f["media"]), list(f["json"]))
        for base, items in f["media"].items():
            meta = {}
            if base in pairs:
                try:
                    jz, jn = f["json"][pairs[base]]
                    meta = json.loads(jz.read(jn).decode("utf-8", "replace"))
                except Exception:
                    meta = {}
            for zf, entry in items:                       # the same name in two parts: both are looked at (identical files dedupe)
                if progress.cancel:
                    break
                progress.done += 1
                if progress.done % 25 == 0:
                    progress.say("{album} — {done}/{total}", album=album, done=progress.done, total=progress.total)
                    con.commit()
                status, nbytes = _ingest_media(con, zf, entry, base, album_id, meta)
                stats.item(status, base, nbytes, album)
    con.commit()


def _upsert_album(con, meta_entry, name) -> int:
    desc = access = None; adate = None
    if meta_entry:
        try:
            m = json.loads(meta_entry[0].read(meta_entry[1]).decode("utf-8", "replace"))
            desc = m.get("description") or None
            access = m.get("access")
            adate = _ts(m.get("date", {}))
        except Exception:
            pass
    con.execute(
        "INSERT INTO albums(name,description,access,album_date,kind) VALUES(?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET description=COALESCE(excluded.description,albums.description),"
        "access=COALESCE(excluded.access,albums.access),album_date=COALESCE(excluded.album_date,albums.album_date)",
        (name, desc, access, adate, _album_kind(name)))
    return con.execute("SELECT id FROM albums WHERE name=?", (name,)).fetchone()["id"]


def _ingest_media(con, zf, entry, base, album_id, meta):
    taken = _ts(meta.get("photoTakenTime", {})) if meta else None
    created = _ts(meta.get("creationTime", {})) if meta else None
    geo = meta.get("geoData") or {}
    lat = geo.get("latitude") or None
    lng = geo.get("longitude") or None
    if lat == 0.0 and lng == 0.0:
        lat = lng = None

    # stream media out of the zip -> temp -> sha -> place if new
    ext = Path(base).suffix.lower()
    year = time.gmtime(taken).tm_year if taken else 0
    tmp = PATHS.media / f".tmp_{int(time.time()*1000)}_{_safe_component(base)}"
    try:
        with zf.open(entry) as src, open(tmp, "wb") as dst:
            import hashlib
            h = hashlib.sha256()
            while chunk := src.read(1 << 20):
                h.update(chunk); dst.write(chunk)
        sha = h.hexdigest()
    except Exception:
        if tmp.exists(): tmp.unlink()
        return "failed", 0

    row = con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()
    if row:
        photo_id = row["id"]
        size, status = tmp.stat().st_size, "duplicate"
        tmp.unlink(missing_ok=True)
    else:
        status = "added"
        sub = PATHS.media / (str(year) if year else "unknown")
        sub.mkdir(parents=True, exist_ok=True)
        dest = _unique_dest(sub / _safe_component(base))
        cloud.replace(tmp, dest)                       # a library inside OneDrive: the file may be locked for a moment
        rel = str(dest.relative_to(PATHS.media))
        is_vid = 1 if images.is_video(dest) else 0
        w, h_ = images.dimensions(dest)
        _, _, _, cam = images.exif_info(dest)
        photo_id = con.execute(
            "INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,"
            "taken_at,created_at,lat,lng,altitude,description,favorited,trashed,gphotos_url,imported_at,"
            "camera_make,camera_model,lens,focal_length,focal_length_35mm) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sha, base, rel, ext.lstrip("."), is_vid, w, h_, dest.stat().st_size,
             taken, created or int(time.time()), lat, lng, geo.get("altitude"),
             meta.get("description") or None, 1 if meta.get("favorited") else 0,
             1 if meta.get("trashed") else 0, meta.get("url"), int(time.time()),
             cam["make"], cam["model"], cam["lens"], cam["focal_length"], cam["focal_length_35mm"])).lastrowid
        images.make_thumb(dest, sha)
        size = dest.stat().st_size

    con.execute("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", (photo_id, album_id))
    for p in (meta.get("people") or []):
        nm = (p or {}).get("name")
        if not nm:
            continue
        con.execute("INSERT OR IGNORE INTO people(name,source) VALUES(?, 'takeout')", (nm,))
        pid = con.execute("SELECT id FROM people WHERE name=?", (nm,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO photo_people(photo_id,person_id,source) VALUES(?,?, 'takeout')",
                    (photo_id, pid))
    return status, size


# ---------- import from a folder / memory card (Lightroom "Copy") ----------
MEDIA_EXT = images.IMAGE_EXT | images.VIDEO_EXT


def scan_folder(folder: str, recursive: bool = True, cap: int = 20000) -> list[dict]:
    """Media files under `folder`, newest first, for the import dialog grid."""
    root = Path(folder)
    it = root.rglob("*") if recursive else root.glob("*")
    out = []
    for p in it:
        if len(out) >= cap:
            break
        if p.suffix.lower() in MEDIA_EXT and p.is_file() and not p.name.startswith("."):
            st = p.stat()
            out.append({"path": str(p), "name": p.name, "bytes": st.st_size,
                        "mtime": int(st.st_mtime), "is_video": images.is_video(p), "online": cloud.is_online_only(p)})
    out.sort(key=lambda f: -f["mtime"])
    return out


def _ingest_file(con, src: Path, taken=None, lat=None, lng=None) -> tuple[int, bool]:
    """Copy one file into the library (filed by capture year) unless an
    identical file is already there. Returns (photo_id, newly_added)."""
    import shutil
    sha = images.sha256_file(src)
    row = con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()
    if row:
        return row["id"], False
    e_taken, e_lat, e_lng, cam = images.exif_info(src)
    taken = taken or e_taken or int(src.stat().st_mtime)
    if lat is None or lng is None:
        lat, lng = e_lat, e_lng
    sub = PATHS.media / str(time.gmtime(taken).tm_year)
    sub.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(sub / _safe_component(src.name))
    shutil.copy2(src, dest)
    w, h = images.dimensions(dest)
    photo_id = con.execute(
        "INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,"
        "taken_at,created_at,lat,lng,imported_at,camera_make,camera_model,lens,focal_length,focal_length_35mm) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sha, src.name, str(dest.relative_to(PATHS.media)), src.suffix.lower().lstrip("."),
         1 if images.is_video(dest) else 0, w, h, dest.stat().st_size,
         taken, int(time.time()), lat, lng, int(time.time()),
         cam["make"], cam["model"], cam["lens"], cam["focal_length"], cam["focal_length_35mm"])).lastrowid
    images.make_thumb(dest, sha)
    return photo_id, True


# ---------- automatic import: new photos in a chosen folder (e.g. where the phone syncs) come in by themselves ----------
AUTO = {"seq": 0, "running": False, "last_run": None, "last_added": 0, "total_added": 0, "error": None}   # for the UI (/api/background)
_AUTO_LOCK = threading.Lock()


def auto_settle_seconds() -> float:
    """A file must have been left alone this long before it is imported (a phone may still be writing it)."""
    return float(os.environ.get("PHOTAG_AUTOIMPORT_SETTLE", 15))


def _auto_walk(folder: str):
    """(path, size, mtime) of every media file under the folder."""
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            if n.startswith(".") or Path(n).suffix.lower() not in MEDIA_EXT:
                continue
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            yield p, st.st_size, int(st.st_mtime)


def folder_conflict(folder: str) -> bool:
    """The auto-import folder must not be the library itself (or inside it, or contain it): that would re-import its own files."""
    try:
        f, r = Path(folder).resolve(), PATHS.root.resolve()
    except OSError:
        return False
    return f == r or r in f.parents or f in r.parents


def baseline_auto_import(con, folder: str) -> int:
    """Remember what the folder holds right now as already seen, so only photos that arrive later are imported."""
    n = 0
    rows = []
    for p, size, mtime in _auto_walk(folder):
        rows.append((p, size, mtime)); n += 1
        if len(rows) >= 500:
            con.executemany("INSERT OR REPLACE INTO auto_import_seen(path,size,mtime,failed) VALUES(?,?,?,0)", rows); rows = []
    con.executemany("INSERT OR REPLACE INTO auto_import_seen(path,size,mtime,failed) VALUES(?,?,?,0)", rows)
    con.commit()
    return n


def run_auto_import(folder: str, progress: Progress) -> dict:
    """One pass over the folder: import files that are new (not seen before at this size/date) and have settled.
    Duplicates of photos already in the catalog are recognised by their content and not stored twice."""
    con = db.connect()
    added = dupes = failed = 0
    try:
        progress.state = "scanning"
        seen = {r["path"]: (r["size"], r["mtime"]) for r in con.execute("SELECT path,size,mtime FROM auto_import_seen")}
        now, settle = time.time(), auto_settle_seconds()
        todo = [(p, size, mtime) for p, size, mtime in _auto_walk(folder)
                if seen.get(p) != (size, mtime) and size > 0 and now - mtime >= settle]
        progress.state = "importing"; progress.total = len(todo); progress.done = 0
        waiting = 0
        for p, size, mtime in todo:
            progress.done += 1
            if cloud.is_online_only(p):                    # still only in the cloud: not read (that would download it), not marked as seen
                waiting += 1
                continue
            try:
                _, new = _ingest_file(con, Path(p))
                added += new; dupes += not new
                bad = 0
            except Exception:                              # unreadable (still being copied, damaged): not retried until it changes
                failed += 1; bad = 1
            con.execute("INSERT OR REPLACE INTO auto_import_seen(path,size,mtime,failed) VALUES(?,?,?,?)", (p, size, mtime, bad))
            if progress.done % 20 == 0:
                con.commit()
        con.commit()
        progress.state = "done"
        progress.result = {"added": added, "duplicates": dupes, "failed": failed, "cloud": waiting}
        return progress.result
    finally:
        con.close()


def auto_import_tick(folder: str, progress: Progress):
    """Job body: run a pass and publish the outcome in AUTO for the UI."""
    with _AUTO_LOCK:
        AUTO["running"] = True
    try:
        r = run_auto_import(folder, progress)
        with _AUTO_LOCK:
            AUTO.update(last_run=time.time(), error=None)
            if r["added"]:
                AUTO["seq"] += 1; AUTO["last_added"] = r["added"]; AUTO["total_added"] += r["added"]
    except Exception as e:
        progress.state = "error"
        with _AUTO_LOCK:
            AUTO.update(last_run=time.time(), error=str(e)[:200])
    finally:
        with _AUTO_LOCK:
            AUTO["running"] = False


def mark_import_start(con):
    """'Previous Import' in the catalog = everything imported since this moment."""
    db.set_setting(con, "last_import", int(time.time()))


def run_folder_import(paths: list[str], keywords: list[str], album: str | None, progress: Progress):
    con = db.init_db()
    mark_import_start(con)
    progress.state = "importing"; progress.total = len(paths); progress.done = 0
    album_id = None
    if album:
        con.execute("INSERT OR IGNORE INTO albums(name,kind) VALUES(?, 'album')", (album,))
        album_id = con.execute("SELECT id FROM albums WHERE name=?", (album,)).fetchone()["id"]
    tag_ids = []
    for k in keywords:
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (k,))
        tag_ids.append(con.execute("SELECT id FROM tags WHERE name=?", (k,)).fetchone()["id"])
    added = dupes = 0
    total_bytes = 0
    for path in paths:
        try:
            total_bytes += Path(path).stat().st_size
        except OSError:
            pass
    stats = ImportStats(progress, "folder", total_bytes)
    try:
        for path in paths:
            if progress.cancel:
                break
            progress.done += 1
            if progress.done % 10 == 0:
                progress.say("{done}/{total}", done=progress.done, total=progress.total)
                con.commit()
            src = Path(path)
            if not src.is_file():
                stats.item("missing", src.name)
                continue
            try:
                size = src.stat().st_size
                photo_id, new = _ingest_file(con, src)
            except Exception:                          # one unreadable file never stops the rest
                stats.item("failed", src.name)
                continue
            added += new; dupes += not new
            stats.item("added" if new else "duplicate", src.name, size)
            if album_id:
                con.execute("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", (photo_id, album_id))
            for tid in tag_ids:
                con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'manual')",
                            (photo_id, tid))
        con.commit()
        progress.state = "done"
        progress.say_parts(*([("Import cancelled", {})] if progress.cancel else []), ("{n} items imported", {"n": added}),
                           *([("{n} already in catalog", {"n": dupes})] if dupes else []),
                           *([("{n} files could not be imported", {"n": stats.d["failed"]})] if stats.d["failed"] else []))
    except Exception as e:
        con.commit()
        progress.fail("Import failed: {error}", error=str(e))


# ---------- import a Lightroom Classic catalog (.lrcat) ----------
# A .lrcat is SQLite. We read it read-only and copy each photo's original file
# into the library with what Lightroom knew about it. Develop edits live in
# Lightroom's own settings format and are not converted (originals come in).
LR_LABELS = {"red": "red", "yellow": "yellow", "green": "green", "blue": "blue", "purple": "purple",
             "אדום": "red", "צהוב": "yellow", "ירוק": "green", "כחול": "blue", "סגול": "purple"}


def _lr_open(path: str):
    import sqlite3
    from urllib.parse import quote
    con = sqlite3.connect(f"file:{quote(str(Path(path).resolve()).replace(chr(92), '/'))}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _lr_has(lr, table, col=None):
    cols = [r[1] for r in lr.execute(f"PRAGMA table_info({table})")]
    return bool(cols) and (col is None or col in cols)


def _lr_time(s) -> int | None:
    """Lightroom captureTime: '2019-05-04T13:22:10' (+ optional fraction / zone)."""
    import calendar, datetime, re as _re
    if not s:
        return None
    m = _re.match(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?", str(s))
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    try:
        return calendar.timegm(datetime.datetime(y, mo, d, h, mi, sec).timetuple())
    except ValueError:
        return None


def _lr_images(lr):
    q = ("SELECT i.id_local id, i.captureTime, i.rating, i.pick, i.colorLabels, "
         "rf.absolutePath || fo.pathFromRoot || fi.baseName || '.' || fi.extension path "
         "FROM Adobe_images i JOIN AgLibraryFile fi ON fi.id_local=i.rootFile "
         "JOIN AgLibraryFolder fo ON fo.id_local=fi.folder JOIN AgLibraryRootFolder rf ON rf.id_local=fo.rootFolder")
    if _lr_has(lr, "Adobe_images", "masterImage"):
        q += " WHERE i.masterImage IS NULL"   # virtual copies share their master's file
    return lr.execute(q).fetchall()


def lrcat_info(path: str) -> dict:
    """Counts for the import dialog, before anything is copied."""
    lr = _lr_open(path)
    imgs = _lr_images(lr)
    missing = sum(1 for r in imgs if not Path(r["path"]).is_file())
    n_kw = lr.execute("SELECT COUNT(*) FROM AgLibraryKeyword WHERE name IS NOT NULL").fetchone()[0]
    n_coll = lr.execute("SELECT COUNT(*) FROM AgLibraryCollection WHERE creationId='com.adobe.ag.library.collection' "
                        "AND COALESCE(systemOnly,0)=0").fetchone()[0]
    return {"images": len(imgs), "missing": missing, "keywords": n_kw, "collections": n_coll,
            "bytes": sum(Path(r["path"]).stat().st_size for r in imgs if Path(r["path"]).is_file())}


def run_lrcat_import(path: str, progress: Progress):
    con = db.init_db()
    mark_import_start(con)
    progress.state = "scanning"; progress.say("Reading the Lightroom catalog…")
    try:
        lr = _lr_open(path)
        imgs = _lr_images(lr)
        gps = {}
        if _lr_has(lr, "AgHarvestedExifMetadata", "gpsLatitude"):
            gps = {r["image"]: (r["gpsLatitude"], r["gpsLongitude"]) for r in lr.execute(
                "SELECT image, gpsLatitude, gpsLongitude FROM AgHarvestedExifMetadata WHERE gpsLatitude IS NOT NULL")}
        captions = {}
        if _lr_has(lr, "AgLibraryIPTC", "caption"):
            captions = {r["image"]: r["caption"] for r in lr.execute(
                "SELECT image, caption FROM AgLibraryIPTC WHERE caption IS NOT NULL AND caption<>''")}
        person = "keywordType" if _lr_has(lr, "AgLibraryKeyword", "keywordType") else "NULL"
        kw = {}
        for r in lr.execute(f"SELECT ki.image, k.name, {person} kind FROM AgLibraryKeywordImage ki "
                            f"JOIN AgLibraryKeyword k ON k.id_local=ki.tag WHERE k.name IS NOT NULL"):
            kw.setdefault(r["image"], []).append((r["name"], r["kind"] == "person"))
        colls, quick = {}, set()
        for r in lr.execute("SELECT ci.image, c.name, COALESCE(c.systemOnly,0) sys FROM AgLibraryCollectionImage ci "
                            "JOIN AgLibraryCollection c ON c.id_local=ci.collection "
                            "WHERE c.creationId='com.adobe.ag.library.collection'"):
            if r["sys"] or (r["name"] or "").lower() == "quick collection":
                quick.add(r["image"])
            elif r["name"]:
                colls.setdefault(r["image"], []).append(r["name"])

        progress.state = "importing"; progress.total = len(imgs); progress.done = 0
        added = dupes = missing = 0
        stats = ImportStats(progress, "lightroom", sum(Path(r["path"]).stat().st_size for r in imgs if Path(r["path"]).is_file()))
        for r in imgs:
            if progress.cancel:
                break
            progress.done += 1
            if progress.done % 10 == 0:
                progress.say("Lightroom — {done}/{total}", done=progress.done, total=progress.total)
                con.commit()
            src = Path(r["path"])
            if not src.is_file():
                missing += 1
                stats.item("missing", src.name)
                continue
            lat, lng = gps.get(r["id"], (None, None))
            try:
                size = src.stat().st_size
                pid, new = _ingest_file(con, src, taken=_lr_time(r["captureTime"]), lat=lat, lng=lng)
            except Exception:
                stats.item("failed", src.name)
                continue
            added += new; dupes += not new
            stats.item("added" if new else "duplicate", src.name, size)
            pick = int(r["pick"] or 0)
            fields = {"rating": int(r["rating"] or 0), "flag": 1 if pick > 0 else -1 if pick < 0 else 0,
                      "label": LR_LABELS.get((r["colorLabels"] or "").strip().lower()),
                      "quick": 1 if r["id"] in quick else 0}
            if r["id"] in captions:
                fields["description"] = captions[r["id"]]
            if not new:  # already in the catalog: add what Lightroom knows, never clear what's here
                fields = {k: v for k, v in fields.items() if v}
            if fields:
                con.execute(f"UPDATE photos SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
            for name, is_person in kw.get(r["id"], []):
                if is_person:
                    con.execute("INSERT OR IGNORE INTO people(name,source) VALUES(?, 'lightroom')", (name,))
                    per = con.execute("SELECT id FROM people WHERE name=?", (name,)).fetchone()["id"]
                    con.execute("INSERT OR IGNORE INTO photo_people(photo_id,person_id,source) VALUES(?,?, 'lightroom')", (pid, per))
                else:
                    con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
                    tid = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
                    con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'lightroom')", (pid, tid))
            for name in colls.get(r["id"], []):
                con.execute("INSERT OR IGNORE INTO albums(name,kind) VALUES(?, 'album')", (name,))
                aid = con.execute("SELECT id FROM albums WHERE name=?", (name,)).fetchone()["id"]
                con.execute("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", (pid, aid))
        con.commit()
        progress.state = "done"
        progress.say_parts(*([("Import cancelled", {})] if progress.cancel else []), ("{n} items imported from Lightroom", {"n": added}),
                           *([("{n} already in catalog", {"n": dupes})] if dupes else []),
                           *([("{n} files missing from disk", {"n": missing})] if missing else []))
    except Exception as e:
        con.commit()
        progress.fail("Import from Lightroom failed: {error}", error=str(e))


def _unique_name(name: str, used: set) -> str:
    """Like _unique_dest, but for names inside a ZIP (no filesystem to check against)."""
    stem, suf = Path(name).stem, Path(name).suffix
    cand, n = name, 1
    while cand in used:
        n += 1
        cand = f"{stem} ({n}){suf}"
    used.add(cand)
    return cand


def _xmp_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _xmp_sidecar(rating: int, label: str | None, tags: list[str], people: list[str], description: str | None) -> str:
    """A standard XMP packet carrying the metadata a plain file copy can't: star rating, color label,
    keywords (and people, as keywords) and caption -- readable by Lightroom, Bridge, digiKam and most
    other photo software. Everything embedded in the file itself (EXIF date/GPS/camera) isn't repeated."""
    attrs = ""
    if rating:
        attrs += f' xmp:Rating="{int(rating)}"'
    if label:
        attrs += f' xmp:Label="{_xmp_escape(label.capitalize())}"'
    subjects = [_xmp_escape(x) for x in (*tags, *people) if x]
    subject_block = ("<dc:subject><rdf:Bag>" + "".join(f"<rdf:li>{s}</rdf:li>" for s in subjects) + "</rdf:Bag></dc:subject>") if subjects else ""
    desc_block = (f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{_xmp_escape(description)}</rdf:li></rdf:Alt></dc:description>') if description else ""
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            f'  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"{attrs}>\n'
            f'   {subject_block}{desc_block}\n'
            '  </rdf:Description>\n'
            ' </rdf:RDF>\n'
            '</x:xmpmeta>\n'
            '<?xpacket end="w"?>\n')


def _photo_xmp_fields(con, pid: int) -> dict:
    r = con.execute("SELECT rating, label, description FROM photos WHERE id=?", (pid,)).fetchone()
    tags = [x["name"] for x in con.execute(
        "SELECT t.name FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id WHERE pt.photo_id=?", (pid,))]
    people = [x["name"] for x in con.execute(
        "SELECT DISTINCT pe.name FROM people pe WHERE pe.id IN ("
        "SELECT person_id FROM photo_people WHERE photo_id=? UNION SELECT person_id FROM faces WHERE photo_id=?)", (pid, pid))]
    return {"rating": r["rating"] or 0, "label": r["label"], "tags": tags, "people": people, "description": r["description"]}


def run_export(ids: list[int], dest: str, originals: bool, long_edge: int | None,
               quality: int, as_zip: bool, xmp_sidecar: bool, progress: Progress):
    """Copy (or re-encode as JPEG) the chosen photos into `dest` -- a folder, or (as_zip) a single ZIP file."""
    con = db.connect()
    progress.state = "exporting"; progress.total = len(ids); progress.done = 0
    out_dir = None
    zf = used_names = tmp_dir = None
    if as_zip:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        zf = zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED)
        used_names = set()
        tmp_dir = Path(tempfile.mkdtemp(prefix="photag_zipexport_"))
    else:
        out_dir = Path(dest)
        out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for pid in ids:
            progress.done += 1
            progress.say("{done}/{total}", done=progress.done, total=progress.total)
            r = con.execute("SELECT filename, rel_path, orig_backup, is_video FROM photos WHERE id=?",
                            (pid,)).fetchone()
            if not r:
                continue
            src = PATHS.media / (r["orig_backup"] if originals and r["orig_backup"] else r["rel_path"])
            if not src.exists():
                continue
            name = _safe_component(r["filename"])
            as_is = r["is_video"] or originals or (not long_edge and quality >= 100)
            xmp_text = _xmp_sidecar(**_photo_xmp_fields(con, pid)) if xmp_sidecar else None
            if as_zip:
                arcname = _unique_name(name if as_is else Path(name).stem + ".jpg", used_names)
                if as_is:
                    zf.write(src, arcname)
                else:
                    tmp = tmp_dir / arcname
                    images.export_resized(src, tmp, long_edge, quality)
                    zf.write(tmp, arcname)
                    tmp.unlink(missing_ok=True)
                if xmp_text:
                    zf.writestr(arcname + ".xmp", xmp_text)
            else:
                if as_is:
                    import shutil
                    out = _unique_dest(out_dir / name)
                    shutil.copy2(src, out)
                else:
                    out = _unique_dest(out_dir / (Path(name).stem + ".jpg"))
                    images.export_resized(src, out, long_edge, quality)
                if xmp_text:
                    out.with_suffix(out.suffix + ".xmp").write_text(xmp_text, "utf-8")
        progress.state = "done"
        progress.say("{n} items exported to {folder}", n=progress.done, folder=str(dest))
    except Exception as e:
        progress.fail("Export failed: {error}", error=str(e))
    finally:
        if zf:
            zf.close()
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


_GALLERY_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
 :root{{color-scheme:dark}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:#161616;color:#ddd;font:14px/1.4 -apple-system,Segoe UI,Arial,sans-serif;padding:24px 16px 60px}}
 h1{{font-weight:500;font-size:18px;color:#eee;margin:0 0 16px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}}
 .cell{{position:relative;background:#222;border-radius:4px;overflow:hidden;cursor:pointer;aspect-ratio:1}}
 .cell img{{width:100%;height:100%;object-fit:cover;display:block}}
 .cell .vid{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:#2a2a2a;color:#888;font-size:12px;text-align:center;padding:8px}}
 .cell .cap{{position:absolute;left:0;right:0;bottom:0;padding:4px 6px;font-size:11px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.75));opacity:0;transition:opacity .15s}}
 .cell:hover .cap{{opacity:1}}
 #lb{{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;flex-direction:column;z-index:10;padding:20px}}
 #lb.on{{display:flex}}
 #lb img{{max-width:100%;max-height:85vh;object-fit:contain}}
 #lb .cap{{color:#ccc;margin-top:10px;font-size:13px}}
 #lb .x{{position:absolute;top:16px;right:20px;color:#ccc;font-size:28px;cursor:pointer;background:none;border:0}}
 #lb .nav{{position:fixed;top:50%;transform:translateY(-50%);background:none;border:0;color:#ccc;font-size:36px;cursor:pointer;padding:10px 16px}}
 #lb .prev{{left:6px}} #lb .next{{right:6px}}
</style></head>
<body>
<h1>{title} &middot; {count} items</h1>
<div class="grid" id="grid"></div>
<div id="lb"><button class="x" onclick="lbClose()">&times;</button><button class="nav prev" onclick="lbStep(-1)">&#8249;</button>
  <img id="lb-img"><div class="cap" id="lb-cap"></div><button class="nav next" onclick="lbStep(1)">&#8250;</button></div>
<script>
const ITEMS = {items_json};
const grid = document.getElementById('grid');
ITEMS.forEach((it, i) => {{
  const c = document.createElement('div'); c.className = 'cell';
  if (it.v) {{ c.innerHTML = '<div class="vid">' + it.n.replace(/[<>&]/g, m => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[m])) + '</div>'; }}
  else {{ c.innerHTML = '<img loading="lazy" src="data:image/jpeg;base64,' + it.b + '"><div class="cap">' + it.n.replace(/[<>&]/g, m => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[m])) + '</div>';
    c.onclick = () => lbOpen(i); }}
  grid.appendChild(c);
}});
let lbI = 0;
function lbOpen(i) {{ lbI = i; const it = ITEMS[i]; if (it.v) return;
  document.getElementById('lb-img').src = 'data:image/jpeg;base64,' + it.b;
  document.getElementById('lb-cap').textContent = it.n + (it.d ? ' · ' + it.d : '');
  document.getElementById('lb').classList.add('on'); }}
function lbClose() {{ document.getElementById('lb').classList.remove('on'); }}
function lbStep(d) {{ let i = lbI; do {{ i = (i + d + ITEMS.length) % ITEMS.length; }} while (ITEMS[i].v && i !== lbI); lbOpen(i); }}
document.addEventListener('keydown', e => {{ if (!document.getElementById('lb').classList.contains('on')) return;
  if (e.key === 'Escape') lbClose(); else if (e.key === 'ArrowLeft') lbStep(-1); else if (e.key === 'ArrowRight') lbStep(1); }});
</script>
</body></html>
"""


def run_export_html(ids: list[int], dest: str, long_edge: int | None, quality: int, title: str, progress: Progress):
    """A single self-contained HTML file: a responsive gallery with a click-to-enlarge lightbox, photos
    embedded as base64 JPEGs (resized) so the page works offline with nothing else to send along.
    Videos are listed by name only (browsers can't usefully inline arbitrary video formats here)."""
    import base64
    import io
    import json
    from PIL import Image
    con = db.connect()
    progress.state = "exporting"; progress.total = len(ids); progress.done = 0
    items = []
    try:
        for pid in ids:
            progress.done += 1
            progress.say("{done}/{total}", done=progress.done, total=progress.total)
            r = con.execute("SELECT filename, rel_path, is_video, taken_at FROM photos WHERE id=?", (pid,)).fetchone()
            if not r:
                continue
            src = PATHS.media / r["rel_path"]
            if not src.exists():
                continue
            if r["is_video"]:
                items.append({"v": True, "n": r["filename"]})
                continue
            im = images.open_image(src)
            if long_edge:
                im.thumbnail((long_edge, long_edge), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            d = time.strftime("%Y-%m-%d", time.gmtime(r["taken_at"])) if r["taken_at"] else ""
            items.append({"v": False, "n": r["filename"], "d": d, "b": base64.b64encode(buf.getvalue()).decode()})
        html = _GALLERY_TEMPLATE.format(title=_xmp_escape(title) or "Photos", count=len(items), items_json=json.dumps(items))
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, "utf-8")
        progress.state = "done"
        progress.say("{n} items exported to {folder}", n=progress.done, folder=str(dest))
    except Exception as e:
        progress.fail("Export failed: {error}", error=str(e))


if __name__ == "__main__":  # ponytail self-check for the fiddly matcher
    assert match_sidecars(["IMG_1.jpg"], ["IMG_1.jpg.supplemental-metadata.json"]) == {"IMG_1.jpg": "IMG_1.jpg.supplemental-metadata.json"}
    # truncated sidecar
    assert match_sidecars(["averylongphotoname_20200101_120000.jpg"],
                          ["averylongphotoname_20200101_120000.jpg.supplemental-metad.json"]) != {}
    # counter shuffle: Google names the dup "IMG_1234(1).jpg" but its sidecar "IMG_1234.jpg(1).json"
    assert match_sidecars(["IMG_1234(1).jpg"], ["IMG_1234.jpg(1).supplemental-metadata.json"]) == {"IMG_1234(1).jpg": "IMG_1234.jpg(1).supplemental-metadata.json"}
    print("match_sidecars OK")
