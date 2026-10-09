"""UI test of: the Keyboard Shortcuts screen (Help menu / Ctrl+/) opens and lists real shortcuts, and the text search field
remembers recent searches (a browser-native dropdown, persisted in localStorage) across reloads. Throw-away profile.

    py -3.12 tools/test_help_search_history.py
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
PORT = 8786
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_help_test_"))
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

        # ---- Keyboard Shortcuts
        pg.keyboard.press("Control+/")
        pg.wait_for_selector(".modal-box")
        txt = pg.inner_text(".modal-box")
        check("the Keyboard Shortcuts screen opens and lists real shortcuts", all(s in txt for s in ("Ctrl+A", "Ctrl+Z", "Delete")), txt[:80])
        check("it covers more than one category", pg.locator(".modal-box h4").count() >= 4, pg.locator(".modal-box h4").count())
        pg.evaluate("closeModal()")

        # ---- recent searches
        pg.click('[data-fb="text"]')
        pg.wait_for_selector("#ft-q")
        check("the search field offers a recent-searches list", pg.locator("#ft-q").get_attribute("list") == "ft-q-recent" and pg.locator("#ft-q-recent").count() == 1)
        for q in ("paris", "jerusalem", "sunset"):
            pg.fill("#ft-q", q)
            pg.press("#ft-q", "Enter")
            pg.wait_for_timeout(400)
        opts = pg.evaluate("[...document.querySelectorAll('#ft-q-recent option')].map(o => o.value)")
        check("searches are remembered, most recent first, no duplicates", opts[:3] == ["sunset", "jerusalem", "paris"], opts)
        pg.fill("#ft-q", "paris")
        pg.press("#ft-q", "Enter")
        pg.wait_for_timeout(400)
        opts2 = pg.evaluate("[...document.querySelectorAll('#ft-q-recent option')].map(o => o.value)")
        check("searching an existing term moves it to the front instead of duplicating it", opts2[0] == "paris" and opts2.count("paris") == 1, opts2)

        pg.reload()
        pg.wait_for_selector(".cell, #v-empty", timeout=20000)
        pg.click('[data-fb="text"]')
        pg.wait_for_selector("#ft-q")
        opts3 = pg.evaluate("[...document.querySelectorAll('#ft-q-recent option')].map(o => o.value)")
        check("recent searches survive a reload (saved in the browser)", opts3[:2] == ["paris", "sunset"], opts3)

        pg.fill("#ft-q", "x")                     # a 1-character search is too short to be worth remembering
        pg.press("#ft-q", "Enter")
        pg.wait_for_timeout(300)
        check("a single character is not remembered", "x" not in pg.evaluate("[...document.querySelectorAll('#ft-q-recent option')].map(o => o.value)"))

        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
