"""Test: camera, lens and focal length from EXIF, usable in Advanced Search / smart collections (app/images.py exif_info,
app/smart.py query_ids, the /api/photos listing). Throw-away profile.

    py -3.12 tools/test_exif_search.py
"""
import os
import sys
import tempfile
from pathlib import Path

import piexif
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_exif_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, images, smart  # noqa: E402
from app.config import PATHS  # noqa: E402


def make_jpeg(path, make=None, model=None, lens=None, focal=None, focal35=None):
    Image.new("RGB", (40, 30), (120, 140, 160)).save(path, "JPEG")
    exif = {"0th": {}, "Exif": {}}
    if make:
        exif["0th"][piexif.ImageIFD.Make] = make.encode()
    if model:
        exif["0th"][piexif.ImageIFD.Model] = model.encode()
    if lens:
        exif["Exif"][piexif.ExifIFD.LensModel] = lens.encode()
    if focal:
        exif["Exif"][piexif.ExifIFD.FocalLength] = (int(round(focal * 10)), 10)
    if focal35:
        exif["Exif"][piexif.ExifIFD.FocalLengthIn35mmFilm] = focal35
    piexif.insert(piexif.dump(exif), str(path))


# ---- images.exif_info ------------------------------------------------------------------------------------------
a = tmp / "a.jpg"
make_jpeg(a, make="Canon", model="Canon EOS R5", lens="RF24-70mm F2.8 L IS USM", focal=50.0, focal35=50)
_, _, _, cam = images.exif_info(a)
check("camera make/model are read from EXIF", cam["make"] == "Canon" and cam["model"] == "Canon EOS R5", cam)
check("lens is read from EXIF", cam["lens"] == "RF24-70mm F2.8 L IS USM", cam)
check("focal length (real mm) is read and rounded to 1 decimal", cam["focal_length"] == 50.0, cam)
check("the 35mm-equivalent focal length is read too", cam["focal_length_35mm"] == 50, cam)
b = tmp / "b.jpg"
Image.new("RGB", (10, 10)).save(b, "JPEG")
_, _, _, cam_none = images.exif_info(b)
check("a photo with no EXIF camera data gives None for all fields, not an error", all(v is None for v in cam_none.values()), cam_none)

# ---- ingested into the catalog ---------------------------------------------------------------------------------
from app import importer  # noqa: E402

config.set_library_root(tmp / "lib")
PATHS.refresh()
con = db.init_db()
c = tmp / "c.jpg"
make_jpeg(c, make="SONY", model="ILCE-7M4", lens="FE 70-200mm F2.8 GM", focal=135.0)
d = tmp / "d.jpg"
make_jpeg(d, make="SONY", model="ILCE-7M4", lens="FE 24-70mm F2.8 GM II", focal=35.0)
e = tmp / "e.jpg"
make_jpeg(e, make="Canon", model="Canon EOS R5", lens="RF24-70mm F2.8 L IS USM", focal=24.0)
pid_c, _ = importer._ingest_file(con, c)
pid_d, _ = importer._ingest_file(con, d)
pid_e, _ = importer._ingest_file(con, e)
con.commit()
row = dict(con.execute("SELECT camera_make, camera_model, lens, focal_length FROM photos WHERE id=?", (pid_c,)).fetchone())
check("the camera columns are filled in when a photo is imported", row == {"camera_make": "SONY", "camera_model": "ILCE-7M4", "lens": "FE 70-200mm F2.8 GM", "focal_length": 135.0}, row)

# ---- smart.query_ids (Advanced Search criteria, server-side) ----------------------------------------------------
ids = smart.query_ids(con, {"cameras": ["ILCE-7M4"]})
check("filtering by camera model matches both Sony photos", set(ids) == {pid_c, pid_d}, ids)
ids = smart.query_ids(con, {"cameras": ["ilce-7m4"]})
check("...case-insensitively", set(ids) == {pid_c, pid_d}, ids)
ids = smart.query_ids(con, {"lenses": ["FE 24-70mm F2.8 GM II"]})
check("filtering by lens matches only that lens", ids == [pid_d], ids)
ids = smart.query_ids(con, {"minFocal": 50})
check("minFocal keeps only telephoto-or-longer shots", set(ids) == {pid_c}, ids)
ids = smart.query_ids(con, {"maxFocal": 35})
check("maxFocal keeps only wide-or-shorter shots", set(ids) == {pid_d, pid_e}, ids)
ids = smart.query_ids(con, {"cameras": ["ILCE-7M4"], "maxFocal": 40})
check("camera + focal length combine", ids == [pid_d], ids)
ids = smart.query_ids(con, {"cameras": ["Nikon Z9"]})
check("a camera nobody shot with matches nothing", ids == [], ids)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
