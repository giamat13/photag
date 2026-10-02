"""Thumbnails, HEIC decode, simple edits, EXIF read/write."""
import hashlib
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from .config import PATHS

VIDEO_EXT = {".mp4", ".m4v", ".mov", ".gif", ".3gp", ".webm", ".mkv", ".avi",
            ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts", ".ts", ".flv"}         # kept in sync with compress.VIDEO_OK (HandBrake-compressible)
# Camera RAW: Pillow can't decode these, but nearly every RAW file carries a
# full-size JPEG preview from the camera -> that's what we show and analyse.
RAW_EXT = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2",
           ".raf", ".pef", ".srw", ".x3f", ".3fr", ".iiq", ".rwl", ".erf", ".mos", ".kdc", ".gpr"}
# AVIF: decoded natively by this Pillow build, like HEIC -> importable and viewable, not (yet) a compress target.
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".avifs", ".webp", ".bmp", ".tiff", ".tif"} | RAW_EXT


def sha256_file(path: Path, buf=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def is_raw(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXT


def raw_preview_bytes(path: Path) -> bytes | None:
    """The largest JPEG embedded in a RAW file (the camera's full preview)."""
    import io
    data = path.read_bytes()
    best, best_px, start = None, 0, data.find(b"\xff\xd8\xff")
    while start != -1:
        end = data.find(b"\xff\xd9", start + 4)
        while end != -1:  # thumbnails nest inside previews; take the first span that decodes
            chunk = data[start:end + 2]
            if len(chunk) > 2000:
                try:
                    with Image.open(io.BytesIO(chunk)) as im:
                        im.load()
                        px = im.width * im.height
                    if px > best_px:  # by pixels: a span can decode as the small image it starts with
                        best, best_px = chunk, px
                    break
                except Exception:
                    pass
            end = data.find(b"\xff\xd9", end + 2)
            if end != -1 and end - start > 60_000_000:
                break
        start = data.find(b"\xff\xd8\xff", start + 3)
    return best


def open_image(path: Path) -> Image.Image:
    """Open + apply EXIF orientation so faces/thumbs aren't sideways."""
    if is_raw(path):
        import io
        data = raw_preview_bytes(path)
        if not data:
            raise ValueError("no preview in RAW file")
        im = Image.open(io.BytesIO(data))
    else:
        im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def thumb_path(sha: str) -> Path:
    return PATHS.thumbs / f"{sha}.jpg"


def make_thumb(src: Path, sha: str, size=512) -> Path | None:
    """Generate a cached JPEG thumbnail. Videos get a placeholder frame if
    ffmpeg isn't available -> we just skip (frontend shows a play badge)."""
    out = thumb_path(sha)
    if out.exists():
        return out
    if is_video(src):
        return _video_thumb(src, out, size)
    try:
        im = open_image(src)
        im.thumbnail((size, size))
        im.save(out, "JPEG", quality=85)
        return out
    except Exception:
        return None


def _video_thumb(src: Path, out: Path, size: int) -> Path | None:
    import subprocess
    from . import ffmpeg
    ff = ffmpeg.exe()
    if not ff:
        return None
    try:
        for seek in (["-ss", "1"], []):          # a frame a second in (the very first is often black), else the first
            subprocess.run([ff, "-y", *seek, "-i", str(src), "-frames:v", "1", "-vf", f"scale={size}:-2", str(out)],
                           capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.exists() and out.stat().st_size:
                return out
        return None
    except Exception:
        return None


def dimensions(path: Path):
    if is_video(path):
        return (None, None)
    if is_raw(path):
        try:
            return open_image(path).size
        except Exception:
            return (None, None)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


def _clean_str(v) -> str | None:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    v = str(v).strip(" \x00") if v is not None else None
    return v or None


def _rational(v) -> float | None:
    """A Pillow EXIF rational (a plain number, or something with .numerator/.denominator) as a float."""
    try:
        if hasattr(v, "numerator"):
            return float(v.numerator) / float(v.denominator or 1)
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def exif_info(path: Path):
    """(taken_at unix seconds | None, lat | None, lng | None, camera dict) from a photo's EXIF.
    The capture time is treated as UTC, the same way Takeout timestamps are shown.
    `camera` (used for advanced search / smart collections) has make, model, lens, focal_length (real mm, rounded to
    1 decimal), focal_length_35mm (int, the 35mm-equivalent some cameras/phones write) -- any of them can be None."""
    taken = lat = lng = None
    cam = {"make": None, "model": None, "lens": None, "focal_length": None, "focal_length_35mm": None}
    if is_video(path):
        return taken, lat, lng, cam
    try:
        import calendar, datetime
        with Image.open(path) as im:
            ex = im.getexif()
            sub = ex.get_ifd(0x8769)  # Exif IFD
            raw = sub.get(36867) or sub.get(36868) or ex.get(306)  # DateTimeOriginal / Digitized / DateTime
            if isinstance(raw, bytes):
                raw = raw.decode("ascii", "ignore")
            if raw:
                dt = datetime.datetime.strptime(str(raw).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
                taken = calendar.timegm(dt.timetuple())
            gps = ex.get_ifd(0x8825)
            if gps and 2 in gps and 4 in gps:
                dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
                lat = dms(gps[2]) * (-1 if gps.get(1) in ("S", b"S") else 1)
                lng = dms(gps[4]) * (-1 if gps.get(3) in ("W", b"W") else 1)
                if lat == 0 and lng == 0:
                    lat = lng = None
            cam["make"] = _clean_str(ex.get(271))                          # Make
            cam["model"] = _clean_str(ex.get(272))                         # Model
            cam["lens"] = _clean_str(sub.get(42036)) or _clean_str(sub.get(42035))  # LensModel / LensMake+Model fallback
            fl = _rational(sub.get(37386))                                 # FocalLength (mm)
            cam["focal_length"] = round(fl, 1) if fl else None
            fl35 = sub.get(41989)                                          # FocalLengthIn35mmFilm
            cam["focal_length_35mm"] = int(fl35) if fl35 else None
    except Exception:
        pass
    return taken, lat, lng, cam


def small_preview(path: Path, size=256) -> bytes | None:
    """Quick JPEG preview of a file that isn't in the library yet (import dialog)."""
    import io
    try:
        im = Image.open(path)
        im.draft("RGB", (size, size))  # JPEG: decode at reduced scale, much faster
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return None


def rotate_file(path: Path, degrees: int):
    """Rotate a photo in place by a multiple of 90° (positive = clockwise)."""
    im = open_image(path)
    im = im.rotate(-degrees, expand=True)
    fmt = "JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    im.save(path, fmt, quality=95)


def export_resized(src: Path, dst: Path, long_edge: int | None, quality: int):
    im = open_image(src)
    if long_edge:
        im.thumbnail((long_edge, long_edge), Image.LANCZOS)
    im.save(dst, "JPEG", quality=quality)


# ---------- simple editing ----------
def apply_edit(src: Path, ops: dict, dst: Path):
    """ops: rotate(deg), crop[x1,y1,x2,y2 fractions], brightness, contrast,
    saturation, grayscale(bool). Saves to dst. Caller keeps a backup."""
    im = open_image(src)
    if ops.get("rotate"):
        im = im.rotate(-float(ops["rotate"]), expand=True)
    c = ops.get("crop")
    if c and len(c) == 4:
        w, h = im.size
        box = (int(c[0] * w), int(c[1] * h), int(c[2] * w), int(c[3] * h))
        im = im.crop(box)
    for key, enh in (("brightness", ImageEnhance.Brightness),
                     ("contrast", ImageEnhance.Contrast),
                     ("saturation", ImageEnhance.Color)):
        v = ops.get(key)
        if v is not None and float(v) != 1.0:
            im = enh(im).enhance(float(v))
    if ops.get("grayscale"):
        im = ImageOps.grayscale(im).convert("RGB")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fmt = "JPEG" if dst.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    im.save(dst, fmt, quality=92)
    return dst


# ---------- EXIF write-back (JPEG only; DB is always source of truth) ----------
def write_exif_jpeg(path: Path, description=None, taken_at=None, lat=None, lng=None):
    if path.suffix.lower() not in (".jpg", ".jpeg"):
        return False
    try:
        import piexif, datetime
        exif = piexif.load(str(path))
        if description is not None:
            exif["0th"][piexif.ImageIFD.ImageDescription] = description.encode("utf-8", "replace")
        if taken_at:
            dt = datetime.datetime.utcfromtimestamp(int(taken_at)).strftime("%Y:%m:%d %H:%M:%S")
            exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt.encode()
        if lat is not None and lng is not None:
            exif["GPS"] = _gps_ifd(lat, lng)
        piexif.insert(piexif.dump(exif), str(path))
        return True
    except Exception:
        return False


def _gps_ifd(lat, lng):
    import piexif
    def deg(v):
        v = abs(v); d = int(v); m = int((v - d) * 60); s = round((v - d - m / 60) * 3600 * 100)
        return ((d, 1), (m, 1), (s, 100))
    return {
        piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude: deg(lat),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lng >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude: deg(lng),
    }
