"""Test: the slideshow video (MP4) -- pictures with cross-fades and music, made by ffmpeg.

    py -3.12 tools/test_slidevideo.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_slidevideo_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from app import config, db, ffmpeg, importer, slidevideo  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


if not ffmpeg.available():
    print("SKIP no ffmpeg on this computer")
    sys.exit(0)

config.set_library_root(tmp / "lib")
PATHS.refresh()
con = db.init_db()
PATHS.media.mkdir(parents=True, exist_ok=True)
for i, (w, h, col) in enumerate([(800, 600, (200, 30, 30)), (400, 900, (30, 200, 30)), (1000, 300, (30, 30, 200))], 1):
    Image.new("RGB", (w, h), col).save(PATHS.media / f"p{i}.jpg", "JPEG")
    con.execute("INSERT INTO photos(id, sha256, filename, rel_path, is_video, trashed) VALUES(?,?,?,?,0,0)", (i, f"s{i}", f"p{i}.jpg", f"p{i}.jpg"))
con.execute("INSERT INTO photos(id, sha256, filename, rel_path, is_video, trashed) VALUES(9,'sv','v.mp4','v.mp4',1,0)")
con.commit()

fr = tmp / "f.jpg"
slidevideo.frame(PATHS.media / "p2.jpg", fr, (1280, 720))
im = Image.open(fr)
check("a tall picture is fitted into a 16:9 frame, none of it cut off", im.size == (1280, 720) and im.getpixel((640, 360))[1] > 150, im.size)
check("...over a blurred copy of itself, not black bars", im.getpixel((10, 10))[1] > 30, im.getpixel((10, 10)))

music = tmp / "m.wav"
ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(music)])
out = tmp / "out" / "show.mp4"
p = importer.Progress()
slidevideo.run([1, 2, 3, 9], str(out), 2, 0.5, 720, str(music), p)
check("the job ends 'done'", p.state == "done", (p.state, p.error))
check("the video file exists", out.is_file() and out.stat().st_size > 1000)
i = ffmpeg.info(str(out))
check("720p, about 5 seconds (3 x 2 s, 2 fades of 0.5 s), the video in the list is skipped", i["width"] == 1280 and i["height"] == 720 and abs(i["duration"] - 5.0) < 0.3, i)
check("the music is in the video (and it is not longer than the pictures)", i["has_audio"])
check("no temporary file is left", not list((tmp / "out").glob("*.part*")))
out2 = tmp / "cuts.mp4"
p2 = importer.Progress()
slidevideo.run([1, 2], str(out2), 3, 0, 720, None, p2)
i2 = ffmpeg.info(str(out2))
check("hard cuts: 2 x 3 s = 6 s, no sound", p2.state == "done" and abs(i2["duration"] - 6.0) < 0.3 and not i2["has_audio"], i2)
p3 = importer.Progress()
slidevideo.run([9], str(tmp / "none.mp4"), 3, 0.5, 720, None, p3)
check("only videos in the list: a clear error, no file", p3.state == "error" and not (tmp / "none.mp4").exists(), p3.error)
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
