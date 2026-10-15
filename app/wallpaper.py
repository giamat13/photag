"""A desktop wallpaper that changes by itself (#55). Windows only.

The user picks where the pictures come from (all photos, favourites, or one collection) and how often the wallpaper changes. Every change
makes a JPEG COPY of the photo as it looks now in <library>/wallpaper (at most 3840 px on the long side) and tells Windows to use it;
the photo itself is only read. The server's background loop calls tick() every half minute, so it keeps working while the window is closed
(as long as photag runs in the background -- the tray icon).
"""
import ctypes
import json
import os
import random
import sys
import time

from . import db, images, render
from .config import PATHS

KEY = "wallpaper"
SOURCES = ("all", "favorites", "album")
ORDERS = ("random", "newest", "oldest")
MIN_MINUTES, MAX_MINUTES = 1, 7 * 24 * 60
DEFAULTS = {"enabled": False, "source": "all", "album_id": None, "minutes": 60, "order": "random"}
_STATE = {"last_id": None, "last_at": 0.0, "pos": 0, "turn": 0}          # kept in memory: after a restart the next change comes at once


def supported() -> bool:
    return sys.platform == "win32"


def get(con) -> dict:
    out = dict(DEFAULTS)
    try:
        out.update(json.loads(db.get_setting(con, KEY, "{}")))
    except ValueError:
        pass
    return out


def save(con, data: dict) -> dict:
    cfg = get(con)
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    if data.get("source") in SOURCES:
        cfg["source"] = data["source"]
    if "album_id" in data:
        cfg["album_id"] = int(data["album_id"]) if data["album_id"] not in (None, "") else None
    if "minutes" in data:
        cfg["minutes"] = max(MIN_MINUTES, min(MAX_MINUTES, int(data["minutes"])))
    if data.get("order") in ORDERS:
        cfg["order"] = data["order"]
    if cfg["source"] == "album" and not cfg["album_id"]:
        cfg["source"] = "all"
    db.set_setting(con, KEY, json.dumps(cfg))
    if cfg["enabled"]:
        _STATE["last_at"] = 0.0                       # a new choice shows at once
    return cfg


def candidates(con, cfg: dict) -> list[int]:
    where = "p.trashed=0 AND p.is_video=0 AND COALESCE(p.flag,0)<>-1"
    args: list = []
    if cfg["source"] == "favorites":
        where += " AND p.favorited=1"
    elif cfg["source"] == "album" and cfg["album_id"]:
        where += " AND p.id IN (SELECT photo_id FROM photo_albums WHERE album_id=?)"
        args.append(cfg["album_id"])
    order = "p.taken_at ASC" if cfg["order"] == "oldest" else "p.taken_at DESC"
    return [r["id"] for r in con.execute(f"SELECT p.id FROM photos p WHERE {where} ORDER BY {order}, p.id", args)]


def _pick(ids: list[int], cfg: dict) -> int | None:
    if not ids:
        return None
    if cfg["order"] == "random":
        pool = [i for i in ids if i != _STATE["last_id"]] or ids
        return random.choice(pool)
    _STATE["pos"] = (_STATE["pos"] + 1) % len(ids) if _STATE["last_id"] in ids else 0
    return ids[_STATE["pos"]]


def _set_windows_wallpaper(path: str) -> bool:
    SPI_SETDESKWALLPAPER, SPIF_UPDATEINIFILE, SPIF_SENDCHANGE = 20, 1, 2
    return bool(ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))


def make_file(con, pid: int, apply: bool = True):
    """The JPEG copy for photo `pid` in the wallpaper folder (a new name every time: Windows keeps showing a file it already knows under the same name).
    Returns its path; with apply (Windows) the wallpaper is changed to it."""
    row = con.execute("SELECT id, sha256, rel_path, orig_backup, edited, edit_ops FROM photos WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    folder = PATHS.root / "wallpaper"
    folder.mkdir(parents=True, exist_ok=True)
    _STATE["turn"] += 1
    out = folder / f"photag-wallpaper-{os.getpid()}-{_STATE['turn'] % 4}.jpg"
    im = images.open_image(render.current_path(row))
    im.thumbnail((3840, 3840))
    im.save(out, "JPEG", quality=92)
    if apply and supported() and not _set_windows_wallpaper(str(out)):
        raise OSError("Windows did not accept the wallpaper")
    for old in folder.glob("photag-wallpaper-*.jpg"):                 # only the last few are kept
        if old != out and time.time() - old.stat().st_mtime > 600:
            old.unlink(missing_ok=True)
    return out


def change_now(con, cfg: dict | None = None) -> int | None:
    """Pick a photo by the settings and put it on the desktop. Returns its id (None when no photo matches)."""
    cfg = cfg or get(con)
    pid = _pick(candidates(con, cfg), cfg)
    if pid is None:
        return None
    make_file(con, pid)
    _STATE["last_id"], _STATE["last_at"] = pid, time.time()
    return pid


def tick(con) -> int | None:
    """Called by the server's background loop: changes the wallpaper when it is time."""
    if not supported():
        return None
    cfg = get(con)
    if not cfg["enabled"] or time.time() - _STATE["last_at"] < cfg["minutes"] * 60:
        return None
    try:
        return change_now(con, cfg)
    finally:
        _STATE["last_at"] = time.time()            # a failure or an empty collection is tried again after the interval, not every half minute
