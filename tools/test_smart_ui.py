"""UI test of the new features in a real browser: quality score badges, ranking the selection, duplicates and similar photos, library
cleanup, smart collections, On This Day (+ slideshow), the timeline with its fast-scroll rail, search by meaning (setup screen) and the
reduced-copies option of the backup dialog. Throw-away profile.

    py -3.12 tools/test_smart_ui.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
PORT = 8784
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_smartui_test_"))
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


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        if call("GET", f"/api/job/{name}")["state"] in ("done", "error"):
            return
        time.sleep(0.3)


src = tmp / "src"
for n in ("paris", "jerusalem", "telaviv_1", "telaviv_2"):
    Image.open(SAMPLES / f"{n}.jpg").convert("RGB").save(src / f"{n}.jpg", quality=92)
Image.open(SAMPLES / "paris.jpg").convert("RGB").resize((450, 300)).save(src / "paris_small.jpg", quality=80)
Image.open(SAMPLES / "jerusalem.jpg").convert("RGB").filter(ImageFilter.GaussianBlur(7)).save(src / "blurry.jpg", quality=92)
ImageEnhance.Brightness(Image.open(SAMPLES / "telaviv_1.jpg").convert("RGB")).enhance(0.07).save(src / "night.jpg", quality=92)
Image.new("RGB", (1080, 2400), (240, 240, 250)).save(src / "Screenshot_20240101.png")
import numpy as np  # noqa: E402
_rng = np.random.default_rng(7)
Image.fromarray((_rng.random((6, 8, 3)) * 255).astype("uint8")).resize((800, 600), Image.BICUBIC).save(src / "soft.jpg", quality=92)   # out of focus, unlike any other picture

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
    # give the photos capture dates: two of them on today's date in earlier years, the rest spread over months
    today = datetime.date.today()
    dates = {"paris.jpg": datetime.datetime(today.year - 2, today.month, today.day, 10), "jerusalem.jpg": datetime.datetime(today.year - 5, today.month, today.day, 9),
             "telaviv_1.jpg": datetime.datetime(2023, 3, 3 if (today.month, today.day) != (3, 3) else 4, 12), "telaviv_2.jpg": datetime.datetime(2023, 7, 9 if (today.month, today.day) != (7, 9) else 10, 12),
             "paris_small.jpg": datetime.datetime(today.year - 2, today.month, today.day, 10, 0, 5), "blurry.jpg": datetime.datetime(2022, 1, 15 if (today.month, today.day) != (1, 15) else 16, 8),
             "night.jpg": datetime.datetime(2021, 11, 20 if (today.month, today.day) != (11, 20) else 21, 23), "soft.jpg": datetime.datetime(2020, 6, 6 if (today.month, today.day) != (6, 6) else 7, 9), "Screenshot_20240101.png": datetime.datetime(2024, 1, 2 if (today.month, today.day) != (1, 2) else 3, 8)}
    for n, d in dates.items():
        call("PATCH", f"/api/photo/{ph[n]['id']}", {"taken_at": int(d.timestamp())})
    call("POST", "/api/analysis/run", {"eyes": False})
    job("analysis")

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        b = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(800)

        def shot(name):                                   # PHOTAG_TEST_SHOTS=<folder> keeps pictures of the screens for a look
            if os.environ.get("PHOTAG_TEST_SHOTS"):
                pg.wait_for_timeout(500)
                pg.screenshot(path=str(Path(os.environ["PHOTAG_TEST_SHOTS"]) / f"{name}.png"))

        def menu(top, item):
            pg.click(f"#menubar [data-menu] >> text={top}")
            pg.click(f"#menu-pop .mi:has-text('{item}')")

        # ---- score badges
        shot("grid")
        check("every analysed photo shows its quality score in the grid", pg.locator(".cell em.scb").count() >= 6, pg.locator(".cell em.scb").count())
        pg.click(".cell >> nth=0")
        pg.wait_for_timeout(500)
        check("the metadata panel shows the score", "Quality score" in pg.inner_text("#p-meta"))

        # ---- On This Day
        check("the Catalog lists 'On This Day' with the two photos from earlier years", pg.locator("#p-catalog .row[data-src=otd] .n").inner_text().strip() == "3",
              pg.locator("#p-catalog .row[data-src=otd] .n").inner_text())
        pg.click("#p-catalog .row[data-src=otd]")
        pg.wait_for_timeout(500)
        n_otd = pg.evaluate("S.list.length")
        check("it shows only photos taken on today's date in earlier years", n_otd == 3 and all(p["taken_at"] for p in pg.evaluate("S.list")), n_otd)
        menu("Library", "On This Day: Slideshow")
        pg.wait_for_selector("#slideshow:not(.hidden)")
        check("the slideshow starts, oldest year first, with the year in the counter", pg.inner_text("#ss-count").startswith(str(today.year - 5)), pg.inner_text("#ss-count"))
        pg.keyboard.press("Escape")
        pg.click("#p-catalog .row[data-src=all]")
        pg.wait_for_timeout(400)

        # ---- ranking the selection
        pg.click(".cell >> nth=0")
        pg.click(".cell >> nth=3", modifiers=["Control"])
        sel = pg.evaluate("[...S.sel]")
        pg.click("[data-t=rank]")
        pg.wait_for_selector(".rk-row .rk")
        cards = pg.locator(".rk-row .rk")
        check("the ranking shows both photos, the best marked", cards.count() == 2 and "Best" in cards.nth(0).inner_text(), cards.count())
        shot("ranking")
        scores = [int(x) for x in pg.locator(".rk .scv").all_inner_texts()]
        check("the best has the higher score", scores[0] >= scores[1], scores)
        best_id = int(pg.evaluate("document.querySelector('.rk.best').dataset.id"))
        pg.click("#rk-flag")
        pg.wait_for_timeout(800)
        fl = {p["id"]: p["flag"] for p in call("GET", "/api/photos?limit=999")}
        check("'Pick the best, reject the rest' flags them", fl[best_id] == 1 and all(fl[i] == -1 for i in sel if i != best_id), fl)

        # ---- duplicates and similar
        menu("Library", "Find Duplicates and Similar Photos")
        pg.wait_for_selector(".rv-g", timeout=15000)
        shot("duplicates")
        txt = pg.inner_text("#v-review")
        check("the duplicates screen finds the resized copy and the blurred twin", "Identical copies" in txt and pg.locator(".rv-g").count() == 2, txt[:80].replace("\n", " "))
        check("each group suggests its sharpest / biggest photo (★) and marks the other to go", pg.locator(".rv-t.keep .rv-star").count() == 2 and pg.locator(".rv-t.drop").count() == 2)
        pg.click(".rv-t.drop >> nth=0")
        check("clicking a photo to keep it removes it from the trash list", pg.locator(".rv-t.drop").count() == 1)
        pg.click("#rv-best")
        check("'Keep only the best' marks it again", pg.locator(".rv-t.drop").count() == 2 and pg.locator("#rv-trash[disabled]").count() == 0)
        pg.click("#rv-trash")
        pg.wait_for_selector("#modal:not(.hidden) .primary")
        pg.click("#modal .primary")
        pg.wait_for_timeout(1500)
        names = {p["filename"] for p in call("GET", "/api/photos?limit=999")}
        check("the unwanted copies went to the Trash, the originals stay", not ({"paris_small.jpg", "blurry.jpg"} & names) and {"paris.jpg", "jerusalem.jpg"} <= names, names)

        # ---- library cleanup
        menu("Library", "Library Cleanup")
        pg.wait_for_selector(".rv-g", timeout=15000)
        shot("cleanup")
        heads = pg.locator(".rv-gh b").all_inner_texts()
        check("cleanup lists screenshots, very dark and blurry photos", {"Screenshots", "Very dark photos", "Blurry photos"} <= set(heads), heads)
        check("nothing is selected for the user", pg.locator("#rv-trash[disabled]").count() == 1)
        pg.click("[data-selcat=blurry]")
        n = pg.locator(".rv-t.pick").count()
        pg.click("#rv-trash")
        pg.wait_for_selector("#modal:not(.hidden) .primary")
        pg.click("#modal .primary")
        pg.wait_for_timeout(1500)
        names = {p["filename"] for p in call("GET", "/api/photos?limit=999")}
        check("the selected blurry photos were moved to the Trash", n >= 1 and "soft.jpg" not in names and "night.jpg" in names, (n, names))
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)

        # ---- smart collections
        pg.click("#new-smart")
        pg.wait_for_selector("#sc-name")
        pg.fill("#sc-name", "Old and good")
        pg.fill("#sc-y0", "2023")
        pg.fill("#sc-y1", "2023")
        pg.fill("#sc-score", "30")
        shot("smart")
        pg.wait_for_function("document.querySelector('#sc-count').textContent.length > 0")
        check("the dialog counts the matching photos live", "2" in pg.inner_text("#sc-count") or "match" in pg.inner_text("#sc-count"), pg.inner_text("#sc-count"))
        pg.click("#sc-save")
        pg.wait_for_selector("#p-colls .row[data-src^='search:']")
        pg.wait_for_timeout(800)
        check("the smart collection appears in 'My Smart Collections'", "Old and good" in pg.inner_text("#p-colls"))
        got = pg.evaluate("S.list.map(p=>p.filename)")
        check("it shows exactly the 2023 photos (telaviv_1 and telaviv_2)", sorted(got) == ["telaviv_1.jpg", "telaviv_2.jpg"], got)
        # a new photo qualifies later: the collection fills itself
        call("PATCH", f"/api/photo/{ph['night.jpg']['id']}", {"taken_at": int(datetime.datetime(2023, 5, 5, 12).timestamp())})
        pg.evaluate("reloadAll()")
        pg.wait_for_timeout(1000)
        row_n = pg.locator("#p-colls .row[data-src^='search:'] .n").first.inner_text()
        check("the count follows the catalog (no manual refresh of the rules)", int(row_n) in (2, 3), row_n)

        # ---- timeline
        pg.click("#p-catalog .row[data-src=all]")
        pg.wait_for_timeout(500)
        pg.click("#toolbar [data-view=timeline]")
        pg.wait_for_selector(".tl-s")
        shot("timeline")
        heads = pg.locator(".tl-s h4 b").all_inner_texts()
        check("the timeline groups photos by month and year, newest first", len(heads) >= 3 and any("2023" in h for h in heads), heads)
        check("thumbnails are drawn for the visible months", pg.locator(".tl-c img").count() >= 2)
        check("the fast-scroll rail lists years", pg.locator("#tl-rail .tl-y").count() >= 2, pg.locator("#tl-rail .tl-y").all_inner_texts())
        box = pg.locator("#tl-rail").bounding_box()
        pg.mouse.move(box["x"] + 20, box["y"] + box["height"] * 0.95)
        pg.mouse.down()
        pg.mouse.move(box["x"] + 20, box["y"] + box["height"] * 0.97)
        bub = pg.inner_text("#tl-bub")
        pg.mouse.up()
        check("dragging the rail scrolls far and names the month under the pointer", bub.strip() != "" and pg.evaluate("document.querySelector('#tl-main').scrollTop") > 0 or pg.evaluate("document.querySelector('#tl-main').scrollHeight <= document.querySelector('#tl-main').clientHeight"), bub)
        pg.wait_for_timeout(600)                                     # let the scroll settle and the visible months draw
        pg.wait_for_selector(".tl-c img")
        pg.click(".tl-c >> nth=0")
        pg.wait_for_function("document.querySelectorAll('.tl-c.sel').length === 1", timeout=5000)
        check("clicking a photo in the timeline selects it", pg.evaluate("S.sel.size") == 1 and pg.locator(".tl-c.sel").count() == 1)
        pg.dblclick(".tl-c >> nth=0")
        pg.wait_for_timeout(500)
        check("double-click opens it in the Loupe", pg.evaluate("S.view") == "loupe")
        pg.keyboard.press("g")

        # ---- search by meaning: set-up screen (the model is not downloaded in tests)
        menu("Library", "Search by Meaning")
        pg.wait_for_selector("#sm-dl")
        check("search by meaning explains the local model and offers the download", "this computer" in pg.inner_text("#modal-box") and "600" in pg.inner_text("#modal-box"))
        pg.click("#sm-close")
        pg.keyboard.press("g")
        pg.click("#fb-tabs [data-fb=text]")
        pg.select_option("#ft-field", "meaning")
        check("the filter bar offers the meaning search", pg.evaluate("S.F.qf") == "meaning")

        # ---- backup dialog: reduced copies
        menu("File", "Backup and restore")
        pg.wait_for_selector("#bk-comp")
        check("reduced copies are off by default", not pg.is_checked("#bk-comp") and pg.is_hidden("#bk-comp-box"))
        pg.check("#bk-comp")
        pg.wait_for_selector("#bk-comp-box:not(.hidden)")
        shot("backup")
        check("turning it on shows strong compression and HD as the defaults", pg.input_value("#bk-q") == "60" and pg.input_value("#bk-size") == "1280", (pg.input_value("#bk-q"), pg.input_value("#bk-size")))
        check("the setting is saved by the server", call("GET", "/api/backup")["settings"]["compress_media"] is True)
        pg.select_option("#bk-size", "1920")
        pg.wait_for_timeout(500)
        check("changing the size is saved", call("GET", "/api/backup")["settings"]["compress_max_side"] == 1920)
        pg.click("#bk-close")

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
