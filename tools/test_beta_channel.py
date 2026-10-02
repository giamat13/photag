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


class Mock(BaseHTTPRequestHandler):
    def do_GET(self):
        _hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.path.startswith("/repos/x/photag/releases/latest"):
            self.wfile.write(json.dumps(STABLE).encode())
        elif self.path.startswith("/repos/x/photag/releases?"):
            self.wfile.write(json.dumps([BETA, STABLE]).encode())
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

mock.shutdown()
n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
