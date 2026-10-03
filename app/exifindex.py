"""The full EXIF of every photo, kept in the catalog (photos.exif_json) so it can be searched, shown and exported
without ever opening the image file again -- and so edits never have to touch the file to preserve it.

New photos get it at import time. Photos that were in the library before this existed are filled in by a gentle
background pass (backfill_batch, driven by server._exif_loop) and, for the one photo you are looking at, on demand.
"""
import json
from pathlib import Path

from . import cloud, images
from .config import PATHS


def source_path(row) -> Path:
    """The file whose EXIF counts: the pristine original (a photo edited the old way keeps it in orig_backup, and
    the working file lost its EXIF when it was re-saved), otherwise the library file."""
    keys = row.keys() if hasattr(row, "keys") else ()
    rel = (row["orig_backup"] if "orig_backup" in keys and row["orig_backup"] else None) or row["rel_path"]
    return PATHS.media / rel


def get(con, pid: int) -> dict | None:
    """The photo's EXIF as a dict ({} if it has none), read from the file and stored if not done yet.
    None if the photo does not exist or its file cannot be read right now."""
    r = con.execute("SELECT id, rel_path, orig_backup, exif_json FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    if r["exif_json"] is not None:
        try:
            return json.loads(r["exif_json"])
        except ValueError:
            pass                                     # damaged text: read it again below
    p = source_path(r)
    if not p.is_file() or cloud.is_online_only(p):
        return None
    text = images.exif_json_text(p)
    con.execute("UPDATE photos SET exif_json=? WHERE id=?", (text, pid))
    con.commit()
    return json.loads(text)


def read_file(con, pid: int) -> dict | None:
    """The photo's EXIF read from the file right now, nothing stored (catalog mode switched off). A photo whose
    file was edited into the file the old way is read from its pristine copy. None if it cannot be read."""
    r = con.execute("SELECT id, rel_path, orig_backup FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    p = source_path(r)
    if not p.is_file() or cloud.is_online_only(p):
        return None
    return json.loads(images.exif_json_text(p))


def backfill_batch(con, after_id: int = 0, limit: int = 50) -> tuple[int, int]:
    """Fills in up to `limit` photos that have no stored EXIF yet, with ids above `after_id`.
    Returns (done, last_id): `done` photos were filled in; last_id is where the next batch continues
    (== after_id when there is nothing left). Files that are missing or only in the cloud are skipped, not retried
    within the same pass, so one unreadable file never stalls the rest."""
    rows = con.execute("SELECT id, rel_path, orig_backup FROM photos WHERE exif_json IS NULL AND id>? ORDER BY id LIMIT ?",
                       (after_id, limit)).fetchall()
    done, last = 0, after_id
    for r in rows:
        last = r["id"]
        p = source_path(r)
        try:
            if not p.is_file() or cloud.is_online_only(p):
                continue
            con.execute("UPDATE photos SET exif_json=? WHERE id=?", (images.exif_json_text(p), r["id"]))
            done += 1
        except Exception:
            continue
    con.commit()
    return done, last
