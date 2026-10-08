"""Test: the picture viewer page (app/ui/viewer.html) in a real browser: shows the picture, walks the folder with the keys and buttons,
zooms, rotates, and the 'Add to the library' button is the only way in. Edge on Windows, or PHOTAG_TEST_BROWSER=<chromium>.

    py -3.12 tools/test_viewer_ui.py
"""
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
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8792
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_viewer_ui_"))
for d in ("a", "l", "h", "pics"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")


Image.new("RGB", (1600, 1000), (30, 90, 160)).save(tmp / "pics" / "a.jpg", "JPEG")
Image.new("RGB", (300, 200), (160, 60, 30)).save(tmp / "pics" / "b.png", "PNG")
Image.new("RGB", (800, 500), (60, 160, 60)).save(tmp / "pics" / "c.tif", "TIFF")

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    lib = Path(call("GET", "/api/status")["library_root"])
    tok = call("POST", "/api/viewer/open", {"path": str(tmp / "pics" / "a.jpg")})["token"]

    def n_photos():
        c = sqlite3.connect(lib / "catalog.db")
        try:
            return c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            c.close()

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        br = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1000, "height": 700})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{APP}/viewer.html?t={tok}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=15000)
        ev = pg.evaluate
        check("the picture is on screen with its name", ev("document.querySelector('#pic').naturalWidth") == 1600 and ev("document.querySelector('#name').textContent") == "a.jpg")
        meta = ev("document.querySelector('#meta').textContent")
        check("the bar says 1 of 3, the size and the dimensions", "1 of 3" in meta and "1600 × 1000" in meta, meta)
        scale = lambda: ev("new DOMMatrix(getComputedStyle(document.querySelector('#pic')).transform).a")
        check("a big picture starts fitted into the window (smaller than its real size)", scale() < 1, scale())
        check("the first picture has no 'previous'", ev("document.querySelector('#b-prev').disabled") and not ev("document.querySelector('#b-next').disabled"))

        pg.keyboard.press("ArrowRight")
        pg.wait_for_function("document.querySelector('#name').textContent === 'b.png'", timeout=10000)
        check("Right shows the next picture of the folder", ev("document.querySelector('#pic').naturalWidth") == 300)
        check("a small picture is shown at its real size, not stretched", abs(scale() - 1) < 0.01, scale())
        pg.keyboard.press("ArrowRight")
        pg.wait_for_function("document.querySelector('#name').textContent === 'c.tif'", timeout=10000)
        check("a TIFF (converted on the fly) shows too", ev("document.querySelector('#pic').naturalWidth") == 800 and ev("document.querySelector('#b-next').disabled"))
        pg.keyboard.press("Home")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=10000)
        pg.click("#b-next")
        pg.wait_for_function("document.querySelector('#name').textContent === 'b.png'", timeout=10000)
        pg.click("#b-prev")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=10000)
        check("Home and the buttons work", True)

        s0 = scale()
        pg.click("#b-in")
        check("zoom in makes it bigger", scale() > s0 * 1.2, (s0, scale()))
        pg.keyboard.press("1")
        check("1 = actual size", abs(scale() - 1) < 0.01, scale())
        pg.mouse.move(500, 300)
        pg.mouse.wheel(0, -300)
        pg.wait_for_timeout(200)
        check("the mouse wheel zooms", scale() > 1.1, scale())
        pg.keyboard.press("0")
        check("0 = fit to the window again", abs(scale() - s0) < 0.01, scale())
        pg.keyboard.press("r")
        rot = ev("(()=>{const m=new DOMMatrix(getComputedStyle(document.querySelector('#pic')).transform);return Math.round(Math.atan2(m.b,m.a)*180/Math.PI)})()")
        check("R turns it 90 degrees (only on screen)", rot == 90, rot)
        check("rotated, it still fits the window", ev("(()=>{const r=document.querySelector('#pic').getBoundingClientRect(),s=document.querySelector('#stage').getBoundingClientRect();return r.width<=s.width+1&&r.height<=s.height+1})()"))
        pg.dblclick("#stage")
        check("double click toggles to the real size", abs(scale() - 1) < 0.01 or scale() < 1)

        check("nothing was added to the catalog by all of this", n_photos() == 0, n_photos())
        pg.click("#b-add")
        pg.wait_for_function("document.querySelector('#msg') && !document.querySelector('#msg').classList.contains('hidden')", timeout=10000)
        check("the Add button says it was added, and then the catalog has it", "Added" in ev("document.querySelector('#msg').textContent") and n_photos() == 1)

        # ---- information (EXIF), location, edit as a copy, Recycle Bin -- in a folder of its own
        sys.path.insert(0, str(ROOT))
        from app import images as _im
        (tmp / "geo").mkdir()
        Image.new("RGB", (400, 300), (100, 100, 100)).save(tmp / "geo" / "g.jpg", "JPEG")
        _im.embed_exif(tmp / "geo" / "g.jpg", None, description="hello", lat=31.77, lng=35.21)
        gt = call("POST", "/api/viewer/open", {"path": str(tmp / "geo" / "g.jpg")})["token"]
        pg.goto(f"{APP}/viewer.html?t={gt}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'g.jpg'", timeout=15000)
        pg.click("#b-info")
        pg.wait_for_function("document.querySelectorAll('#p-info .kv').length > 0", timeout=10000)
        check("the information panel lists the EXIF tags", "GPS" in ev("document.querySelector('#p-info').textContent") and not ev("document.querySelector('#panel').classList.contains('hidden')"))
        pg.click("#b-map")
        pg.wait_for_function("document.querySelector('#coords').textContent.includes('31.77')", timeout=10000)
        check("the location panel shows the map and the coordinates", ev("document.querySelector('#vmap').style.display") != "none" and "35.21" in ev("document.querySelector('#coords').textContent"))
        pg.click("#b-info")
        pg.click("#pclose")
        check("the panel closes", ev("document.querySelector('#panel').classList.contains('hidden')"))
        pg.click("#b-edit")
        pg.eval_on_selector("#e-bri", "e => { e.value = 130; e.dispatchEvent(new Event('input')); }")
        pg.check("#e-bw")
        flt = ev("document.querySelector('#pic').style.filter")
        check("editing shows the result live (brightness, black and white)", "brightness(1.3)" in flt and "grayscale(1)" in flt, flt)
        pg.click("#e-save")
        pg.wait_for_function("document.querySelector('#name').textContent === 'g (edited).jpg'", timeout=15000)
        check("'Save a copy' writes a new file and shows it; the original stays", (tmp / "geo" / "g (edited).jpg").is_file() and Image.open(tmp / "geo" / "g.jpg").size == (400, 300))
        check("the edited copy is shown without the live filter", ev("document.querySelector('#pic').style.filter") == "")
        pg.on("dialog", lambda d: d.accept())
        pg.keyboard.press("Delete")
        pg.wait_for_function("document.querySelector('#name').textContent === 'g.jpg'", timeout=15000)
        check("Delete moves the picture to the Recycle Bin and shows the next one", not (tmp / "geo" / "g (edited).jpg").exists() and (tmp / "geo" / "g.jpg").exists())
        check("still nothing was added to the catalog by viewing / editing / deleting", n_photos() == 1)
        check("no JavaScript errors", not errs, errs[:2])
        br.close()
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
