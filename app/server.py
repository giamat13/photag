"""FastAPI backend: catalog queries, media/thumbnail serving, metadata &
image editing, and background jobs (import / faces)."""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, images, importer, faces, aitag, compress, config, updater, backup, backup_task, refmode, analysis, semantic, smart, cloud, triplan
from .version import __version__
from .config import PATHS
from .security import LocalOnlyMiddleware

app = FastAPI(title="photag")
app.add_middleware(LocalOnlyMiddleware)       # only photag's own window may use this server (see app/security.py)


def err(code: int, key: str, **vars) -> HTTPException:
    """Error for the UI: a Hebrew source string + values, translated by t()."""
    return HTTPException(code, {"key": key, "vars": vars})
UI = Path(__file__).parent / "ui"

# ---- background jobs -------------------------------------------------------
JOBS: dict[str, importer.Progress] = {}
_LOCK = threading.Lock()  # ponytail: one job at a time is plenty for a desktop app


BUSY_STATES = ("scanning", "importing", "detecting", "clustering", "exporting", "preparing", "tagging", "starting", "probing",
               "encoding", "verifying", "replacing", "downloading", "backing_up", "restoring", "analyzing")


def _other_job_running(except_name: str = "") -> bool:
    return any(p.state in BUSY_STATES for n, p in JOBS.items() if n != except_name)


def _backup_loop():
    """Automatic backups: every few minutes, check whether one is due (never while another job is running)."""
    try:
        backup_task.ensure(backup.get_settings()["enabled"])     # the Windows task that backs up even when the app is closed
    except Exception:
        pass
    time.sleep(float(os.environ.get("PHOTAG_BACKUP_START_DELAY", 90)))      # let the app finish starting first
    while True:
        try:
            # catches up on a missed day right after start-up; after a failure it retries every 30 minutes
            if backup.auto_due() is None and not _other_job_running():
                _start("backup", backup.run_backup, "auto")
            elif not _other_job_running():
                backup.verify_if_due("app")           # once a week: is the newest backup still intact?
        except Exception:
            pass
        time.sleep(float(os.environ.get("PHOTAG_BACKUP_TICK", 300)))


def _trash_purge_loop():
    while True:
        try:
            importer.purge_expired_trash(db.init_db())
        except Exception:
            pass
        time.sleep(float(os.environ.get("PHOTAG_TRASH_TICK", 3600)))


def _auto_import_loop():
    """Automatic import: every minute, bring in photos that appeared in the chosen folder (never while another task runs)."""
    time.sleep(float(os.environ.get("PHOTAG_AUTOIMPORT_DELAY", 20)))
    while True:
        try:
            s = config.get_auto_import()
            if s.get("enabled") and s.get("folder") and Path(s["folder"]).is_dir() and not _other_job_running():
                _start("autoimport", importer.auto_import_tick, s["folder"])
        except Exception:
            pass
        time.sleep(float(os.environ.get("PHOTAG_AUTOIMPORT_TICK", 60)))


def _ref_loop():
    """Photos that stay in the user's own folder: look for new / moved / removed files every few minutes (never while another task runs)."""
    time.sleep(float(os.environ.get("PHOTAG_REF_DELAY", 25)))
    while True:
        try:
            st = refmode.get(db.connect())
            if st["enabled"] and st["folder"] and Path(st["folder"]).is_dir() and not _other_job_running():
                _start("refscan", refmode.run_scan)
        except Exception:
            pass
        time.sleep(float(os.environ.get("PHOTAG_REF_TICK", 300)))


@app.on_event("startup")
def _start_trash_purge():
    db.init_db()  # run schema migrations before the first request
    threading.Thread(target=_backup_loop, daemon=True).start()
    threading.Thread(target=_ref_loop, daemon=True).start()
    threading.Thread(target=_auto_import_loop, daemon=True).start()
    try:
        updater.reconcile()   # settle an update that was started before this start (finished, or interrupted)
    except Exception:
        pass
    threading.Thread(target=_trash_purge_loop, daemon=True).start()


def _start(name, target, *args):
    with _LOCK:
        cur = JOBS.get(name)
        if cur and cur.state in BUSY_STATES:
            raise err(409, "The task is already running")
        prog = importer.Progress()
        prog.state = "starting"   # not "idle": a poll right after the start must not read the job as finished
        JOBS[name] = prog
        threading.Thread(target=target, args=(*args, prog), daemon=True).start()
        return prog


@app.get("/api/job/{name}")
def job(name: str):
    p = JOBS.get(name)
    return p.as_dict() if p else {"state": "idle"}


@app.get("/api/background")
def background():
    """What the window polls once a minute: did the automatic import bring in photos, did the weekly backup check find a problem."""
    con = db.connect()
    try:
        last = json.loads(db.get_setting(con, "ref_last_result", "") or "{}")
    except ValueError:
        last = {}
    ref = {"at": db.get_setting(con, "ref_last_scan", None),
           "added": last.get("added", 0), "moved": last.get("moved", 0), "removed": last.get("removed", 0), "changed": last.get("changed", 0)}
    return {"auto_import": dict(importer.AUTO), "verify": backup.get_state().get("last_verify"), "ref": ref}


# ---- automatic import from a folder ------------------------------------------------
class AutoImportIn(BaseModel):
    enabled: bool | None = None
    folder: str | None = None
    existing: bool = False        # also import what the folder already holds (default: only what arrives from now on)


@app.get("/api/auto-import")
def auto_import_info():
    s = config.get_auto_import()
    return {"enabled": bool(s.get("enabled")), "folder": s.get("folder") or "",
            "folder_ok": bool(s.get("folder")) and Path(s["folder"]).is_dir(), "state": dict(importer.AUTO)}


@app.post("/api/auto-import")
def auto_import_set(body: AutoImportIn):
    s = dict(config.get_auto_import())
    con = db.connect()
    if body.folder is not None:
        f = body.folder.strip().strip('"')
        if f and not Path(f).is_dir():
            raise err(404, "Folder not found")
        if f and importer.folder_conflict(f):
            raise err(400, "Choose a folder outside the photag library")
        s["folder"] = f or None
    if body.enabled is not None:
        s["enabled"] = body.enabled
    if s.get("enabled") and not s.get("folder"):
        raise err(400, "Folder not found")
    if s.get("enabled") and s.get("folder") and db.get_setting(con, "auto_import_baseline") != s["folder"]:
        if not body.existing:
            importer.baseline_auto_import(con, s["folder"])       # what is there now stays where it is; only new photos come in
        db.set_setting(con, "auto_import_baseline", s["folder"])
    config.set_auto_import(s)
    return auto_import_info()


# ---- photos that stay in the user's own folder ---------------------------------------
class RefIn(BaseModel):
    enabled: bool | None = None
    folder: str | None = None


def _ref_info():
    con = db.connect()
    st = refmode.get(con)
    last = db.get_setting(con, "ref_last_result", "")
    import json as _json
    try:
        last = _json.loads(last) if last else None
    except ValueError:
        last = None
    return {**st, "folder_ok": bool(st["folder"]) and Path(st["folder"]).is_dir(), "last_scan": db.get_setting(con, "ref_last_scan", None),
            "last_result": last, "photos": con.execute("SELECT COUNT(*) FROM ref_files").fetchone()[0]}


@app.get("/api/ref")
def ref_info():
    return _ref_info()


@app.post("/api/ref")
def ref_set(body: RefIn):
    """Turn "photos stay in my folder" on or off and choose the folder. Off by default. Turning it off keeps the photos in the
    catalog (they stay where they are); nothing is moved or deleted either way."""
    con = db.connect()
    cur = refmode.get(con)
    folder = (body.folder.strip().strip('"') if body.folder is not None else cur["folder"])
    enabled = cur["enabled"] if body.enabled is None else body.enabled
    if enabled or body.folder is not None:
        if folder:
            problem = refmode.folder_problem(folder)
            if problem:
                raise err(400, problem)
        elif enabled:
            raise err(400, "Folder not found")
    refmode.set_state(con, enabled=enabled, folder=folder)
    if enabled and folder:
        try:
            _start("refscan", refmode.run_scan)               # the first look at the folder starts right away
        except HTTPException:
            pass
    return _ref_info()


@app.post("/api/ref/scan")
def ref_scan():
    _start("refscan", refmode.run_scan)
    return {"ok": True}


# ---- the trash ---------------------------------------------------------------------
class TrashDaysIn(BaseModel):
    days: int


@app.get("/api/trash/preview")
def trash_preview(days: int):
    """How many photos in the trash would be deleted for good if the limit were `days` days."""
    return {"n": len(importer.expired_trash(db.connect(), max(importer.TRASH_DAYS_MIN, days)))}


@app.post("/api/settings/trash")
def set_trash_days(body: TrashDaysIn):
    con = db.connect()
    days = importer.set_trash_days(con, body.days)
    n = importer.purge_expired_trash(con, days)
    return {"days": days, "deleted": n}


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
        "trash_days": importer.trash_days(con),
        "version": __version__,
        "legacy_library": config.legacy_library_in_use(),
        "target_library": str(config.TARGET_LIBRARY),
        "move_notice": config.move_notice(),
        "last_import": int(db.get_setting(con, "last_import", 0) or 0),
        "library_in_onedrive": cloud.in_onedrive(PATHS.root),
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
        elif kind == "savezip":
            path = filedialog.asksaveasfilename(
                title=title or "Export as ZIP", parent=root, defaultextension=".zip",
                filetypes=[("ZIP file", "*.zip")])
        elif kind == "savehtml":
            path = filedialog.asksaveasfilename(
                title=title or "Export as HTML Gallery", parent=root, defaultextension=".html",
                filetypes=[("HTML file", "*.html")])
        elif kind == "exe":
            path = filedialog.askopenfilename(
                title=title or "Select HandBrakeCLI.exe", parent=root,
                filetypes=[("HandBrakeCLI", "HandBrakeCLI*.exe"), ("Programs", "*.exe"), ("All files", "*.*")])
        elif kind == "lrcat":
            path = filedialog.askopenfilename(
                title=title or "Select Lightroom catalog", parent=root,
                filetypes=[("Lightroom Catalog", "*.lrcat"), ("All files", "*.*")])
        elif kind == "digikam":
            path = filedialog.askopenfilename(
                title=title or "Select digiKam database", parent=root,
                filetypes=[("digiKam Database", "digikam4.db"), ("SQLite Database", "*.db"), ("All files", "*.*")])
        else:
            paths = filedialog.askopenfilenames(           # a Takeout export can be several ZIP files
                title=title or "Select Google Takeout ZIP files",
                filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")], parent=root)
            paths = [str(Path(p)) for p in paths]          # the file dialog returns C:/x/y.zip; the rest of the app uses C:\x\y.zip
            return {"path": paths[0] if paths else None, "paths": paths,
                    "files": [{"path": p, "name": Path(p).name, "bytes": Path(p).stat().st_size} for p in paths]}
    finally:
        root.destroy()
    return {"path": path or None}


@app.post("/api/library/move-legacy")
def move_legacy_library():
    """Move ~/PhotoManager to ~/Photag the next time photag starts."""
    if not config.legacy_library_in_use():
        raise err(400, "There is no PhotoManager library to move")
    config.request_legacy_move()
    return {"ok": True}


@app.post("/api/library/move-ack")
def move_ack():
    config.ack_move_notice()
    return {"ok": True}


class LibraryIn(BaseModel):
    path: str

@app.post("/api/settings/library")
def set_library(body: LibraryIn):
    config.set_library_root(body.path)
    PATHS.refresh()
    db.init_db()
    return {"library_root": str(PATHS.root), "library_in_onedrive": cloud.in_onedrive(PATHS.root)}


class ImportIn(BaseModel):
    zip_path: str | None = None
    zip_paths: list[str] | None = None        # all the parts of a Takeout export


@app.get("/api/takeout/parts")
def takeout_parts(path: str):
    """The other parts of a multi-file Takeout export that sit next to this ZIP, and the part numbers that are missing."""
    if not Path(path).is_file():
        raise err(404, "The ZIP file was not found")
    return importer.takeout_parts(path)


@app.post("/api/import")
def start_import(body: ImportIn):
    paths = body.zip_paths or ([body.zip_path] if body.zip_path else [])
    if not paths or not all(Path(p).is_file() for p in paths):
        raise err(404, "The ZIP file was not found")
    _start("import", importer.run_import, paths)
    return {"ok": True}


class SocialImportIn(BaseModel):
    zip_paths: list[str]

@app.post("/api/import-social")
def start_import_social(body: SocialImportIn):
    if not body.zip_paths or not all(Path(p).is_file() for p in body.zip_paths):
        raise err(404, "The ZIP file was not found")
    _start("import", importer.run_social_import, body.zip_paths)
    return {"ok": True}


@app.post("/api/import/cancel")
def cancel_import():
    p = JOBS.get("import")
    if p:
        p.cancel = True
    return {"ok": True}


# ---- updates from GitHub releases ------------------------------------------------
class SkipIn(BaseModel):
    version: str

class InstallIn(BaseModel):
    path: str


@app.get("/api/update/check")
def update_check(force: int = 0):
    info = updater.check(bool(force))
    info["can_install"] = updater.can_install(info)
    # packaged EXE only; the dry-run test mode (never launches anything) behaves like it so the whole flow can be tested
    info["frozen"] = bool(getattr(__import__("sys"), "frozen", False)) or bool(os.environ.get("PHOTAG_UPDATE_DRY_RUN"))
    return info


@app.get("/api/update/whatsnew")
def update_whatsnew(current: int = 0):
    """After an update: the notes of the version that was just installed (from GitHub). With current=1: the running version's notes."""
    n = updater.pending_notice()
    ver = (n["updated"] or {}).get("to") if n["updated"] and not current else __version__
    out = {"updated": n["updated"], "failed": n["failed"], "current": __version__}
    out["notes"] = updater.whats_new(ver) if (ver and (current or n["updated"])) else None
    return out


@app.post("/api/update/whatsnew/ack")
def update_whatsnew_ack():
    updater.ack_notice()
    return {"ok": True}


# ---- backups -------------------------------------------------------------------
class BackupSettingsIn(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = None
    keep: int | None = None
    include_media: bool | None = None
    folder: str | None = None
    compress_media: bool | None = None
    compress_quality: int | None = None
    compress_max_side: int | None = None
    include_videos: bool | None = None

class RestoreIn(BaseModel):
    name: str
    media: bool = False
    settings: bool = False
    overwrite: bool = False


@app.get("/api/backup")
def backup_info():
    con = db.connect()
    s = backup.get_settings()
    snaps = backup.list_snapshots()
    last = next((m["created"] for m in snaps if m["reason"] in ("auto", "manual")), None)
    ferr = backup.folder_error()
    folder = Path(s["folder"]) if s["folder"] else PATHS.root / "backups"       # shown even when it cannot be reached
    return {"settings": s, "folder": str(folder), "snapshots": snaps, "last": last,
            "next": backup.next_due(), "media_bytes": con.execute("SELECT COALESCE(SUM(bytes),0) FROM photos").fetchone()[0],
            "mirror": (not ferr) and any(m.get("media_ok") for m in snaps),
            "health": backup.health(),
            "folder_in_onedrive": cloud.in_onedrive(folder),
            "mirror_bytes": 0 if ferr else backup.media_bytes(),
            "free_bytes": 0 if ferr else shutil.disk_usage(folder).free}


@app.post("/api/backup/settings")
def backup_settings(body: BackupSettingsIn):
    try:
        s = backup.set_settings(body.model_dump(exclude_unset=True))
        threading.Thread(target=backup_task.ensure, args=(s["enabled"],), daemon=True).start()
    except OSError:
        raise err(400, "Can't write to the selected folder")
    return backup_info()


@app.post("/api/backup/run")
def backup_run():
    _start("backup", backup.run_backup, "manual")
    return {"ok": True}


@app.post("/api/backup/verify")
def backup_verify():
    """Check the newest backup now (read-only)."""
    _start("backupcheck", backup.run_verify)
    return {"ok": True}


@app.post("/api/backup/restore")
def backup_restore(body: RestoreIn):
    if _other_job_running("backup"):
        raise err(409, "Another task is currently running. Wait for it to finish and try again")
    if not backup.NAME_RE.match(body.name) or not (backup.backup_dir() / body.name).is_file():
        raise err(404, "Backup not found")
    _start("backup", backup.run_restore, body.name, body.media, body.settings, body.overwrite)
    return {"ok": True}


@app.delete("/api/backup/{name}")
def backup_delete(name: str):
    try:
        backup.delete_snapshot(name)
    except ValueError:
        raise err(404, "Backup not found")
    return {"ok": True}


@app.post("/api/update/open-page")
def update_open_page():
    page = updater.check()["page"]
    if page.startswith("https://github.com/"):          # only ever this project's GitHub pages
        _open_url(page)
    return {"ok": True, "url": page}


@app.post("/api/update/skip")
def update_skip(body: SkipIn):
    updater.skip(body.version)
    return {"ok": True}


class BetaIn(BaseModel):
    on: bool

@app.get("/api/update/beta")
def update_beta_get():
    return {"on": config.get_beta_channel()}

@app.post("/api/update/beta")
def update_beta_set(body: BetaIn):
    config.set_beta_channel(body.on)
    return {"ok": True}


@app.post("/api/update/download")
def update_download():
    _start("update", updater.run_download)
    return {"ok": True}


@app.post("/api/update/install")
def update_install(body: InstallIn):
    try:
        backup.create_snapshot("before-update")      # a catalog copy right before the program is replaced (cheap insurance)
    except Exception:
        pass
    try:
        return updater.launch(body.path)
    except updater.BlockedError:
        subprocess.Popen(["explorer", "/select,", str(body.path)])      # show the file: Explorer may be allowed to run it
        raise err(400, "Windows blocked the update installer (Smart App Control). The folder with the downloaded update was opened: double-click the file there, or see the README")
    except updater.UpdateError as e:
        if str(e) == "not a downloaded update":
            raise err(400, "Update file not found. Download it again")
        raise err(400, "The update could not be applied: {error}", error=str(e)[:200])


# ---- video compression (HandBrake) -------------------------------------------
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
        raise err(404, "File not found")
    if p and not compress.handbrake_version(p):
        raise err(400, "The selected file doesn't look like HandBrakeCLI")
    config.set_handbrake_path(p or None)
    return compress.status()


class CompressIn(BaseModel):
    options: dict = {}

class CompressBatchIn(BaseModel):
    ids: list[int]
    video: dict = {}
    image: dict = {}


@app.post("/api/photo/{pid}/compress")
def start_compress(pid: int, body: CompressIn):
    row = db.connect().execute("SELECT is_video FROM photos WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404)
    _start("compress", compress.run_compress if row["is_video"] else compress.run_compress_image, pid, body.options)
    return {"ok": True}


@app.post("/api/compress/batch")
def start_compress_batch(body: CompressBatchIn):
    ids = list(dict.fromkeys(body.ids))
    if not ids:
        raise err(400, "No files selected")
    _start("compress", compress.run_compress_batch, ids, body.video, body.image)
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
        raise err(404, "No previous version to restore")
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
        raise err(502, "Error from the AI provider: {error}", error=str(e))
    except (ValueError, KeyError) as e:
        raise err(400, "Invalid setting: {error}", error=str(e))


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
        raise err(400, "No photos selected for tagging")
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


@app.get("/api/storage/breakdown")
def storage_breakdown():
    """Where the library's disk space goes -- by folder, by year and by file type -- so you can
    decide what to archive or delete. Trashed photos are reported separately (they still take up
    space until the trash is emptied, see Catalog Settings)."""
    con = db.connect()
    by_folder, by_year, by_type = {}, {}, {}
    total_bytes = total_n = trash_bytes = trash_n = 0
    for r in con.execute("SELECT rel_path, bytes, taken_at, trashed FROM photos"):
        b = r["bytes"] or 0
        if r["trashed"]:
            trash_bytes += b; trash_n += 1
            continue
        total_bytes += b; total_n += 1
        fe = by_folder.setdefault(_folder_of(r["rel_path"]) or "(root)", [0, 0]); fe[0] += 1; fe[1] += b
        y = str(time.gmtime(r["taken_at"]).tm_year) if r["taken_at"] else "?"
        ye = by_year.setdefault(y, [0, 0]); ye[0] += 1; ye[1] += b
        ext = (Path(r["rel_path"]).suffix.lstrip(".") or "?").upper()
        te = by_type.setdefault(ext, [0, 0]); te[0] += 1; te[1] += b

    def rows(d, limit=12):
        out = sorted(({"name": k, "n": v[0], "bytes": v[1]} for k, v in d.items()), key=lambda x: -x["bytes"])
        if len(out) <= limit:
            return out
        rest = out[limit - 1:]
        return out[:limit - 1] + [{"name": "…", "n": sum(x["n"] for x in rest), "bytes": sum(x["bytes"] for x in rest)}]

    return {"total_bytes": total_bytes, "total_n": total_n, "trash_bytes": trash_bytes, "trash_n": trash_n,
            "by_folder": rows(by_folder), "by_year": rows(by_year, limit=50), "by_type": rows(by_type)}


class MapIn(BaseModel):
    ids: list[int]


@app.post("/api/map")
def map_points(body: MapIn):
    """GPS positions of the given photos (for the map view): those with a position, and how many have none."""
    con = db.connect()
    ids = list(dict.fromkeys(body.ids))[:20000]
    pts = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        pts += [dict(r) for r in con.execute(
            f"SELECT id,filename,taken_at,lat,lng,is_video FROM photos WHERE trashed=0 AND lat IS NOT NULL AND lng IS NOT NULL "
            f"AND id IN ({','.join('?' * len(part))})", part)]
    return {"points": pts, "total": len(ids), "without": len(ids) - len(pts)}


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
        where.append("(p.rel_path LIKE ? ESCAPE '!' OR p.rel_path LIKE ? ESCAPE '!' OR p.rel_path LIKE ? ESCAPE '!')")
        f = folder.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        args += [f + "\\%", f + "/%", f.replace("/", "\\") + "\\%"]      # the last one: photos of the user's own folder (absolute Windows paths)
    if year:
        where.append("CAST(strftime('%Y', p.taken_at, 'unixepoch') AS INT)=?"); args.append(year)
    if q:
        where.append("(p.filename LIKE ? OR p.description LIKE ? "
                     "OR p.id IN (SELECT photo_id FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.name LIKE ?) "
                     "OR p.id IN (SELECT pp.photo_id FROM photo_people pp JOIN people pe ON pe.id=pp.person_id WHERE pe.name LIKE ?))")
        args += [f"%{q}%"] * 4
    sql = (f"SELECT p.id,p.filename,p.is_video,p.taken_at,p.favorited,p.rating,p.width,p.height,"
           f"p.flag,p.label,p.quick,p.edited,p.bytes,p.imported_at,p.trashed_at,p.rel_path,p.lat,p.lng,"
           f"p.camera_make,p.camera_model,p.lens,p.focal_length,p.focal_length_35mm,"
           f"EXISTS(SELECT 1 FROM photo_tags pt WHERE pt.photo_id=p.id) has_kw, "
           f"(SELECT a.score FROM photo_analysis a WHERE a.photo_id=p.id) score "
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
            raise err(415, "No preview found in the RAW file")
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


def _not_in_own_folder(row):
    """Photos of the user's own folder (app/refmode.py) are never changed by photag."""
    if refmode.is_external(row["rel_path"]):
        raise err(409, "This photo is in your own folder, which photag never changes. Import a copy if you want to edit it")


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
    if m.write_exif:
        _not_in_own_folder(r)
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


class ForeverIn(BaseModel):
    ids: list[int]

@app.post("/api/photos/delete-forever")
def delete_forever(body: ForeverIn):
    """Delete photos that are already in the trash for good (files included). Photos outside the trash are ignored."""
    n = importer.delete_trashed_by_id(db.connect(), body.ids)
    return {"ok": True, "deleted": n}


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
        raise err(400, "Cannot edit")
    _not_in_own_folder(r)
    if images.is_raw(Path(r["rel_path"])):
        raise err(400, "Editing RAW files is not supported — export a JPEG and edit that")
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
        raise err(400, "No original version")
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
        raise err(501, "Casting is only available on Windows")
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
    raise err(501, "No external player found")


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
        raise err(400, "Empty name")
    if con.execute("SELECT 1 FROM albums WHERE name=?", (name,)).fetchone():
        raise err(409, "A collection with this name already exists")
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
        raise err(409, "A collection with this name already exists")
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


class TripIn(BaseModel):
    is_trip: bool


@app.post("/api/album/{aid}/trip")
def album_trip(aid: int, body: TripIn):
    """Mark/unmark a collection as a trip (gets an "Open in triplan" button). Unmarking also forgets
    which triplan trip it was linked to -- re-marking it starts the link fresh."""
    con = db.connect()
    if body.is_trip:
        con.execute("UPDATE albums SET is_trip=1 WHERE id=?", (aid,))
    else:
        con.execute("UPDATE albums SET is_trip=0, triplan_trip_id=NULL WHERE id=?", (aid,))
    con.commit()
    return {"ok": True}


# ---- photag x triplan (connect, pick a trip, open it directly) -------------
class TriplanConnectIn(BaseModel):
    email: str
    password: str


@app.get("/api/triplan/status")
def triplan_status():
    return triplan.status()


@app.post("/api/triplan/connect")
def triplan_connect(body: TriplanConnectIn):
    try:
        return triplan.connect(body.email, body.password)
    except triplan.TriplanError as e:
        raise err(400, "Could not connect to triplan: {error}", error=str(e))


@app.post("/api/triplan/disconnect")
def triplan_disconnect():
    triplan.disconnect()
    return {"ok": True}


@app.get("/api/triplan/trips")
def triplan_trips():
    try:
        return triplan.list_trips()
    except triplan.TriplanError as e:
        raise err(400, "Could not read your trips from triplan: {error}", error=str(e))


class TriplanTripIn(BaseModel):
    trip_id: str | None = None

@app.post("/api/album/{aid}/triplan-trip")
def album_triplan_trip(aid: int, body: TriplanTripIn):
    """Links (or unlinks) this collection to a specific triplan trip, so "Open in triplan" goes
    straight to it instead of triplan's home page."""
    con = db.connect()
    con.execute("UPDATE albums SET triplan_trip_id=? WHERE id=?", (body.trip_id, aid))
    con.commit()
    return {"ok": True}


class TriplanOpenIn(BaseModel):
    album_id: int | None = None

@app.post("/api/triplan/open")
def triplan_open(body: TriplanOpenIn = TriplanOpenIn()):
    trip_id = None
    if body.album_id is not None:
        row = db.connect().execute("SELECT triplan_trip_id FROM albums WHERE id=?", (body.album_id,)).fetchone()
        trip_id = row["triplan_trip_id"] if row else None
    url = triplan.trip_url(trip_id)
    _open_url(url)
    return {"ok": True, "url": url}


# ---- saved searches (Advanced Search) ------------------------------------------------
class SearchIn(BaseModel):
    name: str
    criteria: dict


@app.get("/api/searches")
def searches():
    """Saved searches and smart collections. `ids` is evaluated now, so a smart collection is always up to date."""
    con = db.connect()
    out = []
    for r in con.execute("SELECT id,name,criteria FROM saved_searches ORDER BY name"):
        c = smart.parse(r["criteria"])
        if c is not None:
            out.append({"id": r["id"], "name": r["name"], "criteria": c, "smart": smart.is_smart(c), "ids": smart.query_ids(con, c)})
    return out


@app.post("/api/searches")
def save_search(body: SearchIn):
    import json
    name = body.name.strip()
    if not name:
        raise err(400, "Empty name")
    con = db.connect()
    con.execute("INSERT INTO saved_searches(name,criteria,created_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET criteria=excluded.criteria",
                (name, json.dumps(body.criteria, ensure_ascii=False), int(time.time())))
    con.commit()
    return {"id": con.execute("SELECT id FROM saved_searches WHERE name=?", (name,)).fetchone()["id"]}


@app.delete("/api/searches/{sid}")
def delete_search(sid: int):
    con = db.connect()
    con.execute("DELETE FROM saved_searches WHERE id=?", (sid,))
    con.commit()
    return {"ok": True}


# ---- smart collections ------------------------------------------------------
class SmartIn(BaseModel):
    criteria: dict


@app.post("/api/smart/ids")
def smart_ids(body: SmartIn):
    """Ids of the photos that match a rule set right now (a smart collection, or a search being edited)."""
    return {"ids": smart.query_ids(db.connect(), body.criteria)}


class GoogleSmartIn(BaseModel):
    min_photos: int = 5


@app.post("/api/smart/from-google")
def smart_from_google(body: GoogleSmartIn):
    """One smart collection per person Google Photos tagged in the imported Takeout (existing names are left alone)."""
    con = db.connect()
    rules = smart.google_people_rules(con, max(1, body.min_photos))
    made = 0
    for r in rules:
        name = r["name"]
        if con.execute("SELECT 1 FROM saved_searches WHERE name=?", (name,)).fetchone():
            continue
        con.execute("INSERT INTO saved_searches(name,criteria,created_at) VALUES(?,?,?)",
                    (name, json.dumps(r["criteria"], ensure_ascii=False), int(time.time())))
        made += 1
    con.commit()
    return {"created": made, "people": len(rules)}


# ---- quality score, duplicates, cleanup --------------------------------------
@app.get("/api/analysis/status")
def analysis_status():
    return analysis.pending_counts(db.connect())


class AnalysisIn(BaseModel):
    eyes: bool = True


@app.post("/api/analysis/run")
def analysis_run(body: AnalysisIn):
    _start("analysis", analysis.run_analysis, body.eyes)
    return {"ok": True}


@app.post("/api/analysis/cancel")
def analysis_cancel():
    p = JOBS.get("analysis")
    if p:
        p.cancel = True
    return {"ok": True}


class RankIn(BaseModel):
    ids: list[int]


@app.post("/api/analysis/rank")
def analysis_rank(body: RankIn):
    """Score and order the selected photos, best first (analyses the ones that were not analysed yet)."""
    if len(body.ids) < 2:
        raise err(400, "Select at least two photos to rank")
    return {"ranked": analysis.rank(db.connect(), body.ids), "eyes_checked": analysis.face_model_present()}


@app.get("/api/analysis/groups")
def analysis_groups(dup: int = analysis.DUP_DIST, sim: int = analysis.SIM_DIST, window: int = analysis.TIME_WINDOW, place: int = analysis.PLACE_M):
    con = db.connect()
    return {"groups": analysis.find_groups(con, min(max(dup, 0), 16), min(max(sim, 0), 24), max(window, 0), max(place, 0)),
            **analysis.pending_counts(con)}


@app.get("/api/analysis/cleanup")
def analysis_cleanup():
    con = db.connect()
    return {**analysis.cleanup_report(con), **analysis.pending_counts(con)}


@app.get("/api/analysis/bursts")
def analysis_bursts():
    """Rapid-fire sequences (burst/continuous-shooting mode), for the grid's optional stacking."""
    return {"bursts": analysis.find_bursts(db.connect())}


# ---- search by meaning (local CLIP) -------------------------------------------
@app.get("/api/semantic/status")
def semantic_status():
    return {**semantic.counts(db.connect()), "can_translate": aitag.can_translate()}


class SemanticIndexIn(BaseModel):
    download: bool = False


@app.post("/api/semantic/index")
def semantic_index(body: SemanticIndexIn):
    _start("semantic", semantic.run_index, body.download)
    return {"ok": True}


@app.post("/api/semantic/cancel")
def semantic_cancel():
    p = JOBS.get("semantic")
    if p:
        p.cancel = True
    return {"ok": True}


@app.get("/api/semantic/search")
def semantic_search(q: str, limit: int = 600):
    q = q.strip()
    if not q:
        raise err(400, "Type what to look for")
    if not semantic.model_ready():
        raise err(409, "The search model is not installed yet")
    con = db.connect()
    if semantic.counts(con)["indexed"] == 0:
        raise err(409, "No photos are indexed for search yet")
    english = q
    if not semantic.is_english(q):
        if not aitag.can_translate():
            raise err(422, "Write the search in English, or set up an AI provider in AI tagging settings to search in other languages")
        try:
            english = aitag.translate_query(q)
        except aitag.AIError as e:
            raise err(502, "Could not translate the search: {error}", error=str(e))
    res = semantic.search(con, english, min(max(limit, 1), 2000))
    return {"q": q, "english": english, "results": [{"id": i, "score": s} for i, s in res.items()]}


# ---- import from folder / export -------------------------------------------
@app.get("/api/scan-folder")
def scan_folder(path: str, recursive: int = 1):
    if not Path(path).is_dir():
        raise err(404, "Folder not found")
    files = importer.scan_folder(path, bool(recursive))
    known = {(r["filename"], r["bytes"]) for r in db.connect().execute("SELECT filename, bytes FROM photos")}
    for f in files:  # Lightroom's "suspected duplicate": same name + size already in the catalog
        f["dup"] = (f["name"], f["bytes"]) in known
    return {"files": files}


@app.get("/api/local-thumb")
def local_thumb(path: str):
    p = Path(path)
    if p.suffix.lower() not in images.IMAGE_EXT or not p.is_file() or cloud.is_online_only(p):
        return Response(status_code=204)          # a cloud-only file is not downloaded just to draw a preview
    data = images.small_preview(p)
    return Response(data, media_type="image/jpeg") if data else Response(status_code=204)


class LrcatIn(BaseModel):
    path: str

@app.get("/api/lrcat-candidates")
def lrcat_candidates():
    """Lightroom catalogs found in its usual default locations, for the import screen to offer
    instead of making the user browse for a file."""
    return {"candidates": importer.find_lrcat_candidates()}

@app.get("/api/lrcat-info")
def lrcat_info(path: str):
    if not Path(path).is_file():
        raise err(404, "Catalog not found")
    try:
        return importer.lrcat_info(path)
    except Exception as e:
        raise err(400, "Cannot read the catalog: {error}", error=str(e))

@app.post("/api/import-lrcat")
def start_import_lrcat(body: LrcatIn):
    if not Path(body.path).is_file():
        raise err(404, "Catalog not found")
    _start("import", importer.run_lrcat_import, body.path)
    return {"ok": True}


class DigikamIn(BaseModel):
    path: str

@app.get("/api/digikam-info")
def digikam_info(path: str):
    if not Path(path).is_file():
        raise err(404, "Database not found")
    try:
        return importer.dkdb_info(path)
    except Exception as e:
        raise err(400, "Cannot read the database: {error}", error=str(e))

@app.post("/api/import-digikam")
def start_import_digikam(body: DigikamIn):
    if not Path(body.path).is_file():
        raise err(404, "Database not found")
    _start("import", importer.run_digikam_import, body.path)
    return {"ok": True}


class ImportFolderIn(BaseModel):
    paths: list[str]
    keywords: list[str] = []
    album: str | None = None
    recover_xmp: bool = False

@app.post("/api/import-folder")
def start_import_folder(body: ImportFolderIn):
    if not body.paths:
        raise err(400, "No files selected")
    kws = [k.strip() for k in body.keywords if k.strip()]
    _start("import", importer.run_folder_import, body.paths, kws, (body.album or "").strip() or None, body.recover_xmp)
    return {"ok": True}


class ExportIn(BaseModel):
    ids: list[int]
    dest: str
    originals: bool = False
    long_edge: int | None = None
    quality: int = 100
    zip: bool = False
    xmp: bool = False

@app.post("/api/export")
def start_export(body: ExportIn):
    if not body.ids or not body.dest.strip():
        raise err(400, "Missing items or destination folder")
    _start("export", importer.run_export, body.ids, body.dest.strip(), body.originals,
           body.long_edge, max(10, min(100, body.quality)), body.zip, body.xmp)
    return {"ok": True}


class ExportHtmlIn(BaseModel):
    ids: list[int]
    dest: str
    long_edge: int | None = 1600
    quality: int = 85
    title: str = "Photos"

@app.post("/api/export-html")
def start_export_html(body: ExportHtmlIn):
    if not body.ids or not body.dest.strip():
        raise err(400, "Missing items or destination file")
    _start("export", importer.run_export_html, body.ids, body.dest.strip(),
           body.long_edge, max(10, min(100, body.quality)), body.title.strip())
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
    """Face groups nobody has named yet (Lightroom's "Unnamed People"), each with a suggested
    name when the group is close to an already-named person -- "is this <name>?" instead of a
    blank box to type into."""
    con = db.connect()
    rows = [dict(r) for r in con.execute(
        "SELECT f.cluster_id id, COUNT(DISTINCT f.photo_id) n, "
        "(SELECT f2.id FROM faces f2 WHERE f2.cluster_id=f.cluster_id ORDER BY f2.det_score DESC LIMIT 1) cover_face "
        "FROM faces f WHERE f.cluster_id IS NOT NULL AND f.person_id IS NULL "
        "GROUP BY f.cluster_id HAVING n>=2 ORDER BY n DESC LIMIT 300").fetchall()]
    suggestions = faces.suggest_names(con)
    for r in rows:
        s = suggestions.get(r["id"])
        if s:
            r["suggested_person_id"], r["suggested_name"], _ = s
    return rows


@app.post("/api/cluster/{cid}/name")
def name_cluster(cid: int, body: RenameIn):
    con = db.connect()
    name = body.name.strip()
    if not name:
        raise err(400, "Empty name")
    con.execute("INSERT OR IGNORE INTO people(name,source) VALUES(?, 'manual')", (name,))
    pid = con.execute("SELECT id FROM people WHERE name=?", (name,)).fetchone()["id"]
    con.execute("UPDATE faces SET person_id=? WHERE cluster_id=?", (pid, cid))
    con.execute("UPDATE people SET cover_face_id=COALESCE(cover_face_id, (SELECT id FROM faces WHERE cluster_id=? "
                "ORDER BY det_score DESC LIMIT 1)) WHERE id=?", (cid, pid))
    con.commit()
    return {"id": pid}


# ---- static frontend (mounted last so /api wins) ---------------------------
app.mount("/", StaticFiles(directory=str(UI), html=True), name="ui")
