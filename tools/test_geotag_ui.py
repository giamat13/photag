"""Test: the "Add places to photos without GPS" dialog in a real browser. Edge on Windows, or PHOTAG_TEST_BROWSER=<chromium>.

    py -3.12 tools/test_geotag_ui.py
"""
import calendar
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8795
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_geotag_ui_"))
for d in ("a", "l", "h"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}
res = []
T0 = calendar.timegm((2024, 5, 1, 10, 0, 0))


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            st = json.loads(urllib.request.urlopen(APP + "/api/status", timeout=2).read())
            break
        except Exception:
            time.sleep(0.5)
    db = Path(st["library_root"]) / "catalog.db"
    con = sqlite3.connect(db)
    for i, n, t, la, lo in [(1, "with.jpg", T0, 31.0, 35.0), (2, "near.jpg", T0 + 600, None, None), (3, "near2.jpg", T0 + 1200, None, None)]:
        con.execute("INSERT INTO photos(id, sha256, filename, rel_path, taken_at, lat, lng, trashed) VALUES(?,?,?,?,?,?,?,0)", (i, f"s{i}", n, n, t, la, lo))
    con.commit(); con.close()
    gpx = tmp / "t.gpx"
    gpx.write_text('<gpx><trk><trkseg><trkpt lat="32.5" lon="34.5"><time>2024-05-01T10:10:00Z</time></trkpt></trkseg></trk></gpx>', "utf-8")
    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        br = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1280, "height": 860})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_function("typeof geotagDialog === 'function' && S.all && S.all.length === 3", timeout=30000)
        pg.evaluate("geotagDialog(); 0")
        pg.wait_for_selector("#gt-find")
        pg.click("#gt-find")
        pg.wait_for_selector(".gt-list input[data-id]")
        check("finding places from pictures lists both photos without a place", pg.locator(".gt-list input[data-id]").count() == 2)
        pg.locator(".gt-list input[data-id]").nth(1).uncheck()
        check("the apply button counts the ticked ones", "1" in pg.inner_text("#gt-apply"), pg.inner_text("#gt-apply"))
        check("nothing is saved before confirming", sqlite3.connect(db).execute("SELECT COUNT(*) FROM photos WHERE lat IS NULL").fetchone()[0] == 2)
        pg.click("#gt-apply")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=10000)
        c = sqlite3.connect(db)
        check("confirming saves the place of the ticked photo only", c.execute("SELECT lat FROM photos WHERE id=2").fetchone()[0] == 31.0 and c.execute("SELECT lat FROM photos WHERE id=3").fetchone()[0] is None)
        c.close()
        pg.evaluate("geotagDialog(); 0")
        pg.wait_for_selector("#gt-find")
        pg.check("input[name=gt-mode][value=gpx]")
        pg.wait_for_selector("#gt-file")
        pg.set_input_files("#gt-file", str(gpx))
        pg.fill("#gt-off", "0")
        pg.click("#gt-find")
        pg.wait_for_selector(".gt-list input[data-id]")
        check("from a GPX file: the remaining photo gets the track's place", pg.locator(".gt-list input[data-id]").count() == 1 and "32.5" in pg.inner_text(".gt-list"), pg.inner_text(".gt-list"))
        pg.click("#gt-apply")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=10000)
        check("...and it is saved", sqlite3.connect(db).execute("SELECT lat FROM photos WHERE id=3").fetchone()[0] == 32.5)
        # ---- Ctrl+K: one search box
        pg.keyboard.press("Escape")
        pg.keyboard.press("Control+k")
        pg.wait_for_selector("#pl-q", timeout=5000)
        check("Ctrl+K opens the command search", pg.is_visible("#pl-q"))
        pg.fill("#pl-q", "preferences")
        check("a command is found by its name", "Preferences" in pg.inner_text("#pl-list"), pg.inner_text("#pl-list"))
        pg.fill("#pl-q", "2024")
        check("a year offers 'Photos from 2024'", "2024" in pg.inner_text("#pl-list .pl-row"), pg.inner_text("#pl-list"))
        pg.keyboard.press("Enter")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden') && S.src.kind === 'search'", timeout=5000)
        check("choosing it shows the photos of that year", pg.evaluate("S.base.length") == 3, pg.evaluate("S.base.length"))
        pg.keyboard.press("Control+k")
        pg.wait_for_selector("#pl-q")
        pg.fill("#pl-q", "near2")
        pg.keyboard.press("Enter")
        pg.wait_for_function("S.act === 3 && S.view === 'loupe'", timeout=8000)
        check("a photo is found by its file name and opened", pg.evaluate("S.act") == 3)
        pg.keyboard.press("Control+Shift+K")
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
