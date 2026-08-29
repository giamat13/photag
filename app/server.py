"""FastAPI backend: catalog queries, media/thumbnail serving, metadata &
image editing, and background jobs (import / faces / tags)."""
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, images, importer, faces, tagging, config
from .config import PATHS

app = FastAPI(title="PhotoManager")
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
    threading.Thread(target=_trash_purge_loop, daemon=True).start()


def _start(name, target, *args):
    with _LOCK:
        cur = JOBS.get(name)
        if cur and cur.state in ("scanning", "importing", "detecting", "clustering", "tagging", "pulling"):
            raise HTTPException(409, "המשימה כבר רצה")
        prog = importer.Progress()
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
    vision_model, vision_model_fits = tagging.pick_vision_model_info()
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
        "ollama": tagging.ensure_running(),
        "ollama_vision_model": vision_model,
        "ollama_vision_model_fits": vision_model_fits,
        "ollama_recommended_small_model": tagging.RECOMMENDED_SMALL_VISION_MODEL,
        "trash_days": config.TRASH_RETENTION_DAYS,
    }


@app.get("/api/pick-file")
def pick_file(kind: str = "zip"):
    """Native Windows file/folder picker (tkinter -> real Explorer dialog),
    so choosing the Takeout ZIP or a library folder works like any other app."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            path = filedialog.askdirectory(title="בחר תיקיית ספרייה", parent=root)
        else:
            path = filedialog.askopenfilename(
                title="בחר קובץ ZIP של Google Takeout",
                filetypes=[("ZIP files", "*.zip"), ("כל הקבצים", "*.*")], parent=root)
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
        raise HTTPException(404, "קובץ ה-ZIP לא נמצא")
    _start("import", importer.run_import, body.zip_path)
    return {"ok": True}


@app.post("/api/faces")
def start_faces():
    _start("faces", faces.run_faces)
    return {"ok": True}


@app.post("/api/tags")
def start_tags():
    _start("tags", tagging.run_tagging)
    return {"ok": True}


class PullModelIn(BaseModel):
    model: str = tagging.RECOMMENDED_SMALL_VISION_MODEL

@app.post("/api/pull-model")
def start_pull_model(body: PullModelIn):
    _start("pull_model", tagging.pull_model, body.model)
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
        "(SELECT f.id FROM faces f WHERE f.person_id=pe.id ORDER BY f.det_score DESC LIMIT 1) cover_face "
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


@app.get("/api/photos")
def photos(album: int = 0, person: int = 0, tag: int = 0, q: str = "",
           favorite: int = 0, year: int = 0, trashed: int = 0,
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
    if tag:
        where.append("p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id=?)"); args.append(tag)
    if favorite:
        where.append("p.favorited=1")
    if year:
        where.append("CAST(strftime('%Y', p.taken_at, 'unixepoch') AS INT)=?"); args.append(year)
    if q:
        where.append("(p.filename LIKE ? OR p.description LIKE ? "
                     "OR p.id IN (SELECT photo_id FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.name LIKE ?) "
                     "OR p.id IN (SELECT pp.photo_id FROM photo_people pp JOIN people pe ON pe.id=pp.person_id WHERE pe.name LIKE ?))")
        args += [f"%{q}%"] * 4
    sql = (f"SELECT p.id,p.filename,p.is_video,p.taken_at,p.favorited,p.rating,p.width,p.height "
           f"FROM photos p{joins} WHERE {' AND '.join(where)} "
           f"ORDER BY p.taken_at DESC, p.id DESC LIMIT ? OFFSET ?")
    args += [limit, offset]
    return [dict(r) for r in con.execute(sql, args).fetchall()]


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


@app.get("/media/{pid}")
def media(pid: int):
    con = db.connect()
    r = con.execute("SELECT rel_path FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    p = PATHS.media / r["rel_path"]
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


# ---- edit metadata ---------------------------------------------------------
class MetaIn(BaseModel):
    description: str | None = None
    taken_at: int | None = None
    lat: float | None = None
    lng: float | None = None
    favorited: int | None = None
    rating: int | None = None
    trashed: int | None = None
    add_tags: list[str] | None = None
    remove_tag_ids: list[int] | None = None
    write_exif: bool = False

@app.patch("/api/photo/{pid}")
def update_meta(pid: int, m: MetaIn):
    con = db.connect()
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    fields = {k: v for k, v in m.dict().items()
              if k in ("description", "taken_at", "lat", "lng", "favorited", "rating", "trashed") and v is not None}
    if "trashed" in fields:
        fields["trashed_at"] = int(time.time()) if fields["trashed"] else None
    if fields:
        con.execute(f"UPDATE photos SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                    (*fields.values(), pid))
    for t in (m.add_tags or []):
        t = t.strip()
        if not t:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'manual')", (pid, tid))
    for tid in (m.remove_tag_ids or []):
        con.execute("DELETE FROM photo_tags WHERE photo_id=? AND tag_id=?", (pid, tid))
    con.commit()
    if m.write_exif:
        images.write_exif_jpeg(PATHS.media / r["rel_path"],
                               description=m.description, taken_at=m.taken_at, lat=m.lat, lng=m.lng)
    return photo(pid)


# ---- edit image ------------------------------------------------------------
class EditIn(BaseModel):
    rotate: float | None = None
    crop: list[float] | None = None      # [x1,y1,x2,y2] as fractions
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    grayscale: bool | None = None

@app.post("/api/photo/{pid}/edit")
def edit_image(pid: int, e: EditIn):
    con = db.connect()
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r or r["is_video"]:
        raise HTTPException(400, "לא ניתן לערוך")
    src = PATHS.media / r["rel_path"]
    orig = r["orig_backup"]
    if not orig:  # keep the untouched original once
        bdir = PATHS.media / ".originals"; bdir.mkdir(exist_ok=True)
        bpath = bdir / f"{r['id']}_{r['filename']}"
        if not bpath.exists():
            import shutil; shutil.copy2(src, bpath)
        orig = str(bpath.relative_to(PATHS.media))
    images.apply_edit(PATHS.media / orig, e.dict(exclude_none=True), src)  # edit from pristine original
    new_sha = images.sha256_file(src)
    old_sha = r["sha256"]
    images.thumb_path(old_sha).unlink(missing_ok=True)
    w, h = images.dimensions(src)
    con.execute("UPDATE photos SET sha256=?, bytes=?, width=?, height=?, edited=1, orig_backup=? WHERE id=?",
                (new_sha, src.stat().st_size, w, h, orig, pid))
    con.commit()
    images.make_thumb(src, new_sha)
    return {"ok": True}

@app.post("/api/photo/{pid}/revert")
def revert_image(pid: int):
    con = db.connect()
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r or not r["orig_backup"]:
        raise HTTPException(400, "אין גרסה מקורית")
    import shutil
    src = PATHS.media / r["rel_path"]
    shutil.copy2(PATHS.media / r["orig_backup"], src)
    images.thumb_path(r["sha256"]).unlink(missing_ok=True)
    new_sha = images.sha256_file(src); w, h = images.dimensions(src)
    con.execute("UPDATE photos SET sha256=?, width=?, height=?, edited=0 WHERE id=?", (new_sha, w, h, pid))
    con.commit(); images.make_thumb(src, new_sha)
    return {"ok": True}


# ---- people ----------------------------------------------------------------
class RenameIn(BaseModel):
    name: str

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


# ---- static frontend (mounted last so /api wins) ---------------------------
app.mount("/", StaticFiles(directory=str(UI), html=True), name="ui")
