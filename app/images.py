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

VIDEO_EXT = {".mp4", ".mov", ".gif", ".3gp", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tiff"}


def sha256_file(path: Path, buf=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def open_image(path: Path) -> Image.Image:
    """Open + apply EXIF orientation so faces/thumbs aren't sideways."""
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
    import shutil, subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    try:
        subprocess.run([ff, "-y", "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={size}:-1", str(out)],
                       capture_output=True, timeout=60)
        return out if out.exists() else None
    except Exception:
        return None


def dimensions(path: Path):
    if is_video(path):
        return (None, None)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


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
