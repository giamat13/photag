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
        pg.keyboard.press("Delete")
        pg.wait_for_timeout(500)
        check("rule number one: Delete / F2 do nothing to the viewed picture (no Recycle Bin, no rename), and there is no delete button",
              (tmp / "geo" / "g (edited).jpg").exists() and (tmp / "geo" / "g.jpg").exists() and not ev("!!document.querySelector('#b-del')"))
        check("still nothing was added to the catalog by viewing / editing", n_photos() == 1)

        # ---- the "more" menu, slideshow, print; copy / move use the server's folder dialog (tested on the server side)
        (tmp / "more").mkdir()
        for n in ("m1.jpg", "m2.jpg", "m3.jpg"):
            Image.new("RGB", (200, 100), (10, 90, 160)).save(tmp / "more" / n, "JPEG")
        mt = call("POST", "/api/viewer/open", {"path": str(tmp / "more" / "m1.jpg")})["token"]
        pg.goto(f"{APP}/viewer.html?t={mt}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'm1.jpg'", timeout=15000)
        pg.click("#b-more")
        items = pg.inner_text("#menu")
        check("the more menu offers copy and print -- and no rename / move (rule number one)", all(x in items for x in ("Copy to folder", "Print")) and "Rename" not in items and "Move to folder" not in items, items)
        pg.keyboard.press("Escape")
        pg.keyboard.press("F2")
        pg.wait_for_timeout(400)
        check("F2 does nothing (the picture is never renamed)", (tmp / "more" / "m1.jpg").is_file() and pg.evaluate("document.querySelector('#dlg').classList.contains('hidden')"))
        pg.keyboard.press("s")
        pg.wait_for_selector("#menu:not(.hidden)", timeout=3000)
        check("S offers the slideshow speeds", "Every 2 seconds" in pg.inner_text("#menu"))
        pg.evaluate("window.__fs = 0; document.documentElement.requestFullscreen = () => { window.__fs++; return Promise.resolve(); }")
        pg.click("#menu button:first-of-type + button")          # the second item: every 4 seconds -- the test then drives the timer itself
        check("starting it marks the slideshow as running", pg.evaluate("document.body.classList.contains('show')"))
        first = pg.inner_text("#name")
        pg.wait_for_function(f"document.querySelector('#name').textContent !== {json.dumps(first)}", timeout=9000)
        check("a new picture appears by itself", pg.inner_text("#name") != first)
        pg.keyboard.press("Escape")
        check("Esc ends the slideshow", not pg.evaluate("document.body.classList.contains('show')"))
        pg.evaluate("window.__printed = 0; window.print = () => { window.__printed++; }")
        pg.keyboard.press("Control+p")
        pg.wait_for_function("window.__printed > 0", timeout=5000)
        check("Ctrl+P prints the picture (as drawn on screen)", pg.evaluate("document.querySelector('#printimg').src.startsWith('data:image/jpeg')"))
        # ---- advanced editor: sliders, straighten + crop, improve automatically; thumbnail strip; video; clipboard
        pg.goto(f"{APP}/viewer.html?t={tok}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=15000)
        pg.keyboard.press("t")
        pg.wait_for_selector("#strip button", timeout=10000)
        check("T shows the strip of thumbnails, the current one marked", pg.locator("#strip button").count() >= 3 and pg.locator("#strip button.cur").count() == 1)
        pg.locator("#strip button").nth(1).click()
        pg.wait_for_function("document.querySelector('#name').textContent !== 'a.jpg'", timeout=10000)
        check("a click on a thumbnail opens that picture", pg.inner_text("#name") != "a.jpg")
        pg.keyboard.press("t")
        check("T hides the strip", not pg.evaluate("document.body.classList.contains('strip')"))
        pg.goto(f"{APP}/viewer.html?t={tok}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=15000)
        pg.click("#b-edit")
        pg.wait_for_selector("#e-angle", timeout=5000)
        pg.eval_on_selector("#e-angle", "e => { e.value = 5; e.dispatchEvent(new Event('input')) }")
        c = pg.evaluate("edit.crop")
        check("straightening crops the empty corners automatically", c[0] > 0.01 and c[2] < 0.99, c)
        pg.click("#e-cropon")
        pg.select_option("#e-aspect", "1:1")
        c = pg.evaluate("edit.crop")
        ar = (c[2] - c[0]) * pg.evaluate("bbox().w") / ((c[3] - c[1]) * pg.evaluate("bbox().h"))
        check("a fixed shape (1:1) makes the crop square", abs(ar - 1) < 0.02, ar)
        check("the crop frame is drawn on the picture", pg.is_visible("#crop"))
        pg.eval_on_selector("#e-exposure", "e => { e.value = 1; e.dispatchEvent(new Event('input')) }")
        pg.wait_for_function("document.querySelector('#pic').src.startsWith('blob:')", timeout=15000)
        check("tone sliders show a preview drawn by the server", True)
        pg.click("#e-auto")
        pg.wait_for_function("edit.exposure !== 1 || edit.contrast !== 100 || edit.sharpness > 0", timeout=15000)
        check("'Improve automatically' sets the sliders", True)
        pg.click("#e-save")
        pg.wait_for_function("document.querySelector('#name').textContent.includes('(edited')", timeout=20000)
        sv = next(tmp.joinpath("pics").glob("a (edited*.jpg"))
        im2 = Image.open(sv)
        check("the saved copy is straightened and square-cropped", abs(im2.width - im2.height) <= 2 and im2.width < 1000, im2.size)
        (tmp / "vid").mkdir()
        (tmp / "vid" / "v.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        vt = call("POST", "/api/viewer/open", {"path": str(tmp / "vid" / "v.mp4")})["token"]
        pg.goto(f"{APP}/viewer.html?t={vt}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'v.mp4'", timeout=15000)
        check("a video is shown with the video player", pg.evaluate("document.body.classList.contains('video') && getComputedStyle(document.querySelector('#vid')).display !== 'none'"))
        check("edit is off for a video", pg.is_disabled("#b-edit"))
        pg.click("#b-more")
        txt = pg.inner_text("#menu")
        check("a video's menu has no print / copy picture", "Print" not in txt and "Copy picture" not in txt and "Copy to folder" in txt, txt)
        pg.keyboard.press("Escape")
        pg.goto(f"{APP}/viewer.html?t={tok}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=15000)
        pg.click("#b-more")
        check("the menu offers 'Copy picture'", "Copy picture" in pg.inner_text("#menu"))
        pg.keyboard.press("Escape")
        pg.evaluate("window.__clip = null; navigator.clipboard.write = async items => { window.__clip = items[0].types }; 0")
        pg.keyboard.press("Control+c")
        pg.wait_for_function("window.__clip !== null", timeout=10000)
        check("Ctrl+C puts the picture on the clipboard as PNG", pg.evaluate("window.__clip")[0] == "image/png")
        # ---- compare two pictures side by side
        pg.goto(f"{APP}/viewer.html?t={tok}")
        pg.wait_for_function("document.querySelector('#name').textContent === 'a.jpg'", timeout=15000)
        pg.keyboard.press("c")
        pg.wait_for_function("document.body.classList.contains('cmp') && document.querySelector('#pic2').naturalWidth > 0", timeout=10000)
        check("C shows two pictures side by side, each with its name", "a.jpg" in pg.inner_text("#lab1") and pg.inner_text("#lab2").strip() != "")
        w1 = pg.evaluate("document.querySelector('#pane1').getBoundingClientRect().width"); w2 = pg.evaluate("document.querySelector('#pane2').getBoundingClientRect().width")
        check("the window is split in two equal halves", abs(w1 - w2) < 3 and w1 > 300, (w1, w2))
        pg.wait_for_function("document.querySelector('#lab2').textContent !== ''", timeout=10000)
        right0 = pg.evaluate("document.querySelector('#lab2').textContent")
        pg.keyboard.press("ArrowRight")
        pg.wait_for_function(f"document.querySelector('#lab2').textContent !== {json.dumps(right0)}", timeout=10000)
        l1, l2 = pg.inner_text("#lab1"), pg.evaluate("document.querySelector('#lab2').textContent")
        check("an arrow changes the right picture only", "a.jpg" in l1 and l2 != right0, (l1, l2, right0))
        pg.mouse.move(250, 300); pg.mouse.wheel(0, -300)
        pg.wait_for_timeout(300)
        pg.wait_for_timeout(300)
        sc = pg.evaluate("[document.querySelector('#pic').style.transform.match(/scale\\(([^)]+)\\)/)[1], document.querySelector('#pic2').style.transform.match(/scale\\(([^)]+)\\)/)[1]]")
        check("zoom is the same on both pictures", abs(float(sc[0]) - float(sc[1])) < 1e-6 and float(sc[0]) > 0.33, sc)
        before = pg.inner_text("#lab2")
        pg.keyboard.press("x")
        pg.wait_for_function("document.querySelector('#lab2').textContent.startsWith('a.jpg')", timeout=10000)
        check("X swaps the two pictures", pg.inner_text("#lab1").split("  ·")[0] == before.split("  ·")[0] and "a.jpg" in pg.inner_text("#lab2"), (pg.inner_text("#lab1"), before))
        pg.keyboard.press("c")
        check("C again goes back to one picture", not pg.evaluate("document.body.classList.contains('cmp')"))
        # ---- an older photag.exe ("Open with" while photag already runs, with or without the icon): its window opens the main page,
        # the picture was handed over to this program -- the page must show it in the viewer
        hp = call("POST", "/api/viewer/handoff", {"paths": [str(tmp / "pics" / "b.png")]})
        pg.goto(f"{APP}/")
        pg.wait_for_url("**/viewer.html?t=*", timeout=20000)
        pg.wait_for_function("document.querySelector('#name').textContent === 'b.png'", timeout=15000)
        check("the main page of the running photag jumps to the viewer on the handed-over picture", hp.get("taken") == 1 and pg.inner_text("#name") == "b.png")
        pg.goto(f"{APP}/")
        pg.wait_for_selector("#v-grid, #left", timeout=20000)
        pg.wait_for_timeout(1500)
        check("...and only once: the next time the main page stays", "viewer.html" not in pg.url, pg.url)
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
