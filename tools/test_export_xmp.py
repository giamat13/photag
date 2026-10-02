"""Test: exporting photos with an XMP sidecar (importer.run_export(..., xmp_sidecar=True)) -- rating,
color label, keywords, people (from both Takeout tags and detected faces) and caption end up in a
standard XMP packet next to each exported file, in both folder and ZIP export.

    py -3.12 tools/test_export_xmp.py
"""
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_export_xmp_test_"))
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

p = PATHS.media / "2024" / "a.jpg"
Image.new("RGB", (100, 80), (50, 60, 70)).save(p, "JPEG")
pid = con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes,rating,label,description) VALUES(?,?,?,?,?,?,?)",
                  ("sha1", "a.jpg", str(p.relative_to(PATHS.media)), p.stat().st_size, 4, "red", "A day at the beach")).lastrowid
con.execute("INSERT INTO tags(name) VALUES('Beach')")
tid = con.execute("SELECT id FROM tags WHERE name='Beach'").fetchone()["id"]
con.execute("INSERT INTO photo_tags(photo_id,tag_id,source) VALUES(?,?,'manual')", (pid, tid))
con.execute("INSERT INTO people(name,source) VALUES('Danny','manual')")
peid = con.execute("SELECT id FROM people WHERE name='Danny'").fetchone()["id"]
con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,?,'manual')", (pid, peid))
# a second photo with no metadata at all: must not get a sidecar
p2 = PATHS.media / "2024" / "b.jpg"
Image.new("RGB", (100, 80), (10, 10, 10)).save(p2, "JPEG")
pid2 = con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes) VALUES(?,?,?,?)",
                   ("sha2", "b.jpg", str(p2.relative_to(PATHS.media)), p2.stat().st_size)).lastrowid
con.commit()

# ---- folder export
out_dir = tmp / "folder_export"
prog = _Progress()
importer.run_export([pid, pid2], str(out_dir), originals=True, long_edge=None, quality=100, as_zip=False, xmp_sidecar=True, progress=prog)
check("folder export finished without failing", prog.state != "error", prog.error)
xmp_path = out_dir / "a.jpg.xmp"
check("the sidecar sits next to the file, name.ext.xmp", xmp_path.is_file())
text = xmp_path.read_text("utf-8")
check("the rating is in the sidecar", 'xmp:Rating="4"' in text, text)
check("the color label is capitalized, standard style", 'xmp:Label="Red"' in text, text)
check("the keyword is listed", "<rdf:li>Beach</rdf:li>" in text, text)
check("the tagged person is listed as a keyword too", "<rdf:li>Danny</rdf:li>" in text, text)
check("the caption is in dc:description", "A day at the beach" in text, text)
check("a photo with no rating/label/keywords/people/caption still writes a sidecar (empty is fine)", (out_dir / "b.jpg.xmp").is_file())
empty_text = (out_dir / "b.jpg.xmp").read_text("utf-8")
check("...but that sidecar has no rating/label/keywords attributes", "xmp:Rating" not in empty_text and "<rdf:li>" not in empty_text, empty_text)

# ---- without the xmp_sidecar flag: no .xmp files at all
out_dir2 = tmp / "folder_export_no_xmp"
prog2 = _Progress()
importer.run_export([pid], str(out_dir2), originals=True, long_edge=None, quality=100, as_zip=False, xmp_sidecar=False, progress=prog2)
check("xmp_sidecar=False writes no .xmp file", not list(out_dir2.glob("*.xmp")), list(out_dir2.glob("*.xmp")))

# ---- ZIP export with sidecar
out_zip = tmp / "export.zip"
prog3 = _Progress()
importer.run_export([pid], str(out_zip), originals=True, long_edge=None, quality=100, as_zip=True, xmp_sidecar=True, progress=prog3)
check("zip export finished without failing", prog3.state != "error", prog3.error)
with zipfile.ZipFile(out_zip) as zf:
    names = zf.namelist()
    check("the ZIP holds the photo and its sidecar", sorted(names) == ["a.jpg", "a.jpg.xmp"], names)
    with zf.open("a.jpg.xmp") as f:
        zip_text = f.read().decode("utf-8")
    check("the sidecar inside the ZIP has the same rating", 'xmp:Rating="4"' in zip_text, zip_text)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
