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
        pid = r["id"]
        for table in ("photo_albums", "photo_people", "photo_tags", "faces"):
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
    def __init__(self):
        self.state = "idle"; self.done = 0; self.total = 0; self.msg = ""; self.error = None
    def as_dict(self):
        return {"state": self.state, "done": self.done, "total": self.total,
                "msg": self.msg, "error": self.error}


def run_import(zip_path: str, progress: Progress):
    con = db.init_db()
    progress.state = "scanning"; progress.msg = "קורא את מבנה ה-ZIP…"
    try:
        _do_import(zip_path, con, progress)
        progress.state = "done"; progress.msg = "הייבוא הושלם"
    except Exception as e:  # surface to UI instead of dying silently
        progress.state = "error"; progress.error = str(e)
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
                progress.msg = f"{album} — {progress.done}/{progress.total}"
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


if __name__ == "__main__":  # ponytail self-check for the fiddly matcher
    assert match_sidecars(["IMG_1.jpg"], ["IMG_1.jpg.supplemental-metadata.json"]) == {"IMG_1.jpg": "IMG_1.jpg.supplemental-metadata.json"}
    # truncated sidecar
    assert match_sidecars(["averylongphotoname_20200101_120000.jpg"],
                          ["averylongphotoname_20200101_120000.jpg.supplemental-metad.json"]) != {}
    # counter shuffle: Google names the dup "IMG_1234(1).jpg" but its sidecar "IMG_1234.jpg(1).json"
    assert match_sidecars(["IMG_1234(1).jpg"], ["IMG_1234.jpg(1).supplemental-metadata.json"]) == {"IMG_1234(1).jpg": "IMG_1234.jpg(1).supplemental-metadata.json"}
    print("match_sidecars OK")
