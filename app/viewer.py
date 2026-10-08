"""The picture viewer: "Open with photag" shows a picture WITHOUT adding it to the catalog (like the Photos app of Windows).

photag.exe "C:\\pictures\\a.jpg" opens a window with a page of its own (ui/viewer.html) for that file and the pictures next to it.
Nothing here touches the catalog or the library: a file is only ever read, and only the files the user opened (and the
pictures in the same folder) can be asked for, through random tokens that live as long as the program runs -- a web page in
a browser cannot read arbitrary files through this server (it cannot guess a token, and the server only answers photag's own window).
Adding a picture to the library is a separate, explicit button (server.py: /api/viewer/{token}/import).
"""
import hashlib
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import images

WEB_EXT = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".avif"}       # the window shows these as they are
CONVERT_EXT = {".heic", ".heif", ".avifs", ".tif", ".tiff"} | images.RAW_EXT                    # shown as a JPEG made from the file
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif"}
SUPPORTED = WEB_EXT | CONVERT_EXT
MAX_TOKENS = 20000


class NotAPicture(Exception):
    pass


_LOCK = threading.Lock()
_PATH_OF: dict[str, Path] = {}
_TOKEN_OF: dict[str, str] = {}


def is_picture(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED


def token_for(path: Path) -> str:
    key = str(path)
    with _LOCK:
        tok = _TOKEN_OF.get(key)
        if tok:
            return tok
        if len(_PATH_OF) >= MAX_TOKENS:                     # a very long session: forget the oldest half
            for old in list(_PATH_OF)[: MAX_TOKENS // 2]:
                _TOKEN_OF.pop(str(_PATH_OF.pop(old)), None)
        tok = os.urandom(12).hex()
        _PATH_OF[tok] = path
        _TOKEN_OF[key] = tok
        return tok


def path_of(token: str) -> Path:
    with _LOCK:
        p = _PATH_OF.get(token)
    if p is None:
        raise KeyError(token)
    return p


def open_path(raw: str) -> str:
    """Token for a file the user opened. Refuses anything that is not an existing picture file."""
    try:
        p = Path(raw).resolve()
    except (OSError, ValueError):
        raise NotAPicture(raw)
    if not p.is_file() or not is_picture(p):
        raise NotAPicture(raw)
    return token_for(p)


def _natural(name: str):
    """Explorer-like order: IMG_2 before IMG_10, case ignored."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", name)]


def siblings(path: Path) -> list[Path]:
    """The pictures in the same folder, in the order a file manager shows them."""
    try:
        with os.scandir(path.parent) as it:
            names = [e.name for e in it if e.is_file() and Path(e.name).suffix.lower() in SUPPORTED]
    except OSError:
        return [path]
    names.sort(key=_natural)
    out = [path.parent / n for n in names]
    return out or [path]


def info(token: str) -> dict:
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    sib = siblings(p)
    try:
        idx = sib.index(p)
    except ValueError:                                       # renamed / just created: still show it, first in the list
        sib, idx = [p] + sib, 0
    st = p.stat()
    w, h = images.dimensions(p)

    def tok(i):
        return token_for(sib[i]) if 0 <= i < len(sib) else None

    return {"token": token, "name": p.name, "folder": str(p.parent), "bytes": st.st_size, "modified": int(st.st_mtime),
            "width": w, "height": h, "index": idx + 1, "count": len(sib),
            "prev": tok(idx - 1) if idx > 0 else None, "next": tok(idx + 1) if idx + 1 < len(sib) else None,
            "first": tok(0), "last": tok(len(sib) - 1), "converted": p.suffix.lower() in CONVERT_EXT}


def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "photag-viewer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prune(d: Path, keep: int = 60):
    try:
        files = sorted(d.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep]:
            f.unlink(missing_ok=True)
    except OSError:
        pass


def image_file(token: str) -> tuple[Path, str]:
    """(file to send, media type): the file itself when a browser can show it, else a JPEG made from it (cached in the temp folder)."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if p.suffix.lower() in WEB_EXT:
        return p, MIME.get(p.suffix.lower(), "application/octet-stream")
    st = p.stat()
    out = _cache_dir() / (hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:24] + ".jpg")
    if not out.exists():
        images.open_image(p).save(out, "JPEG", quality=92)
        _prune(out.parent)
    return out, "image/jpeg"


# ---- who is still looking: the program that started the server for a picture stays until the last viewer window is gone ----
_SEEN = {"viewer": 0.0, "main": 0.0}


def seen(kind: str):
    _SEEN[kind] = time.time()


def idle_seconds() -> tuple[float, float]:
    """(seconds since a viewer page asked for anything, seconds since the main window did)."""
    now = time.time()
    return now - _SEEN["viewer"], now - _SEEN["main"]


class SeenMiddleware:
    """Pure ASGI: notes the last request of the viewer pages and of the main window (see idle_seconds)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/api/viewer"):
                seen("viewer")
            elif path.startswith("/api/"):
                seen("main")
        return await self.app(scope, receive, send)


def exif(token: str) -> dict:
    """The picture's EXIF as {"Image": {...}, "Exif": {...}, "GPS": {...}} plus its location ({"lat", "lng"} or None). Read from the file; nothing is stored."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    _, lat, lng, _cam = images.exif_info(p)
    return {"exif": images.exif_full(p), "gps": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None}


def trash(token: str) -> dict:
    """Sends the file to the Recycle Bin (Windows) / Trash. Returns the token to show next (the next picture, else the previous one) or None.
    The catalog is not involved: the file was never in it. Raises OSError when the file could not be moved (it is then left where it is)."""
    from . import refmode
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    i = info(token)
    nxt = i["next"] or i["prev"]
    if not refmode.recycle(str(p)):
        raise OSError("not moved")
    with _LOCK:
        _PATH_OF.pop(token, None)
        _TOKEN_OF.pop(str(p), None)
    return {"next": nxt}


def _clamp(v, lo, hi, default=1.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def save_edit(token: str, ops: dict) -> dict:
    """Saves the picture with simple edits (brightness, contrast, saturation, black and white, rotation) as a NEW file next to it
    ("name (edited).jpg"); the original is never touched. Returns {"token", "name"} of the copy."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    rot = int(_clamp(ops.get("rotate", 0), -360, 360, 0)) // 90 * 90 % 360
    clean = {"brightness": _clamp(ops.get("brightness"), 0.2, 2.0), "contrast": _clamp(ops.get("contrast"), 0.2, 2.0),
             "saturation": _clamp(ops.get("saturation"), 0.0, 2.5), "grayscale": bool(ops.get("grayscale")), "rotate": rot}
    ext = ".png" if p.suffix.lower() == ".png" else ".jpg"
    n, dst = 1, p.with_name(f"{p.stem} (edited){ext}")
    while dst.exists():
        n += 1
        dst = p.with_name(f"{p.stem} (edited {n}){ext}")
    images.apply_edit(p, clean, dst)
    images.embed_exif(dst, p, upright=True)               # the copy keeps the camera data (JPEG only)
    return {"token": token_for(dst), "name": dst.name}


# A program started by "Open with photag" of an older photag.exe (whose launcher does not know pictures; only the `app` package is
# updated by a code update) still opens its main window: the page then asks for the picture named on the command line.
_STARTUP: list[str] = []


def _read_startup(args) -> None:
    for a in args:
        if a.startswith("-"):
            continue
        try:
            p = Path(a)
            if p.is_file() and is_picture(p):
                _STARTUP.append(str(p.resolve()))
        except OSError:
            pass


_read_startup(sys.argv[1:])


def startup_token() -> str | None:
    """The token of the picture this program was started with, once (None when there is none or it was already taken)."""
    while _STARTUP:
        try:
            return open_path(_STARTUP.pop(0))
        except NotAPicture:
            continue
    return None
