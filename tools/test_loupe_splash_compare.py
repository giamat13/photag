"""UI test: issue #30 (a loading screen that says what is loading), issue #31 (right-click on the picture shown large: loupe, slideshow)
and the Compare bug (after picking another photo it compared with the photo left behind). Throw-away profile.

    py -3.12 tools/test_loupe_splash_compare.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PORT = 8808
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_loupe_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
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


for i, c in enumerate(((200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30), (30, 200, 200))):
    Image.new("RGB", (300 + i * 10, 200), c).save(tmp / "src" / f"p{i + 1}.jpg")

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
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted((tmp / "src").iterdir())], "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error", "idle"):
        time.sleep(0.3)

    with sync_playwright() as pw:
        br = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # ---- #30: the loading screen (the photo list is made slow on purpose)
        def slow(route):
            pg.wait_for_timeout(1500)
            route.continue_()
        pg.route("**/api/photos?*", slow)
        pg.goto(APP + "/", wait_until="commit")
        pg.wait_for_selector("#splash", state="attached", timeout=10000)
        pg.wait_for_function("document.querySelector('#sp-msg') && /photos|Loading/.test(document.querySelector('#sp-msg').textContent)", timeout=10000)
        msg = pg.inner_text("#sp-msg")
        check("a loading screen is shown and says what is loading", pg.evaluate("!!document.querySelector('#splash') && !document.querySelector('#splash').classList.contains('gone')") and "Loading" in msg, msg)
        pg.wait_for_selector(".cell", timeout=30000)
        pg.wait_for_function("!document.querySelector('#splash')", timeout=10000)
        check("it goes away when the grid is ready", pg.evaluate("!document.querySelector('#splash')"))
        pg.unroute("**/api/photos?*")
        pg.wait_for_timeout(600)
        pg.evaluate("closeModal()")

        # ---- #31: right-click on the picture shown large
        ids = pg.evaluate("S.all.map(p => p.id)")
        pg.click(f".cell[data-id='{ids[0]}']")
        pg.keyboard.press("e")
        pg.wait_for_selector("#v-loupe:not(.hidden) #loupe-media img, #v-loupe:not(.hidden) #loupe-media video", timeout=10000)
        pg.click("#loupe-media", button="right", position={"x": 300, "y": 200})
        pg.wait_for_selector("#menu-pop:not(.hidden)", timeout=5000)
        m = pg.inner_text("#menu-pop")
        check("right-click on the picture in the loupe opens the photo menu", "Add to Quick Collection" in m and "Copy picture" in m, m[:120].replace("\n", " | "))
        pg.mouse.click(5, 5)
        pg.keyboard.press("Escape")
        pg.evaluate("setView('grid')")

        # slideshow
        pg.evaluate("ssStart(); 0")
        pg.wait_for_selector("#slideshow:not(.hidden)", timeout=5000)
        pg.click("#slideshow", button="right", position={"x": 400, "y": 300})
        pg.wait_for_selector("#menu-pop:not(.hidden)", timeout=5000)
        check("right-click in the slideshow opens the menu, on top of it", pg.evaluate("(() => { const m = document.querySelector('#menu-pop'); const z = +getComputedStyle(m).zIndex, s = +getComputedStyle(document.querySelector('#slideshow')).zIndex; return z > s; })()"))
        pg.mouse.click(5, 5)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)

        # ---- Compare: the candidate follows the selection
        pg.evaluate("setView('grid')")
        pg.click(f".cell[data-id='{ids[0]}']")
        pg.click(f".cell[data-id='{ids[1]}']", modifiers=["Control"])
        pg.keyboard.press("c")
        pg.wait_for_selector("#v-compare .cmp", timeout=5000)
        first = pg.evaluate("[...document.querySelectorAll('#v-compare .cmp')].map(c => +c.dataset.id)")
        check("Compare shows the selected photo and the other selected photo", len(first) == 2 and set(first) == {ids[0], ids[1]}, first)
        # back to the grid, pick a different photo on its own, compare again
        pg.keyboard.press("g")
        pg.click(f".cell[data-id='{ids[3]}']")
        pg.keyboard.press("c")
        pg.wait_for_selector("#v-compare .cmp", timeout=5000)
        second = pg.evaluate("[...document.querySelectorAll('#v-compare .cmp')].map(c => +c.dataset.id)")
        check("after picking another photo, Compare does not use the photo left behind", second[0] == ids[3] and ids[0] not in second and ids[1] not in second, (second, ids))
        check("no script errors", not errs, errs[:3])
        br.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
