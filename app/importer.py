"""Import a Google Takeout (Google Photos) ZIP into the library.

Nothing from the ZIP is dropped: media, albums, per-photo metadata, people
name-tags, GPS, favorites, memory titles and shared-album comments all land in
the catalog. Media is streamed out of the ZIP (the 19 GB archive is never fully
extracted) and de-duplicated by SHA-256 so a photo that appears in several
album folders is stored once but belongs to every album.
"""
import json
import re
import time
import zipfile
from pathlib import Path

from . import db, images
from .config import PATHS, TRASH_RETENTION_DAYS

ROOT_PREFIX = "Takeout/Google Photos/"
YEAR_RE = re.compile(r"(?:תמונות משנת|Photos from)\s*(\d{4})")
LATIN_PAIR_RE = re.compile(r"^[A-Za-z].*,")  # e.g. "Itamar, dafna" -> shared/people album
SUPP_RE = re.compile(r"\.supplemental[\w-]*\.json$", re.I)


def purge_expired_trash(con, days: int = TRASH_RETENTION_DAYS):
    """Permanently delete photos that have sat in the trash for over `days`:
    their media/thumb/backup files and every DB row referencing them."""
    cutoff = int(time.time()) - days * 86400
    rows = con.execute(
        "SELECT id, sha256, rel_path, orig_backup FROM photos "
        "WHERE trashed=1 AND trashed_at IS NOT NULL AND trashed_at < ?", (cutoff,)).fetchall()
    for r in rows:
        for p in (PATHS.media / r["rel_path"], images.thumb_path(r["sha256"])):
            p.unlink(missing_ok=True)
        if r["orig_backup"]:
            (PATHS.media / r["orig_backup"]).unlink(missing_ok=True)
        for b in con.execute("SELECT backup_rel FROM video_backups WHERE photo_id=?", (r["id"],)).fetchall():
            (PATHS.media / b["backup_rel"]).unlink(missing_ok=True)
        pid = r["id"]
        for table in ("photo_albums", "photo_people", "photo_tags", "faces", "video_backups"):
            con.execute(f"DELETE FROM {table} WHERE photo_id=?", (pid,))
        con.execute("DELETE FROM photos WHERE id=?", (pid,))
    if rows:
        con.commit()
    return len(rows)


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


def run_import(zip_path: str, progress: Progress):
    con = db.init_db()
    mark_import_start(con)
    progress.state = "scanning"; progress.say("קורא את מבנה ה-ZIP…")
    try:
        _do_import(zip_path, con, progress)
        progress.state = "done"; progress.say("הייבוא הושלם")
    except Exception as e:  # surface to UI instead of dying silently
        progress.fail("הייבוא נכשל: {error}", error=str(e))
        raise
    finally:
        con.commit()


def _do_import(zip_path, con, progress):
    zf = zipfile.ZipFile(zip_path)
    entries = [n for n in zf.namelist() if n.startswith(ROOT_PREFIX) and not n.endswith("/")]

    # group by album folder (immediate child of Google Photos)
    folders: dict[str, dict] = {}
    specials = {"memory": None, "comments": None}
    for n in entries:
        rest = n[len(ROOT_PREFIX):]
        parts = rest.split("/")
        if len(parts) == 1:  # top-level special json (memory titles / comments)
            b = parts[0]
            if "זיכרונות" in b:
                specials["memory"] = n
            elif "תגובות" in b:
                specials["comments"] = n
            continue
        album = parts[0]
        f = folders.setdefault(album, {"media": {}, "json": {}, "album_meta": None})
        base = parts[-1]
        if base in ("מטא נתונים.json", "metadata.json"):
            f["album_meta"] = n
        elif base.lower().endswith(".json"):
            f["json"][base] = n
        else:
            f["media"][base] = n

    # special artifacts
    if specials["memory"]:
        try:
            titles = json.loads(zf.read(specials["memory"]).decode("utf-8", "replace")).get("title", [])
            con.execute("DELETE FROM memory_titles")
            con.executemany("INSERT INTO memory_titles(title) VALUES(?)", [(t,) for t in titles])
        except Exception:
            pass
    if specials["comments"]:
        try:
            cm = json.loads(zf.read(specials["comments"]).decode("utf-8", "replace")).get("sharedAlbumComments", [])
            con.execute("DELETE FROM shared_comments")
            con.executemany("INSERT INTO shared_comments(text,liked,created_at,content_url) VALUES(?,?,?,?)",
                            [(c.get("text"), 1 if c.get("liked") else 0,
                              _ts(c.get("creationTime", {})), c.get("contentUrl")) for c in cm])
        except Exception:
            pass

    total_media = sum(len(f["media"]) for f in folders.values())
    progress.total = total_media
    progress.state = "importing"

    for album, f in folders.items():
        album_id = _upsert_album(con, zf, album, f["album_meta"])
        pairs = match_sidecars(list(f["media"]), list(f["json"]))
        for base, entry in f["media"].items():
            progress.done += 1
            if progress.done % 25 == 0:
                progress.say("{album} — {done}/{total}", album=album, done=progress.done, total=progress.total)
                con.commit()
            meta = {}
            if base in pairs:
                try:
                    meta = json.loads(zf.read(f["json"][pairs[base]]).decode("utf-8", "replace"))
                except Exception:
                    meta = {}
            _ingest_media(con, zf, entry, base, album_id, meta)
    con.commit()


def _upsert_album(con, zf, name, meta_entry) -> int:
    desc = access = None; adate = None
    if meta_entry:
        try:
            m = json.loads(zf.read(meta_entry).decode("utf-8", "replace"))
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
        return

    row = con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()
    if row:
        photo_id = row["id"]
        tmp.unlink(missing_ok=True)
    else:
        sub = PATHS.media / (str(year) if year else "unknown")
        sub.mkdir(parents=True, exist_ok=True)
        dest = _unique_dest(sub / _safe_component(base))
        tmp.replace(dest)
        rel = str(dest.relative_to(PATHS.media))
        is_vid = 1 if images.is_video(dest) else 0
        w, h_ = images.dimensions(dest)
        photo_id = con.execute(
            "INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,"
            "taken_at,created_at,lat,lng,altitude,description,favorited,trashed,gphotos_url,imported_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sha, base, rel, ext.lstrip("."), is_vid, w, h_, dest.stat().st_size,
             taken, created or int(time.time()), lat, lng, geo.get("altitude"),
             meta.get("description") or None, 1 if meta.get("favorited") else 0,
             1 if meta.get("trashed") else 0, meta.get("url"), int(time.time()))).lastrowid
        images.make_thumb(dest, sha)

    con.execute("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", (photo_id, album_id))
    for p in (meta.get("people") or []):
        nm = (p or {}).get("name")
        if not nm:
            continue
        con.execute("INSERT OR IGNORE INTO people(name,source) VALUES(?, 'takeout')", (nm,))
        pid = con.execute("SELECT id FROM people WHERE name=?", (nm,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO photo_people(photo_id,person_id,source) VALUES(?,?, 'takeout')",
                    (photo_id, pid))


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
                        "mtime": int(st.st_mtime), "is_video": images.is_video(p)})
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
    e_taken, e_lat, e_lng = images.exif_info(src)
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
        "taken_at,created_at,lat,lng,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sha, src.name, str(dest.relative_to(PATHS.media)), src.suffix.lower().lstrip("."),
         1 if images.is_video(dest) else 0, w, h, dest.stat().st_size,
         taken, int(time.time()), lat, lng, int(time.time()))).lastrowid
    images.make_thumb(dest, sha)
    return photo_id, True


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
    try:
        for path in paths:
            progress.done += 1
            if progress.done % 10 == 0:
                progress.say("{done}/{total}", done=progress.done, total=progress.total)
                con.commit()
            src = Path(path)
            if not src.is_file():
                continue
            photo_id, new = _ingest_file(con, src)
            added += new; dupes += not new
            if album_id:
                con.execute("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", (photo_id, album_id))
            for tid in tag_ids:
                con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'manual')",
                            (photo_id, tid))
        con.commit()
        progress.state = "done"
        progress.say_parts(("יובאו {n} פריטים", {"n": added}),
                           *([("{n} כבר היו בקטלוג", {"n": dupes})] if dupes else []))
    except Exception as e:
        con.commit()
        progress.fail("הייבוא נכשל: {error}", error=str(e))


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
    progress.state = "scanning"; progress.say("קורא את קטלוג Lightroom…")
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
        for r in imgs:
            progress.done += 1
            if progress.done % 10 == 0:
                progress.say("Lightroom — {done}/{total}", done=progress.done, total=progress.total)
                con.commit()
            src = Path(r["path"])
            if not src.is_file():
                missing += 1
                continue
            lat, lng = gps.get(r["id"], (None, None))
            pid, new = _ingest_file(con, src, taken=_lr_time(r["captureTime"]), lat=lat, lng=lng)
            added += new; dupes += not new
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
        progress.say_parts(("יובאו {n} פריטים מ‑Lightroom", {"n": added}),
                           *([("{n} כבר היו בקטלוג", {"n": dupes})] if dupes else []),
                           *([("{n} קבצים חסרים בדיסק", {"n": missing})] if missing else []))
    except Exception as e:
        con.commit()
        progress.fail("ייבוא מ‑Lightroom נכשל: {error}", error=str(e))


def run_export(ids: list[int], dest: str, originals: bool, long_edge: int | None,
               quality: int, progress: Progress):
    """Copy (or re-encode as JPEG) the chosen photos into `dest`."""
    con = db.connect()
    out_dir = Path(dest)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress.state = "exporting"; progress.total = len(ids); progress.done = 0
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
            if r["is_video"] or originals or (not long_edge and quality >= 100):
                import shutil
                shutil.copy2(src, _unique_dest(out_dir / name))
            else:
                images.export_resized(src, _unique_dest(out_dir / (Path(name).stem + ".jpg")), long_edge, quality)
        progress.state = "done"; progress.say("יוצאו {n} פריטים אל {folder}", n=progress.done, folder=str(out_dir))
    except Exception as e:
        progress.fail("הייצוא נכשל: {error}", error=str(e))


if __name__ == "__main__":  # ponytail self-check for the fiddly matcher
    assert match_sidecars(["IMG_1.jpg"], ["IMG_1.jpg.supplemental-metadata.json"]) == {"IMG_1.jpg": "IMG_1.jpg.supplemental-metadata.json"}
    # truncated sidecar
    assert match_sidecars(["averylongphotoname_20200101_120000.jpg"],
                          ["averylongphotoname_20200101_120000.jpg.supplemental-metad.json"]) != {}
    # counter shuffle: Google names the dup "IMG_1234(1).jpg" but its sidecar "IMG_1234.jpg(1).json"
    assert match_sidecars(["IMG_1234(1).jpg"], ["IMG_1234.jpg(1).supplemental-metadata.json"]) == {"IMG_1234(1).jpg": "IMG_1234.jpg(1).supplemental-metadata.json"}
    print("match_sidecars OK")
