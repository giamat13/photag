"""UI test of the batch of issues, in a real browser: the Timeline stays the Timeline after the loupe (#61), the right-click menu works in the
Timeline (#64), a series made by hand from the right-click menu (#59), the language kept by the server when the window's own storage is gone (#66),
a person tagged by dragging a rectangle (#67), the wallpaper dialog (#55), the "full quality" export option (#62) and the AI button of the Develop
module (#46). Throw-away profile.

    py -3.12 tools/test_issue_batch_ui.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
PORT = 8893
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_batchui_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        if call("GET", f"/api/job/{name}")["state"] in ("done", "error"):
            return
        time.sleep(0.3)


src = tmp / "src"
for n in ("paris", "jerusalem", "telaviv_1", "telaviv_2"):
    Image.open(SAMPLES / f"{n}.jpg").convert("RGB").save(src / f"{n}.jpg", quality=92)

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())]})
    job("import")
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=999")}

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        b = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(800)

        def menu(top, item):
            pg.click(f"#menubar [data-menu] >> text={top}")
            pg.click(f"#menu-pop .mi:has-text('{item}')")

        # ---- #61 the Timeline stays the Timeline after the loupe
        pg.evaluate("setView('timeline')")
        pg.wait_for_selector("#tl-main .tl-c", timeout=10000)
        pg.dblclick("#tl-main .tl-c >> nth=0")
        pg.wait_for_function("S.view === 'loupe'", timeout=5000)
        check("a double click on a Timeline picture opens the loupe", pg.evaluate("S.view") == "loupe")
        pg.dblclick("#v-loupe .loupe-media img")
        pg.wait_for_function("S.view !== 'loupe'", timeout=5000)
        check("a double click on the loupe goes back to the Timeline, not to the Grid", pg.evaluate("S.view") == "timeline", pg.evaluate("S.view"))
        pg.dblclick("#tl-main .tl-c >> nth=0")
        pg.wait_for_function("S.view === 'loupe'", timeout=5000)
        pg.keyboard.press("Escape")
        pg.wait_for_function("S.view !== 'loupe'", timeout=5000)
        check("Escape in the loupe also goes back to the Timeline", pg.evaluate("S.view") == "timeline", pg.evaluate("S.view"))
        pg.keyboard.press("Escape")
        check("Escape in the Timeline leaves it where it is", pg.evaluate("S.view") == "timeline")
        pg.evaluate("setView('grid')")
        pg.wait_for_selector(".cell")
        pg.dblclick(".cell >> nth=0")
        pg.wait_for_function("S.view === 'loupe'", timeout=5000)
        pg.keyboard.press("Escape")
        pg.wait_for_function("S.view !== 'loupe'", timeout=5000)
        check("from the Grid the loupe still goes back to the Grid", pg.evaluate("S.view") == "grid", pg.evaluate("S.view"))

        # ---- #64 right click in the Timeline
        pg.evaluate("setView('timeline')")
        pg.wait_for_selector("#tl-main .tl-c")
        pg.click("#tl-main .tl-c >> nth=1", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)", timeout=5000)
        items = pg.locator("#menu-pop .mi").all_inner_texts()
        check("a right click on a Timeline picture opens the same menu as in the Grid", any("Add to Collection" in x for x in items) and any("Export" in x for x in items), items[:6])
        pg.keyboard.press("Escape")
        pg.mouse.click(5, 5)

        # ---- #59 a series made by hand
        pg.evaluate("setView('grid')")
        pg.wait_for_selector(".cell")
        pg.click(".cell >> nth=0")
        pg.click(".cell >> nth=1", modifiers=["Control"])
        ids = pg.evaluate("[...S.sel]")
        pg.click(".cell >> nth=1", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)")
        check("with two photos selected the menu offers a series with the count", pg.locator("#menu-pop .mi:has-text('Make a series (2×)')").count() == 1, pg.locator("#menu-pop .mi").all_inner_texts()[:12])
        pg.click("#menu-pop .mi:has-text('Make a series (2×)')")
        pg.wait_for_function(f"S.bursts && S.bursts.get({ids[0]}) && S.bursts.get({ids[0]}).n === 2", timeout=10000)
        check("both photos are a series now (the 2× badge)", pg.evaluate(f"S.bursts.get({ids[1]}).n") == 2)
        pg.click(".cell >> nth=1", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)")
        pg.click("#menu-pop .mi:has-text('Remove from the series')")
        pg.wait_for_function(f"!S.bursts.get({ids[1]})", timeout=10000)
        check("'Remove from the series' ends it", not pg.evaluate(f"!!S.bursts.get({ids[0]})"))

        # ---- #67 tag a person by dragging a rectangle
        pg.click(".cell >> nth=2")
        pid = pg.evaluate("S.act")
        pg.evaluate("tagPersonByHand()")
        pg.wait_for_selector("#ft-layer", timeout=5000)
        pg.wait_for_selector("#loupe-media img")
        bb = pg.locator("#loupe-media img").bounding_box()
        x0, y0 = bb["x"] + bb["width"] * 0.3, bb["y"] + bb["height"] * 0.2
        pg.mouse.move(x0, y0)
        pg.mouse.down()
        pg.mouse.move(x0 + bb["width"] * 0.2, y0 + bb["height"] * 0.3, steps=5)
        pg.mouse.up()
        pg.wait_for_selector("#pb-in", timeout=5000)
        check("after the rectangle the program asks who it is", "Who is this?" in pg.inner_text("#modal-box h3"))
        pg.fill("#pb-in", "Dana")
        pg.click("#pb-ok")
        for _ in range(40):
            d = call("GET", f"/api/photo/{pid}")
            if d["faces"]:
                break
            time.sleep(0.25)
        f = d["faces"][0]
        check("the person is saved on the photo, inside the rectangle that was drawn", d["people"][0]["name"] == "Dana" and f["x1"] < f["x2"] and f["y1"] < f["y2"] and f["x1"] > 0, f)
        check("the drawing layer is gone", pg.locator("#ft-layer").count() == 0)

        # ---- #55 the wallpaper dialog
        menu("Library", "Desktop wallpaper")
        pg.wait_for_selector("#modal:not(.hidden)")
        txt = pg.inner_text("#modal-box")
        check("the Library menu has the wallpaper dialog (on this system it says Windows only)", "Desktop wallpaper" in txt and ("Windows only" in txt or "Change every" in txt), txt[:120])
        pg.keyboard.press("Escape")
        pg.evaluate("setView('grid')")
        pg.wait_for_selector(".cell")

        # ---- #62 export: full quality with the edits and metadata
        pg.click(".cell >> nth=0")
        pg.keyboard.press("Control+Shift+E")
        pg.wait_for_selector("input[name=ex-mode][value=full]", timeout=5000)
        check("the export dialog has 'Full quality, with the edits and all the metadata written into the file'", pg.locator("#modal-box:has-text('all the metadata')").count() == 1)
        pg.keyboard.press("Escape")

        # ---- #46 the AI button in Develop
        check("the Develop module has an AI button next to Auto", pg.locator("#btn-dev-ai").count() == 1 and pg.locator("#btn-dev-auto").count() == 1)

        # ---- #66 the language comes back from the server when the window's storage is gone
        call("POST", "/api/ui-lang", {"lang": "he"})
        pg.evaluate("localStorage.removeItem('pm.lang')")
        pg.reload()
        pg.wait_for_selector(".cell", timeout=20000)
        check("after the window forgot its language the server's choice is used", pg.evaluate("I18N.lang") == "he" and pg.evaluate("document.documentElement.dir") == "rtl", pg.evaluate("I18N.lang"))
        pg.evaluate("setLanguage('en')")
        pg.wait_for_function("I18N.lang === 'en'", timeout=10000)
        check("picking a language is also told to the server", call("GET", "/api/ui-lang")["lang"] == "en", call("GET", "/api/ui-lang"))

        check("no JavaScript errors", not errs, errs[:3])
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
