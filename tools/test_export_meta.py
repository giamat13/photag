"""Test: the export option "Update the EXIF of the exported JPEG files and write the edit settings with the originals"
(importer.run_export(..., update_metadata=True)) and the XMP edit settings it adds.

A render or a resized copy has no EXIF of its own; with the option it gets every tag of the original plus the catalog's caption,
capture time, GPS, rating and keywords, with Orientation 1 and the right pixel size (the pixels are already upright). A copy of an
unedited original keeps its own orientation. An UNEDITED original exported with an edited photo's settings carries them in its XMP
sidecar (exact, in a photag: attribute, and best-effort in Lightroom's crs: vocabulary). Library files are never touched.

    py -3.12 tools/test_export_meta.py
"""
import hashlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_export_meta_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


import piexif  # noqa: E402

from app import config, db, images, importer, render  # noqa: E402
from app.config import PATHS  # noqa: E402


class _Progress:
    def __init__(self):
        self.done = self.total = 0
        self.state = self.error = None
    def say(self, *a, **k): pass
    def fail(self, msg, **vars):
        self.state = "error"; self.error = msg.format(**vars)


config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()
work = tmp / "work"
work.mkdir()
sha = lambda b: hashlib.sha256(b).hexdigest()


def photo(name, size=(120, 80), orientation=6, make="TestCam", color=(200, 90, 40), ext=".jpg", exif=True, shift=0):
    arr = (np.linspace(0, 255, size[0] * size[1] * 3).reshape(size[1], size[0], 3) + shift) % 256
    im = Image.fromarray(arr.astype("uint8"))
    p = work / f"{name}{ext}"
    if ext == ".jpg" and exif:
        e = Image.Exif()
        e[0x010F], e[0x0110], e[0x0112] = make, "Model-1", orientation
        im.save(p, quality=95, exif=e)
    else:
        im.save(p)
    pid, _ = importer._ingest_file(con, p)
    con.commit()
    return pid


def row(pid):
    return con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()


def lib(pid):
    return PATHS.media / row(pid)["rel_path"]


def setmeta(pid, desc="A caption – ünïcode", taken=1700000000, lat=32.0853, lng=34.7818, rating=4, tags=("beach", "sunset")):
    con.execute("UPDATE photos SET description=?, taken_at=?, lat=?, lng=?, rating=? WHERE id=?", (desc, taken, lat, lng, rating, pid))
    for t in tags:
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
        con.execute("INSERT OR IGNORE INTO photo_tags(photo_id, tag_id) SELECT ?, id FROM tags WHERE name=?", (pid, t))
    con.commit()


def edit(pid, ops):
    con.execute("UPDATE photos SET edited=1, edit_ops=? WHERE id=?", (json.dumps(ops), pid))
    con.commit()


def export(ids, dest, **kw):
    p = _Progress()
    args = dict(originals=False, long_edge=None, quality=100, as_zip=False, xmp_sidecar=False)
    args.update(kw)
    um = args.pop("update_metadata", False)
    importer.run_export(ids, str(dest), args["originals"], args["long_edge"], args["quality"], args["as_zip"], args["xmp_sidecar"], p, update_metadata=um)
    return p


ex = lambda path: piexif.load(str(path))
OPS = {"exposure": 0.7, "contrast": 1.3, "highlights": -40, "shadows": 25, "saturation": 0.8, "vibrance": 30, "clarity": 20, "sharpness": 40,
       "vignette": -20, "crop": [0.1, 0.1, 0.9, 0.9], "temperature": 15, "sepia": 10, "flip_h": True}

A = photo("a_edited", orientation=6, make="CamA")
setmeta(A)
edit(A, {"exposure": 0.7, "contrast": 1.3, "crop": [0.1, 0.1, 0.9, 0.9], "highlights": -40, "temperature": 15})
B = photo("b_plain", orientation=3, make="CamB", shift=40)
setmeta(B, desc="plain caption", lat=None, lng=None, rating=0, tags=())
lib_a, lib_b = lib(A), lib(B)
snap = {p: sha(p.read_bytes()) for p in (lib_a, lib_b)}

# ---- "as it looks" copy of an EDITED photo
out = tmp / "o1"
p = export([A], out, update_metadata=True)
f = next(out.glob("*.jpg"))
e = ex(f)
check("export finishes without error", p.state == "done" and not p.error, p.error)
check("the exported render has the camera's EXIF (Make/Model of the original)", e["0th"].get(piexif.ImageIFD.Make) == b"CamA" and e["0th"].get(piexif.ImageIFD.Model) == b"Model-1")
check("Orientation is 1 (the pixels are already upright; the original said 6)", e["0th"].get(piexif.ImageIFD.Orientation) == 1)
w, h = images.dimensions(f)
check("the stored pixel size is the render's size (it was cropped)", e["Exif"].get(piexif.ExifIFD.PixelXDimension) == w and e["Exif"].get(piexif.ExifIFD.PixelYDimension) == h, (w, h))
check("caption from the catalog (unicode survives)", e["0th"].get(piexif.ImageIFD.ImageDescription, b"").decode("utf-8") == "A caption – ünïcode")
check("capture time from the catalog", e["Exif"].get(piexif.ExifIFD.DateTimeOriginal) == b"2023:11:14 22:13:20", e["Exif"].get(piexif.ExifIFD.DateTimeOriginal))
g = e["GPS"]
lat = g[piexif.GPSIFD.GPSLatitude]
check("GPS from the catalog", abs(lat[0][0] + lat[1][0] / 60 - 32.0853) < 0.01 and g[piexif.GPSIFD.GPSLatitudeRef] == b"N", lat)
check("star rating and keywords (Windows XP tags)", e["0th"].get(piexif.ImageIFD.Rating) == 4 and bytes(e["0th"].get(piexif.ImageIFD.XPKeywords, ())).decode("utf-16le").rstrip("\x00") == "beach;sunset")
check("the camera's embedded thumbnail is dropped (it would show the unedited picture)", not e.get("thumbnail") and not e.get("1st"))
check("the exported picture is the EDITED look: the original (turned upright: 80x120) cropped to 80%", (w, h) == (64, 96), (w, h))
check("the library file was not touched", sha(lib_a.read_bytes()) == snap[lib_a])

# ---- without the option: the old behaviour
out = tmp / "o2"
export([A], out)
e2 = ex(next(out.glob("*.jpg")))
check("without the option a render still has no EXIF (the reason for the option)", not e2["0th"].get(piexif.ImageIFD.Make) and not e2["Exif"])

# ---- original of an UNEDITED photo, copied as is
out = tmp / "o3"
export([B], out, update_metadata=True, originals=True)
f = next(out.glob("*.jpg"))
e = ex(f)
check("an unedited original keeps its own EXIF and ORIENTATION (3: its pixels are not rotated)", e["0th"].get(piexif.ImageIFD.Orientation) == 3 and e["0th"].get(piexif.ImageIFD.Make) == b"CamB")
check("...with the catalog's caption on top (this copy only)", e["0th"].get(piexif.ImageIFD.ImageDescription) == b"plain caption")
check("...the pixels are the original's (same image data)", np.array_equal(np.asarray(Image.open(f)), np.asarray(Image.open(lib_b))))
check("no GPS / rating was invented where the catalog has none", not e["GPS"].get(piexif.GPSIFD.GPSLatitude) and not e["0th"].get(piexif.ImageIFD.Rating))
check("the library file is byte-for-byte unchanged", sha(lib_b.read_bytes()) == snap[lib_b])

# ---- resized JPEG mode
out = tmp / "o4"
export([A, B], out, update_metadata=True, long_edge=60, quality=80)
fs = sorted(out.glob("*.jpg"))
ok = True
for f in fs:
    e = ex(f)
    w, h = images.dimensions(f)
    ok &= e["0th"].get(piexif.ImageIFD.Orientation) == 1 and e["Exif"].get(piexif.ExifIFD.PixelXDimension) == w and max(w, h) <= 60 and bool(e["0th"].get(piexif.ImageIFD.Make))
check("resized copies: EXIF carried over, Orientation 1, real pixel size (both photos)", len(fs) == 2 and ok)

# ---- ZIP
zp = tmp / "e.zip"
p = export([A, B], zp, update_metadata=True, as_zip=True, originals=True)
with zipfile.ZipFile(zp) as z:
    names = z.namelist()
    z.extractall(tmp / "unz")
fa = next((tmp / "unz").glob("a_edited*.jpg"))
check("ZIP export: copies carry the updated EXIF too", p.state == "done" and ex(fa)["0th"].get(piexif.ImageIFD.ImageDescription, b"").decode("utf-8").startswith("A caption"), names)
check("ZIP export: no temp files left in the archive, library untouched", all(not n.endswith(".tmp") for n in names) and sha(lib_a.read_bytes()) == snap[lib_a])

# ---- not JPEG / video / odd input
C = photo("c_png", ext=".png", shift=2)
setmeta(C, desc="png caption")
V = photo("d_video", shift=3)
con.execute("UPDATE photos SET is_video=1 WHERE id=?", (V,))
con.commit()
out = tmp / "o5"
p = export([C, V], out, update_metadata=True, originals=True)
check("a PNG is exported as is (no EXIF can be written; no crash) and a video too", p.state == "done" and (out / "c_png.png").exists() and not p.error, p.error)
check("...the PNG is byte-identical to the library file", (out / "c_png.png").read_bytes() == lib(C).read_bytes())
D = photo("e_badexif", shift=5)
bad = lib(D)
data = bad.read_bytes()
bad.write_bytes(data[:2] + b"\xff\xe1\x00\x10Exif\x00\x00garbage" + data[2:])         # a damaged EXIF segment in the original
setmeta(D, desc="after damage")
out = tmp / "o6"
p = export([D], out, update_metadata=True, long_edge=50)
f = next(out.glob("*.jpg"))
check("damaged EXIF in the original: the export still works and the catalog's values are written", p.state == "done" and ex(f)["0th"].get(piexif.ImageIFD.ImageDescription) == b"after damage", p.error)
# a missing original file is skipped like before, others continue
E = photo("f_gone", shift=90)
lib(E).unlink()
out = tmp / "o7"
p = export([E, B], out, update_metadata=True, originals=True)
check("a photo whose file is missing is skipped, the rest is exported", p.state == "done" and len(list(out.glob("*.jpg"))) == 1)

# ---- XMP: the edit settings
def xmp_of(out_dir, name):
    t = (out_dir / (name + ".xmp")).read_text("utf-8")
    root = ET.fromstring(t.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])
    return t, root


NS = {"xmp": "http://ns.adobe.com/xap/1.0/", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "photag": "https://github.com/giamat13/photag/ns/1.0/", "crs": "http://ns.adobe.com/camera-raw-settings/1.0/"}
F = photo("g_alledits", shift=70)
setmeta(F, desc='quote " and <tag> & ünï', tags=('a"b', "c&d"))
edit(F, OPS)
out = tmp / "o8"
export([F, B], out, update_metadata=True, originals=True)
name_f = next(p.name for p in out.glob("g_alledits*") if p.suffix == ".jpg")
t, root = xmp_of(out, name_f)
desc = root.find(".//rdf:Description", NS)
attr = lambda ns, k: desc.get("{%s}%s" % (NS[ns], k))
check("an UNEDITED original with edits: the XMP is well-formed XML even with quotes, <, & and unicode in caption and keywords", desc is not None)
check("the exact settings travel in photag:EditSettings (JSON), version 1", json.loads(attr("photag", "EditSettings")) == OPS and attr("photag", "EditVersion") == "1")
check("a best-effort crs: set exists: exposure +0.70, contrast +30 (from the 1.3 factor), highlights -40, saturation -20 (from 0.8), vibrance +30",
      attr("crs", "Exposure2012") == "+0.70" and attr("crs", "Contrast2012") == "+30" and attr("crs", "Highlights2012") == "-40" and attr("crs", "Saturation") == "-20" and attr("crs", "Vibrance") == "+30",
      {k.split("}")[1]: v for k, v in desc.attrib.items() if "camera-raw" in k})
check("...clarity, sharpness, vignette and shadows too; temperature / sepia / flip have no counterpart and are not written to crs:",
      attr("crs", "Clarity2012") == "+20" and attr("crs", "Sharpness") == "40" and attr("crs", "PostCropVignetteAmount") == "-20" and attr("crs", "Shadows2012") == "+25"
      and attr("crs", "Temperature") is None and attr("crs", "Sepia") is None)
check("...the crop is written (no rotation) as fractions", attr("crs", "HasCrop") == "True" and attr("crs", "CropLeft") == "0.100000" and attr("crs", "CropBottom") == "0.900000")
check("the sidecar still has the usual fields (rating 4, caption)", attr("xmp", "Rating") == "4" and 'quote "' in t.replace("&quot;", '"'))
check("the keywords with quotes survived as text", 'a"b' in [li.text for li in root.iter("{%s}li" % NS["rdf"])] and "c&d" in [li.text for li in root.iter("{%s}li" % NS["rdf"])])
# rotation: no crop in crs, exact settings still there
G = photo("h_rot", shift=10)
edit(G, {"rotate": 90, "crop": [0, 0, 0.5, 1], "exposure": -0.5})
out = tmp / "o9"
export([G], out, update_metadata=True, originals=True)
t, root = xmp_of(out, next(p.name for p in out.glob("*.jpg")))
d = root.find(".//rdf:Description", NS)
check("a rotated photo: the crop is NOT written to crs: (its meaning would differ), the exact settings are", d.get("{%s}HasCrop" % NS["crs"]) is None and "rotate" in json.loads(d.get("{%s}EditSettings" % NS["photag"])))
check("...and the safe ones are (exposure -0.50)", d.get("{%s}Exposure2012" % NS["crs"]) == "-0.50")

# the rules: when are the settings written
out = tmp / "o10"
export([F], out, update_metadata=True)
check("an edited photo exported AS IT LOOKS gets no edit settings (they are already in the pixels; a second application would double the edit) and no sidecar",
      not list(out.glob("*.xmp")))
out = tmp / "o11"
export([F], out, update_metadata=True, xmp_sidecar=True)
t = next(out.glob("*.xmp")).read_text("utf-8")
check("...not even when an XMP sidecar is asked for", "photag:EditSettings" not in t and "crs:" not in t)
out = tmp / "o12"
export([F], out, originals=True, xmp_sidecar=True)
check("without the new option an XMP sidecar has no edit settings (as before)", "photag:EditSettings" not in next(out.glob("*.xmp")).read_text("utf-8"))
out = tmp / "o13"
export([B], out, update_metadata=True, originals=True)
check("an unedited photo gets no sidecar from the option alone, and no edit settings", not list(out.glob("*.xmp")))
out = tmp / "o14"
export([B], out, update_metadata=True, originals=True, xmp_sidecar=True)
check("...and with a sidecar asked for, still no edit settings", "photag:" not in next(out.glob("*.xmp")).read_text("utf-8"))
H = photo("i_neutral", shift=20)
edit(H, {"exposure": 0, "brightness": 1.0, "crop": [0, 0, 1, 1]})
out = tmp / "o15"
export([H], out, update_metadata=True, originals=True, xmp_sidecar=True)
check("a photo whose settings are all at zero counts as unedited", "photag:" not in next(out.glob("*.xmp")).read_text("utf-8"))
con.execute("UPDATE photos SET edit_ops='{not json' WHERE id=?", (H,))
con.commit()
out = tmp / "o16"
p = export([H], out, update_metadata=True, originals=True, xmp_sidecar=True)
check("damaged settings text does not stop the export", p.state == "done" and not p.error, p.error)

# ---- the mapping on its own
cs = importer._crs_settings
check("crs mapping: nothing set -> nothing written", cs({}) == {} and cs({"exposure": 0, "contrast": 1.0, "saturation": 1.0, "crop": [0, 0, 1, 1]}) == {})
check("crs mapping: contrast 0.8 -> -29, 1.0 -> none, 1.4 -> +40", cs({"contrast": 0.8})["Contrast2012"] == "-29" and cs({"contrast": 1.4})["Contrast2012"] == "+40")
check("crs mapping: grayscale -> ConvertToGrayscale", cs({"grayscale": True}) == {"ConvertToGrayscale": "True"})
check("crs mapping: rotate 180 (multiple of 180 but not 360) -> no crop", "HasCrop" not in cs({"rotate": 180, "crop": [0, 0, .5, 1]}) and "HasCrop" in cs({"rotate": 0, "crop": [0, 0, .5, 1]}))

# ---- the page's option reaches the export (API field)
src = (ROOT / "app" / "server.py").read_text("utf-8")
check("the API has the update_metadata field and passes it on", "update_metadata: bool = False" in src and "partial(importer.run_export, update_metadata=body.update_metadata)" in src)
js = (ROOT / "app" / "ui" / "app.js").read_text("utf-8")
check("the export dialog has the checkbox, remembers it, hides it for the HTML gallery and sends it", all(x in js for x in ("id=\"ex-meta\"", "meta:false", "ex-meta-row", "update_metadata:meta")))

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
