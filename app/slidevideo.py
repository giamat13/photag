"""A video (MP4) of a slideshow: the chosen pictures, one after the other, with a soft cross-fade and optional music.

Every picture is first fitted onto a 16:9 frame (the picture on a blurred copy of itself, so nothing is cropped and there are no
black bars), then one ffmpeg run joins them. The edits of a photo (the non-destructive develop settings) are included.
"""
import tempfile
from pathlib import Path

from . import db, ffmpeg, images, render
from .config import PATHS

MAX_PICTURES = 300
SIZES = {720: (1280, 720), 1080: (1920, 1080)}


class NoFfmpeg(RuntimeError):
    pass


def frame(src: Path, out: Path, size: tuple[int, int]):
    """src -> out (JPEG), exactly `size`: the picture contained, over a blurred, darkened enlargement of itself."""
    from PIL import Image, ImageFilter, ImageEnhance
    W, H = size
    im = images.open_image(src).convert("RGB")
    bg = im.copy()
    k = max(W / bg.width, H / bg.height)
    bg = bg.resize((max(W, round(bg.width * k)), max(H, round(bg.height * k))), Image.BILINEAR)
    bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(24))).enhance(0.55)
    im.thumbnail((W, H), Image.LANCZOS)
    bg.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    bg.save(out, "JPEG", quality=92)


def command(frames: list[Path], dest: Path, seconds: float, fade: float, size: tuple[int, int], music: str | None, fps: int = 25) -> list[str]:
    """The ffmpeg arguments. Each picture is shown `seconds` seconds, of which the last `fade` overlap the next one."""
    n = len(frames)
    fade = max(0.0, min(fade, seconds / 2)) if n > 1 else 0.0
    args = ["-y", "-v", "error", "-nostats"]
    for f in frames:
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.3f}", "-i", str(f)]
    total = n * seconds - (n - 1) * fade
    if music:
        args += ["-stream_loop", "-1", "-i", music]
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]format=yuv420p,setsar=1,fps={fps}[v{i}]")
    if n == 1:
        chain = "[v0]"
    elif fade:
        cur = "[v0]"
        for i in range(1, n):
            parts.append(f"{cur}[v{i}]xfade=transition=fade:duration={fade:.3f}:offset={i * (seconds - fade):.3f}[x{i}]")
            cur = f"[x{i}]"
        chain = cur
    else:
        parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[x]")
        chain = "[x]"
    args += ["-filter_complex", ";".join(parts), "-map", chain]
    if music:
        args += ["-map", f"{n}:a", "-af", f"afade=t=out:st={max(0.0, total - 2):.3f}:d=2", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
    return args


def run(ids: list[int], dest: str, seconds: float, fade: float, height: int, music: str | None, progress):
    """Background job (see server._start). Needs ffmpeg."""
    progress.state = "exporting"
    try:
        if not ffmpeg.available():
            raise NoFfmpeg()
        size = SIZES.get(int(height), SIZES[1080])
        seconds = max(1.0, min(float(seconds), 30.0))
        con = db.connect()
        paths = []
        for pid in ids:
            r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
            if not r or r["is_video"]:
                continue
            p = render.current_path(r)
            if p and Path(p).exists():
                paths.append(Path(p))
        if not paths:
            progress.fail("There are no pictures to put in the video")
            return
        if len(paths) > MAX_PICTURES:
            progress.fail("A slideshow video can hold up to {n} pictures", n=MAX_PICTURES)
            return
        progress.total = len(paths) + 1; progress.done = 0
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="photag-slides-") as td:
            frames = []
            for i, p in enumerate(paths):
                if progress.cancel:
                    progress.say("Cancelled"); progress.state = "done"
                    return
                progress.say("Preparing picture {done} of {total}", done=i + 1, total=len(paths))
                f = Path(td) / f"{i:04d}.jpg"
                frame(p, f, size)
                frames.append(f)
                progress.done = i + 1
            progress.say("Making the video…")
            tmp = out.with_name(out.name + ".part.mp4")
            rc, _, e = ffmpeg.run(command(frames, tmp, seconds, fade, size, music if music and Path(music).is_file() else None), lambda: progress.cancel)
            if progress.cancel:
                tmp.unlink(missing_ok=True)
                progress.say("Cancelled"); progress.state = "done"
                return
            if rc != 0 or not tmp.exists():
                tmp.unlink(missing_ok=True)
                progress.fail("The video could not be made: {error}", error=e.strip()[-200:])
                return
            tmp.replace(out)
        progress.done = progress.total
        progress.result = {"path": str(out), "pictures": len(paths), "seconds": round(len(paths) * seconds - max(0, len(paths) - 1) * (fade if len(paths) > 1 else 0), 1)}
        progress.say("The video was saved")
        progress.state = "done"
    except NoFfmpeg:
        progress.fail("The video maker (ffmpeg) is not available on this computer")
    except Exception as e:
        progress.fail("The video could not be made: {error}", error=str(e)[:200])
