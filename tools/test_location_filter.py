"""UI test of: the "Location" column of the library filter's Metadata tab (issue #21): photos are grouped by their GPS position
(0.1 degree), those without a position under "None", picking a value filters the grid, and the column cascades with the
others. Throw-away profile.

    py -3.12 tools/test_location_filter.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8783
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_location_test_"))
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


def label(p):
    if p.get("lat") is None or p.get("lng") is None:
        return None
    return f"{abs(p['lat']):.1f}°{'N' if p['lat'] >= 0 else 'S'} {abs(p['lng']):.1f}°{'E' if p['lng'] >= 0 else 'W'}"


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
    photos = call("GET", "/api/photos?limit=999")
    groups = Counter(label(p) for p in photos)
    n_none = groups.pop(None, 0)
    check("the samples have photos with and without a position", n_none >= 1 and len(groups) >= 2, (n_none, dict(groups)))

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.click('[data-fb="meta"]')
        pg.wait_for_selector("#fb-meta .mcol")
        check("the Metadata tab has a Location column", pg.evaluate("META_COLS.some(c => c[0] === 'place')") and pg.locator('#fb-meta [data-mk="place"]').count() > 0)

        shown = pg.evaluate("[...document.querySelectorAll('#fb-meta [data-mk=\"place\"][data-mv]:not([data-mv=\"\"])')].map(r => [r.dataset.mv, +r.querySelector('.n').textContent.replace(/\\D/g, '')])")
        shown = {k: v for k, v in shown}
        check("every position (to 0.1 degree) is listed with its photo count", all(shown.get(k) == v for k, v in groups.items()), (shown, dict(groups)))
        check("photos without a position are listed under None", shown.get("None") == n_none, shown.get("None"))

        top, top_n = groups.most_common(1)[0]
        pg.click(f'#fb-meta [data-mk="place"][data-mv="{top}"]')
        pg.wait_for_timeout(300)
        check("picking a place filters the grid to its photos", pg.evaluate("S.list.length") == top_n, (pg.evaluate("S.list.length"), top_n))
        check("...and the filter is shown as active", "Filter active" in pg.inner_text("#fb-state"))

        pg.click('#fb-meta [data-mk="place"][data-mv="None"]')
        pg.wait_for_timeout(300)
        check("picking None shows the photos without a position", pg.evaluate("S.list.length") == n_none, pg.evaluate("S.list.length"))

        pg.click('#fb-meta [data-mk="place"][data-mv=""]')
        pg.wait_for_timeout(300)
        check("All clears the place filter", pg.evaluate("S.list.length") == len(photos), pg.evaluate("S.list.length"))
        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
