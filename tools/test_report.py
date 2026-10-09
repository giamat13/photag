"""Test: "Help > Report a problem" (app/report.py, /api/report): what is redacted, what is sent, the limits, the fallback to GitHub's own
page, and the dialog in the window. A fake GitHub API (a local server) receives the report; nothing leaves this computer.

    py -3.12 tools/test_report.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import report  # noqa: E402

PORT, FAKE = 8804, 8805
APP = f"http://127.0.0.1:{PORT}"
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


# ---- pure functions
home = str(Path.home())
txt = f'File "{home}/photag/app/x.py", line 3\nmail me at jane.doe@example.com from 203.0.113.9 or 127.0.0.1\nlib D:\\Photos\\Mine\\media'
red = report.redact(txt, ("D:\\Photos\\Mine",))
check("redact: the home folder, e-mail and IP addresses and the library folder are removed", home not in red and "jane.doe" not in red and "203.0.113.9" not in red and "Photos" not in red and "<email>" in red and "<ip>" in red, red)
check("...the local address stays (it is useful and harmless)", "127.0.0.1" in red)
report._RING.clear()
for i in range(500):
    report.remember(f"line {i}")
t = report.tech_text("17.0.0", "he", "/lib", 12)
check("the technical details hold the version, the system and only the last lines", "photag 17.0.0" in t and "Photos in the catalog: 12" in t and "line 499" in t and "line 10\n" not in t and len(t) <= report.LOG_CHARS, len(t))
check("...and the system, the libraries and how long it has been running", "Python: " in t and "Libraries: " in t and "Running for:" in t and "CPUs" in t, t[:300])
title, body = report.compose("The crop tool\nfreezes when I click twice.", "x ```y", "17.0.0", "Window: 1x1\nErrors in the window:\nboom me@x.org")
check("the issue title is the first line of what the user wrote", title == "[Report] The crop tool", title)
check("...the body holds the text, the details (without a way to break out of the code block) and the version", "freezes when I click twice." in body and "```y" not in body and "17.0.0" in body and "<details>" in body)
check("the details of the window come with the technical ones (redacted), and not without them", "Window details" in body and "boom <email>" in body and "Window details" not in report.compose("hello world!!", None, "1", "Window: 1")[1])
check("a suggestion is titled [Suggestion] and says so", report.compose("Please add a dark mode toggle", None, "1", "", "feature")[0] == "[Suggestion] Please add a dark mode toggle" and "feature suggestion" in report.compose("Please add a dark mode", None, "1", "", "feature")[1])
check("the fallback address of a suggestion carries the enhancement label", "labels=enhancement" in report.fallback_url("t", "b", "feature") and "labels=user-report" in report.fallback_url("t", "b"))
check("without technical details there is no details block", "<details>" not in report.compose("hello world!!", None, "1")[1])
fb = report.fallback_url("[Report] a b", "x" * 9000)
check("the fallback address is GitHub's new-issue page, cut to a safe length", fb.startswith("https://github.com/giamat13/photag/issues/new?title=") and len(fb) <= report.FALLBACK_URL_CHARS)

# ---- the server, with a fake GitHub
got = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        got.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"number": 42, "html_url": "https://github.com/giamat13/photag/issues/42"}).encode())

    def log_message(self, *a):
        pass


fake = HTTPServer(("127.0.0.1", FAKE), H)
threading.Thread(target=fake.serve_forever, daemon=True).start()
tmp = Path(tempfile.mkdtemp(prefix="photag_report_test_"))
base_env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
            "XDG_CONFIG_HOME": str(tmp / "cfg"), "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999", "PHOTAG_EXIF_DELAY": "9999",
            "PHOTAG_MIGRATE_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "cfg"):
    (tmp / d).mkdir()


def call(m, p, b=None, ok=True):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")
    except urllib.error.HTTPError as e:
        if ok:
            raise
        return {"_status": e.code}


def start(env_extra):
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env={**base_env, **env_extra},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    return p


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()


srv = start({"PHOTAG_REPORT_TOKEN": "test-token", "PHOTAG_REPORT_API": f"http://127.0.0.1:{FAKE}"})
try:
    pv = call("GET", "/api/report/preview?language=he")
    check("the preview shows the details and that the program can send by itself", pv["can_send"] and pv["allowed"] and "photag " in pv["tech"] and "Language: he" in pv["tech"])
    logs = list((tmp / "appdata" / "photag" / "logs").glob("photag*.log")) + list((tmp / "cfg" / "photag" / "logs").glob("photag*.log")) + list((tmp / "home").rglob("photag.log"))
    check("the program keeps a log file of its messages next to its settings", bool(logs) and "---- start" in logs[0].read_text("utf-8"), [str(x) for x in logs][:2])
    check("a report that is too short is refused", call("POST", "/api/report", {"description": "bug"}, ok=False).get("_status") == 400)
    r = call("POST", "/api/report", {"description": "The crop tool freezes when I click twice. My mail is me@x.org", "include_tech": True, "language": "he"})
    check("a report is sent to GitHub (issue number comes back)", r == {"sent": True, "number": 42, "url": "https://github.com/giamat13/photag/issues/42"}, r)
    g = got[-1]
    check("...as an issue of the photag repository with the bot's token", g["path"] == "/repos/giamat13/photag/issues" and g["auth"] == "Bearer test-token", (g["path"], g["auth"]))
    check("...with the user-report label, a title, the text (e-mail removed) and the details", g["body"]["labels"] == ["user-report"] and g["body"]["title"].startswith("[Report] The crop tool") and "me@x.org" not in g["body"]["body"] and "<email>" in g["body"]["body"] and "Technical details" in g["body"]["body"])
    call("POST", "/api/report", {"description": "A second one, quite long enough.", "include_tech": False})
    check("without technical details none are sent", "Technical details" not in got[-1]["body"]["body"])
    call("POST", "/api/report", {"description": "Please add a button that does something useful.", "kind": "feature"})
    check("a feature suggestion is sent as [Suggestion] with the enhancement label", got[-1]["body"]["title"].startswith("[Suggestion] ") and got[-1]["body"]["labels"] == ["user-report", "enhancement"], got[-1]["body"]["labels"])
    check("the fourth message within an hour is refused (rate limit)", call("POST", "/api/report", {"description": "A fourth one, long enough."}, ok=False).get("_status") == 429)
    check("...and the preview says so", call("GET", "/api/report/preview")["allowed"] is False)
    # ---- the dialog in the window
    for f in tmp.rglob("report-times.json"):
        f.unlink()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        b = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1300, "height": 850})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector("#menubar button", timeout=20000)
        pg.wait_for_timeout(800)
        pg.evaluate("openMenu(MENUS.length - 1)")
        pg.wait_for_timeout(300)
        check("Help has 'Report a problem or suggest a feature…'", "Report a problem or suggest a feature" in pg.inner_text("#menu-pop"))
        pg.evaluate("closeMenu()")
        pg.evaluate("reportProblem()")
        pg.wait_for_selector("#rp-desc", timeout=5000)
        check("the dialog explains that no account is needed (the program can send)", "do not need an account" in pg.inner_text("#modal-box"))
        check("...asks whether it is a problem or a feature suggestion, a problem by default", pg.is_checked("input[name=rp-kind][value=problem]") and pg.locator("input[name=rp-kind]").count() == 2)
        check("...and 'Include technical details' is on by default", pg.is_checked("#rp-tech"))
        pg.check("input[name=rp-kind][value=feature]")
        check("choosing a suggestion changes the question and the button", "What would you like" in pg.inner_text("#rp-lbl") and "Send suggestion" in pg.inner_text("#rp-ok"))
        pg.check("input[name=rp-kind][value=problem]")
        pg.evaluate("document.querySelector('#rp-det').open = true")
        check("...and shows what will be sent", "photag " in pg.inner_text("#rp-pre"), pg.inner_text("#rp-pre")[:80])
        pg.click("#rp-ok")
        pg.wait_for_timeout(300)
        check("an empty report is not sent (a hint is shown)", "little more" in pg.inner_text("#rp-msg"))
        pg.evaluate("setTimeout(()=>{ throw new Error('test boom'); }, 0)")
        pg.wait_for_timeout(300)
        pg.fill("#rp-desc", "The Develop panel is empty when I open a video.")
        n0 = len(got)
        pg.click("#rp-ok")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=10000)
        check("Send puts the report on GitHub and closes the dialog", len(got) == n0 + 1 and "Develop panel is empty" in got[-1]["body"]["body"])
        bd = got[-1]["body"]["body"]
        check("...with the technical details, the log lines and what the window knows (its screen, theme and its own errors)", "Technical details" in bd and "Window details" in bd and "Window: " in bd and "test boom" in bd and "Libraries:" in bd)
        check("...and the window thanks the user with the issue number", "#42" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        check("no JavaScript errors (apart from the one the test throws on purpose)", [e for e in errs if "test boom" not in e] == [], errs[:3])
        b.close()
finally:
    stop(srv)
(tmp / "appdata" / "photag" / "report-times.json").unlink(missing_ok=True)
for f in tmp.rglob("report-times.json"):
    f.unlink()
n_before = len(got)
srv = start({"PHOTAG_REPORT_TOKEN": "", "PHOTAG_NO_OPEN": "1"})
try:
    pv = call("GET", "/api/report/preview")
    check("without a token the preview says the program cannot send by itself", pv["can_send"] is False)
    r = call("POST", "/api/report", {"description": "No token here, so it cannot be sent."}, ok=False)
    check("...and a report is refused, GitHub is never opened (nothing is sent)", r.get("_status") == 503 and len(got) == n_before, r)
finally:
    stop(srv)
fake.shutdown()
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
