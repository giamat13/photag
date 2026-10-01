"""FastAPI backend: catalog queries, media/thumbnail serving, metadata &
image editing, and background jobs (import / faces)."""
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, images, importer, faces, aitag, compress, config
from .config import PATHS

app = FastAPI(title="photag")


def err(code: int, key: str, **vars) -> HTTPException:
    """Error for the UI: a Hebrew source string + values, translated by t()."""
    return HTTPException(code, {"key": key, "vars": vars})
UI = Path(__file__).parent / "ui"

# ---- background jobs -------------------------------------------------------
JOBS: dict[str, importer.Progress] = {}
_LOCK = threading.Lock()  # ponytail: one job at a time is plenty for a desktop app


def _trash_purge_loop():
    while True:
        importer.purge_expired_trash(db.init_db())
        time.sleep(24 * 3600)


@app.on_event("startup")
def _start_trash_purge():
    db.init_db()  # run schema migrations before the first request
    threading.Thread(target=_trash_purge_loop, daemon=True).start()


def _start(name, target, *args):
    with _LOCK:
        cur = JOBS.get(name)
        if cur and cur.state in ("scanning", "importing", "detecting", "clustering", "exporting", "preparing", "tagging", "starting", "probing", "encoding", "verifying", "replacing"):
            raise err(409, "המשימה כבר רצה")
        prog = importer.Progress()
        prog.state = "starting"   # not "idle": a poll right after the start must not read the job as finished
        JOBS[name] = prog
        threading.Thread(target=target, args=(*args, prog), daemon=True).start()
        return prog


@app.get("/api/job/{name}")
def job(name: str):
    p = JOBS.get(name)
    return p.as_dict() if p else {"state": "idle"}


# ---- status / settings -----------------------------------------------------
@app.get("/api/status")
def status():
    con = db.init_db()
    c = lambda q: con.execute(q).fetchone()[0]
    return {
        "library_root": str(PATHS.root),
        "db_path": str(PATHS.db),
        "media_path": str(PATHS.media),
        "counts": {
            "photos": c("SELECT COUNT(*) FROM photos WHERE trashed=0"),
            "videos": c("SELECT COUNT(*) FROM photos WHERE is_video=1 AND trashed=0"),
            "albums": c("SELECT COUNT(*) FROM albums"),
            "people": c("SELECT COUNT(*) FROM people"),
            "faces": c("SELECT COUNT(*) FROM faces"),
            "tags": c("SELECT COUNT(*) FROM tags"),
            "trashed": c("SELECT COUNT(*) FROM photos WHERE trashed=1"),
        },
        "trash_days": config.TRASH_RETENTION_DAYS,
        "last_import": int(db.get_setting(con, "last_import", 0) or 0),
    }


@app.get("/api/pick-file")
def pick_file(kind: str = "zip", title: str = ""):
    """Native Windows file/folder picker (tkinter -> real Explorer dialog),
    so choosing the Takeout ZIP or a library folder works like any other app."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            path = filedialog.askdirectory(title=title or "Select folder", parent=root)
        elif kind == "exe":
            path = filedialog.askopenfilename(
                title=title or "Select HandBrakeCLI.exe", parent=root,
                filetypes=[("HandBrakeCLI", "HandBrakeCLI*.exe"), ("Programs", "*.exe"), ("All files", "*.*")])
        elif kind == "lrcat":
            path = filedialog.askopenfilename(
                title=title or "Select Lightroom catalog", parent=root,
                filetypes=[("Lightroom Catalog", "*.lrcat"), ("All files", "*.*")])
        else:
            path = filedialog.askopenfilename(
                title=title or "Select Google Takeout ZIP",
                filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")], parent=root)
    finally:
        root.destroy()
    return {"path": path or None}


class LibraryIn(BaseModel):
    path: str

@app.post("/api/settings/library")
def set_library(body: LibraryIn):
    config.set_library_root(body.path)
    PATHS.refresh()
    db.init_db()
    return {"library_root": str(PATHS.root)}


class ImportIn(BaseModel):
    zip_path: str

@app.post("/api/import")
def start_import(body: ImportIn):
    if not Path(body.zip_path).exists():
        raise err(404, "קובץ ה-ZIP לא נמצא")
    _start("import", importer.run_import, body.zip_path)
    return {"ok": True}


# ---- video compression (HandBrake) -------------------------------------------
class CompressIn(BaseModel):
    options: dict = {}

class HandbrakePathIn(BaseModel):
    path: str = ""


def _open_url(url: str):
    """Open a fixed, known URL in the default browser (PHOTAG_NO_OPEN=1 skips it, for tests)."""
    if not os.environ.get("PHOTAG_NO_OPEN"):
        import webbrowser
        webbrowser.open(url)


@app.get("/api/handbrake")
def handbrake_status():
    return compress.status()


@app.post("/api/handbrake/open-page")
def handbrake_open_page():
    _open_url(compress.HANDBRAKE_PAGE)
    return {"ok": True, "url": compress.HANDBRAKE_PAGE}


@app.post("/api/handbrake/path")
def handbrake_set_path(body: HandbrakePathIn):
    p = body.path.strip().strip('"')
    if p and not Path(p).is_file():
        raise err(404, "הקובץ לא נמצא")
    if p and not compress.handbrake_version(p):
        raise err(400, "הקובץ שנבחר לא נראה כמו HandBrakeCLI")
    config.set_handbrake_path(p or None)
    return compress.status()


@app.post("/api/photo/{pid}/compress")
def start_compress(pid: int, body: CompressIn):
    _start("compress", compress.run_compress, pid, body.options)
    return {"ok": True}


@app.post("/api/compress/cancel")
def cancel_compress():
    p = JOBS.get("compress")
    if p:
        p.cancel = True
    return {"ok": True}


@app.post("/api/photo/{pid}/compress/restore")
def compress_restore(pid: int):
    """Swap back to the version saved before the last compression (and the compressed one becomes the backup)."""
    try:
        compress.restore_previous(pid)
    except (LookupError, FileNotFoundError):
        raise err(404, "אין גרסה קודמת לשחזור")
    return photo(pid)


# ---- AI tagging (OpenAI / Claude / Gemini / OpenRouter / any OpenAI-compatible API) ----
class AiSettingsIn(BaseModel):
    provider: str
    model: str = ""
    language: str = "en"
    base_url: str = ""
    api_key: str | None = None   # None/empty = keep the saved key

class AiProbeIn(BaseModel):
    provider: str
    base_url: str = ""
    api_key: str | None = None   # a key typed but not saved yet

class AiTagIn(BaseModel):
    ids: list[int] | None = None
    only_untagged: bool = False


def _ai_call(fn, *a):
    try:
        return fn(*a)
    except aitag.AIError as e:
        raise err(502, "שגיאה מספק ה‑AI: {error}", error=str(e))
    except (ValueError, KeyError) as e:
        raise err(400, "הגדרה לא תקינה: {error}", error=str(e))


@app.get("/api/ai/settings")
def ai_settings():
    return aitag.get_settings()


@app.post("/api/ai/settings")
def ai_save(body: AiSettingsIn):
    _ai_call(aitag.save_settings, body.provider, body.model, body.language, body.base_url, body.api_key)
    return aitag.get_settings()


@app.delete("/api/ai/key/{provider}")
def ai_delete_key(provider: str):
    aitag.delete_key(provider)
    return aitag.get_settings()


@app.post("/api/ai/models")
def ai_models(body: AiProbeIn):
    """Live model list of the provider (and what "auto" would pick); also serves as the connection test."""
    return _ai_call(aitag.probe, body.provider, body.base_url, body.api_key)


@app.post("/api/aitag")
def start_aitag(body: AiTagIn):
    if not body.ids and not body.only_untagged:
        raise err(400, "לא נבחרו תמונות לתיוג")
    _start("aitag", aitag.run_aitag, body.ids, body.only_untagged)
    return {"ok": True}


@app.post("/api/aitag/cancel")
def cancel_aitag():
    p = JOBS.get("aitag")
    if p:
        p.cancel = True
    return {"ok": True}


@app.post("/api/faces")
def start_faces():
    _start("faces", faces.run_faces)
    return {"ok": True}


# ---- browse ----------------------------------------------------------------
@app.get("/api/albums")
def albums():
    con = db.connect()
    return [dict(r) for r in con.execute(
        "SELECT a.*, (SELECT COUNT(*) FROM photo_albums pa WHERE pa.album_id=a.id) n, "
        "(SELECT p.id FROM photo_albums pa JOIN photos p ON p.id=pa.photo_id "
        " WHERE pa.album_id=a.id AND p.trashed=0 ORDER BY p.taken_at DESC LIMIT 1) cover "
        "FROM albums a ORDER BY a.kind, a.name").fetchall()]


@app.get("/api/people")
def people():
    con = db.connect()
    return [dict(r) for r in con.execute(
        "SELECT pe.*, "
        "(SELECT COUNT(DISTINCT f.photo_id) FROM faces f WHERE f.person_id=pe.id) face_photos, "
        "(SELECT COUNT(*) FROM photo_people pp WHERE pp.person_id=pe.id) tag_photos, "
        "(SELECT f.id FROM faces f WHERE f.person_id=pe.id ORDER BY f.det_score DESC LIMIT 1) cover_face, "
        "(SELECT pp.photo_id FROM photo_people pp JOIN photos p ON p.id=pp.photo_id "
        " WHERE pp.person_id=pe.id AND p.trashed=0 AND p.is_video=0 ORDER BY p.taken_at DESC LIMIT 1) cover_photo "
        "FROM people pe ORDER BY tag_photos DESC, face_photos DESC").fetchall()]


@app.get("/api/tags")
def tags():
    con = db.connect()
    return [dict(r) for r in con.execute(
        "SELECT t.id, t.name, COUNT(*) n FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id "
        "GROUP BY t.id ORDER BY n DESC").fetchall()]


@app.get("/api/memories")
def memories():
    con = db.connect()
    return {"titles": [r["title"] for r in con.execute("SELECT title FROM memory_titles").fetchall()],
            "comments": [dict(r) for r in con.execute("SELECT * FROM shared_comments ORDER BY created_at DESC").fetchall()]}


def _folder_of(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/")
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


@app.get("/api/folders")
def folders():
    """Top-level folders under media/ (the importer files photos by year)."""
    con = db.connect()
    counts: dict[str, int] = {}
    for r in con.execute("SELECT rel_path FROM photos WHERE trashed=0"):
        f = _folder_of(r["rel_path"])
        counts[f] = counts.get(f, 0) + 1
    return {"root": str(PATHS.media),
            "folders": [{"name": k, "n": v} for k, v in sorted(counts.items(), reverse=True)]}


@app.get("/api/photos")
def photos(album: int = 0, person: int = 0, tag: int = 0, q: str = "",
           favorite: int = 0, year: int = 0, trashed: int = 0,
           folder: str | None = None, quick: int = 0, prev_import: int = 0, cluster: int = 0,
           limit: int = 200, offset: int = 0):
    con = db.connect()
    where = ["p.trashed=?"]; args = [trashed]
    joins = ""
    if album:
        joins += " JOIN photo_albums pa ON pa.photo_id=p.id AND pa.album_id=?"; args.insert(0, album)
    if person:
        # match by detected face OR Google people-tag
        where.append("(p.id IN (SELECT photo_id FROM faces WHERE person_id=?) "
                     "OR p.id IN (SELECT photo_id FROM photo_people WHERE person_id=?))")
        args += [person, person]
    if cluster:
        where.append("p.id IN (SELECT photo_id FROM faces WHERE cluster_id=?)"); args.append(cluster)
    if tag:
        where.append("p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id=?)"); args.append(tag)
    if favorite:
        where.append("p.favorited=1")
    if quick:
        where.append("p.quick=1")
    if prev_import:
        where.append("p.imported_at>=?"); args.append(int(db.get_setting(con, "last_import", 0) or 0))
    if folder == "":
        where.append("p.rel_path NOT LIKE '%\\%' AND p.rel_path NOT LIKE '%/%'")
    elif folder is not None:
        # rel_path is "<folder>\\<file>" (Windows) or "<folder>/<file>"
        where.append("(p.rel_path LIKE ? ESCAPE '!' OR p.rel_path LIKE ? ESCAPE '!')")
        f = folder.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        args += [f + "\\%", f + "/%"]
    if year:
        where.append("CAST(strftime('%Y', p.taken_at, 'unixepoch') AS INT)=?"); args.append(year)
    if q:
        where.append("(p.filename LIKE ? OR p.description LIKE ? "
                     "OR p.id IN (SELECT photo_id FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.name LIKE ?) "
                     "OR p.id IN (SELECT pp.photo_id FROM photo_people pp JOIN people pe ON pe.id=pp.person_id WHERE pe.name LIKE ?))")
        args += [f"%{q}%"] * 4
    sql = (f"SELECT p.id,p.filename,p.is_video,p.taken_at,p.favorited,p.rating,p.width,p.height,"
           f"p.flag,p.label,p.quick,p.edited,p.bytes,p.imported_at,p.rel_path,"
           f"EXISTS(SELECT 1 FROM photo_tags pt WHERE pt.photo_id=p.id) has_kw "
           f"FROM photos p{joins} WHERE {' AND '.join(where)} "
           f"ORDER BY p.taken_at DESC, p.id DESC LIMIT ? OFFSET ?")
    args += [limit, offset]
    out = []
    for r in con.execute(sql, args).fetchall():
        d = dict(r); d["folder"] = _folder_of(d.pop("rel_path") or "")
        out.append(d)
    return out


@app.get("/api/photo/{pid}")
def photo(pid: int):
    con = db.connect()
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    d = dict(r); d.pop("orig_backup", None)
    d["albums"] = [dict(x) for x in con.execute(
        "SELECT a.id,a.name FROM albums a JOIN photo_albums pa ON pa.album_id=a.id WHERE pa.photo_id=?", (pid,))]
    d["people"] = [dict(x) for x in con.execute(
        "SELECT DISTINCT pe.id, pe.name FROM people pe "
        "WHERE pe.id IN (SELECT person_id FROM photo_people WHERE photo_id=?) "
        "OR pe.id IN (SELECT person_id FROM faces WHERE photo_id=? AND person_id IS NOT NULL)", (pid, pid))]
    d["tags"] = [dict(x) for x in con.execute(
        "SELECT t.id,t.name FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id WHERE pt.photo_id=?", (pid,))]
    d["video_backups"] = con.execute("SELECT COUNT(*) FROM video_backups WHERE photo_id=?", (pid,)).fetchone()[0]
    d["faces"] = [dict(x) for x in con.execute(
        "SELECT id,x1,y1,x2,y2,person_id FROM faces WHERE photo_id=?", (pid,))]
    return d


# ---- media -----------------------------------------------------------------
@app.get("/thumb/{pid}")
def thumb(pid: int):
    con = db.connect()
    r = con.execute("SELECT sha256, rel_path FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    tp = images.thumb_path(r["sha256"])
    if not tp.exists():
        images.make_thumb(PATHS.media / r["rel_path"], r["sha256"])
    if tp.exists():
        return FileResponse(tp, media_type="image/jpeg")
    return Response(status_code=204)  # no thumb (e.g. video w/o ffmpeg)


@app.get("/face/{fid}")
def face_thumb(fid: int):
    """Square crop around a detected face (cached) — used as a person's avatar."""
    out = PATHS.thumbs / f"face_{fid}.jpg"
    if not out.exists():
        con = db.connect()
        r = con.execute("SELECT f.x1,f.y1,f.x2,f.y2,p.rel_path FROM faces f "
                        "JOIN photos p ON p.id=f.photo_id WHERE f.id=?", (fid,)).fetchone()
        if not r:
            raise HTTPException(404)
        try:
            im = images.open_image(PATHS.media / r["rel_path"])
            cx, cy = (r["x1"] + r["x2"]) / 2, (r["y1"] + r["y2"]) / 2
            half = max(r["x2"] - r["x1"], r["y2"] - r["y1"]) * 0.8
            box = (max(0, int(cx - half)), max(0, int(cy - half)),
                   min(im.width, int(cx + half)), min(im.height, int(cy + half)))
            crop = im.crop(box)
            crop.thumbnail((256, 256))
            crop.save(out, "JPEG", quality=88)
        except Exception:
            return Response(status_code=204)
    return FileResponse(out, media_type="image/jpeg")


@app.get("/media/{pid}")
def media(pid: int):
    con = db.connect()
    r = con.execute("SELECT rel_path FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    p = PATHS.media / r["rel_path"]
    if not p.exists():
        raise HTTPException(404)
    if images.is_raw(p):
        return _raw_jpeg(p)
    return FileResponse(p)


def _raw_jpeg(p: Path):
    out = PATHS.thumbs / f"raw_{images.sha256_file(p)[:24]}.jpg"
    if not out.exists():
        try:
            images.open_image(p).save(out, "JPEG", quality=92)   # oriented, cached
        except Exception:
            raise err(415, "לא נמצאה תצוגה מקדימה בקובץ ה‑RAW")
    return FileResponse(out, media_type="image/jpeg")


# ---- edit metadata ---------------------------------------------------------
class MetaIn(BaseModel):
    description: str | None = None
    taken_at: int | None = None
    lat: float | None = None
    lng: float | None = None
    favorited: int | None = None
    rating: int | None = None
    flag: int | None = None          # 1 pick, -1 reject, 0 unflagged
    label: str | None = None         # "" clears the color label
    quick: int | None = None
    trashed: int | None = None
    add_tags: list[str] | None = None
    remove_tag_ids: list[int] | None = None
    write_exif: bool = False

SIMPLE_FIELDS = ("description", "taken_at", "lat", "lng", "favorited", "rating", "flag", "quick", "trashed")


def _apply_meta(con, ids: list[int], m: MetaIn):
    fields = {k: v for k, v in m.dict().items() if k in SIMPLE_FIELDS and v is not None}
    if m.label is not None:
        fields["label"] = m.label or None
    if "trashed" in fields:
        fields["trashed_at"] = int(time.time()) if fields["trashed"] else None
    q = ",".join("?" * len(ids))
    if fields:
        con.execute(f"UPDATE photos SET {', '.join(f'{k}=?' for k in fields)} WHERE id IN ({q})",
                    (*fields.values(), *ids))
    for t in (m.add_tags or []):
        t = t.strip()
        if not t:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()["id"]
        con.executemany("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'manual')",
                        [(pid, tid) for pid in ids])
    for tid in (m.remove_tag_ids or []):
        con.execute(f"DELETE FROM photo_tags WHERE tag_id=? AND photo_id IN ({q})", (tid, *ids))
    con.commit()


@app.patch("/api/photo/{pid}")
def update_meta(pid: int, m: MetaIn):
    con = db.connect()
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    _apply_meta(con, [pid], m)
    if m.write_exif:
        images.write_exif_jpeg(PATHS.media / r["rel_path"],
                               description=m.description, taken_at=m.taken_at, lat=m.lat, lng=m.lng)
    return photo(pid)


class BatchIn(MetaIn):
    ids: list[int]

@app.patch("/api/photos")
def update_many(b: BatchIn):
    """Same as PATCH /api/photo/{id}, applied to every selected photo at once."""
    if b.ids:
        _apply_meta(db.connect(), b.ids, b)
    return {"ok": True, "n": len(b.ids)}


class KeywordsIn(BaseModel):
    ids: list[int]

@app.post("/api/keywords")
def keywords(body: KeywordsIn):
    """Keywords on a selection, with how many of the selected photos carry each."""
    id_list = body.ids
    if not id_list:
        return []
    q = ",".join("?" * len(id_list))
    return [dict(r) for r in db.connect().execute(
        f"SELECT t.id, t.name, COUNT(*) n FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id "
        f"WHERE pt.photo_id IN ({q}) GROUP BY t.id ORDER BY t.name", id_list)]


# ---- edit image ------------------------------------------------------------
# Develop settings are kept as JSON on the photo and always rendered from the
# pristine original, so re-opening a photo in Develop picks up where it left off.
class EditIn(BaseModel):
    rotate: float | None = None
    crop: list[float] | None = None      # [x1,y1,x2,y2] as fractions of the rotated image
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    grayscale: bool | None = None


def _is_neutral(ops: dict) -> bool:
    return (not ops.get("rotate") and not ops.get("grayscale")
            and ops.get("crop") in (None, [0, 0, 1, 1])
            and all(ops.get(k) in (None, 1, 1.0) for k in ("brightness", "contrast", "saturation")))


def _refresh_file(con, r, src: Path, **extra):
    images.thumb_path(r["sha256"]).unlink(missing_ok=True)
    new_sha = images.sha256_file(src)
    w, h = images.dimensions(src)
    cols = {"sha256": new_sha, "bytes": src.stat().st_size, "width": w, "height": h, **extra}
    con.execute(f"UPDATE photos SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), r["id"]))
    con.commit()
    images.make_thumb(src, new_sha)


def _render(con, r, ops: dict):
    import json, shutil
    src = PATHS.media / r["rel_path"]
    orig = r["orig_backup"]
    if not orig:  # keep the untouched original once
        bdir = PATHS.media / ".originals"; bdir.mkdir(exist_ok=True)
        bpath = bdir / f"{r['id']}_{r['filename']}"
        if not bpath.exists():
            shutil.copy2(src, bpath)
        orig = str(bpath.relative_to(PATHS.media))
    if _is_neutral(ops):  # everything back at zero -> just restore the original
        shutil.copy2(PATHS.media / orig, src)
        _refresh_file(con, r, src, edited=0, orig_backup=orig, edit_ops=None)
        return
    images.apply_edit(PATHS.media / orig, ops, src)
    _refresh_file(con, r, src, edited=1, orig_backup=orig, edit_ops=json.dumps(ops))


def _editable(con, pid):
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r or r["is_video"]:
        raise err(400, "לא ניתן לערוך")
    if images.is_raw(Path(r["rel_path"])):
        raise err(400, "עריכת קובצי RAW לא נתמכת — ייצאו JPEG וערכו אותו")
    return r


@app.post("/api/photo/{pid}/edit")
def edit_image(pid: int, e: EditIn):
    con = db.connect()
    _render(con, _editable(con, pid), e.dict(exclude_none=True))
    return {"ok": True}


class RotateIn(BaseModel):
    degrees: int  # +90 clockwise, -90 counter-clockwise

@app.post("/api/photo/{pid}/rotate")
def rotate_image(pid: int, body: RotateIn):
    """Grid/Loupe rotate buttons: folded into the develop settings so a later
    Develop edit keeps the rotation (and the crop turns with the photo)."""
    import json
    con = db.connect()
    r = _editable(con, pid)
    ops = json.loads(r["edit_ops"]) if r["edit_ops"] else {}
    d = 90 if body.degrees > 0 else -90
    ops["rotate"] = (float(ops.get("rotate") or 0) + d + 180) % 360 - 180
    c = ops.get("crop")
    if c and len(c) == 4:
        x1, y1, x2, y2 = c
        ops["crop"] = [1 - y2, x1, 1 - y1, x2] if d > 0 else [y1, 1 - x2, y2, 1 - x1]
    _render(con, r, ops)
    return photo(pid)


@app.post("/api/photo/{pid}/revert")
def revert_image(pid: int):
    import shutil
    con = db.connect()
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r or not r["orig_backup"]:
        raise err(400, "אין גרסה מקורית")
    src = PATHS.media / r["rel_path"]
    shutil.copy2(PATHS.media / r["orig_backup"], src)
    _refresh_file(con, r, src, edited=0, edit_ops=None)
    return {"ok": True}


@app.post("/api/cast")
def cast():
    """Open Windows' Cast/Connect flyout (Win+K) so the slideshow can be sent to a
    TV or wireless display. Windows owns the discovery and the connection."""
    import sys
    if sys.platform != "win32":
        raise err(501, "שידור למסך זמין רק ב-Windows")
    import ctypes
    key = ctypes.windll.user32.keybd_event
    VK_LWIN, VK_K, KEYUP = 0x5B, 0x4B, 0x2
    key(VK_LWIN, 0, 0, 0); key(VK_K, 0, 0, 0); key(VK_K, 0, KEYUP, 0); key(VK_LWIN, 0, KEYUP, 0)
    return {"ok": True}


@app.post("/api/photo/{pid}/open-external")
def open_external(pid: int):
    """Open a video in VLC when it's installed (it plays nearly everything the
    built-in player can't), otherwise in the system's default player."""
    import os, shutil, subprocess, sys
    r = db.connect().execute("SELECT rel_path FROM photos WHERE id=?", (pid,)).fetchone()
    if not r or not (PATHS.media / r["rel_path"]).exists():
        raise HTTPException(404)
    path = str(PATHS.media / r["rel_path"])
    vlc = shutil.which("vlc") or next((c for c in (
        os.path.join(os.environ.get("ProgramFiles", ""), "VideoLAN", "VLC", "vlc.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "VideoLAN", "VLC", "vlc.exe")) if os.path.isfile(c)), None)
    if vlc:
        subprocess.Popen([vlc, path])
        return {"player": "vlc"}
    if sys.platform == "win32":
        os.startfile(path)
        return {"player": "default"}
    raise err(501, "לא נמצא נגן חיצוני")


@app.post("/api/photo/{pid}/reveal")
def reveal(pid: int):
    """Lightroom's "Show in Explorer": open the folder with the file selected."""
    import subprocess
    r = db.connect().execute("SELECT rel_path FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    subprocess.Popen(["explorer", "/select,", str(PATHS.media / r["rel_path"])])
    return {"ok": True}


@app.get("/original/{pid}")
def original(pid: int):
    """The untouched original (what Develop previews its settings on top of)."""
    r = db.connect().execute("SELECT rel_path, orig_backup FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    p = PATHS.media / (r["orig_backup"] or r["rel_path"])
    if not p.exists():
        raise HTTPException(404)
    if images.is_raw(p):
        return _raw_jpeg(p)
    return FileResponse(p)


# ---- collections (albums) --------------------------------------------------
class CollectionIn(BaseModel):
    name: str
    ids: list[int] = []

class IdsIn(BaseModel):
    ids: list[int]

class RenameIn(BaseModel):
    name: str

@app.post("/api/albums")
def create_album(body: CollectionIn):
    con = db.connect()
    name = body.name.strip()
    if not name:
        raise err(400, "שם ריק")
    if con.execute("SELECT 1 FROM albums WHERE name=?", (name,)).fetchone():
        raise err(409, "כבר קיים אוסף בשם הזה")
    aid = con.execute("INSERT INTO albums(name,kind) VALUES(?, 'album')", (name,)).lastrowid
    con.executemany("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", [(i, aid) for i in body.ids])
    con.commit()
    return {"id": aid}

@app.post("/api/album/{aid}/add")
def album_add(aid: int, body: IdsIn):
    con = db.connect()
    con.executemany("INSERT OR IGNORE INTO photo_albums(photo_id,album_id) VALUES(?,?)", [(i, aid) for i in body.ids])
    con.commit()
    return {"ok": True}

@app.post("/api/album/{aid}/remove")
def album_remove(aid: int, body: IdsIn):
    con = db.connect()
    con.executemany("DELETE FROM photo_albums WHERE photo_id=? AND album_id=?", [(i, aid) for i in body.ids])
    con.commit()
    return {"ok": True}

@app.post("/api/album/{aid}/rename")
def album_rename(aid: int, body: RenameIn):
    con = db.connect()
    name = body.name.strip()
    if con.execute("SELECT 1 FROM albums WHERE name=? AND id<>?", (name, aid)).fetchone():
        raise err(409, "כבר קיים אוסף בשם הזה")
    con.execute("UPDATE albums SET name=? WHERE id=?", (name, aid))
    con.commit()
    return {"ok": True}

@app.delete("/api/album/{aid}")
def album_delete(aid: int):
    """Deletes the collection only; its photos stay in the catalog."""
    con = db.connect()
    con.execute("DELETE FROM photo_albums WHERE album_id=?", (aid,))
    con.execute("DELETE FROM albums WHERE id=?", (aid,))
    con.commit()
    return {"ok": True}


# ---- import from folder / export -------------------------------------------
@app.get("/api/scan-folder")
def scan_folder(path: str, recursive: int = 1):
    if not Path(path).is_dir():
        raise err(404, "התיקייה לא נמצאה")
    files = importer.scan_folder(path, bool(recursive))
    known = {(r["filename"], r["bytes"]) for r in db.connect().execute("SELECT filename, bytes FROM photos")}
    for f in files:  # Lightroom's "suspected duplicate": same name + size already in the catalog
        f["dup"] = (f["name"], f["bytes"]) in known
    return {"files": files}


@app.get("/api/local-thumb")
def local_thumb(path: str):
    p = Path(path)
    if p.suffix.lower() not in images.IMAGE_EXT or not p.is_file():
        return Response(status_code=204)
    data = images.small_preview(p)
    return Response(data, media_type="image/jpeg") if data else Response(status_code=204)


class LrcatIn(BaseModel):
    path: str

@app.get("/api/lrcat-info")
def lrcat_info(path: str):
    if not Path(path).is_file():
        raise err(404, "הקטלוג לא נמצא")
    try:
        return importer.lrcat_info(path)
    except Exception as e:
        raise err(400, "לא ניתן לקרוא את הקטלוג: {error}", error=str(e))

@app.post("/api/import-lrcat")
def start_import_lrcat(body: LrcatIn):
    if not Path(body.path).is_file():
        raise err(404, "הקטלוג לא נמצא")
    _start("import", importer.run_lrcat_import, body.path)
    return {"ok": True}


class ImportFolderIn(BaseModel):
    paths: list[str]
    keywords: list[str] = []
    album: str | None = None

@app.post("/api/import-folder")
def start_import_folder(body: ImportFolderIn):
    if not body.paths:
        raise err(400, "לא נבחרו קבצים")
    kws = [k.strip() for k in body.keywords if k.strip()]
    _start("import", importer.run_folder_import, body.paths, kws, (body.album or "").strip() or None)
    return {"ok": True}


class ExportIn(BaseModel):
    ids: list[int]
    dest: str
    originals: bool = False
    long_edge: int | None = None
    quality: int = 100

@app.post("/api/export")
def start_export(body: ExportIn):
    if not body.ids or not body.dest.strip():
        raise err(400, "חסרים פריטים או תיקיית יעד")
    _start("export", importer.run_export, body.ids, body.dest.strip(), body.originals,
           body.long_edge, max(10, min(100, body.quality)))
    return {"ok": True}


# ---- people ----------------------------------------------------------------
@app.post("/api/person/{pid}/rename")
def rename_person(pid: int, body: RenameIn):
    con = db.connect()
    dup = con.execute("SELECT id FROM people WHERE name=? AND id<>?", (body.name, pid)).fetchone()
    if dup:  # merge into existing person of that name
        con.execute("UPDATE faces SET person_id=? WHERE person_id=?", (dup["id"], pid))
        con.execute("UPDATE OR IGNORE photo_people SET person_id=? WHERE person_id=?", (dup["id"], pid))
        con.execute("DELETE FROM people WHERE id=?", (pid,))
        con.commit()
        return {"id": dup["id"], "merged": True}
    con.execute("UPDATE people SET name=? WHERE id=?", (body.name, pid))
    con.commit()
    return {"id": pid}


@app.get("/api/clusters")
def clusters():
    """Face groups nobody has named yet (Lightroom's "Unnamed People")."""
    return [dict(r) for r in db.connect().execute(
        "SELECT f.cluster_id id, COUNT(DISTINCT f.photo_id) n, "
        "(SELECT f2.id FROM faces f2 WHERE f2.cluster_id=f.cluster_id ORDER BY f2.det_score DESC LIMIT 1) cover_face "
        "FROM faces f WHERE f.cluster_id IS NOT NULL AND f.person_id IS NULL "
        "GROUP BY f.cluster_id HAVING n>=2 ORDER BY n DESC LIMIT 300").fetchall()]


@app.post("/api/cluster/{cid}/name")
def name_cluster(cid: int, body: RenameIn):
    con = db.connect()
    name = body.name.strip()
    if not name:
        raise err(400, "שם ריק")
    con.execute("INSERT OR IGNORE INTO people(name,source) VALUES(?, 'manual')", (name,))
    pid = con.execute("SELECT id FROM people WHERE name=?", (name,)).fetchone()["id"]
    con.execute("UPDATE faces SET person_id=? WHERE cluster_id=?", (pid, cid))
    con.execute("UPDATE people SET cover_face_id=COALESCE(cover_face_id, (SELECT id FROM faces WHERE cluster_id=? "
                "ORDER BY det_score DESC LIMIT 1)) WHERE id=?", (cid, pid))
    con.commit()
    return {"id": pid}


# ---- static frontend (mounted last so /api wins) ---------------------------
app.mount("/", StaticFiles(directory=str(UI), html=True), name="ui")
