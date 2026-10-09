"""Test: search by place in Advanced Search (issue #21). One field takes a city / address / country (found through a fake
OpenStreetMap server here), plain coordinates ("32.1, 34.8") or an area drawn on the map; the photos are then filtered on
their GPS positions. Throw-away profile, nothing real is contacted.

    py -3.12 tools/test_place_search.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT, FAKE = 8784, 8794
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_place_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999",
       "PHOTAG_GEOCODE_URL": f"http://127.0.0.1:{FAKE}/search"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []
asked = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


# a fake Nominatim: Paris is a city (big box), "Dizengoff 1" an address (tiny box), anything else is unknown
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        asked.append(q)
        text = (q.get("q") or [""])[0].lower()
        if text == "paris":
            rows = [{"lat": "48.8566", "lon": "2.3522", "display_name": "Paris, France", "boundingbox": ["48.0", "49.5", "1.5", "3.5"]}]
        elif text == "dizengoff 1 tel aviv":
            rows = [{"lat": "32.0745", "lon": "34.7743", "display_name": "Dizengoff 1, Tel Aviv", "boundingbox": ["32.0744", "32.0746", "34.7742", "34.7744"]}]
        else:
            rows = []
        b = json.dumps(rows).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


httpd = ThreadingHTTPServer(("127.0.0.1", FAKE), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(m, p, b=None, expect=200):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == expect:
            return {"status": e.code}
        raise


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
    with_gps = [p for p in photos if p.get("lat") is not None]
    check("six photos, four with a position", len(photos) == 6 and len(with_gps) == 4, (len(photos), len(with_gps)))

    # ---- the server side
    r = call("GET", "/api/geocode?q=Paris&lang=he")
    check("a city comes back with its box", r["name"] == "Paris, France" and r["box"] == [48.0, 49.5, 1.5, 3.5], r)
    check("the language is passed on", asked and asked[-1].get("accept-language") == ["he"], asked[-1:])
    r = call("GET", "/api/geocode?q=Dizengoff%201%20Tel%20Aviv")
    check("an address is a point (no box)", r["box"] is None and abs(r["lat"] - 32.0745) < 1e-6, r)
    check("an unknown place is a 404", call("GET", "/api/geocode?q=Nowhereville", expect=404).get("status") == 404)

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)

        def fresh_form():                       # the form starts from the search that is open: go back to All Photographs first
            pg.evaluate("SEARCH_TMP = null; setSource(srcFromKey('all'))")
            pg.wait_for_timeout(400)
            pg.evaluate("advancedSearch()")
            pg.wait_for_selector("#as-q")

        def search_with(typed=None, setup=None, after=None):
            fresh_form()
            pg.wait_for_selector("#as-q")
            if setup:
                setup()
            if typed is not None:
                pg.fill("#as-q", typed)
                pg.press("#as-q", "Enter")
                pg.wait_for_timeout(700)
            if after:
                after()
            pg.click("#as-go")
            pg.wait_for_timeout(500)
            return pg.evaluate("S.list.length")

        check("the search form has the place field, a Find button and a draw button", (pg.evaluate("advancedSearch(), 1"), pg.locator("#as-q").count(), pg.locator("#as-find").count(), pg.locator("#as-draw").count()) == (1, 1, 1, 1))
        pg.keyboard.press("Escape")
        pg.evaluate("closeModal()")

        n = search_with("Paris")
        place = pg.evaluate("SEARCH_TMP.place")
        check("a city: its box is stored and only the photos inside show", place and place.get("box") and n == 1, (n, place))

        n = search_with("48.86, 2.35", lambda: pg.select_option("#as-km", "25"))
        check("coordinates (no network): the photos around them show", n == 1, n)
        n = search_with("32.1 N 34.8 E", lambda: pg.select_option("#as-km", "25"))
        check("coordinates with N / E letters work", n == 2, n)
        n = search_with("Dizengoff 1 Tel Aviv", after=lambda: pg.select_option("#as-km", "25"))
        check("an address is a point plus a radius the user can change", n == 2 and pg.evaluate("SEARCH_TMP.place.box == null && SEARCH_TMP.place.km == 25"), (n, pg.evaluate("SEARCH_TMP.place")))

        fresh_form()
        pg.wait_for_selector("#as-q")
        pg.fill("#as-q", "Nowhereville")
        pg.press("#as-q", "Enter")
        pg.wait_for_timeout(700)
        pg.click("#as-go")
        pg.wait_for_timeout(300)
        check("an unknown place sets no place filter", pg.evaluate("SEARCH_TMP === null"), pg.evaluate("JSON.stringify([SEARCH_TMP, S.src.kind, document.querySelector('#as-q') && document.querySelector('#as-q').value, document.querySelector('.toast, #toast') && document.querySelector('.toast, #toast').textContent])"))
        pg.evaluate("closeModal()")

        # ---- draw an area on the map: a big one covers every photo that has a position, a small one in the middle only Jerusalem
        def draw_box(x0, y0, x1, y1):           # fractions of the map's width / height
            pg.click("#as-draw")
            pg.locator("#as-map").scroll_into_view_if_needed()
            box = pg.locator("#as-map").bounding_box()
            pt = lambda fx, fy: (box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
            pg.mouse.move(*pt(x0, y0))
            pg.mouse.down()
            pg.mouse.move(*pt((x0 + x1) / 2, (y0 + y1) / 2), steps=4)
            pg.mouse.move(*pt(x1, y1), steps=4)
            pg.mouse.up()
            pg.wait_for_timeout(300)

        n = search_with(None, lambda: draw_box(0.1, 0.1, 0.9, 0.9))
        check("an area drawn around the middle east: the three photos there (not Paris, which is outside)", n == 3 and pg.evaluate("!!SEARCH_TMP.place.box"), (n, pg.evaluate("JSON.stringify(SEARCH_TMP.place)")))
        n = search_with(None, lambda: draw_box(0.02, 0.02, 0.2, 0.2))
        check("an area drawn over an empty part of the map: no photos", n == 0, n)

        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
    httpd.shutdown()
sys.exit(0 if all(res) else 1)
