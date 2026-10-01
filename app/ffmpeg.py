"""ffmpeg helpers: probing a video, counting its frames exactly, and SSIM between two videos.

Used to verify a compressed video against the original, and for video thumbnails.
The binary comes from PATH, else from the `imageio-ffmpeg` package (which ships one).
"""
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@lru_cache(maxsize=1)
def exe() -> str | None:
    env = os.environ.get("PHOTAG_FFMPEG")
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        return p if p and Path(p).is_file() else None
    except Exception:
        return None


def available() -> bool:
    return exe() is not None


def _popen(args, **kw):
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            creationflags=_NOWIN, **kw)


def run(args: list[str], cancel=None, cwd: str | None = None) -> tuple[int, str, str]:
    """Run ffmpeg to completion, polling `cancel()` so a long decode can be stopped."""
    p = _popen([exe(), "-hide_banner", *args], cwd=cwd)
    import threading
    out: list[bytes] = [b""]; err: list[bytes] = [b""]
    t1 = threading.Thread(target=lambda: out.__setitem__(0, p.stdout.read()), daemon=True)
    t2 = threading.Thread(target=lambda: err.__setitem__(0, p.stderr.read()), daemon=True)
    t1.start(); t2.start()
    while p.poll() is None:
        if cancel and cancel():
            p.kill()
            break
        time.sleep(0.1)
    t1.join(); t2.join()
    return p.returncode, out[0].decode("utf-8", "replace"), err[0].decode("utf-8", "replace")


def _secs(h, m, s) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def info(path: str) -> dict:
    """Container duration, first video stream's codec / size / fps, audio presence."""
    _, _, err = run(["-i", str(path)])      # exits 1 ("no output file"), but prints the stream list
    d = {"duration": None, "codec": None, "width": None, "height": None, "fps": None, "has_audio": "Audio:" in err}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        d["duration"] = _secs(*m.groups())
    v = re.search(r"Stream #0:\d+[^\n]*Video:\s*(\w+)[^\n]*?\b(\d{2,5})x(\d{2,5})\b([^\n]*)", err)
    if v:
        d["codec"], d["width"], d["height"] = v.group(1), int(v.group(2)), int(v.group(3))
        f = re.search(r"(\d+(?:\.\d+)?)\s*fps", v.group(4))
        d["fps"] = float(f.group(1)) if f else None
    return d


def count_frames(path: str, cancel=None) -> dict:
    """Decode the whole first video stream (no frame dropping/duplication) and report how many
    frames it really has and where the last one ends. Exact, unlike container metadata."""
    base = ["-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(path), "-map", "0:v:0", "-an", "-sn"]
    for sync in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        rc, out, err = run(base + sync + ["-f", "null", "-"], cancel)
        if rc == 0 or (cancel and cancel()):
            break
    frames = re.findall(r"^frame=(\d+)", out, re.M)
    t = re.findall(r"^out_time_us=(\d+)", out, re.M)
    if not frames:
        raise RuntimeError(f"ffmpeg could not decode the video: {err.strip()[-200:]}")
    return {"frames": int(frames[-1]), "video_duration": int(t[-1]) / 1e6 if t else None}


def ssim(distorted: str, reference: str, width: int = 640, cancel=None, fps: float | None = None) -> dict:
    """Frame-by-frame SSIM of `distorted` against `reference`, both scaled to the same small
    width first (fast, and tolerates a resolution change). With `fps`, both are put on that frame rate first so
    a frame-rate change compares the matching moments. Returns mean / min / frames compared."""
    with tempfile.TemporaryDirectory() as td:
        # HandBrake tags its output (e.g. bt709) while an untagged source has no tag, and ffmpeg then converts
        # colours between the two before comparing -- which shows up as a fake quality loss (measured: SSIM 0.970
        # vs 0.994 for the same file). Stripping the tags on both sides compares the actual pixel values.
        untag = "setparams=colorspace=unknown:range=unknown:color_primaries=unknown:color_trc=unknown"
        rate = f"fps={fps:g}," if fps else ""
        graph = (f"[0:v]{untag},scale={width}:-2:flags=bilinear,{rate}setpts=PTS-STARTPTS[a];"
                 f"[1:v]{untag},scale={width}:-2:flags=bilinear,{rate}setpts=PTS-STARTPTS[b];[a][b]ssim=stats_file=ssim.log")
        rc, _, err = run(["-v", "error", "-nostats", "-i", str(distorted), "-i", str(reference), "-lavfi", graph,
                          "-an", "-sn", "-f", "null", "-"], cancel, cwd=td)
        log = Path(td, "ssim.log")
        vals = [float(x) for x in re.findall(r"\bAll:(\d+(?:\.\d+)?)", log.read_text("utf-8", "replace"))] if log.exists() else []
    if not vals:
        raise RuntimeError(f"ffmpeg could not compare the videos: {err.strip()[-200:]}")
    return {"mean": statistics.fmean(vals), "min": min(vals), "frames": len(vals)}
