"""New media made from existing ones, and small helpers for the newer features.

Rule number one: every function here makes a NEW file in the library (or only changes the catalog). The picture or video it starts
from -- in the library, in the user's own folder (reference mode), opened in the viewer -- is only read.

  save_frame   a still picture from a video                       -> a new photo
  trim         a part of a video (start .. end)                   -> a new video
  from_upload  a file dropped on the window or pasted (Ctrl+V)    -> a new photo / video
  merge_people two faces groups / people that are the same person -> catalog only
  new_summary  what came in with the last import                  -> numbers for the "New in library" window
  fill_colors / bucket  the dominant colour of each photo, for the colour search
"""

import os
import re
import tempfile
import time
from pathlib import Path

from . import db, ffmpeg, images, importer, render
from .config import PATHS

UPLOAD_MAX = 600 * 1024 * 1024
SUPPORTED = images.IMAGE_EXT | images.VIDEO_EXT


class ExtrasError(Exception):
    """key = a source string the UI translates (see server.err)."""

    def __init__(self, key: str, **vars):
        super().__init__(key)
        self.key, self.vars = key, vars


def _source(con, pid: int) -> tuple[dict, Path]:
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ExtrasError("Photo not found")
    p = Path(render.current_path(r) if not r["is_video"] else PATHS.media / r["rel_path"])
    if not p.is_file():
        raise ExtrasError("The file was not found")
    return dict(r), p


def _stamp(sec: float) -> str:
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}m{int(sec % 60):02d}s"


def _add(con, f: Path, row: dict, offset: int = 0) -> dict:
    pid, new = importer._ingest_file(con, f, taken=(row["taken_at"] + offset) if row.get("taken_at") else None,
                                     lat=row.get("lat"), lng=row.get("lng"))
    con.commit()
    return {"id": pid, "new": new}


def save_frame(pid: int, t: float) -> dict:
    """The video's picture at second `t`, saved as a new JPEG photo."""
    if not ffmpeg.available():
        raise ExtrasError("ffmpeg is missing for verifying the result. Run: pip install imageio-ffmpeg")
    con = db.connect()
    row, src = _source(con, pid)
    if not row["is_video"]:
        raise ExtrasError("This is not a video")
    t = max(0.0, float(t or 0))
    with tempfile.TemporaryDirectory(prefix="photag-frame-") as td:
        out = Path(td) / f"{src.stem} frame {_stamp(t)}.jpg"
        code, _o, err = ffmpeg.run(["-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)])
        if code != 0 or not out.is_file() or out.stat().st_size == 0:
            raise ExtrasError("The picture could not be taken from the video")
        return _add(con, out, row, int(t))


def trim(pid: int, start: float, end: float, exact: bool = False) -> dict:
    """A part of the video as a new video. Not exact (the default): the streams are copied, instant and lossless, but the cut starts
    at the nearest earlier key frame. Exact: re-encoded, the cut is where asked."""
    if not ffmpeg.available():
        raise ExtrasError("ffmpeg is missing for verifying the result. Run: pip install imageio-ffmpeg")
    con = db.connect()
    row, src = _source(con, pid)
    if not row["is_video"]:
        raise ExtrasError("This is not a video")
    start, end = max(0.0, float(start or 0)), float(end or 0)
    if end <= start + 0.05:
        raise ExtrasError("The end must be after the start")
    ext = src.suffix.lower() or ".mp4"
    if ext == ".gif":
        raise ExtrasError("This is not a video")
    with tempfile.TemporaryDirectory(prefix="photag-trim-") as td:
        out = Path(td) / f"{src.stem} trimmed {_stamp(start)}-{_stamp(end)}{ext}"
        args = ["-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}"]
        args += (["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-c:a", "aac"] if exact else ["-c", "copy"])
        args += ["-avoid_negative_ts", "make_zero", str(out)]
        code, _o, err = ffmpeg.run(args)
        if code != 0 or not out.is_file() or out.stat().st_size == 0:
            raise ExtrasError("The video could not be trimmed")
        return _add(con, out, row, int(start))


def from_upload(name: str, data: bytes, modified: float | None = None) -> dict:
    """A file that came from a drop on the window or a paste: it is copied into the library like any import."""
    if len(data) > UPLOAD_MAX:
        raise ExtrasError("The file is too big")
    name = importer._safe_component(os.path.basename(name or "")) or "image.png"
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise ExtrasError("This kind of file cannot be imported")
    if re.fullmatch(r"(image|blob|unnamed)\.\w+", name.lower()):                 # a picture pasted from the clipboard has no name
        name = time.strftime("Pasted %Y-%m-%d %H-%M-%S", time.localtime()) + ext
    con = db.connect()
    with tempfile.TemporaryDirectory(prefix="photag-drop-") as td:
        f = Path(td) / name
        f.write_bytes(data)
        if modified and modified > 0:
            os.utime(f, (modified, modified))
        pid, new = importer._ingest_file(con, f)
        con.commit()
    return {"id": pid, "new": new}


# ---- merging two faces groups / people ----------------------------------------------------
def merge_people(src: dict, dst: dict) -> dict:
    """src / dst: {"person": id} or {"cluster": id}. The source's faces and names move to the destination; only the catalog changes.
    A group can be merged into a person or into another group; a named person only into another person."""
    con = db.connect()
    sp, sc, dp, dc = src.get("person"), src.get("cluster"), dst.get("person"), dst.get("cluster")
    if (sp is None) == (sc is None) or (dp is None) == (dc is None):
        raise ExtrasError("Cannot edit")
    if sp is not None and dc is not None:
        raise ExtrasError("A named person can only be merged into another named person")
    if (sp is not None and sp == dp) or (sc is not None and sc == dc):
        raise ExtrasError("Cannot edit")
    if dp is not None and not con.execute("SELECT 1 FROM people WHERE id=?", (dp,)).fetchone():
        raise ExtrasError("Photo not found")
    moved = 0
    if sp is not None:                                              # person -> person
        if not con.execute("SELECT 1 FROM people WHERE id=?", (sp,)).fetchone():
            raise ExtrasError("Photo not found")
        moved = con.execute("UPDATE faces SET person_id=? WHERE person_id=?", (dp, sp)).rowcount
        con.execute("UPDATE OR IGNORE photo_people SET person_id=? WHERE person_id=?", (dp, sp))
        con.execute("DELETE FROM photo_people WHERE person_id=?", (sp,))
        con.execute("UPDATE people SET cover_face_id=COALESCE(cover_face_id, (SELECT cover_face_id FROM people WHERE id=?)) WHERE id=?", (sp, dp))
        con.execute("DELETE FROM people WHERE id=?", (sp,))
    elif dp is not None:                                            # group -> person
        moved = con.execute("UPDATE faces SET person_id=? WHERE cluster_id=? AND person_id IS NULL", (dp, sc)).rowcount
        con.execute("UPDATE people SET cover_face_id=COALESCE(cover_face_id, (SELECT id FROM faces WHERE cluster_id=? ORDER BY det_score DESC LIMIT 1)) WHERE id=?", (sc, dp))
    else:                                                           # group -> group
        moved = con.execute("UPDATE faces SET cluster_id=? WHERE cluster_id=?", (dc, sc)).rowcount
    con.commit()
    return {"moved": moved}


# ---- "New in library" ---------------------------------------------------------------------
def new_summary(con) -> dict:
    since = int(db.get_setting(con, "last_import", 0) or 0)
    q = "FROM photos p WHERE p.trashed=0 AND p.imported_at>=?"
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]          # noqa: E731
    total = one("SELECT COUNT(*) " + q, since)
    videos = one("SELECT COUNT(*) " + q + " AND p.is_video=1", since)
    return {
        "since": since, "total": total, "videos": videos, "photos": total - videos,
        "no_place": one("SELECT COUNT(*) " + q + " AND (p.lat IS NULL OR p.lng IS NULL)", since),
        "no_date": one("SELECT COUNT(*) " + q + " AND (p.taken_at IS NULL OR p.taken_at=0)", since),
        "unanalyzed": one("SELECT COUNT(*) " + q + " AND p.is_video=0 AND p.id NOT IN (SELECT photo_id FROM photo_analysis)", since),
        "no_faces": one("SELECT COUNT(*) " + q + " AND p.is_video=0 AND p.id NOT IN (SELECT photo_id FROM faces)", since),
    }


# ---- dominant colour ----------------------------------------------------------------------
COLORS = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "brown", "black", "white", "gray")


def _hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """(hue 0-360, saturation 0-1, value 0-1). Written out here: a new standard-library import would change what the packaged
    runtime has to contain (see tools/test_runtime_lock.py)."""
    r, g, b = r / 255, g / 255, b / 255
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = 60 * (((g - b) / d) % 6)
    elif mx == g:
        h = 60 * ((b - r) / d + 2)
    else:
        h = 60 * ((r - g) / d + 4)
    return h, (0.0 if mx == 0 else d / mx), mx


def bucket(r: int, g: int, b: int) -> str:
    h, s, v = _hsv(r, g, b)
    if v < 0.18:
        return "black"
    if s < 0.14:
        return "white" if v > 0.85 else "gray"
    if h < 15 or h >= 345:
        return "red" if v > 0.45 else "brown"
    if h < 40:
        return "orange" if v > 0.6 else "brown"
    if h < 70:
        return "yellow" if v > 0.55 else "brown"
    if h < 165:
        return "green"
    if h < 200:
        return "cyan"
    if h < 255:
        return "blue"
    if h < 300:
        return "purple"
    return "pink"


def dominant(path: Path) -> tuple[int, int, int] | None:
    """The colour most of the picture is made of (a 6-colour palette of a tiny copy; colourful beats grey when it is close)."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((48, 48))
            pal = im.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
            counts = sorted(pal.getcolors() or [], reverse=True)
            flat = pal.getpalette() or []
    except Exception:
        return None
    if not counts:
        return None
    total = sum(n for n, _ in counts)
    best, best_score = None, -1.0
    for n, i in counts:
        r, g, b = flat[i * 3:i * 3 + 3]
        _h, s, v = _hsv(r, g, b)
        score = n / total * (1 + 1.5 * s * (1 if v > 0.18 else 0))
        if score > best_score:
            best, best_score = (r, g, b), score
    return best


def fill_colors(con, budget: float | None = None, limit: int = 100000) -> int:
    """Work out the dominant colour for the photos that have none yet (from their thumbnails). Returns how many were done."""
    t0 = time.time()
    con.execute("CREATE TABLE IF NOT EXISTS photo_color(photo_id INTEGER PRIMARY KEY, sha TEXT, r INTEGER, g INTEGER, b INTEGER, name TEXT)")
    rows = con.execute("SELECT p.id, p.sha256 FROM photos p WHERE p.trashed=0 AND p.is_video=0 AND p.id NOT IN (SELECT photo_id FROM photo_color) LIMIT ?", (limit,)).fetchall()
    n = 0
    for r in rows:
        if budget is not None and time.time() - t0 > budget:
            break
        tp = images.thumb_path(r["sha256"])
        rgb = dominant(tp) if tp.is_file() else None
        if rgb:
            con.execute("INSERT OR REPLACE INTO photo_color(photo_id,sha,r,g,b,name) VALUES(?,?,?,?,?,?)", (r["id"], r["sha256"], *rgb, bucket(*rgb)))
        else:
            con.execute("INSERT OR REPLACE INTO photo_color(photo_id,sha,r,g,b,name) VALUES(?,?,?,?,?,?)", (r["id"], r["sha256"], 0, 0, 0, ""))
        n += 1
    con.commit()
    return n
