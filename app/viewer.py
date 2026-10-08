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

from . import config, images

WEB_EXT = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".avif"}       # the window shows these as they are
CONVERT_EXT = {".heic", ".heif", ".avifs", ".tif", ".tiff"} | images.RAW_EXT                    # shown as a JPEG made from the file
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif"}
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".mpg", ".mpeg", ".3gp"}       # (not .ts / .flv: too many other files use them)
VIDEO_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
              ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".3gp": "video/3gpp"}
SUPPORTED = WEB_EXT | CONVERT_EXT | VIDEO_EXT
MAX_TOKENS = 20000


class NotAPicture(Exception):
    pass


_LOCK = threading.Lock()
_PATH_OF: dict[str, Path] = {}
_TOKEN_OF: dict[str, str] = {}


def is_picture(path: Path) -> bool:
    """A picture or a video photag can show in the viewer."""
    return path.suffix.lower() in SUPPORTED


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


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
    w, h = (None, None) if is_video(p) else images.dimensions(p)

    def tok(i):
        return token_for(sib[i]) if 0 <= i < len(sib) else None

    return {"token": token, "name": p.name, "folder": str(p.parent), "bytes": st.st_size, "modified": int(st.st_mtime),
            "width": w, "height": h, "index": idx + 1, "count": len(sib),
            "prev": tok(idx - 1) if idx > 0 else None, "next": tok(idx + 1) if idx + 1 < len(sib) else None,
            "first": tok(0), "last": tok(len(sib) - 1), "converted": p.suffix.lower() in CONVERT_EXT,
            "video": is_video(p), "can_wallpaper": sys.platform == "win32" and not is_video(p)}


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
    if is_video(p):
        return p, VIDEO_MIME.get(p.suffix.lower(), "application/octet-stream")
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
    """Saves the picture with the edits of the viewer's editor (see clean_ops) as a NEW file next to it ("name (edited).jpg");
    the original is never touched. Returns {"token", "name"} of the copy."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if is_video(p):
        raise NotAPicture(str(p))
    clean = clean_ops(ops)
    ext = ".png" if p.suffix.lower() == ".png" else ".jpg"
    n, dst = 1, p.with_name(f"{p.stem} (edited){ext}")
    while dst.exists():
        n += 1
        dst = p.with_name(f"{p.stem} (edited {n}){ext}")
    images.apply_edit(p, clean, dst)
    images.embed_exif(dst, p, upright=True)               # the copy keeps the camera data (JPEG only)
    return {"token": token_for(dst), "name": dst.name}


# ---- the strip of thumbnails, the clipboard / wallpaper helpers, the editor ----
def listing(token: str, limit: int = 3000) -> dict:
    """Every picture of the folder (token, name, video?) for the strip of thumbnails; the window around the current one when there are very many."""
    p = path_of(token)
    sib = siblings(p)
    try:
        i = sib.index(p)
    except ValueError:
        i = 0
    lo = max(0, min(i - limit // 2, len(sib) - limit)) if len(sib) > limit else 0
    part = sib[lo:lo + limit]
    return {"index": i - lo, "total": len(sib), "items": [{"token": token_for(q), "name": q.name, "video": is_video(q)} for q in part]}


def thumb_file(token: str, size: int = 200) -> Path | None:
    """A small JPEG of the picture (cached in the temp folder); None for a video (the page draws an icon)."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if is_video(p):
        return None
    st = p.stat()
    out = _cache_dir() / ("t-" + hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}|{size}".encode()).hexdigest()[:22] + ".jpg")
    if not out.exists():
        im = images.open_image(p)
        im.thumbnail((size, size))
        im.convert("RGB").save(out, "JPEG", quality=75)
        _prune(out.parent, 600)
    return out


def _set_wallpaper(path: str) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3))       # SPI_SETDESKWALLPAPER, update the profile and tell everyone


def wallpaper(token: str) -> bool:
    """Makes the picture the desktop background (Windows). A picture a browser cannot show is converted first, into photag's own folder."""
    p = path_of(token)
    if sys.platform != "win32" or is_video(p):
        raise OSError("not supported")
    if not p.is_file():
        raise FileNotFoundError(str(p))
    src, _ = image_file(token)
    if src != p:                                             # converted copy (HEIC, TIFF, RAW): keep it, the temp folder is pruned
        keep = config.settings_dir() / "wallpaper.jpg"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_bytes(src.read_bytes())
        src = keep
    return _set_wallpaper(str(src))


TONE_RANGES = {"exposure": (-4.0, 4.0), "highlights": (-100, 100), "shadows": (-100, 100), "temperature": (-100, 100), "tint": (-100, 100),
               "vibrance": (-100, 100), "sharpness": (0, 100), "vignette": (-100, 100), "clarity": (-100, 100)}


def clean_ops(ops: dict) -> dict:
    """The edit settings of the viewer's editor, every number kept inside its range (nothing the page sends is trusted)."""
    out = {}
    for k, (lo, hi) in TONE_RANGES.items():
        v = ops.get(k)
        v = _clamp(v, lo, hi, 0.0) if v not in (None, 0, 0.0) else 0.0
        if v:
            out[k] = v
    for k, lo, hi in (("brightness", 0.2, 2.0), ("contrast", 0.2, 2.0), ("saturation", 0.0, 2.5)):
        v = _clamp(ops.get(k), lo, hi)
        if abs(v - 1.0) > 1e-6:
            out[k] = v
    if ops.get("grayscale"):
        out["grayscale"] = True
    rot = _clamp(ops.get("rotate", 0), -360, 360, 0.0)
    if abs(rot) > 1e-6:
        out["rotate"] = round(rot, 2)
    c = ops.get("crop")
    if isinstance(c, (list, tuple)) and len(c) == 4:
        x1, y1, x2, y2 = (_clamp(v, 0.0, 1.0, 0.0) for v in c)
        if x2 - x1 >= 0.01 and y2 - y1 >= 0.01 and (x1, y1, x2, y2) != (0.0, 0.0, 1.0, 1.0):
            out["crop"] = [x1, y1, x2, y2]
    return out


def tone_preview(token: str, ops: dict) -> bytes:
    """The picture with only the tone settings applied (what CSS cannot draw), at most 1600 px: the live preview of the sliders."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return images.preview_tone(p, clean_ops(ops), 1600)


def auto_settings(token: str) -> dict:
    """What "improve automatically" would set for this picture (the same algorithm as the Develop module)."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return images.auto_ops(p)


class NameNotAllowed(Exception):
    pass


class AlreadyExists(Exception):
    pass


class NoFolder(Exception):
    pass


_BAD_CHARS = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def clean_name(name: str, old_suffix: str) -> str:
    """The new file name for a rename: no path, no characters Windows refuses, the extension of the file kept (typing a bare name is fine)."""
    n = (name or "").strip().rstrip(". ")
    if not n or any(c in _BAD_CHARS or ord(c) < 32 for c in n) or n in (".", "..") or Path(n).stem.upper() in _RESERVED or len(n) > 200:
        raise NameNotAllowed(name)
    suf = Path(n).suffix.lower()
    if suf in SUPPORTED and suf != old_suffix.lower():
        raise NameNotAllowed(name)                          # a rename does not convert the picture to another format
    if suf != old_suffix.lower():
        n += old_suffix
    return n


def _forget(token: str, path: Path):
    with _LOCK:
        _PATH_OF.pop(token, None)
        _TOKEN_OF.pop(str(path), None)


def rename(token: str, name: str) -> dict:
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    new = p.with_name(clean_name(name, p.suffix))
    if new.name != p.name:                          # Path equality ignores case on Windows: compare the spelling
        if new.exists() and not (new.name.lower() == p.name.lower() and os.path.samefile(new, p)):
            raise AlreadyExists(new.name)
        os.rename(p, new)
        _forget(token, p)
    return {"token": token_for(new), "name": new.name}


def _free_name(folder: Path, name: str) -> Path:
    """folder/name, or folder/name (2).ext ... when that exists: a copy never replaces a file."""
    out = folder / name
    n = 1
    while out.exists():
        n += 1
        out = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
    return out


def _dest(folder: str) -> Path:
    d = Path(folder or "")
    if not folder or not d.is_dir():
        raise NoFolder(folder)
    return d


def copy_to(token: str, folder: str) -> dict:
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    import shutil
    out = _free_name(_dest(folder), p.name)
    shutil.copy2(p, out)
    return {"name": out.name, "folder": str(out.parent)}


def move_to(token: str, folder: str) -> dict:
    """Moves the file to another folder (never over a file there). Returns the token to show next, like trash()."""
    p = path_of(token)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    import shutil
    d = _dest(folder)
    i = info(token)
    nxt = i["next"] or i["prev"]
    out = _free_name(d, p.name) if d.resolve() != p.parent.resolve() else p
    if out != p:
        shutil.move(str(p), str(out))
        _forget(token, p)
    return {"next": nxt if out != p else token, "name": out.name, "folder": str(d)}


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
STARTED_WITH_PICTURE = bool(_STARTUP)          # this program was started by "Open with" on a picture: a viewer, not the whole program


# Pictures handed over by a photag started meanwhile by "Open with" of an older photag.exe, while this one was already running (since
# 13.0.0 photag keeps running in the background): that launcher only opens a window on this program's main page, which then asks
# for them here. (path, time); forgotten after HANDOFF_TTL seconds, and dropped when the new program opened the viewer itself.
_HANDOFF: list[tuple[str, float]] = []
HANDOFF_TTL = 30.0
HANDED_OFF = False                            # this (new) program gave its picture to the photag that was already running


def take_handoff(paths: list[str]) -> int:
    now = time.time()
    n = 0
    for x in paths[:20]:
        try:
            p = Path(x)
            if p.is_file() and is_picture(p):
                _HANDOFF.append((str(p.resolve()), now)); n += 1
        except OSError:
            pass
    return n


def drop_handoff(path: str) -> None:
    """The new program opened that picture in its own viewer window (a launcher that knows pictures): the main page must not show it again."""
    try:
        key = str(Path(path).resolve())
    except OSError:
        return
    _HANDOFF[:] = [h for h in _HANDOFF if h[0] != key]


def _hand_to_running() -> bool:
    """At start-up, when this program was started with a picture and another photag is already answering: give it the picture."""
    import json
    import urllib.request
    base = int(os.environ.get("PHOTAG_BASE_PORT", 8756))
    body = json.dumps({"paths": _STARTUP}).encode()
    socket = __import__("socket")                 # not an `import`: built into Python (runtime.lock tracks import names)
    for port in range(base, base + 50):
        url = f"http://127.0.0.1:{port}"
        try:                                   # a quick look first (on Windows a refused connection takes ~2 s); a free port means
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()       # no photag after it (it would have taken that one)
        except OSError:
            return False
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=3) as r:
                st = json.loads(r.read())
            if not isinstance(st, dict) or "library_root" not in st:
                continue
            req = urllib.request.Request(url + "/api/viewer/handoff", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                return bool(json.loads(r.read()).get("taken"))
        except Exception:
            continue
    return False


if STARTED_WITH_PICTURE and not os.environ.get("PHOTAG_NO_HANDOFF"):
    try:
        HANDED_OFF = _hand_to_running()
    except Exception:
        HANDED_OFF = False


def startup_token() -> str | None:
    """The token of the picture this program was started with -- or one handed over by a photag started meanwhile -- once (None
    when there is none or it was already taken)."""
    while _STARTUP and not HANDED_OFF:
        try:
            return open_path(_STARTUP.pop(0))
        except NotAPicture:
            continue
    now = time.time()
    while _HANDOFF:
        path, at = _HANDOFF.pop(0)
        if now - at > HANDOFF_TTL:
            continue
        try:
            return open_path(path)
        except NotAPicture:
            continue
    return None
