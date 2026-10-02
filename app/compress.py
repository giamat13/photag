"""Compress videos (HandBrake) and photos (Pillow), then prove the result is good before touching the original.

Flow: probe the source -> HandBrakeCLI -> probe the output -> verify -> only if every check passes,
back up the old file and swap the new one in. Any failure leaves the library file exactly as it was.

Verification (thresholds come from measurements on real HandBrake output, see docs/project-history.md):
  * frame count  : identical to the source (default: no frame is dropped); with a frame-rate cap the
                   count must match what that cap implies
  * length       : container length and decoded video length within DURATION_TOL of the source
                   (measured deviation 0-20 ms: AAC encoder delay and container rounding)
  * resolution   : unchanged, or capped as requested with the aspect ratio kept
  * audio        : still present when it should be
  * size         : actually smaller
  * SSIM         : mean and worst frame above a floor that only catches corrupt / misaligned output;
                   the measured value is reported to the user as a quality grade
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import backup, config, db, ffmpeg, images, importer, refmode
from .config import PATHS

HANDBRAKE_PAGE = "https://handbrake.fr/downloads2.php"      # the "Command Line Version" lives on this page
DURATION_TOL = 0.050        # seconds
SSIM_MEAN_MIN = 0.80
SSIM_FRAME_MIN = 0.60
SSIM_GRADES = [(0.97, "excellent"), (0.93, "very_good"), (0.88, "good"), (0.0, "noticeable")]
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

_X26X = ["veryslow", "slower", "slow", "medium", "fast", "faster", "veryfast", "superfast", "ultrafast"]
# rf = [best quality, strongest compression, default]; speed_stops = the 7 positions of the "speed" slider, slow -> fast
ENCODERS = {
    "x264": {"label": "H.264 (x264)", "rf": [14, 32, 21], "presets": _X26X,
             "speed_stops": _X26X[:7], "default_preset": "medium"},
    "x265": {"label": "H.265 (x265)", "rf": [16, 34, 23], "presets": _X26X,
             "speed_stops": _X26X[:7], "default_preset": "medium"},
    "svt_av1": {"label": "AV1 (SVT-AV1)", "rf": [20, 40, 28], "presets": [str(i) for i in range(-1, 11)],
                "speed_stops": ["2", "4", "5", "6", "7", "8", "10"], "default_preset": "6"},
}
DEFAULTS = {"encoder": "x264", "quality": None, "preset": None, "max_height": 0,
            "fps_mode": "same",          # same = keep every frame (default) | limit = cap at fps (drops) | constant
            "fps": 30.0, "audio": "auto", "audio_bitrate": 160}
# every filter HandBrake's default preset may switch on: all off, so no frame or pixel is altered besides compression
FILTERS_OFF = ["--no-comb-detect", "--no-deinterlace", "--no-decomb", "--no-detelecine", "--no-hqdn3d", "--no-nlmeans",
               "--no-chroma-smooth", "--no-unsharp", "--no-lapsharp", "--no-deblock"]
VIDEO_OK = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".3gp", ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts", ".ts", ".flv"}


# ------------------------------------------------------------------ HandBrakeCLI discovery
def _candidates():
    yield config.get_handbrake_path()
    yield os.environ.get("PHOTAG_HANDBRAKECLI")
    yield shutil.which("HandBrakeCLI")
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            yield os.path.join(base, "HandBrake", "HandBrakeCLI.exe")
            yield os.path.join(base, "Programs", "HandBrake", "HandBrakeCLI.exe")
    yield str(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "photag" / "tools" / "HandBrakeCLI.exe")


def find_handbrake() -> str | None:
    for c in _candidates():
        if c and Path(c).is_file():
            return str(c)
    return None


def handbrake_version(exe: str) -> str | None:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, creationflags=NO_WIN)
        m = re.search(r"HandBrake\s+(\d+(?:\.\d+)+)", r.stdout + r.stderr)
        return m.group(1) if m else None
    except Exception:
        return None


def status() -> dict:
    exe = find_handbrake()
    return {"found": bool(exe), "path": exe, "version": handbrake_version(exe) if exe else None,
            "page": HANDBRAKE_PAGE, "ffmpeg": ffmpeg.available(),
            "encoders": ENCODERS, "defaults": DEFAULTS, "image_defaults": IMG_DEFAULTS,
            "video_ext": sorted(VIDEO_OK), "image_ext": sorted(IMAGE_OK)}


# ------------------------------------------------------------------ options -> HandBrakeCLI arguments
def normalize(raw: dict | None) -> dict:
    o = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in DEFAULTS and v is not None}}
    if o["encoder"] not in ENCODERS:
        raise ValueError("encoder")
    enc = ENCODERS[o["encoder"]]
    lo, hi, dflt = enc["rf"]
    o["quality"] = float(max(lo, min(hi, o["quality"] if o["quality"] is not None else dflt)))
    o["preset"] = str(o["preset"]) if str(o["preset"]) in enc["presets"] else enc["default_preset"]
    o["max_height"] = max(0, int(o["max_height"] or 0))
    if o["fps_mode"] not in ("same", "limit", "constant"):
        o["fps_mode"] = "same"
    o["fps"] = float(max(1, min(240, o["fps"])))
    if o["audio"] not in ("auto", "aac", "none"):
        o["audio"] = "auto"
    o["audio_bitrate"] = int(max(32, min(512, o["audio_bitrate"])))
    return o


def build_args(exe: str, src: str, dst: str, o: dict) -> list[str]:
    a = [exe, "-i", src, "-o", dst, "-f", "av_mp4", "-e", o["encoder"], "-q", f"{o['quality']:g}",
         "--encoder-preset", o["preset"]]
    a += {"same": ["--vfr"], "limit": ["--pfr", "-r", f"{o['fps']:g}"], "constant": ["--cfr", "-r", f"{o['fps']:g}"]}[o["fps_mode"]]
    a += ["--crop-mode", "none", "--non-anamorphic", "--maxWidth", "16384",
          "--maxHeight", str(o["max_height"] or 16384)]
    if o["audio"] == "none":
        a += ["-a", "none"]
    elif o["audio"] == "aac":
        a += ["--all-audio", "-E", "av_aac", "-B", str(o["audio_bitrate"])]
    else:   # keep AAC as it is, transcode anything else
        a += ["--all-audio", "-E", "copy:aac", "--audio-fallback", "av_aac", "-B", str(o["audio_bitrate"])]
    return a + ["-O", "--keep-metadata", *FILTERS_OFF]


# ------------------------------------------------------------------ verification
def grade(mean: float) -> str:
    return next(g for floor, g in SSIM_GRADES if mean >= floor)


def expected_frames(src: dict, o: dict) -> tuple[int, int, str]:
    """(lowest, highest, text) number of frames the output may have."""
    n = src["frames"]
    if o["fps_mode"] == "same":
        return n, n, str(n)
    dur = src["video_duration"] or src["duration"] or 0
    target = round(dur * o["fps"])
    if o["fps_mode"] == "limit":
        src_fps = n / dur if dur else (src["fps"] or 0)
        if src_fps <= o["fps"] * 1.001:                 # nothing to drop
            return n, n, str(n)
        e = min(n, target)
        return int(e * 0.9), e + 2, f"≈{e}"
    return target - 2, target + 2, f"≈{target}"


def verify(src: dict, out: dict, o: dict, ssim: dict) -> list[dict]:
    checks = []
    def add(cid, ok, expected, actual, **extra):
        checks.append({"id": cid, "ok": bool(ok), "expected": expected, "actual": actual, **extra})

    lo, hi, text = expected_frames(src, o)
    add("frames", lo <= out["frames"] <= hi, text, out["frames"])

    # with a new frame rate the last frame can end up to one frame duration later than in the source
    tol = DURATION_TOL if o["fps_mode"] == "same" else DURATION_TOL + 1 / o["fps"]
    d_cont = abs((out["duration"] or 0) - (src["duration"] or 0))
    d_vid = abs((out["video_duration"] or 0) - (src["video_duration"] or 0))
    add("duration", max(d_cont, d_vid) <= tol, f"±{tol * 1000:.0f} ms", f"{max(d_cont, d_vid) * 1000:.0f} ms",
        container_ms=round(d_cont * 1000), video_ms=round(d_vid * 1000),
        src_s=round(src["duration"] or 0, 3), out_s=round(out["duration"] or 0, 3))

    w, h = src["width"], src["height"]
    if o["max_height"] and h > o["max_height"]:
        ok = (o["max_height"] - 8 <= out["height"] <= o["max_height"]
              and abs(out["width"] / out["height"] - w / h) / (w / h) < 0.01)
        want = f"≤{o['max_height']}p"
    else:
        ok, want = (out["width"], out["height"]) == (w, h), f"{w}×{h}"
    add("resolution", ok, want, f"{out['width']}×{out['height']}")

    want_audio = src["has_audio"] and o["audio"] != "none"
    add("audio", out["has_audio"] == want_audio, "yes" if want_audio else "no", "yes" if out["has_audio"] else "no")
    add("size", out["bytes"] < src["bytes"], f"<{src['bytes']}", out["bytes"])
    add("ssim", ssim["mean"] >= SSIM_MEAN_MIN and ssim["min"] >= SSIM_FRAME_MIN,
        f"≥{SSIM_MEAN_MIN:.2f}", f"{ssim['mean']:.4f}", mean=round(ssim["mean"], 4), min=round(ssim["min"], 4),
        frames=ssim["frames"], grade=grade(ssim["mean"]))
    return checks


def _measure(path: Path, cancel) -> dict:
    return {**ffmpeg.info(str(path)), **ffmpeg.count_frames(str(path), cancel), "bytes": path.stat().st_size}


# ------------------------------------------------------------------ backups and swapping the file in
def _unique(p: Path) -> Path:
    i = 1
    while p.exists():
        p = p.with_name(f"{p.stem} ({i}){p.suffix}"); i += 1
    return p


class DuplicateError(Exception):
    """The new file is byte-identical to another photo already in the library."""


def _install(con, pid: int, new_file: Path, target_rel: str | None, kind: str, report: dict | None, reuse_row: int | None = None,
             keep_backup: bool = True):
    """Back up the photo's current file, put `new_file` in its place and update the catalog.
    The backup is verified byte for byte before anything is replaced. With keep_backup=False (compression, which makes
    a regular backup of the whole library first) the file is only held aside while the swap happens, as a hard link
    where possible, and is dropped once the catalog is updated."""
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    old_path = PATHS.media / r["rel_path"]
    old_sha = images.sha256_file(old_path)
    new_sha0 = images.sha256_file(new_file)
    if con.execute("SELECT 1 FROM photos WHERE sha256=? AND id!=?", (new_sha0, pid)).fetchone():
        raise DuplicateError()
    bdir = PATHS.media / ".originals"
    bdir.mkdir(exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", r["filename"])
    backup = _unique(bdir / f"{'video' if r['is_video'] else 'photo'}_{pid}_{int(time.time())}_{safe_name}")
    linked = False
    if not keep_backup:
        try:
            os.link(old_path, backup); linked = True            # same data, no copy: it only keeps the old file alive during the swap
        except OSError:
            pass
    if not linked:
        shutil.copy2(old_path, backup)
        if images.sha256_file(backup) != old_sha:
            backup.unlink(missing_ok=True)
            raise RuntimeError("backup verification failed")

    if target_rel:
        final = PATHS.media / target_rel
    else:
        final = old_path.with_name(Path(r["filename"]).stem + new_file.suffix.lower())
    if final != old_path and final.exists():
        final = _unique(final)
    old_size = old_path.stat().st_size
    try:
        os.replace(new_file, final)
        if final != old_path:
            old_path.unlink(missing_ok=True)
        new_sha = images.sha256_file(final)
        rel = str(final.relative_to(PATHS.media))
        row = (str(backup.relative_to(PATHS.media)), r["rel_path"], r["filename"], old_sha, old_size, int(time.time()), kind,
               json.dumps(report) if report else None)
        con.execute("UPDATE photos SET filename=?, rel_path=?, mime=?, bytes=?, sha256=? WHERE id=?",
                    (final.name, rel, final.suffix.lstrip(".").lower(), final.stat().st_size, new_sha, pid))
        if not r["is_video"]:
            _sync_dimensions(con, pid, r, final)
        if not keep_backup:
            pass
        elif reuse_row:
            con.execute("UPDATE video_backups SET backup_rel=?, orig_rel=?, orig_filename=?, orig_sha=?, orig_bytes=?, "
                        "created_at=?, kind=?, report=? WHERE id=?", (*row, reuse_row))
        else:
            con.execute("INSERT INTO video_backups(photo_id, backup_rel, orig_rel, orig_filename, orig_sha, orig_bytes, "
                        "created_at, kind, report) VALUES(?,?,?,?,?,?,?,?,?)", (pid, *row))
        con.commit()
        if not keep_backup:
            backup.unlink(missing_ok=True)
    except Exception:
        con.rollback()                                  # put the previous file back; the catalog never changed
        if backup.exists():
            shutil.copy2(backup, old_path)
            if final != old_path:
                final.unlink(missing_ok=True)
        raise
    images.thumb_path(old_sha).unlink(missing_ok=True)
    images.make_thumb(final, new_sha)
    return final


def _sync_dimensions(con, pid: int, old_row, final: Path):
    """A photo that changed size: store the new size and scale its face boxes (pixel coordinates) with it."""
    w, h = images.dimensions(final)
    if not (w and h):
        return
    con.execute("UPDATE photos SET width=?, height=? WHERE id=?", (w, h, pid))
    ow, oh = old_row["width"], old_row["height"]
    if ow and oh and max(w, h) != max(ow, oh):
        f = max(w, h) / max(ow, oh)
        con.execute("UPDATE faces SET x1=x1*?, y1=y1*?, x2=x2*?, y2=y2*? WHERE photo_id=?", (f, f, f, f, pid))


def restore_previous(pid: int) -> dict:
    """Swap the photo back to the version saved before the last compression. The compressed version is
    kept as the new backup, so calling this again switches back."""
    con = db.init_db()
    row = con.execute("SELECT * FROM video_backups WHERE photo_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if not row:
        raise LookupError("no backup")
    backup = PATHS.media / row["backup_rel"]
    if not backup.exists():
        raise FileNotFoundError(row["backup_rel"])
    _install(con, pid, backup, row["orig_rel"], "restore", None, reuse_row=row["id"])
    return {"ok": True}


# ------------------------------------------------------------------ the job
_PCT = re.compile(rb"Encoding: task \d+ of \d+, (\d+(?:\.\d+)?) %(?: \((?:\d+(?:\.\d+)?) fps, avg (\d+(?:\.\d+)?) fps, ETA (\d+)h(\d+)m(\d+)s\))?")


class _BackupProgress:
    """Lets the backup report what it is doing without touching the compression's own progress numbers."""
    cancel = False
    total = done = 0

    def __init__(self, real):
        self.real = real

    def say(self, key, **vars):
        self.real.say(key, **vars)


def ensure_backup(progress) -> bool:
    """A regular backup (catalog + a full set of the photos and videos) before the first file is replaced; once per
    compression job. False = it could not be made: the job is marked failed and nothing was changed."""
    root = getattr(progress, "parent", progress)
    if getattr(root, "backed_up", False):
        return True
    progress.say("Making a backup first…")
    try:
        backup.create_snapshot("before-compress", _BackupProgress(progress))
    except backup.BusyError:
        progress.fail("Another backup is running right now, try again in a few minutes")
        return False
    except backup.NoSpaceError as e:
        progress.fail("Not enough free space on the backup disk: {need} MB needed, {free} MB free. Choose a folder on another disk",
                      need=round(e.need / 1048576), free=round(e.free / 1048576))
        return False
    except Exception as e:
        progress.fail("Could not make a backup before compressing ({error}); nothing was changed", error=str(e)[:160])
        return False
    root.backed_up = True
    return True


def run_compress(pid: int, raw_options: dict, progress):
    con = db.init_db()
    tmp = None
    result = {"applied": False, "ok": False, "checks": []}
    progress.result = result
    cancelled = lambda: bool(getattr(progress, "cancel", False))
    try:
        o = normalize(raw_options)
        row = con.execute("SELECT * FROM photos WHERE id=? AND is_video=1", (pid,)).fetchone()
        if not row:
            return progress.fail("The file is not a video")
        if refmode.is_external(row["rel_path"]):
            return progress.fail("This photo is in your own folder, which photag never changes. Import a copy if you want to edit it")
        src_path = PATHS.media / row["rel_path"]
        if src_path.suffix.lower() not in VIDEO_OK:
            return progress.fail("This file type is not supported for compression ({ext})", ext=src_path.suffix.lower())
        exe = find_handbrake()
        if not exe:
            return progress.fail("HandBrakeCLI was not found. Install it and try again")
        if not ffmpeg.available():
            return progress.fail("ffmpeg is missing for verifying the result. Run: pip install imageio-ffmpeg")

        progress.state = "probing"; progress.total = 0; progress.say("Checking the original video…")
        src = _measure(src_path, cancelled)
        if cancelled():
            return _cancelled(progress)
        tdir = PATHS.media / ".compress_tmp"
        tdir.mkdir(exist_ok=True)
        tmp = tdir / f"{pid}_{int(time.time())}.mp4"

        progress.state = "encoding"; progress.total = 1000; progress.done = 0
        progress.say("Compressing… {pct}%", pct=0)
        p = subprocess.Popen(build_args(exe, str(src_path), str(tmp), o), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=NO_WIN)
        tail: list[bytes] = []

        def reader():
            buf = b""
            while chunk := p.stdout.read1(4096):
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for line in parts:
                    m = _PCT.search(line)
                    if m:
                        pct = float(m.group(1))
                        if m.group(2):
                            progress.extra = {**progress.extra, "fps": float(m.group(2)),
                                              "eta": int(m.group(3)) * 3600 + int(m.group(4)) * 60 + int(m.group(5))}
                        progress.done = int(pct * 10); progress.say("Compressing… {pct}%", pct=int(pct))
                    elif line.strip():
                        tail.append(line[-300:]); del tail[:-8]
        t = threading.Thread(target=reader, daemon=True); t.start()
        while p.poll() is None:
            if cancelled():
                p.terminate()
                break
            time.sleep(0.2)
        t.join(5)
        if cancelled():
            return _cancelled(progress)
        if p.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            return progress.fail("HandBrake failed: {error}", error=b" | ".join(tail[-3:]).decode("utf-8", "replace")[:300])

        progress.state = "verifying"; progress.total = 0; progress.say("Verifying the result…")
        out = _measure(tmp, cancelled)
        changed_fps = o["fps_mode"] != "same" and abs((out["fps"] or 0) - (src["fps"] or 0)) > 0.01
        ssim = ffmpeg.ssim(str(tmp), str(src_path), cancel=cancelled, fps=out["fps"] if changed_fps else None)
        if cancelled():
            return _cancelled(progress)
        checks = verify(src, out, o, ssim)
        result.update(checks=checks, ok=all(c["ok"] for c in checks), options=o, handbrake=handbrake_version(exe),
                      src={k: src[k] for k in ("frames", "duration", "width", "height", "fps", "bytes")},
                      out={k: out[k] for k in ("frames", "duration", "width", "height", "fps", "bytes")},
                      saved=src["bytes"] - out["bytes"], ratio=round(out["bytes"] / src["bytes"], 4))
        if not result["ok"]:
            bad = [c["id"] for c in checks if not c["ok"]]
            return progress.fail("The result failed verification ({checks}); the original file was not changed", checks=", ".join(bad))

        progress.state = "replacing"
        if not ensure_backup(progress):
            return
        progress.say("Replacing the file…")
        _install(con, pid, tmp, None, "compress", result, keep_backup=False)
        tmp = None
        result["applied"] = True
        progress.state = "done"
        progress.say_parts(("Video compressed to {pct}% of its size", {"pct": round(result["ratio"] * 100)}),
                           ("The previous version is in the backup made before compressing", {}))
    except DuplicateError:
        progress.fail("An image identical to the result already exists in the library; the original file was not changed")
    except Exception as e:
        progress.fail("Compression failed: {error}", error=str(e))
    finally:
        if tmp and tmp.exists():
            tmp.unlink(missing_ok=True)


def _cancelled(progress):
    progress.state = "done"
    progress.say("Compression was cancelled")


# ================================================================== photos
IMAGE_OK = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}     # RAW / HEIC / GIF are left alone
IMG_DEFAULTS = {"quality": 86, "max_side": 0}
IMG_Q_RANGE = (40, 98)
IMG_SSIM_FLOOR = 0.80


def normalize_image(raw: dict | None) -> dict:
    o = {**IMG_DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in IMG_DEFAULTS and v is not None}}
    o["quality"] = int(max(IMG_Q_RANGE[0], min(IMG_Q_RANGE[1], round(float(o["quality"])))))
    ms = int(o["max_side"] or 0)
    o["max_side"] = ms if 256 <= ms <= 16384 else 0
    return o


def _expected_size(w: int, h: int, max_side: int) -> tuple[int, int]:
    if max_side and max(w, h) > max_side:
        s = max_side / max(w, h)
        return max(1, round(w * s)), max(1, round(h * s))
    return w, h


def _encode_image(src: Path, dst: Path, o: dict) -> dict:
    """Re-encode `src` into `dst` (same pixels unless a size cap applies). Metadata and the colour profile are carried
    over; the EXIF orientation tag is kept as it is, so the pixels are never rotated. Returns what was done."""
    from PIL import Image
    ext = src.suffix.lower()
    with Image.open(src) as im:
        if getattr(im, "n_frames", 1) > 1:
            raise ValueError("animated")
        im.load()
        exif, icc = im.info.get("exif"), im.info.get("icc_profile")
        size0 = im.size
        w, h = _expected_size(*im.size, o["max_side"])
        if (w, h) != im.size:
            im = im.resize((w, h), Image.LANCZOS)
        kw = {}
        if exif: kw["exif"] = exif
        if icc: kw["icc_profile"] = icc
        if ext in (".jpg", ".jpeg"):
            if im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=o["quality"], optimize=True, progressive=True,
                    subsampling="keep" if (o["quality"] >= 95 and ext in (".jpg", ".jpeg") and im.mode == "YCbCr") else 2, **kw)
            how = "jpeg"
        elif ext == ".webp":
            im.save(dst, "WEBP", quality=o["quality"], method=6, **kw)
            how = "webp"
        elif ext in (".tif", ".tiff"):
            im.save(dst, "TIFF", compression="tiff_adobe_deflate", **({"icc_profile": icc} if icc else {}))
            how = "tiff-lossless"
        else:       # .png and .bmp: lossless
            im.save(dst, "PNG", optimize=True, compress_level=9, **kw)
            how = "png-lossless"
    return {"how": how, "size0": size0}


def _read_meta(path: Path) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        return {"exif": im.info.get("exif") or b"", "icc": im.info.get("icc_profile") or b"", "size": im.size,
                "frames": getattr(im, "n_frames", 1)}


def verify_image(src: dict, out: dict, o: dict, ssim: dict, lossless_kind: bool, src_bytes: int, out_bytes: int) -> list[dict]:
    checks = []
    def add(cid, ok, expected, actual, **extra):
        checks.append({"id": cid, "ok": bool(ok), "expected": expected, "actual": actual, **extra})

    ew, eh = _expected_size(*src["size"], o["max_side"])
    ok = abs(out["size"][0] - ew) <= 1 and abs(out["size"][1] - eh) <= 1
    add("resolution", ok, f"{ew}×{eh}", f"{out['size'][0]}×{out['size'][1]}")
    add("metadata", out["exif"] == src["exif"] and out["icc"] == src["icc"], "same", "same" if out["exif"] == src["exif"] and out["icc"] == src["icc"] else "changed")
    add("size", out_bytes < src_bytes, f"<{src_bytes}", out_bytes)
    add("ssim", ssim["mean"] >= IMG_SSIM_FLOOR, f"≥{IMG_SSIM_FLOOR:.2f}", f"{ssim['mean']:.4f}",
        mean=round(ssim["mean"], 4), min=round(ssim["mean"], 4), frames=1, grade=grade(ssim["mean"]), lossless=lossless_kind)
    return checks


def run_compress_image(pid: int, raw_options: dict, progress):
    con = db.init_db()
    tmp = None
    result = {"applied": False, "ok": False, "checks": [], "kind": "image"}
    progress.result = result
    cancelled = lambda: bool(getattr(progress, "cancel", False))
    try:
        o = normalize_image(raw_options)
        row = con.execute("SELECT * FROM photos WHERE id=? AND is_video=0", (pid,)).fetchone()
        if not row:
            return progress.fail("The file is not an image")
        if refmode.is_external(row["rel_path"]):
            return progress.fail("This photo is in your own folder, which photag never changes. Import a copy if you want to edit it")
        src_path = PATHS.media / row["rel_path"]
        ext = src_path.suffix.lower()
        if ext not in IMAGE_OK:
            return progress.fail("This file type is not supported for compression ({ext})", ext=ext)
        if not ffmpeg.available():
            return progress.fail("ffmpeg is missing for verifying the result. Run: pip install imageio-ffmpeg")

        progress.state = "probing"; progress.total = 0; progress.say("Checking the original photo…")
        src = _read_meta(src_path)
        if src["frames"] > 1:
            return progress.fail("Animated images are not supported for compression")
        if cancelled():
            return _cancelled(progress)
        tdir = PATHS.media / ".compress_tmp"
        tdir.mkdir(exist_ok=True)
        out_ext = ".png" if ext == ".bmp" else ext
        tmp = tdir / f"{pid}_{int(time.time())}{out_ext}"

        progress.state = "encoding"; progress.total = 0; progress.say("Compressing…")
        done = _encode_image(src_path, tmp, o)
        if cancelled():
            return _cancelled(progress)

        progress.state = "verifying"; progress.say("Verifying the result…")
        out = _read_meta(tmp)
        ssim = ffmpeg.ssim(str(tmp), str(src_path), cancel=cancelled)
        if cancelled():
            return _cancelled(progress)
        s_bytes, o_bytes = src_path.stat().st_size, tmp.stat().st_size
        checks = verify_image(src, out, o, ssim, done["how"].endswith("lossless"), s_bytes, o_bytes)
        result.update(checks=checks, ok=all(c["ok"] for c in checks), options=o, how=done["how"],
                      src={"width": src["size"][0], "height": src["size"][1], "bytes": s_bytes},
                      out={"width": out["size"][0], "height": out["size"][1], "bytes": o_bytes},
                      saved=s_bytes - o_bytes, ratio=round(o_bytes / s_bytes, 4),
                      failed=[c["id"] for c in checks if not c["ok"]])
        if not result["ok"]:
            return progress.fail("The result failed verification ({checks}); the original file was not changed", checks=", ".join(result["failed"]))

        progress.state = "replacing"
        if not ensure_backup(progress):
            return
        progress.say("Replacing the file…")
        _install(con, pid, tmp, None, "compress", result, keep_backup=False)
        tmp = None
        result["applied"] = True
        progress.state = "done"
        progress.say_parts(("Image compressed to {pct}% of its size", {"pct": round(result["ratio"] * 100)}),
                           ("The previous version is in the backup made before compressing", {}))
    except DuplicateError:
        progress.fail("An image identical to the result already exists in the library; the original file was not changed")
    except Exception as e:
        progress.fail("Compression failed: {error}", error=str(e))
    finally:
        if tmp and tmp.exists():
            tmp.unlink(missing_ok=True)


# ================================================================== several files at once (photos and/or videos)
_BUSY = ("probing", "encoding", "verifying", "replacing")


class _Sub(importer.Progress):
    """Progress of one file inside a batch: shares the batch's cancel flag and mirrors itself into the batch."""
    cancel = property(lambda s: s.parent.cancel, lambda s, v: None)

    def __init__(self, parent, idx: int, n: int, name: str, kind: str):
        super().__init__()
        self.parent, self.idx, self.n, self.name, self.kind = parent, idx, n, name, kind

    def say(self, key, **vars):
        super().say(key, **vars)
        p = self.parent
        p.done = int((self.idx + (self.done / self.total if self.total else 0)) * 1000)
        p.total = self.n * 1000
        if self.state in _BUSY:
            p.state = self.state
        p.extra = {**self.extra, "i": self.idx, "n": self.n, "name": self.name, "kind": self.kind}
        p.say_parts(("File {i} of {n}: {name}", {"i": self.idx + 1, "n": self.n, "name": self.name}), (key, vars))


def run_compress_batch(ids: list[int], video_options: dict, image_options: dict, progress):
    """Compress many files in turn. One file failing (or not getting smaller) never stops the others;
    every file is verified and backed up on its own exactly like a single compression."""
    con = db.init_db()
    items, saved, n = [], 0, len(ids)
    result = {"batch": True, "applied": False, "items": items, "saved": 0, "counts": {}}
    progress.result = result
    progress.state = "probing"; progress.total = n * 1000; progress.done = 0
    hb = find_handbrake()
    for i, pid in enumerate(ids):
        if progress.cancel:
            break
        row = con.execute("SELECT id, filename, rel_path, is_video, bytes FROM photos WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        ext = Path(row["rel_path"]).suffix.lower()
        item = {"id": pid, "filename": row["filename"], "kind": "video" if row["is_video"] else "image",
                "status": "running", "src_bytes": row["bytes"]}
        items.append(item)
        if (row["is_video"] and ext not in VIDEO_OK) or (not row["is_video"] and ext not in IMAGE_OK):
            item.update(status="skipped", error_key="This file type is not supported for compression ({ext})", error_vars={"ext": ext})
            progress.done = (i + 1) * 1000
            continue
        if row["is_video"] and not hb:
            item.update(status="skipped", error_key="HandBrakeCLI was not found. Install it and try again", error_vars={})
            progress.done = (i + 1) * 1000
            continue
        sub = _Sub(progress, i, n, row["filename"], item["kind"])
        sub.state = "starting"
        (run_compress if row["is_video"] else run_compress_image)(pid, video_options if row["is_video"] else image_options, sub)
        r = sub.result or {}
        if sub.state == "error":
            only_size = r.get("checks") and [c["id"] for c in r["checks"] if not c["ok"]] == ["size"]
            item.update(status="no_gain" if only_size else "failed",
                        error_key=sub.error_key, error_vars=sub.vars)
        elif r.get("applied"):
            item.update(status="done", applied=True, out_bytes=r["out"]["bytes"], ratio=r["ratio"],
                        grade=next((c.get("grade") for c in r["checks"] if c["id"] == "ssim"), None))
            saved += r["saved"]
        else:
            item.update(status="cancelled")
        progress.done = (i + 1) * 1000
    counts = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    result.update(applied=bool(counts.get("done")), saved=saved, counts=counts, cancelled=bool(progress.cancel))
    progress.state = "done"
    if progress.cancel:
        progress.say_parts(("Compression was cancelled", {}), ("Compressed {n} files", {"n": counts.get("done", 0)}))
    else:
        progress.say_parts(("Compressed {n} files", {"n": counts.get("done", 0)}),
                           *([("{n} files were not compressed", {"n": n - counts.get("done", 0)})] if n > counts.get("done", 0) else []))
