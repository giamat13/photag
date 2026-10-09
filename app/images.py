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


_NAME_DATE = None


def date_from_filename(name: str) -> int | None:
    """Unix seconds (wall-clock-as-UTC, like exif_info) from a date in a file name such as IMG-20240501-WA0003.jpg,
    PXL_20240501_101530123.jpg, 2024-05-01 10.15.30.png or Screenshot_2024-05-01-10-15-30.png; None when there is none.
    Only years 1990..current+1 are believed, so a long number (a phone number, a counter) is not read as a date."""
    global _NAME_DATE
    import calendar, datetime, re
    if _NAME_DATE is None:
        _NAME_DATE = re.compile(r"(?<!\d)(19[89]\d|20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
                                r"(?:[-_. T]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?(?!\d{3})")
    m = _NAME_DATE.search(Path(name).stem)
    if not m:
        return None
    y, mo, d, hh, mi, ss = (int(g) if g else 0 for g in m.groups())
    if y < 1990 or y > datetime.date.today().year + 1:
        return None
    try:
        return calendar.timegm(datetime.datetime(y, mo, d, hh, mi, ss).timetuple())
    except ValueError:
        return None


_EXIF_BLOB_MAX = 64         # bytes longer than this (MakerNote, thumbnails, ICC...) are noted by size, not stored


def _exif_value(v, depth=0):
    """One EXIF value as something json.dumps accepts and a person can read."""
    if isinstance(v, bytes):
        if len(v) > _EXIF_BLOB_MAX:
            return f"<{len(v)} bytes>"
        txt = v.decode("ascii", "ignore").strip("\x00 ")
        return txt if txt and txt.isprintable() else v.hex()
    if isinstance(v, (tuple, list)):
        return [_exif_value(x, depth + 1) for x in v][:64] if depth < 3 else str(v)
    if isinstance(v, dict):
        return {str(k): _exif_value(x, depth + 1) for k, x in v.items()} if depth < 3 else str(v)
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    try:
        f = float(v)                         # PIL's IFDRational / Fraction
        if f != f or f in (float("inf"), float("-inf")):      # 1/0 in a damaged file: NaN/Infinity are not valid JSON
            return str(v)
        return int(f) if f == int(f) and abs(f) < 1e15 else round(f, 6)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return str(v)


def exif_full(path: Path) -> dict:
    """EVERY EXIF tag of a photo as {"Image": {...}, "Exif": {...}, "GPS": {...}} with readable tag names
    (unknown tags as "0xNNNN"). {} for videos, files without EXIF and files that cannot be read -- never raises."""
    from PIL import ExifTags
    if is_video(path):
        return {}
    out: dict = {}
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            groups = (("Image", None, None), ("Exif", 0x8769, None), ("GPS", 0x8825, ExifTags.GPSTAGS), ("Interop", 0xA005, None))
            for name, pointer, names in groups:
                try:
                    ifd = ex if pointer is None else ex.get_ifd(pointer)        # a group the file doesn't have raises KeyError
                except Exception:
                    continue
                d = {}
                for tag, val in dict(ifd).items():
                    if name == "Image" and tag in (0x8769, 0x8825, 0xA005):      # the pointers to the other groups
                        continue
                    label = (names or ExifTags.TAGS).get(tag) or f"0x{tag:04X}"
                    d[label] = _exif_value(val)
                if d:
                    out[name] = d
    except Exception:
        return {}
    return out


def exif_json_text(path: Path) -> str:
    """exif_full() as the text stored in photos.exif_json. "{}" means "looked, nothing there" (NULL = not looked yet)."""
    import json
    return json.dumps(exif_full(path), ensure_ascii=False, separators=(",", ":"))


def store_exif(con, photo_id: int, path: Path):
    """Reads the EXIF of `path` into photos.exif_json for that photo (a failure leaves it NULL, to be retried)."""
    try:
        con.execute("UPDATE photos SET exif_json=? WHERE id=?", (exif_json_text(path), photo_id))
    except Exception:
        pass


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
# Develop settings that change the picture itself ("stage 1", done on the full frame before rotating/cropping) -- the
# rest (rotate, crop, flips, brightness, contrast, saturation, grayscale) is geometry and simple tone ("stage 2").
TONE_KEYS = ("exposure", "highlights", "shadows", "temperature", "tint", "vibrance", "clarity", "sharpness",
             "blur", "vignette", "sepia",
             # the extra tools (develop_ops.py)
             "whites", "blacks", "texture", "dehaze", "grain", "grain_size", "grain_rough",
             "vignette_mid", "vignette_feather", "vignette_round", "vignette_hl",
             "sharp_radius", "sharp_detail", "sharp_mask", "nr_lum", "nr_color", "nr_detail",
             "curve", "curve_p", "mixer", "bwmix", "grading", "calib",
             # places and shapes (develop_local.py)
             "lens_dist", "lens_vig", "ca_r", "ca_b", "ca_auto", "defringe", "persp_v", "persp_h", "geo_aspect", "geo_scale", "geo_x", "geo_y",
             "spots", "redeye", "lut", "masks")
FLIP_KEYS = ("flip_h", "flip_v")


_PLAIN_TONE = ("exposure", "highlights", "shadows", "temperature", "tint", "vibrance", "clarity", "sharpness", "blur", "vignette", "sepia")


def has_tone(ops: dict) -> bool:
    from . import develop_ops
    return any(ops.get(k) not in (None, 0, 0.0) for k in _PLAIN_TONE) or develop_ops.active(ops)


def _smooth(x, a, b):
    import numpy as np
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _unit(im):
    import numpy as np
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


def _from_unit(a):
    import numpy as np
    return Image.fromarray((np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")


def _opt(ops: dict, key: str, default: float) -> float:
    v = ops.get(key)
    return default if v is None else float(v)


_LOCAL_MAP = {"temp": "temperature", "tint": "tint", "exposure": "exposure", "highlights": "highlights", "shadows": "shadows", "whites": "whites",
              "blacks": "blacks", "texture": "texture", "clarity": "clarity", "dehaze": "dehaze"}


def _local_adjust(a, adj: dict, size: int):
    """The picture (float array) with the adjustments of one local mask applied to all of it; the mask decides where they show."""
    import numpy as np
    from . import develop_ops
    ops = {_LOCAL_MAP[k]: v for k, v in adj.items() if k in _LOCAL_MAP and v}
    sh, nz = float(adj.get("sharpness") or 0), float(adj.get("noise") or 0)
    if sh > 0:
        ops["sharpness"] = sh
    elif sh < 0:
        ops["blur"] = -sh * 0.4
    if nz > 0:
        ops["nr_lum"] = nz
    out = _unit(apply_tone(_from_unit(a), ops)) if ops else a
    con, sat, hue = float(adj.get("contrast") or 0), float(adj.get("saturation") or 0), float(adj.get("hue") or 0)
    if con:
        out = (out - 0.5) * (1 + con / 100.0 * 0.8) + 0.5
    if sat:
        gray = out.mean(axis=2, keepdims=True)
        out = gray + (out - gray) * (1 + sat / 100.0)
    if hue:
        h, s, v = develop_ops.rgb_to_hsv(np.clip(out, 0, 1))
        out = develop_ops.hsv_to_rgb(h + hue / 100.0 * 60.0, s, v)
    return out


def apply_tone(im, ops: dict):
    """Stage 1: exposure (EV), highlights, shadows, temperature, tint, vibrance, clarity, sharpness, blur, vignette,
    sepia. All amounts are -100..100 (0..100 where only one direction exists) except exposure, which is in stops.
    Radii scale with the picture size, so a small preview looks like the full-size render."""
    import numpy as np
    from PIL import ImageFilter
    from . import develop_ops, develop_local
    if not has_tone(ops):
        return im
    im = im.convert("RGB")
    size = max(im.size)
    g = lambda k: float(ops.get(k) or 0)
    im = develop_local.optics(im, ops)                             # lens distortion and chromatic aberration come first
    if ops.get("spots") or ops.get("redeye"):
        a0 = _unit(im)
        if ops.get("spots"):
            a0 = develop_local.spots(a0, ops["spots"])
        if ops.get("redeye"):
            a0 = develop_local.redeye(a0, ops["redeye"])
        im = _from_unit(a0)
    if g("nr_lum") or g("nr_color"):                               # noise reduction comes first: sharpening must not make the noise crisp
        im = develop_ops.denoise(im, g("nr_lum"), g("nr_color"), _opt(ops, "nr_detail", 50.0), size)
    if g("blur"):
        im = im.filter(ImageFilter.GaussianBlur(radius=g("blur") / 100.0 * 0.012 * size))
    if g("clarity"):
        im = im.filter(ImageFilter.UnsharpMask(radius=max(2.0, size / 60.0), percent=int(g("clarity") * 1.2), threshold=0))
    if g("sharpness"):
        im = develop_ops.sharpen(im, g("sharpness"), size, _opt(ops, "sharp_radius", 50.0), _opt(ops, "sharp_detail", 25.0), g("sharp_mask"))
    a = _unit(im)
    if g("lens_vig"):
        a = develop_local.vignette_gain(a, g("lens_vig"))
    if g("defringe"):
        a = develop_local.defringe(a, g("defringe"))
    if g("exposure"):                                              # gain in linear light
        lin = np.power(a, 2.2) * (2.0 ** g("exposure"))
        a = np.power(np.clip(lin, 0.0, 1.0), 1 / 2.2)
    if g("highlights") or g("shadows"):
        lum = a.mean(axis=2, keepdims=True)
        a = a + (g("highlights") / 100.0) * 0.35 * _smooth(lum, 0.5, 1.0) \
              + (g("shadows") / 100.0) * 0.35 * (1.0 - _smooth(lum, 0.0, 0.5))
    if g("temperature") or g("tint"):
        t, ti = g("temperature") / 100.0, g("tint") / 100.0
        a = a * np.array([1 + 0.3 * t, 1 - 0.2 * ti, 1 - 0.3 * t], dtype=np.float32)
    if g("vibrance"):
        mx, mn = a.max(axis=2, keepdims=True), a.min(axis=2, keepdims=True)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
        v = g("vibrance") / 100.0
        f = 1 + v * (1 - sat) if v > 0 else 1 + v * (0.5 + 0.5 * sat)
        gray = a.mean(axis=2, keepdims=True)
        a = gray + (a - gray) * f
    a = develop_ops.apply(a, ops, size)                            # texture, dehaze, whites / blacks, curves, colour mixer, grading...
    if ops.get("lut"):
        a = develop_local.lut(a, ops["lut"])
    if g("vignette"):
        a = develop_ops.vignette(a, g("vignette"), float(ops.get("vignette_mid", 50) if ops.get("vignette_mid") is not None else 50),
                                 float(ops.get("vignette_feather", 50) if ops.get("vignette_feather") is not None else 50),
                                 g("vignette_round"), g("vignette_hl"))
    if g("sepia"):
        k = np.array([[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]], dtype=np.float32)
        a = a + (np.clip(a @ k.T, 0.0, 1.0) - a) * (g("sepia") / 100.0)
    if ops.get("masks"):
        a = develop_local.apply_masks(a, ops["masks"], lambda arr, adj: _local_adjust(arr, adj, size))
    if g("grain"):
        a = develop_ops.grain(a, g("grain"), float(ops.get("grain_size", 25) if ops.get("grain_size") is not None else 25),
                              float(ops.get("grain_rough", 50) if ops.get("grain_rough") is not None else 50))
    return develop_local.geometry(_from_unit(a), ops)                # perspective / aspect / scale / offset last, so everything above lines up


def apply_edit(src: Path, ops: dict, dst: Path):
    """ops: tone settings (see TONE_KEYS), rotate(deg), crop[x1,y1,x2,y2 fractions], brightness, contrast,
    saturation, grayscale(bool), flip_h, flip_v. Saves to dst. The source file is never touched."""
    im = apply_tone(open_image(src), ops)
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
    if ops.get("flip_h"):
        im = ImageOps.mirror(im)
    if ops.get("flip_v"):
        im = ImageOps.flip(im)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fmt = "JPEG" if dst.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    im.save(dst, fmt, quality=92)
    return dst


def preview_tone(src: Path, ops: dict, dst_max: int = 1600) -> bytes:
    """JPEG of the original (shrunk to dst_max) with only the tone settings applied -- the live preview of the
    Develop sliders that CSS filters cannot show. Geometry and the simple tone sliders are drawn by the page."""
    import io
    im = open_image(src)
    im.thumbnail((dst_max, dst_max))
    out = io.BytesIO()
    apply_tone(im, ops).save(out, "JPEG", quality=88)
    return out.getvalue()


def auto_ops(src: Path) -> dict:
    """What this picture needs, worked out from the picture itself ("Auto"): exposure so the average brightness lands
    near the middle, contrast from how much of the tonal range is used, highlights/shadows pulled back when they clip,
    white balance from the grey-world average, vibrance when colours are dull, a touch of sharpness. Returns develop
    settings in the same units apply_edit() takes (everything stays moderate: Auto improves, it never ruins)."""
    import numpy as np
    im = open_image(src)
    im.thumbnail((640, 640))
    a = _unit(im)
    lum = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2])
    mean = float(lum.mean())
    p1, p99 = float(np.percentile(lum, 1)), float(np.percentile(lum, 99))
    clip_hi = float((lum > 0.97).mean())
    clip_lo = float((lum < 0.03).mean())
    clamp = lambda v, lo, hi: max(lo, min(hi, v))
    out = {}
    # exposure: move the mean towards 0.45 (in linear light, limited to +-1.5 stops, only 80% of the way)
    target = 0.45
    out["exposure"] = 0.0                                          # (an all-black picture has nothing to recover)
    if mean > 0.001:
        ev = float(np.log2(max(target, 1e-3) ** 2.2 / max(mean, 1e-3) ** 2.2)) * 0.8
        out["exposure"] = round(clamp(ev, -1.5, 1.5), 2)
    # highlights / shadows: only what is actually blown out or crushed
    out["highlights"] = -round(clamp(clip_hi * 600, 0, 60))
    out["shadows"] = round(clamp(clip_lo * 600 + max(0.0, 0.25 - float(np.percentile(lum, 10))) * 150, 0, 60))
    # contrast: a narrow tonal range needs more, a very wide one less
    spread = p99 - p1
    out["contrast"] = round(1 + clamp((0.8 - spread) * 70, -15, 30) / 100, 3)       # a factor, like apply_edit takes
    # white balance (grey world, limited): warm up a bluish cast, cool down a yellow one; green/magenta likewise
    r, g, b = (float(a[..., i].mean()) for i in range(3))
    gray = (r + g + b) / 3 or 1e-3
    out["temperature"] = round(clamp((b - r) / gray * 120, -40, 40))
    out["tint"] = round(clamp((g - (r + b) / 2) / gray * 120, -30, 30))
    # colour: dull pictures get vibrance
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0).mean())
    out["vibrance"] = round(clamp((0.38 - sat) * 160, -10, 40))
    out["sharpness"] = 20
    return out


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


def embed_exif(dst: Path, src: Path | None, *, description=None, taken_at=None, lat=None, lng=None, rating=None,
               keywords=(), upright=False) -> bool:
    """Writes EXIF into the JPEG `dst` (an EXPORTED copy, never a library file): all the tags of `src` (the original photo --
    an edited render and a re-encoded copy have none of their own), then what the catalog knows on top: caption, capture
    time, GPS, star rating and keywords. `upright` says dst's pixels are already turned the right way up (a render or a
    re-encode), so Orientation becomes 1 -- otherwise a viewer would turn the picture a second time -- and the stored
    pixel size is corrected; the camera's embedded thumbnail is dropped (it would show the unedited picture).
    False if dst is not a JPEG or nothing could be written; a tag that cannot be carried over never stops the rest."""
    if dst.suffix.lower() not in (".jpg", ".jpeg"):
        return False
    import datetime
    import piexif
    empty = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "Interop": {}, "thumbnail": None}
    exif = dict(empty)
    if src is not None:
        try:
            exif = piexif.load(str(src))
        except Exception:
            exif = dict(empty)                                         # no EXIF in the source (PNG, HEIC, damaged): the catalog's values only

    def fill(e):
        e["thumbnail"] = None
        e["1st"] = {}
        if upright:
            e["0th"][piexif.ImageIFD.Orientation] = 1
            try:
                w, h = dimensions(dst)
                e["Exif"][piexif.ExifIFD.PixelXDimension] = int(w)
                e["Exif"][piexif.ExifIFD.PixelYDimension] = int(h)
            except Exception:
                pass
        if description:
            e["0th"][piexif.ImageIFD.ImageDescription] = str(description).encode("utf-8", "replace")
        if taken_at:
            dt = datetime.datetime.utcfromtimestamp(int(taken_at)).strftime("%Y:%m:%d %H:%M:%S").encode()
            e["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt
            e["Exif"][piexif.ExifIFD.DateTimeDigitized] = dt
        if lat is not None and lng is not None:
            e["GPS"] = _gps_ifd(lat, lng)
        if rating:
            e["0th"][piexif.ImageIFD.Rating] = int(rating)
        kw = [str(k) for k in keywords if k]
        if kw:
            e["0th"][piexif.ImageIFD.XPKeywords] = (";".join(kw) + "\x00").encode("utf-16le")
        return e

    for candidate in (exif, dict(empty)):                              # a tag piexif cannot write: retry with the catalog's values alone
        try:
            piexif.insert(piexif.dump(fill(candidate)), str(dst))
            return True
        except Exception:
            continue
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
