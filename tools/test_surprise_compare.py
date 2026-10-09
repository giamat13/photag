"""UI test of: the "Surprise Me" button / Shift+R (a random photo from the current list, never the same one twice in a row, only
from what the filter shows) and the Compare view's synchronized zoom and pan (wheel zooms both pictures together, dragging moves
both, a new pair starts at 100% again). Throw-away profile.

    py -3.12 tools/test_surprise_compare.py
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

ROOT = Path(__file__).resolve().parent.parent
PORT = 8787
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_surprise_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")


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
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir()) if p.suffix.lower() == ".jpg"]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)

        # ---- Surprise Me
        check("the Grid toolbar has a Surprise Me button", pg.locator('#toolbar [data-t="surprise"]').count() == 1)
        picks = []
        for _ in range(8):
            pg.evaluate("setView('grid')")
            pg.click('#toolbar [data-t="surprise"]')
            pg.wait_for_timeout(150)
            picks.append(pg.evaluate("S.act"))
            assert pg.evaluate("S.view") == "loupe"
        check("it opens a photo in the Loupe each time", all(p is not None for p in picks) and pg.evaluate("S.view") == "loupe", picks)
        check("it never picks the same photo twice in a row", all(a != b for a, b in zip(picks, picks[1:])), picks)
        check("over several tries it visits more than one photo", len(set(picks)) > 2, len(set(picks)))
        pg.evaluate("setView('grid')")
        before = pg.evaluate("S.act")
        pg.keyboard.press("Shift+R")
        pg.wait_for_timeout(200)
        check("Shift+R does the same", pg.evaluate("S.view") == "loupe" and pg.evaluate("S.act") != before)

        # only from what the filter shows
        pg.evaluate("setView('grid')")
        subset = pg.evaluate("S.list.slice(0, 2).map(p => p.id)")
        call("PATCH", "/api/photos", {"ids": subset, "flag": 1})
        pg.evaluate("reloadAll().then(() => { S.F.flags = new Set(['pick']); applyFilter(); })")
        pg.wait_for_timeout(700)
        n = pg.evaluate("S.list.length")
        seen = set()
        for _ in range(8):
            pg.evaluate("setView('grid')")
            pg.click('#toolbar [data-t="surprise"]')
            pg.wait_for_timeout(120)
            seen.add(pg.evaluate("S.act"))
        check("with a filter on, it only picks from what is shown", n == 2 and seen <= set(subset) and len(seen) >= 1, (n, sorted(seen), subset))
        pg.evaluate("S.F.flags = new Set(); applyFilter(); setView('grid')")
        pg.wait_for_timeout(300)

        # ---- Compare: synchronized zoom and pan
        pg.evaluate("setView('grid')")
        pg.click(".cell >> nth=0")
        pg.click(".cell >> nth=1", modifiers=["Control"])
        pg.keyboard.press("c")
        pg.wait_for_selector("#v-compare .cmp img")
        pg.wait_for_timeout(500)
        check("two photos are compared side by side", pg.locator("#v-compare .cmp img").count() == 2 and pg.evaluate("S.view") == "compare")
        check("it starts at 100%", pg.evaluate("CMPZ.scale") == 1 and "zoomed" not in (pg.get_attribute("#v-compare", "class") or ""))
        box = pg.locator("#v-compare .cmp >> nth=0").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pg.mouse.move(cx, cy)
        for _ in range(4):
            pg.mouse.wheel(0, -200)
            pg.wait_for_timeout(60)
        sc = pg.evaluate("CMPZ.scale")
        check("the wheel over one picture zooms in", sc > 1.5, sc)
        t0, t1 = pg.evaluate("[...document.querySelectorAll('#v-compare .cmp img')].map(i => i.style.transform)")
        import re
        scales = [float(re.search(r"scale\(([\d.]+)\)", t or "").group(1)) if re.search(r"scale\(([\d.]+)\)", t or "") else 0 for t in (t0, t1)]
        check("both pictures got the same zoom", t0 == t1 and abs(scales[0] - sc) < 0.001, (t0, t1))
        check("the zoom level is shown", "%" in pg.inner_text("#v-compare .cmp-zoom") and pg.inner_text("#v-compare .cmp-zoom") != "100%", pg.inner_text("#v-compare .cmp-zoom"))
        pg.mouse.down()
        pg.mouse.move(cx + 60, cy + 40, steps=5)
        pg.mouse.up()
        pg.wait_for_timeout(100)
        x, y = pg.evaluate("[CMPZ.x, CMPZ.y]")
        u0, u1 = pg.evaluate("[...document.querySelectorAll('#v-compare .cmp img')].map(i => i.style.transform)")
        check("dragging pans both pictures together", abs(x) > 20 and u0 == u1, (x, y))
        pg.click("#v-compare .cmp >> nth=1")
        pg.wait_for_timeout(200)
        check("a click while zoomed does not swap the pictures", pg.evaluate("CMPZ.scale") > 1.5)
        for _ in range(12):
            pg.mouse.wheel(0, 300)
            pg.wait_for_timeout(40)
        check("zooming back out returns to 100%", pg.evaluate("CMPZ.scale") == 1 and "zoomed" not in (pg.get_attribute("#v-compare", "class") or ""))
        pg.mouse.move(cx, cy)
        pg.mouse.wheel(0, -400)
        pg.wait_for_timeout(100)
        pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(300)
        pg.evaluate("compareStep(1)")
        pg.wait_for_timeout(300)
        check("a new pair starts at 100% again", pg.evaluate("CMPZ.scale") == 1, pg.evaluate("CMPZ.scale"))

        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
