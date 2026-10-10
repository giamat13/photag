"""Test of issue #26: the program remembers the reports and suggestions it sent and tells the user, once, when one of them has been dealt
with (its issue is closed on GitHub). A fake GitHub answers; nothing real is contacted. Throw-away profile.

    py -3.12 tools/test_report_news.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT, FAKE = 8793, 8795
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_news_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999",
       "PHOTAG_REPORT_TOKEN": "test-token", "PHOTAG_REPORT_API": f"http://127.0.0.1:{FAKE}"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []
STATE = {}          # issue number -> "open" | "closed"
NEXT = [101]


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        n = NEXT[0]
        NEXT[0] += 1
        STATE[n] = "open"
        self._json(201, {"number": n, "html_url": f"https://github.com/giamat13/photag/issues/{n}"})

    def do_GET(self):
        m = re.search(r"/issues/(\d+)$", self.path)
        n = int(m.group(1)) if m else 0
        if n in STATE:
            self._json(200, {"number": n, "state": STATE[n], "html_url": f"https://github.com/giamat13/photag/issues/{n}"})
        else:
            self._json(404, {"message": "Not Found"})


httpd = ThreadingHTTPServer(("127.0.0.1", FAKE), Fake)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


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

    a = call("POST", "/api/report", {"title": "Add a dark mode", "kind": "feature", "include_tech": False})
    b = call("POST", "/api/report", {"title": "Crash when opening", "kind": "problem", "include_tech": False})
    check("two reports were sent (issues 101 and 102)", (a["number"], b["number"]) == (101, 102), (a, b))
    check("while both are open there is no news", call("GET", "/api/report/news?force=1")["items"] == [])

    STATE[101] = "closed"
    items = call("GET", "/api/report/news?force=1")["items"]
    check("a suggestion that was closed on GitHub is news (with its title, kind and link)", len(items) == 1 and items[0]["number"] == 101 and items[0]["kind"] == "feature"
          and items[0]["title"] == "Add a dark mode" and items[0]["url"].endswith("/issues/101"), items)
    check("...it stays news until the user has been told", len(call("GET", "/api/report/news")["items"]) == 1)
    call("POST", "/api/report/news/ack", {"numbers": [101]})
    check("...and once told it is not news again", call("GET", "/api/report/news?force=1")["items"] == [])

    # the window: told at once when it is opened, never twice
    STATE[102] = "closed"
    check("GitHub is asked at most once an hour: a close right after a check is not news yet", call("GET", "/api/report/news")["items"] == [])
    call("GET", "/api/report/news?force=1")                       # an hour later (the program asks again; here it is made to)
    with sync_playwright() as pw:
        br = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector("#toolbar", timeout=20000)
        pg.wait_for_timeout(500)
        pg.evaluate("closeModal()")
        pg.evaluate("reportNews()")
        pg.wait_for_selector("#modal:not(.hidden) .rn-row", timeout=10000)
        txt = pg.inner_text("#modal")
        check("the window tells the user that the report was handled", "Your reports and suggestions" in txt and "Crash when opening" in txt and "#102" in txt, txt[:120].replace("\n", " "))
        posted = []
        pg.on("request", lambda r: posted.append(r.url) if "/api/report/news/open" in r.url else None)
        pg.click("[data-rn='102']")
        pg.wait_for_timeout(500)
        check("'Open on GitHub' asks the program to open that issue", any(u.endswith("/api/report/news/open") for u in posted), posted)
        pg.click("#rn-ok")
        pg.wait_for_timeout(300)
        pg.evaluate("reportNews()")
        pg.wait_for_timeout(800)
        check("the same news is not shown a second time", pg.evaluate("document.querySelector('#modal').classList.contains('hidden')"))
        check("no script errors", not errs, errs[:2])
        br.close()

    # GitHub not reachable: no news, no error
    httpd.shutdown()
    httpd.server_close()
    call("POST", "/api/report/news/ack", {"numbers": []})
    r = urllib.request.urlopen(APP + "/api/report/news?force=1", timeout=60)
    check("GitHub unreachable: the answer is just 'no news' (no error)", r.status == 200 and json.loads(r.read())["items"] == [])
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
