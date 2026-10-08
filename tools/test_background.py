"""Test: background mode (app/background.py, app/tray.py) -- the settings, the notifications about the backup (what is announced, when, in which
language, never more than once per 12 hours), the sign-in entry, the `--background` switch and the endpoints. The icon itself needs a Windows
desktop: there it is started and stopped for real, anywhere else it must simply not be there.

    py -3.12 tools/test_background.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_bg_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["HOME"] = str(tmp / "home")
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
from app import background, backup, config, tray  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


# ---- settings
check("defaults: keep running on, start at sign-in off", background.get() == {"keep": True, "autostart": False})
background.set_(keep=False)
check("switching it off is remembered", background.get()["keep"] is False)
background.set_(autostart=True)
check("starting at sign-in switches 'keep running' on too (it makes no sense without the icon)", background.get() == {"keep": True, "autostart": True})
background.set_(keep=True, autostart=False)
check("the settings file keeps other settings", "background" in config.read_all())

# ---- language of the notifications
check("English is the default and placeholders are filled", background.tr("No backup for {n} days", {"n": 3}) == "No backup for 3 days")
background.set_ui_lang("he")
check("the language of the window is used (from the locale file)", "3" in background.tr("No backup for {n} days", {"n": 3}) and background.tr("The backup failed", {}) != "The backup failed", background.tr("The backup failed", {}))
background.set_ui_lang("../../x")
check("a language code that is not a code is ignored", config.read_all()["ui_lang"] == "he")
background.set_ui_lang("de")
check("another language", background.tr("The backup failed", {}) == "Die Sicherung ist fehlgeschlagen")
background.set_ui_lang("en")

# ---- what is announced
NOW = 1_800_000_000.0
state = {"enabled": True, "folder_error": None, "overdue": False, "last_success": NOW - 5 * 86400, "last_error": None, "fail_count": 0}
backup.health = lambda now=None: dict(state)
check("a healthy backup: nothing to say", background.pending_alerts(NOW) == [])
state["enabled"] = False; state["folder_error"] = "unplugged"
check("backups switched off: nothing to say (even with the drive away)", background.pending_alerts(NOW) == [])
state["enabled"] = True
a = background.pending_alerts(NOW)
check("the backup drive is not connected", len(a) == 1 and a[0]["key"] == "drive" and "drive" in a[0]["title"] and a[0]["warning"], a)
state.update(folder_error=None, overdue=True)
a = background.pending_alerts(NOW)
check("no backup for several days (says how many)", len(a) == 1 and a[0]["key"] == "overdue" and "5" in a[0]["title"], a)
state.update(overdue=False, last_error="boom", fail_count=1)
check("one failure is not announced (it retries by itself)", background.pending_alerts(NOW) == [])
state.update(fail_count=2)
a = background.pending_alerts(NOW)
check("repeated failures are", len(a) == 1 and a[0]["key"] == "failed", a)
state.update(folder_error="unplugged", overdue=True)
check("the drive problem comes first and alone (the rest follows from it)", [x["key"] for x in background.pending_alerts(NOW)] == ["drive"])

# ---- once per 12 hours; forgotten when the problem is gone
(config.settings_dir() / "alerts.json").unlink(missing_ok=True)
check("first time: announced", [x["key"] for x in background.due_alerts(NOW)] == ["drive"])
check("an hour later: not again", background.due_alerts(NOW + 3600) == [])
check("13 hours later, still broken: again", [x["key"] for x in background.due_alerts(NOW + 13 * 3600)] == ["drive"])
state.update(folder_error=None, overdue=False, last_error=None, fail_count=0)
check("fixed: nothing, and it is forgotten", background.due_alerts(NOW + 14 * 3600) == [] and json.loads((config.settings_dir() / "alerts.json").read_text()) == {})
state.update(folder_error="unplugged")
check("it breaks again: announced at once (not waiting out the old 12 hours)", [x["key"] for x in background.due_alerts(NOW + 14 * 3600 + 60)] == ["drive"])

# ---- the icon: not there anywhere but Windows with a program to start
if sys.platform != "win32":
    check("not Windows: no background mode, no icon", not background.supported() and tray.Tray("x", []).start() is False and background.start() is False)
    check("a headless announcement does nothing", background.announce_headless() is False)
else:
    t = tray.Tray("photag test", [("Open", lambda: None), None, ("Exit", lambda: None)])
    ok = t.start()
    print(f"INFO the icon could be added on this machine: {ok}")
    if ok:
        t.notify("Test", "balloon")
        time.sleep(0.5)
        check("the icon is alive and takes a notification", t.alive())
    t.stop()
    check("stopping removes it and ends the thread", not t.alive())

# ---- sign-in entry (Windows registry, test key)
check("the sign-in command starts photag without a window", background.run_command(r"C:\p\photag.exe") == '"C:\\p\\photag.exe" --background')
if sys.platform == "win32":
    rk = r"Software\photag-test-run"
    try:
        background.set_autostart_entry(True, r"C:\p\photag.exe", rk)
        check("the entry is written and found", background.autostart_on(r"C:\p\photag.exe", rk))
        check("...but not for another exe", not background.autostart_on(r"C:\other\photag.exe", rk))
        background.set_autostart_entry(False, r"C:\p\photag.exe", rk)
        check("and removed again", not background.autostart_on(r"C:\p\photag.exe", rk))
    finally:
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rk)
        except OSError:
            pass

# ---- `--background` means: no window (the launcher's own switch for it)
out = subprocess.run([sys.executable, "-c", "import sys, os; sys.argv=['photag.exe','--background']\nfrom app import background\nprint(os.environ.get('PHOTAG_NO_WINDOW'))"],
                     cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
check("--background sets PHOTAG_NO_WINDOW", out == "1", out)
out = subprocess.run([sys.executable, "-c", "from app import background, viewer\nimport os\nprint(os.environ.get('PHOTAG_NO_WINDOW'), viewer.STARTED_WITH_PICTURE)"],
                     cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
check("without it nothing changes; a normal start is not a 'picture' start", out == "None False", out)

# ---- endpoints
PORT = 8793
APP = f"http://127.0.0.1:{PORT}"
env = {**os.environ}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))


def call(method, path, body=None):
    req = urllib.request.Request(APP + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    code, st = call("GET", "/api/tray")
    check("the status endpoint answers", code == 200 and "supported" in st and "keep" in st, st)
    if sys.platform != "win32":
        check("not supported here: changing it is refused", st["supported"] is False and call("POST", "/api/tray", {"keep": False})[0] == 400)
    code, _ = call("POST", "/api/ui-lang", {"lang": "fr"})
    check("the window tells the server its language", code == 200 and json.loads(((tmp / "appdata" / "photag" / "settings.json").read_text() if (tmp / "appdata" / "photag" / "settings.json").exists() else "{}") or "{}").get("ui_lang", "fr") == "fr")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
