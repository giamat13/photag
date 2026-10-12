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
        # ---- issue #49: Compare shows every selected photo, however many (more than four too)
        pg.keyboard.press("g")
        pg.wait_for_function("!document.querySelector('#v-grid').classList.contains('hidden')", timeout=5000)
        pg.click(f".cell[data-id='{ids[0]}']")
        pg.evaluate(f"(() => {{ for (const id of {ids}) S.sel.add(id); onSelChange(); }})()")      # select them all (Edge did not take the last Ctrl+clicks on the lower rows)
        pg.wait_for_function(f"S.sel.size === {len(ids)}", timeout=5000)
        pg.keyboard.press("c")
        try:
            pg.wait_for_function(f"document.querySelectorAll('#v-compare .cmp').length === {len(ids)}", timeout=5000)
        except Exception:
            print("DEBUG", pg.evaluate("({view: S.view, sel: [...S.sel], act: S.act, n: document.querySelectorAll('#v-compare .cmp').length, ids: S.all.map(p => p.id)})"))
            raise
        shown = pg.evaluate("[...document.querySelectorAll('#v-compare .cmp')].map(c => +c.dataset.id)")
        check(f"Compare shows all {len(ids)} selected photos", set(shown) == set(ids), shown)
        pg.click("#v-compare .cmp:not(.sel)")
        check("clicking one of them keeps all of them on screen", pg.evaluate("document.querySelectorAll('#v-compare .cmp').length") == len(ids))
        # ---- issue #43: Rank and Compare are ONE screen: Compare, with a score on every photo, the best first and marked
        pg.evaluate("setView('grid')")
        pg.wait_for_function("!document.querySelector('#v-grid').classList.contains('hidden')", timeout=5000)
        pg.evaluate(f"(() => {{ S.sel = new Set({ids[:3]}); S.act = {ids[0]}; onSelChange(); }})()")
        pg.evaluate("rankSelected()")
        pg.wait_for_function("S.view === 'compare' && document.querySelectorAll('#v-compare .cmp').length === 3", timeout=60000)
        pg.wait_for_function("[...document.querySelectorAll('#v-compare .cmp-score')].every(e => e.textContent.includes('/ 100'))", timeout=60000)
        check("Rank opens Compare with a score on every photo", True)
        best = pg.evaluate("+document.querySelector('#v-compare .cmp.best').dataset.id")
        check("the best photo is marked and shown first (the selection)", pg.evaluate("+document.querySelector('#v-compare .cmp.sel').dataset.id") == best and pg.evaluate("document.querySelectorAll('#v-compare .cmp.best').length") == 1)
        scores = pg.evaluate("[...document.querySelectorAll('#v-compare .cmp-score b.scv')].map(e => +e.textContent)")
        check("the best photo has the highest score", scores[0] == max(scores), scores)
        other = pg.evaluate("+document.querySelector('#v-compare .cmp:not(.sel)').dataset.id")
        pg.click(f"#v-compare .cmp[data-id='{other}']")
        pg.wait_for_function(f"S.act === {other}", timeout=5000)
        pg.wait_for_function("document.querySelectorAll('#v-compare .cmp').length === 3", timeout=5000)
        check("clicking another photo makes it the one you pick (the best stays marked), all three stay on screen", pg.evaluate("+document.querySelector('#v-compare .cmp.best').dataset.id") == best)
        pg.click("[data-t=cmppick]")
        pg.wait_for_function(f"S.byId.get({other}).flag === 1", timeout=10000)
        pg.wait_for_function(f"[...S.sel].filter(id => id !== {other}).every(id => S.byId.get(id).flag === -1)", timeout=10000)      # the rejects are saved right after the pick
        check("Compare: 'Pick this, reject the others' picks it and rejects the rest", True)
        # a plain Compare of two photos shows the scores too
        pg.evaluate("setView('grid')")
        pg.evaluate(f"selectOnly({ids[0]})")
        pg.evaluate("setView('compare')")
        pg.wait_for_function("document.querySelectorAll('#v-compare .cmp').length === 2 && [...document.querySelectorAll('#v-compare .cmp-score')].every(e => e.textContent.includes('/ 100'))", timeout=60000)
        check("an ordinary Compare of two photos shows their scores as well", True)
        # ---- issue #45: an "Ask AI" button in Compare: the AI's pick is marked, its reason shown, and it becomes the photo you pick
        pg.route("**/api/ai/settings", lambda route: route.fulfill(json={"configured": True, "rank_mode": "off", "provider": "openai", "model": "", "language": "en", "base_url": "", "providers": {}}))
        pg.route("**/api/ai/pick", lambda route: route.fulfill(json={"best_id": ids[1], "reason": "Sharper and everybody smiles", "model": "fake-model"}))
        pg.evaluate("setView('grid')")
        pg.evaluate(f"(() => {{ S.sel = new Set({ids[:3]}); S.act = {ids[0]}; onSelChange(); }})()")
        pg.evaluate("setView('compare')")
        pg.wait_for_function("document.querySelectorAll('#v-compare .cmp').length === 3", timeout=10000)
        pg.click("[data-t=cmpai]")
        pg.wait_for_selector("#v-compare .cmp-ai", timeout=10000)
        check("the AI's reason is shown in Compare", "Sharper and everybody smiles" in pg.inner_text("#v-compare .cmp-ai"), pg.inner_text("#v-compare .cmp-ai")[:80])
        check("the AI's pick is marked and is the photo that Pick keeps", pg.evaluate("+document.querySelector('#v-compare .cmp.ai').dataset.id") == ids[1] and pg.evaluate("S.act") == ids[1])
        pg.unroute("**/api/ai/settings")
        pg.unroute("**/api/ai/pick")
        # ---- issue #48: Score All: every photo gets a score, shown on the thumbnails, and the list is sorted by it
        pg.evaluate("setView('grid')")
        pg.wait_for_selector("[data-t=scoreall]", timeout=5000)
        pg.click("[data-t=scoreall]")
        pg.wait_for_function("S.sort === 'quality' && S.all.every(p => p.score != null)", timeout=90000)
        check("Score All: every photo has a score and the list is sorted by Quality Score", True)
        pg.wait_for_selector(".cell .scb", timeout=5000)
        check("...and the score shows on the thumbnails", pg.evaluate("document.querySelectorAll('.cell .scb').length") >= 1)
        # ---- issue #42: a series (similar photos within 2 minutes) has a badge on its thumbnails; a click opens it in Compare
        pg.evaluate(f"(() => {{ const m = new Map(); const mem = {ids[:3]}; for (const id of mem) m.set(id, {{best: mem[0], n: 3, members: mem}}); S.bursts = m; refreshCells(); }})()")
        pg.wait_for_selector(f".cell[data-id='{ids[1]}'] [data-a=stack]", state="attached", timeout=5000)
        check("a photo of a series shows the series badge", pg.evaluate(f"document.querySelector(\".cell[data-id='{ids[1]}'] [data-a=stack]\").textContent.includes('3')"))
        pg.evaluate(f"document.querySelector(\".cell[data-id='{ids[1]}'] [data-a=stack]\").dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, button: 0}}))")      # the grid reacts to mousedown
        pg.wait_for_function("S.view === 'compare' && document.querySelectorAll('#v-compare .cmp').length === 3", timeout=60000)
        check("clicking the badge compares the whole series", pg.evaluate("S.act") == ids[0])
        pg.evaluate("setView('grid')")
        # ---- issue #41: open with the Timeline when the user chose it (saved in the catalog: the window forgets its own storage when it closes)
        pg.evaluate("send('POST', '/api/start-view', {view: 'timeline'})")
        pg.wait_for_function("fetch('/api/start-view').then(r => r.json()).then(j => j.view === 'timeline')", timeout=5000)
        pg.reload()
        pg.wait_for_function("typeof S !== 'undefined' && S.view === 'timeline'", timeout=30000)
        check("with 'Open with the Timeline' on, photag starts on the Timeline", pg.evaluate("S.view") == "timeline")
        pg.evaluate("localStorage.clear()")          # even when the window's own storage is gone
        pg.reload()
        pg.wait_for_function("typeof S !== 'undefined' && S.view === 'timeline'", timeout=30000)
        check("...also when the window's own storage was cleared", pg.evaluate("S.view") == "timeline")
        pg.evaluate("send('POST', '/api/start-view', {view: 'grid'})")
        pg.wait_for_function("fetch('/api/start-view').then(r => r.json()).then(j => j.view === 'grid')", timeout=5000)
        pg.reload()
        pg.wait_for_function("typeof S !== 'undefined' && S.view === 'grid'", timeout=30000)
        check("...and on the Grid when it is off", pg.evaluate("S.view") == "grid")
        # ---- issue #56: typing in the keyword filter keeps the caret at the end (it jumped to the start: "day" became "yad")
        pg.evaluate("setView('grid')")
        pg.wait_for_selector("#kw-filter", state="attached", timeout=5000)
        pg.click("#kw-filter")
        pg.keyboard.type("day", delay=250)
        pg.wait_for_timeout(400)
        check("typing in the keyword filter does not move the caret to the start", pg.evaluate("document.querySelector('#kw-filter').value") == "day", pg.evaluate("document.querySelector('#kw-filter').value"))
        check("no script errors", not errs, errs[:3])
        br.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
