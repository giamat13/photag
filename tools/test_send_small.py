"""Test: "Send (small copy)", "Copy path" and the date taken from a file name.

    py -3.12 tools/test_send_small.py
"""
import calendar
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import images  # noqa: E402

PORT = 8797
APP = f"http://127.0.0.1:{PORT}"
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


g = lambda *a: calendar.timegm(a)
d = images.date_from_filename
check("IMG-20240501-WA0003.jpg", d("IMG-20240501-WA0003.jpg") == g(2024, 5, 1, 0, 0, 0))
check("PXL_20240501_101530.jpg has the time", d("PXL_20240501_101530.jpg") == g(2024, 5, 1, 10, 15, 30))
check("2024-05-01 10.15.30.png", d("2024-05-01 10.15.30.png") == g(2024, 5, 1, 10, 15, 30))
check("Screenshot_2024-05-01-10-15-30.png", d("Screenshot_2024-05-01-10-15-30.png") == g(2024, 5, 1, 10, 15, 30))
check("no date", d("holiday.jpg") is None and d("IMG_1234.jpg") is None)
check("a phone number is not a date", d("0521234567.jpg") is None)
check("month 13 is not a date", d("IMG-20241301.jpg") is None)
check("an old year is not believed", d("IMG-18500101.jpg") is None)

tmp = Path(tempfile.mkdtemp(prefix="photag_send_"))
for x in ("a", "l", "h"):
    (tmp / x).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            st = json.loads(urllib.request.urlopen(APP + "/api/status", timeout=2).read())
            break
        except Exception:
            time.sleep(0.5)
    root = Path(st["library_root"])
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    import piexif
    exif = piexif.dump({"GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((31, 1), (0, 1), (0, 1)),
                                piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: ((35, 1), (0, 1), (0, 1))}})
    Image.new("RGB", (4000, 3000), "red").save(media / "big.jpg", exif=exif)
    con = sqlite3.connect(root / "catalog.db")
    con.execute("INSERT INTO photos(id, sha256, filename, rel_path, taken_at, trashed) VALUES(1,'s1','big.jpg','big.jpg',1,0)")
    con.commit(); con.close()

    def call(method, url, body=None):
        req = urllib.request.Request(APP + url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=60).read())

    check("path endpoint", call("GET", "/api/photo/1/path")["path"] == str(media / "big.jpg"), call("GET", "/api/photo/1/path"))
    r = call("POST", "/api/send", {"ids": [1]})
    out = Path(tempfile.gettempdir()) / "photag-send" / "big.jpg"
    check("send makes one copy", r["count"] == 1 and out.is_file(), r)
    im = Image.open(out)
    check("the copy is 1600 px on the long edge", max(im.size) == 1600, im.size)
    check("the copy has no GPS", not im.getexif().get_ifd(0x8825))
    check("the original is untouched", Image.open(media / "big.jpg").size == (4000, 3000))
    try:
        call("POST", "/api/send", {"ids": []}); check("nothing selected is refused", False)
    except Exception:
        check("nothing selected is refused", True)
    if sys.platform == "win32":
        check("the files are on the clipboard", r["copied"] is True, r)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
shutil.rmtree(tmp, ignore_errors=True)
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
