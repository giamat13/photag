"""Test of issue #34: "Your reports and suggestions" -- the reports this program sent can be seen (with their state on GitHub), edited
(title and text; the technical details stay) and withdrawn. A fake GitHub answers; nothing real is contacted. Throw-away profile.

    py -3.12 tools/test_report_manage.py
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
PORT, FAKE = 8809, 8810
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_manage_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999",
       "PHOTAG_REPORT_TOKEN": "test-token", "PHOTAG_REPORT_API": f"http://127.0.0.1:{FAKE}"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []
ISSUES = {}         # number -> {"title", "body", "state"}
NEXT = [201]


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

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_POST(self):
        b = self._body()
        n = NEXT[0]
        NEXT[0] += 1
        ISSUES[n] = {"title": b["title"], "body": b["body"], "state": "open"}
        self._json(201, {"number": n, "html_url": f"https://github.com/giamat13/photag/issues/{n}"})

    def do_PATCH(self):
        n = int(re.search(r"/issues/(\d+)$", self.path).group(1))
        b = self._body()
        ISSUES[n].update({k: v for k, v in b.items() if k in ("title", "body", "state")})
        self._json(200, {"number": n})

    def do_GET(self):
        m = re.search(r"/issues/(\d+)$", self.path)
        n = int(m.group(1)) if m else 0
        if n in ISSUES:
            i = ISSUES[n]
            self._json(200, {"number": n, "state": i["state"], "title": i["title"], "body": i["body"], "comments": 2, "html_url": f"https://github.com/giamat13/photag/issues/{n}"})
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
    check("nothing sent yet: the list is empty", call("GET", "/api/report/mine")["items"] == [])

    a = call("POST", "/api/report", {"title": "Add a dark mode", "description": "I would like it", "kind": "feature", "include_tech": True})
    b = call("POST", "/api/report", {"title": "Crash when opening", "kind": "problem", "include_tech": False})
    mine = call("GET", "/api/report/mine")
    check("both reports are listed, newest first, open, with their text", [x["number"] for x in mine["items"]] == [b["number"], a["number"]]
          and all(x["state"] == "open" for x in mine["items"]) and mine["items"][1]["description"].startswith("I would like it") and mine["can_edit"], mine)

    call("POST", "/api/report/edit", {"number": a["number"], "title": "Add a dark theme", "description": "A dark theme please"})
    got = ISSUES[a["number"]]
    check("an edit changes the title (prefix kept) and the text", got["title"] == "[Suggestion] Add a dark theme" and got["body"].startswith("A dark theme please"), got)
    check("...and the technical details and the 'Sent from' line stay", "Technical details" in got["body"] and "Sent from photag" in got["body"], got["body"][-200:])

    call("POST", "/api/report/state", {"number": b["number"], "closed": True})
    check("withdrawing closes the issue on GitHub", ISSUES[b["number"]]["state"] == "closed")
    check("...and the list shows it as closed", {x["number"]: x["state"] for x in call("GET", "/api/report/mine")["items"]}[b["number"]] == "closed")
    call("POST", "/api/report/state", {"number": b["number"], "closed": False})
    check("...it can be reopened", ISSUES[b["number"]]["state"] == "open")

    ISSUES[999] = {"title": "Someone else's", "body": "x", "state": "open"}
    try:
        call("POST", "/api/report/edit", {"number": 999, "title": "hacked", "description": "x"})
        check("an issue this installation did not create cannot be edited", False)
    except urllib.error.HTTPError as e:
        check("an issue this installation did not create cannot be edited", e.code == 503 and ISSUES[999]["title"] == "Someone else's", e.code)

    with sync_playwright() as pw:
        br = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector("#toolbar", timeout=20000)
        pg.wait_for_timeout(500)
        pg.evaluate("closeModal()")
        pg.evaluate("myReports()")
        pg.wait_for_selector("#modal:not(.hidden) [data-mr-edit]", timeout=10000)
        txt = pg.inner_text("#modal")
        check("the window lists the reports", "Add a dark theme" in txt and "Crash when opening" in txt and f"#{a['number']}" in txt, txt[:150].replace("\n", " "))
        pg.click(f"[data-mr-edit='{a['number']}']")
        pg.wait_for_selector("#mr-title", timeout=5000)
        pg.fill("#mr-title", "Add a dark theme, please")
        pg.click("#mr-save")
        pg.wait_for_function("document.querySelector('#modal [data-mr-edit]') !== null", timeout=10000)
        check("saving in the window changes the issue", ISSUES[a["number"]]["title"] == "[Suggestion] Add a dark theme, please", ISSUES[a["number"]]["title"])
        check("no script errors", not errs, errs[:2])
        br.close()
finally:
    srv.terminate()
    httpd.shutdown()
sys.exit(0 if all(res) else 1)
