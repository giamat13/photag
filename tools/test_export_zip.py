"""Test: exporting photos as a single ZIP file (importer.run_export(..., as_zip=True)) instead of a
folder -- originals/current copied as-is, JPEG-resize mode re-encoded, videos always copied as-is,
duplicate names get a number, and nothing is left behind in the temp folder used for resizing.

    py -3.12 tools/test_export_zip.py
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_export_zip_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, importer  # noqa: E402
from app.config import PATHS  # noqa: E402


class _Progress:
    def __init__(self):
        self.done = self.total = 0
        self.state = self.error = None
    def say(self, *a, **k): pass
    def fail(self, msg, **vars):
        self.state = "error"; self.error = msg.format(**vars)


config.PATHS.root.mkdir(parents=True, exist_ok=True)
(PATHS.media / "2024").mkdir(parents=True, exist_ok=True)
con = db.init_db()

ids = []
for i, name in enumerate(("a.jpg", "b.jpg"), 1):      # two photos that will collide after resize (same stem "x")
    p = PATHS.media / "2024" / ("x.jpg" if i == 2 else name)
    Image.new("RGB", (300, 200), (10 * i, 20 * i, 30 * i)).save(p, "JPEG")
    ids.append(con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes) VALUES(?,?,?,?)",
                           (f"sha{i}", p.name, str(p.relative_to(PATHS.media)), p.stat().st_size)).lastrowid)
# a third photo whose own name is already "x.jpg" in a JPEG-resize export, to force a name collision
p3 = PATHS.media / "2024" / "x.jpg"
if not p3.exists():
    Image.new("RGB", (300, 200), (90, 90, 90)).save(p3, "JPEG")
id3 = con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes) VALUES(?,?,?,?)",
                  ("sha3", "x.jpg", str(p3.relative_to(PATHS.media)), p3.stat().st_size)).lastrowid
# a "video" (fake bytes, just needs is_video=1 so it's copied as-is, never resized)
vp = PATHS.media / "2024" / "clip.mp4"
vp.write_bytes(b"not a real video, just needs to exist")
idv = con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes,is_video) VALUES(?,?,?,?,1)",
                  ("sha4", "clip.mp4", str(vp.relative_to(PATHS.media)), vp.stat().st_size)).lastrowid
con.commit()

out_zip = tmp / "export" / "photos.zip"
prog = _Progress()
importer.run_export([ids[0], ids[1], id3, idv], str(out_zip), originals=False, long_edge=200, quality=80, as_zip=True, xmp_sidecar=False, progress=prog)

check("the export finished without failing", prog.state != "error", prog.error)
check("the ZIP file was created", out_zip.is_file())
with zipfile.ZipFile(out_zip) as zf:
    names = zf.namelist()
    check("4 items are in the ZIP (no silent drops)", len(names) == 4, names)
    check("the video is copied as-is (original extension, not re-encoded)", "clip.mp4" in names, names)
    check("duplicate stems after resize got de-duplicated names", len({n for n in names if n.startswith("x")}) == 2, names)
    for n in names:
        if n.endswith(".jpg"):
            with zf.open(n) as f:
                im = Image.open(f); im.load()
                check(f"{n} was actually resized to the long edge", max(im.size) <= 200, im.size)
            break  # one is enough to prove resizing happened

import glob as _glob   # tempfile.mkdtemp's resize-temp dir lands outside `tmp`, in the system temp dir
leftovers = _glob.glob(str(Path(tempfile.gettempdir()) / "photag_zipexport_*"))
check("the temp folder used for resizing was cleaned up", not leftovers, leftovers)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
