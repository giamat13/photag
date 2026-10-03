"""Test: tester mode (config.get/set_beta_channel) makes updater.check() offer pre-releases too --
GitHub's own /releases/latest endpoint always skips them, so tester mode asks for /releases instead.

    py -3.12 tools/test_beta_channel.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_beta_channel_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


STABLE = {"tag_name": "v1.7.0", "body": "stable notes", "html_url": "https://x/1.7.0",
          "published_at": "2026-01-01T00:00:00Z", "assets": [], "prerelease": False}
BETA = {"tag_name": "v1.8.0-beta.1", "body": "beta notes", "html_url": "https://x/1.8.0-beta.1",
        "published_at": "2026-02-01T00:00:00Z", "assets": [], "prerelease": True}
_hits = []
FAIL_LIST = False


class Mock(BaseHTTPRequestHandler):
    def do_GET(self):
        _hits.append(self.path)
        if FAIL_LIST and self.path.startswith("/repos/x/photag/releases?"):
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.path.startswith("/repos/x/photag/releases/latest"):
            self.wfile.write(json.dumps(STABLE).encode())
        elif self.path.startswith("/repos/x/photag/releases?"):
            self.wfile.write(json.dumps([BETA, STABLE]).encode())
        elif self.path.startswith("/repos/x/photag/releases/tags/"):
            self.wfile.write(json.dumps(BETA if "beta" in self.path else STABLE).encode())
        else:
            self.wfile.write(b"[]")

    def log_message(self, *a):
        pass


mock = HTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=mock.serve_forever, daemon=True).start()
os.environ["PHOTAG_UPDATE_API"] = f"http://127.0.0.1:{mock.server_port}"

from app import config, updater  # noqa: E402
updater.REPO = "x/photag"

check("tester mode is off by default", config.get_beta_channel() is False)

out = updater.check(force=True)
check("without tester mode, the latest STABLE release is offered", out["latest"] == "1.7.0", out["latest"])
check("without tester mode, /releases/latest was queried, not the list", any("releases/latest" in h for h in _hits), _hits)

config.set_beta_channel(True)
check("tester mode can be turned on", config.get_beta_channel() is True)
_hits.clear()
out2 = updater.check(force=True)
check("with tester mode, the pre-release is offered instead", out2["latest"] == "1.8.0-beta.1", out2["latest"])
check("with tester mode, the releases list was queried, not /latest", any("releases?" in h for h in _hits) and not any("releases/latest" in h for h in _hits), _hits)

config.set_beta_channel(False)
check("tester mode can be turned back off", config.get_beta_channel() is False)

# running_is_prerelease() / auto_enable_beta_if_prerelease(): once the running build is itself a
# pre-release, tester mode should turn on by itself so later checks keep offering pre-releases.
check("a plain version like '1.7.2' is not a pre-release", updater.parse_version("1.7.2")[3] == 1)
check("running_is_prerelease() agrees for the real running version (a plain release)", updater.running_is_prerelease() is False)
check("parse_version() marks a '-beta'-suffixed version as a pre-release", updater.parse_version("1.8.0-beta.1")[3] == 0)

_orig_version = updater.__version__
updater.__version__ = "1.8.0-beta.1"
try:
    check("running_is_prerelease() is True for a pre-release build", updater.running_is_prerelease() is True)
    updater.auto_enable_beta_if_prerelease()
    check("auto_enable_beta_if_prerelease() turns tester mode on", config.get_beta_channel() is True)
    config.set_beta_channel(False)
    updater.auto_enable_beta_if_prerelease()
    check("...and keeps doing so every start, not just once", config.get_beta_channel() is True)
finally:
    updater.__version__ = _orig_version
    config.set_beta_channel(False)

updater.__version__ = "1.7.2"
config.set_beta_channel(False)
updater.auto_enable_beta_if_prerelease()
check("a plain release build never turns tester mode on by itself", config.get_beta_channel() is False)
updater.__version__ = _orig_version

# ---- pre-release banner / manual check while tester mode is OFF
config.set_beta_channel(False)
updater.__version__ = "1.7.2"
try:
    o = updater.check(force=True)
    check("tester OFF, plain check: nothing about pre-releases is reported", o["pre"] is None and o["available"] is False and o["prerelease"] is False, o)
    o = updater.check(force=True, include_pre=True)
    check("tester OFF, manual check: the newer pre-release is reported as 'pre'", o["pre"] and o["pre"]["latest"] == "1.8.0-beta.1", o["pre"])
    check("...with its page and notes", o["pre"]["page"] == "https://x/1.8.0-beta.1" and o["pre"]["notes"] == "beta notes")
    check("...but it is NOT offered as an update (not available, nothing to install)", o["available"] is False and o["asset"] is None and o["prerelease"] is False)
    updater.__version__ = "1.8.0-beta.1"
    check("a pre-release that is not newer than the running version is not reported", updater.check(force=True, include_pre=True)["pre"] is None)
    updater.__version__ = "1.6.0"
    o = updater.check(force=True, include_pre=True)
    check("a newer STABLE update and a newer pre-release can both be reported", o["available"] and o["latest"] == "1.7.0" and o["prerelease"] is False and o["pre"]["latest"] == "1.8.0-beta.1", (o["latest"], o["pre"]))
    STABLE["tag_name"] = "v1.9.0"
    updater.__version__ = "1.7.2"
    o = updater.check(force=True, include_pre=True)
    check("a pre-release OLDER than the available stable release is not mentioned", o["available"] and o["latest"] == "1.9.0" and o["pre"] is None, (o["latest"], o["pre"]))
    STABLE["tag_name"] = "v1.7.0"
    FAIL_LIST = True
    o = updater.check(force=True, include_pre=True)
    check("if the releases list cannot be read, the manual check still works and says nothing about pre-releases", o["error"] is None and o["pre"] is None)
    FAIL_LIST = False

    config.set_beta_channel(True)
    o = updater.check(force=True)
    check("tester ON: the offered release is flagged as a pre-release (the page shows the big banner)", o["available"] and o["prerelease"] is True and o["latest"] == "1.8.0-beta.1")
    o = updater.check(force=True, include_pre=True)
    check("tester ON: no separate 'pre' note is needed (it is the offer itself)", o["pre"] is None)
    config.set_beta_channel(False)
    o = updater.check(force=True)
    check("a stable release is never flagged", o["prerelease"] is False)
    check("what's new: a pre-release version's notes are flagged", updater.whats_new("1.8.0-beta.1")["prerelease"] is True)
    check("what's new: a stable version's notes are not", updater.whats_new("1.7.0")["prerelease"] is False)
    check("a release marked prerelease on GitHub counts even without a suffix in its tag", updater._is_pre({"tag_name": "v2.0.0", "prerelease": True}) and not updater._is_pre({"tag_name": "v2.0.0", "prerelease": False}))
finally:
    updater.__version__ = _orig_version
    STABLE["tag_name"] = "v1.7.0"
    config.set_beta_channel(False)

mock.shutdown()
n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
