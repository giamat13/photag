"""Test: importer.run_social_import() reads an Instagram/Facebook "Download Your Information" ZIP --
every photo/video in it, with caption and date recovered from the export's own JSON when present.

    py -3.12 tools/test_social_import.py
"""
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_social_import_test_"))
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


def jpeg_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(buf, "JPEG")
    return buf.getvalue()


zip_path = tmp / "instagram-export.zip"
caption = "Sunset at the beach"
caption_latin1 = caption.encode("utf-8").decode("latin1")  # simulates the export's mis-encoding
with zipfile.ZipFile(zip_path, "w") as zf:
    zf.writestr("media/posts/202401/beach.jpg", jpeg_bytes((10, 20, 30)))
    zf.writestr("media/posts/202401/noinfo.jpg", jpeg_bytes((40, 50, 60)))   # not referenced by any JSON
    zf.writestr("your_instagram_activity/media/posts_1.json", json.dumps({
        "ig_timeline_activity": [{"media": [{
            "uri": "media/posts/202401/beach.jpg",
            "creation_timestamp": 1704110400,
            "title": caption_latin1,
        }]}]
    }))

config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()

prog_type = type("P", (), {"done": 0, "total": 0, "cancel": False, "state": None, "error": None, "extra": {},
                            "say": lambda self, *a, **k: None,
                            "say_parts": lambda self, *a, **k: None,
                            "fail": lambda self, msg, **v: setattr(self, "error", msg.format(**v))})
prog = prog_type()
importer.run_social_import(str(zip_path), prog)

check("import finished without failing", prog.state != "error", prog.error)
check("both media files were imported", con.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"] == 2)
r = con.execute("SELECT taken_at, description FROM photos WHERE filename='beach.jpg'").fetchone()
check("the photo with JSON metadata was found", r is not None)
check("its date was recovered from the export JSON", r and r["taken_at"] == 1704110400, dict(r) if r else None)
check("its caption was recovered and the Latin-1 mis-encoding was undone", r and r["description"] == caption, r["description"] if r else None)
r2 = con.execute("SELECT description FROM photos WHERE filename='noinfo.jpg'").fetchone()
check("a file with no matching JSON entry still imports, with no caption", r2 is not None and r2["description"] is None)

# importing the same export again must not duplicate anything
prog2 = prog_type()
importer.run_social_import(str(zip_path), prog2)
check("re-importing the same export adds no duplicates", con.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"] == 2)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
