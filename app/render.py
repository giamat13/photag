"""Non-destructive edits: an edit is only a set of instructions (photos.edit_ops) -- the photo file itself is never
rewritten. How an edited photo LOOKS is a derived picture in <library>/renders, rebuilt on demand and never backed up.

  library file (photos.rel_path)   always the untouched original; its sha256 / bytes are the original's
  photos.edit_ops                  JSON of the develop settings (rotate, crop, brightness, contrast, saturation, grayscale)
  <library>/renders/<id>-<key>.jpg the edited look; <key> hashes the settings AND the original's sha256, so a changed
                                   original or changed settings can never be shown from a stale render
  photos.width / height            the size of the LOOK (a crop or rotation changes the aspect the grid lays out)

Photos edited by an older version are "legacy": photos.orig_backup names a pristine copy and rel_path is the re-saved
working file. migrate_one() turns one into the new model (the original goes back to rel_path); until that has happened
every function here treats such a photo exactly as before, so nothing breaks half-way.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import images
from .config import PATHS

_LOCK = threading.Lock()
REFUSED: dict = {}          # photo id -> why migrate_one() left it as it was (for the log)


def is_neutral(ops: dict) -> bool:
    """Every setting at its zero point: nothing to render."""
    return (not ops.get("rotate") and not ops.get("grayscale")
            and ops.get("crop") in (None, [0, 0, 1, 1])
            and all(ops.get(k) in (None, 1, 1.0) for k in ("brightness", "contrast", "saturation")))


def _col(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def is_legacy(row) -> bool:
    return bool(_col(row, "orig_backup"))


def _suffix(src: Path) -> str:
    return ".png" if src.suffix.lower() == ".png" else ".jpg"


def render_key(ops: dict, sha: str) -> str:
    return hashlib.sha1((json.dumps(ops, sort_keys=True, separators=(",", ":")) + "|" + (sha or "")).encode()).hexdigest()[:12]


def drop_renders(photo_id: int):
    for p in PATHS.renders.glob(f"{int(photo_id)}-*"):
        p.unlink(missing_ok=True)


def make_render(row, ops: dict):
    """Renders `ops` on top of the original into the cache. Returns (path, width, height).
    Raises if the original cannot be edited -- before anything else was changed."""
    src = PATHS.media / row["rel_path"]
    out = PATHS.renders / f"{row['id']}-{render_key(ops, row['sha256'])}{_suffix(src)}"
    with _LOCK:
        if not out.exists():
            PATHS.renders.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f"{out.stem}.part{out.suffix}")
            try:
                images.apply_edit(src, ops, tmp)
                os.replace(tmp, out)
            finally:
                tmp.unlink(missing_ok=True)
            for p in PATHS.renders.glob(f"{row['id']}-*"):      # older looks of this photo
                if p != out:
                    p.unlink(missing_ok=True)
    w, h = images.dimensions(out)
    return out, w, h


def ensure_render(row) -> Path | None:
    """The render of an edited (non-legacy) photo, made now if it is missing. None if there is none to show."""
    if is_legacy(row) or not _col(row, "edited") or not _col(row, "edit_ops"):
        return None
    try:
        ops = json.loads(row["edit_ops"])
        if is_neutral(ops):
            return None
        return make_render(row, ops)[0]
    except Exception:
        return None


def current_path(row) -> Path:
    """The file that shows the photo as it looks NOW: the render for an edited photo, otherwise the library file
    (for a legacy photo that file IS the edited picture)."""
    return ensure_render(row) or (PATHS.media / row["rel_path"])


def migrate_one(con, row) -> bool:
    """Brings one legacy photo (orig_backup set) into the new model. True if it is in the new model afterwards.

    Order matters, so that nothing is ever lost: the pristine original is copied over the working file through a
    temporary name (a new inode, so a backup that hard-links the old working file keeps its content), the catalog
    row is updated and committed, and only then is the old backup file deleted. The working file is derived data --
    if anything fails before the commit it is rebuilt from the backup and the settings, which stay until the end.
    False (and the photo stays as the old model expects it) if the backup is missing/empty, its content already
    belongs to another photo, or the catalog could not be updated."""
    if not row["orig_backup"]:
        return True
    pid = row["id"]
    work, orig = PATHS.media / row["rel_path"], PATHS.media / row["orig_backup"]
    ops, neutral, swapped = None, True, False
    try:
        if not orig.is_file() or orig.stat().st_size == 0:
            REFUSED[pid] = "the pristine copy is missing or empty"
            return False
        sha = images.sha256_file(orig)
        if con.execute("SELECT 1 FROM photos WHERE sha256=? AND id<>?", (sha, pid)).fetchall():
            REFUSED[pid] = "the original is already in the catalog as another photo"
            return False
        try:
            ops = json.loads(row["edit_ops"]) if row["edit_ops"] else None
        except ValueError:
            ops = None
        neutral = not ops or is_neutral(ops)
        tmp = work.with_name(work.name + ".migrating")
        try:
            shutil.copy2(orig, tmp)
            os.replace(tmp, work)
            swapped = True
        finally:
            tmp.unlink(missing_ok=True)
        w, h = images.dimensions(work)
        args = (sha, work.stat().st_size, w, h, 0 if neutral else 1, None if neutral else json.dumps(ops), pid)
        for attempt in range(5):
            try:
                con.execute("UPDATE photos SET sha256=?, bytes=?, width=?, height=?, orig_backup=NULL, edited=?, edit_ops=? WHERE id=?", args)
                con.commit()
                break
            except sqlite3.OperationalError as e:               # another connection is writing: wait a moment and try again
                con.rollback()
                if "locked" not in str(e) and "busy" not in str(e):
                    raise
                if attempt == 4:
                    REFUSED[pid] = f"the catalog is busy ({e})"
                    return _put_back(orig, work, ops, neutral)
                time.sleep(0.4)
            except sqlite3.Error as e:
                con.rollback()
                REFUSED[pid] = f"the catalog could not be updated ({e})"
                return _put_back(orig, work, ops, neutral)
    except Exception as e:
        print(f"migrating photo {pid} to non-destructive editing failed: {e!r}", file=sys.stderr, flush=True)   # retried later
        REFUSED[pid] = f"unexpected error ({e!r})"
        return _put_back(orig, work, ops, neutral) if swapped else False
    # From here the catalog says "migrated"; what is left is cleanup and rebuilding derived files, none of which can undo that.
    try:
        orig.unlink(missing_ok=True)
        images.thumb_path(row["sha256"]).unlink(missing_ok=True)
        images.thumb_path(sha).unlink(missing_ok=True)
        look = work
        if not neutral:
            fresh = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
            try:
                look, w, h = make_render(fresh, ops)
                con.execute("UPDATE photos SET width=?, height=? WHERE id=?", (w, h, pid))
                con.commit()
            except Exception:
                look = work                                     # the render is rebuilt on demand when the photo is first looked at
        images.make_thumb(look, sha)
    except Exception:
        pass
    return True


def _put_back(orig: Path, work: Path, ops, neutral: bool) -> bool:
    """The catalog was not updated: leave the working file as the old model expects (the edited look) and report failure."""
    try:
        if ops and not neutral:
            images.apply_edit(orig, ops, work)
    except Exception:
        pass
    return False


_FAILS: dict = {}           # photo id -> how many times migrating it failed this session


def migrate_batch(con, skip: set, limit: int = 20) -> tuple[int, int]:
    """Migrates up to `limit` legacy photos not in `skip`. Returns (migrated, failed). A photo that fails is retried
    on later passes (the cause is often only a busy catalog) and goes into `skip` after its third failure, so one
    stubborn photo never blocks the rest."""
    rows = con.execute("SELECT * FROM photos WHERE orig_backup IS NOT NULL AND orig_backup<>'' ORDER BY id").fetchall()
    ok = bad = 0
    for r in rows:
        if r["id"] in skip:
            continue
        if ok + bad >= limit:
            break
        if migrate_one(con, r):
            ok += 1
            _FAILS.pop(r["id"], None)
        else:
            bad += 1
            _FAILS[r["id"]] = _FAILS.get(r["id"], 0) + 1
            if _FAILS[r["id"]] >= 3:
                skip.add(r["id"])
    return ok, bad
