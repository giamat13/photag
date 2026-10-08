"""Test: the picture viewer ("Open with photag", app/viewer.py + /api/viewer/*) shows pictures WITHOUT adding anything to the catalog.

    py -3.12 tools/test_viewer.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PORT = 8791
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_viewer_test_"))
home = tmp / "h"
for d in ("a", "l", "h", "pics", "other"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(home), "HOME": str(home), "PYTHONIOENCODING": "utf-8"}
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(APP + path, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read()
            return r.status, (b if raw else json.loads(b or b"null")), r.headers
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b), e.headers
        except Exception:
            return e.code, b, e.headers


# pictures: IMG_2, IMG_10 (natural order), a PNG, a TIFF (converted), a text file, and a picture in another folder
pics = tmp / "pics"
for name, fmt in (("IMG_10.jpg", "JPEG"), ("img_2.JPG", "JPEG"), ("b.png", "PNG"), ("c.tif", "TIFF")):
    Image.new("RGB", (64, 48), (10, 120, 200)).save(pics / name, fmt)
(pics / "notes.txt").write_text("not a picture")
Image.new("RGB", (20, 20), (200, 0, 0)).save(tmp / "other" / "secret.jpg", "JPEG")

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    library = Path(call("GET", "/api/status")[1]["library_root"])
    db_file = library / "catalog.db"

    def photos_in_catalog():
        if not db_file.exists():
            return 0
        c = sqlite3.connect(db_file)
        try:
            return c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            c.close()

    def library_files():
        return sorted(str(p.relative_to(library)) for p in (library / "media").rglob("*") if p.is_file()) if (library / "media").exists() else []

    before_n, before_files = photos_in_catalog(), library_files()

    code, r, _ = call("POST", "/api/viewer/open", {"path": str(pics / "img_2.JPG")})
    check("opening a picture gives a token", code == 200 and len(r.get("token", "")) >= 16, r)
    tok = r["token"]
    code, info, _ = call("GET", f"/api/viewer/{tok}/info")
    check("info: name, size, dimensions", code == 200 and info["name"] == "img_2.JPG" and info["width"] == 64 and info["height"] == 48 and info["bytes"] > 0, info)
    check("the folder's pictures are in natural order (img_2, IMG_10, b.png... case ignored) and the text file is not one of them",
          info["count"] == 4, info)
    names = []
    t = info["first"]
    for _ in range(6):
        i = call("GET", f"/api/viewer/{t}/info")[1]
        names.append(i["name"])
        if not i["next"]:
            break
        t = i["next"]
    check("next walks the folder: b.png, c.tif, img_2.JPG, IMG_10.jpg", names == ["b.png", "c.tif", "img_2.JPG", "IMG_10.jpg"], names)
    check("first / last / index / prev are right", info["index"] == 3 and info["prev"] and info["next"] and call("GET", f"/api/viewer/{info['last']}/info")[1]["name"] == "IMG_10.jpg")
    code, body, hdr = call("GET", f"/api/viewer/{tok}/image", raw=True)
    check("a JPEG is sent as it is", code == 200 and hdr.get("content-type") == "image/jpeg" and body == (pics / "img_2.JPG").read_bytes())
    tif_tok = call("POST", "/api/viewer/open", {"path": str(pics / "c.tif")})[1]["token"]
    code, body, hdr = call("GET", f"/api/viewer/{tif_tok}/image", raw=True)
    check("a TIFF (a browser cannot show it) is sent as a JPEG made from it", code == 200 and hdr.get("content-type") == "image/jpeg" and body[:2] == b"\xff\xd8")
    check("the temp copy is the only thing written (the original is untouched)", (pics / "c.tif").stat().st_size > 0 and not list(pics.glob("*.jpg.tmp")))

    # ---- refusals
    check("a text file is refused", call("POST", "/api/viewer/open", {"path": str(pics / "notes.txt")})[0] == 400)
    check("a missing file is refused", call("POST", "/api/viewer/open", {"path": str(pics / "nope.jpg")})[0] == 400)
    check("a folder is refused", call("POST", "/api/viewer/open", {"path": str(pics)})[0] == 400)
    check("an unknown token gets nothing", call("GET", "/api/viewer/0123456789abcdef/info")[0] == 404 and call("GET", "/api/viewer/0123456789abcdef/image")[0] == 404)
    check("a path is not a token (no way to read an arbitrary file)", call("GET", f"/api/viewer/{str(tmp / 'other' / 'secret.jpg')}/image")[0] in (404, 405))
    check("a picture in another folder that was never opened cannot be asked for", all("secret" not in (call("GET", f"/api/viewer/{x}/info")[1] or {}).get("name", "") for x in (info["first"], info["last"])))
    pics_tok = call("POST", "/api/viewer/open", {"path": str(pics / "b.png")})[1]["token"]
    os.remove(pics / "b.png")
    check("a picture deleted meanwhile: a clear 404, not a crash", call("GET", f"/api/viewer/{pics_tok}/image")[0] == 404)

    # ---- the main point: nothing went into the catalog or the library
    check("after all that viewing the catalog has no new photo", photos_in_catalog() == before_n, (before_n, photos_in_catalog()))
    check("...and no file was copied into the library", library_files() == before_files, library_files())


    # ---- EXIF, location, edit (as a copy) and the Recycle Bin: still nothing goes into the catalog
    sys.path.insert(0, str(ROOT))
    from app import images as _im
    gps_jpg = pics / "geo.jpg"
    Image.new("RGB", (80, 60), (120, 120, 120)).save(gps_jpg, "JPEG")
    _im.embed_exif(gps_jpg, None, description="hello", lat=31.77, lng=35.21)
    gtok = call("POST", "/api/viewer/open", {"path": str(gps_jpg)})[1]["token"]
    code, ex, _ = call("GET", f"/api/viewer/{gtok}/exif")
    check("exif: the tags of the file and its location", code == 200 and ex["gps"] and abs(ex["gps"]["lat"] - 31.77) < 0.01 and abs(ex["gps"]["lng"] - 35.21) < 0.01 and "Image" in ex["exif"], ex)
    code, ex2, _ = call("GET", f"/api/viewer/{tok}/exif")
    check("exif: a picture without any has no location and no error", code == 200 and ex2["gps"] is None, ex2)
    check("exif: unknown token is 404", call("GET", "/api/viewer/0123456789abcdef/exif")[0] == 404)
    code, ed, _ = call("POST", f"/api/viewer/{gtok}/edit", {"brightness": 1.3, "contrast": 1.1, "saturation": 0.0, "grayscale": False, "rotate": 90})
    cp = pics / "geo (edited).jpg"
    check("edit: saved as a new file next to the original", code == 200 and cp.is_file() and ed["name"] == "geo (edited).jpg", ed)
    check("edit: the original is untouched", gps_jpg.stat().st_size > 0 and Image.open(gps_jpg).size == (80, 60))
    check("edit: rotated by 90 and the camera data / location carried over", Image.open(cp).size == (60, 80) and call("GET", f"/api/viewer/{ed['token']}/exif")[1]["gps"] is not None)
    code, ed2, _ = call("POST", f"/api/viewer/{gtok}/edit", {"brightness": 0.8})
    check("edit: a second copy gets its own name, never overwrites", code == 200 and ed2["name"] == "geo (edited 2).jpg" and (pics / "geo (edited 2).jpg").is_file(), ed2)
    code, ed3, _ = call("POST", f"/api/viewer/{gtok}/edit", {"brightness": 99, "contrast": -5, "rotate": 37})
    check("edit: absurd values are clamped, not trusted", code == 200 and (pics / "geo (edited 3).jpg").is_file())
    check("edit: an unknown token is 404", call("POST", "/api/viewer/0123456789abcdef/edit", {})[0] == 404)
    ttok = call("POST", "/api/viewer/open", {"path": str(pics / "geo (edited 3).jpg")})[1]["token"]
    code, tr, _ = call("POST", f"/api/viewer/{ttok}/trash")
    check("trash: the file leaves the folder (to the Recycle Bin) and the answer names the picture to show next", code == 200 and not (pics / "geo (edited 3).jpg").exists() and (tr["next"] is None or isinstance(tr["next"], str)), tr)
    check("trash: the token is gone", call("GET", f"/api/viewer/{ttok}/info")[0] == 404)
    check("trash: an unknown token is 404", call("POST", "/api/viewer/0123456789abcdef/trash")[0] == 404)
    check("none of this touched the catalog or the library", photos_in_catalog() == before_n and library_files() == before_files)

    # ---- only the explicit button adds it
    code, r, _ = call("POST", f"/api/viewer/{tok}/import")
    check("'Add to the library' adds the picture", code == 200 and r["added"] is True and photos_in_catalog() == before_n + 1, r)
    code, r, _ = call("POST", f"/api/viewer/{tok}/import")
    check("...once (the same picture again is 'already there')", code == 200 and r["added"] is False and photos_in_catalog() == before_n + 1, r)
    check("the viewer page and its script are served", call("GET", "/viewer.html", raw=True)[0] == 200 and call("GET", "/viewer.js", raw=True)[0] == 200)
    check("a program without an Origin (photag.exe itself) can open a file", call("POST", "/api/viewer/open", {"path": str(pics / "img_2.JPG")})[0] == 200)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

# the cross-site check needs raw headers
import http.client  # noqa: E402
n_fail_before = res.count(False)
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    c.request("POST", "/api/viewer/open", body=json.dumps({"path": str(pics / "img_2.JPG")}),
              headers={"Host": f"127.0.0.1:{PORT}", "Origin": "http://evil.example", "Content-Type": "application/json"})
    check("POST from another website is refused (403)", c.getresponse().status == 403)
    c.close()
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
