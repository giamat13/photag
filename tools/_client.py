"""A tiny HTTP client for tests that work on a throw-away profile in this process AND talk to the real server of that same profile
(a subprocess). It has the small part of the requests / TestClient API that those tests use: .get(path), .post(path, json=..., content=...)
-> an object with .status_code, .text and .json().

(fastapi's TestClient would be simpler, but it needs an extra package that the CI machines do not have.)
"""
import json as _json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self.content = status_code, body
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.text or "null")


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def _do(self, method, path, data=None, ctype=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": ctype} if ctype else {})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return Reply(r.status, r.read())
        except urllib.error.HTTPError as e:
            return Reply(e.code, e.read())

    def get(self, path):
        return self._do("GET", path)

    def post(self, path, json=None, content=None):
        if content is not None:
            return self._do("POST", path, content, "application/octet-stream")
        return self._do("POST", path, _json.dumps(json if json is not None else {}).encode(), "application/json")


def start_server(port, env):
    """The real server on `port`, with the environment `env` (the profile folders of the test). Returns (process, client)."""
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
    cl = Client(port)
    for _ in range(80):
        try:
            if cl.get("/api/status").status_code == 200:
                return srv, cl
        except Exception:
            pass
        time.sleep(0.5)
    srv.terminate()
    raise RuntimeError("the server did not start")
